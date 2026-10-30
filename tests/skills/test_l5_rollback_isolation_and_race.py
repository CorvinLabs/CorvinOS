"""L5 Phase 2 rollback — tenant-scoped readiness invalidation and the trip/reset race.

Review r4_s2-2: a trip or reset of tenant A emptied the WHOLE readiness cache, so tenant
B's next console turn was served ``evaluating`` (recorded as a ``pending`` shadow row)
and B's bridge soak restarted. Review r4_s2-3: a reset landing between a trip's
committed ``routing.rollback_triggered`` record and its state write answered "not
tripped" while the trip then overwrote the reset (chain: reset last, state: tripped).

Entry points as production calls them: ``dual_write.decide`` (the phase gate every
console/bridge turn asks), ``rollback_detector.evaluate`` (the judge) and
``rollback_detector.reset_rollback(require_active=True)`` (what the reset route runs).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "bridges" / "shared"))

from core.skills.os_skills.monitoring import (  # noqa: E402
    dual_write,
    readiness,
    rollback_detector,
    routing_ledger,
)

A, B = "alpha", "beta"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_ACP_PHASE", raising=False)
    dual_write.reset_for_tests()
    yield
    dual_write.reset_for_tests()


def _seed(tenant: str, n: int, *, source: str, ok: bool) -> None:
    for _ in range(n):
        tid = routing_ledger.new_turn_id()
        routing_ledger.record_decision(
            tenant_id=tenant, turn_id=tid, surface="console", phase="dual_write",
            bundled="acs" if source == "skill" else "native", used="native",
            source=source, skill="native", skill_conf=0.9, features={"complexity": "simple"})
        routing_ledger.record_outcome(tenant_id=tenant, turn_id=tid, surface="console",
                                      used="native", ok=ok, latency_ms=5)


def _trailing_skill_ledger(tenant: str) -> None:
    _seed(tenant, 100, source="skill", ok=False)
    _seed(tenant, 100, source="bundled", ok=True)


# ── r4_s2-2: invalidation is per tenant ─────────────────────────────────────

class TestReadinessInvalidationIsPerTenant:
    def test_another_tenants_trip_and_reset_keep_this_tenants_fresh_verdict(self, monkeypatch):
        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(B, 10, source="bundled", ok=True)
        warm = readiness.cached_evaluate(B, "console", background=False)
        assert warm.reason != readiness.EVALUATING_REASON
        before = dual_write.decide(tenant_id=B, surface="console", skill_available=True)
        assert before.reason == warm.reason

        _trailing_skill_ledger(A)
        assert rollback_detector.evaluate(A, audit=lambda et, d: True) is True   # A trips
        rollback_detector.reset_rollback(A, audit=lambda et, d: True,           # A resets
                                         require_active=True)

        after = dual_write.decide(tenant_id=B, surface="console", skill_available=True)
        assert after.reason == warm.reason, (
            f"tenant {B}'s cached verdict was dropped by tenant {A}'s trip/reset "
            f"(served {after.reason!r})")

    def test_the_tripping_tenants_own_verdict_is_invalidated(self, monkeypatch):
        monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
        _seed(A, 10, source="bundled", ok=True)
        readiness.cached_evaluate(A, "console", background=False)
        rollback_detector._write_state(A, {"tripped": True, "at": time.time()})
        rollback_detector.reset_rollback(A, audit=lambda et, d: True)
        with readiness._lock:
            assert (A, "console") not in readiness._cache


# ── r4_s2-3: a reset never interleaves with a trip ──────────────────────────

def _append(chain: str, event: str, tenant: str) -> None:
    with open(chain, "a") as fh:
        fh.write(json.dumps({"event_type": event, "tenant_id": tenant}) + "\n")


def _trip_child(home: str, chain: str, barrier, hold_s: float) -> None:
    import os as _os
    _os.environ["CORVIN_HOME"] = home

    def audit(event_type: str, details: dict) -> bool:
        _append(chain, event_type, details["tenant_id"])
        if barrier is not None:
            barrier.wait(10)       # the record is on the chain: release the reset NOW
        time.sleep(hold_s)         # ... the writer returns a moment later
        return True
    rollback_detector.evaluate(A, audit=audit)


def _reset_child(home: str, chain: str, barrier, result: str, delay_s: float) -> None:
    import os as _os
    _os.environ["CORVIN_HOME"] = home
    if barrier is not None:
        barrier.wait(10)
    time.sleep(delay_s)
    try:
        rollback_detector.reset_rollback(
            A, audit=lambda et, d: (_append(chain, et, d["tenant_id"]), True)[1],
            require_active=True)
        answer = "200"
    except rollback_detector.NotTripped:
        answer = "409"
    except RuntimeError:
        answer = "503"
    Path(result).write_text(answer)


def _race(tmp_path, *, mode: str) -> tuple[list[str], dict, str]:
    """``during``: the reset is released right after the trip's record committed;
    ``before``: the reset runs before the trip starts; ``after``: after it finished."""
    import multiprocessing
    import os as _os
    ctx = multiprocessing.get_context("fork")
    home = _os.environ["CORVIN_HOME"]
    chain = str(tmp_path / f"chain-{mode}.jsonl")
    result = str(tmp_path / f"reset-{mode}.txt")
    _trailing_skill_ledger(A)
    barrier = ctx.Barrier(2) if mode == "during" else None
    trip = ctx.Process(target=_trip_child, args=(home, chain, barrier, 0.5))
    reset = ctx.Process(target=_reset_child, args=(home, chain, barrier, result, 0.0))
    if mode == "before":
        reset.start(); reset.join(20); trip.start(); trip.join(20)
    elif mode == "after":
        trip.start(); trip.join(20); reset.start(); reset.join(20)
    else:
        trip.start(); reset.start(); trip.join(20); reset.join(20)
    assert trip.exitcode == 0 and reset.exitcode == 0
    events = [json.loads(line)["event_type"] for line in Path(chain).read_text().splitlines()]
    state = json.loads(rollback_detector.state_path(A).read_text())
    return events, state, Path(result).read_text()


needs_fork = pytest.mark.skipif(sys.platform == "win32", reason="fork start method")


@needs_fork
class TestResetTripRace:
    @pytest.mark.parametrize("mode", ["during", "before", "after"])
    def test_chain_state_and_answer_agree_in_every_interleaving(self, tmp_path, mode):
        events, state, answer = _race(tmp_path, mode=mode)
        rollback_detector.reset_for_tests()          # read what a fresh process sees
        last = events[-1]
        if last == "routing.rollback_reset":
            assert state["tripped"] is False, (events, state, answer)
            assert answer == "200"
        else:
            assert last == "routing.rollback_triggered"
            assert state["tripped"] is True, (events, state, answer)
            assert answer != "200", (events, state, answer)
        assert rollback_detector.rollback_active(A) is state["tripped"]
        if mode == "before":
            assert answer == "409" and events == ["routing.rollback_triggered"]
        else:
            assert events == ["routing.rollback_triggered", "routing.rollback_reset"]
            assert answer == "200"

    def test_a_committed_trip_whose_state_failed_is_persisted_before_the_reset(self, monkeypatch):
        """The trip's state write failed (marker committed=True): the reset writes that
        state first, then its record, then its own state."""
        import errno
        _trailing_skill_ledger(A)
        real = rollback_detector.os.replace
        fail = {"on": True}

        def flaky(src, dst, *a, **k):
            if fail["on"] and Path(dst).name == rollback_detector.STATE_FILENAME:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(src, dst, *a, **k)
        monkeypatch.setattr(rollback_detector.os, "replace", flaky)
        calls: list[str] = []
        rollback_detector.evaluate(A, audit=lambda et, d: (calls.append(et), True)[1])
        assert not rollback_detector.state_path(A).exists()
        # Still failing: refused before the reset's record, the trip stays active.
        with pytest.raises(rollback_detector.ResetNotApplied):
            rollback_detector.reset_rollback(
                A, audit=lambda et, d: (calls.append(et), True)[1], require_active=True)
        assert calls == ["routing.rollback_triggered"]
        assert rollback_detector.rollback_active(A)
        fail["on"] = False
        rollback_detector.reset_rollback(
            A, audit=lambda et, d: (calls.append(et), True)[1], require_active=True)
        assert calls == ["routing.rollback_triggered", "routing.rollback_reset"]
        state = json.loads(rollback_detector.state_path(A).read_text())
        assert state["tripped"] is False and state["generation"] == 2
        assert rollback_detector.rollback_active(A) is False
