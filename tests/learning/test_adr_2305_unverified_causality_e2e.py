"""ADR-2305 Part 1 (PLAN-0966 WP2) through the real console route.

``POST /v1/console/learning/feedback`` -> ``SkillAdapter.run_optimizer_epoch``: an accepted
hypothesis carries ``:unverified_causality`` in ``optimizer_reason`` and in the persisted
version's ``change_reason`` unless Phase 2 dual-write holds on every routing surface.
Everything else (acceptance, version, config, ``skill_config_updated`` audit) is unchanged.

Seeds go through production writers only (the adapter's persisted epoch counter, OUTCOME
events through the booted emitter), like ``test_learning_loop_routes_e2e``.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.learning.console_client import boot_learning_registry, console_client

MARK = ":unverified_causality"


def _prime_and_accept(sb):
    """Fast-forward past the 50-epoch baseline, record 10 successes, post one feedback."""
    from core.learning.outcome_sink import emit_task_outcome
    from core.learning.learning_events import EventType, LearningEvent
    from core.skills.os_skills.skill_adapter import SkillAdapter

    emitter = boot_learning_registry(sb)
    # ADR-0534 reality check: the router really ran.
    emitter.write_sync(LearningEvent.create(
        event_type=EventType.SKILL_EXECUTED, skill_id="os.delegation_router", tenant_id=sb.tenant_id,
        signal={"status": "success", "output": {"ok": True}},
        lom="core/skills/skill_registry_phase1.py:execute"))
    adapter = SkillAdapter("os.delegation_router", sb.tenant_id)
    adapter.state.epoch = 51
    adapter.state.baseline_success_rate = 0.0
    adapter._persist()
    for i in range(10):
        assert emit_task_outcome(tenant_id=sb.tenant_id, task_id=f"t{i}", status="completed",
                                 exit_code=0, duration_ms=10, engine="native", task_type="chat",
                                 emitter=emitter)
    for _ in range(100):
        if len([e for e in sb.events_on_disk() if e["event_type"] == "outcome"]) >= 10:
            break
        time.sleep(0.05)
    r = sb.client.post("/v1/console/learning/feedback", headers=sb.csrf_headers,
                       json={"task_id": "task-9", "outcome_quality": "excellent", "would_repeat": True})
    assert r.status_code == 200, r.text
    return r.json(), emitter


def _cfg(sb) -> dict:
    return json.loads((sb.tenant_home / "skills" / "os_delegation_router_config.json").read_text())


def _verify_tenant_chain(sb):
    from core.deployment.audit_sink import _forge

    se, fp = _forge()
    path = fp.tenant_audit_chain(sb.tenant_id)
    ok, problems = se.verify_chain(path)
    assert ok, problems
    return path


def test_acceptance_is_labelled_while_phase2_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("CORVIN_ACP_PHASE", raising=False)
    with console_client(tmp_path) as sb:
        body, emitter = _prime_and_accept(sb)
        assert body["recent_outcomes"] == {"successes": 10, "total": 10}
        accepted = [h for h in body["hypotheses"] if h["accepted"]]
        assert len(accepted) == 1
        assert accepted[0]["optimizer_reason"] == "improvement_100.00%_accepted" + MARK
        # the rejected / non-accepted ones are not relabelled
        assert all(MARK not in h["optimizer_reason"] for h in body["hypotheses"] if not h["accepted"])
        # unchanged behaviour: version, config
        assert body["current_version"] == "v1"
        assert body["current_config"]["confidence_threshold"] == pytest.approx(0.75)
        [version] = _cfg(sb)["versions"]
        assert version["change_reason"].endswith(MARK)
        assert version["change_reason"].count(MARK) == 1
        listed = sb.client.get("/v1/console/learning/config-versions").json()
        assert listed[0]["change_reason"] == version["change_reason"]
        emitter.stop(timeout=5.0)
        # the marker never enters a chain; the acceptance is still audited
        path = _verify_tenant_chain(sb)
        assert MARK not in path.read_text()
        assert MARK not in sb.chain.read_text() if sb.chain.exists() else True
        assert any(e["event_type"] == "config_updated" for e in sb.events_on_disk())


def test_no_label_when_every_adr_2092_gate_holds(tmp_path, monkeypatch):
    """Gates held: Phase 2 requested, Skill booted (by boot_learning_registry), no rollback,
    readiness ready on both surfaces. The gates themselves are not under test here — only that
    the label follows ``effective_phase``."""
    from core.skills.os_skills.monitoring import dual_write, readiness, rollback_detector

    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    asked = []

    def _ready(tenant_id, surface, **_kw):
        asked.append(surface)
        return readiness.Readiness(True, "ready", 600, 0.99, 0, 10)

    monkeypatch.setattr(readiness, "cached_evaluate", _ready)
    monkeypatch.setattr(rollback_detector, "rollback_active", lambda _t: False)
    dual_write.reset_for_tests()
    with console_client(tmp_path) as sb:
        body, emitter = _prime_and_accept(sb)
        accepted = [h for h in body["hypotheses"] if h["accepted"]]
        assert len(accepted) == 1
        assert accepted[0]["optimizer_reason"] == "improvement_100.00%_accepted"
        assert not _cfg(sb)["versions"][0]["change_reason"].endswith(MARK)
        assert {"bridge", "console"} <= set(asked)
        emitter.stop(timeout=5.0)
        _verify_tenant_chain(sb)


def test_one_surface_not_ready_keeps_the_label(tmp_path, monkeypatch):
    from core.skills.os_skills.monitoring import dual_write, readiness, rollback_detector

    monkeypatch.setenv("CORVIN_ACP_PHASE", "phase2_dual_write")
    monkeypatch.setattr(readiness, "cached_evaluate", lambda t, s, **_k: readiness.Readiness(
        s == "console", "ready" if s == "console" else "insufficient_samples", 600, 0.99, 0, 10))
    monkeypatch.setattr(rollback_detector, "rollback_active", lambda _t: False)
    dual_write.reset_for_tests()
    with console_client(tmp_path) as sb:
        body, emitter = _prime_and_accept(sb)
        accepted = [h for h in body["hypotheses"] if h["accepted"]]
        assert accepted[0]["optimizer_reason"].endswith(MARK)
        emitter.stop(timeout=5.0)
