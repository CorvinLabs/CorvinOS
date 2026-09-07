"""Regression tests for the 2026-09-07 learning-subsystem hardening (F-L5..F-L12).

Each test names the finding it pins. They drive the real modules against an
isolated ``CORVIN_HOME`` / core chain (``tests/conftest.py`` + the learning
``conftest.py``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.paths.tenant import corvin_home, tenant_home


def _outcome(tenant: str, success: bool) -> LearningEvent:
    return LearningEvent.create(
        event_type=EventType.OUTCOME, skill_id="os.delegation_router", tenant_id=tenant,
        signal={"task_id": "t", "success": success}, lom="tests",
    )


# ── F-L5: the live collector measures, it does not simulate ─────────────────


def test_live_collector_reads_real_sources_and_never_fabricates(tmp_path: Path):
    from core.learning import live_experiment_collector as mod
    from core.learning.live_experiment_collector import LiveExperimentCollector

    assert not hasattr(mod, "random"), "random-number fabrication is back in the collector"
    store = EventStore(tenant_home("_default"), tenant_id="_default")
    store.write_event(_outcome("_default", True))
    store.write_event(_outcome("_default", False))

    collector = LiveExperimentCollector("_default")
    assert str(collector.base_dir).startswith(str(corvin_home()))  # F-L12
    m1 = collector.collect_all_metrics()
    m2 = collector.collect_all_metrics()
    assert m1["sources"]["learning"] == "event_store"
    assert m1["learning"] == m2["learning"]  # deterministic: same store → same numbers
    assert m1["learning"]["event_counts"]["outcome"] == 2
    assert m1["learning"]["recent_outcomes"] == {"window": 50, "total": 2, "successes": 1}
    assert m1["learning"]["success_rate"] == 0.5 and m1["learning"]["outcome_loss"] == 0.5
    assert m1["user_actions"]["outcomes_last_hour"] == 2
    assert m1["component_health"]["outcome"]["active"] is True
    assert m1["component_health"]["feedback"]["active"] is False
    assert m1["system"]["process_max_rss_mb"] > 0

    collector.save_measurement(m1)
    saved = json.loads(collector.get_today_file().read_text().splitlines()[-1])
    assert saved["learning"] == m1["learning"]
    stats = collector.get_statistics(days=1)
    assert stats["num_measurements"] == 1 and stats["outcome_loss"]["mean"] == 0.5


# ── F-L6: no free text in learning records ───────────────────────────────────


def test_rating_event_carries_has_text_not_the_text():
    from core.learning.operator_feedback import RATING_KIND_TOOL, build_rating_event

    ev = build_rating_event(kind=RATING_KIND_TOOL, entity_id="t", entity_name="T", rating=5,
                            tenant_id="_default", feedback_text="  do not store me  ")
    assert "feedback_text" not in ev.signal
    assert ev.signal["has_text"] is True and ev.signal["text_length"] == len("do not store me")
    assert "store me" not in json.dumps(ev.to_dict())
    ev2 = build_rating_event(kind=RATING_KIND_TOOL, entity_id="t", entity_name="T", rating=3, tenant_id="_default")
    assert ev2.signal["has_text"] is False and ev2.signal["text_length"] == 0


def test_grade_pattern_goes_through_the_chained_store(tmp_path: Path):
    from core.learning.integration import LearningIntegration

    th = tenant_home("_default")
    integration = LearningIntegration(th / "learning", tenant_id="_default")
    integration.register_pattern("pattern_a", "A", when=[])
    event_id = integration.grade_pattern("pattern_a", 0.4, reason="private operator remark")
    assert integration.get_pattern_confidence("pattern_a") > 0.5

    disk = [e for e in EventStore(th).query_events("_default", event_type=EventType.FEEDBACK) if e.event_id == event_id]
    assert len(disk) == 1 and disk[0].audit_ref
    assert disk[0].signal == {
        "kind": "pattern_grade", "pattern_id": "pattern_a", "grade": 0.4,
        "has_reason": True, "reason_length": len("private operator remark"), "source": "operator",
    }
    chain = Path(os.environ["VOICE_AUDIT_PATH"]).read_text()
    assert disk[0].audit_ref in chain and "operator remark" not in chain
    # the unchained TreeOfThoughts JSONL is no longer written for grades
    assert not list((th / "learning" / "events").glob("*.jsonl")) or all(
        "confidence_delta" not in line
        for f in (th / "learning" / "events").glob("*.jsonl") for line in f.read_text().splitlines()
    )


# ── F-L7: negative feedback is negative ─────────────────────────────────────


def test_negative_user_feedback_lowers_relevance():
    from core.learning.confidence_scorer import ConfidenceScorer

    neg = ConfidenceScorer.score_decision("s", "_default", feedback_count=1, user_feedback_positive=False)
    pos = ConfidenceScorer.score_decision("s", "_default", feedback_count=1, user_feedback_positive=True)
    none = ConfidenceScorer.score_decision("s", "_default", feedback_count=0, user_feedback_positive=None)
    assert neg.relevance_score < none.relevance_score < pos.relevance_score


# ── F-L8: user_id cannot escape the profiles directory ──────────────────────


@pytest.mark.parametrize("bad", ["../escape", "/etc/passwd", "a/b", "", "..", ".hidden", "x" * 200])
def test_profile_manager_rejects_path_like_user_ids(bad):
    from core.learning.user_profile import UserProfileManager

    mgr = UserProfileManager()
    with pytest.raises(ValueError):
        mgr.get_profile(bad, "_default")
    assert not (tenant_home("_default") / "learning" / "profiles").exists() or not list(
        (tenant_home("_default") / "learning" / "profiles").glob("*")
    )


def test_profile_manager_accepts_plain_ids():
    from core.learning.user_profile import UserProfileManager

    p = UserProfileManager().get_profile("user.name@example-1", "_default")
    assert p.user_id == "user.name@example-1"
    assert (tenant_home("_default") / "learning" / "profiles" / "user.name@example-1.json").is_file()


# ── F-L10: unified loss — validated weights, no prod mock, no div-by-zero ───


def test_unified_loss_validates_weights_and_has_no_prod_mock():
    from core.learning import unified_loss
    from core.learning.unified_loss import UnifiedLossOptimizer, validate_weights
    from tests.learning.mock_audit_backend import MockAuditBackend

    assert not hasattr(unified_loss, "MockAuditBackend")
    audit = MockAuditBackend()
    opt = UnifiedLossOptimizer("_default", audit)
    base = dict(opt.weights)
    for bad in (
        {**base, "routing": float("nan")},
        {**base, "routing": -0.1, "latency": base["latency"] + 0.1},
        {k: v for k, v in base.items() if k != "diversity"},
        {**base, "routing": 0.9},
    ):
        with pytest.raises(ValueError):
            opt.update_weights(bad)
        assert opt.weights == base
    with pytest.raises(ValueError):
        validate_weights({**base, "extra": 0.0})
    with pytest.raises(ValueError):
        UnifiedLossOptimizer("_default", None)

    # zero budget must not divide by zero
    loss = opt._compute_L_attention([{"tokens_used": 10, "budget_allocated": 0}, {"tokens_used": 500}])
    assert loss >= 0.0


def test_duplicate_backprop_modules_are_gone():
    import importlib

    for name in ("core.learning.loss_backprop", "core.learning.unified_loss_simple"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
    from core.learning.gradient_backprop import LossBackpropagator  # the one with callers

    assert LossBackpropagator


# ── F-L11: no asserts, plugin gradients recorded ────────────────────────────


def test_normalize_components_raises_instead_of_asserting():
    from core.learning.memory_optimizer import MemoryOptimizer

    loop = MemoryOptimizer(tenant_id="_default")
    with pytest.raises(ValueError):
        loop.normalize_components({"a": 1.5}, {"a": 1.0})
    with pytest.raises(ValueError):
        loop.normalize_components({"a": 0.5}, {"a": 0.5})
    with pytest.raises(ValueError):
        loop.normalize_components({"a": 0.5}, {"b": 1.0})


def test_plugin_orchestrator_records_gradients_and_can_converge():
    from core.learning.plugin_optimizer import PluginOrchestrator

    loop = PluginOrchestrator()
    fb = {"quality_gain": 0.8, "execution_time_ms": 50, "error_rate": 0.0, "conflict_score": 0.0}
    for _ in range(101):
        loss = loop.compute_loss(fb)
        g = loop.compute_gradients(loss, loss)
        loop.apply_gradients(g)
    assert loop.gradient_history["plugin_priority"]
    assert loop.check_convergence() is True


# ── F-L12: everything under CORVIN_HOME ─────────────────────────────────────


def test_no_module_in_core_learning_hardcodes_home_corvin():
    root = Path(__file__).resolve().parents[2] / "core" / "learning"
    offenders = []
    for py in root.glob("*.py"):
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if 'Path.home() / ".corvin"' in line or 'Path.home()/".corvin"' in line:
                offenders.append(f"{py.name}:{i}")
    assert offenders == []


# ── EventStore tenant binding ───────────────────────────────────────────────


def test_bound_event_store_rejects_foreign_tenant_events(tmp_path: Path):
    store = EventStore(tenant_home("_default"), tenant_id="_default")
    with pytest.raises(ValueError):
        store.write_event(_outcome("acme-corp", True))
    assert store.count_events("_default") == 0
    with pytest.raises(ValueError):
        EventStore(tmp_path, tenant_id="../evil")
