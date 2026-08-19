"""Tests for Tool Cost Learning (Gap 6, ADR-0326).

Tests EMA-based cost multiplier updates, outlier detection, confidence
convergence, and integration with CostController.
"""

import pytest
import asyncio
from datetime import datetime, timezone
from typing import Dict, Tuple

from core.learning.tool_cost_learning import (
    ToolCostLearner,
    CostLearnerMetrics,
    DEFAULT_EMA_ALPHA,
    OUTLIER_THRESHOLD,
    MIN_SAMPLES_FOR_CONFIDENCE,
)


class TestCostLearnerMetrics:
    """Tests for CostLearnerMetrics dataclass."""

    def test_metrics_creation(self):
        """Test creating a CostLearnerMetrics instance."""
        metrics = CostLearnerMetrics(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents_median=100,
            actual_cost_cents_median=150,
            task_complexity_multiplier=1.5,
            subsystem_overhead_multiplier=1.45,
            samples=20,
            outliers_flagged=1,
            trend=0.1,
            confidence=0.85,
        )
        assert metrics.tool_id == "tool_1"
        assert metrics.model_id == "claude-opus-5"
        assert metrics.samples == 20
        assert metrics.confidence == 0.85

    def test_metrics_immutable(self):
        """Test that CostLearnerMetrics is immutable."""
        metrics = CostLearnerMetrics(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents_median=100,
            actual_cost_cents_median=150,
            task_complexity_multiplier=1.5,
            subsystem_overhead_multiplier=1.45,
            samples=20,
            outliers_flagged=0,
            trend=0.0,
            confidence=0.8,
        )
        with pytest.raises(Exception):  # frozen dataclass
            metrics.samples = 25

    def test_metrics_to_dict(self):
        """Test converting metrics to JSON-serializable dict."""
        metrics = CostLearnerMetrics(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents_median=100,
            actual_cost_cents_median=150,
            task_complexity_multiplier=1.5,
            subsystem_overhead_multiplier=1.45,
            samples=20,
            outliers_flagged=1,
            trend=0.1,
            confidence=0.85,
        )
        d = metrics.to_dict()
        assert d["tool_id"] == "tool_1"
        assert d["samples"] == 20
        assert "timestamp" in d
        assert isinstance(d["timestamp"], str)


class TestToolCostLearnerInitialization:
    """Tests for ToolCostLearner initialization."""

    def test_initialization_defaults(self):
        """Test ToolCostLearner with default parameters."""
        learner = ToolCostLearner()
        assert learner.ema_alpha == DEFAULT_EMA_ALPHA
        assert learner.outlier_threshold == OUTLIER_THRESHOLD
        assert len(learner.multipliers) == 0
        assert len(learner.execution_history) == 0

    def test_initialization_custom_alpha(self):
        """Test ToolCostLearner with custom EMA alpha."""
        learner = ToolCostLearner(ema_alpha=0.2)
        assert learner.ema_alpha == 0.2

    def test_initialization_custom_outlier_threshold(self):
        """Test ToolCostLearner with custom outlier threshold."""
        learner = ToolCostLearner(outlier_threshold=3.0)
        assert learner.outlier_threshold == 3.0

    def test_initialization_invalid_alpha(self):
        """Test that invalid alpha raises ValueError."""
        with pytest.raises(ValueError):
            ToolCostLearner(ema_alpha=0.0)  # Must be > 0
        with pytest.raises(ValueError):
            ToolCostLearner(ema_alpha=1.5)  # Must be <= 1.0

    def test_initialization_invalid_outlier_threshold(self):
        """Test that invalid outlier threshold raises ValueError."""
        with pytest.raises(ValueError):
            ToolCostLearner(outlier_threshold=1.0)  # Must be > 1.0


class TestCostObservationBasics:
    """Tests for observing execution costs."""

    @pytest.mark.asyncio
    async def test_single_observation(self):
        """Test recording a single execution."""
        learner = ToolCostLearner()
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=120,
        )
        key = ("tool_1", "claude-opus-5")
        assert key in learner.multipliers
        # With one sample, multiplier = 0.1 * 1.2 + 0.9 * 1.0 = 1.02
        assert abs(learner.multipliers[key] - 1.02) < 0.01

    @pytest.mark.asyncio
    async def test_zero_estimated_cost_skipped(self):
        """Test that zero estimated cost is skipped."""
        learner = ToolCostLearner()
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=0,
            actual_cost_cents=100,
        )
        key = ("tool_1", "claude-opus-5")
        assert key not in learner.multipliers

    @pytest.mark.asyncio
    async def test_negative_actual_cost_skipped(self):
        """Test that negative actual cost is skipped."""
        learner = ToolCostLearner()
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=-50,
        )
        key = ("tool_1", "claude-opus-5")
        assert key not in learner.multipliers

    @pytest.mark.asyncio
    async def test_zero_actual_cost_allowed(self):
        """Test that zero actual cost is allowed (free execution)."""
        learner = ToolCostLearner()
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=0,
        )
        key = ("tool_1", "claude-opus-5")
        assert key in learner.multipliers
        # multiplier = 0 / 100 = 0, EMA: 0.1 * 0 + 0.9 * 1.0 = 0.9
        assert abs(learner.multipliers[key] - 0.9) < 0.01


class TestEMAUpdates:
    """Tests for exponential moving average update logic."""

    @pytest.mark.asyncio
    async def test_ema_converges_to_true_value(self):
        """Test that EMA converges to the true multiplier."""
        learner = ToolCostLearner(ema_alpha=0.1)
        key = ("tool_1", "claude-opus-5")

        # True multiplier is 1.5 (actual is always 1.5x estimated)
        true_multiplier = 1.5
        for i in range(50):
            actual_cost = int(100 * true_multiplier)
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=actual_cost,
            )

        final_multiplier = learner.multipliers[key]
        # Should converge close to 1.5
        assert abs(final_multiplier - true_multiplier) < 0.01

    @pytest.mark.asyncio
    async def test_ema_with_varying_samples(self):
        """Test EMA with varying cost multipliers."""
        learner = ToolCostLearner(ema_alpha=0.1)
        key = ("tool_1", "claude-opus-5")

        # Samples with different multipliers
        multipliers = [1.0, 1.2, 1.4, 1.3, 1.5, 1.4, 1.6]
        for mult in multipliers:
            actual_cost = int(100 * mult)
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=actual_cost,
            )

        final_multiplier = learner.multipliers[key]
        # Should be somewhere between min and max of samples
        assert 1.0 <= final_multiplier <= 1.6
        # With alpha=0.1, recent samples pull it up but historical values stabilize it
        assert final_multiplier > 1.1

    @pytest.mark.asyncio
    async def test_ema_update_formula(self):
        """Test that EMA follows the correct formula."""
        learner = ToolCostLearner(ema_alpha=0.1)
        key = ("tool_1", "claude-opus-5")

        # First observation
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=100,
        )
        first = learner.multipliers[key]
        # First: 0.1 * 1.0 + 0.9 * 1.0 = 1.0
        assert abs(first - 1.0) < 0.01

        # Second observation
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=200,
        )
        second = learner.multipliers[key]
        # Second: 0.1 * 2.0 + 0.9 * 1.0 = 1.1
        assert abs(second - 1.1) < 0.01


class TestOutlierDetection:
    """Tests for outlier flagging."""

    @pytest.mark.asyncio
    async def test_outlier_detection(self):
        """Test that outliers (>2x estimated) are flagged."""
        learner = ToolCostLearner(outlier_threshold=2.0)
        key = ("tool_1", "claude-opus-5")

        # Normal execution
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=150,
        )
        assert learner.outlier_counts.get(key, 0) == 0

        # Outlier execution (3x estimated)
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=300,
        )
        assert learner.outlier_counts[key] == 1

    @pytest.mark.asyncio
    async def test_multiple_outliers_tracked(self):
        """Test that multiple outliers are tracked."""
        learner = ToolCostLearner(outlier_threshold=2.0)
        key = ("tool_1", "claude-opus-5")

        for _ in range(3):
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=250,  # 2.5x
            )

        assert learner.outlier_counts[key] == 3

    @pytest.mark.asyncio
    async def test_outlier_at_threshold(self):
        """Test behavior at exactly the outlier threshold."""
        learner = ToolCostLearner(outlier_threshold=2.0)
        key = ("tool_1", "claude-opus-5")

        # Exactly at threshold (2.0x) should NOT be flagged
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=200,
        )
        assert learner.outlier_counts.get(key, 0) == 0

        # Just above threshold (2.01x) should be flagged
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=201,
        )
        assert learner.outlier_counts[key] == 1


class TestCostEstimation:
    """Tests for get_cost_estimate method."""

    def test_estimate_with_no_history(self):
        """Test estimate when tool has no history (use default 1.0x)."""
        learner = ToolCostLearner()
        estimate = learner.get_cost_estimate(
            tool_id="tool_1",
            model_id="claude-opus-5",
            base_cost_cents=100,
        )
        # No history, so default multiplier 1.0
        assert estimate == 100

    def test_estimate_with_learned_multiplier(self):
        """Test estimate when multiplier is learned."""
        learner = ToolCostLearner()
        learner.multipliers[("tool_1", "claude-opus-5")] = 1.5
        estimate = learner.get_cost_estimate(
            tool_id="tool_1",
            model_id="claude-opus-5",
            base_cost_cents=100,
        )
        assert estimate == 150

    def test_estimate_with_correction_disabled(self):
        """Test estimate with correction disabled."""
        learner = ToolCostLearner()
        learner.multipliers[("tool_1", "claude-opus-5")] = 1.5
        estimate = learner.get_cost_estimate(
            tool_id="tool_1",
            model_id="claude-opus-5",
            base_cost_cents=100,
            use_correction=False,
        )
        assert estimate == 100  # Ignores multiplier

    def test_estimate_zero_base_cost(self):
        """Test estimate with zero base cost."""
        learner = ToolCostLearner()
        learner.multipliers[("tool_1", "claude-opus-5")] = 2.0
        estimate = learner.get_cost_estimate(
            tool_id="tool_1",
            model_id="claude-opus-5",
            base_cost_cents=0,
        )
        assert estimate == 0

    def test_estimate_rounding(self):
        """Test that estimate is rounded to integer."""
        learner = ToolCostLearner()
        learner.multipliers[("tool_1", "claude-opus-5")] = 1.33
        estimate = learner.get_cost_estimate(
            tool_id="tool_1",
            model_id="claude-opus-5",
            base_cost_cents=100,
        )
        # 100 * 1.33 = 133.0
        assert estimate == 133
        assert isinstance(estimate, int)


class TestTrendDetection:
    """Tests for trend detection logic."""

    def test_compute_trend_increasing(self):
        """Test trend detection for increasing costs."""
        learner = ToolCostLearner()
        # Recent samples higher than older
        history = [1.0, 1.0, 1.0, 1.0, 1.5, 1.6, 1.7, 1.8]
        trend = learner._compute_trend(history)
        assert trend > 0  # Positive trend

    def test_compute_trend_stable(self):
        """Test trend detection for stable costs."""
        learner = ToolCostLearner()
        # All samples roughly the same
        history = [1.5, 1.5, 1.5, 1.5, 1.5]
        trend = learner._compute_trend(history)
        assert abs(trend) < 0.5  # Stable

    def test_compute_trend_decreasing(self):
        """Test trend detection for decreasing costs."""
        learner = ToolCostLearner()
        # Recent samples lower than older
        history = [2.0, 1.9, 1.8, 1.7, 1.0, 1.0, 1.0, 1.0]
        trend = learner._compute_trend(history)
        assert trend < 0  # Negative trend

    def test_compute_trend_empty(self):
        """Test trend detection with empty history."""
        learner = ToolCostLearner()
        trend = learner._compute_trend([])
        assert trend == 0.0


class TestConfidenceConvergence:
    """Tests for confidence interval convergence."""

    def test_confidence_zero_samples(self):
        """Test confidence with zero samples."""
        learner = ToolCostLearner()
        conf = learner._compute_confidence(0)
        assert conf == 0.0

    def test_confidence_low_samples(self):
        """Test confidence with low sample count."""
        learner = ToolCostLearner()
        conf = learner._compute_confidence(5)
        assert 0.0 < conf < 0.5

    def test_confidence_converges_at_threshold(self):
        """Test that confidence converges near MIN_SAMPLES_FOR_CONFIDENCE."""
        learner = ToolCostLearner()
        conf_at_min = learner._compute_confidence(MIN_SAMPLES_FOR_CONFIDENCE)
        conf_at_double = learner._compute_confidence(MIN_SAMPLES_FOR_CONFIDENCE * 2)
        # Both should be high, but double should be higher or equal
        assert conf_at_min > 0.5
        assert conf_at_double >= conf_at_min
        assert conf_at_double <= 1.0  # At or near 1.0

    def test_confidence_maxes_at_one(self):
        """Test that confidence never exceeds 1.0."""
        learner = ToolCostLearner()
        conf = learner._compute_confidence(1000)
        assert conf == 1.0


class TestAggregateMetrics:
    """Tests for aggregating learned metrics."""

    @pytest.mark.asyncio
    async def test_aggregate_empty(self):
        """Test aggregation with no learned state."""
        learner = ToolCostLearner()
        metrics = await learner.aggregate_metrics()
        assert len(metrics) == 0

    @pytest.mark.asyncio
    async def test_aggregate_single_tool(self):
        """Test aggregation with one tool."""
        learner = ToolCostLearner()
        for i in range(10):
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=int(100 * (1.4 + i * 0.01)),
            )

        metrics = await learner.aggregate_metrics()
        assert len(metrics) == 1

        key = ("tool_1", "claude-opus-5")
        m = metrics[key]
        assert m.tool_id == "tool_1"
        assert m.model_id == "claude-opus-5"
        assert m.samples == 10
        assert m.confidence > 0.5

    @pytest.mark.asyncio
    async def test_aggregate_multiple_tools(self):
        """Test aggregation with multiple tools."""
        learner = ToolCostLearner()

        for tool_id in ["tool_1", "tool_2"]:
            for i in range(15):
                await learner.observe_execution(
                    tool_id=tool_id,
                    model_id="claude-opus-5",
                    estimated_cost_cents=100,
                    actual_cost_cents=int(100 * 1.5),
                )

        metrics = await learner.aggregate_metrics()
        assert len(metrics) == 2

        for key, m in metrics.items():
            assert m.samples == 15
            assert m.confidence > 0.8

    @pytest.mark.asyncio
    async def test_aggregate_with_outliers(self):
        """Test that outlier counts are included in metrics."""
        learner = ToolCostLearner(outlier_threshold=2.0)

        for i in range(5):
            cost = 100 if i < 4 else 250  # Last one is outlier
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=cost,
            )

        metrics = await learner.aggregate_metrics()
        key = ("tool_1", "claude-opus-5")
        m = metrics[key]
        assert m.outliers_flagged == 1


class TestResetMethods:
    """Tests for resetting learned state."""

    @pytest.mark.asyncio
    async def test_reset_single_multiplier(self):
        """Test resetting a single tool's multiplier."""
        learner = ToolCostLearner()

        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=150,
        )

        key = ("tool_1", "claude-opus-5")
        assert key in learner.multipliers
        assert key in learner.execution_history

        learner.reset_multiplier("tool_1", "claude-opus-5")
        assert key not in learner.multipliers
        assert key not in learner.execution_history

    @pytest.mark.asyncio
    async def test_reset_all(self):
        """Test resetting all learned state."""
        learner = ToolCostLearner()

        for i in range(3):
            await learner.observe_execution(
                tool_id=f"tool_{i}",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=150,
            )

        assert len(learner.multipliers) == 3
        assert len(learner.execution_history) == 3

        learner.reset_all()
        assert len(learner.multipliers) == 0
        assert len(learner.execution_history) == 0


class TestStatistics:
    """Tests for statistics reporting."""

    @pytest.mark.asyncio
    async def test_stats_empty(self):
        """Test stats with no learned state."""
        learner = ToolCostLearner()
        stats = learner.stats()

        assert stats["tracked_tools"] == 0
        assert stats["total_samples"] == 0
        assert stats["avg_multiplier"] == 1.0
        assert stats["min_multiplier"] == 1.0
        assert stats["max_multiplier"] == 1.0

    @pytest.mark.asyncio
    async def test_stats_with_data(self):
        """Test stats with learned state."""
        learner = ToolCostLearner()

        # Tool 1: 20 samples with 1.5x multiplier
        for _ in range(20):
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=150,
            )

        # Tool 2: 10 samples with 2.0x multiplier
        for _ in range(10):
            await learner.observe_execution(
                tool_id="tool_2",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=200,
            )

        stats = learner.stats()

        assert stats["tracked_tools"] == 2
        assert stats["total_samples"] == 30
        assert stats["avg_samples_per_tool"] == 15.0
        assert 1.5 <= stats["avg_multiplier"] <= 2.0


class TestIntegrationScenarios:
    """Integration tests for real-world usage patterns."""

    @pytest.mark.asyncio
    async def test_cost_improvement_over_time(self):
        """Test that cost estimates improve with more samples."""
        learner = ToolCostLearner(ema_alpha=0.1)
        true_multiplier = 1.6

        # Initial estimate (no history)
        initial_estimate = learner.get_cost_estimate(
            "tool_1", "claude-opus-5", 1000
        )
        assert initial_estimate == 1000  # 1.0x multiplier

        # Observe 30 executions
        for _ in range(30):
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=1000,
                actual_cost_cents=int(1000 * true_multiplier),
            )

        # Final estimate should be close to true value
        final_estimate = learner.get_cost_estimate(
            "tool_1", "claude-opus-5", 1000
        )
        expected = int(1000 * true_multiplier)
        assert abs(final_estimate - expected) < 100  # Within 10%

    @pytest.mark.asyncio
    async def test_multiple_models_tracked_separately(self):
        """Test that different models are tracked separately."""
        learner = ToolCostLearner()

        # Tool with Opus (2.0x multiplier)
        for _ in range(20):
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=200,
            )

        # Same tool with Haiku (1.2x multiplier)
        for _ in range(20):
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-3.5-haiku",
                estimated_cost_cents=100,
                actual_cost_cents=120,
            )

        # Verify different multipliers for different models
        opus_key = ("tool_1", "claude-opus-5")
        haiku_key = ("tool_1", "claude-3.5-haiku")

        opus_mult = learner.multipliers[opus_key]
        haiku_mult = learner.multipliers[haiku_key]

        assert opus_mult > 1.8
        assert haiku_mult < 1.3
        assert abs(opus_mult - haiku_mult) > 0.5

    @pytest.mark.asyncio
    async def test_outlier_doesnt_break_convergence(self):
        """Test that a single outlier doesn't break convergence."""
        learner = ToolCostLearner(ema_alpha=0.1, outlier_threshold=2.0)
        true_multiplier = 1.5

        # 20 normal observations
        for i in range(20):
            cost = int(100 * true_multiplier)
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=cost,
            )

        # One outlier (5x estimated)
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=500,
        )

        # 10 more normal observations
        for i in range(10):
            cost = int(100 * true_multiplier)
            await learner.observe_execution(
                tool_id="tool_1",
                model_id="claude-opus-5",
                estimated_cost_cents=100,
                actual_cost_cents=cost,
            )

        # Multiplier should still be close to true value
        final_mult = learner.multipliers[("tool_1", "claude-opus-5")]
        assert abs(final_mult - true_multiplier) < 0.2


class TestTenantIsolation:
    """Tests for tenant isolation (future extensibility)."""

    @pytest.mark.asyncio
    async def test_observe_with_tenant_id(self):
        """Test that tenant_id parameter is accepted."""
        learner = ToolCostLearner()
        # This should not raise even though tenant_id isn't used yet
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=150,
            tenant_id="custom_tenant",
        )
        key = ("tool_1", "claude-opus-5")
        assert key in learner.multipliers

    @pytest.mark.asyncio
    async def test_aggregate_with_tenant_id(self):
        """Test that aggregate_metrics accepts tenant_id parameter."""
        learner = ToolCostLearner()
        await learner.observe_execution(
            tool_id="tool_1",
            model_id="claude-opus-5",
            estimated_cost_cents=100,
            actual_cost_cents=150,
        )
        # Should not raise
        metrics = await learner.aggregate_metrics(tenant_id="custom_tenant")
        assert len(metrics) == 1
