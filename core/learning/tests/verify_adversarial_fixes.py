#!/usr/bin/env python3
"""
Verification script for Adversarial Review Round 2 Fixes
(No external dependencies, direct verification of fixes)

Tests:
- F1: Race Condition in weight_updater.py (HIGH)
- F2: PII Leakage in event_store.py (HIGH)
- F3: Signature Validation in feedback_ingestion.py (MEDIUM)
- F4: Unbounded Payload in event_schema.py (MEDIUM)
"""

import sys
import threading
import time
import re
from pathlib import Path
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor, as_completed





def test_f2_pii_scrubbing():
    """F2: Verify PII scrubbing functions."""
    print("\n" + "="*70)
    print("F2: PII LEAKAGE FIX VERIFICATION")
    print("="*70)

    try:
        from core.learning.event_store import _scrub_pii, _scrub_pii_deep

        # Test email
        text = "Contact user@example.com for help"
        scrubbed = _scrub_pii(text)
        if "user@example.com" in scrubbed:
            print("❌ F2: Email not scrubbed!")
            return False
        if "[EMAIL]" not in scrubbed:
            print("❌ F2: Email not replaced with [EMAIL]!")
            return False
        print("✅ F2: Email scrubbing works")

        # Test phone
        text = "Call 555-123-4567"
        scrubbed = _scrub_pii(text)
        if "555-123" in scrubbed:
            print("❌ F2: Phone not scrubbed!")
            return False
        print("✅ F2: Phone scrubbing works")

        # Test credit card
        text = "Card 4111 1111 1111 1111"
        scrubbed = _scrub_pii(text)
        if "4111" in scrubbed:
            print("❌ F2: Credit card not scrubbed!")
            return False
        print("✅ F2: Credit card scrubbing works")

        # Test deep scrubbing
        nested = {
            "feedback": "Email: user@test.com",
            "details": {
                "phone": "555-123-4567"
            }
        }
        scrubbed_deep = _scrub_pii_deep(nested)
        scrubbed_str = str(scrubbed_deep)
        if "@test.com" in scrubbed_str:
            print("❌ F2: Deep scrubbing failed for email!")
            return False
        print("✅ F2: Deep scrubbing works")

        return True

    except Exception as e:
        print(f"❌ F2 verification failed: {e}")
        return False




def test_f4_payload_size_limit():
    """F4: Verify payload size limit validation."""
    print("\n" + "="*70)
    print("F4: UNBOUNDED PAYLOAD FIX VERIFICATION")
    print("="*70)

    try:
        from core.learning.event_schema import MAX_PAYLOAD_SIZE_BYTES, LearningEvent, LearningEventType
        from datetime import datetime
        import json

        # Verify constant
        if MAX_PAYLOAD_SIZE_BYTES != 4096:
            print(f"❌ F4: MAX_PAYLOAD_SIZE_BYTES = {MAX_PAYLOAD_SIZE_BYTES}, expected 4096")
            return False
        print(f"✅ F4: MAX_PAYLOAD_SIZE_BYTES = {MAX_PAYLOAD_SIZE_BYTES} (4 KB)")

        # Verify validate_payload_size method exists
        if not hasattr(LearningEvent, 'validate_payload_size'):
            print("❌ F4: validate_payload_size method missing!")
            return False
        print("✅ F4: validate_payload_size method exists")

        # Test small payload
        small = {"data": "test"}
        if not LearningEvent.validate_payload_size(small):
            print("❌ F4: Small payload should be valid!")
            return False
        print("✅ F4: Small payload validates")

        # Test large payload
        large = {"data": "x" * (MAX_PAYLOAD_SIZE_BYTES + 1000)}
        if LearningEvent.validate_payload_size(large):
            print("❌ F4: Large payload should be invalid!")
            return False
        print("✅ F4: Large payload rejected by validator")

        # Test that __post_init__ validates
        try:
            event = LearningEvent(
                event_type=LearningEventType.USER_FEEDBACK,
                tenant_id="test",
                instance_id="i1",
                skill_name="s1",
                session_id="sess1",
                timestamp_utc=datetime.utcnow(),
                payload=large
            )
            print("❌ F4: __post_init__ should reject large payload!")
            return False
        except ValueError as e:
            if "exceeds maximum" not in str(e):
                print(f"❌ F4: Error message incorrect: {e}")
                return False
            print("✅ F4: __post_init__ validates and rejects large payload")

        return True

    except Exception as e:
        print(f"❌ F4 verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False




def main():
    """Run all verification tests."""
    print("\n" + "="*70)
    print("ADVERSARIAL REVIEW ROUND 2 - FIXES VERIFICATION")
    print("="*70)

    results = []

    # Import test
    if not test_imports():
        print("\n⚠️  Skipping remaining tests (import failures)")
        return 1

    # F1: Race Condition
    results.append(("F1: Race Condition Fix", test_f1_lock_exists()))

    # F2: PII Leakage
    results.append(("F2: PII Leakage Fix", test_f2_pii_scrubbing()))

    # F3: Signature Validation
    results.append(("F3: Signature Validation Fix", test_f3_signature_validation()))

    # F4: Unbounded Payload
    results.append(("F4: Unbounded Payload Fix", test_f4_payload_size_limit()))

    # Stress test F1
    results.append(("F1: Concurrent Stress Test", test_concurrent_weight_updates()))

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL ADVERSARIAL REVIEW FIXES VERIFIED!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
