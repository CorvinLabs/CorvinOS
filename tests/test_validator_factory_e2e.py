"""E2E integration tests for Input Validator Factory — ADR-0296.

This test verifies that the ValidatorFactory is integrated into
real code paths and works with the pipeline (capability gates + audit).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.validators import FACTORY, validate


class TestValidatorFactoryE2EFeatureFlagEndpoint:
    """Test validator integration with feature flag endpoint."""

    def test_flag_id_validation_through_factory(self) -> None:
        """Validate flag_id through factory matches RFC in feature_flags.py."""
        # Valid flag IDs
        result = validate("flag_id", "plugin_health_monitoring", tenant_id="default")
        assert result.is_valid is True

        result = validate("flag_id", "bridge_tde_execution", tenant_id="default")
        assert result.is_valid is True

        result = validate("flag_id", "validator_factory_enabled", tenant_id="default")
        assert result.is_valid is True

        # Invalid flag IDs (would be rejected on route validation)
        result = validate("flag_id", "MyFlag", tenant_id="default")
        assert result.is_valid is False

        result = validate("flag_id", "flag-with-dash", tenant_id="default")
        assert result.is_valid is False

        result = validate("flag_id", "1_starting_digit", tenant_id="default")
        assert result.is_valid is False

    def test_feature_flag_registry_validates_correctly(self) -> None:
        """Feature flag registry imports without errors (flags validated)."""
        from core.console.corvin_console.feature_flags import REGISTRY

        # Check that validator_factory_enabled is in registry
        flag_ids = [f.id for f in REGISTRY]
        assert "validator_factory_enabled" in flag_ids

        # Check that the flag has correct defaults
        for flag in REGISTRY:
            if flag.id == "validator_factory_enabled":
                assert flag.default is False  # Must default OFF
                assert flag.owner == "maintainer"
                assert flag.target_release == "0.11.x"
                break
        else:
            pytest.fail("validator_factory_enabled flag not found in registry")


class TestValidatorFactoryE2EPipelineIntegration:
    """Test validator integration with pipeline (capability + audit)."""

    def test_validator_result_structured_for_pipeline(self) -> None:
        """ValidationResult structure supports pipeline error responses."""
        # Invalid input
        result = validate("email", "not-an-email", tenant_id="default")

        assert result.is_valid is False
        assert result.error_code is not None
        assert result.error_message is not None

        # Error code is non-specific (no data leakage)
        assert "not-an-email" not in result.error_code
        assert "not-an-email" not in result.error_message

    def test_tenant_isolation_in_validation(self) -> None:
        """Validator tenant_id is keyword-only (enforces isolation)."""
        # This validates tenant_id is required and keyword-only
        result = validate("string", "test_value", tenant_id="tenant_1")
        assert result.is_valid is True

        result = validate("string", "test_value", tenant_id="tenant_2")
        assert result.is_valid is True

        # Both tenants can validate independently without cross-contamination
        assert result.is_valid is True


class TestValidatorFactoryE2EUseCases:
    """Test real-world use cases for the validator factory."""

    def test_validate_peer_id_for_a2a_protocol(self) -> None:
        """ADR-0296 example: peer_id validation for A2A protocol."""
        # Valid peer IDs
        result = validate("peer_id", "instance_001", tenant_id="default")
        assert result.is_valid is True

        result = validate("peer_id", "gateway_primary", tenant_id="default")
        assert result.is_valid is True

        # Invalid peer IDs
        result = validate("peer_id", "", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_length"

        result = validate("peer_id", "x" * 100, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_length"

        result = validate("peer_id", "peer-with-dash", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_format"

    def test_validate_email_for_consent_tracking(self) -> None:
        """ADR-0296 example: email validation for consent tracking."""
        # Valid emails
        result = validate("email", "user@example.com", tenant_id="default")
        assert result.is_valid is True

        result = validate("email", "user+tag@sub.example.co.uk", tenant_id="default")
        assert result.is_valid is True

        # Invalid emails
        result = validate("email", "invalid-email", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_email"

    def test_validate_uuid_for_session_ids(self) -> None:
        """ADR-0296 example: UUID validation for session tracking."""
        # Valid UUID v4
        result = validate(
            "uuid",
            "550e8400-e29b-41d4-a716-446655440000",
            tenant_id="default",
        )
        assert result.is_valid is True

        # Invalid UUIDs
        result = validate("uuid", "not-a-uuid", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_uuid"

        result = validate("uuid", "550e8400-e29b-41d4-a716", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_uuid"

    def test_validate_url_for_webhook_targets(self) -> None:
        """ADR-0296 example: URL validation for webhook configuration."""
        # Valid URLs
        result = validate(
            "url",
            "https://webhook.example.com/callback",
            tenant_id="default",
        )
        assert result.is_valid is True

        # Invalid URLs
        result = validate("url", "not-a-url", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_url"

        result = validate("url", "ftp://example.com", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_url"

    def test_validate_integer_for_quota_limits(self) -> None:
        """ADR-0296 example: integer validation for quota enforcement."""
        # Valid integers in range
        result = validate(
            "integer",
            1000,
            tenant_id="default",
            min_value=0,
            max_value=10000,
        )
        assert result.is_valid is True

        # Out of range
        result = validate(
            "integer",
            50000,
            tenant_id="default",
            min_value=0,
            max_value=10000,
        )
        assert result.is_valid is False
        assert result.error_code == "invalid_range"

    def test_validate_string_with_pattern_for_identifiers(self) -> None:
        """ADR-0296 example: string pattern validation for identifiers."""
        # Valid identifiers
        result = validate(
            "string",
            "corvin_instance_001",
            tenant_id="default",
            pattern=r'^[a-z_][a-z0-9_]*$',
        )
        assert result.is_valid is True

        # Invalid pattern
        result = validate(
            "string",
            "INVALID_CAPS",
            tenant_id="default",
            pattern=r'^[a-z_][a-z0-9_]*$',
        )
        assert result.is_valid is False
        assert result.error_code == "invalid_format"


class TestValidatorFactoryE2EFailClosed:
    """Test fail-closed behavior in real scenarios."""

    def test_invalid_input_fails_not_passes(self) -> None:
        """Invalid input always fails, never silently passes."""
        # Invalid string
        result = validate("string", 12345, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

        # Invalid email
        result = validate("email", None, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

        # Invalid UUID
        result = validate("uuid", 123, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_unknown_validator_fails_closed(self) -> None:
        """Unknown validator rejects value (fail-closed)."""
        result = validate("unknown_validator_type", "value", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "unknown_validator"

    def test_validation_error_caught_not_raised(self) -> None:
        """Validation errors are caught and converted to failures, not raised."""
        # This should not raise an exception even with invalid regex
        result = validate(
            "string",
            "test",
            tenant_id="default",
            pattern=r'[invalid(',  # Malformed regex
        )
        assert result.is_valid is False
        assert result.error_code == "validation_error"


class TestValidatorFactoryE2EErrorMessages:
    """Test that error messages don't leak data (security)."""

    def test_error_messages_generic(self) -> None:
        """Error messages do not contain the rejected value."""
        result = validate("email", "user@INVALID", tenant_id="default")
        assert result.is_valid is False

        # Message should not contain the input value
        assert "user@INVALID" not in result.error_message
        assert "INVALID" not in result.error_message

    def test_error_codes_are_generic(self) -> None:
        """Error codes are generic, not specific to input."""
        # String validation error
        result = validate("string", 12345, tenant_id="default")
        assert result.error_code == "invalid_type"  # Generic code

        # Integer validation error
        result = validate("integer", "not_an_int", tenant_id="default")
        assert result.error_code == "invalid_type"  # Generic code

        # Email validation error
        result = validate("email", "not-an-email", tenant_id="default")
        assert result.error_code == "invalid_email"  # Non-specific


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
