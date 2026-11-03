"""Windows transport for the compute worker (no AF_UNIX).

The worker binds 127.0.0.1:<ephemeral> and publishes ``<sock>.port``
(``<port>\n<token>\n``); every connection must send ``<token>\n`` first.
The TCP path is forced via monkeypatch so these tests also run on Linux.
"""
from __future__ import annotations

import asyncio
import socket
import sys
import threading
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute import endpoint  # noqa: E402
from corvin_compute.client import WorkerClient, is_socket_reachable  # noqa: E402
from corvin_compute.transport import (  # noqa: E402
    TransportError, recv_frame_sync, send_frame_sync,
)
from corvin_compute.worker import WorkerServer  # noqa: E402


@pytest.fixture
def tcp_worker(tmp_path, monkeypatch):
    # Simulate a platform without AF_UNIX (Windows) — worker + client both
    # resolve the transport through endpoint.use_tcp_loopback().
    monkeypatch.setattr(endpoint, "use_tcp_loopback", lambda: True)
    home = tmp_path / "corvin"
    sock_path = home / "tenants" / "_default" / "compute" / "worker.sock"
    sock_path.parent.mkdir(parents=True)
    loop = asyncio.new_event_loop()
    server = WorkerServer(
        tenant_id="_default", corvin_home=home, socket_path=sock_path,
        runner_fn=lambda tool, payload: {"loss": 0.0},
    )
    ready = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)

        async def _serve():
            task = asyncio.create_task(server.serve_forever())
            while not endpoint.port_file_for(sock_path).exists():
                await asyncio.sleep(0.01)
            ready.set()
            await task

        try:
            loop.run_until_complete(_serve())
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    assert ready.wait(10.0), "worker failed to start"
    yield sock_path
    asyncio.run_coroutine_threadsafe(server.stop(), loop).result(timeout=5.0)
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5.0)


def test_port_file_format_and_no_unix_socket(tcp_worker):
    sock_path = tcp_worker
    assert not sock_path.exists()
    lines = endpoint.port_file_for(sock_path).read_text(encoding="ascii").splitlines()
    assert len(lines) == 2
    assert 1 <= int(lines[0]) <= 65535
    assert len(lines[1]) == 64 and int(lines[1], 16) >= 0
    assert endpoint.read_port_file(sock_path) == (int(lines[0]), lines[1])


def test_roundtrip_with_token(tcp_worker):
    client = WorkerClient(tcp_worker, timeout_s=10.0)
    pong = client.ping()
    assert pong.get("pong") is True
    assert pong.get("tenant_id") == "_default"
    assert is_socket_reachable(tcp_worker, timeout_s=2.0)


def test_wrong_token_rejected(tcp_worker):
    port, token = endpoint.read_port_file(tcp_worker)
    bad = ("0" * 64 if token != "0" * 64 else "1" * 64)
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as s:
        s.sendall(bad.encode("ascii") + b"\n")
        send_frame_sync(s, {"op": "ping", "params": {}})
        with pytest.raises((TransportError, OSError)):
            recv_frame_sync(s)


def test_missing_token_rejected(tcp_worker):
    port, _ = endpoint.read_port_file(tcp_worker)
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as s:
        send_frame_sync(s, {"op": "ping", "params": {}})
        with pytest.raises((TransportError, OSError)):
            recv_frame_sync(s)


def test_stop_removes_port_file(tmp_path, monkeypatch):
    monkeypatch.setattr(endpoint, "use_tcp_loopback", lambda: True)
    sock_path = tmp_path / "worker.sock"
    endpoint.write_port_file(sock_path, 1234, endpoint.new_token())
    assert endpoint.endpoint_exists(sock_path)
    server = WorkerServer(tenant_id="_default", corvin_home=tmp_path,
                          socket_path=sock_path)
    asyncio.run(server.stop())
    assert not endpoint.endpoint_exists(sock_path)


def test_client_without_port_file_raises_typed_error(tmp_path, monkeypatch):
    monkeypatch.setattr(endpoint, "use_tcp_loopback", lambda: True)
    client = WorkerClient(tmp_path / "worker.sock", timeout_s=1.0)
    with pytest.raises(endpoint.WorkerEndpointError):
        client.ping()
    assert not is_socket_reachable(tmp_path / "worker.sock")
