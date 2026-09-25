"""Both shipped hosts wire A2A correctly (2026-09-25 adversarial review).

1. ``/v1/a2a/receive`` and ``/v1/a2a/ping`` run the SYNC receiver off the
   event loop — one inbound task (a worker run of up to ttl_s) used to freeze
   the whole host: console, every other peer, the relay keepalive.
2. Both lifespans start the A2A connectivity manager (ingress, advertised
   URL, runtime relay listener, per-connection upkeep). Until 2026-09-25 only
   the E2E harness called ``start_manager()``; the hosts built a relay
   listener once at boot, so a fresh install that paired by token stayed
   unreachable over the relay until a restart.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOSTS = {
    "gateway": REPO / "core" / "gateway" / "corvin_gateway" / "app.py",
    "standalone": REPO / "core" / "console" / "corvin_console" / "standalone.py",
}


@pytest.mark.parametrize("name", sorted(HOSTS))
def test_host_starts_and_stops_the_connectivity_manager(name):
    src = HOSTS[name].read_text(encoding="utf-8")
    assert "start_manager(" in src, f"{name} never starts the A2A connectivity manager"
    assert "stop_manager()" in src, f"{name} never stops the A2A connectivity manager"
    # The boot-only listener is gone: the manager owns the relay listener.
    assert not re.search(r"RelayListener\(", src), f"{name} still builds a boot-only RelayListener"


@pytest.mark.parametrize("name", sorted(HOSTS))
def test_receive_and_ping_run_off_the_event_loop(name):
    src = HOSTS[name].read_text(encoding="utf-8")
    assert "run_a2a_work(_a2a_receiver.receive, body)" in src, \
        "receive must run on the dedicated A2A work executor, off the loop and off the ping pool"
    assert re.search(r"to_thread\(\s*process_ping_request, body, _a2a_receiver,\s*client_addr=", src), \
        "ping must run off the loop AND pass the TCP peer for the pre-auth rate-limit bucket"
    assert not re.search(r"=\s*_a2a_receiver\.receive\(body\)", src)


def test_gateway_loop_stays_responsive_during_a_slow_receive():
    httpx = pytest.importorskip("httpx")
    gw = pytest.importorskip("corvin_gateway.app")

    class _Slow:
        def receive(self, body):
            time.sleep(2.5)

            class _R:
                def to_dict(self):
                    return {"status": "ok"}
            return _R()

    old = (gw._a2a_receiver, gw._A2A_AVAILABLE)
    gw._a2a_receiver, gw._A2A_AVAILABLE = _Slow(), True
    try:
        async def main():
            transport = httpx.ASGITransport(app=gw.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
                await c.post("/v1/a2a/receive", json={"warm": 1})  # cold-start cost out of the way
                t0 = time.monotonic()
                slow = asyncio.create_task(c.post("/v1/a2a/receive", json={"x": 1}))
                await asyncio.sleep(0.1)
                fired = time.monotonic() - t0
                await slow
                return fired
        fired = asyncio.run(main())
    finally:
        gw._a2a_receiver, gw._A2A_AVAILABLE = old
    assert fired < 1.0, f"event loop was blocked by receive(): 100 ms timer fired after {fired:.2f}s"
