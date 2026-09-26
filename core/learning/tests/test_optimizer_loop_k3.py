"""k=3 Tests: OptimizerLoop — Trigger + Parameter-Update (Tier-1/2/3)."""

import pytest
from core.learning.confidence_scoreboard import ConfidenceTrend
from core.learning.optimizer_loop import (
    ParameterUpdate,
    OptimizerLoop,
    ModelOptimizationLoop,
)


class TestParameterUpdate:
    """Tier-1: Unit tests for ParameterUpdate."""

    def test_parameter_update_creation(self):
        """Create a parameter update record."""
        update = ParameterUpdate(
            update_id="upd_001",
            trend_id="trend_task123_opus",
            tenant_id="_default",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            parameter_name="temperature",
            old_value=0.7,
            new_value=0.75,
            confidence_delta=0.05,
            reasoning="Trend improving",
            timestamp="2026-09-27T12:00:00Z",
            audit_chain_hash="abc123",
        )

        assert update.parameter_name == "temperature"
        assert update.new_value == 0.75
        assert update.confidence_delta == 0.05

    def test_parameter_update_immutability(self):
        """ParameterUpdate is frozen (immutable)."""
        update = ParameterUpdate(
            update_id="upd_002",
            trend_id="trend_task123_opus",
            tenant_id="_default",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            parameter_name="temperature",
            old_value=0.7,
            new_value=0.75,
            confidence_delta=0.05,
            reasoning="Trend improving",
            timestamp="2026-09-27T12:00:00Z",
            audit_chain_hash="abc123",
        )

        with pytest.raises(Exception):
            update.new_value = 0.8


class TestOptimizerLoop:
    """Tier-1/2: Unit tests for OptimizerLoop."""

    def test_optimizer_init(self):
        """Initialize optimizer."""
        optimizer = OptimizerLoop(min_confidence_threshold=0.75)
        assert optimizer.min_confidence_threshold == 0.75

    def test_optimizer_threshold_clamped(self):
        """Threshold clamped to [0.0, 1.0]."""
        opt_low = OptimizerLoop(min_confidence_threshold=-0.5)
        assert opt_low.min_confidence_threshold == 0.0

        opt_high = OptimizerLoop(min_confidence_threshold=1.5)
        assert opt_high.min_confidence_threshold == 1.0

    @pytest.mark.asyncio
    async def test_compute_parameter_delta_insufficient_data(self):
        """Trend with n < 10 produces no delta."""
        optimizer = OptimizerLoop()

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=5,  # Too small
            mean_confidence=0.80,
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        delta = await optimizer.compute_parameter_delta(trend)
        assert delta is None

    @pytest.mark.asyncio
    async def test_compute_parameter_delta_below_threshold(self):
        """Trend below confidence threshold produces no delta."""
        optimizer = OptimizerLoop(min_confidence_threshold=0.75)

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.50,  # Below threshold
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        delta = await optimizer.compute_parameter_delta(trend)
        assert delta is None

    @pytest.mark.asyncio
    async def test_compute_parameter_delta_improving_trend(self):
        """Improving trend produces positive delta."""
        optimizer = OptimizerLoop(min_confidence_threshold=0.75)

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.85,
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        delta = await optimizer.compute_parameter_delta(trend)
        assert delta is not None
        assert "learning_rate" in delta
        assert delta["learning_rate"] == 0.001  # Positive

    @pytest.mark.asyncio
    async def test_compute_parameter_delta_degrading_trend(self):
        """Degrading trend produces negative delta."""
        optimizer = OptimizerLoop(min_confidence_threshold=0.75)

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.80,
            std_dev=0.1,
            trend_direction="degrading",
            last_updated="2026-09-27T12:00:00Z",
        )

        delta = await optimizer.compute_parameter_delta(trend)
        assert delta is not None
        assert "learning_rate" in delta
        assert delta["learning_rate"] == -0.0005  # Negative

    @pytest.mark.asyncio
    async def test_compute_parameter_delta_stable_trend(self):
        """Stable trend produces no delta."""
        optimizer = OptimizerLoop(min_confidence_threshold=0.75)

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.80,
            std_dev=0.05,
            trend_direction="stable",
            last_updated="2026-09-27T12:00:00Z",
        )

        delta = await optimizer.compute_parameter_delta(trend)
        assert delta is None or delta == {}

    @pytest.mark.asyncio
    async def test_apply_parameter_update(self):
        """Apply a parameter update."""
        optimizer = OptimizerLoop()

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.85,
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        delta = {"learning_rate": 0.001, "temperature": 0.85}

        update_id = await optimizer.apply_parameter_update(
            trend_id="trend_task123_opus",
            tenant_id="_default",
            trend=trend,
            parameter_delta=delta,
            audit_chain_hash="abc123",
        )

        assert update_id is not None
        assert update_id.startswith("upd_")
        assert update_id in optimizer._parameter_updates

    @pytest.mark.asyncio
    async def test_apply_parameter_update_empty_delta(self):
        """Applying empty delta returns None."""
        optimizer = OptimizerLoop()

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.80,
            std_dev=0.05,
            trend_direction="stable",
            last_updated="2026-09-27T12:00:00Z",
        )

        update_id = await optimizer.apply_parameter_update(
            trend_id="trend_task123_opus",
            tenant_id="_default",
            trend=trend,
            parameter_delta={},
            audit_chain_hash="abc123",
        )

        assert update_id is None

    @pytest.mark.asyncio
    async def test_get_updates_for_model(self):
        """Retrieve updates for a specific model."""
        optimizer = OptimizerLoop()

        trend1 = ConfidenceTrend(
            task_id="task-1",
            model_id="claude-opus-5",
            pattern_key="p1",
            n_samples=10,
            mean_confidence=0.85,
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        trend2 = ConfidenceTrend(
            task_id="task-2",
            model_id="claude-sonnet-5",
            pattern_key="p2",
            n_samples=10,
            mean_confidence=0.90,
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        await optimizer.apply_parameter_update(
            trend_id="trend_1",
            tenant_id="_default",
            trend=trend1,
            parameter_delta={"learning_rate": 0.001},
        )

        await optimizer.apply_parameter_update(
            trend_id="trend_2",
            tenant_id="_default",
            trend=trend2,
            parameter_delta={"learning_rate": 0.001},
        )

        opus_updates = await optimizer.get_updates_for_model("claude-opus-5")
        sonnet_updates = await optimizer.get_updates_for_model("claude-sonnet-5")

        assert len(opus_updates) >= 1
        assert len(sonnet_updates) >= 1

    @pytest.mark.asyncio
    async def test_get_latest_parameter_state(self):
        """Get latest parameter values for a model."""
        optimizer = OptimizerLoop()

        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.85,
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        await optimizer.apply_parameter_update(
            trend_id="trend_task123_opus",
            tenant_id="_default",
            trend=trend,
            parameter_delta={"learning_rate": 0.001, "temperature": 0.85},
        )

        params = await optimizer.get_latest_parameter_state("claude-opus-5")

        assert "learning_rate" in params
        assert "temperature" in params
        assert params["learning_rate"] == 0.001
        assert params["temperature"] == 0.85


# Integration test (Tier-3 — would need mocked scoreboard)
@pytest.mark.asyncio
async def test_model_optimization_loop_structure():
    """Test ModelOptimizationLoop (Tier-3 integration)."""
    # Note: This is a structural test, not a full integration
    # (requires mocked scoreboard + optimizer instances)

    # Verify class can be instantiated
    from core.learning.confidence_scoreboard import ConfidenceScoreboard

    scoreboard = ConfidenceScoreboard()
    optimizer = OptimizerLoop()

    loop = ModelOptimizationLoop(scoreboard=scoreboard, optimizer=optimizer)

    assert loop.scoreboard is scoreboard
    assert loop.optimizer is optimizer
