"""Worker endpoint transport selection (ADR-0159 M4, Windows support).

On platforms with ``AF_UNIX`` (Linux/macOS) the compute worker listens on
``<compute>/worker.sock`` exactly as before — nothing in this module changes
that path.

On Windows (no ``AF_UNIX`` in CPython) the worker binds TCP ``127.0.0.1``
on an ephemeral port and publishes ``<sock_path>.port`` (owner-only):

    <port>\\n
    <token>\\n

``<token>`` is 64 lowercase hex chars (``secrets.token_hex(32)``). Every
TCP connection must send ``<token>\\n`` as its first bytes; the worker
compares it constant-time (``hmac.compare_digest``) and drops the connection
on mismatch. The first line stays a bare port so
``bridge_transport.read_port_file`` keeps working for liveness probes.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

LOOPBACK_HOST = "127.0.0.1"
TOKEN_HEX_LEN = 64
# Token line = 64 hex chars + "\n".
AUTH_LINE_LEN = TOKEN_HEX_LEN + 1


class WorkerEndpointError(OSError):
    """Raised when the worker endpoint cannot be resolved / connected."""


def use_tcp_loopback() -> bool:
    """True when the worker must use TCP loopback (no usable AF_UNIX).

    Aligned with ``bridge_transport._have_unix_socket``: Windows always uses
    TCP so the worker, client and discovery probes agree.
    """
    return sys.platform == "win32" or not hasattr(socket, "AF_UNIX")


def port_file_for(sock_path: Path) -> Path:
    sock_path = Path(sock_path)
    return sock_path.with_suffix(sock_path.suffix + ".port")


def new_token() -> str:
    return secrets.token_hex(TOKEN_HEX_LEN // 2)


def _restrict_owner_only(path: Path) -> None:
    """Best-effort owner-only ACL. POSIX: chmod 0600. Windows: icacls
    (drop inherited ACEs, grant the current user full control only)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if sys.platform != "win32":
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def write_port_file(sock_path: Path, port: int, token: str) -> Path:
    """Atomically write ``<sock_path>.port`` with owner-only permissions."""
    target = port_file_for(sock_path)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, f"{int(port)}\n{token}\n".encode("ascii"))
    finally:
        os.close(fd)
    _restrict_owner_only(tmp)
    os.replace(tmp, target)
    return target


def read_port_file(sock_path: Path) -> tuple[int, str] | None:
    """Return ``(port, token)`` or None when missing / malformed."""
    try:
        lines = port_file_for(sock_path).read_text(encoding="ascii").split()
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) < 2:
        return None
    try:
        port = int(lines[0])
    except ValueError:
        return None
    token = lines[1]
    if not (1 <= port <= 65535) or len(token) != TOKEN_HEX_LEN:
        return None
    return port, token


def endpoint_exists(sock_path: Path) -> bool:
    """Replacement for ``sock_path.exists()`` that is transport-aware."""
    if use_tcp_loopback():
        return port_file_for(sock_path).exists()
    return Path(sock_path).exists()


def connect(sock_path: Path, *, timeout_s: float) -> socket.socket:
    """Open an authenticated connection to the worker.

    Unix: plain AF_UNIX connect (unchanged behaviour). Windows: TCP loopback
    connect + token line.
    """
    if not use_tcp_loopback():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
        sock.settimeout(timeout_s)
        try:
            sock.connect(str(sock_path))
        except BaseException:
            sock.close()
            raise
        return sock
    info = read_port_file(sock_path)
    if info is None:
        raise WorkerEndpointError(
            f"compute worker port file missing or invalid: {port_file_for(sock_path)}"
        )
    port, token = info
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((LOOPBACK_HOST, port))
        sock.sendall(token.encode("ascii") + b"\n")
    except BaseException:
        sock.close()
        raise
    return sock
