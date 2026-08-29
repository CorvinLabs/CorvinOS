#!/usr/bin/env python3
"""test_daemon_windows.py — Windows-specific daemon transport tests.

Tests the TCPLoopbackTransport on any platform (mocked as Windows)
and verifies cross-platform compatibility.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import importlib.util

# Import the transport module directly using importlib
transport_path = Path(__file__).parent / "daemon_transport.py"
spec = importlib.util.spec_from_file_location("daemon_transport", transport_path)
transport_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport_module)

TCPLoopbackTransport = transport_module.TCPLoopbackTransport
UnixDomainSocketTransport = transport_module.UnixDomainSocketTransport
create_transport = transport_module.create_transport


def test_tcp_loopback_transport_bind_connect():
    """Test TCPLoopbackTransport bind and connect."""
    socket_path = Path(tempfile.mkdtemp()) / "init.sock"
    transport = TCPLoopbackTransport(socket_path)

    # Bind should create a port file
    sock, endpoint = transport.bind()
    port_file = socket_path.parent / "daemon.port"
    assert port_file.exists(), "Port file should exist after bind"
    port_str = port_file.read_text().strip()
    assert port_str.isdigit(), f"Port file should contain a number, got {port_str}"
    assert endpoint.startswith("127.0.0.1:"), f"Endpoint should start with 127.0.0.1:, got {endpoint}"

    sock.listen(5)
    sock.settimeout(0)

    # Client connect should read the port file and connect
    transport_client = TCPLoopbackTransport(socket_path)
    try:
        client_sock = transport_client.connect(timeout=1.0)
        assert client_sock is not None, "Client should connect successfully"
        client_sock.close()
        print("✓ TCP loopback bind/connect test PASSED")
    finally:
        transport.cleanup()


def test_windows_transport_selection():
    """Test that create_transport picks TCPLoopbackTransport on Windows."""
    socket_path = Path(tempfile.mkdtemp()) / "init.sock"

    with patch("sys.platform", "win32"):
        transport = create_transport(socket_path)
        assert isinstance(transport, TCPLoopbackTransport), "Should pick TCP on Windows"
        print("✓ Windows transport selection test PASSED")

    # On non-Windows, should use Unix
    with patch("sys.platform", "linux"):
        transport = create_transport(socket_path)
        assert isinstance(transport, UnixDomainSocketTransport), "Should pick Unix on Linux"
        print("✓ Unix transport selection test PASSED")


def test_port_file_cleanup():
    """Test that port file is cleaned up after daemon shuts down."""
    socket_path = Path(tempfile.mkdtemp()) / "init.sock"
    transport = TCPLoopbackTransport(socket_path)

    sock, _ = transport.bind()
    port_file = socket_path.parent / "daemon.port"
    assert port_file.exists(), "Port file should exist"

    transport.cleanup()
    assert not port_file.exists(), "Port file should be cleaned up"
    print("✓ Port file cleanup test PASSED")


def test_stale_port_file_replaced():
    """Test that daemon can replace a stale port file."""
    socket_path = Path(tempfile.mkdtemp()) / "init.sock"
    port_file = socket_path.parent / "daemon.port"

    # Create a stale port file
    port_file.write_text("9999\n")
    assert port_file.exists()

    # Bind should replace it with a valid port
    transport = TCPLoopbackTransport(socket_path)
    sock, endpoint = transport.bind()

    new_port_str = port_file.read_text().strip()
    assert new_port_str != "9999", "Port file should be updated"
    assert int(new_port_str) > 0, "Port should be valid"
    print("✓ Stale port file replacement test PASSED")

    transport.cleanup()


if __name__ == "__main__":
    test_tcp_loopback_transport_bind_connect()
    test_windows_transport_selection()
    test_port_file_cleanup()
    test_stale_port_file_replaced()
    print("\n✅ All Windows-specific tests passed")
