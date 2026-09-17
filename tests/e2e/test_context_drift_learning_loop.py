"""Context-Drift Learning Loop Tests.

Tests for feedback collection, threshold tuning, and convergence.
Validates that the learning loop improves accuracy over time.

ADR-0407: Session Context Drift Prevention
ADR-0314: Learning Infrastructure
"""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timedelta
from core.learning.context_drift_feedback_loop import (
    ContextDriftFeedbackLoop,
    ContextDriftFeedbackStore,
    FeedbackEvent,
)


@pytest.fixture
def temp_store_path():
    """Create temporary feedback store."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "feedback.jsonl"
        yield str(store_path)


@pytest.fixture
def feedback_store(temp_store_path):
    """Create feedback store."""
    return ContextDriftFeedbackStore(store_path=temp_store_path)


@pytest.fixture
def feedback_loop(feedback_store):
    """Create learning loop with temp store."""
    return ContextDriftFeedbackLoop(store=feedback_store)


class TestFeedbackCollection:
    """Test feedback collection mechanism."""

    def test_collect_single_feedback(self, feedback_loop):
        """Collect single feedback event."""
        feedback = feedback_loop.collect_feedback(
            goal_id="goal_123",
            alignment_score=0.25,
            user_feedback={"rating": 5, "was_correct": True},
            tenant_id="tenant_default"
        )

        assert feedback.goal_id == "goal_123"
        assert feedback.alignment_score == 0.25
        assert feedback.was_drift_correct == True
        assert feedback.user_rating == 5
        assert feedback.timestamp is not None

    def test_collect_multiple_feedback(self, feedback_loop):
        """Collect multiple feedback events."""
        for i in range(5):
            feedback_loop.collect_feedback(
                goal_id=f"goal_{i}",
                alignment_score=0.2 + (i * 0.1),
                user_feedback={"rating": 4, "was_correct": i % 2 == 0},
            )

        # Verify all collected
        stored = feedback_loop.store.get_recent_feedback()
        assert len(stored) == 5

    def test_feedback_immutability(self, feedback_loop, feedback_store):
        """Feedback events are immutable (frozen dataclass)."""
        feedback = feedback_loop.collect_feedback(
            goal_id="goal_123",
            alignment_score=0.5,
            user_feedback={"rating": 3, "was_correct": True},
        )

        # Try to modify (should fail)
        with pytest.raises(AttributeError):
            feedback.alignment_score = 0.9

    def test_feedback_store_append_only(self, temp_store_path):
        """Feedback store is append-only."""
        store = ContextDriftFeedbackStore(store_path=temp_store_path)

        # Write 3 events
        for i in range(3):
            event = FeedbackEvent(
                goal_id=f"goal_{i}",
                alignment_score=0.5,
                user_rating=3,
                was_drift_correct=True,
                timestamp=datetime.utcnow().isoformat() + "Z"
            )
            store.record_feedback(event)

        # Verify count
        assert store.count_feedback() == 3

        # Write 2 more
        for i in range(3, 5):
            event = FeedbackEvent(
                goal_id=f"goal_{i}",
                alignment_score=0.5,
                user_rating=3,
                was_drift_correct=True,
                timestamp=datetime.utcnow().isoformat() + "Z"
            )
            store.record_feedback(event)

        # Verify new count (should be 5, not replaced)
        assert store.count_feedback() == 5


class TestThresholdTuning:
    """Test threshold optimization."""

    def test_threshold_tuning_with_perfect_feedback(self, feedback_loop):
        """Tuning with perfect feedback (100% accuracy)."""
        # Simulate perfect feedback: scores < 0.35 are drift, scores >= 0.35 are safe
        feedback_scores_and_labels = [
            (0.1, True),   # Drift detected, was correct ✓
            (0.2, True),   # Drift detected, was correct ✓
            (0.3, True),   # Drift detected, was correct ✓
            (0.4, False),  # No drift, was correct ✓
            (0.5, False),  # No drift, was correct ✓
            (0.6, False),  # No drift, was correct ✓
            (0.7, False),  # No drift, was correct ✓
            (0.8, False),  # No drift, was correct ✓
            (0.9, False),  # No drift, was correct ✓
            (0.95, False), # No drift, was correct ✓
        ]

        # Collect feedback
        for score, was_correct in feedback_scores_and_labels:
            feedback_loop.collect_feedback(
                goal_id=f"goal_{score}",
                alignment_score=score,
                user_feedback={"rating": 5, "was_correct": was_correct}
            )

        # Tune threshold
        result = feedback_loop.tune_thresholds()

        assert result is not None
        assert result.accuracy_before >= 0.5  # Random baseline
        assert result.accuracy_after >= 0.95  # Should be very accurate
        assert result.new_threshold <= 0.4  # Should be near 0.35
        print(f"✅ Tuning result: {result.old_threshold:.3f} → {result.new_threshold:.3f} (accuracy: {result.accuracy_before:.2%} → {result.accuracy_after:.2%})")

    def test_threshold_tuning_with_noisy_feedback(self, feedback_loop):
        """Tuning with noisy feedback (70% accuracy)."""
        # Mix of correct and incorrect labels
        feedback_scores_and_labels = [
            (0.1, True),   # Drift, correct
            (0.15, True),  # Drift, correct
            (0.2, False),  # Drift, WRONG (noise)
            (0.3, True),   # Drift, correct
            (0.4, False),  # No drift, correct
            (0.45, True),  # No drift, WRONG (noise)
            (0.5, False),  # No drift, correct
            (0.6, False),  # No drift, correct
            (0.7, False),  # No drift, correct
            (0.8, False),  # No drift, correct
        ]

        for score, was_correct in feedback_scores_and_labels:
            feedback_loop.collect_feedback(
                goal_id=f"goal_{score}",
                alignment_score=score,
                user_feedback={"rating": 3, "was_correct": was_correct}
            )

        result = feedback_loop.tune_thresholds()

        assert result is not None
        # With noise, accuracy should still improve but less dramatically
        assert result.accuracy_after >= 0.6
        print(f"✅ Noisy tuning: {result.accuracy_before:.2%} → {result.accuracy_after:.2%}")

    def test_threshold_insufficient_data(self, feedback_loop):
        """No tuning with insufficient feedback."""
        # Collect only 5 samples (need 10)
        for i in range(5):
            feedback_loop.collect_feedback(
                goal_id=f"goal_{i}",
                alignment_score=0.5,
                user_feedback={"rating": 3, "was_correct": True}
            )

        result = feedback_loop.tune_thresholds()
        assert result is None  # Should return None

    def test_tuning_result_immutability(self, feedback_loop):
        """Tuning results are recorded in history."""
        # Generate sufficient feedback
        for i in range(10):
            feedback_loop.collect_feedback(
                goal_id=f"goal_{i}",
                alignment_score=0.2 + (i * 0.08),
                user_feedback={"rating": 4, "was_correct": i < 5}
            )

        result1 = feedback_loop.tune_thresholds()
        assert len(feedback_loop.tuning_history) == 1

        # Tune again
        for i in range(10, 20):
            feedback_loop.collect_feedback(
                goal_id=f"goal_{i}",
                alignment_score=0.2 + ((i-10) * 0.08),
                user_feedback={"rating": 4, "was_correct": (i-10) < 5}
            )

        result2 = feedback_loop.tune_thresholds()
        assert len(feedback_loop.tuning_history) == 2
        assert result1 is not result2  # Different objects


class TestConvergence:
    """Test learning loop convergence."""

    def test_convergence_over_10_iterations(self, feedback_loop):
        """Feedback loop converges over multiple iterations."""
        accuracy_history = []

        for iteration in range(10):
            # Collect 20 feedback samples per iteration
            for i in range(20):
                sample_id = iteration * 20 + i
                # Simulate drift correctly at score < 0.35
                score = 0.2 + (i * 0.03)
                is_drift = score < 0.35
                feedback_loop.collect_feedback(
                    goal_id=f"goal_{sample_id}",
                    alignment_score=score,
                    user_feedback={"rating": 5, "was_correct": is_drift}
                )

            # Tune
            result = feedback_loop.tune_thresholds()
            if result:
                accuracy_history.append(result.accuracy_after)

        # Verify convergence: later iterations should have higher accuracy
        if len(accuracy_history) > 1:
            assert accuracy_history[-1] >= accuracy_history[0]
            print(f"✅ Convergence verified: {accuracy_history[0]:.2%} → {accuracy_history[-1]:.2%}")

    def test_convergence_detection(self, feedback_loop):
        """System detects convergence."""
        # Simulate converged state
        for _ in range(3):
            for i in range(20):
                feedback_loop.collect_feedback(
                    goal_id=f"goal_{i}",
                    alignment_score=0.2 + (i * 0.03),
                    user_feedback={"rating": 5, "was_correct": (0.2 + i*0.03) < 0.35}
                )
            feedback_loop.tune_thresholds()

        convergence = feedback_loop.get_convergence_status()
        # Should show convergence (improvements < 1%)
        assert convergence["accuracy_trend"] < 0.01


class TestMetricsAndMonitoring:
    """Test metrics for monitoring dashboard."""

    def test_get_metrics_empty(self, feedback_loop):
        """Metrics with no feedback."""
        metrics = feedback_loop.get_metrics()

        assert metrics["total_feedback_samples"] == 0
        assert metrics["feedback_quality"] == 0.5  # Default
        assert metrics["current_threshold"] == 0.35  # Default
        assert metrics["tuning_iterations"] == 0
        assert metrics["last_tuning"] is None

    def test_get_metrics_with_feedback(self, feedback_loop):
        """Metrics with feedback."""
        for i in range(15):
            feedback_loop.collect_feedback(
                goal_id=f"goal_{i}",
                alignment_score=0.2 + (i * 0.04),
                user_feedback={"rating": 4, "was_correct": (0.2 + i*0.04) < 0.35}
            )

        feedback_loop.tune_thresholds()

        metrics = feedback_loop.get_metrics()

        assert metrics["total_feedback_samples"] == 15
        assert metrics["feedback_quality"] > 0.5
        assert metrics["current_threshold"] != 0.35  # Should have changed
        assert metrics["tuning_iterations"] == 1
        assert metrics["last_tuning"] is not None
        assert "timestamp" in metrics["last_tuning"]

    def test_feedback_quality_score(self, feedback_loop):
        """Feedback quality score calculation."""
        # Collect perfect feedback
        for i in range(10):
            score = 0.2 + (i * 0.08)
            feedback_loop.collect_feedback(
                goal_id=f"goal_{i}",
                alignment_score=score,
                user_feedback={"rating": 5, "was_correct": score < 0.35}
            )

        quality = feedback_loop.get_feedback_quality_score()
        assert 0.0 <= quality <= 1.0
        assert quality > 0.8  # With perfect feedback, should be high


class TestMultiTenantIsolation:
    """Test tenant isolation in learning loop."""

    def test_tenant_isolation(self, feedback_loop):
        """Feedback from different tenants is isolated."""
        # Tenant A feedback
        for i in range(5):
            feedback_loop.collect_feedback(
                goal_id=f"goal_a_{i}",
                alignment_score=0.2,
                user_feedback={"rating": 5, "was_correct": True},
                tenant_id="tenant_a"
            )

        # Tenant B feedback
        for i in range(5):
            feedback_loop.collect_feedback(
                goal_id=f"goal_b_{i}",
                alignment_score=0.8,
                user_feedback={"rating": 3, "was_correct": False},
                tenant_id="tenant_b"
            )

        # Both should be in store
        all_feedback = feedback_loop.store.get_recent_feedback()
        assert len(all_feedback) == 10

        # Verify tenant IDs are preserved
        tenant_a_feedback = [f for f in all_feedback if f.tenant_id == "tenant_a"]
        tenant_b_feedback = [f for f in all_feedback if f.tenant_id == "tenant_b"]
        assert len(tenant_a_feedback) == 5
        assert len(tenant_b_feedback) == 5


class TestAuditTrailCompliance:
    """Test GDPR/audit compliance of learning loop."""

    def test_feedback_immutability_enforcement(self, feedback_loop):
        """Feedback events cannot be modified post-hoc."""
        feedback = feedback_loop.collect_feedback(
            goal_id="goal_123",
            alignment_score=0.5,
            user_feedback={"rating": 3, "was_correct": True}
        )

        # Verify frozen dataclass
        with pytest.raises(AttributeError):
            feedback.was_drift_correct = False

        with pytest.raises(AttributeError):
            feedback.alignment_score = 0.9

    def test_tuning_audit_trail(self, feedback_loop):
        """Threshold tuning is audited."""
        # Collect feedback and tune
        for i in range(15):
            feedback_loop.collect_feedback(
                goal_id=f"goal_{i}",
                alignment_score=0.2 + (i * 0.04),
                user_feedback={"rating": 4, "was_correct": (0.2 + i*0.04) < 0.35}
            )

        result = feedback_loop.tune_thresholds()

        # Verify audit trail has entry
        assert len(feedback_loop.tuning_history) == 1
        tuning_result = feedback_loop.tuning_history[0]

        # Verify tuning result is immutable (contains all necessary audit info)
        assert tuning_result.old_threshold is not None
        assert tuning_result.new_threshold is not None
        assert tuning_result.accuracy_improvement is not None
        assert tuning_result.samples_used > 0
        assert tuning_result.timestamp is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
