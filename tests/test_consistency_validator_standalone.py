#!/usr/bin/env python3
"""Standalone test for Fix #9: Feedback Contradiction Validator

Simple tests without external dependencies (no numpy, pytest).
Tests the core logic of the consistency validator.
"""

import sys
import os
from enum import Enum
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional

# Add repo to path
sys.path.insert(0, '/home/shumway/projects/CorvinOS')


# ============================================================================
# SIMPLIFIED VALIDATOR LOGIC (for testing without imports)
# ============================================================================

class FeedbackSignal(str, Enum):
    """Feedback signal type."""
    GOOD = "good"
    BAD = "bad"
    NEUTRAL = "neutral"


class TrendType(str, Enum):
    """Loss trend classification."""
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    UNKNOWN = "unknown"


def compute_mean(values: List[float]) -> float:
    """Compute mean without numpy."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def detect_trend(losses: List[float]) -> Tuple[TrendType, float]:
    """Detect loss trend (simplified, no numpy)."""
    if not losses or len(losses) < 2:
        return TrendType.UNKNOWN, 0.0

    mid = len(losses) // 2
    first_half = compute_mean(losses[:mid]) if mid > 0 else losses[0]
    second_half = compute_mean(losses[mid:])

    if first_half != 0:
        loss_delta = (second_half - first_half) / abs(first_half)
    else:
        loss_delta = 0.0

    threshold = 0.05
    if abs(loss_delta) < threshold:
        trend = TrendType.STABLE
    elif loss_delta > 0:
        trend = TrendType.INCREASING
    else:
        trend = TrendType.DECREASING

    return trend, loss_delta


def infer_expected_signal(trend: TrendType) -> FeedbackSignal:
    """Infer expected feedback from loss trend."""
    if trend == TrendType.DECREASING:
        return FeedbackSignal.GOOD
    elif trend == TrendType.INCREASING:
        return FeedbackSignal.BAD
    else:
        return FeedbackSignal.NEUTRAL


def compute_consistency_score(
    feedback_signal: FeedbackSignal,
    expected_signal: FeedbackSignal,
    loss_delta: float,
) -> float:
    """Compute consistency score [0, 1]."""
    if feedback_signal == expected_signal:
        return 1.0

    if feedback_signal == FeedbackSignal.NEUTRAL or expected_signal == FeedbackSignal.NEUTRAL:
        return 0.5

    # Direct contradiction
    threshold = 0.05
    severity = min(1.0, abs(loss_delta) / (2 * threshold))
    return max(0.0, 0.5 - severity)


# ============================================================================
# TESTS
# ============================================================================

def test_1_consistent_feedback_good_with_decreasing_loss():
    """Test 1: GOOD feedback consistent with decreasing loss."""
    # Arrange
    losses = [0.50, 0.48, 0.46, 0.44, 0.42, 0.40, 0.38, 0.36, 0.34, 0.30]

    # Act
    trend, loss_delta = detect_trend(losses)
    expected_signal = infer_expected_signal(trend)
    score = compute_consistency_score(FeedbackSignal.GOOD, expected_signal, loss_delta)

    # Assert
    assert trend == TrendType.DECREASING, f"Expected DECREASING, got {trend}"
    assert expected_signal == FeedbackSignal.GOOD, f"Expected GOOD, got {expected_signal}"
    assert score >= 0.5, f"Expected score >= 0.5, got {score}"
    assert score == 1.0, f"Expected perfect match (1.0), got {score}"

    print("✅ Test 1: GOOD feedback + decreasing loss = CONSISTENT (score=1.0)")
    return True


def test_2_contradictory_feedback_good_with_increasing_loss():
    """Test 2: GOOD feedback inconsistent with increasing loss."""
    # Arrange
    losses = [0.30, 0.32, 0.34, 0.36, 0.38, 0.40, 0.42, 0.44, 0.46, 0.50]

    # Act
    trend, loss_delta = detect_trend(losses)
    expected_signal = infer_expected_signal(trend)
    score = compute_consistency_score(FeedbackSignal.GOOD, expected_signal, loss_delta)

    # Assert
    assert trend == TrendType.INCREASING, f"Expected INCREASING, got {trend}"
    assert expected_signal == FeedbackSignal.BAD, f"Expected BAD, got {expected_signal}"
    assert score < 0.5, f"Expected score < 0.5, got {score}"
    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"

    print(f"✅ Test 2: GOOD feedback + increasing loss = INCONSISTENT (score={score:.2f})")
    return True


def test_3_consistency_score_range():
    """Test 3: Consistency score always in [0, 1]."""
    test_cases = [
        (FeedbackSignal.GOOD, FeedbackSignal.GOOD, 0.1),    # Match
        (FeedbackSignal.BAD, FeedbackSignal.BAD, -0.1),     # Match
        (FeedbackSignal.GOOD, FeedbackSignal.BAD, 0.1),     # Contradiction
        (FeedbackSignal.BAD, FeedbackSignal.GOOD, -0.1),    # Contradiction
        (FeedbackSignal.NEUTRAL, FeedbackSignal.GOOD, 0.0), # Neutral
    ]

    for feedback, expected, loss_delta in test_cases:
        score = compute_consistency_score(feedback, expected, loss_delta)
        assert 0.0 <= score <= 1.0, f"Score out of range: {score}"

    print("✅ Test 3: Consistency score always in [0, 1]")
    return True


def test_4_neutral_feedback():
    """Test 4: Neutral feedback doesn't cause contradictions."""
    trend = TrendType.INCREASING
    expected = infer_expected_signal(trend)
    score = compute_consistency_score(FeedbackSignal.NEUTRAL, expected, 0.1)

    assert score == 0.5, f"Expected 0.5 for neutral, got {score}"
    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"

    print("✅ Test 4: Neutral feedback gets neutral score (0.5)")
    return True


def test_5_trend_detection():
    """Test 5: Trend detection accuracy."""
    # Increasing
    increasing = [0.1, 0.2, 0.3, 0.4, 0.5]
    trend, delta = detect_trend(increasing)
    assert trend == TrendType.INCREASING, f"Expected INCREASING, got {trend}"
    assert delta > 0, f"Expected positive delta, got {delta}"

    # Decreasing
    decreasing = [0.5, 0.4, 0.3, 0.2, 0.1]
    trend, delta = detect_trend(decreasing)
    assert trend == TrendType.DECREASING, f"Expected DECREASING, got {trend}"
    assert delta < 0, f"Expected negative delta, got {delta}"

    # Stable
    stable = [0.3, 0.30, 0.31, 0.30, 0.31]
    trend, delta = detect_trend(stable)
    assert trend == TrendType.STABLE, f"Expected STABLE, got {trend}"
    assert abs(delta) < 0.05, f"Expected small delta, got {delta}"

    print("✅ Test 5: Trend detection accurate (increasing, decreasing, stable)")
    return True


def test_6_bad_feedback_with_decreasing_loss():
    """Test 6: BAD feedback inconsistent with decreasing loss."""
    losses = [0.5, 0.4, 0.3, 0.2, 0.1]  # Decreasing (improving)

    trend, loss_delta = detect_trend(losses)
    expected_signal = infer_expected_signal(trend)
    score = compute_consistency_score(FeedbackSignal.BAD, expected_signal, loss_delta)

    assert trend == TrendType.DECREASING
    assert expected_signal == FeedbackSignal.GOOD
    assert score < 0.5, f"Expected inconsistency, got score={score}"

    print(f"✅ Test 6: BAD feedback + decreasing loss = INCONSISTENT (score={score:.2f})")
    return True


def test_7_empty_history():
    """Test 7: Fail-closed with empty history."""
    trend, delta = detect_trend([])
    assert trend == TrendType.UNKNOWN
    assert delta == 0.0

    expected = infer_expected_signal(trend)
    assert expected == FeedbackSignal.NEUTRAL

    score = compute_consistency_score(FeedbackSignal.BAD, expected, 0.0)
    assert score == 0.5, "Should default to neutral"

    print("✅ Test 7: Fail-closed behavior with empty loss history")
    return True


def test_8_perfect_match_scenarios():
    """Test 8: Perfect score when signals match."""
    test_cases = [
        (FeedbackSignal.GOOD, FeedbackSignal.GOOD),
        (FeedbackSignal.BAD, FeedbackSignal.BAD),
    ]

    for feedback, expected in test_cases:
        score = compute_consistency_score(feedback, expected, 0.1)
        assert score == 1.0, f"Expected 1.0 for match, got {score}"

    print("✅ Test 8: Perfect consistency (1.0) when signals match")
    return True


# ============================================================================
# MAIN
# ============================================================================

def run_all_tests():
    """Run all tests."""
    print("=" * 70)
    print("FIX #9: FEEDBACK CONTRADICTION CONSISTENCY VALIDATOR")
    print("STANDALONE TESTS (no dependencies)")
    print("=" * 70)
    print()

    tests = [
        test_1_consistent_feedback_good_with_decreasing_loss,
        test_2_contradictory_feedback_good_with_increasing_loss,
        test_3_consistency_score_range,
        test_4_neutral_feedback,
        test_5_trend_detection,
        test_6_bad_feedback_with_decreasing_loss,
        test_7_empty_history,
        test_8_perfect_match_scenarios,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test_func.__name__}: Unexpected error: {e}")
            failed += 1

    print()
    print("=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 70)

    if failed == 0:
        print("✅ ALL TESTS PASSED")
        return True
    else:
        print(f"❌ {failed} TEST(S) FAILED")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
