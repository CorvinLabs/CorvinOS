"""Cross-platform daemon IPC transport abstraction.

Provides a unified interface for daemon-client communication:
  - Unix: AF_UNIX domain socket (existing, tested)
  - Windows: TCP loopback (127.0.0.1:random_port)

Both transport modes are transparent to callers of daemon() and daemon_call().
The transport type is auto-selected based on sys.platform.
"""
from __future__ import annotations

import json
import os
import socket as _socket
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional


class DaemonTransport(ABC):
    """Abstract base for daemon IPC transport."""

    @abstractmethod
    def bind(self) -> tuple[Any, str]:
        """Start listening. Returns (socket, endpoint_description)."""
        pass

    @abstractmethod
    def connect(self, timeout: float = 5.0) -> Any:
        """Open client connection. Returns connected socket."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up any persistent resources (files, ports, etc)."""
        pass

    @abstractmethod
    def store_endpoint(self) -> None:
        """Write endpoint info to disk (port file on Windows, etc)."""
        pass


class UnixDomainSocketTransport(DaemonTransport):
    """AF_UNIX socket transport (Unix/Linux/macOS only)."""

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self.sock: Optional[_socket.socket] = None

    def bind(self) -> tuple[_socket.socket, str]:
        """Bind to the Unix domain socket."""
        # Best-effort cleanup of stale socket
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError:
            pass

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        prev_umask = os.umask(0o077)
        try:
            sock.bind(str(self.socket_path))
        except OSError as exc:
            os.umask(prev_umask)
            raise RuntimeError(f"bind {self.socket_path}: {exc}") from exc
        finally:
            os.umask(prev_umask)

        # Belt-and-braces: chmod after bind (covers umask edge cases)
        try:
            self.socket_path.chmod(0o600)
        except OSError:
            pass  # umask already set the right mode

        self.sock = sock
        return sock, str(self.socket_path)

    def connect(self, timeout: float = 5.0) -> _socket.socket:
        """Connect to the Unix domain socket."""
        if not self.socket_path.exists():
            raise OSError(f"daemon not running ({self.socket_path})")

        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self.socket_path))
        except (OSError, ConnectionError) as exc:
            sock.close()
            raise RuntimeError(f"connect {self.socket_path}: {exc}") from exc
        return sock

    def cleanup(self) -> None:
        """Close socket and remove the socket file."""
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError:
            pass

    def store_endpoint(self) -> None:
        """No-op for Unix sockets (endpoint is always socket_path)."""
        pass


class TCPLoopbackTransport(DaemonTransport):
    """TCP loopback transport (Windows / cross-platform fallback)."""

    def __init__(self, socket_path: Path):
        # socket_path is still used to determine the port file location
        self.socket_path = socket_path
        self.port_file = socket_path.parent / "daemon.port"
        self.sock: Optional[_socket.socket] = None
        self.port: Optional[int] = None

    def bind(self) -> tuple[_socket.socket, str]:
        """Bind to 127.0.0.1:0 (OS assigns free port)."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            # Bind to 127.0.0.1:0 — OS picks an available port
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        except OSError as exc:
            sock.close()
            raise RuntimeError(f"bind 127.0.0.1:0: {exc}") from exc

        self.sock = sock
        endpoint = f"127.0.0.1:{self.port}"
        self.store_endpoint()
        return sock, endpoint

    def connect(self, timeout: float = 5.0) -> _socket.socket:
        """Connect to 127.0.0.1:port (read port from port file)."""
        if not self.port_file.exists():
            raise OSError(f"daemon not running ({self.port_file} missing)")

        try:
            port_str = self.port_file.read_text(encoding="utf-8").strip()
            port = int(port_str)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"read {self.port_file}: {exc}") from exc

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(("127.0.0.1", port))
        except (OSError, ConnectionError) as exc:
            sock.close()
            raise RuntimeError(f"connect 127.0.0.1:{port}: {exc}") from exc
        return sock

    def cleanup(self) -> None:
        """Close socket and remove port file."""
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

        try:
            if self.port_file.exists():
                self.port_file.unlink()
        except OSError:
            pass

    def store_endpoint(self) -> None:
        """Write the port to daemon.port file."""
        if self.port is None:
            return
        try:
            self.port_file.write_text(str(self.port), encoding="utf-8")
        except OSError:
            pass  # Best-effort; don't break daemon startup


def create_transport(socket_path: Path) -> DaemonTransport:
    """Factory: choose transport based on platform."""
    if sys.platform == "win32":
        return TCPLoopbackTransport(socket_path)
    else:
        return UnixDomainSocketTransport(socket_path)
