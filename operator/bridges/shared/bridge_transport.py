"""ADR-0159 M4 — BridgeTransport: cross-platform socket connector.

On Linux/macOS (AF_UNIX available) the compute-worker and future sidecar
(ECI "sidecar" transport) communicate via Unix domain sockets.
On Windows (or any platform where ``socket.AF_UNIX`` is absent) the same
connection is made over TCP loopback to a port published in a companion
``.port`` file.

Public API
----------
get_transport()               -- str: "unix_socket" | "tcp_loopback"
connect_to_socket(sock_path)  -- socket.socket (caller owns lifetime)
BRIDGE_PORT_ENV               -- env-var name for sitecustomize.py exception

Wire-format
-----------
Unix path:  <sock_path>             (e.g. .../compute/worker.sock)
TCP sidecar port file: <sock_path>.port  (plain text: "12345\\n")

The port file must be mode 0600 and written atomically by the server process.

No ``import anthropic`` (CI AST lint enforces).
No network I/O other than the connect itself.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

# Env var that sitecustomize.py checks to allow bridge TCP loopback port.
BRIDGE_PORT_ENV = "CORVIN_BRIDGE_PORT"

# Env var for operator override ("unix_socket" | "tcp_loopback").
CORVIN_TRANSPORT_ENV = "CORVIN_BRIDGE_TRANSPORT"

# Loopback bind host used for TCP fallback.
LOOPBACK_HOST = "127.0.0.1"


def _have_unix_socket() -> bool:
    """True when AF_UNIX is available and we are NOT on Windows."""
    if sys.platform == "win32":
        return False
    return hasattr(socket, "AF_UNIX")


def get_transport() -> str:
    """Return the active IPC transport name.

    Returns ``"unix_socket"`` when AF_UNIX is available (Linux/macOS),
    ``"tcp_loopback"`` on Windows or when overridden by env var.
    """
    override = os.environ.get(CORVIN_TRANSPORT_ENV, "").strip().lower()
    if override in ("unix_socket", "tcp_loopback"):
        return override
    return "unix_socket" if _have_unix_socket() else "tcp_loopback"


def _port_file_for(sock_path: Path) -> Path:
    """Companion TCP port file alongside the Unix socket path."""
    return sock_path.with_suffix(sock_path.suffix + ".port")


def read_port_file(sock_path: Path) -> int | None:
    """Read the TCP port from a ``.port`` companion file.

    Returns the port as int, or None if the file does not exist or is invalid.
    """
    port_file = _port_file_for(sock_path)
    try:
        text = port_file.read_text(encoding="ascii").strip()
        port = int(text)
        if 1 <= port <= 65535:
            return port
        return None
    except (FileNotFoundError, ValueError, OSError):
        return None


def connect_to_socket(
    sock_path: Path,
    *,
    timeout: float = 2.0,
    host: str = LOOPBACK_HOST,
) -> socket.socket:
    """Return a connected socket to the worker at ``sock_path``.

    On platforms with AF_UNIX: connects directly to the Unix domain socket.
    On Windows / tcp_loopback override: reads the companion ``.port`` file and
    connects via TCP loopback.

    Raises
    ------
    OSError
        When the socket/port file does not exist or the connect fails.
    """
    transport = get_transport()
    if transport == "unix_socket":
        if not sock_path.exists():
            raise OSError(
                f"Unix socket not found: {sock_path} — "
                "worker may be offline or not yet started"
            )
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
        s.settimeout(timeout)
        try:
            s.connect(str(sock_path))
        except Exception:
            s.close()
            raise
        return s
    else:
        # TCP loopback path (Windows / override)
        port = read_port_file(sock_path)
        if port is None:
            raise OSError(
                f"TCP port file not found or invalid: {_port_file_for(sock_path)} — "
                "worker may not support TCP transport or may be offline"
            )
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, port))
        except Exception:
            s.close()
            raise
        return s


def probe_socket(sock_path: Path, *, timeout: float = 0.1) -> bool:
    """Non-blocking availability probe; returns True if connectable.

    Drops the connection immediately — only used for liveness checks.
    Mirrors the old ``_probe()`` pattern in ``_compute_discovery.py``
    but works on all three platforms.
    """
    try:
        s = connect_to_socket(sock_path, timeout=timeout)
        s.close()
        return True
    except (OSError, socket.timeout):
        return False
