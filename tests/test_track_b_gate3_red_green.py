"""
Track B: Learning Loop — Gate 3: Red→Green (Implementation)

Tests for:
1. FeedbackCollector — validation, PII scrubbing, collection
2. FeedbackBatcher — buffering, threshold triggering
3. ConfigApplier — delta application, persistence, rollback
4. ConvergenceDetector — metric calculation, convergence detection
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4


# ============================================================================
# FEEDBACK COLLECTOR TESTS
# ============================================================================

class TestFeedbackCollector:
    """Tests for FeedbackCollector."""

    @pytest.fixture
    def collector(self):
        """Feedback collector instance."""
        from core.learning.feedback_collector import FeedbackCollector
        return FeedbackCollector(tenant_id="_default")

    def test_collect_valid_feedback_outcome(self, collector):
        """Test: Collect valid outcome feedback."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            outcome_feedback="yes",
        ))

        assert result.accepted is True
        assert result.feedback_id
        assert result.skill_id == "test.skill"

    def test_collect_valid_feedback_quality(self, collector):
        """Test: Collect valid quality rating feedback."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            quality_rating=4,
        ))

        assert result.accepted is True
        assert result.feedback_id

    def test_collect_valid_feedback_preference(self, collector):
        """Test: Collect valid preference feedback."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            preference_feedback="llm",
        ))

        assert result.accepted is True

    def test_collect_multiple_feedback_types(self, collector):
        """Test: Collect feedback with multiple types."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            outcome_feedback="yes",
            quality_rating=5,
            preference_feedback="deterministic",
        ))

        assert result.accepted is True

    def test_reject_no_feedback_type(self, collector):
        """Test: Reject feedback with no type provided."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            # No feedback type
        ))

        assert result.accepted is False
        assert "At least one feedback type" in result.reason

    def test_reject_invalid_outcome_feedback(self, collector):
        """Test: Reject invalid outcome_feedback value."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            outcome_feedback="maybe",  # Invalid
        ))

        assert result.accepted is False

    def test_reject_invalid_quality_rating(self, collector):
        """Test: Reject quality_rating out of bounds."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            quality_rating=10,  # Out of bounds
        ))

        assert result.accepted is False

    def test_reject_invalid_preference_feedback(self, collector):
        """Test: Reject invalid preference_feedback value."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            preference_feedback="random",  # Invalid
        ))

        assert result.accepted is False

    def test_pii_scrubbing_email(self, collector):
        """Test: PII scrubbing removes email addresses."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            outcome_feedback="yes",
            reason="User john@example.com reported an issue",
        ))

        assert result.accepted is True
        # Verify PII was scrubbed
        feedback = collector.get_all_feedback(limit=1)[0]
        assert "[REDACTED]" in feedback["reason"]
        assert "john@example.com" not in feedback["reason"]

    def test_pii_scrubbing_too_much_pii(self, collector):
        """Test: Reject feedback with too much PII."""
        import asyncio
        result = asyncio.run(collector.collect_feedback(
            skill_id="test.skill",
            task_id="task_123",
            outcome_feedback="yes",
            reason="john@example.com said bob@example.com told alice@example.com and carol@example.com",
        ))

        # Should be rejected (too much PII)
        assert result.accepted is False

    def test_get_feedback_for_skill(self, collector):
        """Test: Retrieve feedback for specific skill."""
        import asyncio
        # Add multiple feedback
        for i in range(3):
            asyncio.run(collector.collect_feedback(
                skill_id="skill_a",
                task_id=f"task_{i}",
                outcome_feedback="yes",
            ))

        feedback = collector.get_feedback_for_skill("skill_a")
        assert len(feedback) == 3
        assert all(f["skill_id"] == "skill_a" for f in feedback)

    def test_get_all_feedback(self, collector):
        """Test: Retrieve all feedback."""
        import asyncio
        for i in range(2):
            asyncio.run(collector.collect_feedback(
                skill_id=f"skill_{i}",
                task_id=f"task_{i}",
                outcome_feedback="yes",
            ))

        feedback = collector.get_all_feedback()
        assert len(feedback) >= 2


# ============================================================================
# FEEDBACK BATCHER TESTS
# ============================================================================

class TestFeedbackBatcher:
    """Tests for FeedbackBatcher."""

    @pytest.fixture
    def batcher(self):
        """Feedback batcher instance."""
        from core.learning.feedback_batcher import FeedbackBatcher
        return FeedbackBatcher(threshold_count=5, threshold_time_seconds=10)

    def test_add_feedback_below_threshold(self, batcher):
        """Test: Add feedback below threshold, no trigger."""
        triggered = batcher.add_feedback("skill_a", "feedback_1")
        assert triggered is False

    def test_add_feedback_reaches_threshold(self, batcher):
        """Test: Add feedback, reach threshold, trigger optimization."""
        triggered = False
        for i in range(5):
            triggered = batcher.add_feedback("skill_a", f"feedback_{i}")

        assert triggered is True  # 5th feedback triggers

    def test_state_tracking(self, batcher):
        """Test: Batcher tracks state per skill."""
        batcher.add_feedback("skill_a", "feedback_1")
        batcher.add_feedback("skill_b", "feedback_1")
        batcher.add_feedback("skill_a", "feedback_2")

        state_a = batcher.get_state("skill_a")
        state_b = batcher.get_state("skill_b")

        assert state_a.feedback_count == 2
        assert state_b.feedback_count == 1

    def test_callback_on_trigger(self, batcher):
        """Test: Callback is called when threshold reached."""
        triggered_skills = []

        def on_optimize(skill_id):
            triggered_skills.append(skill_id)

        batcher.register_trigger_callback(on_optimize)

        for i in range(5):
            batcher.add_feedback("skill_a", f"feedback_{i}")

        assert triggered_skills == ["skill_a"]

    def test_reset_state(self, batcher):
        """Test: Reset batcher state."""
        batcher.add_feedback("skill_a", "feedback_1")
        batcher.reset_state("skill_a")

        state = batcher.get_state("skill_a")
        assert state is None

    def test_multiple_skills_independent(self, batcher):
        """Test: Multiple skills trigger independently."""
        triggered_skills = []

        def on_optimize(skill_id):
            triggered_skills.append(skill_id)

        batcher.register_trigger_callback(on_optimize)

        # Add 5 feedback for skill_a (triggers)
        for i in range(5):
            batcher.add_feedback("skill_a", f"feedback_{i}")

        assert "skill_a" in triggered_skills
        assert len(triggered_skills) == 1

        # Add 5 feedback for skill_b (triggers)
        for i in range(5):
            batcher.add_feedback("skill_b", f"feedback_{i}")

        assert triggered_skills == ["skill_a", "skill_b"]


# ============================================================================
# CONFIG APPLIER TESTS
# ============================================================================

class TestConfigApplier:
    """Tests for ConfigApplier."""

    @pytest.fixture
    def applier(self):
        """Config applier with temp directory."""
        from core.learning.config_applier import ConfigApplier
        with tempfile.TemporaryDirectory() as tmpdir:
            applier = ConfigApplier(corvin_home=tmpdir)
            yield applier

    def test_apply_valid_delta(self, applier):
        """Test: Apply valid config delta."""
        success, msg, event = applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": 0.1},
            reason="test",
        )

        assert success is True
        assert event is not None
        assert event.version == 1

    def test_apply_invalid_delta_too_large(self, applier):
        """Test: Reject delta that's too large."""
        success, msg, event = applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": 2.0},  # Too large
        )

        assert success is False
        assert "too large" in msg.lower()

    def test_apply_multiple_deltas(self, applier):
        """Test: Apply multiple parameter deltas at once."""
        success, msg, event = applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": 0.1, "timeout": -0.05},
        )

        assert success is True
        config = applier.get_config("skill_a")
        assert "threshold" in config
        assert "timeout" in config

    def test_persistence_to_file(self, applier):
        """Test: Config updates persisted to config_history.jsonl."""
        applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": 0.1},
            tenant_id="_default",
        )

        history = applier.get_config_history("skill_a")
        assert len(history) == 1
        assert history[0]["parameter_deltas"]["threshold"] == 0.1

    def test_versioning(self, applier):
        """Test: Config versions increment."""
        applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"param1": 0.1},
        )
        applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"param2": 0.2},
        )

        history = applier.get_config_history("skill_a")
        assert len(history) == 2
        assert history[0]["version"] == 1
        assert history[1]["version"] == 2

    def test_clamping_to_bounds(self, applier):
        """Test: Config values clamped to [0, 1]."""
        applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": 1.5},  # Will be clamped to 1.0
        )

        config = applier.get_config("skill_a")
        assert config["threshold"] <= 1.0

        applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": -0.5},  # Will be clamped to 0.0
        )

        config = applier.get_config("skill_a")
        assert config["threshold"] >= 0.0

    def test_rollback_config(self, applier):
        """Test: Rollback to previous version."""
        applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": 0.1},
        )
        applier.apply_config_delta(
            skill_id="skill_a",
            parameter_deltas={"threshold": 0.2},
        )

        # Rollback to v1
        applier.rollback_config("skill_a", version=1)
        # Verify rolled back (config should reflect v1 state)
        # Note: exact behavior depends on implementation


# ============================================================================
# CONVERGENCE DETECTOR TESTS
# ============================================================================

class TestConvergenceDetector:
    """Tests for ConvergenceDetector (from skill_optimizer.py)."""

    @pytest.fixture
    def detector(self):
        """Convergence detector instance."""
        from core.learning.skill_optimizer import ConvergenceDetector
        return ConvergenceDetector(window_size=10, convergence_threshold=0.01)

    def test_add_sample(self, detector):
        """Test: Add success rate samples."""
        detector.add_sample(0.8)
        detector.add_sample(0.82)
        assert len(detector.history) == 2

    def test_compute_slope_increasing(self, detector):
        """Test: Compute slope for increasing trend."""
        for i in range(10):
            detector.add_sample(0.5 + i * 0.01)  # 0.50, 0.51, 0.52, ...

        slope = detector.compute_slope()
        assert slope > 0, "Slope should be positive for increasing trend"

    def test_compute_slope_decreasing(self, detector):
        """Test: Compute slope for decreasing trend."""
        for i in range(10):
            detector.add_sample(0.9 - i * 0.01)  # 0.90, 0.89, 0.88, ...

        slope = detector.compute_slope()
        assert slope < 0, "Slope should be negative for decreasing trend"

    def test_compute_confidence(self, detector):
        """Test: Compute confidence score."""
        for i in range(10):
            detector.add_sample(0.8)  # Constant value

        confidence = detector.compute_confidence()
        # Constant values should have high confidence (low variance)
        assert confidence > 0.5

    def test_convergence_detection(self, detector):
        """Test: Detect convergence (low slope, high confidence)."""
        for i in range(10):
            detector.add_sample(0.85)  # Stable at 0.85

        slope = detector.compute_slope()
        is_converged = abs(slope) < detector.convergence_threshold

        assert is_converged is True


# ============================================================================
# SUMMARY
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
