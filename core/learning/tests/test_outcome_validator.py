"""Tests for outcome_validator.py — Weight Poisoning Mitigation (Security Fix #3).

Test Coverage:
  - Source whitelist validation
  - Audit ref verification
  - Timestamp validation
  - Comprehensive validation pipeline
  - Filtered outcome processing
  - Attack vector prevention
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from core.learning.outcome_validator import (
    verify_outcome_source,
    verify_audit_ref,
    verify_outcome_timestamp,
    validate_outcome_comprehensive,
    compute_outcome_hash,
    filter_verified_outcomes,
    OutcomeValidationResult,
    OutcomeValidationError,
    AuditRefNotFoundError,
    OutcomeSourceUnverifiedError,
    ChainIntegrityError,
    TRUSTED_OUTCOME_SOURCES,
)


class TestSourceWhitelistValidation:
    """Test source whitelist gate (Gate #1)."""

    def test_valid_source_task_manager(self):
        """Outcome from task_manager is accepted."""
        signal = {
            "source": "task_manager",
            "task_id": "task-123",
            "status": "completed",
        }
        result = verify_outcome_source(signal)
        assert result.is_valid is True
        assert result.source == "task_manager"
        assert result.verification_method == "source_whitelist"

    def test_valid_source_feedback_loop(self):
        """Outcome from feedback_loop is accepted."""
        signal = {
            "source": "feedback_loop",
            "task_id": "task-123",
            "feedback_rating": 4,
        }
        result = verify_outcome_source(signal)
        assert result.is_valid is True
        assert result.source == "feedback_loop"

    def test_valid_source_audit_backend_outcome(self):
        """Outcome from audit_backend_outcome is accepted."""
        signal = {
            "source": "audit_backend_outcome",
            "task_id": "task-123",
            "audit_ref": "ref-abc",
        }
        result = verify_outcome_source(signal)
        assert result.is_valid is True
        assert result.source == "audit_backend_outcome"

    def test_invalid_source_external_api(self):
        """Outcome from external API is rejected (attack vector #1)."""
        signal = {
            "source": "external_api",
            "task_id": "task-123",
            "result": "success",
        }
        result = verify_outcome_source(signal)
        assert result.is_valid is False
        assert "external_api" in result.error_reason
        assert "not in TRUSTED_OUTCOME_SOURCES" in result.error_reason

    def test_invalid_source_skill_config(self):
        """Outcome from unverified skill config is rejected (attack vector #2)."""
        signal = {
            "source": "skill_config",
            "task_id": "task-123",
            "injected_outcome": "success",
        }
        result = verify_outcome_source(signal)
        assert result.is_valid is False
        assert result.source == "skill_config"

    def test_missing_source_field(self):
        """Outcome without source field is rejected."""
        signal = {"task_id": "task-123"}
        result = verify_outcome_source(signal)
        assert result.is_valid is False
        assert result.source == "unknown"

    def test_empty_source_field(self):
        """Outcome with empty source is rejected."""
        signal = {"source": "", "task_id": "task-123"}
        result = verify_outcome_source(signal)
        assert result.is_valid is False


class TestAuditRefVerification:
    """Test audit ref verification gate (Gate #2)."""

    def test_valid_audit_ref_present(self):
        """Outcome with audit_ref is accepted."""
        signal = {
            "source": "task_manager",
            "audit_ref": "550e8400-e29b-41d4-a716-446655440000",
            "task_id": "task-123",
        }
        result = verify_audit_ref(signal)
        assert result.is_valid is True
        assert result.audit_ref == "550e8400-e29b-41d4-a716-446655440000"
        assert result.verification_method == "chain_verification"

    def test_missing_audit_ref(self):
        """Outcome without audit_ref is rejected (attack vector #3)."""
        signal = {
            "source": "task_manager",
            "task_id": "task-123",
        }
        result = verify_audit_ref(signal)
        assert result.is_valid is False
        assert "missing audit_ref" in result.error_reason

    def test_audit_ref_verification_with_store_found(self):
        """Audit ref is verified against chain when store provided (found case)."""
        signal = {
            "source": "task_manager",
            "audit_ref": "ref-123",
            "task_id": "task-456",
        }
        # Mock store with chain lookup
        store = Mock()
        store.query_chain_by_ref = Mock(return_value={"event_id": "ref-123", "chain_link": "valid"})

        result = verify_audit_ref(signal, store=store)
        assert result.is_valid is True
        store.query_chain_by_ref.assert_called_once_with("ref-123")

    def test_audit_ref_verification_with_store_not_found(self):
        """Audit ref verification fails when chain lookup returns None."""
        signal = {
            "source": "task_manager",
            "audit_ref": "ref-fake",
            "task_id": "task-456",
        }
        # Mock store with chain lookup that fails
        store = Mock()
        store.query_chain_by_ref = Mock(return_value=None)

        result = verify_audit_ref(signal, store=store)
        assert result.is_valid is False
        assert "not found in chain" in result.error_reason

    def test_audit_ref_verification_store_exception(self):
        """Chain lookup exception doesn't block if audit_ref format is valid."""
        signal = {
            "source": "task_manager",
            "audit_ref": "ref-123",
            "task_id": "task-456",
        }
        # Mock store that raises exception
        store = Mock()
        store.query_chain_by_ref = Mock(side_effect=RuntimeError("chain unavailable"))

        result = verify_audit_ref(signal, store=store)
        # Should still be valid (non-fatal error)
        assert result.is_valid is True


class TestTimestampValidation:
    """Test timestamp validation gate (Gate #3)."""

    def test_current_timestamp_valid(self):
        """Outcome with current timestamp is accepted."""
        signal = {
            "source": "task_manager",
            "timestamp": time.time(),
            "task_id": "task-123",
        }
        result = verify_outcome_timestamp(signal)
        assert result.is_valid is True

    def test_recent_timestamp_valid(self):
        """Outcome from 1 hour ago is accepted."""
        signal = {
            "source": "task_manager",
            "timestamp": time.time() - 3600,  # 1 hour ago
            "task_id": "task-123",
        }
        result = verify_outcome_timestamp(signal)
        assert result.is_valid is True

    def test_ancient_timestamp_rejected(self):
        """Outcome from >24 hours ago is rejected (attack vector #4)."""
        signal = {
            "source": "task_manager",
            "timestamp": time.time() - 86400 - 1,  # >24h ago
            "task_id": "task-123",
        }
        result = verify_outcome_timestamp(signal)
        assert result.is_valid is False
        assert "too old" in result.error_reason

    def test_future_timestamp_rejected(self):
        """Outcome from future is rejected (attack vector #4)."""
        signal = {
            "source": "task_manager",
            "timestamp": time.time() + 600,  # 10 minutes in future
            "task_id": "task-123",
        }
        result = verify_outcome_timestamp(signal)
        assert result.is_valid is False
        assert "future" in result.error_reason

    def test_missing_timestamp_uses_current(self):
        """Outcome without timestamp uses current time (acceptable)."""
        signal = {
            "source": "task_manager",
            "task_id": "task-123",
        }
        result = verify_outcome_timestamp(signal)
        # Should be valid (uses current time as default)
        assert result.is_valid is True


class TestComprehensiveValidation:
    """Test comprehensive validation pipeline."""

    def test_valid_outcome_all_gates(self):
        """Outcome that passes all three gates is accepted."""
        signal = {
            "source": "task_manager",
            "audit_ref": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": time.time(),
            "task_id": "task-123",
            "status": "completed",
            "exit_code": 0,
        }
        result = validate_outcome_comprehensive(signal, task_id="task-123")
        assert result.is_valid is True

    def test_invalid_source_comprehensive(self):
        """Comprehensive validation fails on invalid source."""
        signal = {
            "source": "external_api",
            "audit_ref": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": time.time(),
            "task_id": "task-123",
        }
        result = validate_outcome_comprehensive(signal, task_id="task-123")
        assert result.is_valid is False
        assert "external_api" in result.error_reason

    def test_invalid_audit_ref_comprehensive(self):
        """Comprehensive validation fails on missing audit_ref."""
        signal = {
            "source": "task_manager",
            "timestamp": time.time(),
            "task_id": "task-123",
        }
        result = validate_outcome_comprehensive(signal, task_id="task-123")
        assert result.is_valid is False
        assert "audit_ref" in result.error_reason

    def test_invalid_timestamp_comprehensive(self):
        """Comprehensive validation fails on invalid timestamp."""
        signal = {
            "source": "task_manager",
            "audit_ref": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": time.time() + 1000,  # Future
            "task_id": "task-123",
        }
        result = validate_outcome_comprehensive(signal, task_id="task-123")
        assert result.is_valid is False
        assert "future" in result.error_reason


class TestOutcomeHash:
    """Test outcome hash computation for tamper detection."""

    def test_hash_computation(self):
        """Hash is computed consistently for same signal."""
        signal = {
            "source": "task_manager",
            "task_id": "task-123",
            "status": "completed",
        }
        hash1 = compute_outcome_hash(signal)
        hash2 = compute_outcome_hash(signal)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length

    def test_hash_changes_with_modification(self):
        """Hash changes if signal is modified."""
        signal = {
            "source": "task_manager",
            "task_id": "task-123",
            "status": "completed",
        }
        hash1 = compute_outcome_hash(signal)
        signal["status"] = "failed"
        hash2 = compute_outcome_hash(signal)
        assert hash1 != hash2

    def test_hash_ignores_verification_fields(self):
        """Hash doesn't include verification fields (excluded from hash)."""
        signal1 = {
            "source": "task_manager",
            "task_id": "task-123",
            "status": "completed",
            "outcome_source_verified": True,
        }
        signal2 = {
            "source": "task_manager",
            "task_id": "task-123",
            "status": "completed",
            "outcome_source_verified": False,
        }
        # Should have same hash (verification field excluded)
        hash1 = compute_outcome_hash(signal1)
        hash2 = compute_outcome_hash(signal2)
        assert hash1 == hash2


class TestFilteredOutcomes:
    """Test outcome filtering for backprop."""

    def test_filter_all_valid_outcomes(self):
        """All verified outcomes are included in filtered list."""
        outcomes = [
            {
                "source": "task_manager",
                "audit_ref": "ref-1",
                "task_id": "task-1",
            },
            {
                "source": "feedback_loop",
                "audit_ref": "ref-2",
                "task_id": "task-2",
            },
            {
                "source": "task_manager",
                "audit_ref": "ref-3",
                "task_id": "task-3",
            },
        ]
        verified, unverified = filter_verified_outcomes(outcomes)
        assert len(verified) == 3
        assert len(unverified) == 0

    def test_filter_rejects_invalid_outcomes(self):
        """Invalid outcomes are excluded and tracked."""
        outcomes = [
            {
                "source": "task_manager",
                "audit_ref": "ref-1",
                "task_id": "task-1",
            },
            {
                "source": "external_api",  # Invalid source
                "audit_ref": "ref-2",
                "task_id": "task-2",
            },
            {
                "source": "skill_config",  # Invalid source
                "task_id": "task-3",
            },
        ]
        verified, unverified = filter_verified_outcomes(outcomes)
        assert len(verified) == 1
        assert len(unverified) == 2
        assert "task-2" in unverified
        assert "task-3" in unverified

    def test_filter_with_store_verification(self):
        """Store is passed through for chain verification."""
        outcomes = [
            {
                "source": "task_manager",
                "audit_ref": "ref-1",
                "task_id": "task-1",
            },
        ]
        store = Mock()
        store.query_chain_by_ref = Mock(return_value={"valid": True})

        verified, unverified = filter_verified_outcomes(outcomes, store=store)
        assert len(verified) == 1

    def test_filter_mixed_outcomes(self):
        """Mix of valid and invalid outcomes are properly separated."""
        outcomes = [
            {
                "source": "task_manager",
                "audit_ref": "ref-1",
                "task_id": "task-1",
                "status": "completed",
            },
            {
                "source": "external_malicious_api",
                "audit_ref": "ref-fake",
                "task_id": "task-2",
                "status": "success",
            },
            {
                "source": "feedback_loop",
                "audit_ref": "ref-3",
                "task_id": "task-3",
                "feedback_rating": 5,
            },
        ]
        verified, unverified = filter_verified_outcomes(outcomes)
        assert len(verified) == 2
        assert len(unverified) == 1
        # Verify only legitimate outcomes are included
        assert any(o.get("task_id") == "task-1" for o in verified)
        assert any(o.get("task_id") == "task-3" for o in verified)


class TestAttackVectorPrevention:
    """Test prevention of specific weight poisoning attack vectors."""

    def test_prevent_external_api_injection(self):
        """Attack vector #1: External API outcome injection is blocked."""
        # Attacker tries to inject outcome from untrusted API
        malicious_signal = {
            "source": "external_api",
            "audit_ref": "forged-ref",
            "task_id": "task-123",
            "outcome": "optimized_success",  # Fake success
        }
        result = validate_outcome_comprehensive(malicious_signal)
        assert result.is_valid is False

    def test_prevent_skill_config_spoofing(self):
        """Attack vector #2: Skill config outcome spoofing is blocked."""
        # Attacker tries to spoof outcome from skill config
        malicious_signal = {
            "source": "skill_config",
            "config_param": "override_outcome_success",
            "task_id": "task-123",
        }
        result = verify_outcome_source(malicious_signal)
        assert result.is_valid is False

    def test_prevent_direct_signal_tampering(self):
        """Attack vector #3: Direct outcome signal tampering is detected."""
        # Original outcome
        original = {
            "source": "task_manager",
            "audit_ref": "ref-123",
            "task_id": "task-123",
            "status": "completed",
            "exit_code": 0,
        }
        original_hash = compute_outcome_hash(original)

        # Attacker modifies the signal
        tampered = original.copy()
        tampered["exit_code"] = 1  # Change from success to failure
        tampered_hash = compute_outcome_hash(tampered)

        # Hashes differ (tampering detected)
        assert original_hash != tampered_hash

    def test_prevent_feedback_hijacking(self):
        """Attack vector #4: Feedback loop hijacking is blocked."""
        # Attacker tries to inject feedback from unverified source
        malicious_signal = {
            "source": "hijacked_feedback_api",
            "task_id": "task-123",
            "feedback": "artificially_positive",
        }
        result = verify_outcome_source(malicious_signal)
        assert result.is_valid is False

    def test_prevent_silent_substitution(self):
        """Attack vector #5: Silent outcome substitution is audited."""
        # Attacker tries to substitute outcome without audit trail
        signal = {
            "source": "task_manager",
            "audit_ref": "ref-123",
            "task_id": "task-123",
            "status": "completed",
        }
        # When validated, should log to audit chain
        with patch("core.learning.outcome_validator._audit_outcome_validation") as mock_audit:
            validate_outcome_comprehensive(signal, task_id="task-123", tenant_id="tenant-1")
            # Audit was called
            mock_audit.assert_called_once()


class TestValidationResultDataStructure:
    """Test OutcomeValidationResult dataclass."""

    def test_result_to_dict(self):
        """Result can be converted to dict for audit logging."""
        result = OutcomeValidationResult(
            is_valid=True,
            source="task_manager",
            audit_ref="ref-123",
            verification_method="source_whitelist",
            verified_at=time.time(),
        )
        result_dict = result.to_dict()
        assert result_dict["is_valid"] is True
        assert result_dict["source"] == "task_manager"
        assert result_dict["verification_method"] == "source_whitelist"

    def test_result_with_error(self):
        """Result captures error reasons."""
        result = OutcomeValidationResult(
            is_valid=False,
            source="external_api",
            error_reason="untrusted source",
            verification_method="source_whitelist",
        )
        assert result.is_valid is False
        assert result.error_reason == "untrusted source"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
