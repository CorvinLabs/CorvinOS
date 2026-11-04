"""ADR-2092 G4 — round-8 findings on the L5 rollback judge (review c120 M1).

M1-1: a state write over an UNREADABLE rollback state (torn, a non-object, EACCES)
restarted the generation at 1 (``(persisted_generation() or 0) + 1`` with
``persisted_generation()`` None). Every pending trip another process held under a
higher generation then no longer read as stale, so the operator's reset of a torn
state was overridden by that process's next judge (a second ``routing.rollback_triggered``
after ``routing.rollback_reset``). A write over an unreadable state now takes a
generation above every counter value (a nanosecond clock reading, never below the
highest generation still visible).

Entry points as production calls them: ``delegation_policy.record_turn_outcome``
(starts the judge thread for a Skill-served turn) and ``POST
/v1/console/learning/routing/rollback/reset`` through the real console router with a
real session + CSRF token.
"""
from __future__ import annotations

import pytest

from tests.learning.console_client import console_client
from tests.learning.test_l5_rollback_lock_and_window_e2e import _seed, _skill_turn
from tests.learning.test_l5_rollback_reset_route_e2e import RESET, _named, _trip
from tests.learning.test_l5_rollback_round7b_e2e import _events


@pytest.fixture(autouse=True)
def _phase2(monkeypatch):
    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    yield
    from core.skills.os_skills.monitoring import dual_write, rollback_detector

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
