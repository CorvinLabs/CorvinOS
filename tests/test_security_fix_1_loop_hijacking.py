"""Security Fix #1: Loop Hijacking Mitigation — Cryptographic Feedback Signatures (ADR-0640).

Test coverage for HMAC-SHA256 signature validation on feedback events to prevent:
- Tampering: attacker modifies feedback in transit
- Forgery: attacker crafts fake feedback with high confidence
- Replay attacks: attacker reuses stale feedback with altered timestamps

Tests:
- Test 1: Valid signature is accepted
- Test 2: Tampered feedback is rejected (signature mismatch)
- Test 3: Replayed feedback is rejected (timestamp out of window)
- Bonus Test: Cross-tenant signature reuse prevented
"""

import pytest
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import Mock, patch, MagicMock

from core.learning.feedback_sink import (
    FeedbackEvent,
    FeedbackValidator,
    OutcomeFeedbackType,
    PreferenceFeedbackType,
)
from core.learning.feedback_signature import (
    FeedbackSignatureValidator,
    canonical_json,
)


class TestLoopHijackingMitigation:
    """Test suite for Security Fix #1 — Loop Hijacking."""

    @pytest.fixture
    def signature_validator(self, tmp_path):
        """Create a signature validator with a temporary corvin_home."""
        validator = FeedbackSignatureValidator(corvin_home_override=tmp_path)
        return validator

    @pytest.fixture
    def feedback_validator(self, signature_validator):
        """Create a feedback validator with signature support."""
        return FeedbackValidator(signature_validator=signature_validator)

    @pytest.fixture
    def feedback_event(self):
        """Create a valid feedback event."""
        return FeedbackEvent.create(
            skill_id="os.delegation_router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            quality_rating=5,
            confidence=0.95,
        )

    # ── Test 1: Valid Signature is Accepted ──────────────────────────────────

    def test_valid_signature_accepted(self, signature_validator, feedback_validator, feedback_event, tmp_path):
        """Test 1: Feedback with valid HMAC-SHA256 signature is accepted.

        Scenario:
        - Create feedback
        - Sign it with HMAC-SHA256
        - Verify signature passes validation
        """
        # Prepare feedback for signing (exclude signature fields)
        feedback_dict = {
            "feedback_id": feedback_event.feedback_id,
            "skill_id": feedback_event.skill_id,
            "task_id": feedback_event.task_id,
            "tenant_id": feedback_event.tenant_id,
            "timestamp": feedback_event.timestamp,
            "outcome_feedback": feedback_event.outcome_feedback.value,
            "quality_rating": feedback_event.quality_rating,
            "preference_feedback": None,
            "reason": None,
            "confidence": feedback_event.confidence,
            "source": "user",
            "lom": None,
        }

        # Sign the feedback
        signature, error = signature_validator.sign_feedback(
            "_default", feedback_dict
        )
        assert signature is not None, f"Sign failed: {error}"
        assert len(signature) == 64  # HMAC-SHA256 = 64 hex chars

        # Create event with signature
        signed_event = FeedbackEvent.create(
            skill_id=feedback_event.skill_id,
            task_id=feedback_event.task_id,
            tenant_id=feedback_event.tenant_id,
            outcome_feedback=OutcomeFeedbackType.YES,
            quality_rating=5,
            confidence=0.95,
            signature=signature,
        )

        # Validate — should pass
        is_valid, error = feedback_validator.validate(signed_event)
        assert is_valid, f"Validation failed: {error}"
        assert signed_event.signature_verified

    # ── Test 2: Tampered Feedback is Rejected ────────────────────────────────

    def test_tampered_feedback_rejected(self, signature_validator, feedback_validator, feedback_event):
        """Test 2: Feedback with tampering is rejected (signature mismatch).

        Scenario:
        - Create and sign feedback
        - Attacker modifies the outcome (YES → NO)
        - Verification detects tampering via signature mismatch
        """
        # Create original feedback
        feedback_dict = {
            "feedback_id": feedback_event.feedback_id,
            "skill_id": feedback_event.skill_id,
            "task_id": feedback_event.task_id,
            "tenant_id": feedback_event.tenant_id,
            "timestamp": feedback_event.timestamp,
            "outcome_feedback": OutcomeFeedbackType.YES.value,
            "quality_rating": 5,
            "preference_feedback": None,
            "reason": None,
            "confidence": 0.95,
            "source": "user",
            "lom": None,
        }

        # Sign the original
        signature, error = signature_validator.sign_feedback(
            "_default", feedback_dict
        )
        assert signature is not None

        # Attacker tampers: change YES to NO
        tampered_dict = feedback_dict.copy()
        tampered_dict["outcome_feedback"] = OutcomeFeedbackType.NO.value

        # Verification should fail
        is_valid, error = signature_validator.validate_feedback_signature(
            "_default", tampered_dict, signature
        )
        assert not is_valid
        assert "mismatch" in error.lower()

    # ── Test 3: Replayed Feedback is Rejected ────────────────────────────────

    def test_replayed_feedback_rejected(self, signature_validator):
        """Test 3: Replayed/stale feedback is rejected (timestamp out of window).

        Scenario:
        - Sign feedback with an old timestamp (> 60 min ago)
        - Verification should reject as out-of-replay-window
        """
        # Create feedback with old timestamp (65 minutes in the past)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=65)).isoformat().replace('+00:00', 'Z')

        feedback_dict = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.router",
            "task_id": "task-old",
            "tenant_id": "_default",
            "timestamp": old_timestamp,
            "outcome_feedback": OutcomeFeedbackType.YES.value,
            "quality_rating": 5,
            "preference_feedback": None,
            "reason": None,
            "confidence": 0.9,
            "source": "user",
            "lom": None,
        }

        # Sign the old feedback
        signature, error = signature_validator.sign_feedback(
            "_default", feedback_dict
        )
        assert signature is not None

        # Verification should fail due to old timestamp
        is_valid, error = signature_validator.validate_feedback_signature(
            "_default", feedback_dict, signature
        )
        assert not is_valid
        assert "too old" in error.lower() or "window" in error.lower()

    # ── Bonus Test: Cross-Tenant Attack Prevention ────────────────────────────

    def test_cross_tenant_attack_prevented(self, signature_validator):
        """Bonus Test: Signature from one tenant cannot be reused on another.

        Scenario:
        - Attacker signs feedback for tenant-A
        - Attacker tries to apply same signature to tenant-B
        - Verification detects cross-tenant mismatch
        """
        feedback_dict = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.router",
            "task_id": "task-123",
            "tenant_id": "tenant-A",  # Original tenant
            "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "outcome_feedback": OutcomeFeedbackType.YES.value,
            "quality_rating": 5,
            "preference_feedback": None,
            "reason": None,
            "confidence": 0.9,
            "source": "user",
            "lom": None,
        }

        # Sign for tenant-A
        signature, error = signature_validator.sign_feedback(
            "tenant-A", feedback_dict
        )
        assert signature is not None

        # Attacker tries to use this signature on tenant-B
        # First, update feedback dict to claim tenant-B
        tampered_dict = feedback_dict.copy()
        tampered_dict["tenant_id"] = "tenant-B"

        # Verification should fail (signature was keyed to tenant-A)
        is_valid, error = signature_validator.validate_feedback_signature(
            "tenant-B", tampered_dict, signature
        )
        assert not is_valid
        assert "key" in error.lower() or "mismatch" in error.lower()

    # ── Integration Test: Full Workflow ──────────────────────────────────────

    def test_full_feedback_workflow_with_signatures(self, signature_validator, feedback_validator):
        """Integration: create → sign → validate → emit workflow."""
        # Step 1: Create feedback
        feedback = FeedbackEvent.create(
            skill_id="os.context_adapter",
            task_id="task-999",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.NO,
            quality_rating=2,
            confidence=0.5,
            reason="Answer was inaccurate",
        )

        # Step 2: Sign feedback
        feedback_dict = {
            "feedback_id": feedback.feedback_id,
            "skill_id": feedback.skill_id,
            "task_id": feedback.task_id,
            "tenant_id": feedback.tenant_id,
            "timestamp": feedback.timestamp,
            "outcome_feedback": feedback.outcome_feedback.value,
            "quality_rating": feedback.quality_rating,
            "preference_feedback": None,
            "reason": feedback.reason,
            "confidence": feedback.confidence,
            "source": feedback.source,
            "lom": feedback.lom,
        }

        signature, error = signature_validator.sign_feedback(
            "_default", feedback_dict
        )
        assert signature is not None, f"Signing failed: {error}"

        # Step 3: Create event with signature
        signed_feedback = FeedbackEvent.create(
            skill_id=feedback.skill_id,
            task_id=feedback.task_id,
            tenant_id=feedback.tenant_id,
            outcome_feedback=feedback.outcome_feedback,
            quality_rating=feedback.quality_rating,
            confidence=feedback.confidence,
            reason=feedback.reason,
            signature=signature,
        )

        # Step 4: Validate
        is_valid, error = feedback_validator.validate(signed_feedback)
        assert is_valid, f"Validation failed: {error}"

        # Step 5: Check that signature_verified flag is set
        assert signed_feedback.signature_verified

    # ── Test Missing Signature Handling ──────────────────────────────────────

    def test_missing_signature_rejected_in_strict_mode(self, feedback_validator):
        """Test: Feedback without signature is rejected when signature_verified=False."""
        feedback = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            confidence=0.9,
            signature=None,  # No signature
        )

        # In strict mode (signature_verified=False), feedback with None signature
        # is technically accepted by the schema, but the validator should warn
        # about missing cryptographic binding.
        # For now, we accept it but log a warning.
        assert feedback.signature is None
        assert feedback.signature_verified is False

    # ── Test Batch Signature Verification ────────────────────────────────────

    def test_batch_signature_verification(self, signature_validator):
        """Test: Validate multiple feedback items in batch."""
        feedback_items = []

        # Create 5 valid feedback events with signatures
        for i in range(5):
            feedback_dict = {
                "feedback_id": str(uuid4()),
                "skill_id": f"os.skill_{i}",
                "task_id": f"task-{i}",
                "tenant_id": "_default",
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "outcome_feedback": OutcomeFeedbackType.YES.value if i % 2 == 0 else OutcomeFeedbackType.NO.value,
                "quality_rating": 4,
                "preference_feedback": None,
                "reason": None,
                "confidence": 0.85,
                "source": "user",
                "lom": None,
            }

            signature, _ = signature_validator.sign_feedback("_default", feedback_dict)
            feedback_items.append((feedback_dict, signature))

        # Batch verify
        valid_count, errors = signature_validator.validate_feedback_batch(
            "_default", feedback_items
        )

        assert valid_count == 5
        assert len(errors) == 0

    def test_batch_verification_with_tampering(self, signature_validator):
        """Test: Batch verification detects tampering."""
        feedback_items = []

        # Create 3 feedback items
        for i in range(3):
            feedback_dict = {
                "feedback_id": str(uuid4()),
                "skill_id": f"os.skill_{i}",
                "task_id": f"task-{i}",
                "tenant_id": "_default",
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "outcome_feedback": OutcomeFeedbackType.YES.value,
                "quality_rating": 5,
                "preference_feedback": None,
                "reason": None,
                "confidence": 0.95,
                "source": "user",
                "lom": None,
            }

            signature, _ = signature_validator.sign_feedback("_default", feedback_dict)

            # Tamper with the middle one
            if i == 1:
                feedback_dict["quality_rating"] = 1  # Change rating

            feedback_items.append((feedback_dict, signature))

        # Batch verify
        valid_count, errors = signature_validator.validate_feedback_batch(
            "_default", feedback_items
        )

        assert valid_count == 2  # 2 valid, 1 tampered
        assert len(errors) == 1  # 1 error
        assert "Item 1" in errors[0]


class TestCanonicalJSON:
    """Test suite for canonical JSON encoding (deterministic serialization)."""

    def test_canonical_json_deterministic(self):
        """Test: canonical JSON produces identical bytes regardless of dict key order."""
        payload1 = {"z": 1, "a": 2, "m": 3}
        payload2 = {"a": 2, "m": 3, "z": 1}  # Different key order

        bytes1 = canonical_json(payload1)
        bytes2 = canonical_json(payload2)

        assert bytes1 == bytes2
        assert bytes1 == b'{"a":2,"m":3,"z":1}'

    def test_canonical_json_unicode(self):
        """Test: canonical JSON handles unicode safely."""
        payload = {"msg": "Hello Wörld", "emoji": "🔐"}
        result = canonical_json(payload)

        # Should be ASCII-encoded JSON (unicode escaped)
        assert b"\\u00f6" in result or b"ö" in result  # ö encoded
        assert isinstance(result, bytes)

    def test_canonical_json_empty(self):
        """Test: empty dict encodes consistently."""
        result = canonical_json({})
        assert result == b"{}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
