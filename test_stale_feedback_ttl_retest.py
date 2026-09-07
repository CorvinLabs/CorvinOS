"""
Adversarial Vector: Stale Feedback TTL (Re-Test with NEW Fix)

Test coverage: Can we still exploit stale feedback TTL after the fix?

The FeedbackTTLValidator should now:
1. Require explicit timestamp in every feedback payload
2. Validate timestamp format (ISO 8601 UTC)
3. Reject feedback older than MAX_AGE (default 60 min)
4. Reject future-dated feedback (clock skew detection)
5. Log all rejections as audit events

Attack vectors to test:
1. Inject stale feedback (70+ minutes old)
2. Omit timestamp entirely
3. Submit malformed timestamp
4. Use far-future timestamp
5. Try to bypass with crafted payloads
"""

import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from datetime import datetime, timezone, timedelta
from core.learning.feedback_validator import (
    FeedbackTTLValidator,
    FeedbackStalenessReason,
)


class MockAuditBackend:
    """Mock audit backend to capture events."""
    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)


def test_stale_feedback_injection():
    """
    ATTACK #1: Inject 70-minute-old feedback to poison learning loop.

    Expected: Should be REJECTED
    Actual (after fix): Should be REJECTED with EXCEEDS_TTL
    """
    print("\n" + "=" * 70)
    print("ATTACK #1: Stale Feedback Injection (70 min old)")
    print("=" * 70)

    validator = FeedbackTTLValidator()
    now = datetime.now(timezone.utc)
    stale_timestamp = (now - timedelta(minutes=70)).isoformat() + "Z"

    result = validator.validate_timestamp(
        timestamp_iso=stale_timestamp,
        feedback_id="fb_stale_001",
        tenant_id="_default",
    )

    print(f"Feedback age: 70 minutes")
    print(f"Max TTL: {validator.max_age_seconds / 60} minutes")
    print(f"Result: is_valid={result.is_valid}")
    print(f"Reason: {result.reason}")
    print(f"Message: {result.message}")
    print()

    if result.is_valid:
        print("❌ VULNERABILITY: Stale feedback was ACCEPTED!")
        return False
    elif result.reason == FeedbackStalenessReason.EXCEEDS_TTL:
        print("✅ PASS: Stale feedback correctly REJECTED with EXCEEDS_TTL")
        return True
    else:
        print(f"❌ FAIL: Wrong rejection reason: {result.reason}")
        return False


def test_missing_timestamp():
    """
    ATTACK #2: Omit timestamp to bypass TTL check.

    Expected: Should be REJECTED with MISSING_TIMESTAMP
    """
    print("\n" + "=" * 70)
    print("ATTACK #2: Missing Timestamp")
    print("=" * 70)

    validator = FeedbackTTLValidator()

    result = validator.validate_timestamp(
        timestamp_iso=None,
        feedback_id="fb_notimestamp",
        tenant_id="_default",
    )

    print(f"Timestamp provided: None")
    print(f"Result: is_valid={result.is_valid}")
    print(f"Reason: {result.reason}")
    print(f"Message: {result.message}")
    print()

    if result.is_valid:
        print("❌ VULNERABILITY: Feedback without timestamp was ACCEPTED!")
        return False
    elif result.reason == FeedbackStalenessReason.MISSING_TIMESTAMP:
        print("✅ PASS: Missing timestamp correctly REJECTED")
        return True
    else:
        print(f"❌ FAIL: Wrong rejection reason: {result.reason}")
        return False


def test_empty_timestamp_string():
    """
    ATTACK #3: Submit empty string as timestamp.

    Expected: Should be REJECTED with MISSING_TIMESTAMP
    """
    print("\n" + "=" * 70)
    print("ATTACK #3: Empty Timestamp String")
    print("=" * 70)

    validator = FeedbackTTLValidator()

    result = validator.validate_timestamp(
        timestamp_iso="",
        feedback_id="fb_empty",
        tenant_id="_default",
    )

    print(f"Timestamp provided: '' (empty string)")
    print(f"Result: is_valid={result.is_valid}")
    print(f"Reason: {result.reason}")
    print()

    if result.is_valid:
        print("❌ VULNERABILITY: Empty timestamp was ACCEPTED!")
        return False
    elif result.reason == FeedbackStalenessReason.MISSING_TIMESTAMP:
        print("✅ PASS: Empty timestamp correctly REJECTED")
        return True
    else:
        print(f"❌ FAIL: Wrong rejection reason: {result.reason}")
        return False


def test_malformed_timestamp():
    """
    ATTACK #4: Submit malformed timestamp.

    Expected: Should be REJECTED with TIMESTAMP_PARSE_ERROR
    """
    print("\n" + "=" * 70)
    print("ATTACK #4: Malformed Timestamp")
    print("=" * 70)

    validator = FeedbackTTLValidator()
    malformed_timestamps = [
        "not-a-timestamp",
        "12:34:56",
        "2026-13-45T99:99:99Z",
        "invalid-iso-8601",
        "abc123def",
    ]

    all_rejected = True
    for ts in malformed_timestamps:
        result = validator.validate_timestamp(
            timestamp_iso=ts,
            feedback_id="fb_malformed",
            tenant_id="_default",
        )
        print(f"Timestamp: {ts:30} → valid={result.is_valid}, reason={result.reason}")

        if result.is_valid:
            all_rejected = False
            print(f"  ❌ VULNERABILITY: Malformed timestamp ACCEPTED!")
        elif result.reason != FeedbackStalenessReason.TIMESTAMP_PARSE_ERROR:
            print(f"  ❌ FAIL: Wrong rejection reason: {result.reason}")

    print()
    if all_rejected:
        print("✅ PASS: All malformed timestamps correctly REJECTED")
        return True
    else:
        print("❌ FAIL: Some malformed timestamps were ACCEPTED")
        return False


def test_far_future_timestamp():
    """
    ATTACK #5: Submit far-future timestamp to evade clock checks.

    Expected: Should be REJECTED with FUTURE_TIMESTAMP
    """
    print("\n" + "=" * 70)
    print("ATTACK #5: Far-Future Timestamp")
    print("=" * 70)

    validator = FeedbackTTLValidator(allow_future_seconds=5)
    now = datetime.now(timezone.utc)
    far_future = (now + timedelta(days=1)).isoformat() + "Z"

    result = validator.validate_timestamp(
        timestamp_iso=far_future,
        feedback_id="fb_future",
        tenant_id="_default",
    )

    print(f"Clock skew tolerance: {validator.allow_future_seconds} seconds")
    print(f"Timestamp offset: +1 day")
    print(f"Result: is_valid={result.is_valid}")
    print(f"Reason: {result.reason}")
    print()

    if result.is_valid:
        print("❌ VULNERABILITY: Far-future timestamp was ACCEPTED!")
        return False
    elif result.reason == FeedbackStalenessReason.FUTURE_TIMESTAMP:
        print("✅ PASS: Far-future timestamp correctly REJECTED")
        return True
    else:
        print(f"❌ FAIL: Wrong rejection reason: {result.reason}")
        return False


def test_ttl_boundary():
    """
    ATTACK #6: Try to bypass at exact TTL boundary.

    Expected:
    - At boundary (3600s): Should be ACCEPTED (<=)
    - Beyond boundary (3601s): Should be REJECTED (>)
    """
    print("\n" + "=" * 70)
    print("ATTACK #6: TTL Boundary Bypass")
    print("=" * 70)

    validator = FeedbackTTLValidator(max_age_seconds=3600)
    now = datetime.now(timezone.utc)

    # Test at boundary
    at_boundary = (now - timedelta(seconds=3600)).isoformat() + "Z"
    result_at = validator.validate_timestamp(timestamp_iso=at_boundary)

    # Test beyond boundary
    beyond_boundary = (now - timedelta(seconds=3601)).isoformat() + "Z"
    result_beyond = validator.validate_timestamp(timestamp_iso=beyond_boundary)

    print(f"Feedback at boundary (3600s): valid={result_at.is_valid}")
    print(f"Feedback beyond boundary (3601s): valid={result_beyond.is_valid}")
    print()

    if result_at.is_valid and not result_beyond.is_valid:
        print("✅ PASS: TTL boundary correctly enforced")
        return True
    else:
        print("❌ FAIL: TTL boundary not correctly enforced")
        return False


def test_audit_logging():
    """
    ATTACK #7: Verify stale feedback is logged to audit trail.

    Expected: All rejected feedback should emit audit events
    """
    print("\n" + "=" * 70)
    print("ATTACK #7: Audit Logging Verification")
    print("=" * 70)

    audit_backend = MockAuditBackend()
    validator = FeedbackTTLValidator(audit_backend=audit_backend)
    now = datetime.now(timezone.utc)
    stale_timestamp = (now - timedelta(minutes=70)).isoformat() + "Z"

    result = validator.validate_timestamp(
        timestamp_iso=stale_timestamp,
        feedback_id="fb_audit_001",
        tenant_id="_default",
    )

    print(f"Total audit events logged: {len(audit_backend.events)}")

    if len(audit_backend.events) > 0:
        event = audit_backend.events[0]
        print(f"Event type: {event.get('event_type')}")
        print(f"Feedback ID: {event.get('feedback_id')}")
        print(f"Rejection reason: {event.get('rejection_reason')}")
        print()

        if event.get('event_type') == 'feedback_stale':
            print("✅ PASS: Stale feedback correctly logged to audit trail")
            return True
        else:
            print(f"❌ FAIL: Wrong event type: {event.get('event_type')}")
            return False
    else:
        print("❌ FAIL: No audit events logged for rejected feedback")
        return False


def test_tenant_scoped_ttl():
    """
    ATTACK #8: Try to bypass with tenant-scoped config.

    Expected: Each tenant can have independent TTL, but both should enforce
    """
    print("\n" + "=" * 70)
    print("ATTACK #8: Tenant-Scoped TTL Configuration")
    print("=" * 70)

    # Tenant A: 30 min TTL
    validator_a = FeedbackTTLValidator(max_age_seconds=1800)

    # Tenant B: 120 min TTL
    validator_b = FeedbackTTLValidator(max_age_seconds=7200)

    now = datetime.now(timezone.utc)
    old_60_min = (now - timedelta(minutes=60)).isoformat() + "Z"

    result_a = validator_a.validate_timestamp(timestamp_iso=old_60_min)
    result_b = validator_b.validate_timestamp(timestamp_iso=old_60_min)

    print(f"Tenant A (30 min TTL): 60 min old feedback → valid={result_a.is_valid}")
    print(f"Tenant B (120 min TTL): 60 min old feedback → valid={result_b.is_valid}")
    print()

    if not result_a.is_valid and result_b.is_valid:
        print("✅ PASS: Tenant-scoped TTL correctly enforced")
        return True
    else:
        print("❌ FAIL: Tenant-scoped TTL not correctly enforced")
        return False


def test_clock_skew_tolerance():
    """
    ATTACK #9: Exploit clock skew tolerance.

    Expected:
    - Slight future (within tolerance): Should be ACCEPTED
    - Far future (beyond tolerance): Should be REJECTED
    """
    print("\n" + "=" * 70)
    print("ATTACK #9: Clock Skew Tolerance Exploit")
    print("=" * 70)

    validator = FeedbackTTLValidator(allow_future_seconds=5)
    now = datetime.now(timezone.utc)

    # Within tolerance
    slight_future = (now + timedelta(seconds=3)).isoformat() + "Z"
    result_within = validator.validate_timestamp(timestamp_iso=slight_future)

    # Beyond tolerance
    far_future = (now + timedelta(seconds=10)).isoformat() + "Z"
    result_beyond = validator.validate_timestamp(timestamp_iso=far_future)

    print(f"Allow future seconds: {validator.allow_future_seconds}")
    print(f"Feedback +3s in future: valid={result_within.is_valid}")
    print(f"Feedback +10s in future: valid={result_beyond.is_valid}")
    print()

    if result_within.is_valid and not result_beyond.is_valid:
        print("✅ PASS: Clock skew tolerance correctly enforced")
        return True
    else:
        print("❌ FAIL: Clock skew tolerance not correctly enforced")
        return False


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("ADVERSARIAL VECTOR: STALE FEEDBACK TTL (RE-TEST)")
    print("=" * 70)
    print("Testing FeedbackTTLValidator with NEW fix")

    tests = [
        ("Stale Feedback Injection", test_stale_feedback_injection),
        ("Missing Timestamp", test_missing_timestamp),
        ("Empty Timestamp String", test_empty_timestamp_string),
        ("Malformed Timestamp", test_malformed_timestamp),
        ("Far-Future Timestamp", test_far_future_timestamp),
        ("TTL Boundary", test_ttl_boundary),
        ("Audit Logging", test_audit_logging),
        ("Tenant-Scoped TTL", test_tenant_scoped_ttl),
        ("Clock Skew Tolerance", test_clock_skew_tolerance),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
            results.append((name, False))

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY: STALE FEEDBACK TTL RE-TEST")
    print("=" * 70)

    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")

    print()
    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)
    print(f"Total: {passed_count}/{total_count} tests passed")
    print()

    if passed_count == total_count:
        print("🎉 ALL TESTS PASSED: Stale Feedback TTL Vulnerability is PATCHED")
        print()
        print("VERDICT: exploitable=false, severity=FIXED")
        sys.exit(0)
    else:
        print("⚠️  SOME TESTS FAILED: Stale Feedback TTL Vulnerability may still be EXPLOITABLE")
        print()
        failed_count = total_count - passed_count
        print("VERDICT: exploitable=true, severity=HIGH")
        sys.exit(1)
