"""Tests for learning integration (ADR-0612 + ADR-0314)."""

import pytest

from core.skills.orchestration.learning_integration import (
    OrchestrationLearner,
    PluginPerformanceModel,
    PluginPerformanceStats,
)


class TestPluginPerformanceStats:
    """Test individual plugin stats."""

    def test_observe_success(self):
        """Record successful invocation."""
        stats = PluginPerformanceStats(
            plugin_id="plugin1",
            capability_id="cap1",
        )
        stats.observe_invocation(latency_ms=100, success=True, slo_met=True)

        assert stats.invocations == 1
        assert stats.successes == 1
        assert stats.failures == 0
        assert stats.success_rate == 1.0
        assert stats.slo_met_rate == 1.0

    def test_observe_failure(self):
        """Record failed invocation."""
        stats = PluginPerformanceStats(
            plugin_id="plugin1",
            capability_id="cap1",
        )
        stats.observe_invocation(latency_ms=1000, success=False, slo_met=False)

        assert stats.invocations == 1
        assert stats.successes == 0
        assert stats.failures == 1
        assert stats.success_rate == 0.0
        assert stats.slo_met_rate == 0.0

    def test_multiple_invocations(self):
        """Aggregate multiple invocations."""
        stats = PluginPerformanceStats(
            plugin_id="plugin1",
            capability_id="cap1",
        )
        # 3 successes
        for _ in range(3):
            stats.observe_invocation(latency_ms=100, success=True, slo_met=True)
        # 1 failure
        stats.observe_invocation(latency_ms=1000, success=False, slo_met=False)

        assert stats.invocations == 4
        assert stats.successes == 3
        assert stats.failures == 1
        assert stats.success_rate == 0.75
        assert stats.slo_met_rate == 0.75

    def test_p50_latency(self):
        """Calculate average latency."""
        stats = PluginPerformanceStats(
            plugin_id="plugin1",
            capability_id="cap1",
        )
        stats.observe_invocation(latency_ms=100, success=True, slo_met=True)
        stats.observe_invocation(latency_ms=200, success=True, slo_met=True)

        assert stats.p50_latency_ms == 150.0


class TestPluginPerformanceModel:
    """Test learning model."""

    def test_record_outcome(self):
        """Record outcomes for plugin."""
        model = PluginPerformanceModel(skill_id="skill1")

        model.record_outcome(
            plugin_id="plugin1",
            capability_id="cap1",
            latency_ms=100,
            success=True,
            slo_met=True,
        )

        assert "plugin1:cap1" in model.stats
        assert model.stats["plugin1:cap1"].success_rate == 1.0

    def test_confidence_grows_with_invocations(self):
        """Confidence increases as we observe more."""
        model = PluginPerformanceModel(skill_id="skill1")

        # 10 invocations
        for _ in range(10):
            model.record_outcome("plugin1", "cap1", 100, True, True)

        conf1 = model.confidence

        # 100 invocations total
        for _ in range(90):
            model.record_outcome("plugin1", "cap1", 100, True, True)

        conf2 = model.confidence

        assert conf2 > conf1
        assert conf2 == 1.0  # Confidence caps at 100 invocations

    def test_recommend_plugin(self):
        """Recommend best plugin based on performance."""
        model = PluginPerformanceModel(skill_id="skill1")

        # Plugin 1: 100% success
        for _ in range(10):
            model.record_outcome("plugin1", "cap1", 100, True, True)

        # Plugin 2: 50% success
        for _ in range(5):
            model.record_outcome("plugin2", "cap1", 50, True, True)
        for _ in range(5):
            model.record_outcome("plugin2", "cap1", 1000, False, False)

        # Recommend: should pick plugin1
        recommendation = model.recommend_plugin(
            allowed_plugins=["plugin1", "plugin2"],
            capability_id="cap1",
        )

        assert recommendation is not None
        plugin_id, confidence = recommendation
        assert plugin_id == "plugin1"

    def test_recommend_unknown_plugin(self):
        """Return None if no data."""
        model = PluginPerformanceModel(skill_id="skill1")
        recommendation = model.recommend_plugin(
            allowed_plugins=["unknown"],
            capability_id="cap1",
        )
        assert recommendation is None


class TestOrchestrationLearner:
    """Test learner."""

    def test_process_outcome(self):
        """Process outcome."""
        learner = OrchestrationLearner()

        learner.process_outcome(
            skill_id="skill1",
            plugin_id="plugin1",
            capability_id="cap1",
            latency_ms=100,
            success=True,
            slo_met=True,
        )

        model = learner.get_model("skill1")
        assert model is not None
        assert "plugin1:cap1" in model.stats

    def test_tenant_isolation(self):
        """Models are tenant-scoped."""
        learner = OrchestrationLearner()

        # Tenant A
        learner.process_outcome("skill1", "plugin1", "cap1", 100, True, True, tenant_id="tenant_a")

        # Tenant B (different outcome)
        learner.process_outcome("skill1", "plugin2", "cap1", 1000, False, False, tenant_id="tenant_b")

        model_a = learner.get_model("skill1", tenant_id="tenant_a")
        model_b = learner.get_model("skill1", tenant_id="tenant_b")

        # Different stats
        assert "plugin1:cap1" in model_a.stats
        assert "plugin2:cap1" in model_b.stats

    def test_recommend(self):
        """Get recommendation."""
        learner = OrchestrationLearner()

        # Record data
        for _ in range(10):
            learner.process_outcome(
                "skill1", "plugin1", "cap1", 100, True, True, tenant_id="default"
            )

        # Get recommendation
        rec = learner.recommend("skill1", "cap1", ["plugin1", "plugin2"])
        assert rec is not None
        plugin_id, confidence = rec
        assert plugin_id == "plugin1"
