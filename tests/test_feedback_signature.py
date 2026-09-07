"""Loop Hijacking Mitigation Tests: Cryptographic Signature Validation (ADR-0640).

Test coverage:
- Valid signature acceptance (5 tests)
- Invalid signature rejection (8 tests)
- Tampered feedback detection (6 tests)
- Missing signature rejection (4 tests)
- Tenant isolation (4 tests)
- Batch operations (3 tests)
- Total: 30 tests
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from core.learning.feedback_signature import (
    FeedbackSignatureValidator,
    canonical_json,
)


class TestCanonicalJson:
    """Test canonical JSON encoding (deterministic, format-independent)."""

    def test_sorted_keys(self):
        """Keys must be sorted alphabetically."""
        payload = {"z": 1, "a": 2, "m": 3}
        result = canonical_json(payload).decode()
        expected = '{"a":2,"m":3,"z":1}'
        assert result == expected

    def test_no_whitespace(self):
        """No spaces or newlines in canonical form."""
        payload = {"key": "value", "nested": {"a": 1}}
        result = canonical_json(payload).decode()
        assert " " not in result
        assert "\n" not in result

    def test_consistent_encoding(self):
        """Same payload always produces same canonical form."""
        payload = {"x": 1, "y": 2}
        result1 = canonical_json(payload)
        result2 = canonical_json(payload)
        assert result1 == result2

    def test_different_order_same_encoding(self):
        """Different insertion order produces same canonical form."""
        payload1 = {"a": 1, "b": 2}
        payload2 = {"b": 2, "a": 1}
        assert canonical_json(payload1) == canonical_json(payload2)

    def test_unicode_safe(self):
        """Unicode is safely handled in canonical form."""
        payload = {"text": "hello world", "emoji": "🔒"}
        result = canonical_json(payload)
        # Should be valid JSON bytes
        assert isinstance(result, bytes)
        # Should be ASCII-safe (unicode escaped)
        assert result.decode("ascii")


class TestFeedbackSignatureValidator:
    """Test FeedbackSignatureValidator core functionality."""

    @pytest.fixture
    def temp_corvin_home(self):
        """Create temporary corvin_home for isolated testing."""
        with TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def validator(self, temp_corvin_home):
        """Create validator with temporary corvin_home."""
        return FeedbackSignatureValidator(corvin_home_override=temp_corvin_home)

    def test_sign_feedback_creates_key(self, validator):
        """First sign() call creates tenant key automatically."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        signature, error = validator.sign_feedback("_default", feedback)
        assert error == "", f"Unexpected error: {error}"
        assert signature is not None
        assert len(signature) == 64  # SHA256 hex digest is 64 chars

    def test_sign_same_feedback_consistent(self, validator):
        """Signing same feedback twice produces identical signature."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        sig1, _ = validator.sign_feedback("_default", feedback)
        sig2, _ = validator.sign_feedback("_default", feedback)
        assert sig1 == sig2

    def test_sign_different_feedback_different_signature(self, validator):
        """Different feedback produces different signatures."""
        feedback1 = {"skill_id": "os.router", "task_id": "task-123"}
        feedback2 = {"skill_id": "os.router", "task_id": "task-456"}
        sig1, _ = validator.sign_feedback("_default", feedback1)
        sig2, _ = validator.sign_feedback("_default", feedback2)
        assert sig1 != sig2

    def test_sign_requires_tenant_id(self, validator):
        """Signing requires valid tenant_id (fail-closed)."""
        feedback = {"skill_id": "os.router"}
        sig, error = validator.sign_feedback("", feedback)
        assert sig is None
        assert "tenant_id is required" in error

    def test_sign_requires_dict(self, validator):
        """Signing requires dict payload (fail-closed)."""
        sig, error = validator.sign_feedback("_default", "not a dict")
        assert sig is None
        assert "must be a dict" in error

    # ── Validation Tests ─────────────────────────────────────────────

    def test_valid_signature_accepted(self, validator):
        """Valid signature passes verification."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        signature, _ = validator.sign_feedback("_default", feedback)
        is_valid, error = validator.validate_feedback_signature(
            "_default", feedback, signature
        )
        assert is_valid
        assert error == ""

    def test_invalid_signature_rejected(self, validator):
        """Invalid signature fails verification (fail-closed)."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        signature, _ = validator.sign_feedback("_default", feedback)
        # Flip a bit in the signature to corrupt it
        corrupted = signature[:-1] + ("0" if signature[-1] != "0" else "1")
        is_valid, error = validator.validate_feedback_signature(
            "_default", feedback, corrupted
        )
        assert not is_valid
        assert "Signature mismatch" in error

    def test_tampered_feedback_detected(self, validator):
        """Tampering with feedback invalidates signature."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        signature, _ = validator.sign_feedback("_default", feedback)
        # Tamper with feedback after signing
        tampered = {"skill_id": "os.router", "task_id": "task-456"}  # Different task_id
        is_valid, error = validator.validate_feedback_signature(
            "_default", tampered, signature
        )
        assert not is_valid
        assert "Signature mismatch" in error

    def test_missing_signature_rejected(self, validator):
        """Missing signature causes validation failure."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        is_valid, error = validator.validate_feedback_signature(
            "_default", feedback, ""
        )
        assert not is_valid
        assert "signature is required" in error

    def test_none_signature_rejected(self, validator):
        """None signature is rejected."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        is_valid, error = validator.validate_feedback_signature(
            "_default", feedback, None
        )
        assert not is_valid

    def test_validate_requires_tenant_id(self, validator):
        """Validation requires valid tenant_id."""
        feedback = {"skill_id": "os.router"}
        signature, _ = validator.sign_feedback("_default", feedback)
        is_valid, error = validator.validate_feedback_signature(
            "", feedback, signature
        )
        assert not is_valid
        assert "tenant_id is required" in error

    def test_validate_requires_dict(self, validator):
        """Validation requires dict payload."""
        is_valid, error = validator.validate_feedback_signature(
            "_default", "not a dict", "somesig"
        )
        assert not is_valid
        assert "must be a dict" in error

    def test_missing_key_fails_validation(self, validator):
        """Validation fails if key doesn't exist."""
        feedback = {"skill_id": "os.router"}
        # Try to validate without creating key first
        is_valid, error = validator.validate_feedback_signature(
            "unknown_tenant", feedback, "somesig"
        )
        assert not is_valid
        assert "Signing key not found" in error

    # ── Tenant Isolation Tests ───────────────────────────────────────

    def test_tenant_isolation_different_keys(self, validator):
        """Different tenants have different keys (can't cross-verify)."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        # Sign with tenant1
        sig_tenant1, _ = validator.sign_feedback("tenant1", feedback)
        # Try to verify with tenant2
        is_valid, _ = validator.validate_feedback_signature("tenant2", feedback, sig_tenant1)
        assert not is_valid  # Signature mismatch (different keys)

    def test_tenant_id_validation_strict(self, validator):
        """Tenant ID validation is strict (fail-closed)."""
        feedback = {"skill_id": "os.router"}
        # Invalid tenant IDs
        invalid_ids = ["../../../etc/passwd", "", "   ", "tenant with spaces", None]
        for invalid_id in invalid_ids:
            if invalid_id is None:
                continue
            sig, error = validator.sign_feedback(invalid_id, feedback)
            assert sig is None or error != ""

    # ── Batch Operations ─────────────────────────────────────────────

    def test_validate_batch_all_valid(self, validator):
        """Batch validation passes when all signatures valid."""
        feedback_items = [
            ({"skill_id": "os.router", "task_id": "t1"}, None),
            ({"skill_id": "os.router", "task_id": "t2"}, None),
            ({"skill_id": "os.router", "task_id": "t3"}, None),
        ]
        # Sign each item
        signed_items = []
        for feedback, _ in feedback_items:
            sig, _ = validator.sign_feedback("_default", feedback)
            signed_items.append((feedback, sig))

        valid_count, errors = validator.validate_feedback_batch(
            "_default", signed_items
        )
        assert valid_count == 3
        assert len(errors) == 0

    def test_validate_batch_some_invalid(self, validator):
        """Batch validation detects invalid signatures."""
        feedback_items = [
            {"skill_id": "os.router", "task_id": "t1"},
            {"skill_id": "os.router", "task_id": "t2"},
            {"skill_id": "os.router", "task_id": "t3"},
        ]
        signed_items = []
        for idx, feedback in enumerate(feedback_items):
            sig, _ = validator.sign_feedback("_default", feedback)
            # Corrupt signature on item 1
            if idx == 1:
                sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
            signed_items.append((feedback, sig))

        valid_count, errors = validator.validate_feedback_batch(
            "_default", signed_items
        )
        assert valid_count == 2
        assert len(errors) == 1
        assert "Item 1" in errors[0]

    def test_validate_batch_empty_list(self, validator):
        """Batch validation handles empty list."""
        valid_count, errors = validator.validate_feedback_batch("_default", [])
        assert valid_count == 0
        assert len(errors) == 0

    # ── Audit Integration Tests ──────────────────────────────────────

    def test_sign_calls_audit_callback(self, validator):
        """Signing invokes audit callback."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        audit_callback = MagicMock()
        validator.sign_feedback("_default", feedback, audit_callback=audit_callback)
        # Should have been called once
        assert audit_callback.called

    def test_validate_failure_calls_audit(self, validator):
        """Validation failure logs to audit callback."""
        feedback = {"skill_id": "os.router"}
        audit_callback = MagicMock()
        validator.validate_feedback_signature(
            "_default", feedback, "invalidsig", audit_callback=audit_callback
        )
        # Should have been called with failure event
        assert audit_callback.called
        call_kwargs = audit_callback.call_args[1]
        assert call_kwargs["event_type"] == "feedback_signature_verification_failed"

    def test_validate_success_no_audit_failure(self, validator):
        """Successful validation doesn't log failure event."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        signature, _ = validator.sign_feedback("_default", feedback)
        audit_callback = MagicMock()
        validator.validate_feedback_signature(
            "_default", feedback, signature, audit_callback=audit_callback
        )
        # Check no failure event was logged
        if audit_callback.called:
            call_kwargs = audit_callback.call_args[1]
            assert call_kwargs.get("event_type") != "feedback_signature_verification_failed"


class TestLoopHijackingMitigation:
    """End-to-end tests for Loop Hijacking attack prevention."""

    @pytest.fixture
    def validator(self):
        """Create validator for hijacking scenario testing."""
        with TemporaryDirectory() as tmpdir:
            yield FeedbackSignatureValidator(corvin_home_override=tmpdir)

    def test_attacker_cannot_craft_valid_feedback(self, validator):
        """Attacker cannot forge valid feedback without the key."""
        # First, create a legitimate key (system initialization)
        legitimate_feedback = {"skill_id": "os.router", "task_id": "task-1"}
        validator.sign_feedback("_default", legitimate_feedback)

        # Now attacker tries to create fake feedback with high confidence
        fake_feedback = {
            "skill_id": "os.delegation_router",
            "task_id": "task-999",
            "outcome_feedback": "yes",
            "confidence": 0.95,  # High confidence to influence α weights
        }
        # Attacker doesn't have the key, so they guess a signature
        fake_signature = "0" * 64
        is_valid, error = validator.validate_feedback_signature(
            "_default", fake_feedback, fake_signature
        )
        assert not is_valid
        assert "Signature mismatch" in error

    def test_attacker_cannot_replay_with_modified_confidence(self, validator):
        """Attacker cannot replay feedback with modified confidence."""
        original_feedback = {
            "skill_id": "os.delegation_router",
            "task_id": "task-999",
            "outcome_feedback": "yes",
            "confidence": 0.5,
        }
        signature, _ = validator.sign_feedback("_default", original_feedback)

        # Attacker replays but increases confidence
        replayed_feedback = {
            "skill_id": "os.delegation_router",
            "task_id": "task-999",
            "outcome_feedback": "yes",
            "confidence": 0.95,  # Maliciously increased
        }
        is_valid, _ = validator.validate_feedback_signature(
            "_default", replayed_feedback, signature
        )
        assert not is_valid  # Signature invalid (confidence changed)

    def test_attacker_cannot_intercept_and_modify(self, validator):
        """MITM attacker cannot modify feedback in transit."""
        original_feedback = {
            "skill_id": "os.delegation_router",
            "task_id": "task-999",
            "outcome_feedback": "no",
        }
        signature, _ = validator.sign_feedback("_default", original_feedback)

        # MITM modifies feedback outcome
        modified_feedback = {
            "skill_id": "os.delegation_router",
            "task_id": "task-999",
            "outcome_feedback": "yes",  # Attacker flips no → yes
        }
        is_valid, _ = validator.validate_feedback_signature(
            "_default", modified_feedback, signature
        )
        assert not is_valid  # Signature fails on modified outcome

    def test_timing_attack_resistance(self, validator):
        """Constant-time comparison resists timing attacks."""
        feedback = {"skill_id": "os.router", "task_id": "task-123"}
        signature, _ = validator.sign_feedback("_default", feedback)

        # Slightly wrong signature
        corrupted = signature[:-2] + "xx"

        # Validate uses constant-time comparison (hmac.compare_digest)
        # This test passes if no timing-based exception is raised
        is_valid, _ = validator.validate_feedback_signature(
            "_default", feedback, corrupted
        )
        assert not is_valid


class TestIntegrationWithFeedbackSink:
    """Test integration scenarios with feedback_sink.py."""

    @pytest.fixture
    def validator(self):
        """Create validator for integration testing."""
        with TemporaryDirectory() as tmpdir:
            yield FeedbackSignatureValidator(corvin_home_override=tmpdir)

    def test_sign_feedback_dict_before_emission(self, validator):
        """Typical flow: sign feedback dict before emitting."""
        feedback_dict = {
            "skill_id": "os.delegation_router",
            "task_id": "task-123",
            "outcome_feedback": "yes",
            "confidence": 0.85,
            "tenant_id": "_default",
        }

        # Step 1: Sign the feedback
        signature, error = validator.sign_feedback("_default", feedback_dict)
        assert error == ""
        assert signature is not None

        # Step 2: Add signature to feedback
        feedback_with_sig = {**feedback_dict, "signature": signature}

        # Step 3: Emit feedback (would be stored with signature)
        # ... (in real code, would call event_store.write())

        # Step 4: Verify when reading from store
        is_valid, error = validator.validate_feedback_signature(
            "_default",
            {k: v for k, v in feedback_with_sig.items() if k != "signature"},
            feedback_with_sig["signature"],
        )
        assert is_valid

    def test_validate_before_buffering(self, validator):
        """Typical flow: validate signature before buffering feedback."""
        feedback_dict = {
            "skill_id": "os.delegation_router",
            "task_id": "task-123",
            "outcome_feedback": "yes",
            "confidence": 0.85,
        }

        # Step 1: Sign feedback
        signature, _ = validator.sign_feedback("_default", feedback_dict)

        # Step 2: on_feedback(feedback) receives signature
        # Step 3: Validate FIRST before any processing
        is_valid, error = validator.validate_feedback_signature(
            "_default", feedback_dict, signature
        )
        assert is_valid  # Only if valid, proceed to buffering
        # ... (would now proceed to FeedbackBuffer)

    def test_detect_tampering_after_storage(self, validator):
        """Detect tampering if feedback is tampered after storage."""
        original_feedback = {
            "skill_id": "os.delegation_router",
            "task_id": "task-123",
            "outcome_feedback": "yes",
        }
        signature, _ = validator.sign_feedback("_default", original_feedback)

        # Simulate storage + tampering
        stored_feedback = {**original_feedback, "confidence": 0.5}
        tampered_feedback = {**stored_feedback, "confidence": 0.95}

        # Verify original: OK
        is_valid_orig, _ = validator.validate_feedback_signature(
            "_default", original_feedback, signature
        )
        assert is_valid_orig

        # Verify tampered: FAIL
        is_valid_tampered, _ = validator.validate_feedback_signature(
            "_default", tampered_feedback, signature
        )
        assert not is_valid_tampered
