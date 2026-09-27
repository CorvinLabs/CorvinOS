"""Regressions for the 2026-09-27 adversarial review of core/learning.

Each test failed before its fix (see the module each one names).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _chain_records():
    chain = Path(os.environ["VOICE_AUDIT_PATH"])
    if not chain.exists():
        return []
    return [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]


def test_every_learning_event_type_is_allowlisted():
    """event_store.EventStore.write_event commits ``learning.<type>`` and proves
    the commit by reading back ``audit_ref`` — a type without an allowlist has
    that key scrubbed and EVERY write of it fails closed (geo_mismatch,
    scene_rendered, plugin_*, ... until 2026-09-27)."""
    from core.learning.event_persistence import _LEARNING_EVENT_ALLOWLISTS
    from core.learning.learning_events import EventType

    missing = [t.value for t in EventType if f"learning.{t.value}" not in _LEARNING_EVENT_ALLOWLISTS]
    assert missing == []


def test_plugin_confidence_reads_the_store_the_registry_writes():
    from core.learning import plugin_confidence
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    from core.paths import tenant_home

    store = EventStore(tenant_home("_default"), tenant_id="_default")
    for ok in (True, True, True, False):
        store.write_event(LearningEvent.create(
            event_type=EventType.PLUGIN_EXECUTED, skill_id="plugin.demo", tenant_id="_default",
            signal={"success": ok}, lom="test"))
    assert plugin_confidence.calculate_plugin_confidence("demo", "_default") == pytest.approx(0.75)


def test_plugin_confidence_unreadable_store_is_not_measured(monkeypatch):
    from core.learning import plugin_confidence

    def boom(_tid):
        raise OSError("disk gone")

    monkeypatch.setattr(plugin_confidence, "_store", boom)
    assert plugin_confidence.calculate_plugin_confidence("demo", "_default") is None


def test_phase7_audit_sink_commits_to_the_core_chain_first(monkeypatch):
    from core.learning import phase7_orchestrator_bridge as b

    sink = b.AuditSink("_default")
    ev = b.SkillExecutedEvent(skill_id="os.x", version="1", input_data={"prompt": "secret text"},
                              output_data={"answer": "secret out"}, latency_ms=3, lom="t",
                              tenant_id="_default")
    assert sink.emit(ev) is True
    recs = [r for r in _chain_records() if r["event_type"] == "learning.phase7_skill_executed"]
    assert recs and recs[-1]["details"]["skill_id"] == "os.x"
    assert "secret" not in json.dumps(recs[-1])

    import core.learning.event_persistence as ep

    def boom(*a, **k):
        raise RuntimeError("no commit")

    monkeypatch.setattr(ep, "core_audit_event", boom)
    n = len(sink.events)
    assert sink.emit(ev) is False
    assert len(sink.events) == n


def test_phase7_audit_sink_refuses_a_foreign_tenant_event():
    from core.learning import phase7_orchestrator_bridge as b

    sink = b.AuditSink("_default")
    assert sink.emit(b.SkillExecutedEvent(skill_id="os.x", tenant_id="other")) is False


def test_phase2b_integration_writes_the_chain_and_fails_closed(monkeypatch):
    from core.learning.phase2b_integration import Phase2bAuditIntegration

    integ = Phase2bAuditIntegration()
    ref = integ.log_false_convergence_detected(
        skill_id="os.x", plateau_confidence=0.8, resumed_confidence=0.85,
        resume_improvement_percent=6.25, tenant_id="_default")
    rec = [r for r in _chain_records()
           if r["event_type"] == "learning.phase2b.false_convergence_detected"][-1]
    assert rec["details"]["audit_ref"] == ref
    assert rec["details"]["resumed_confidence"] == 0.85

    import core.learning.event_persistence as ep

    monkeypatch.setattr(ep, "core_audit_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        integ.log_false_convergence_detected(
            skill_id="os.x", plateau_confidence=0.8, resumed_confidence=0.85,
            resume_improvement_percent=6.25, tenant_id="_default")


def test_feedback_window_accepts_fresh_feedback():
    """An aware timestamp minus a naive utcnow() raised TypeError, read as 'too old'."""
    from core.learning.feedback_ingestion import FeedbackIngestionValidator

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assert FeedbackIngestionValidator._is_within_feedback_window(now)


def test_feedback_task_check_is_fail_closed_without_a_lookup():
    from core.learning.feedback_ingestion import FeedbackIngestionValidator

    v = FeedbackIngestionValidator(event_store=object(), feedback_secret="s")
    assert v._task_exists_in_audit("t", "_default") is False


def test_skill_learning_bridge_rejects_path_skill_ids():
    from core.learning.skill_learning_bridge import get_skill_learning_bridge

    with pytest.raises(ValueError):
        get_skill_learning_bridge("../../escape", "_default")


def test_confidence_persistence_honours_the_one_resolver(monkeypatch, tmp_path):
    from core.learning import confidence_persistence

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "h"))
    assert confidence_persistence._corvin_home() == tmp_path / "h"


def test_k3_scoreboard_is_per_tenant():
    from core.learning import confidence_scoreboard_k3_extension as k3

    k3.initialize_scoreboard(tenant_id="tenant_a")
    k3.initialize_scoreboard(tenant_id="tenant_b")
    k3.update_scoreboard("opus", "success", tenant_id="tenant_a")
    k3.update_scoreboard("opus", "error", tenant_id="tenant_b")
    assert k3.get_confidence_delta("opus", tenant_id="tenant_a") == pytest.approx(1.0)
    assert k3.get_confidence_delta("opus", tenant_id="tenant_b") == pytest.approx(0.0)


def test_event_store_consumer_default_writer_commits_to_the_chain():
    from datetime import datetime as _dt
    from unittest.mock import Mock

    from core.learning.event_store_consumer import run_consumer_cycle

    store = Mock()
    store.tenant_id = "_default"
    store.query = Mock(return_value=[
        {"event_type": "outcome_feedback", "skill_id": "os.router", "timestamp": _dt.now(),
         "payload": {"outcome": "success", "confidence": 0.8}, "audit_ref": f"h{i}"}
        for i in range(3)
    ])
    assert run_consumer_cycle("_default", store) == 3
    recs = [r for r in _chain_records() if r["event_type"] == "learning.outcome_aggregated"]
    assert recs and recs[-1]["details"]["skill_id"] == "os.router"
