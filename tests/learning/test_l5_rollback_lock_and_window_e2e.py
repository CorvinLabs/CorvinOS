"""ADR-2092 G4 — the rollback judge fails closed on its lock, and a reset opens a fresh window.

Review c120 r5_s2-1: the per-tenant ``rollback.lock`` was opened before anything else;
when it could not be opened the judge raised, the production judge thread swallowed it,
and a trailing Skill kept serving Phase 2 with no record (and the reset route answered an
unhandled 500). Review c120 r5_s2-2: after an operator reset the judge still evaluated
the pre-reset window, so the first Skill-served turn re-tripped the tenant.

Entry points as production calls them: ``delegation_policy.record_turn_outcome`` (the
outcome writer that starts the judge thread for a Skill-served turn), ``dual_write.decide``
(the phase gate every turn asks) and ``POST /v1/console/learning/routing/rollback/reset``
through the real console router with a real session + CSRF token.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

from tests.learning.console_client import console_client
from tests.learning.test_l5_rollback_reset_route_e2e import RESET, _named, _trip, _verify_chain

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "bridges" / "shared"))


def _seed(tenant: str, n: int, *, source: str, ok: bool) -> None:
    from core.skills.os_skills.monitoring import routing_ledger

    for _ in range(n):
        tid = routing_ledger.new_turn_id()
        routing_ledger.record_decision(
            tenant_id=tenant, turn_id=tid, surface="console", phase="dual_write",
            bundled="acs" if source == "skill" else "native", used="native",
            source=source, skill="native", skill_conf=0.9, features={"complexity": "simple"})
        routing_ledger.record_outcome(tenant_id=tenant, turn_id=tid, surface="console",
                                      used="native", ok=ok, latency_ms=5)


def _skill_turn(tenant: str, *, ok: bool) -> None:
    """One Skill-served console turn: the decision row, then the production outcome
    writer — which starts the rollback judge on a daemon thread. Waits for the judge."""
    import delegation_policy
    from core.skills.os_skills.monitoring import routing_ledger

    tid = routing_ledger.new_turn_id()
    routing_ledger.record_decision(
        tenant_id=tenant, turn_id=tid, surface="console", phase="dual_write",
        bundled="acs", used="native", source="skill", skill="native", skill_conf=0.9,
        features={"complexity": "simple"})
    before = {t for t in threading.enumerate() if t.name == "l5-rollback-judge"}
    delegation_policy.record_turn_outcome(
        tenant_id=tenant, turn_id=tid, surface="console", used="native", ok=ok,
        latency_ms=5, source="skill")
    _join_judges(before)


def _join_judges(before=frozenset()) -> None:
    for t in threading.enumerate():
        if t.name == "l5-rollback-judge" and t not in before:
            t.join(timeout=30)
            assert not t.is_alive(), "rollback judge hung"


#: Every background thread the L5 rollback/readiness machinery starts. Each one resolves
#: its paths from the PROCESS environment when it runs, so one still alive after its test
#: acts on the NEXT test's sandbox (an ``l5-rollback-retry`` evaluate is a second judge).
_L5_THREADS = ("l5-rollback-judge", "l5-rollback-retry", "l5-readiness")


def _join_background(timeout: float = 60.0) -> None:
    """Wait (join, no sleep) for every L5 background thread alive now."""
    for t in threading.enumerate():
        if t.name in _L5_THREADS and t is not threading.current_thread():
            t.join(timeout=timeout)
            assert not t.is_alive(), f"{t.name} hung"


def _phase(tenant: str):
    from core.skills.os_skills.monitoring import dual_write

    return dual_write.decide(tenant_id=tenant, surface="console", skill_available=True)


def _events(sb) -> list[str]:
    names = ("routing.rollback_triggered", "routing.rollback_reset",
             "routing.rollback_unverified", "routing.rollback_reset_not_applied")
    out = []
    for r in sb.chain_records():
        ev = r.get("event") or r.get("event_type")
        if ev in names:
            out.append(ev)
    return out


@pytest.fixture(autouse=True)
def _phase2(monkeypatch):
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    yield
    from core.skills.os_skills.monitoring import dual_write, rollback_detector

    rollback_detector.reset_for_tests()
    dual_write.reset_for_tests()


# ── r5_s2-1: an unopenable lock holds the tenant tripped ─────────────────────

@pytest.mark.parametrize("mode", ["ro_lock", "ro_dir"])
def test_unopenable_rollback_lock_holds_phase2_off_and_is_chained(tmp_path, mode):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector, routing_ledger

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        routing = routing_ledger.routing_dir(sb.tenant_id)
        lock = routing / rollback_detector.LOCK_FILENAME
        if mode == "ro_lock":
            lock.write_text("")
            os.chmod(lock, 0o400)
        else:
            assert not lock.exists()
            os.chmod(routing, 0o500)
        try:
            _skill_turn(sb.tenant_id, ok=False)
            assert rollback_detector.rollback_active(sb.tenant_id), \
                "lock unavailable read as NOT tripped (fail-open)"
            pd = _phase(sb.tenant_id)
            assert (pd.phase, pd.reason) == ("shadow", "rollback_active")
            assert _events(sb) == ["routing.rollback_unverified"]
            rec = _named(sb, "routing.rollback_unverified")[0]
            details = rec.get("details") or {}
            assert details.get("cause") == "lock_unavailable"
            assert "error" not in details and "reason" not in details   # enums only
            marker = rollback_detector.pending_path(sb.tenant_id)
            assert marker.exists() is (mode == "ro_lock")
            _skill_turn(sb.tenant_id, ok=False)        # a second judge: no second record
            assert _events(sb) == ["routing.rollback_unverified"]
        finally:
            os.chmod(routing, 0o700)
            if lock.exists():
                os.chmod(lock, 0o600)
        # The lock is usable again: the held trip is completed by the next judge.
        _skill_turn(sb.tenant_id, ok=False)
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_unverified", "routing.rollback_triggered"]
        _verify_chain(sb)


def test_reset_route_with_unopenable_lock_is_503_and_still_tripped(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        _trip(sb)
        lock = routing_ledger.routing_dir(sb.tenant_id) / rollback_detector.LOCK_FILENAME
        assert lock.exists()
        os.chmod(lock, 0o400)
        try:
            r = sb.client.post(RESET, headers=sb.csrf_headers)
            assert r.status_code == 503, r.text
            assert r.json()["detail"] == {"error": "lock_unavailable"}
            assert rollback_detector.rollback_active(sb.tenant_id)
            assert _named(sb, "routing.rollback_reset") == []
        finally:
            os.chmod(lock, 0o600)
        r = sb.client.post(RESET, headers=sb.csrf_headers)          # lock fixed: reset works
        assert r.status_code == 200, r.text
        assert not rollback_detector.rollback_active(sb.tenant_id)


# ── r5_s2-2: a reset opens a fresh window ────────────────────────────────────

def _assert_reset_held(sb) -> None:
    from core.skills.os_skills.monitoring import rollback_detector

    assert not rollback_detector.rollback_active(sb.tenant_id), \
        "the first Skill-served turn after a reset re-tripped on the pre-reset window"
    assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset"]
    state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
    assert state["tripped"] is False


def test_first_skill_turn_after_a_reset_does_not_retrip_on_the_old_window(tmp_path):
    with console_client(tmp_path) as sb:
        _trip(sb)                                   # 100 failing Skill rows vs 100 ok bundled
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        _skill_turn(sb.tenant_id, ok=False)          # would trip on the old window
        _assert_reset_held(sb)
        _verify_chain(sb)


def test_a_judge_queued_behind_a_reset_does_not_retrip_on_the_old_window(tmp_path, monkeypatch):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb)
        real_default = rollback_detector._default_audit
        started: list[threading.Thread] = []

        def default_audit(tid):
            emit = real_default(tid)

            def _emit(event_type, details):
                ok = emit(event_type, details)
                if event_type == "routing.rollback_reset":
                    # A Skill-served turn finishes while the reset holds the lock:
                    # its judge queues on rollback.lock behind the reset.
                    t = threading.Thread(target=_skill_turn, args=(sb.tenant_id,),
                                         kwargs={"ok": False}, name="queued-turn")
                    t.start()
                    started.append(t)
                return ok
            return _emit
        monkeypatch.setattr(rollback_detector, "_default_audit", default_audit)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        for t in started:
            t.join(timeout=30)
        _join_judges()
        assert started, "the queued judge never ran"
        _assert_reset_held(sb)
