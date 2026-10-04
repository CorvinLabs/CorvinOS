"""ADR-0534: Learning Loop Trust Boundary — 3-layer semantic validation.

Verify:
1. Layer 1 (Throttle): max 5 signals/60sec per Skill
2. Layer 2 (Reality): feedback must match execution record
3. Layer 3 (Adversarial): track trust weight per source
"""

import time
from datetime import datetime, timezone

from core.learning.feedback_validator import (
    FeedbackValidator_ADR0534,
    FeedbackThrottler,
    FeedbackRealityValidator,
    AdversarialDetector,
)


def test_throttler_allows_within_limit():
    """Layer 1: Allow up to 5 signals per 60sec."""
    throttler = FeedbackThrottler()
    now = datetime.now(timezone.utc).timestamp()

    # First 5 should pass
    for i in range(5):
        result = throttler.check_rate("os.test_skill", "outcome", now + i)
        assert result.is_allowed, f"Signal {i} should pass rate limit"

    # 6th should fail
    result = throttler.check_rate("os.test_skill", "outcome", now + 5)
    assert not result.is_allowed, "6th signal should exceed rate limit"
    assert "Rate limit exceeded" in result.reason


def test_throttler_decay_after_window():
    """Layer 1: Rate limit resets after 60sec window expires."""
    throttler = FeedbackThrottler()
    now = datetime.now(timezone.utc).timestamp()

    # Fill quota at time t
    for i in range(5):
        throttler.check_rate("os.test_skill", "outcome", now + i)

    # Should fail at t+5
    result = throttler.check_rate("os.test_skill", "outcome", now + 5)
    assert not result.is_allowed

    # Should pass at t+65 (window has decayed)
    result = throttler.check_rate("os.test_skill", "outcome", now + 65)
    assert result.is_allowed, "Rate should reset after window decay"


def test_reality_validator_window_check():
    """Layer 2: Reject feedback outside 10sec window."""
    now = datetime.now(timezone.utc).timestamp()
    execution = {"timestamp": now, "output_hash": "hash:abc123"}

    # Within window: should pass
    feedback_on_time = {"timestamp": now + 2, "expected_output_hash": "hash:abc123"}
    result = FeedbackRealityValidator.validate(execution, feedback_on_time, now + 2)
    assert result.is_valid, "Feedback within 10sec should pass"

    # Outside window: should fail
    feedback_late = {"timestamp": now + 15, "expected_output_hash": "hash:abc123"}
    result = FeedbackRealityValidator.validate(execution, feedback_late, now + 15)
    assert not result.is_valid, "Feedback outside 10sec should fail"
    assert "window" in result.reason.lower()


def test_reality_validator_output_hash_mismatch():
    """Layer 2: Reject feedback with mismatched output hash."""
    now = datetime.now(timezone.utc).timestamp()
    execution = {"timestamp": now, "output_hash": "hash:abc123"}

    # Hash mismatch
    feedback = {"timestamp": now + 2, "expected_output_hash": "hash:wrong"}
    result = FeedbackRealityValidator.validate(execution, feedback, now + 2)
    assert not result.is_valid, "Mismatched output hash should fail"
    assert "hash" in result.reason.lower()


def test_adversarial_detector_trust_weight():
    """Layer 3: Track trust weight ∈ [0.1, 1.0]."""
    detector = AdversarialDetector()

    # Default weight is 1.0
    weight = detector.get_trust_weight("source_a")
    assert weight == 1.0

    # Correct feedback: weight increases slightly
    score = detector.record_feedback("source_a", was_correct=True)
    assert score.weight > 1.0 or score.weight == 1.0  # Capped at 1.0

    # Incorrect feedback: weight decays
    for _ in range(10):
        score = detector.record_feedback("source_a", was_correct=False)

    assert score.weight < 1.0, "Repeated incorrect feedback should decay trust"
    assert score.weight >= 0.1, "Weight should not drop below 0.1"


def test_unified_validator_rejects_throttled_feedback():
    """Full 3-layer validation: reject throttled feedback."""
    validator = FeedbackValidator_ADR0534()
    now = datetime.now(timezone.utc).timestamp()
    execution = {"timestamp": now, "output_hash": "hash:abc"}

    # Exceed rate limit
    for i in range(6):
        feedback = {"timestamp": now + i, "expected_output_hash": "hash:abc", "type": "outcome"}
        is_valid, reason = validator.validate_feedback(
            "os.test", execution, feedback, "source_1", now + i
        )
        if i < 5:
            assert is_valid, f"First 5 should pass, got: {reason}"
        else:
            assert not is_valid, "6th should be throttled"
            assert "THROTTLE" in reason


def test_unified_validator_rejects_stale_feedback():
    """Full 3-layer validation: reject stale feedback."""
    validator = FeedbackValidator_ADR0534()
    now = datetime.now(timezone.utc).timestamp()
    execution = {"timestamp": now, "output_hash": "hash:abc"}

    # Feedback way too old
    feedback = {"timestamp": now - 20, "expected_output_hash": "hash:abc"}
    is_valid, reason = validator.validate_feedback(
        "os.test", execution, feedback, "source_1", now
    )
    assert not is_valid, "Stale feedback should be rejected"
    assert "REALITY_CHECK" in reason


if __name__ == "__main__":
    test_throttler_allows_within_limit()
    test_throttler_decay_after_window()
    test_reality_validator_window_check()
    test_reality_validator_output_hash_mismatch()
    test_adversarial_detector_trust_weight()
    test_unified_validator_rejects_throttled_feedback()
    test_unified_validator_rejects_stale_feedback()
    print("✓ All ADR-0534 tests passed")
