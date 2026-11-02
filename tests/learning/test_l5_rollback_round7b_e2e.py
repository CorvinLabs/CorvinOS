"""ADR-2092 G4 — round-7b findings on the L5 rollback judge (review c120 r7b).

r7b-1: a pending entry (lock-failure hold, judged-but-unrecorded trip) was dropped as
"stale" by a WALL-CLOCK comparison (the reset state's ``at`` vs the entry's
``created``); staleness is now decided by generations only.
r7b-2/-3: the post-reset window was an inode number + byte offset; a re-created ledger
(reused inode) or one truncated in place and regrown hid the post-reset turns. The
position now carries device + inode + a fingerprint of the bytes before the offset,
and any mismatch reads the ledger whole.

Entry points as production calls them: ``delegation_policy.record_turn_outcome``
(starts the judge thread for a Skill-served turn), ``dual_write.decide`` (the phase
gate) and ``POST /v1/console/learning/routing/rollback/reset`` through the real
console router with a real session + CSRF token.
"""
from __future__ import annotations

import json
import os
import sys
import time as _real_time
import types

import pytest

from tests.learning.console_client import console_client
from tests.learning.test_l5_rollback_lock_and_window_e2e import _phase, _seed, _skill_turn
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


def _clock(offset: float):
    return types.SimpleNamespace(
        time=lambda: _real_time.time() + offset, time_ns=_real_time.time_ns,
        monotonic=_real_time.monotonic, sleep=_real_time.sleep)


def _lock(sb):
    from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

    return routing_ledger.routing_dir(sb.tenant_id) / rollback_detector.LOCK_FILENAME


def _reset_with_clock(sb, monkeypatch, *, reset_offset: float, after_offset: float) -> None:
    """Trip, reset with the detector's clock at ``reset_offset``, then run on with it
    at ``after_offset`` (an NTP step between the reset and what follows)."""
    from core.skills.os_skills.monitoring import rollback_detector

    _trip(sb)
    monkeypatch.setattr(rollback_detector, "time", _clock(reset_offset))
    r = sb.client.post(RESET, headers=sb.csrf_headers)
    assert r.status_code == 200, r.text
    monkeypatch.setattr(rollback_detector, "time", _clock(after_offset))


# ── r7b-1: staleness by generations only ─────────────────────────────────────

# (reset clock, clock afterwards): a reset taken +1 h ahead, then NTP steps back;
# a reset at the true time, then the clock steps 1 h back.
_CLOCKS = [pytest.param(3600.0, 0.0, id="reset_clock_plus_1h"),
           pytest.param(0.0, -3600.0, id="clock_minus_1h_after_reset")]


@pytest.mark.parametrize("reset_offset,after_offset", _CLOCKS)
def test_a_lock_failure_hold_survives_a_clock_step_around_the_reset(
        tmp_path, monkeypatch, reset_offset, after_offset):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _reset_with_clock(sb, monkeypatch, reset_offset=reset_offset,
                          after_offset=after_offset)
        lock = _lock(sb)
        os.chmod(lock, 0o400)
        try:
            _skill_turn(sb.tenant_id, ok=False)        # the judge: lock unavailable -> hold
            assert rollback_detector.rollback_active(sb.tenant_id), \
                "the hold was erased by a wall-clock comparison"
            assert sb.tenant_id in rollback_detector._pending_trips
            assert rollback_detector.pending_path(sb.tenant_id).exists()
            assert _phase(sb.tenant_id).reason == "rollback_active"
        finally:
            os.chmod(lock, 0o600)
        assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                               "routing.rollback_unverified"]


@pytest.mark.parametrize("reset_offset,after_offset", _CLOCKS)
def test_a_judged_trip_whose_record_failed_survives_a_clock_step_around_the_reset(
        tmp_path, monkeypatch, reset_offset, after_offset):
    with console_client(tmp_path) as sb:
        import delegation_policy
        from core.skills.os_skills.monitoring import rollback_detector

        _reset_with_clock(sb, monkeypatch, reset_offset=reset_offset,
                          after_offset=after_offset)
        _seed(sb.tenant_id, 150, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        real_audit = delegation_policy._audit

        def _trip_fails(et, d, *, tenant_id):
            if et == "routing.rollback_triggered":
                return False
            return real_audit(et, d, tenant_id=tenant_id)
        monkeypatch.setattr(delegation_policy, "_audit", _trip_fails)
        _skill_turn(sb.tenant_id, ok=False)            # judged; its record does not commit
        monkeypatch.setattr(delegation_policy, "_audit", real_audit)
        assert rollback_detector.rollback_active(sb.tenant_id), \
            "a judged trip was erased by a wall-clock comparison"
        assert rollback_detector.pending_path(sb.tenant_id).exists()
        _seed(sb.tenant_id, 1000, source="skill", ok=True)   # the window recovers
        _skill_turn(sb.tenant_id, ok=True)             # the retry completes the JUDGED trip
        assert rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                               "routing.rollback_triggered"]
        rec = _named(sb, "routing.rollback_triggered")[-1]
        assert (rec.get("details") or {}).get("skill_n") == 151
        assert _named(sb, "routing.rollback_hold_released") == []


def test_a_newer_persisted_generation_still_drops_a_stale_in_process_entry(
        tmp_path, monkeypatch):
    """No regression of r3_s2-5: another process's reset (a newer generation) wins over
    this process's judged-but-unrecorded trip."""
    with console_client(tmp_path) as sb:
        import delegation_policy
        from core.skills.os_skills.monitoring import rollback_detector

        _seed(sb.tenant_id, 150, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        real_audit = delegation_policy._audit
        monkeypatch.setattr(delegation_policy, "_audit", lambda et, d, *, tenant_id: (
            False if et == "routing.rollback_triggered" else real_audit(et, d, tenant_id=tenant_id)))
        _skill_turn(sb.tenant_id, ok=False)
        monkeypatch.setattr(delegation_policy, "_audit", real_audit)
        entry = dict(rollback_detector._pending_trips[sb.tenant_id])
        gen_before = rollback_detector.persisted_generation(sb.tenant_id)
        assert entry["generation"] == gen_before
        r = sb.client.post(RESET, headers=sb.csrf_headers)     # the operator, "elsewhere"
        assert r.status_code == 200, r.text
        rollback_detector._pending_trips[sb.tenant_id] = entry  # this process's memory
        assert rollback_detector.persisted_generation(sb.tenant_id) > gen_before
        assert rollback_detector.rollback_active(sb.tenant_id) is False
        assert sb.tenant_id not in rollback_detector._pending_trips
        _skill_turn(sb.tenant_id, ok=True)
        assert _named(sb, "routing.rollback_triggered") == []
        assert _events(sb) == ["routing.rollback_reset"]


# ── r7b-2/-3: the ledger position names content, not an inode number ─────────

def _assert_post_reset_failures_trip(sb) -> None:
    from core.skills.os_skills.monitoring import rollback_detector

    assert rollback_detector.rollback_active(sb.tenant_id), \
        "151 failing Skill turns after the reset did not trip (window blinded)"
    assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset",
                           "routing.rollback_triggered"]
    state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
    assert state["tripped"] is True and state["skill_n"] == 151


def _post_reset_failures(sb) -> None:
    _seed(sb.tenant_id, 150, source="skill", ok=False)
    _seed(sb.tenant_id, 150, source="bundled", ok=True)
    _skill_turn(sb.tenant_id, ok=False)


def test_a_recreated_ledger_with_a_reused_inode_is_read_whole(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        led = routing_ledger.ledger_path(sb.tenant_id)
        led.unlink()                                  # the operator frees disk space
        _seed(sb.tenant_id, 150, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        # Simulate inode-number reuse deterministically: the stored position names the
        # NEW file's device and inode (the filesystem here reuses it on its own too).
        st = os.stat(led)
        sp = rollback_detector.state_path(sb.tenant_id)
        state = json.loads(sp.read_text())
        assert state["ledger_pos"]["offset"] < st.st_size
        state["ledger_pos"]["ino"] = st.st_ino
        state["ledger_pos"]["dev"] = st.st_dev
        tmp = sp.with_name(sp.name + ".sim.tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(sp)
        _skill_turn(sb.tenant_id, ok=False)
        _assert_post_reset_failures_trip(sb)
        _verify_chain(sb)


def test_a_ledger_truncated_in_place_and_regrown_is_read_whole(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        pos = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())["ledger_pos"]
        assert set(pos) == {"dev", "ino", "offset", "fp"}
        led = routing_ledger.ledger_path(sb.tenant_id)
        ino = os.stat(led).st_ino
        os.truncate(led, 0)                           # copytruncate / `: > file`
        _post_reset_failures(sb)
        assert os.stat(led).st_ino == ino and os.stat(led).st_size > pos["offset"]
        _assert_post_reset_failures_trip(sb)


def test_an_untouched_ledger_still_keeps_the_pre_reset_rows_out(tmp_path):
    """The fingerprint matches on an append-only ledger: the window starts at the reset."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        _skill_turn(sb.tenant_id, ok=False)
        assert not rollback_detector.rollback_active(sb.tenant_id)
        assert _events(sb) == ["routing.rollback_triggered", "routing.rollback_reset"]
