"""Regression tests: A2ATaskEnvelope.dispatch (multi_instance_sync send-task).

2026-09-25 defects:
  * sender.send() (blocking, up to timeout_s) ran ON the event loop;
  * every non-ok result was retried with a NEW task — a slow peer that timed
    out / a signed rejection executed the same work up to three times;
  * error_detail was str(SendResult) — response data leaked into the reply.

Synchronous tests driving asyncio.run (pytest-asyncio is not installed here).
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import pytest

_shared = Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))

from corvin_console.api import multi_instance_sync as mis  # noqa: E402


@dataclass
class _Res:
    ok: bool
    status: str = "error"
    task_id: str = "remote-1"
    instance_id: str = ""
    data: dict = field(default_factory=dict)
    duration_ms: int = 1
    error_category: str | None = None
    error_detail: str | None = None


def _fake_sender_module(results, calls, *, delay=0.0, check_thread=None):
    class FakeSender:
        def send(self, **kw):
            calls.append(kw)
            if check_thread is not None:
                check_thread()
            if delay:
                time.sleep(delay)
            r = results[min(len(calls) - 1, len(results) - 1)]
            if isinstance(r, BaseException):
                raise r
            return r
    return types.SimpleNamespace(RemoteTriggerSender=FakeSender)


def _envelope():
    return mis.A2ATaskEnvelope(task_id="t1", context_snapshot={}, decision_history=[],
                               endpoint_id="peer", timeout_s=5, retry_count=3)


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Skip the 1/2/4 s retry backoff without touching the global asyncio."""
    async def quick(delay, *a, **k):
        return await asyncio.sleep(0)
    proxy = types.SimpleNamespace(to_thread=asyncio.to_thread, sleep=quick,
                                  timeout=asyncio.timeout, TimeoutError=asyncio.TimeoutError)
    monkeypatch.setattr(mis, "asyncio", proxy)
    yield


def _dispatch(fake_mod):
    with mock.patch.dict(sys.modules, {"remote_trigger_sender": fake_mod}):
        return asyncio.run(_envelope().dispatch())


def test_send_runs_off_the_event_loop():
    calls: list = []
    loop_state: list = []

    def check_thread():
        try:
            asyncio.get_running_loop()
            loop_state.append("loop")    # would break the sender's relay asyncio.run
        except RuntimeError:
            loop_state.append("no-loop")
    fake = _fake_sender_module([_Res(ok=True, status="ok")], calls, delay=0.8,
                               check_thread=check_thread)

    async def scenario():
        t0 = time.monotonic()
        task = asyncio.create_task(_envelope().dispatch())
        await asyncio.sleep(0.1)          # any other coroutine on the console loop
        tick_at = time.monotonic() - t0
        return await task, tick_at
    with mock.patch.dict(sys.modules, {"remote_trigger_sender": fake}):
        res, tick_at = asyncio.run(scenario())
    assert res["ok"] is True
    assert loop_state == ["no-loop"]
    assert tick_at < 0.4, f"event loop was blocked: 100 ms timer fired at {tick_at:.2f}s"


@pytest.mark.parametrize("category,status", [
    ("timeout_transport", "error"),   # delivered, no answer yet — peer may be running it
    ("timeout_remote", "timeout"),
    ("rejected", "rejected"),
    ("auth_failed", "error"),
    ("protocol_error", "error"),
])
def test_non_unreachable_failures_are_never_resent(category, status):
    calls: list = []
    res = _dispatch(_fake_sender_module(
        [_Res(ok=False, status=status, error_category=category, error_detail="HTTP request timeout",
              data={"secret": "response-data"})], calls))
    assert len(calls) == 1
    assert res["ok"] is False
    assert res["error_category"] == category
    assert "response-data" not in repr(res)


def test_unreachable_is_retried_until_success():
    calls: list = []
    res = _dispatch(_fake_sender_module(
        [_Res(ok=False, error_category="unreachable"),
         _Res(ok=False, error_category="unreachable"),
         _Res(ok=True, status="ok", task_id="remote-3")], calls))
    assert len(calls) == 3
    assert res["ok"] is True and res["remote_task_id"] == "remote-3"


def test_exception_is_not_retried_and_not_leaked():
    calls: list = []
    res = _dispatch(_fake_sender_module([RuntimeError("token=sk-secret host=10.0.0.1")], calls))
    assert len(calls) == 1
    assert res["ok"] is False
    assert res["error_detail"] == "RuntimeError"
    assert "sk-secret" not in repr(res)
