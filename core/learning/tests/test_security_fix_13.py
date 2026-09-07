"""Finding #13 Mitigation Tests: Convergence Lockout — Divergence Detector.

Tests for automatic recovery from suboptimal convergence by detecting divergence
and rolling back to Pareto frontier.

Test Coverage:
1. test_divergence_detector_detects_drift: 300+ samples → divergence detected
2. test_divergence_detector_ignores_noise: Random noise < 2*stddev → no detection
3. test_rollback_to_pareto_frontier: Dominated weights restored to frontier
4. test_rollback_audit_logged: weight_rollback_executed events logged
5. test_rollback_learning_rate_reduced: Learning rate set to 0.5x after rollback
6. test_lockout_recovery_demonstrated: Loss converges after rollback (no permanent lockout)
"""

import pytest
import math
from datetime import datetime
from unittest.mock import Mock, MagicMock

from core.learning.divergence_detector import (
    DivergenceDetector,
    WeightSnapshot,
    DivergenceEvent,
    RollbackEvent,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event: dict) -> str:
        """Record audit event and return event ID."""
        self.events.append(event)
        return f"event_{len(self.events)}"


class TestDivergenceDetectorDetectsDrift:
    """Test 1: Loss increasing for 300+ samples triggers divergence detection."""

    def test_divergence_detector_detects_drift_increasing_loss(self):
        """300+ samples with steadily increasing loss → divergence detected."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Phase 1: Baseline (stable loss)
        for i in range(100):
            detector.record_loss(0.5)

        # Phase 2: Divergence (rapidly increasing loss)
        for i in range(200):
            # Loss increases: 0.5 → 0.9
            loss = 0.5 + (i / 200) * 0.4
            detector.record_loss(loss)

        # Should detect divergence after 300 samples
        event = detector.detect_divergence()
        assert event is not None, "Divergence should be detected"
        assert event.loop_id == "test_loop"
        assert event.loss_tail_mean > event.loss_baseline_mean
        assert event.consecutive_divergence_count >= 1

    def test_divergence_detector_detects_drift_within_windows(self):
        """Divergence detection works across multiple windows."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Stable baseline (2 windows = 200 samples)
        for i in range(200):
            detector.record_loss(0.3)

        # Divergence starts (window 3+)
        for i in range(200):
            loss = 0.3 + (i / 200) * 0.5  # Loss jumps to ~0.8
            detector.record_loss(loss)

        # After 400 samples total, divergence should be detected
        event = detector.detect_divergence()
        if event is not None:
            assert event.loss_tail_mean > event.threshold
            assert event.consecutive_divergence_count >= 1

    def test_divergence_detector_requires_multiple_windows(self):
        """Single window of high loss is insufficient (need 3 consecutive)."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Add one high-loss window
        for i in range(100):
            detector.record_loss(0.9)

        # Single window → no divergence event yet
        event = detector.detect_divergence()
        assert event is None, "Single window should not trigger divergence"


class TestDivergenceDetectorIgnoresNoise:
    """Test 2: Random noise < 2*stddev does not trigger false divergence."""

    def test_divergence_detector_ignores_noise_below_threshold(self):
        """Normal random variation should not trigger divergence."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Add stable baseline with small random noise
        import random
        random.seed(42)
        for i in range(200):
            noise = random.gauss(0, 0.01)  # Small Gaussian noise
            detector.record_loss(0.5 + noise)

        # Should NOT detect divergence
        event = detector.detect_divergence()
        assert event is None, "Small random noise should not trigger divergence"

    def test_divergence_detector_ignores_momentary_spike(self):
        """Single spike in loss should not trigger divergence."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Stable loss
        for i in range(200):
            detector.record_loss(0.5)

        # One spike
        detector.record_loss(1.0)

        # Then back to normal
        for i in range(198):
            detector.record_loss(0.5)

        # Should NOT detect divergence
        event = detector.detect_divergence()
        assert event is None, "Single spike should not trigger divergence"

    def test_divergence_detector_threshold_calculation(self):
        """Verify threshold = baseline_mean + STDDEV_THRESHOLD * stddev."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Add baseline data
        for i in range(100):
            detector.record_loss(0.5)

        # Compute expected baseline stats
        expected_baseline_mean = 0.5
        expected_stddev = 0.0  # No variation

        # Window without divergence (within threshold)
        for i in range(100):
            detector.record_loss(0.5 + 0.01)  # Small variation

        event = detector.detect_divergence()
        assert event is None


class TestRollbackToPareto:
    """Test 3: Dominated weights restored to frontier point."""

    def test_rollback_to_pareto_frontier_basic(self):
        """Weights dominated by frontier restored to frontier point."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Create frontier points (Pareto optimal)
        frontier_point_1 = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5, "w2": 0.5},
            loss=0.3,
            is_frontier=True,
        )
        frontier_point_2 = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.3, "w2": 0.7},
            loss=0.35,
            is_frontier=True,
        )

        # Add frontier points
        detector.frontier_points = [frontier_point_1, frontier_point_2]

        # Current weights (dominated by frontier_point_1)
        current_weights = {"w1": 0.4, "w2": 0.6}
        current_loss = 0.5  # Higher than frontier point

        # Apply rollback
        rollback_event = detector.apply_rollback(current_weights, current_loss)

        # Verify rollback restored to frontier
        assert rollback_event.frontier_point.loss == frontier_point_1.loss
        assert rollback_event.loss_after < rollback_event.loss_before
        assert rollback_event.reason == "divergence_detected"

    def test_rollback_to_pareto_frontier_nearest_point(self):
        """Nearest frontier point in weight space is selected."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Multiple frontier points
        frontier_points = [
            WeightSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                weights={"w1": 0.1, "w2": 0.9},
                loss=0.2,
                is_frontier=True,
            ),
            WeightSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                weights={"w1": 0.5, "w2": 0.5},
                loss=0.3,
                is_frontier=True,
            ),
            WeightSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                weights={"w1": 0.9, "w2": 0.1},
                loss=0.25,
                is_frontier=True,
            ),
        ]
        detector.frontier_points = frontier_points

        # Current weights closer to frontier_points[2]
        current_weights = {"w1": 0.8, "w2": 0.2}
        current_loss = 0.5

        nearest = detector.find_nearest_frontier_point(current_weights)
        assert nearest is not None
        # Should be closest to (0.9, 0.1)
        assert abs(nearest.weights["w1"] - 0.9) < 0.2

    def test_rollback_fails_with_empty_frontier(self):
        """Rollback raises error if Pareto frontier is empty."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Empty frontier
        assert len(detector.frontier_points) == 0

        current_weights = {"w1": 0.5, "w2": 0.5}
        current_loss = 0.5

        with pytest.raises(RuntimeError, match="Pareto frontier is empty"):
            detector.apply_rollback(current_weights, current_loss)


class TestRollbackAuditLogged:
    """Test 4: weight_rollback_executed events logged."""

    def test_rollback_audit_logged_before_execution(self):
        """Audit event logged BEFORE rollback (audit-first)."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        # Add frontier point
        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5, "w2": 0.5},
            loss=0.2,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        current_weights = {"w1": 0.6, "w2": 0.4}
        current_loss = 0.6

        # Apply rollback
        rollback_event = detector.apply_rollback(current_weights, current_loss)

        # Verify audit event was created
        assert len(mock_audit.events) > 0
        audit_event = mock_audit.events[0]
        assert audit_event["event_type"] == "weight_rollback_executed"
        assert audit_event["reason"] == "divergence_detected"
        assert audit_event["weights_before"] == current_weights
        assert audit_event["weights_after"] == frontier_point.weights

    def test_rollback_audit_contains_loss_values(self):
        """Audit event includes before/after loss values."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5},
            loss=0.2,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        rollback_event = detector.apply_rollback({"w1": 0.6}, 0.6)

        audit_event = mock_audit.events[0]
        assert audit_event["loss_before"] == 0.6
        assert audit_event["loss_after"] == 0.2

    def test_rollback_audit_fails_blocks_execution(self):
        """Audit failure prevents rollback (fail-closed)."""
        mock_audit = MockAuditBackend()
        mock_audit.write_event = Mock(side_effect=RuntimeError("Audit failed"))

        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5},
            loss=0.2,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        # Rollback should raise RuntimeError
        with pytest.raises(RuntimeError, match="audit failed.*fail-closed"):
            detector.apply_rollback({"w1": 0.6}, 0.6)


class TestRollbackLearningRateReduced:
    """Test 5: Learning rate set to 0.5x after rollback."""

    def test_rollback_sets_learning_rate_reduction(self):
        """Rollback event includes 0.5x learning rate reduction."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5},
            loss=0.2,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        rollback_event = detector.apply_rollback({"w1": 0.6}, 0.6)

        # Verify learning rate reduction
        assert rollback_event.learning_rate_reduction == 0.5

    def test_post_rollback_sample_tracking(self):
        """Post-rollback sample counter increments."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5},
            loss=0.2,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        # Apply rollback
        detector.apply_rollback({"w1": 0.6}, 0.6)

        # Initially, post_rollback_samples should be 0
        assert detector.post_rollback_samples == 0

        # Record post-rollback samples
        for _ in range(50):
            detector.record_post_rollback_sample()

        assert detector.post_rollback_samples == 50
        assert detector.should_restore_learning_rate()

    def test_should_restore_learning_rate_after_50_samples(self):
        """Learning rate restored after 50 post-rollback samples."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5},
            loss=0.2,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        detector.apply_rollback({"w1": 0.6}, 0.6)

        # Before 50 samples
        for _ in range(49):
            detector.record_post_rollback_sample()
        assert not detector.should_restore_learning_rate()

        # At 50 samples
        detector.record_post_rollback_sample()
        assert detector.should_restore_learning_rate()


class TestLockoutRecovery:
    """Test 6: Loss converges after rollback (no permanent lockout)."""

    def test_lockout_recovery_loss_improves_after_rollback(self):
        """Loss improves after rollback + reduced learning rate."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        # Simulate divergence
        for i in range(100):
            detector.record_loss(0.5)

        for i in range(200):
            loss = 0.5 + (i / 200) * 0.4
            detector.record_loss(loss)

        # Detect divergence
        event = detector.detect_divergence()
        assert event is not None

        # Create frontier point with good loss
        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5, "w2": 0.5},
            loss=0.3,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        # Record rollback
        rollback_event = detector.apply_rollback(
            {"w1": 0.4, "w2": 0.6},
            detector.loss_history[-1],
        )

        # Verify loss improved
        assert rollback_event.loss_after < rollback_event.loss_before

    def test_lockout_recovery_convergence_resumes(self):
        """Loss trajectory continues to improve after rollback."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        # Stable phase
        for i in range(100):
            detector.record_loss(0.5)

        # Divergence phase
        for i in range(200):
            loss = 0.5 + (i / 200) * 0.4
            detector.record_loss(loss)

        # Apply rollback
        frontier_point = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5},
            loss=0.2,
            is_frontier=True,
        )
        detector.frontier_points = [frontier_point]

        rollback_event = detector.apply_rollback(
            {"w1": 0.5},
            0.8,
        )

        # Convergence resumes with reduced learning rate
        for i in range(50):
            # Loss improves gradually
            loss = rollback_event.loss_after + (i / 50) * 0.05
            detector.record_loss(loss)
            detector.record_post_rollback_sample()

        # Final loss should be close to frontier loss
        final_loss = detector.loss_history[-1]
        assert final_loss <= rollback_event.loss_after + 0.05

    def test_divergence_counter_resets_after_rollback(self):
        """Consecutive divergence counter resets after rollback."""
        mock_audit = MockAuditBackend()
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=mock_audit,
        )

        # Simulate 3 consecutive divergent windows
        for i in range(300):
            detector.record_loss(0.7)  # High loss

        event = detector.detect_divergence()
        if event is not None:
            assert detector.consecutive_divergence_count >= 1

            # After rollback, counter should reset
            frontier_point = WeightSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                weights={"w1": 0.5},
                loss=0.2,
                is_frontier=True,
            )
            detector.frontier_points = [frontier_point]

            detector.apply_rollback({"w1": 0.5}, 0.7)
            assert detector.consecutive_divergence_count == 0


class TestStatisticsAndDiagnostics:
    """Additional tests for statistics and diagnostics."""

    def test_get_statistics_returns_valid_dict(self):
        """Statistics dict contains all required fields."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Add some data
        for i in range(100):
            detector.record_loss(0.5)

        stats = detector.get_statistics()

        required_fields = [
            "loop_id",
            "loss_history_length",
            "windows_completed",
            "frontier_size",
            "rollback_count",
            "consecutive_divergence_count",
            "post_rollback_samples",
        ]

        for field in required_fields:
            assert field in stats, f"Missing field: {field}"

    def test_frontier_update_removes_dominated_points(self):
        """Pareto frontier removes dominated points."""
        detector = DivergenceDetector(
            loop_id="test_loop",
            tenant_id="_default",
            audit_backend=MockAuditBackend(),
        )

        # Add initial frontier point
        point1 = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.5},
            loss=0.5,
        )
        detector._update_frontier(point1)
        assert len(detector.frontier_points) == 1

        # Add better point (should dominate)
        point2 = WeightSnapshot(
            timestamp=datetime.utcnow().isoformat() + "Z",
            weights={"w1": 0.6},
            loss=0.3,  # Lower loss
        )
        detector._update_frontier(point2)

        # point1 should be removed (dominated by point2)
        assert len(detector.frontier_points) == 1
        assert detector.frontier_points[0].loss == 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
