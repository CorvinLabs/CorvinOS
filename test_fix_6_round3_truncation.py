#!/usr/bin/env python3
"""Security Fix #6 Round 3 (FINAL): Integer Truncation Bypass in Stale Feedback.

Bug: int() truncates 5.5s future → -5s, passing the check when it should reject.
     Example: 5.5s future feedback = age_seconds -5.5, int(-5.5) = -5
     Check: if age_seconds < -5, then -5 < -5 is False → PASS (BUG!)

Fix: Replace int() with math.ceil(abs()) + sign preservation
     Example: 5.5s future = -5.5, ceil(abs(-5.5)) = 6, negate to -6
     Check: if age_seconds < -5, then -6 < -5 is True → REJECT (CORRECT!)

Tests:
1. Stale feedback at 5.5s future (MUST REJECT)
2. Stale feedback at 5.1s future (MUST REJECT)
3. Stale feedback at 5.01s future (MUST REJECT)
4. Valid feedback at 5.0s future (MUST PASS - at boundary)
5. Valid feedback at 4.9s future (MUST PASS)
6. Stale feedback at 3600.5s past (MUST REJECT)
7. Valid feedback at 3599.5s past (MUST PASS)
8. Valid feedback at 3600.0s past (MUST PASS - at boundary)
"""

import sys
import os
from datetime import datetime, timezone, timedelta
import math

# Add CorvinOS to path
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.learning.feedback_validator import FeedbackTTLValidator, FeedbackTTLCheckResult


def test_future_5_5_seconds():
    """Test 1: Feedback at 5.5s in future MUST BE REJECTED."""
    print("\n[Test 1] Feedback at 5.5s in future (MUST REJECT)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    future_time = now + timedelta(seconds=5.5)
    timestamp_iso = future_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")
    print(f"  Reason: {result.reason}")

    assert result.is_valid is False, f"FAILED: Should reject 5.5s future feedback (was {result.is_valid})"
    assert result.age_seconds is not None and result.age_seconds < -5, \
        f"FAILED: age_seconds should be < -5 (was {result.age_seconds})"
    print("  ✓ PASSED - Correctly rejected 5.5s future feedback")
    return True


def test_future_5_1_seconds():
    """Test 2: Feedback at 5.1s in future MUST BE REJECTED."""
    print("\n[Test 2] Feedback at 5.1s in future (MUST REJECT)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    future_time = now + timedelta(seconds=5.1)
    timestamp_iso = future_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")
    print(f"  Reason: {result.reason}")

    assert result.is_valid is False, f"FAILED: Should reject 5.1s future feedback (was {result.is_valid})"
    assert result.age_seconds is not None and result.age_seconds < -5, \
        f"FAILED: age_seconds should be < -5 (was {result.age_seconds})"
    print("  ✓ PASSED - Correctly rejected 5.1s future feedback")
    return True


def test_future_5_01_seconds():
    """Test 3: Feedback at 5.01s in future MUST BE REJECTED."""
    print("\n[Test 3] Feedback at 5.01s in future (MUST REJECT)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    future_time = now + timedelta(seconds=5.01)
    timestamp_iso = future_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")
    print(f"  Reason: {result.reason}")

    assert result.is_valid is False, f"FAILED: Should reject 5.01s future feedback (was {result.is_valid})"
    assert result.age_seconds is not None and result.age_seconds < -5, \
        f"FAILED: age_seconds should be < -5 (was {result.age_seconds})"
    print("  ✓ PASSED - Correctly rejected 5.01s future feedback")
    return True


def test_future_5_0_seconds_boundary():
    """Test 4: Feedback at exactly 5.0s in future MUST PASS (boundary)."""
    print("\n[Test 4] Feedback at exactly 5.0s in future (MUST PASS - boundary)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    future_time = now + timedelta(seconds=5.0)
    timestamp_iso = future_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")

    assert result.is_valid is True, f"FAILED: Should accept 5.0s future feedback (was {result.is_valid})"
    assert result.age_seconds is not None and result.age_seconds == -5, \
        f"FAILED: age_seconds should be -5 (was {result.age_seconds})"
    print("  ✓ PASSED - Correctly accepted 5.0s future feedback")
    return True


def test_future_4_9_seconds():
    """Test 5: Feedback at 4.9s in future MUST PASS."""
    print("\n[Test 5] Feedback at 4.9s in future (MUST PASS)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    future_time = now + timedelta(seconds=4.9)
    timestamp_iso = future_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")

    assert result.is_valid is True, f"FAILED: Should accept 4.9s future feedback (was {result.is_valid})"
    print("  ✓ PASSED - Correctly accepted 4.9s future feedback")
    return True


def test_past_3600_5_seconds():
    """Test 6: Feedback at 3600.5s in past MUST BE REJECTED."""
    print("\n[Test 6] Feedback at 3600.5s in past (MUST REJECT)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    past_time = now - timedelta(seconds=3600.5)
    timestamp_iso = past_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")
    print(f"  Reason: {result.reason}")

    assert result.is_valid is False, f"FAILED: Should reject 3600.5s past feedback (was {result.is_valid})"
    assert result.age_seconds is not None and result.age_seconds > 3600, \
        f"FAILED: age_seconds should be > 3600 (was {result.age_seconds})"
    print("  ✓ PASSED - Correctly rejected 3600.5s past feedback")
    return True


def test_past_3599_5_seconds():
    """Test 7: Feedback at 3599.5s in past MUST PASS."""
    print("\n[Test 7] Feedback at 3599.5s in past (MUST PASS)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    past_time = now - timedelta(seconds=3599.5)
    timestamp_iso = past_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")

    assert result.is_valid is True, f"FAILED: Should accept 3599.5s past feedback (was {result.is_valid})"
    print("  ✓ PASSED - Correctly accepted 3599.5s past feedback")
    return True


def test_past_3600_0_seconds_boundary():
    """Test 8: Feedback at exactly 3600.0s in past MUST PASS (boundary)."""
    print("\n[Test 8] Feedback at exactly 3600.0s in past (MUST PASS - boundary)")

    validator = FeedbackTTLValidator(
        max_age_seconds=3600,
        allow_future_seconds=5,
    )

    now = datetime.now(timezone.utc)
    past_time = now - timedelta(seconds=3600.0)
    timestamp_iso = past_time.isoformat()

    result = validator.validate_timestamp(timestamp_iso)

    print(f"  Timestamp: {timestamp_iso}")
    print(f"  Age result: is_valid={result.is_valid}, age_seconds={result.age_seconds}")

    assert result.is_valid is True, f"FAILED: Should accept 3600.0s past feedback (was {result.is_valid})"
    assert result.age_seconds is not None and result.age_seconds == 3600, \
        f"FAILED: age_seconds should be 3600 (was {result.age_seconds})"
    print("  ✓ PASSED - Correctly accepted 3600.0s past feedback")
    return True


def main():
    """Run all edge-case tests."""
    print("=" * 70)
    print("Security Fix #6 Round 3 (FINAL): Integer Truncation Bypass")
    print("Edge Cases: 5.5s, 5.1s, 5.01s future — all should REJECT")
    print("=" * 70)

    tests = [
        ("Future 5.5s (REJECT)", test_future_5_5_seconds),
        ("Future 5.1s (REJECT)", test_future_5_1_seconds),
        ("Future 5.01s (REJECT)", test_future_5_01_seconds),
        ("Future 5.0s (PASS boundary)", test_future_5_0_seconds_boundary),
        ("Future 4.9s (PASS)", test_future_4_9_seconds),
        ("Past 3600.5s (REJECT)", test_past_3600_5_seconds),
        ("Past 3599.5s (PASS)", test_past_3599_5_seconds),
        ("Past 3600.0s (PASS boundary)", test_past_3600_0_seconds_boundary),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed, None))
        except AssertionError as e:
            results.append((test_name, False, str(e)))
        except Exception as e:
            results.append((test_name, False, f"Exception: {e}"))

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for test_name, passed, error in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{status}: {test_name}")
        if error:
            print(f"       {error}")

    total = len(results)
    passed_count = sum(1 for _, p, _ in results if p)

    print(f"\n{passed_count}/{total} tests passed")

    if passed_count == total:
        print("\n" + "=" * 70)
        print("ALL TESTS PASSED ✓✓✓")
        print("=" * 70)
        print("\nSecurity Fix #6 Round 3 (FINAL) is VERIFIED:")
        print("  ✓ Integer truncation bypass FIXED")
        print("  ✓ Edge cases at 5.5s, 5.1s, 5.01s future correctly REJECTED")
        print("  ✓ Boundary at 5.0s future correctly ACCEPTED")
        print("  ✓ Past feedback boundaries (3600s, 3600.5s) correctly handled")
        print("  ✓ No more truncation of fractional seconds")
        return 0
    else:
        print("\n" + "=" * 70)
        print("SOME TESTS FAILED ✗")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    exit(main())
