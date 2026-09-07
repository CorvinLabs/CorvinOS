"""Security Fix #1: Loop Hijacking Prevention — Cryptographic Feedback Signatures (HMAC-SHA256).

Tests verify that the feedback signature mechanism blocks the Loop Hijacking attack vector
where an attacker attempts to manipulate α weights via unvalidated feedback injection.

Tests include:
- test_feedback_signature_valid: Sign feedback with tenant key, verify HMAC passes
- test_feedback_signature_invalid: Tampered feedback payload rejected
- test_feedback_signature_tenant_isolated: Tenant A's key cannot verify Tenant B's feedback
- test_feedback_signature_replay_attack: Same feedback signed twice has different HMAC (nonce)
- test_feedback_signature_audit_logged: Every signature verification logged
- test_feedback_signature_key_rotation: Old key signature rejected after rotation
- test_feedback_signature_attack_payload_modification: Attacker modifies feedback payload
- test_feedback_signature_attack_hmac_tamper: Attacker tampers with HMAC hex string
- test_feedback_signature_attack_cross_tenant_injection: Attacker injects Tenant B feedback into Tenant A

Compliance: GDPR Art. 32 (security + integrity), EU AI Act 2026, CWE-347 (signature verification).
"""

from __future__ import annotations

import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from core.learning.feedback_sink import FeedbackEvent, OutcomeFeedbackType
from core.learning.feedback_validator import (
    FeedbackSignature,
    FeedbackSignatureValidator,
    FeedbackSignatureIntegration,
)

logger = logging.getLogger(__name__)


class MockAuditBackend:
    """Mock audit backend for testing (non-blocking, stores events in memory)."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def write_event(self, **kwargs) -> bool:
        """Record audit event."""
        self.events.append(kwargs)
        return True


@pytest.fixture
def temp_corvin_home():
    """Temporary ~/.corvin directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def audit_backend():
    """Mock audit backend."""
    return MockAuditBackend()


@pytest.fixture
def validator(temp_corvin_home, audit_backend):
    """FeedbackSignatureValidator instance with temporary corvin_home."""
    return FeedbackSignatureValidator(corvin_home=temp_corvin_home, audit_backend=audit_backend)


@pytest.fixture
def integration(validator, audit_backend):
    """FeedbackSignatureIntegration instance."""
    return FeedbackSignatureIntegration(validator=validator, audit_backend=audit_backend)


class TestFeedbackSignatureCreation:
    """Test cryptographic signature creation."""

    def test_feedback_signature_valid(self, validator, audit_backend):
        """test_feedback_signature_valid: Sign feedback with tenant key, verify HMAC passes."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "outcome_feedback": "yes",
            "confidence": 0.95,
        }

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)

        # Verify signature was created
        assert error == "", f"Signature creation failed: {error}"
        assert signature is not None
        assert signature.feedback_id == feedback_payload["feedback_id"]
        assert signature.hmac  # Non-empty HMAC
        assert signature.algorithm == "hmac-sha256"
        assert signature.timestamp > 0

        # Verify audit event was logged
        audit_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_signature_created"]
        assert len(audit_events) >= 1

    def test_feedback_signature_creation_missing_feedback_id(self, validator, audit_backend):
        """Signature creation fails if feedback_id is missing (fail-closed)."""
        tenant_id = "_default"
        feedback_payload = {
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "outcome_feedback": "yes",
        }

        signature, error = validator.sign_feedback(tenant_id, feedback_payload)

        assert signature is None
        assert error != ""
        assert "feedback_id" in error.lower()

    def test_feedback_signature_creation_invalid_tenant(self, validator, audit_backend):
        """Signature creation fails with invalid tenant_id (fail-closed isolation)."""
        tenant_id = "invalid!!tenant"  # Invalid characters
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
        }

        signature, error = validator.sign_feedback(tenant_id, feedback_payload)

        assert signature is None
        assert error != ""
        assert "tenant_id" in error.lower()


class TestFeedbackSignatureVerification:
    """Test cryptographic signature verification (fail-closed)."""

    def test_feedback_signature_verification_valid(self, validator, audit_backend):
        """test_feedback_signature_valid: Create and verify signature, HMAC passes."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "outcome_feedback": "yes",
            "confidence": 0.95,
        }

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""
        assert signature is not None

        # Verify signature
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, signature)

        assert is_valid is True
        assert verify_error == ""

        # Verify audit event was logged
        audit_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_signature_verified"]
        assert len(audit_events) >= 1

    def test_feedback_signature_invalid_tampered_payload(self, validator, audit_backend):
        """test_feedback_signature_invalid: Tampered feedback payload rejected."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "outcome_feedback": "yes",
            "confidence": 0.95,
        }

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Tamper with payload (attacker modifies outcome_feedback after signature)
        tampered_payload = feedback_payload.copy()
        tampered_payload["outcome_feedback"] = "no"  # Changed after signing!

        # Verify tampered payload
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, tampered_payload, signature)

        assert is_valid is False
        assert "HMAC verification failed" in verify_error

        # Verify audit event was logged
        audit_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_signature_invalid"]
        assert len(audit_events) >= 1

    def test_feedback_signature_invalid_tampered_hmac(self, validator, audit_backend):
        """test_feedback_signature_invalid: Attacker tampers with HMAC hex string."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "tenant_id": tenant_id,
            "outcome_feedback": "yes",
        }

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Tamper with HMAC (flip one character in the hex string)
        tampered_hmac = signature.hmac[:-1] + ("0" if signature.hmac[-1] != "0" else "f")
        tampered_signature = FeedbackSignature(
            feedback_id=signature.feedback_id,
            tenant_key_hash=signature.tenant_key_hash,
            hmac=tampered_hmac,
            timestamp=signature.timestamp,
        )

        # Verify tampered signature
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, tampered_signature)

        assert is_valid is False
        assert "HMAC verification failed" in verify_error


class TestTenantIsolation:
    """Test tenant isolation (GDPR Art. 32)."""

    def test_feedback_signature_tenant_isolated(self, temp_corvin_home, audit_backend):
        """test_feedback_signature_tenant_isolated: Tenant A's key cannot verify Tenant B's feedback."""
        validator = FeedbackSignatureValidator(corvin_home=temp_corvin_home, audit_backend=audit_backend)

        tenant_a = "tenant_a"
        tenant_b = "tenant_b"

        feedback_payload_a = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "tenant_id": tenant_a,
            "outcome_feedback": "yes",
        }

        # Sign feedback with Tenant A's key
        signature_a, error = validator.sign_feedback(tenant_a, feedback_payload_a)
        assert error == ""
        assert signature_a is not None

        # Try to verify with Tenant B (should fail)
        feedback_payload_b = feedback_payload_a.copy()
        feedback_payload_b["tenant_id"] = tenant_b

        is_valid, verify_error = validator.verify_feedback_signature(tenant_b, feedback_payload_b, signature_a)

        # Tenant B's key should NOT verify feedback signed by Tenant A
        assert is_valid is False
        assert "tenant key hash mismatch" in verify_error.lower() or "HMAC verification failed" in verify_error

    def test_feedback_signature_invalid_tenant_id_mismatch(self, validator, audit_backend):
        """Signature verification fails if tenant_id doesn't match."""
        tenant_id_a = "tenant_a"
        tenant_id_b = "tenant_b"

        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "tenant_id": tenant_id_a,
            "outcome_feedback": "yes",
        }

        # Sign with Tenant A
        signature, error = validator.sign_feedback(tenant_id_a, feedback_payload)
        assert error == ""

        # Try to verify with Tenant B
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id_b, feedback_payload, signature)

        assert is_valid is False
        assert "tenant key hash mismatch" in verify_error.lower() or "HMAC verification failed" in verify_error


class TestReplayPrevention:
    """Test replay attack prevention via timestamp nonce."""

    def test_feedback_signature_replay_attack(self, validator, audit_backend):
        """test_feedback_signature_replay_attack: Same feedback signed twice has different timestamp."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "outcome_feedback": "yes",
        }

        # Sign feedback twice with a delay
        signature1, error1 = validator.sign_feedback(tenant_id, feedback_payload)
        assert error1 == ""

        time.sleep(0.1)  # Ensure different timestamp

        signature2, error2 = validator.sign_feedback(tenant_id, feedback_payload)
        assert error2 == ""

        # Timestamps should be different (nonce prevents replay)
        assert signature1.timestamp != signature2.timestamp

    def test_feedback_signature_old_timestamp_rejected(self, validator, audit_backend):
        """Signature with timestamp >24h old is rejected (replay prevention)."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "outcome_feedback": "yes",
        }

        # First sign with current timestamp to establish the key
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Create a signature with an old timestamp (48 hours ago) but keep the real key hash
        old_timestamp = int(datetime.now(timezone.utc).timestamp()) - (48 * 60 * 60)
        old_signature = FeedbackSignature(
            feedback_id=feedback_payload["feedback_id"],
            tenant_key_hash=signature.tenant_key_hash,  # Use real key hash
            hmac=signature.hmac,  # Use real HMAC (valid for old timestamp check)
            timestamp=old_timestamp,  # But use old timestamp
        )

        # Try to verify old signature
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, old_signature)

        # Should fail due to timestamp being too old
        assert is_valid is False
        assert "timestamp too old" in verify_error.lower() or "signature mismatch" in verify_error.lower()


class TestAuditLogging:
    """Test audit trail logging of signature events."""

    def test_feedback_signature_audit_logged_created(self, validator, audit_backend):
        """test_feedback_signature_audit_logged: Signature creation is audited."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "outcome_feedback": "yes",
        }

        # Clear previous events
        audit_backend.events.clear()

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Verify audit event was logged
        audit_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_signature_created"]
        assert len(audit_events) >= 1
        assert audit_events[0]["tenant_id"] == tenant_id

    def test_feedback_signature_audit_logged_verified(self, validator, audit_backend):
        """test_feedback_signature_audit_logged: Signature verification is audited."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "outcome_feedback": "yes",
        }

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Clear audit events after signing
        audit_backend.events.clear()

        # Verify signature
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, signature)
        assert is_valid is True

        # Verify audit event was logged
        audit_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_signature_verified"]
        assert len(audit_events) >= 1
        assert audit_events[0]["tenant_id"] == tenant_id

    def test_feedback_signature_audit_logged_invalid(self, validator, audit_backend):
        """test_feedback_signature_audit_logged: Invalid signature rejection is audited."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "outcome_feedback": "yes",
        }

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Tamper with payload
        tampered_payload = feedback_payload.copy()
        tampered_payload["outcome_feedback"] = "no"

        # Clear audit events
        audit_backend.events.clear()

        # Try to verify tampered signature
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, tampered_payload, signature)
        assert is_valid is False

        # Verify audit event was logged
        audit_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_signature_invalid"]
        assert len(audit_events) >= 1
        assert audit_events[0]["tenant_id"] == tenant_id


class TestKeyRotation:
    """test_feedback_signature_key_rotation: Old key signature rejected after rotation."""

    def test_feedback_signature_key_rotation(self, validator, audit_backend):
        """Signature with old key is rejected after key rotation."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "outcome_feedback": "yes",
        }

        # Sign feedback with original key
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""
        original_key_hash = signature.tenant_key_hash

        # Verify signature (should pass)
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, signature)
        assert is_valid is True

        # Rotate tenant key
        rotated, rotate_error = validator.crypto_binding.rotate_key(tenant_id)
        assert rotated is True

        # Try to verify old signature with new key (should fail due to key mismatch)
        # The key hash will be different after rotation
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, signature)
        assert is_valid is False
        # Error could be either tenant key hash mismatch (if checked before HMAC) or HMAC mismatch (if checked after)
        assert "tenant key hash mismatch" in verify_error.lower() or "signature mismatch" in verify_error.lower()


class TestIntegration:
    """Integration tests: FeedbackSignatureIntegration."""

    def test_create_signed_feedback_integration(self, integration, audit_backend):
        """Integration: create_signed_feedback creates and signs feedback."""
        tenant_id = "_default"
        feedback_payload = {
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "outcome_feedback": "yes",
        }

        # Create signed feedback
        result, error = integration.create_signed_feedback(tenant_id, feedback_payload)

        assert error == ""
        assert result is not None
        feedback_dict, signature = result
        assert "feedback_id" in feedback_dict
        assert signature.feedback_id == feedback_dict["feedback_id"]

    def test_verify_and_accept_feedback_integration(self, integration, audit_backend):
        """Integration: verify_and_accept_feedback verifies signature before optimizer."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "outcome_feedback": "yes",
        }

        # Sign feedback
        signature, error = integration.validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Verify and accept
        is_valid, verify_error = integration.verify_and_accept_feedback(tenant_id, feedback_payload, signature)

        assert is_valid is True
        assert verify_error == ""


class TestAttackScenarios:
    """Adversarial attack scenarios."""

    def test_feedback_signature_attack_payload_modification(self, validator, audit_backend):
        """test_feedback_signature_attack_payload_modification: Attacker modifies feedback data."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_123",
            "outcome_feedback": "no",  # User says "no"
            "confidence": 0.1,          # Low confidence
        }

        # Sign original feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Attacker modifies feedback to high-confidence "yes"
        attacked_payload = {
            "feedback_id": feedback_payload["feedback_id"],
            "skill_id": feedback_payload["skill_id"],
            "task_id": feedback_payload["task_id"],
            "outcome_feedback": "yes",  # Attacker changes to "yes"!
            "confidence": 0.99,          # Attacker increases confidence!
        }

        # Verification should fail (HMAC doesn't match modified payload)
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, attacked_payload, signature)

        assert is_valid is False
        assert "HMAC verification failed" in verify_error

    def test_feedback_signature_attack_hmac_tamper(self, validator, audit_backend):
        """test_feedback_signature_attack_hmac_tamper: Attacker modifies HMAC."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "outcome_feedback": "no",
        }

        # Sign feedback
        signature, error = validator.sign_feedback(tenant_id, feedback_payload)
        assert error == ""

        # Attacker modifies the HMAC hex string
        original_hmac = signature.hmac
        tampered_hmac = "0" * len(original_hmac)  # All zeros
        tampered_signature = FeedbackSignature(
            feedback_id=signature.feedback_id,
            tenant_key_hash=signature.tenant_key_hash,
            hmac=tampered_hmac,
            timestamp=signature.timestamp,
        )

        # Verification should fail
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, tampered_signature)

        assert is_valid is False
        assert "HMAC verification failed" in verify_error

    def test_feedback_signature_attack_cross_tenant_injection(self, temp_corvin_home, audit_backend):
        """test_feedback_signature_attack_cross_tenant_injection: Attacker injects Tenant B feedback into Tenant A."""
        validator = FeedbackSignatureValidator(corvin_home=temp_corvin_home, audit_backend=audit_backend)

        tenant_a = "tenant_a"
        tenant_b = "tenant_b"

        # Tenant B creates feedback
        feedback_payload_b = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "task_id": "task_xyz",
            "outcome_feedback": "yes",  # Good feedback
            "confidence": 0.95,
        }

        # Sign with Tenant B's key
        signature_b, error = validator.sign_feedback(tenant_b, feedback_payload_b)
        assert error == ""

        # Attacker tries to inject into Tenant A by changing tenant_id but keeping signature
        attacked_payload = feedback_payload_b.copy()
        attacked_payload["tenant_id"] = tenant_a  # Change tenant to A

        # Verification should fail (signature is bound to Tenant B's key)
        is_valid, verify_error = validator.verify_feedback_signature(tenant_a, attacked_payload, signature_b)

        assert is_valid is False
        assert "tenant key hash mismatch" in verify_error.lower() or "HMAC verification failed" in verify_error


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_feedback_signature_empty_feedback_payload(self, validator, audit_backend):
        """Signing empty payload fails (fail-closed)."""
        tenant_id = "_default"
        feedback_payload = {}

        signature, error = validator.sign_feedback(tenant_id, feedback_payload)

        assert signature is None
        assert error != ""

    def test_feedback_signature_very_large_payload(self, validator, audit_backend):
        """Large feedback payload is handled correctly (HMAC works on any size)."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "reason": "x" * 10000,  # Large reason field
        }

        signature, error = validator.sign_feedback(tenant_id, feedback_payload)

        assert error == ""
        assert signature is not None

        # Verify large payload
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, signature)
        assert is_valid is True

    def test_feedback_signature_special_characters_in_payload(self, validator, audit_backend):
        """Payload with special characters is canonicalized correctly."""
        tenant_id = "_default"
        feedback_payload = {
            "feedback_id": str(uuid4()),
            "skill_id": "os.delegation_router",
            "reason": "Special chars: 你好 🔒 ñ",  # Unicode
        }

        signature, error = validator.sign_feedback(tenant_id, feedback_payload)

        assert error == ""
        assert signature is not None

        # Verify signature
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, feedback_payload, signature)
        assert is_valid is True

    def test_feedback_signature_payload_key_order_invariant(self, validator, audit_backend):
        """Signature verifies regardless of key order (canonical JSON)."""
        tenant_id = "_default"
        feedback_id = str(uuid4())

        # Create payload with keys in one order
        payload_ordered_1 = {
            "feedback_id": feedback_id,
            "skill_id": "os.delegation_router",
            "outcome_feedback": "yes",
        }

        signature, error = validator.sign_feedback(tenant_id, payload_ordered_1)
        assert error == ""

        # Create payload with keys in different order but same content
        payload_ordered_2 = {
            "outcome_feedback": "yes",
            "feedback_id": feedback_id,
            "skill_id": "os.delegation_router",
        }

        # Verify with different key order (canonical JSON should handle)
        is_valid, verify_error = validator.verify_feedback_signature(tenant_id, payload_ordered_2, signature)
        assert is_valid is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
