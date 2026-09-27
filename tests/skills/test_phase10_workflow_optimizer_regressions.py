"""Regressions for core/skills/os_skills/workflow_optimizer_skill
(adversarial review 2026-09-27). Each test failed before its fix."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.learning.event_store import EventStore
from core.learning.learning_events import EventType
from core.skills.os_skills.workflow_optimizer_skill.ab_testing import CanaryManager, CanaryStage
from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import (
    ConfidenceCalculator,
    RoutingWeights,
)
from core.skills.os_skills.workflow_optimizer_skill.feedback_handler import (
    FeedbackHandler,
    FeedbackType,
    RoutingFeedback,
)


def test_canary_start_emits_config_updated_event(tmp_path):
    store = Mock(spec=EventStore)
    CanaryManager(event_store=store, config_dir=tmp_path).start_canary(CanaryStage.STAGE_10)
    assert store.write_event.call_args.args[0].event_type == EventType.CONFIG_UPDATED


def test_canary_audit_failure_leaves_config_unchanged(tmp_path):
    store = Mock(spec=EventStore)
    store.write_event.side_effect = RuntimeError("chain down")
    mgr = CanaryManager(event_store=store, config_dir=tmp_path)
    with pytest.raises(RuntimeError):
        mgr.start_canary(CanaryStage.STAGE_10)
    assert mgr.config.stage == CanaryStage.DISABLED
    assert not (tmp_path / "canary_config.json").exists()


def test_learning_loop_is_sync_and_writes_with_real_store_signature(tmp_path, monkeypatch):
    from core.skills.os_skills.workflow_optimizer_skill.learning_loop_phase3 import (
        WorkflowOptimizerLearningLoop,
    )
    assert not inspect.iscoroutinefunction(WorkflowOptimizerLearningLoop.run_learning_loop)
    written = []

    class Store:  # EventStore's real call shape: write_event(event) -> str
        def write_event(self, event):
            written.append(event)
            return "ref"

        def query_events(self, **kw):
            return [e for e in written if e.event_type == kw.get("event_type")]

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    loop = WorkflowOptimizerLearningLoop(event_store=Store(), tenant_id="_default")
    result = loop.run_learning_loop(20, 5, 20)
    assert written[-1].event_type == EventType.OUTCOME
    assert written[-1].signal["success"] == result.success


def test_learning_loop_never_writes_live_routing_weights(tmp_path, monkeypatch):
    from core.skills.os_skills.workflow_optimizer_skill.learning_loop_phase3 import (
        WorkflowOptimizerLearningLoop,
    )
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    loop = WorkflowOptimizerLearningLoop(event_store=Mock(), tenant_id="_default")
    live = tmp_path / "tenants" / "_default" / "workflow_optimizer_config"
    assert loop.confidence_calculator.config_dir != live
    assert loop.confidence_calculator.config_dir.name == "workflow_optimizer_simulation"


def test_learned_selector_reads_its_own_tenants_weights(tmp_path, monkeypatch):
    from core.skills.os_skills.workflow_optimizer_skill.l5_agent_selector_learned import (
        L5AgentSelectorLearned,
    )
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    sel = L5AgentSelectorLearned(tenant_id="tenant-b")
    assert sel.config_persistence.config_dir == (
        tmp_path / "tenants" / "tenant-b" / "workflow_optimizer_config"
    )


def _calc_with_feedback(tmp_path, outcomes, model="sonnet-5", complexity="medium"):
    store = EventStore(tmp_path / "t", tenant_id="_default")
    handler = FeedbackHandler(event_store=store, tenant_id="_default")
    for i, ok in enumerate(outcomes):
        handler.process_feedback(RoutingFeedback(
            task_id=f"t{i}", routed_model=model, task_complexity=complexity,
            feedback_type=FeedbackType.CORRECT if ok else FeedbackType.INCORRECT,
            tenant_id="_default",
        ))
    return ConfidenceCalculator(event_store=store, tenant_id="_default",
                                config_dir=tmp_path / "cfg")


def test_single_incorrect_does_not_discard_prior(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    calc = _calc_with_feedback(tmp_path, [False])
    w, _ = calc.update_from_feedback()
    # prior 0.80, n0=2: (1.6 + 0) / 3 = 0.533 — Laplace gave 1/3.
    assert w.weights["medium_sonnet"] == pytest.approx(1.6 / 3)


def test_repeated_update_does_not_recount_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    calc = _calc_with_feedback(tmp_path, [True, True, False, True])
    w1, _ = calc.update_from_feedback()
    w2, _ = calc.update_from_feedback()
    assert w1.weights["medium_sonnet"] == pytest.approx(w2.weights["medium_sonnet"])


def test_weight_stats_does_not_double_count(tmp_path, monkeypatch):
    from core.skills.os_skills.workflow_optimizer_skill.config_persistence import (
        ConfigPersistence,
    )
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    calc = _calc_with_feedback(tmp_path, [True] * 4)
    calc.update_from_feedback()
    calc.update_from_feedback()
    stats = ConfigPersistence(config_dir=tmp_path / "cfg").get_weight_stats()
    assert stats["total_feedback_incorporated"] == 4


def test_save_weights_is_audited_and_versioned(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    store = Mock(spec=EventStore)
    calc = ConfidenceCalculator(event_store=store, tenant_id="_default", config_dir=tmp_path / "c")
    v1 = calc.save_weights(RoutingWeights(weights={"simple_haiku": 0.9}))
    v2 = calc.save_weights(RoutingWeights(weights={"simple_haiku": 0.7}))
    assert v1.version != v2.version
    assert len(list(calc.history_dir.glob("v*.json"))) == 2
    assert store.write_event.call_count == 2
    store.write_event.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError):
        calc.save_weights(RoutingWeights(weights={"simple_haiku": 0.1}))
    assert json.loads(calc.weights_file.read_text())["weights"]["simple_haiku"] == 0.7
