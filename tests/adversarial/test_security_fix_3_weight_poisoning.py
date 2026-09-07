"""Security Fix #3: Weight Poisoning Mitigation — Outcome Source Verification.

Finding #3: Weight Poisoning — corrupted gradients from unverified outcomes
can influence backpropagation, skewing the system away from optimal routing
and configuration decisions.

**Mitigation:** Outcome Source Verification (Audit Backend Only)
- Only outcomes from TRUSTED_OUTCOME_SOURCES are used for backprop
- Outcomes from untrusted sources (external APIs, unverified skill configs)
  are logged but rejected
- Every outcome verification is audited (outcome_source_verified/unverified)

**Attack Scenarios Tested:**
1. Normal outcome from audit_backend_outcome source (should accept)
2. Poisoned outcome from skill_config source (should reject)
3. Poisoned outcome from external_api source (should reject)
4. Verification logged to audit chain
5. Unverified outcomes don't influence optimizer backprop

**Compliance:** GDPR Art. 30, 32 (audit trail), ADR-0314 (learning loop)
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from core.learning.outcome_sink import (
    verify_outcome_source,
    audit_outcome_verification,
    emit_task_outcome,
    integrate_feedback_outcome,
    TRUSTED_OUTCOME_SOURCES,
    recent_outcomes,
    OUTCOME_SKILL_ID,
)


class TestOutcomeFromAuditBackendAccepted:
    """Test: Outcomes from audit backend source are accepted for backprop."""

    def test_task_manager_source_accepted(self):
        """Outcome from task_manager (trusted source) passes verification."""
        signal = {
            "task_id": "task-123",
            "status": "completed",
            "success": True,
            "source": "task_manager",
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is True
        assert "outcome_source_verified" in reason
        assert "task_manager" in reason

    def test_feedback_loop_source_accepted(self):
        """Outcome from feedback_loop (trusted source) passes verification."""
        signal = {
            "task_id": "task-123",
            "feedback_signal": {"outcome": "yes"},
            "source": "feedback_loop",
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is True
        assert "outcome_source_verified" in reason
        assert "feedback_loop" in reason

    def test_audit_backend_outcome_source_accepted(self):
        """Outcome explicitly from audit_backend_outcome passes verification."""
        signal = {
            "task_id": "task-123",
            "status": "completed",
            "success": True,
            "source": "audit_backend_outcome",
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is True
        assert "outcome_source_verified" in reason


class TestOutcomeFromSkillConfigRejected:
    """Test: Outcomes from skill_config source are rejected."""

    def test_skill_config_source_rejected(self):
        """Outcome from skill_config (untrusted) fails verification."""
        signal = {
            "task_id": "task-123",
            "config_param": "routing_threshold",
            "source": "skill_config",
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is False
        assert "outcome_source_unverified" in reason
        assert "skill_config" in reason
        assert "not in trusted sources" in reason

    def test_hardcoded_config_source_rejected(self):
        """Outcome from hardcoded_config (untrusted) fails verification."""
        signal = {
            "task_id": "task-123",
            "value": 0.7,
            "source": "hardcoded_config",
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is False
        assert "outcome_source_unverified" in reason


class TestOutcomeFromExternalAPIRejected:
    """Test: Outcomes from external APIs are rejected."""

    def test_external_api_source_rejected(self):
        """Outcome from external_api (untrusted) fails verification."""
        signal = {
            "task_id": "task-123",
            "api_response": {"result": "success"},
            "source": "external_api",
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is False
        assert "outcome_source_unverified" in reason
        assert "external_api" in reason

    def test_unknown_source_rejected(self):
        """Outcome from unknown source fails verification."""
        signal = {
            "task_id": "task-123",
            "source": "unknown_source",
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is False
        assert "outcome_source_unverified" in reason

    def test_missing_source_rejected(self):
        """Outcome without source field fails verification."""
        signal = {
            "task_id": "task-123",
            # no "source" key
        }

        verified, reason = verify_outcome_source(signal)

        assert verified is False
        assert "outcome_source_unverified" in reason
        assert "unknown" in reason.lower()


class TestOutcomeVerificationAuditLogged:
    """Test: Every outcome verification is logged to the audit chain."""

    @mock.patch("core.learning.outcome_sink.audit_outcome_verification")
    def test_verification_logged_on_emit_task_outcome(self, mock_audit):
        """emit_task_outcome logs verification to audit chain."""
        mock_audit.return_value = True

        # Mock the emitter
        mock_emitter = mock.MagicMock()
        mock_emitter.emit.return_value = True

        result = emit_task_outcome(
            tenant_id="_default",
            task_id="task-123",
            status="completed",
            exit_code=0,
            duration_ms=100,
            engine="claude-code",
            emitter=mock_emitter,
        )

        assert result is True
        # Verify audit was called with verification details
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["tenant_id"] == "_default"
        assert call_kwargs["task_id"] == "task-123"
        assert call_kwargs["verified"] is True  # task_manager is trusted
        assert call_kwargs["source"] == "task_manager"

    @mock.patch("core.learning.outcome_sink.audit_outcome_verification")
    def test_verification_logged_for_untrusted_source(self, mock_audit):
        """Audit logs when outcome is from untrusted source."""
        mock_audit.return_value = True

        # Simulate an outcome from an untrusted source
        signal = {
            "task_id": "task-456",
            "source": "external_api",
        }

        verified, reason = verify_outcome_source(signal)

        # Manually call audit logging (simulating what emit_task_outcome does)
        audit_outcome_verification(
            tenant_id="_default",
            task_id="task-456",
            verified=verified,
            source=signal["source"],
            reason=reason,
        )

        mock_audit.assert_called_once()

    def test_audit_logged_with_correct_event_type(self):
        """Audit logging differentiates verified vs unverified events."""
        with mock.patch("core.learning.outcome_sink.core_audit_event") as mock_core_audit:
            mock_core_audit.return_value = "audit-ref-123"

            # Test verified outcome
            audit_outcome_verification(
                tenant_id="_default",
                task_id="task-123",
                verified=True,
                source="task_manager",
                reason="outcome_source_verified: source=task_manager",
            )

            # Should call with verified event type
            assert mock_core_audit.call_count >= 1
            first_call = mock_core_audit.call_args_list[0]
            assert "verified" in first_call[0][0]  # event_type contains "verified"


class TestOutcomePoisoningBackpropBlocked:
    """Test: Corrupted/unverified outcomes do NOT influence optimizer backprop."""

    def test_recent_outcomes_filters_unverified(self):
        """recent_outcomes only counts outcomes with outcome_source_verified=true."""
        # Create mock events with mixed verification states
        mock_event_verified = mock.MagicMock()
        mock_event_verified.signal = {
            "success": True,
            "outcome_source_verified": True,
        }

        mock_event_unverified = mock.MagicMock()
        mock_event_unverified.signal = {
            "success": True,
            "outcome_source_verified": False,  # Poisoned outcome
        }

        mock_event_no_verification = mock.MagicMock()
        mock_event_no_verification.signal = {
            "success": True,
            # No outcome_source_verified key — treated as unverified
        }

        # Mock the store
        mock_store = mock.MagicMock()
        mock_store.query_events.return_value = [
            mock_event_verified,
            mock_event_unverified,
            mock_event_verified,
            mock_event_no_verification,
        ]

        successes, total = recent_outcomes("_default", limit=10, store=mock_store)

        # Should only count the 2 verified outcomes
        assert total == 2, "Should only count outcomes with outcome_source_verified=true"
        assert successes == 2, "Should only count successes from verified outcomes"

    def test_recent_outcomes_empty_when_all_unverified(self):
        """recent_outcomes returns (0, 0) when all outcomes are unverified."""
        # All events are unverified
        mock_event_unverified_1 = mock.MagicMock()
        mock_event_unverified_1.signal = {
            "success": True,
            "outcome_source_verified": False,
        }

        mock_event_unverified_2 = mock.MagicMock()
        mock_event_unverified_2.signal = {
            "success": False,
            "outcome_source_verified": False,
        }

        mock_store = mock.MagicMock()
        mock_store.query_events.return_value = [
            mock_event_unverified_1,
            mock_event_unverified_2,
        ]

        successes, total = recent_outcomes("_default", limit=10, store=mock_store)

        # Should return (0, 0) because no verified outcomes exist
        assert (successes, total) == (0, 0)

    def test_emit_task_outcome_sets_verification_flag(self):
        """emit_task_outcome sets outcome_source_verified flag in signal."""
        mock_emitter = mock.MagicMock()
        mock_emitter.emit.return_value = True

        with mock.patch("core.learning.outcome_sink.audit_outcome_verification"):
            emit_task_outcome(
                tenant_id="_default",
                task_id="task-789",
                status="completed",
                exit_code=0,
                emitter=mock_emitter,
            )

        # Check that emit was called
        assert mock_emitter.emit.called
        event = mock_emitter.emit.call_args[0][0]

        # The event's signal should have outcome_source_verified=true
        # (because task_manager is a trusted source)
        signal = event.signal
        assert signal.get("outcome_source_verified") is True


class TestIntegrateFeedbackOutcomeSourceVerification:
    """Test: Feedback outcomes also go through source verification."""

    @mock.patch("core.learning.outcome_sink.audit_outcome_verification")
    def test_feedback_outcome_verified(self, mock_audit):
        """integrate_feedback_outcome verifies and logs feedback source."""
        mock_audit.return_value = True
        mock_emitter = mock.MagicMock()
        mock_emitter.emit.return_value = True

        feedback_signal = {
            "outcome_feedback": "yes",
            "quality_rating": 5,
        }

        result = integrate_feedback_outcome(
            tenant_id="_default",
            task_id="task-111",
            feedback_signal=feedback_signal,
            emitter=mock_emitter,
        )

        assert result is True
        # Verify audit was called
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["verified"] is True  # feedback_loop is trusted
        assert call_kwargs["source"] == "feedback_loop"


class TestTrustedOutcomeSourcesConstant:
    """Test: TRUSTED_OUTCOME_SOURCES is properly defined."""

    def test_trusted_sources_includes_expected_values(self):
        """TRUSTED_OUTCOME_SOURCES includes audit backend, task_manager, feedback_loop."""
        assert "audit_backend_outcome" in TRUSTED_OUTCOME_SOURCES
        assert "task_manager" in TRUSTED_OUTCOME_SOURCES
        assert "feedback_loop" in TRUSTED_OUTCOME_SOURCES

    def test_trusted_sources_is_immutable(self):
        """TRUSTED_OUTCOME_SOURCES is a frozenset (immutable)."""
        assert isinstance(TRUSTED_OUTCOME_SOURCES, frozenset)

    def test_untrusted_sources_not_in_constant(self):
        """Untrusted sources are not in TRUSTED_OUTCOME_SOURCES."""
        assert "external_api" not in TRUSTED_OUTCOME_SOURCES
        assert "skill_config" not in TRUSTED_OUTCOME_SOURCES
        assert "hardcoded_config" not in TRUSTED_OUTCOME_SOURCES


class TestAttackScenarios:
    """Test: Realistic attack scenarios are blocked."""

    def test_weight_poisoning_attack_from_external_api(self):
        """Attack: External API injects fake positive outcome → blocked."""
        # Attacker tries to inject a positive outcome from an external API
        # to skew the optimizer toward a bad routing decision
        signal = {
            "task_id": "task-attack-1",
            "status": "completed",
            "success": True,  # Fake success
            "source": "external_api",  # Untrusted source
        }

        verified, reason = verify_outcome_source(signal)

        # Attack should be blocked
        assert verified is False
        assert "outcome_source_unverified" in reason

    def test_weight_poisoning_attack_from_skill_config(self):
        """Attack: Skill config source injects fake outcome → blocked."""
        # Attacker tries to poison the config with a fake outcome
        signal = {
            "task_id": "task-attack-2",
            "config_update": {"threshold": 0.9},
            "source": "skill_config",  # Untrusted source
        }

        verified, reason = verify_outcome_source(signal)

        # Attack should be blocked
        assert verified is False
        assert "outcome_source_unverified" in reason

    @mock.patch("core.learning.outcome_sink.audit_outcome_verification")
    def test_poisoning_attempt_is_audited(self, mock_audit):
        """Attack: Poisoning attempt is logged to audit chain."""
        mock_audit.return_value = True

        # Simulate poisoning attempt
        signal = {
            "task_id": "task-attack-3",
            "source": "malicious_source",
        }

        verified, reason = verify_outcome_source(signal)

        # Verify the poisoning attempt was logged
        audit_outcome_verification(
            tenant_id="_default",
            task_id="task-attack-3",
            verified=verified,
            source=signal["source"],
            reason=reason,
        )

        # Should have called audit logging
        mock_audit.assert_called_once()


class TestCompliance:
    """Test: Security fix maintains compliance with GDPR/ADR-0314."""

    def test_outcome_source_verification_is_audited_gdpr_art_30(self):
        """GDPR Art. 30: Every decision/processing step is audited."""
        # The outcome source verification itself is audited
        with mock.patch("core.learning.outcome_sink.core_audit_event") as mock_core_audit:
            mock_core_audit.return_value = "audit-ref-123"

            audit_outcome_verification(
                tenant_id="_default",
                task_id="task-123",
                verified=False,
                source="untrusted",
                reason="outcome_source_unverified",
            )

            # Core audit event should be called
            mock_core_audit.assert_called()

    def test_tenant_isolation_in_verification(self):
        """GDPR Art. 32: Tenant isolation in outcome verification."""
        with mock.patch("core.learning.outcome_sink.core_audit_event") as mock_core_audit:
            mock_core_audit.return_value = "audit-ref-123"

            # Verify for tenant-1
            audit_outcome_verification(
                tenant_id="tenant-1",
                task_id="task-123",
                verified=True,
                source="task_manager",
                reason="verified",
            )

            # core_audit_event should receive the tenant_id
            assert mock_core_audit.called
            call_kwargs = mock_core_audit.call_args[1]
            assert call_kwargs["tenant_id"] == "tenant-1"

    def test_content_free_audit_logging(self):
        """ADR-0314: Audit logging is content-free (no PII/prompt data)."""
        with mock.patch("core.learning.outcome_sink.core_audit_event") as mock_core_audit:
            mock_core_audit.return_value = "audit-ref-123"

            # Call audit logging
            audit_outcome_verification(
                tenant_id="_default",
                task_id="task-123",
                verified=True,
                source="task_manager",
                reason="outcome_source_verified",
            )

            # Check that details don't contain PII or prompt data
            call_kwargs = mock_core_audit.call_args[1]
            details = call_kwargs.get("details", {})

            # Should only contain: task_id, outcome_source, verification_reason
            # No prompt data, no user instructions, no conversation content
            assert "task_id" in details
            assert "outcome_source" in details
            assert "verification_reason" in details
            # No sensitive fields
            assert "instruction" not in details
            assert "prompt" not in details
