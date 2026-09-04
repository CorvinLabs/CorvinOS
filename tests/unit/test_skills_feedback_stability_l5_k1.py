"""Unit Tests: L5 k=1 — Feedback Stability (EMA Smoothing)."""

import pytest
from core.skills.feedback_stability import FeedbackStabilityGate, DriftAlert


class TestEMASmoothing:
    """Test EMA smoothing logic."""

    def test_ema_smoothing_first_feedback(self):
        """First feedback creates baseline."""
        gate = FeedbackStabilityGate(ema_alpha=0.3)

        smoothed, drift = gate.apply_feedback("skill.1", "threshold", raw_delta=0.1)

        assert smoothed.raw_delta == 0.1
        assert smoothed.smoothed_delta == 0.03  # 0.3 * 0.1 + 0.7 * 0
        assert drift is None

    def test_ema_smoothing_converges(self):
        """EMA should dampen wild swings."""
        gate = FeedbackStabilityGate(ema_alpha=0.3)

        # Send: 1.0, -1.0, 1.0 (wild oscillation)
        s1, _ = gate.apply_feedback("skill.1", "metric", 1.0)
        s2, _ = gate.apply_feedback("skill.1", "metric", -1.0)
        s3, _ = gate.apply_feedback("skill.1", "metric", 1.0)

        # Smoothed should be dampened compared to raw
        assert abs(s3.smoothed_delta) < 1.0  # Dampened

    def test_ema_confidence_builds(self):
        """Confidence increases with consistent feedback."""
        gate = FeedbackStabilityGate()

        # Consistent positive feedback
        gate.apply_feedback("skill.1", "metric", 0.1)
        gate.apply_feedback("skill.1", "metric", 0.1)

        conf = gate.get_confidence("skill.1", "metric")
        assert conf > 0  # Some confidence built


class TestDriftDetection:
    """Test drift detection (n-of-1 vs real drift)."""

    def test_single_high_delta_no_drift_alert(self):
        """Single high delta shouldn't trigger alert (n-of-1 noise)."""
        gate = FeedbackStabilityGate(drift_threshold=0.15, drift_window=3)

        smoothed, drift = gate.apply_feedback("skill.1", "metric", 0.5)

        assert drift is None  # No drift alert (single high delta)

    def test_consistent_high_deltas_trigger_drift(self):
        """Multiple high deltas should trigger drift alert."""
        gate = FeedbackStabilityGate(drift_threshold=0.1, drift_window=3)

        gate.apply_feedback("skill.1", "metric", 0.3)
        gate.apply_feedback("skill.1", "metric", 0.3)
        smoothed, drift = gate.apply_feedback("skill.1", "metric", 0.3)

        assert drift is not None
        assert drift.requires_operator_approval

    def test_drift_within_threshold_no_alert(self):
        """Deltas within threshold shouldn't alert."""
        gate = FeedbackStabilityGate(drift_threshold=0.15)

        smoothed, drift = gate.apply_feedback("skill.1", "metric", 0.1)

        assert drift is None


class TestMultiSkillState:
    """Test independent state per skill."""

    def test_different_skills_independent(self):
        """Different skills should have independent state."""
        gate = FeedbackStabilityGate()

        s1, _ = gate.apply_feedback("skill.1", "metric", 0.5)
        s2, _ = gate.apply_feedback("skill.2", "metric", -0.5)

        assert s1.raw_delta == 0.5
        assert s2.raw_delta == -0.5

    def test_different_metrics_independent(self):
        """Different metrics should have independent state."""
        gate = FeedbackStabilityGate()

        s1, _ = gate.apply_feedback("skill.1", "metric_a", 0.2)
        s2, _ = gate.apply_feedback("skill.1", "metric_b", -0.2)

        assert s1.metric_name == "metric_a"
        assert s2.metric_name == "metric_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
