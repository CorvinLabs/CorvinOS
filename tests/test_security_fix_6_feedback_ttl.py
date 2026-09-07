"""Security Fix #6: Stale Feedback Timestamp Validation + TTL Enforcement Tests.

Test coverage for FeedbackTTLValidator:
- Timestamp validation (present, format, range)
- TTL enforcement (fresh, stale, future-dated)
- Audit logging (feedback_stale events)
- Tenant-scoped configuration
- Attack vectors (stale injection, missing timestamp, format tampering)

Total: 16 test cases covering all scenarios
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock
from uuid import uuid4

from core.learning.feedback_validator import (
    FeedbackTTLValidator,
    FeedbackTTLCheckResult,
    FeedbackStalenessReason,
)


class TestFeedbackTTLCheckResult:
    """Test FeedbackTTLCheckResult data structure."""

    def test_create_valid_result(self):
        """Create a valid TTL check result."""
        result = FeedbackTTLCheckResult(
            is_valid=True,
            age_seconds=30,
            message="Timestamp within valid TTL window",
        )
        assert result.is_valid is True
        assert result.age_seconds == 30
        assert result.reason is None
        assert "valid TTL" in result.message

    def test_create_invalid_result_stale(self):
        """Create an invalid result for stale feedback."""
        result = FeedbackTTLCheckResult(
            is_valid=False,
            age_seconds=7200,  # 2 hours
            reason=FeedbackStalenessReason.EXCEEDS_TTL,
            message="Feedback is 7200s old (max TTL: 3600s)",
        )
        assert result.is_valid is False
        assert result.age_seconds == 7200
        assert result.reason == FeedbackStalenessReason.EXCEEDS_TTL

    def test_create_invalid_result_missing_timestamp(self):
        """Create an invalid result for missing timestamp."""
        result = FeedbackTTLCheckResult(
            is_valid=False,
            reason=FeedbackStalenessReason.MISSING_TIMESTAMP,
            message="Feedback timestamp is required (missing from payload)",
        )
        assert result.is_valid is False
        assert result.age_seconds is None
        assert result.reason == FeedbackStalenessReason.MISSING_TIMESTAMP


class TestFeedbackTTLValidator:
    """Test FeedbackTTLValidator with default configuration."""

    def test_validator_initialization_defaults(self):
        """Validator uses default TTL of 60 minutes."""
        validator = FeedbackTTLValidator()
        assert validator.max_age_seconds == 3600
        assert validator.allow_future_seconds == 5

    def test_validator_initialization_custom(self):
        """Validator accepts custom TTL settings."""
        validator = FeedbackTTLValidator(
            max_age_seconds=1800,  # 30 minutes
            allow_future_seconds=10,
        )
        assert validator.max_age_seconds == 1800
        assert validator.allow_future_seconds == 10

    def test_feedback_fresh_accepted(self):
        """Fresh feedback (1 min old) is accepted."""
        validator = FeedbackTTLValidator()
        now = datetime.utcnow()
        one_min_ago = (now - timedelta(minutes=1)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=one_min_ago,
            feedback_id="fb-123",
            tenant_id="_default",
        )

        assert result.is_valid is True
        assert result.age_seconds <= 60
        assert result.reason is None

    def test_feedback_stale_rejected(self):
        """Stale feedback (61 min old) is rejected."""
        validator = FeedbackTTLValidator()
        now = datetime.utcnow()
        stale_time = (now - timedelta(minutes=61)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=stale_time,
            feedback_id="fb-stale",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.age_seconds > 3600
        assert result.reason == FeedbackStalenessReason.EXCEEDS_TTL

    def test_feedback_missing_timestamp_rejected(self):
        """Feedback without timestamp is rejected."""
        validator = FeedbackTTLValidator()

        result = validator.validate_timestamp(
            timestamp_iso=None,
            feedback_id="fb-notimestamp",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.MISSING_TIMESTAMP

    def test_feedback_empty_timestamp_rejected(self):
        """Feedback with empty timestamp string is rejected."""
        validator = FeedbackTTLValidator()

        result = validator.validate_timestamp(
            timestamp_iso="",
            feedback_id="fb-empty",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.MISSING_TIMESTAMP

    def test_feedback_invalid_format_rejected(self):
        """Feedback with invalid timestamp format is rejected."""
        validator = FeedbackTTLValidator()

        result = validator.validate_timestamp(
            timestamp_iso="not-a-timestamp",
            feedback_id="fb-invalid-format",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.TIMESTAMP_PARSE_ERROR

    def test_feedback_malformed_iso8601_rejected(self):
        """Feedback with malformed ISO 8601 is rejected."""
        validator = FeedbackTTLValidator()

        result = validator.validate_timestamp(
            timestamp_iso="not-a-valid-date-at-all",
            feedback_id="fb-malformed",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.TIMESTAMP_PARSE_ERROR

    def test_feedback_at_ttl_boundary_accepted(self):
        """Feedback at exact TTL boundary (3600s) is accepted."""
        validator = FeedbackTTLValidator(max_age_seconds=3600)
        now = datetime.utcnow()
        at_boundary = (now - timedelta(seconds=3600)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=at_boundary,
            feedback_id="fb-boundary",
            tenant_id="_default",
        )

        # At boundary should be accepted (<=)
        assert result.is_valid is True

    def test_feedback_beyond_ttl_boundary_rejected(self):
        """Feedback beyond TTL boundary (3601s) is rejected."""
        validator = FeedbackTTLValidator(max_age_seconds=3600)
        now = datetime.utcnow()
        beyond_boundary = (now - timedelta(seconds=3601)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=beyond_boundary,
            feedback_id="fb-beyond",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.EXCEEDS_TTL

    def test_feedback_slightly_future_accepted(self):
        """Feedback slightly in future (within clock skew tolerance) is accepted."""
        validator = FeedbackTTLValidator(allow_future_seconds=5)
        now = datetime.utcnow()
        slight_future = (now + timedelta(seconds=3)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=slight_future,
            feedback_id="fb-slight-future",
            tenant_id="_default",
        )

        assert result.is_valid is True

    def test_feedback_far_future_rejected(self):
        """Feedback far in future (beyond clock skew tolerance) is rejected."""
        validator = FeedbackTTLValidator(allow_future_seconds=5)
        now = datetime.utcnow()
        far_future = (now + timedelta(seconds=10)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=far_future,
            feedback_id="fb-far-future",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.FUTURE_TIMESTAMP

    def test_feedback_ttl_audit_logged(self):
        """Stale feedback is logged as audit event."""
        mock_audit_backend = Mock()
        validator = FeedbackTTLValidator(audit_backend=mock_audit_backend)

        now = datetime.utcnow()
        stale_time = (now - timedelta(minutes=61)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=stale_time,
            feedback_id="fb-audit-test",
            tenant_id="tenant-abc",
        )

        # Verify audit backend was called
        assert mock_audit_backend.write_event.called
        call_args = mock_audit_backend.write_event.call_args[0][0]
        assert call_args["event_type"] == "feedback_stale"
        assert call_args["feedback_id"] == "fb-audit-test"
        assert call_args["tenant_id"] == "tenant-abc"
        assert call_args["rejection_reason"] == "exceeds_ttl"

    def test_feedback_ttl_configurable(self):
        """MAX_AGE_SECONDS can be configured per validator instance."""
        validator_30min = FeedbackTTLValidator(max_age_seconds=1800)  # 30 min
        validator_120min = FeedbackTTLValidator(max_age_seconds=7200)  # 120 min

        now = datetime.utcnow()
        old_60_min = (now - timedelta(minutes=60)).isoformat() + "Z"

        result_30 = validator_30min.validate_timestamp(old_60_min)
        result_120 = validator_120min.validate_timestamp(old_60_min)

        # 60 min > 30 min TTL → rejected
        assert result_30.is_valid is False
        assert result_30.reason == FeedbackStalenessReason.EXCEEDS_TTL

        # 60 min < 120 min TTL → accepted
        assert result_120.is_valid is True

    def test_feedback_ttl_tenant_scoped(self):
        """Each tenant can have independent TTL configuration."""
        config_tenant_a = {
            'spec': {
                'learning': {
                    'feedback': {
                        'max_age_seconds': 1800,  # 30 min
                    }
                }
            }
        }
        config_tenant_b = {
            'spec': {
                'learning': {
                    'feedback': {
                        'max_age_seconds': 7200,  # 120 min
                    }
                }
            }
        }

        validator_a = FeedbackTTLValidator.from_tenant_config(config_tenant_a['spec'])
        validator_b = FeedbackTTLValidator.from_tenant_config(config_tenant_b['spec'])

        assert validator_a.max_age_seconds == 1800
        assert validator_b.max_age_seconds == 7200

        # Verify they operate independently
        now = datetime.utcnow()
        old_60_min = (now - timedelta(minutes=60)).isoformat() + "Z"

        result_a = validator_a.validate_timestamp(old_60_min)
        result_b = validator_b.validate_timestamp(old_60_min)

        assert result_a.is_valid is False  # Exceeds 30 min TTL
        assert result_b.is_valid is True   # Within 120 min TTL


class TestFeedbackTTLValidatorAttackVectors:
    """Test security-focused attack scenarios."""

    def test_attack_stale_feedback_injection(self):
        """Attack: Inject 1-hour-old feedback to poison learning loop."""
        validator = FeedbackTTLValidator()
        now = datetime.utcnow()

        # Attacker sends feedback from 70 minutes ago
        attack_timestamp = (now - timedelta(minutes=70)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=attack_timestamp,
            feedback_id="attack-stale-inject",
            tenant_id="_default",
        )

        # Attack must be blocked
        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.EXCEEDS_TTL

    def test_attack_missing_timestamp_bypass(self):
        """Attack: Omit timestamp to bypass TTL check."""
        validator = FeedbackTTLValidator()

        # Attacker sends feedback with no timestamp
        result = validator.validate_timestamp(
            timestamp_iso=None,
            feedback_id="attack-missing-ts",
            tenant_id="_default",
        )

        # Attack must be blocked
        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.MISSING_TIMESTAMP

    def test_attack_timestamp_format_tampering(self):
        """Attack: Tamper with timestamp format to confuse parser."""
        validator = FeedbackTTLValidator()

        # Attacker sends various malformed timestamps
        malformed_timestamps = [
            "12:34:56",      # Time only
            "invalid",       # Garbage
            "not-iso-8601",  # Garbage
            "abc123def",     # Random string
        ]

        for malformed in malformed_timestamps:
            result = validator.validate_timestamp(
                timestamp_iso=malformed,
                feedback_id="attack-tampering",
                tenant_id="_default",
            )
            assert result.is_valid is False
            assert result.reason == FeedbackStalenessReason.TIMESTAMP_PARSE_ERROR

    def test_attack_future_timestamp_clock_reset(self):
        """Attack: Use far-future timestamp to evade clock checks."""
        validator = FeedbackTTLValidator(allow_future_seconds=5)
        now = datetime.utcnow()

        # Attacker sends feedback from 1 day in the future
        attack_future = (now + timedelta(days=1)).isoformat() + "Z"

        result = validator.validate_timestamp(
            timestamp_iso=attack_future,
            feedback_id="attack-future",
            tenant_id="_default",
        )

        # Attack must be blocked
        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.FUTURE_TIMESTAMP

    def test_audit_event_audit_backend_failure_nonfatal(self):
        """Audit backend failure doesn't break TTL validation."""
        mock_audit_backend = Mock()
        mock_audit_backend.write_event.side_effect = Exception("Audit backend error")

        validator = FeedbackTTLValidator(audit_backend=mock_audit_backend)
        now = datetime.utcnow()
        stale_time = (now - timedelta(minutes=70)).isoformat() + "Z"

        # Validation should still work despite audit failure
        result = validator.validate_timestamp(
            timestamp_iso=stale_time,
            feedback_id="fb-audit-fail",
            tenant_id="_default",
        )

        assert result.is_valid is False
        assert result.reason == FeedbackStalenessReason.EXCEEDS_TTL


class TestFeedbackTTLValidatorTenantConfig:
    """Test tenant configuration loading."""

    def test_from_tenant_config_defaults(self):
        """Load validator from tenant config with defaults."""
        config = {'spec': {}}
        validator = FeedbackTTLValidator.from_tenant_config(config['spec'])

        assert validator.max_age_seconds == 3600
        assert validator.allow_future_seconds == 5

    def test_from_tenant_config_custom_max_age(self):
        """Load validator from tenant config with custom max_age_seconds."""
        config = {
            'spec': {
                'learning': {
                    'feedback': {
                        'max_age_seconds': 1800,
                    }
                }
            }
        }
        validator = FeedbackTTLValidator.from_tenant_config(config['spec'])

        assert validator.max_age_seconds == 1800
        assert validator.allow_future_seconds == 5  # Default

    def test_from_tenant_config_custom_future_seconds(self):
        """Load validator from tenant config with custom allow_future_seconds."""
        config = {
            'spec': {
                'learning': {
                    'feedback': {
                        'allow_future_seconds': 15,
                    }
                }
            }
        }
        validator = FeedbackTTLValidator.from_tenant_config(config['spec'])

        assert validator.max_age_seconds == 3600  # Default
        assert validator.allow_future_seconds == 15

    def test_from_tenant_config_all_custom(self):
        """Load validator from tenant config with all custom values."""
        config = {
            'spec': {
                'learning': {
                    'feedback': {
                        'max_age_seconds': 7200,
                        'allow_future_seconds': 10,
                    }
                }
            }
        }
        validator = FeedbackTTLValidator.from_tenant_config(config['spec'])

        assert validator.max_age_seconds == 7200
        assert validator.allow_future_seconds == 10

    def test_from_tenant_config_none_config(self):
        """Load validator from None config uses defaults."""
        validator = FeedbackTTLValidator.from_tenant_config(None)

        assert validator.max_age_seconds == 3600
        assert validator.allow_future_seconds == 5

    def test_from_tenant_config_with_audit_backend(self):
        """Load validator from tenant config with audit backend."""
        mock_audit = Mock()
        config = {'spec': {'learning': {'feedback': {'max_age_seconds': 1800}}}}

        validator = FeedbackTTLValidator.from_tenant_config(config['spec'], audit_backend=mock_audit)

        assert validator.audit_backend is mock_audit
        assert validator.max_age_seconds == 1800
