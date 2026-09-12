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

def test_imports():
    """Verify all modules can be imported."""
    print("\n" + "="*70)
    print("TESTING IMPORTS")
    print("="*70)

    try:
        # F1: weight_updater
        print("Importing weight_updater...", end=" ")
        from core.learning.weight_updater import WeightUpdater, WeightAuditFailedError
        print("✅")

        # F2: event_store
        print("Importing event_store...", end=" ")
        from core.learning.event_store import _scrub_pii, _scrub_pii_deep
        print("✅")

        # F3: feedback_ingestion
        print("Importing feedback_ingestion...", end=" ")
        from core.learning.feedback_ingestion import SkillFeedback, FeedbackType, FeedbackIngestionValidator
        print("✅")

        # F4: event_schema
        print("Importing event_schema...", end=" ")
        from core.learning.event_schema import MAX_PAYLOAD_SIZE_BYTES
        print("✅")

        return True
    except ImportError as e:
        print(f"\n❌ Import failed: {e}")
        return False


def test_f1_lock_exists():
    """F1: Verify RLock exists in WeightUpdater."""
    print("\n" + "="*70)
    print("F1: RACE CONDITION FIX VERIFICATION")
    print("="*70)

    try:
        from core.learning.weight_updater import WeightUpdater
        import threading

        updater = WeightUpdater()

        # Check lock exists
        if not hasattr(updater, '_lock'):
            print("❌ F1: _lock attribute missing!")
            return False

        # Check it's an RLock
        if not isinstance(updater._lock, threading.RLock):
            print(f"❌ F1: _lock is {type(updater._lock)}, expected RLock")
            return False

        print("✅ F1: Threading.RLock present in WeightUpdater")

        # Check that update_weight has lock context
        import inspect
        source = inspect.getsource(updater.update_weight)
        if "with self._lock:" not in source:
            print("❌ F1: update_weight doesn't use lock!")
            return False

        print("✅ F1: update_weight uses lock protection")

        # Check other methods
        for method_name in ['is_oscillation_clamped', 'get_oscillation_status', 'get_update_history']:
            method = getattr(updater, method_name)
            source = inspect.getsource(method)
            if "with self._lock:" not in source:
                print(f"❌ F1: {method_name} doesn't use lock!")
                return False

        print("✅ F1: All dict-access methods use lock protection")
        return True

    except Exception as e:
        print(f"❌ F1 verification failed: {e}")
        return False


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


def test_f3_signature_validation():
    """F3: Verify signature validation for feedback."""
    print("\n" + "="*70)
    print("F3: SIGNATURE VALIDATION FIX VERIFICATION")
    print("="*70)

    try:
        from core.learning.feedback_ingestion import SkillFeedback, FeedbackType, FeedbackIngestionValidator
        import os

        secret = "test_secret_key"
        os.environ["CORVIN_FEEDBACK_SECRET"] = secret

        # Test signature computation
        feedback = SkillFeedback(
            skill_id="test.skill",
            task_id="task_1",
            feedback_type=FeedbackType.GOOD
        )

        sig = feedback.compute_signature(secret=secret)
        if not sig or len(sig) != 64:
            print(f"❌ F3: Signature invalid (got {sig})")
            return False
        print("✅ F3: Signature computation works (SHA256, 64 chars)")

        # Test validator rejects unsigned feedback
        validator = FeedbackIngestionValidator(feedback_secret=secret)
        is_valid, error = validator.validate(feedback)
        if is_valid:
            print("❌ F3: Validator should reject unsigned feedback!")
            return False
        if "signature" not in error.lower():
            print(f"❌ F3: Error should mention signature: {error}")
            return False
        print("✅ F3: Validator rejects unsigned feedback")

        # Test validator accepts valid signature
        feedback_signed = SkillFeedback(
            skill_id="test.skill",
            task_id="task_1",
            feedback_type=FeedbackType.GOOD,
            signature=sig
        )
        is_valid, error = validator.validate(feedback_signed)
        if not is_valid:
            print(f"❌ F3: Validator should accept valid signature: {error}")
            return False
        print("✅ F3: Validator accepts valid signature")

        # Test validator rejects wrong signature
        feedback_wrong = SkillFeedback(
            skill_id="test.skill",
            task_id="task_1",
            feedback_type=FeedbackType.GOOD,
            signature="wrong_signature_123456789012345678901234567890123456"
        )
        is_valid, error = validator.validate(feedback_wrong)
        if is_valid:
            print("❌ F3: Validator should reject wrong signature!")
            return False
        print("✅ F3: Validator rejects wrong signature")

        return True

    except Exception as e:
        print(f"❌ F3 verification failed: {e}")
        import traceback
        traceback.print_exc()
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


def test_concurrent_weight_updates():
    """F1: Stress test concurrent weight updates."""
    print("\n" + "="*70)
    print("F1: CONCURRENT STRESS TEST")
    print("="*70)

    try:
        from core.learning.weight_updater import WeightUpdater
        from unittest.mock import Mock

        updater = WeightUpdater()
        audit_backend = Mock()
        audit_backend.write_event = Mock(return_value=None)

        results = []
        errors = []

        def update_task(delta):
            try:
                record = updater.update_weight(
                    weight_id="test.weight",
                    delta=delta,
                    audit_backend=audit_backend,
                    tenant_id="test"
                )
                results.append(record)
            except Exception as e:
                errors.append(e)

        # Launch concurrent tasks
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(update_task, 0.01 * i)
                for i in range(20)
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    errors.append(e)

        if errors:
            print(f"❌ F1: Concurrent test had errors: {errors}")
            return False

        if len(results) != 20:
            print(f"❌ F1: Expected 20 results, got {len(results)}")
            return False

        if audit_backend.write_event.call_count != 20:
            print(f"❌ F1: Expected 20 audit writes, got {audit_backend.write_event.call_count}")
            return False

        print(f"✅ F1: Concurrent stress test passed (20 ops, 0 errors, 0 races)")
        return True

    except Exception as e:
        print(f"❌ F1 stress test failed: {e}")
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
