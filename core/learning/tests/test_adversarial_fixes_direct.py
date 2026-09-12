#!/usr/bin/env python3
"""
Direct verification of Adversarial Review Round 2 Fixes

This script directly tests the fixed modules without importing core.learning.
"""

import sys
import os
import re
import json
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from unittest.mock import Mock

# Add the CorvinOS path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


def test_f1_race_condition_fix():
    """F1: Verify RLock in weight_updater.py"""
    print("\n" + "="*70)
    print("F1: RACE CONDITION FIX VERIFICATION")
    print("="*70)

    try:
        # Import directly, bypassing __init__.py
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "weight_updater",
            Path(__file__).parent.parent / "weight_updater.py"
        )
        weight_updater_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(weight_updater_module)

        WeightUpdater = weight_updater_module.WeightUpdater

        # Create updater
        updater = WeightUpdater()

        # Check _lock exists
        if not hasattr(updater, '_lock'):
            print("❌ F1: _lock attribute missing!")
            return False
        print("✅ F1: _lock attribute exists")

        # Check it's an RLock
        if not isinstance(updater._lock, threading.RLock):
            print(f"❌ F1: _lock is {type(updater._lock)}, expected RLock")
            return False
        print("✅ F1: _lock is threading.RLock")

        # Check update_weight has lock
        import inspect
        source = inspect.getsource(updater.update_weight)
        if "with self._lock:" not in source:
            print("❌ F1: update_weight doesn't use lock!")
            return False
        print("✅ F1: update_weight uses 'with self._lock:'")

        # Verify other methods also use lock
        for method_name in ['is_oscillation_clamped', 'get_oscillation_status', 'get_update_history']:
            method = getattr(updater, method_name)
            source = inspect.getsource(method)
            if "with self._lock:" not in source:
                print(f"❌ F1: {method_name} doesn't use lock!")
                return False
        print("✅ F1: All dict-access methods protected by lock")

        return True

    except Exception as e:
        print(f"❌ F1 verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_f2_pii_scrubbing_fix():
    """F2: Verify PII scrubbing functions"""
    print("\n" + "="*70)
    print("F2: PII LEAKAGE FIX VERIFICATION")
    print("="*70)

    try:
        # Import directly
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "event_store",
            Path(__file__).parent.parent / "event_store.py"
        )
        event_store_module = importlib.util.module_from_spec(spec)
        # Mock the LearningEvent import
        event_store_module.LearningEvent = Mock()
        event_store_module.EventType = Mock()
        spec.loader.exec_module(event_store_module)

        _scrub_pii = event_store_module._scrub_pii
        _scrub_pii_deep = event_store_module._scrub_pii_deep

        # Test email scrubbing
        text = "Contact user@example.com for help"
        scrubbed = _scrub_pii(text)
        if "user@example.com" in scrubbed:
            print("❌ F2: Email not scrubbed!")
            return False
        if "[EMAIL]" not in scrubbed:
            print("❌ F2: Email not replaced with [EMAIL]!")
            return False
        print("✅ F2: Email scrubbing works")

        # Test phone scrubbing
        text = "Call 555-123-4567"
        scrubbed = _scrub_pii(text)
        if "555-123" in scrubbed:
            print("❌ F2: Phone not scrubbed!")
            return False
        print("✅ F2: Phone scrubbing works")

        # Test credit card scrubbing
        text = "Card 4111 1111 1111 1111 for payment"
        scrubbed = _scrub_pii(text)
        if "4111" in scrubbed:
            print("❌ F2: Card not scrubbed!")
            return False
        if "[CARD]" not in scrubbed:
            print("❌ F2: Card not replaced with [CARD]!")
            return False
        print("✅ F2: Credit card scrubbing works")

        # Test API key scrubbing
        text = "API key sk_live_1234567890123456789012 is secret"
        scrubbed = _scrub_pii(text)
        if "sk_live_" in scrubbed:
            print("❌ F2: API key not scrubbed!")
            return False
        if "[API_KEY]" not in scrubbed:
            print("❌ F2: API key not replaced with [API_KEY]!")
            return False
        print("✅ F2: API key scrubbing works")

        # Test deep scrubbing
        nested = {
            "feedback": "Email: user@test.com",
            "details": {
                "phone": "555-123-4567",
                "items": ["Card 4111-1111-1111-1111"]
            }
        }
        scrubbed_deep = _scrub_pii_deep(nested)
        scrubbed_str = json.dumps(scrubbed_deep)
        if "@test.com" in scrubbed_str:
            print("❌ F2: Deep scrubbing failed for email!")
            return False
        if "555-123" in scrubbed_str:
            print("❌ F2: Deep scrubbing failed for phone!")
            return False
        if "4111" in scrubbed_str:
            print("❌ F2: Deep scrubbing failed for card!")
            return False
        print("✅ F2: Deep scrubbing works for nested structures")

        return True

    except Exception as e:
        print(f"❌ F2 verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_f3_signature_validation_fix():
    """F3: Verify signature validation"""
    print("\n" + "="*70)
    print("F3: SIGNATURE VALIDATION FIX VERIFICATION")
    print("="*70)

    try:
        # Import directly
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "feedback_ingestion",
            Path(__file__).parent.parent / "feedback_ingestion.py"
        )
        feedback_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(feedback_module)

        SkillFeedback = feedback_module.SkillFeedback
        FeedbackType = feedback_module.FeedbackType
        FeedbackIngestionValidator = feedback_module.FeedbackIngestionValidator

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
        print(f"✅ F3: Signature computation works (SHA256, 64 chars)")

        # Test validator rejects unsigned
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
            signature="0" * 64  # Wrong signature
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


def test_f4_payload_size_limit_fix():
    """F4: Verify payload size limit"""
    print("\n" + "="*70)
    print("F4: UNBOUNDED PAYLOAD FIX VERIFICATION")
    print("="*70)

    try:
        # Import directly
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "event_schema",
            Path(__file__).parent.parent / "event_schema.py"
        )
        event_schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(event_schema_module)

        MAX_PAYLOAD_SIZE_BYTES = event_schema_module.MAX_PAYLOAD_SIZE_BYTES
        LearningEvent = event_schema_module.LearningEvent
        LearningEventType = event_schema_module.LearningEventType

        # Verify constant is 4 KB
        if MAX_PAYLOAD_SIZE_BYTES != 4096:
            print(f"❌ F4: MAX_PAYLOAD_SIZE_BYTES = {MAX_PAYLOAD_SIZE_BYTES}, expected 4096")
            return False
        print(f"✅ F4: MAX_PAYLOAD_SIZE_BYTES = {MAX_PAYLOAD_SIZE_BYTES} (4 KB)")

        # Verify validate_payload_size exists
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
        print("✅ F4: Large payload validator rejects")

        # Test __post_init__ validation
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
            print("✅ F4: __post_init__ rejects oversized payload")

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

    # Test each finding
    results.append(("F1: Race Condition Fix", test_f1_race_condition_fix()))
    results.append(("F2: PII Leakage Fix", test_f2_pii_scrubbing_fix()))
    results.append(("F3: Signature Validation Fix", test_f3_signature_validation_fix()))
    results.append(("F4: Unbounded Payload Fix", test_f4_payload_size_limit_fix()))

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
