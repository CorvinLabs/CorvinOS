"""ADR-2092 G4 — the operator reset of the L5 Phase 2 rollback, over HTTP.

``reset_rollback`` had no production caller although the docs describe an operator
reset (review round 3, s2 out-of-scope note). ``POST
/v1/console/learning/routing/rollback/reset`` is that caller. Driven through the REAL
console router with a REAL session + CSRF token (``console_client``); the trip is
made by the production judge (``rollback_detector.evaluate``) over a ledger written
by the production writer, and its record goes to the sandbox's real chain.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path

from tests.learning.console_client import console_client
from tests.learning.test_adr_2304_learning_brake_e2e import _named as _tenant_named
from tests.learning.test_adr_2304_learning_brake_e2e import _verify_chain as _verify_tenant_chain

RESET = "/v1/console/learning/routing/rollback/reset"


def _trip(sb, *, commit: bool = True) -> None:
    from core.skills.os_skills.monitoring import dual_write, rollback_detector, routing_ledger

    dual_write.reset_for_tests()
    for ok, source, n in ((False, "skill", 100), (True, "bundled", 100)):
        for _ in range(n):
            tid = routing_ledger.new_turn_id()
            routing_ledger.record_decision(
                tenant_id=sb.tenant_id, turn_id=tid, surface="console", phase="dual_write",
                bundled="acs" if source == "skill" else "native", used="native",
                source=source, skill="native", skill_conf=0.9, features={"complexity": "simple"})
            routing_ledger.record_outcome(tenant_id=sb.tenant_id, turn_id=tid, surface="console",
                                          used="native", ok=ok, latency_ms=5)
    audit = rollback_detector._default_audit(sb.tenant_id) if commit else (lambda et, d: False)
    assert rollback_detector.evaluate(sb.tenant_id, audit=audit) is True
    assert rollback_detector.rollback_active(sb.tenant_id)


def _named(sb, name: str) -> list[dict]:
    """Records on the tenant chain plus the bridge-writer chain. The routing records go
    through the bridges' audit writer, which honours ``VOICE_AUDIT_PATH`` (set by
    ``console_client``); in production both are the one tenant chain."""
    bridge = [r for r in sb.chain_records() if name in (r.get("event"), r.get("event_type"))]
    return _tenant_named(sb, name) + bridge


def _verify_chain(sb) -> None:
    from core.deployment.audit_sink import _forge

    _verify_tenant_chain(sb)
    se, _fp = _forge()
    ok, problems = se.verify_chain(sb.chain)
    assert ok, problems


def _active(sb) -> bool:
    from core.skills.os_skills.monitoring import rollback_detector

    return rollback_detector.rollback_active(sb.tenant_id)


def _actions(sb) -> list[str]:
    return [r["details"]["action"] for r in _named(sb, "console.action_performed")]


def test_reset_clears_a_trip_audit_first(tmp_path):
    with console_client(tmp_path) as sb:
        _trip(sb)
        assert len(_named(sb, "routing.rollback_triggered")) == 1
        r = sb.client.post(RESET)                                  # no CSRF header
        assert r.status_code == 403, r.text
        assert _active(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"tenant_id": sb.tenant_id, "tripped": False}
        assert not _active(sb)
        assert len(_named(sb, "routing.rollback_reset")) == 1
        assert "learning.routing_rollback_reset" in _actions(sb)
        from core.skills.os_skills.monitoring import rollback_detector

        state = json.loads(rollback_detector.state_path(sb.tenant_id).read_text())
        assert (state["tripped"], state["generation"]) == (False, 2)
        _verify_chain(sb)


def test_reset_without_a_trip_is_409_and_records_nothing(tmp_path):
    with console_client(tmp_path) as sb:
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == {"error": "not_tripped"}
        assert _named(sb, "routing.rollback_reset") == []


def test_reset_clears_a_pending_trip(tmp_path):
    with console_client(tmp_path) as sb:
        _trip(sb, commit=False)                                    # marker only
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 200, r.text
        assert not _active(sb)


def test_an_uncommitted_reset_record_is_503_and_still_tripped(tmp_path, monkeypatch):
    with console_client(tmp_path) as sb:
        _trip(sb)
        from core.skills.os_skills.monitoring import rollback_detector

        monkeypatch.setattr(rollback_detector, "_default_audit", lambda tid: (lambda et, d: False))
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == {"error": "audit_not_committed"}
        assert _active(sb)


def test_a_state_write_failure_after_the_record_is_compensated(tmp_path, monkeypatch):
    with console_client(tmp_path) as sb:
        _trip(sb)
        from core.skills.os_skills.monitoring import rollback_detector

        real = os.replace

        def enospc(src, dst, *a, **k):
            if Path(dst).name == rollback_detector.STATE_FILENAME:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(src, dst, *a, **k)
        monkeypatch.setattr(rollback_detector.os, "replace", enospc)
        r = sb.client.post(RESET, headers=sb.csrf_headers)
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == {"error": "state_not_written"}
        monkeypatch.setattr(rollback_detector.os, "replace", real)
        assert _active(sb)
        assert len(_named(sb, "routing.rollback_reset")) == 1
        assert len(_named(sb, "routing.rollback_reset_not_applied")) == 1
        assert "learning.routing_rollback_reset" not in _actions(sb)
        r = sb.client.post(RESET, headers=sb.csrf_headers)          # disk freed: retry works
        assert r.status_code == 200, r.text
        assert not _active(sb)
        _verify_chain(sb)
