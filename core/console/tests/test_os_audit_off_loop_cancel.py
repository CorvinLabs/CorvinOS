"""os_turn.* chain audits run OFF the event loop (awaited ``asyncio.to_thread``) — and a
cancel landing on that await changes nothing that reaches the chain (adversarial review
2026-11-06):

* the write always finishes before the CancelledError propagates;
* a turn cut there after its ``os_turn.started`` went out gets the paired
  ``os_turn.completed`` (exit -1) — never an unpaired start (ADR-0171 pairing);
* an uncancelled turn writes started then completed, in order, while the event loop
  stays free during a slow write.

Drives the real ``chat_runtime.stream_turn`` down its gate-refusal path (the routing gate
is stubbed to refuse; nothing is spawned). ``_bridge_audit.audit_event`` is replaced by a
recorder that makes ``os_turn.started`` slow, like the AV-scanned chain write it models.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))


class _Recorder:
    def __init__(self, slow_event: str, delay_s: float):
        self.events: list[tuple[str, dict, str]] = []
        self.slow_event, self.delay_s = slow_event, delay_s
        self.started_writing = threading.Event()

    def audit_event(self, event, **kw):
        if event == self.slow_event:
            self.started_writing.set()
            time.sleep(self.delay_s)
        self.events.append((event, kw.get("details") or {}, threading.current_thread().name))


@pytest.fixture
def cr(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("CORVIN_HOME", tmp.name)
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    from corvin_console import chat_runtime

    async def _refuse(*a, **kw):
        return types.SimpleNamespace(refusal="refused by test gate", rerouted=None)

    monkeypatch.setattr(chat_runtime, "_route_off_loop", _refuse)
    monkeypatch.setattr(chat_runtime, "_engine_unavailable_message", lambda e: None)
    monkeypatch.setattr(chat_runtime, "_espan", None, raising=False)
    yield chat_runtime
    try:
        tmp.cleanup()
    except OSError:
        pass


def _os_events(rec):
    return [(e, d) for e, d, _t in rec.events if e.startswith("os_turn.")]


def test_uncancelled_refusal_writes_started_then_completed_off_the_loop(cr, monkeypatch):
    rec = _Recorder("os_turn.started", 1.0)
    monkeypatch.setattr(cr, "_bridge_audit", rec)
    sess = cr.create_session("_default")
    ticks = []

    async def main():
        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)

        hb = asyncio.ensure_future(heartbeat())
        out = [ev async for ev in cr.stream_turn(sess, "hello")]
        hb.cancel()
        return out

    asyncio.run(main())
    evs = _os_events(rec)
    assert [e for e, _ in evs] == ["os_turn.started", "os_turn.completed"]
    assert evs[1][1].get("exit_code") == 1
    # the slow write ran in a worker thread and the loop kept ticking meanwhile
    started_thread = [t for e, _d, t in rec.events if e == "os_turn.started"][0]
    assert started_thread != threading.main_thread().name
    assert len(ticks) >= 10


def test_cancel_during_the_slow_started_write_still_pairs_it(cr, monkeypatch):
    rec = _Recorder("os_turn.started", 1.5)
    monkeypatch.setattr(cr, "_bridge_audit", rec)
    sess = cr.create_session("_default")

    async def main():
        async def consume():
            return [ev async for ev in cr.stream_turn(sess, "hello")]

        task = asyncio.ensure_future(consume())
        while not rec.started_writing.is_set():
            await asyncio.sleep(0.01)
        task.cancel()                                  # lands on the awaited write
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    evs = _os_events(rec)
    # the started record was FINISHED (not abandoned) and is paired with a cut completion
    assert [e for e, _ in evs] == ["os_turn.started", "os_turn.completed"]
    assert evs[1][1].get("exit_code") == -1
