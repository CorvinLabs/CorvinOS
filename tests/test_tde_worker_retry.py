"""ADR-0219 R3: transient-failure retry/backoff for delegated TDE workers.

Before this, a single rate-limit or timeout dropped the step AND every dependent
(no retry anywhere in tde/). Now transient failures retry with bounded
exponential backoff; terminal failures still fail fast (no wasted tokens on a
step that will never succeed, e.g. error_max_turns — the B1 boundary).

Run: python3 -m pytest tests/test_tde_worker_retry.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))
sys.path.insert(0, str(_REPO / "operator"))

from tde import worker_ipc as w  # noqa: E402


def _envelope():
    from tde.adaptive_delegation_executor import DelegationEnvelope
    from initial_analysis import Step, GlobalPlan
    step = Step(step=1, action="analyze", description="x", estimated_tokens=100)
    plan = GlobalPlan(steps=[step], estimated_duration_s=1, estimated_tokens=100)
    return DelegationEnvelope(step=step, decision_context=plan, statement_snapshot={},
                              budget={"max_tokens": 30000}, idempotency_key="r3")


# ── classification ──────────────────────────────────────────────────────────

def test_transient_markers_detected():
    for e in ("worker timeout after 120s", "API rate_limit exceeded",
              "server overloaded", "HTTP 529", "connection reset by peer"):
        assert w._is_transient_error(e), e


def test_terminal_errors_are_not_transient():
    for e in ("worker envelope error: error_max_turns", "invalid step",
              "worker exit 2: bad prompt", None, ""):
        assert not w._is_transient_error(e), e


# ── retry loop ──────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_transient_then_success_retries_and_succeeds():
    ipc = w.SubprocessWorkerIPC()
    calls = []

    def fake_run(prompt, proc_holder=None):
        calls.append(1)
        if len(calls) < 3:
            return {"success": False, "output": None, "error": "rate_limit"}
        return {"success": True, "output": "done", "error": None, "usage": {"total_tokens": 5}}

    with patch.object(ipc, "_run_worker", side_effect=fake_run), \
         patch.object(w.asyncio, "sleep", new=_noop_sleep):
        res = _run(ipc.send_delegation(_envelope()))
    assert res["success"] and res["output"] == "done"
    assert len(calls) == 3
    assert res.get("retries") == 2


def test_terminal_error_fails_fast_no_retry():
    ipc = w.SubprocessWorkerIPC()
    calls = []

    def fake_run(prompt, proc_holder=None):
        calls.append(1)
        return {"success": False, "output": None,
                "error": "worker envelope error: error_max_turns"}

    with patch.object(ipc, "_run_worker", side_effect=fake_run), \
         patch.object(w.asyncio, "sleep", new=_noop_sleep):
        res = _run(ipc.send_delegation(_envelope()))
    assert not res["success"]
    assert len(calls) == 1, "terminal error must NOT retry"


def test_persistent_transient_exhausts_attempts():
    ipc = w.SubprocessWorkerIPC()
    calls = []

    def fake_run(prompt, proc_holder=None):
        calls.append(1)
        return {"success": False, "output": None, "error": "overloaded"}

    with patch.object(ipc, "_run_worker", side_effect=fake_run), \
         patch.object(w.asyncio, "sleep", new=_noop_sleep):
        res = _run(ipc.send_delegation(_envelope()))
    assert not res["success"]
    assert len(calls) == w._WORKER_MAX_ATTEMPTS, "must stop at the attempt cap"


async def _noop_sleep(_delay):  # patched in place of asyncio.sleep
    return None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
