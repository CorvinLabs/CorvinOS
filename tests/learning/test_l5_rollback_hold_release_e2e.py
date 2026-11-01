"""ADR-2092 G4 — an unverified rollback hold is released (audited), and the post-reset
window is a ledger position, not a wall-clock time.

Review c120 r6_s2-1: one transient lock failure held a HEALTHY tenant forever — the next
judge that got the lock turned the hold into a ``routing.rollback_triggered`` with equal
success rates (and, with the marker unreadable inside the lock, a trip with 0 samples).
Review c120 r6_s2-2: the reset window compared two wall-clock readings, so a clock step
blinded the judge. Review c120 r6_s2-3: under EMFILE the hold was never written.

Entry points as production calls them: ``delegation_policy.record_turn_outcome`` (starts
the judge thread for a Skill-served turn), ``dual_write.decide`` (the phase gate; it
retries a held tenant on the ``l5-rollback-retry`` thread) and
``POST /v1/console/learning/routing/rollback/reset`` through the real console router.
"""
from __future__ import annotations

import json
import os
import threading
import time as _real_time
import types

import pytest

from tests.learning.console_client import console_client
from tests.learning.test_l5_rollback_lock_and_window_e2e import (
    _join_judges, _phase, _seed, _skill_turn,
)
from tests.learning.test_l5_rollback_reset_route_e2e import RESET, _named, _trip, _verify_chain

_NAMES = ("routing.rollback_triggered", "routing.rollback_reset",
          "routing.rollback_unverified", "routing.rollback_reset_not_applied",
          "routing.rollback_hold_released")


def _events(sb) -> list[str]:
    out = []
    for r in sb.chain_records():
        ev = r.get("event") or r.get("event_type")
        if ev in _NAMES:
            out.append(ev)
    return out


@pytest.fixture(autouse=True)
def _phase2(monkeypatch):
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    yield
    from core.skills.os_skills.monitoring import dual_write, rollback_detector

    rollback_detector.reset_for_tests()
    dual_write.reset_for_tests()


def _lock(sb):
    from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

    return routing_ledger.routing_dir(sb.tenant_id) / rollback_detector.LOCK_FILENAME


def _break_lock(sb) -> None:
    lock = _lock(sb)
    lock.parent.mkdir(parents=True, exist_ok=True)
    if not lock.exists():
        lock.write_text("")
    os.chmod(lock, 0o400)


def _fix_lock(sb) -> None:
    os.chmod(_lock(sb), 0o600)


# ── r6_s2-1: a transient lock failure on a healthy tenant ────────────────────

def test_transient_lock_failure_on_a_healthy_tenant_is_released_audited(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=True)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        _break_lock(sb)
        try:
            _skill_turn(sb.tenant_id, ok=True)        # ONE judge during the failure
            assert rollback_detector.rollback_active(sb.tenant_id)
            assert _events(sb) == ["routing.rollback_unverified"]
        finally:
            _fix_lock(sb)
        _skill_turn(sb.tenant_id, ok=True)            # the lock works again
        assert not rollback_detector.rollback_active(sb.tenant_id), \
            "a healthy tenant stayed tripped after one transient lock failure"
        assert _events(sb) == ["routing.rollback_unverified", "routing.rollback_hold_released"]
        assert _named(sb, "routing.rollback_triggered") == []
        rec = _named(sb, "routing.rollback_hold_released")[0]
        details = rec.get("details") or {}
        assert details.get("cause") == "lock_unavailable"
        assert details.get("skill_n") == 122 and details.get("bundled_n") == 120   # 120 + 2 turns
        assert not rollback_detector.pending_path(sb.tenant_id).exists()
        assert _phase(sb.tenant_id).reason != "rollback_active"
        _verify_chain(sb)


def test_the_phase_gate_retry_releases_a_held_healthy_tenant(tmp_path):
    """While held, no turn is Skill-served: the phase gate's retry is what re-judges."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=True)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        _break_lock(sb)
        try:
            _skill_turn(sb.tenant_id, ok=True)
        finally:
            _fix_lock(sb)
        before = {t for t in threading.enumerate() if t.name == "l5-rollback-retry"}
        pd = _phase(sb.tenant_id)
        assert pd.reason == "rollback_active"
        for t in threading.enumerate():
            if t.name == "l5-rollback-retry" and t not in before:
                t.join(timeout=30)
        assert not rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_unverified", "routing.rollback_hold_released"]
        _verify_chain(sb)


def test_a_hold_with_too_few_samples_stays_held(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 50, source="skill", ok=True)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        _break_lock(sb)
        try:
            _skill_turn(sb.tenant_id, ok=True)
        finally:
            _fix_lock(sb)
        _skill_turn(sb.tenant_id, ok=True)
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_unverified"]


def test_a_hold_whose_window_trails_becomes_a_real_trip(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        _break_lock(sb)
        try:
            _skill_turn(sb.tenant_id, ok=False)
        finally:
            _fix_lock(sb)
        _skill_turn(sb.tenant_id, ok=False)
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_unverified", "routing.rollback_triggered"]


def test_a_hold_whose_trip_record_fails_stays_a_judged_trip(tmp_path, monkeypatch):
    """Review c120 r7 (J1): a hold re-judged on a trailing window becomes a JUDGED trip
    BEFORE its record is chained. When ``routing.rollback_triggered`` does not commit,
    the marker and the in-process entry carry the judged details, so a window that
    recovers afterwards never releases the tenant — only the operator reset does."""
    with console_client(tmp_path) as sb:
        import delegation_policy
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        _break_lock(sb)
        try:
            _skill_turn(sb.tenant_id, ok=False)       # the hold
        finally:
            _fix_lock(sb)
        assert _events(sb) == ["routing.rollback_unverified"]

        real_audit = delegation_policy._audit

        def _trip_fails(et, d, *, tenant_id):
            if et == "routing.rollback_triggered":
                return False                          # the record does not commit
            return real_audit(et, d, tenant_id=tenant_id)
        monkeypatch.setattr(delegation_policy, "_audit", _trip_fails)
        _skill_turn(sb.tenant_id, ok=False)           # the window trails: judged now
        monkeypatch.setattr(delegation_policy, "_audit", real_audit)
        assert rollback_detector.rollback_active(sb.tenant_id)
        marker = json.loads(rollback_detector.pending_path(sb.tenant_id).read_text())
        entry = dict(rollback_detector._pending_trips[sb.tenant_id])

        _seed(sb.tenant_id, 300, source="skill", ok=True)    # the window recovers
        _seed(sb.tenant_id, 300, source="bundled", ok=False)
        _skill_turn(sb.tenant_id, ok=True)
        assert rollback_detector.rollback_active(sb.tenant_id), \
            "a trip the judge saw was released without an operator reset"
        assert _named(sb, "routing.rollback_hold_released") == []
        # the failed record left a JUDGED trip behind, on disk and in process
        assert marker.get("cause") is None, marker
        assert isinstance(marker.get("details"), dict) and marker["details"]["skill_n"] == 122
        assert entry.get("cause") is None and isinstance(entry.get("details"), dict)
        # the retry completed the JUDGED trip with the details it was judged on
        assert _events(sb) == ["routing.rollback_unverified", "routing.rollback_triggered"]
        rec = _named(sb, "routing.rollback_triggered")[0]
        assert (rec.get("details") or {}).get("skill_n") == 122
        state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
        assert state["tripped"] is True
        _verify_chain(sb)


def test_a_judged_trip_is_never_released_automatically(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb, commit=False)                       # judged, its record not committed
        _seed(sb.tenant_id, 400, source="skill", ok=True)   # the window recovers
        _skill_turn(sb.tenant_id, ok=True)
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _named(sb, "routing.rollback_hold_released") == []
        assert _events(sb) == ["routing.rollback_triggered"]   # completed, not released


def test_an_unreadable_marker_inside_the_lock_holds_without_a_zero_sample_trip(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=True)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        marker = rollback_detector.pending_path(sb.tenant_id)
        marker.mkdir()                                # read_text -> OSError (EISDIR)
        try:
            assert rollback_detector.evaluate(
                sb.tenant_id, audit=rollback_detector._default_audit(sb.tenant_id)) is True
            assert _events(sb) == [], "a trip was recorded over an unread marker"
        finally:
            marker.rmdir()
        _skill_turn(sb.tenant_id, ok=True)            # next run judges normally
        assert not rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == []


def test_an_unreadable_marker_inside_the_lock_defers_even_a_trailing_trip(tmp_path):
    """The run that cannot read the marker records nothing; the next one judges."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        marker = rollback_detector.pending_path(sb.tenant_id)
        marker.mkdir()
        try:
            assert rollback_detector.evaluate(
                sb.tenant_id, audit=rollback_detector._default_audit(sb.tenant_id)) is True
            assert _events(sb) == [], "the run that could not read the marker judged anyway"
        finally:
            marker.rmdir()
        _skill_turn(sb.tenant_id, ok=False)
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_triggered"]


# ── r6_s2-3: the hold is written when the lock AND the marker are unreadable ─

def test_lock_and_marker_unreadable_the_hold_is_written_and_reported(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        dual_write.reset_for_tests()
        _seed(sb.tenant_id, 120, source="skill", ok=True)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        marker = rollback_detector.pending_path(sb.tenant_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.mkdir()
        _break_lock(sb)
        try:
            assert rollback_detector.evaluate(
                sb.tenant_id, audit=rollback_detector._default_audit(sb.tenant_id)) is True
            assert sb.tenant_id in rollback_detector._pending_trips, "no in-process hold"
        finally:
            marker.rmdir()
            _fix_lock(sb)
        # the obstruction is gone: the hold is real (in-process), not a fail-closed read
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _phase(sb.tenant_id).reason == "rollback_active"
        _join_judges()
        for t in threading.enumerate():
            if t.name == "l5-rollback-retry":
                t.join(timeout=30)
        _skill_turn(sb.tenant_id, ok=True)
        assert not rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_unverified", "routing.rollback_hold_released"]
        _verify_chain(sb)


# ── r6_s2-2: the post-reset window is a ledger position ──────────────────────

def _clock(offset: float):
    return types.SimpleNamespace(
        time=lambda: _real_time.time() + offset, time_ns=_real_time.time_ns,
        monotonic=_real_time.monotonic, sleep=_real_time.sleep)


def _assert_post_reset_failures_trip(sb) -> None:
    from core.skills.os_skills.monitoring import rollback_detector

    assert rollback_detector.rollback_active(sb.tenant_id), \
        "150 failing Skill turns after the reset did not trip (window blinded by the clock)"
    assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                           "routing.rollback_triggered"]
    state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
    assert state["tripped"] is True and state["skill_n"] == 150


def test_a_reset_taken_with_the_clock_ahead_does_not_blind_the_judge(tmp_path, monkeypatch):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb)
        monkeypatch.setattr(rollback_detector, "time", _clock(3600.0))
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        monkeypatch.setattr(rollback_detector, "time", _real_time)   # NTP steps back
        state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
        assert state["at"] > _real_time.time() + 3000 and "ledger_pos" in state
        _seed(sb.tenant_id, 149, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        _skill_turn(sb.tenant_id, ok=False)
        _assert_post_reset_failures_trip(sb)
        _verify_chain(sb)


def test_a_clock_step_back_after_the_reset_does_not_change_the_result(tmp_path, monkeypatch):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        monkeypatch.setattr(routing_ledger, "time", _clock(-3600.0))   # rows stamped 1 h back
        _seed(sb.tenant_id, 149, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        _skill_turn(sb.tenant_id, ok=False)
        _assert_post_reset_failures_trip(sb)


def test_a_clock_step_back_still_keeps_the_pre_reset_rows_out(tmp_path, monkeypatch):
    """The position bounds both ends: pre-reset rows stamped LATER than the reset
    (clock ahead while they were written) stay out of the post-reset window."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        monkeypatch.setattr(routing_ledger, "time", _clock(7200.0))
        _trip(sb)                                    # pre-reset rows stamped +2 h
        monkeypatch.setattr(routing_ledger, "time", _real_time)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        _skill_turn(sb.tenant_id, ok=False)
        assert not rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset"]
