"""ADR-2092 G4 — round-8 findings on the L5 rollback judge (review c120 M1).

M1-1: a state write over an UNREADABLE rollback state (torn, a non-object, EACCES)
restarted the generation at 1 (``(persisted_generation() or 0) + 1`` with
``persisted_generation()`` None). Every pending trip another process held under a
higher generation then no longer read as stale, so the operator's reset of a torn
state was overridden by that process's next judge (a second ``routing.rollback_triggered``
after ``routing.rollback_reset``). A write over an unreadable state now takes a
generation above every counter value (a nanosecond clock reading, never below the
highest generation still visible).

M1-2: a process holding a JUDGED trip whose record had not committed re-chained
``routing.rollback_triggered`` although another process had already committed that
trip (its marker says ``committed: true``, its state write failed). r7b-6 re-read the
marker only for an in-process HOLD; now every uncommitted in-process entry yields to a
committed judged marker under the lock.

M1-3: an entry created while the state could not be read (a lock-failure hold, or a
trip judged from it) carried ``generation: None`` and so never read as stale: after an
operator reset written by another process, the process holding it (the bridge has no
reset route) stayed held until it restarted. Such an entry now records the inode of the
unreadable state file; once the state is readable again under ANOTHER inode (it was
replaced by a write: a reset or a trip), the entry is dropped. The same inode readable
again (a transient EACCES/EMFILE) keeps it.

Entry points as production calls them: ``delegation_policy.record_turn_outcome``
(starts the judge thread for a Skill-served turn) and ``POST
/v1/console/learning/routing/rollback/reset`` through the real console router with a
real session + CSRF token.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from tests.learning.console_client import console_client
from tests.learning.test_l5_rollback_lock_and_window_e2e import _join_background, _seed, _skill_turn
from tests.learning.test_l5_rollback_reset_route_e2e import RESET, _named, _trip
from tests.learning.test_l5_rollback_round7b_e2e import _CHILD, _events


@pytest.fixture(autouse=True)
def _phase2(monkeypatch):
    _join_background()  # a straggler from an earlier test must not judge this sandbox
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    yield
    from core.skills.os_skills.monitoring import dual_write, rollback_detector

    _join_background()  # nor may one of ours outlive this test
    rollback_detector.reset_for_tests()
    dual_write.reset_for_tests()


def _fail_trip_records(monkeypatch):
    """Make ``routing.rollback_triggered`` fail to commit in THIS process; returns the
    restore callable."""
    import delegation_policy

    real_audit = delegation_policy._audit
    monkeypatch.setattr(delegation_policy, "_audit", lambda et, d, *, tenant_id: (
        False if et == "routing.rollback_triggered"
        else real_audit(et, d, tenant_id=tenant_id)))
    return lambda: monkeypatch.setattr(delegation_policy, "_audit", real_audit)


# ── M1-1: a write over an unreadable state never lowers the generation ──────

@pytest.mark.parametrize("content", ["{", "[]", "null"], ids=["torn", "list", "null"])
def test_a_reset_over_an_unreadable_state_outranks_older_pending_trips(
        tmp_path, monkeypatch, content):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        gen_reset = rollback_detector.persisted_generation(sb.tenant_id)
        assert gen_reset >= 2
        # This process (think: the bridge) judges a trip whose record fails.
        restore = _fail_trip_records(monkeypatch)
        _seed(sb.tenant_id, 150, source="skill", ok=False)
        _seed(sb.tenant_id, 150, source="bundled", ok=True)
        _skill_turn(sb.tenant_id, ok=False)
        restore()
        entry = dict(rollback_detector._pending_trips[sb.tenant_id])
        assert entry["details"] is not None and entry["committed"] is False
        assert entry["generation"] == gen_reset
        # The state becomes unreadable; the tenant reads tripped (fail-closed) and the
        # operator resets it — "elsewhere", i.e. another process does the reset.
        rollback_detector.state_path(sb.tenant_id).write_text(content)
        assert rollback_detector.rollback_active(sb.tenant_id)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        rollback_detector._pending_trips[sb.tenant_id] = entry   # this process's memory
        assert rollback_detector.persisted_generation(sb.tenant_id) > entry["generation"], \
            "the reset over an unreadable state restarted the generation"
        assert rollback_detector.rollback_active(sb.tenant_id) is False
        _skill_turn(sb.tenant_id, ok=True)
        assert _events(sb)[-2:] == ["routing.rollback_reset", "routing.rollback_reset"], \
            "the operator's reset was overridden by a stale pending trip"
        assert rollback_detector.rollback_active(sb.tenant_id) is False


def test_generations_keep_rising_after_a_write_over_an_unreadable_state(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _trip(sb)
        rollback_detector.state_path(sb.tenant_id).write_text("{")
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        g1 = rollback_detector.persisted_generation(sb.tenant_id)
        _trip(sb)
        g2 = rollback_detector.persisted_generation(sb.tenant_id)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        g3 = rollback_detector.persisted_generation(sb.tenant_id)
        assert g1 < g2 < g3 and g2 == g1 + 1 and g3 == g2 + 1


# ── M1-2: one trip, one record, also for a judged in-process entry ──────────

@pytest.mark.skipif(sys.platform == "win32", reason="separate interpreter + POSIX env")
def test_a_judged_unrecorded_trip_does_not_rechain_a_trip_another_process_committed(
        tmp_path, monkeypatch):
    import subprocess

    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _seed(sb.tenant_id, 120, source="skill", ok=False)
        _seed(sb.tenant_id, 120, source="bundled", ok=True)
        restore = _fail_trip_records(monkeypatch)
        _skill_turn(sb.tenant_id, ok=False)        # P1: judged trip, record not committed
        restore()
        entry = rollback_detector._pending_trips[sb.tenant_id]
        assert entry["details"] is not None and entry["committed"] is False
        assert entry.get("cause") is None
        assert _named(sb, "routing.rollback_triggered") == []
        # P2, its own interpreter: judges from P1's marker, its record commits, its
        # trip-state write fails — its marker says committed.
        child_env = {**os.environ,
                     "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        child = subprocess.run([sys.executable, "-c", _CHILD, sb.tenant_id],
                               env=child_env, capture_output=True, text=True, timeout=120)
        assert child.returncode == 0, child.stderr[-4000:]
        marker = json.loads(rollback_detector.pending_path(sb.tenant_id).read_text())
        assert marker["committed"] is True and marker.get("cause") is None
        assert len(_named(sb, "routing.rollback_triggered")) == 1
        _skill_turn(sb.tenant_id, ok=False)        # P1 retries its pending trip
        assert len(_named(sb, "routing.rollback_triggered")) == 1, \
            "one trip, two trip records"
        state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
        assert state["tripped"] is True and state["skill_n"] == marker["details"]["skill_n"]
        assert not rollback_detector.pending_path(sb.tenant_id).exists()
        assert sb.tenant_id not in rollback_detector._pending_trips


# ── M1-3: an entry created over an unreadable state is not immortal ─────────

def _hold_over_unreadable_state(sb):
    """A lock-failure hold taken while the state file cannot be parsed."""
    from core.skills.os_skills.monitoring import rollback_detector, routing_ledger

    _trip(sb)
    r = sb.client.post(RESET, headers=sb.csrf_headers)
    assert r.status_code == 200, r.text
    rollback_detector.state_path(sb.tenant_id).write_text("{")
    lock = routing_ledger.routing_dir(sb.tenant_id) / rollback_detector.LOCK_FILENAME
    os.chmod(lock, 0o400)
    try:
        _skill_turn(sb.tenant_id, ok=False)       # the judge: lock unavailable -> hold
    finally:
        os.chmod(lock, 0o600)
    entry = dict(rollback_detector._pending_trips[sb.tenant_id])
    assert entry["cause"] == "lock_unavailable" and entry["generation"] is None
    return entry


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes (read-only lock file)")
def test_a_hold_taken_over_an_unreadable_state_yields_to_a_later_reset(tmp_path):
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import dual_write, rollback_detector

        entry = _hold_over_unreadable_state(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)   # the operator, "elsewhere"
        assert r.status_code == 200, r.text
        rollback_detector._pending_trips[sb.tenant_id] = entry   # the bridge's memory
        assert rollback_detector.rollback_active(sb.tenant_id) is False, \
            "a hold from an unreadable state outlived the operator's reset"
        assert sb.tenant_id not in rollback_detector._pending_trips
        dual_write.reset_for_tests()
        assert dual_write.decide(tenant_id=sb.tenant_id, surface="console",
                                 skill_available=True).reason != "rollback_active"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes (read-only lock file)")
def test_a_hold_taken_over_an_unreadable_state_survives_the_same_file_turning_readable(
        tmp_path):
    """Negative control of M1-3: no write happened — the same state file (same inode)
    is readable again — so the hold is NOT stale and keeps restricting."""
    with console_client(tmp_path) as sb:
        from core.skills.os_skills.monitoring import rollback_detector

        _hold_over_unreadable_state(sb)
        path = rollback_detector.state_path(sb.tenant_id)
        ino = path.stat().st_ino
        with open(path, "w") as fh:                   # in place: the inode is kept
            fh.write(json.dumps({"tripped": False, "at": 1.0, "generation": 2}))
        assert path.stat().st_ino == ino
        assert rollback_detector.rollback_active(sb.tenant_id) is True
        assert sb.tenant_id in rollback_detector._pending_trips
