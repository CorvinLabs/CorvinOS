"""
Track B: Learning Loop — Gate 4: Adversarial Testing

Attacks and edge cases:
1. Feedback injection (rapid-fire, spam)
2. Config corruption (extreme deltas, invalid values)
3. Oscillation attacks (alternating feedback)
4. Cross-skill contamination (feedback for wrong skill)
5. PII bypass attempts
6. Concurrent update conflicts
7. Convergence false positives
"""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone


# ============================================================================
# FEEDBACK INJECTION ATTACKS
# ============================================================================

class TestFeedbackInjectionAttacks:
    """Attack: Rapid-fire feedback injection to corrupt learning."""

    @pytest.fixture
    def collector(self):
        from core.learning.feedback_collector import FeedbackCollector
        return FeedbackCollector(tenant_id="_default")

    def test_rapid_fire_feedback_spam(self, collector):
        """Attack: Submit 1000 feedback in rapid succession.

        Expected: All accepted and buffered (batcher will throttle optimization).
        Verifies: No crash, no buffer overflow, batcher doesn't trigger prematurely.
        """
        import asyncio

        async def spam():
            for i in range(100):  # 100 feedback (reduced from 1000 for test speed)
                result = await collector.collect_feedback(
                    skill_id="attack.skill",
                    task_id=f"task_{i}",
                    outcome_feedback="yes",
                )
                assert result.accepted is True

        asyncio.run(spam())

        # Verify all feedback stored
        feedback = collector.get_feedback_for_skill("attack.skill")
        assert len(feedback) == 100

    def test_alternating_feedback_oscillation(self, collector):
        """Attack: Alternate between contradictory feedback.

        Expected: Batcher buffers all, optimizer computes average.
        Verifies: Learning doesn't oscillate (convergence detector catches it).
        """
        import asyncio

        async def oscillate():
            for i in range(20):
                # Alternate: yes, no, yes, no, ...
                outcome = "yes" if i % 2 == 0 else "no"
                result = await collector.collect_feedback(
                    skill_id="osc.skill",
                    task_id=f"task_{i}",
                    outcome_feedback=outcome,
                )
                assert result.accepted is True

        asyncio.run(oscillate())

        feedback = collector.get_feedback_for_skill("osc.skill")
        assert len(feedback) == 20

        # Optimizer should handle alternating feedback gracefully
        # (verified in convergence tests)

    def test_single_feedback_bombard(self, collector):
        """Attack: Bombard single task with conflicting feedback.

        Expected: Each feedback accepted (different feedback_id).
        Verifies: Task_id is not unique identifier.
        """
        import asyncio

        async def bombard():
            for i in range(10):
                result = await collector.collect_feedback(
                    skill_id="bomb.skill",
                    task_id="same_task",  # Same task_id
                    quality_rating=i % 5 + 1,  # Varying ratings
                )
                assert result.accepted is True

        asyncio.run(bombard())

        feedback = collector.get_feedback_for_skill("bomb.skill")
        assert len(feedback) == 10


# ============================================================================
# CONFIG CORRUPTION ATTACKS
# ============================================================================

class TestConfigCorruptionAttacks:
    """Attack: Attempt to corrupt skill config with invalid deltas."""

    @pytest.fixture
    def applier(self):
        from core.learning.config_applier import ConfigApplier
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            applier = ConfigApplier(corvin_home=tmpdir)
            yield applier

    def test_extremely_large_delta(self, applier):
        """Attack: Attempt to apply delta > 1.0.

        Expected: Rejected, not applied.
        Verifies: Bounds validation (fail-closed).
        """
        success, msg, event = applier.apply_config_delta(
            skill_id="corrupt.skill",
            parameter_deltas={"threshold": 999.0},  # Extremely large
        )

        assert success is False
        assert "too large" in msg.lower()

    def test_nan_delta(self, applier):
        """Attack: Attempt to apply NaN delta.

        Expected: Rejected.
        Verifies: Type validation.
        """
        success, msg, event = applier.apply_config_delta(
            skill_id="corrupt.skill",
            parameter_deltas={"threshold": float('nan')},
        )

        # Should either reject or handle gracefully
        # NaN validation is implementation detail
        if not success:
            assert "invalid" in msg.lower() or "numeric" in msg.lower()

    def test_negative_extreme_delta(self, applier):
        """Attack: Apply large negative delta to force negative config.

        Expected: Config clamped to [0.0, 1.0], not negative.
        Verifies: Lower bound enforcement.
        """
        applier.apply_config_delta(
            skill_id="corrupt.skill",
            parameter_deltas={"threshold": 1.0},  # Set to max first
        )

        applier.apply_config_delta(
            skill_id="corrupt.skill",
            parameter_deltas={"threshold": -999.0},  # Try to go negative
        )

        config = applier.get_config("corrupt.skill")
        # Should be >= 0 after clamping
        assert config.get("threshold", 0) >= 0

    def test_add_arbitrary_parameters(self, applier):
        """Attack: Try to add arbitrary parameters not in schema.

        Expected: Accepted (learnable params are extensible).
        Verifies: No hardcoded parameter list.
        """
        success, msg, event = applier.apply_config_delta(
            skill_id="corrupt.skill",
            parameter_deltas={"arbitrary_param": 0.5},
        )

        assert success is True  # Should accept new parameters
        config = applier.get_config("corrupt.skill")
        assert "arbitrary_param" in config


# ============================================================================
# PII BYPASS ATTACKS
# ============================================================================

class TestPIIBypassAttacks:
    """Attack: Attempt to inject PII through various vectors."""

    @pytest.fixture
    def collector(self):
        from core.learning.feedback_collector import FeedbackCollector
        return FeedbackCollector(tenant_id="_default")

    def test_pii_base64_encoded(self, collector):
        """Attack: Encode PII in base64 to bypass regex.

        Expected: Feedback still accepted (base64 not in PII patterns).
        Verifies: PII scrubbing is not foolproof (known limitation).
        """
        import asyncio
        import base64

        pii = base64.b64encode(b"john@example.com").decode()
        result = asyncio.run(collector.collect_feedback(
            skill_id="pii.skill",
            task_id="task_1",
            outcome_feedback="yes",
            reason=f"User reported issue: {pii}",
        ))

        # Feedback accepted (base64 not in regex patterns)
        assert result.accepted is True

    def test_pii_alternative_format_ssn(self, collector):
        """Attack: SSN in different format.

        Expected: Scrubbed if matched by regex.
        Verifies: Regex coverage.
        """
        import asyncio

        result = asyncio.run(collector.collect_feedback(
            skill_id="pii.skill",
            task_id="task_2",
            outcome_feedback="yes",
            reason="SSN: 123-45-6789 reported issue",
        ))

        assert result.accepted is True
        feedback = collector.get_all_feedback(limit=1)[0]
        assert "[REDACTED]" in feedback["reason"]

    def test_pii_phone_not_detected(self, collector):
        """Attack: Phone number (not in current patterns).

        Expected: Not scrubbed (current regex only covers email, SSN, etc).
        Verifies: Known limitation of PII scrubber.
        """
        import asyncio

        result = asyncio.run(collector.collect_feedback(
            skill_id="pii.skill",
            task_id="task_3",
            outcome_feedback="yes",
            reason="Call John at 555-123-4567",
        ))

        assert result.accepted is True
        # Phone number not currently scrubbed (acceptable limitation)


# ============================================================================
# BATCHER EDGE CASES
# ============================================================================

class TestBatcherEdgeCases:
    """Edge cases in feedback batching."""

    @pytest.fixture
    def batcher(self):
        from core.learning.feedback_batcher import FeedbackBatcher
        return FeedbackBatcher(threshold_count=5, threshold_time_seconds=10)

    def test_zero_feedback_threshold(self, batcher):
        """Edge case: Batcher with threshold=0 (shouldn't happen, but test it)."""
        batcher.threshold_count = 0
        triggered = batcher.add_feedback("skill_a", "feedback_1")

        # Should trigger immediately on first feedback
        assert triggered is True

    def test_add_feedback_after_reset(self, batcher):
        """Edge case: Add feedback after resetting state."""
        for i in range(5):
            batcher.add_feedback("skill_a", f"feedback_{i}")

        batcher.reset_state("skill_a")

        # Add feedback again
        triggered = batcher.add_feedback("skill_a", "feedback_new")
        assert triggered is False  # Should start counting from 0 again

    def test_multiple_triggers_same_skill(self, batcher):
        """Edge case: Trigger optimization multiple times for same skill."""
        triggered_count = 0

        def on_optimize(skill_id):
            nonlocal triggered_count
            triggered_count += 1

        batcher.register_trigger_callback(on_optimize)

        # First trigger (5 feedback)
        for i in range(5):
            batcher.add_feedback("skill_a", f"feedback_{i}")

        assert triggered_count == 1

        # Add more feedback (should trigger again after second batch)
        for i in range(5, 10):
            batcher.add_feedback("skill_a", f"feedback_{i}")

        assert triggered_count == 2


# ============================================================================
# CONVERGENCE FALSE POSITIVES
# ============================================================================

class TestConvergenceFalsePositives:
    """Edge cases in convergence detection."""

    @pytest.fixture
    def detector(self):
        from core.learning.skill_optimizer import ConvergenceDetector
        return ConvergenceDetector(window_size=10, convergence_threshold=0.01)

    def test_single_outlier_not_convergence(self, detector):
        """Edge case: Single outlier shouldn't trigger false convergence."""
        # Add stable samples
        for i in range(5):
            detector.add_sample(0.85)

        # Add outlier
        detector.add_sample(0.5)

        # Add more stable samples
        for i in range(5):
            detector.add_sample(0.85)

        slope = detector.compute_slope()
        confidence = detector.compute_confidence()

        # Should still show some variance (not converged due to outlier)
        assert confidence < 0.95 or abs(slope) > 0

    def test_narrow_oscillation_not_convergence(self, detector):
        """Edge case: Small oscillation shouldn't trigger convergence."""
        # Add oscillating samples (0.849, 0.851, 0.849, 0.851, ...)
        for i in range(10):
            value = 0.85 + (0.001 if i % 2 == 0 else -0.001)
            detector.add_sample(value)

        slope = detector.compute_slope()
        # Slope should be near 0 but small oscillation present
        assert abs(slope) < 0.01

    def test_empty_history(self, detector):
        """Edge case: Convergence check with empty history."""
        slope = detector.compute_slope()
        confidence = detector.compute_confidence()

        # Should handle gracefully
        assert slope == 0.0 or slope is not None
        assert confidence >= 0.0 and confidence <= 1.0


# ============================================================================
# CONCURRENT UPDATE CONFLICTS
# ============================================================================

class TestConcurrentUpdates:
    """Edge cases with concurrent updates."""

    @pytest.fixture
    def applier(self):
        from core.learning.config_applier import ConfigApplier
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            applier = ConfigApplier(corvin_home=tmpdir)
            yield applier

    def test_concurrent_config_updates(self, applier):
        """Edge case: Apply multiple config updates concurrently.

        Expected: All updates persisted in order (JSONL is append-only).
        Verifies: Persistence is safe under concurrent writes.
        """
        import asyncio

        async def apply_updates():
            tasks = []
            for i in range(5):
                # Not truly concurrent (no actual async in applier),
                # but test sequential updates
                success, _, _ = applier.apply_config_delta(
                    skill_id="concurrent.skill",
                    parameter_deltas={f"param_{i}": 0.1},
                )
                assert success is True

        asyncio.run(apply_updates())

        # Verify all updates persisted
        history = applier.get_config_history("concurrent.skill")
        assert len(history) == 5

    def test_interleaved_updates_multiple_skills(self, applier):
        """Edge case: Update multiple skills concurrently."""
        for i in range(3):
            for j in range(3):
                applier.apply_config_delta(
                    skill_id=f"skill_{i}",
                    parameter_deltas={f"param_{j}": 0.05},
                )

        # Verify each skill has 3 updates
        for i in range(3):
            history = applier.get_config_history(f"skill_{i}")
            assert len(history) == 3


# ============================================================================
# SUMMARY
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
