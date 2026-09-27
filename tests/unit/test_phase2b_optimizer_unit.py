"""Unit tests for Phase2bOptimizer core logic."""

import pytest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from core.learning.phase2b_optimizer import Phase2bOptimizer


class TestPhase2bOptimizerCore:
    """Unit tests for Phase2bOptimizer — core confidence tracking logic."""

    @pytest.fixture
    def temp_dir(self):
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def optimizer(self, temp_dir):
        return Phase2bOptimizer("test-tenant", temp_dir)

    def test_init_validates_tenant_id(self, temp_dir):
        """Test that initialization validates tenant_id."""
        with pytest.raises(ValueError, match="tenant_id required"):
            Phase2bOptimizer("", temp_dir)

    def test_init_validates_observation_window(self, temp_dir):
        """Test that observation_window_days is validated."""
        with pytest.raises(ValueError, match="observation_window_days must be"):
            Phase2bOptimizer("test", temp_dir, observation_window_days=1)

    def test_record_confidence_validates_input(self, optimizer):
        """Test that record_confidence_score validates inputs."""
        with pytest.raises(ValueError, match="skill_id required"):
            optimizer.record_confidence_score("", 0.5)

        with pytest.raises(ValueError, match="confidence must be"):
            optimizer.record_confidence_score("skill-1", 1.5)

        with pytest.raises(ValueError, match="confidence must be"):
            optimizer.record_confidence_score("skill-1", -0.1)

    def test_record_confidence_first_observation(self, optimizer):
        """Test recording first confidence observation."""
        trend = optimizer.record_confidence_score("skill-1", 0.60)

        assert trend.skill_id == "skill-1"
        assert trend.current_confidence == 0.60
        assert trend.rolling_avg_7day == 0.60
        assert trend.rolling_min_7day == 0.60
        assert trend.rolling_max_7day == 0.60
        assert trend.n_samples == 1
        assert not trend.phase_2b_eligible  # 0.60 < 0.75

    def test_record_confidence_multiple_observations(self, optimizer):
        """Test tracking multiple observations."""
        base_time = datetime.now(timezone.utc)

        trend1 = optimizer.record_confidence_score("skill-1", 0.50)
        trend2 = optimizer.record_confidence_score("skill-1", 0.60, timestamp=base_time + timedelta(days=1))
        trend3 = optimizer.record_confidence_score("skill-1", 0.70, timestamp=base_time + timedelta(days=2))

        assert trend3.n_samples == 3
        assert trend3.rolling_avg_7day == pytest.approx(0.60, abs=0.01)

    def test_phase_2b_eligibility_threshold(self, optimizer):
        """Test Phase 2b eligibility check at threshold."""
        # Below threshold
        trend = optimizer.record_confidence_score("skill-1", 0.74)
        assert not trend.phase_2b_eligible

        # At threshold
        trend = optimizer.record_confidence_score("skill-2", 0.75)
        assert not trend.phase_2b_eligible  # Must be > 0.75, not =

        # Above threshold
        trend = optimizer.record_confidence_score("skill-3", 0.76)
        assert trend.phase_2b_eligible

    def test_convergence_false_before_plateau(self, optimizer):
        """Test that convergence is not detected before plateau."""
        base_time = datetime.now(timezone.utc)

        # Record high confidence but only 1 day
        optimizer.record_confidence_score("skill-1", 0.95, timestamp=base_time)
        optimizer.record_confidence_score("skill-1", 0.94, timestamp=base_time + timedelta(days=1))

        # Should not be converged yet
        assert not optimizer.detect_convergence("skill-1")

    def test_convergence_true_on_plateau(self, optimizer):
        """Test convergence detection on proper plateau."""
        base_time = datetime.now(timezone.utc)

        # Create high confidence plateau
        high_scores = [0.91, 0.92, 0.91, 0.93, 0.90, 0.92, 0.91]
        for i, score in enumerate(high_scores):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", score, timestamp=ts)

        # Should be converged (avg > 0.90, stable 7 days)
        assert optimizer.detect_convergence("skill-1")

    def test_trend_direction_detection(self, optimizer):
        """Test trend direction detection (climbing/plateau/diverging)."""
        base_time = datetime.now(timezone.utc)

        # Climbing trend
        climbing = [0.50, 0.55, 0.60, 0.65, 0.70]
        for i, score in enumerate(climbing):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-climb", score, timestamp=ts)

        trend = optimizer.get_confidence_trend("skill-climb")
        assert trend.trend_direction == "climbing"

        # Plateau trend
        plateau = [0.70, 0.70, 0.71, 0.69, 0.70]
        for i, score in enumerate(plateau):
            ts = base_time + timedelta(days=10 + i)
            optimizer.record_confidence_score("skill-plateau", score, timestamp=ts)

        trend = optimizer.get_confidence_trend("skill-plateau")
        assert trend.trend_direction == "plateau"

        # Diverging trend
        diverging = [0.80, 0.70, 0.60, 0.50, 0.40]
        for i, score in enumerate(diverging):
            ts = base_time + timedelta(days=20 + i)
            optimizer.record_confidence_score("skill-diverge", score, timestamp=ts)

        trend = optimizer.get_confidence_trend("skill-diverge")
        assert trend.trend_direction == "diverging"

    def test_threshold_bounds(self, optimizer):
        """Test that thresholds are bounded correctly."""
        base_time = datetime.now(timezone.utc)

        # Create high confidence for testing
        for i in range(10):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", 0.91, timestamp=ts)

        # Lower bound test
        delta = optimizer.trigger_optimization("skill-1", "velocity_improvement", 0.8)
        assert delta.new_value >= optimizer.THRESHOLD_MIN

        # Upper bound test
        for i in range(20):  # Apply many convergence optimizations
            ts = base_time + timedelta(days=10 + i)
            optimizer.record_confidence_score("skill-1", 0.92, timestamp=ts)

        # Repeatedly optimize
        for _ in range(10):
            delta = optimizer.trigger_optimization("skill-1", "convergence_detected", 0.9)
            if delta:
                assert delta.new_value <= optimizer.THRESHOLD_MAX


class TestPhase2bOptimizerOptimization:
    """Unit tests for Phase2bOptimizer — parameter optimization logic."""

    @pytest.fixture
    def temp_dir(self):
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def optimizer(self, temp_dir):
        return Phase2bOptimizer("test-tenant", temp_dir)

    def test_optimization_requires_signal_strength(self, optimizer):
        """Test that optimization requires minimum signal strength."""
        base_time = datetime.now(timezone.utc)

        # Setup confidence
        for i in range(10):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", 0.90, timestamp=ts)

        # Weak signal should be rejected
        delta = optimizer.trigger_optimization(
            "skill-1",
            "feedback_signal",
            feedback_signal_strength=0.5,  # < 0.6
        )
        assert delta is None

        # Strong signal should be accepted
        delta = optimizer.trigger_optimization(
            "skill-1",
            "feedback_signal",
            feedback_signal_strength=0.75,  # > 0.6
        )
        assert delta is not None

    def test_convergence_optimization_raises_threshold(self, optimizer):
        """Test that convergence optimization raises threshold."""
        base_time = datetime.now(timezone.utc)

        for i in range(10):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", 0.91, timestamp=ts)

        old_threshold = optimizer._confidence_thresholds.get("skill-1", 0.75)

        delta = optimizer.trigger_optimization("skill-1", "convergence_detected", 0.8)
        assert delta is not None
        assert delta.new_value > old_threshold

    def test_velocity_optimization_lowers_threshold(self, optimizer):
        """Test that velocity improvement lowers threshold."""
        base_time = datetime.now(timezone.utc)

        for i in range(10):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", 0.80, timestamp=ts)

        old_threshold = optimizer._confidence_thresholds.get("skill-1", 0.75)

        delta = optimizer.trigger_optimization("skill-1", "velocity_improvement", 0.8)
        assert delta is not None
        assert delta.new_value < old_threshold

    def test_optimization_parameter_delta_tracking(self, optimizer):
        """Test that parameter deltas are tracked."""
        base_time = datetime.now(timezone.utc)

        for i in range(10):
            ts = base_time + timedelta(days=i)
            optimizer.record_confidence_score("skill-1", 0.91, timestamp=ts)

        # Apply optimizations
        for _ in range(3):
            optimizer.trigger_optimization("skill-1", "convergence_detected", 0.8)

        deltas = optimizer._param_deltas.get("skill-1", [])
        assert len(deltas) == 3

        # Check deltas are ordered chronologically
        for i in range(1, len(deltas)):
            assert deltas[i].timestamp >= deltas[i-1].timestamp


class TestPhase2bOptimizerVelocity:
    """Unit tests for Phase2bOptimizer — learning velocity tracking."""

    @pytest.fixture
    def temp_dir(self):
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def optimizer(self, temp_dir):
        return Phase2bOptimizer("test-tenant", temp_dir)

    def test_feedback_recording(self, optimizer):
        """Test recording feedback signals."""
        base_time = datetime.now(timezone.utc)

        velocity = optimizer.record_feedback("skill-1", 0.85, base_time)
        assert velocity.skill_id == "skill-1"
        assert velocity.feedback_signal_strength == 0.85
        assert not velocity.adjustment_triggered

    def test_learning_velocity_calculation(self, optimizer):
        """Test learning velocity tracking."""
        base_time = datetime.now(timezone.utc)

        # Record feedback
        velocity = optimizer.record_feedback("skill-1", 0.75, base_time)

        # Record confidence (starts monitoring)
        optimizer.record_confidence_score("skill-1", 0.80, base_time + timedelta(minutes=5))

        # Trigger optimization
        delta = optimizer.trigger_optimization("skill-1", "feedback_signal", 0.75)

        # Velocity should be recorded
        # (Implementation detail: tracked internally via _velocities)
        assert "skill-1" in optimizer._velocities


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
