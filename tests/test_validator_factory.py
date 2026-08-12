"""Unit tests for Input Validator Factory — ADR-0296."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.validators import (
    FACTORY,
    ValidationResult,
    ValidatorFactory,
    AndValidator,
    OrValidator,
    NotValidator,
    validate,
    validate_string,
    validate_integer,
    validate_email,
    validate_url,
    validate_peer_id,
    validate_flag_id,
    validate_uuid,
)


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_valid_result_creation(self) -> None:
        """Create a valid result."""
        result = ValidationResult(is_valid=True, value="test")
        assert result.is_valid is True
        assert result.value == "test"
        assert result.error_message is None
        assert result.error_code is None

    def test_invalid_result_creation(self) -> None:
        """Create an invalid result."""
        result = ValidationResult(
            is_valid=False,
            error_message="Test error",
            error_code="test_error",
        )
        assert result.is_valid is False
        assert result.error_message == "Test error"
        assert result.error_code == "test_error"

    def test_valid_result_with_error_raises(self) -> None:
        """Valid result cannot have error_message."""
        with pytest.raises(ValueError):
            ValidationResult(is_valid=True, error_message="Invalid")

    def test_invalid_result_without_error_raises(self) -> None:
        """Invalid result must have error_message."""
        with pytest.raises(ValueError):
            ValidationResult(is_valid=False)


class TestStringValidator:
    """Test string validator."""

    def test_valid_string(self) -> None:
        """Valid string passes."""
        result = validate_string("hello", tenant_id="default")
        assert result.is_valid is True
        assert result.value == "hello"

    def test_non_string_rejected(self) -> None:
        """Non-string values rejected."""
        result = validate_string(123, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_string_too_short(self) -> None:
        """String below min_length rejected."""
        result = validate_string("", tenant_id="default", min_length=1)
        assert result.is_valid is False
        assert result.error_code == "invalid_length"

    def test_string_too_long(self) -> None:
        """String above max_length rejected."""
        result = validate_string("x" * 100, tenant_id="default", max_length=50)
        assert result.is_valid is False
        assert result.error_code == "invalid_length"

    def test_string_pattern_valid(self) -> None:
        """String matching pattern passes."""
        result = validate_string("hello123", tenant_id="default", pattern=r'^[a-z0-9]+$')
        assert result.is_valid is True

    def test_string_pattern_invalid(self) -> None:
        """String not matching pattern fails."""
        result = validate_string("HELLO", tenant_id="default", pattern=r'^[a-z]+$')
        assert result.is_valid is False
        assert result.error_code == "invalid_format"

    def test_string_pattern_regex_error(self) -> None:
        """Invalid regex pattern handled gracefully."""
        result = validate_string("test", tenant_id="default", pattern=r'[invalid(')
        assert result.is_valid is False
        assert result.error_code == "validation_error"


class TestIntegerValidator:
    """Test integer validator."""

    def test_valid_integer(self) -> None:
        """Valid integer passes."""
        result = validate_integer(42, tenant_id="default")
        assert result.is_valid is True
        assert result.value == 42

    def test_non_integer_rejected(self) -> None:
        """Non-integer values rejected."""
        result = validate_integer("42", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_boolean_rejected(self) -> None:
        """Booleans rejected (not integers)."""
        result = validate_integer(True, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_integer_below_min(self) -> None:
        """Integer below min_value rejected."""
        result = validate_integer(5, tenant_id="default", min_value=10)
        assert result.is_valid is False
        assert result.error_code == "invalid_range"

    def test_integer_above_max(self) -> None:
        """Integer above max_value rejected."""
        result = validate_integer(50, tenant_id="default", max_value=40)
        assert result.is_valid is False
        assert result.error_code == "invalid_range"

    def test_integer_in_range(self) -> None:
        """Integer in range passes."""
        result = validate_integer(25, tenant_id="default", min_value=10, max_value=40)
        assert result.is_valid is True

    def test_negative_integer(self) -> None:
        """Negative integers allowed by default."""
        result = validate_integer(-10, tenant_id="default")
        assert result.is_valid is True


class TestEmailValidator:
    """Test email validator."""

    def test_valid_email(self) -> None:
        """Valid email passes."""
        result = validate_email("user@example.com", tenant_id="default")
        assert result.is_valid is True

    def test_non_string_rejected(self) -> None:
        """Non-string email rejected."""
        result = validate_email(123, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_email_without_at_rejected(self) -> None:
        """Email without @ rejected."""
        result = validate_email("userexample.com", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_email"

    def test_email_without_domain_rejected(self) -> None:
        """Email without domain rejected."""
        result = validate_email("user@", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_email"

    def test_email_without_tld_rejected(self) -> None:
        """Email without TLD rejected."""
        result = validate_email("user@example", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_email"

    def test_complex_email(self) -> None:
        """Complex but valid email passes."""
        result = validate_email("user+tag@sub.example.co.uk", tenant_id="default")
        assert result.is_valid is True


class TestURLValidator:
    """Test URL validator."""

    def test_valid_http_url(self) -> None:
        """Valid HTTP URL passes."""
        result = validate_url("http://example.com", tenant_id="default")
        assert result.is_valid is True

    def test_valid_https_url(self) -> None:
        """Valid HTTPS URL passes."""
        result = validate_url("https://example.com", tenant_id="default")
        assert result.is_valid is True

    def test_non_string_url_rejected(self) -> None:
        """Non-string URL rejected."""
        result = validate_url(123, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_url_without_scheme_rejected(self) -> None:
        """URL without scheme rejected."""
        result = validate_url("example.com", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_url"

    def test_url_with_path(self) -> None:
        """URL with path passes."""
        result = validate_url("https://example.com/path/to/resource", tenant_id="default")
        assert result.is_valid is True

    def test_url_with_query(self) -> None:
        """URL with query string passes."""
        result = validate_url("https://example.com/search?q=test", tenant_id="default")
        assert result.is_valid is True

    def test_url_allowed_schemes(self) -> None:
        """URL with allowed scheme passes."""
        result = validate_url(
            "https://example.com",
            tenant_id="default",
            allowed_schemes=["https"],
        )
        assert result.is_valid is True

    def test_url_disallowed_scheme(self) -> None:
        """URL with disallowed scheme rejected."""
        result = validate_url(
            "http://example.com",
            tenant_id="default",
            allowed_schemes=["https"],
        )
        assert result.is_valid is False
        assert result.error_code == "invalid_scheme"


class TestPeerIdValidator:
    """Test peer_id validator."""

    def test_valid_peer_id(self) -> None:
        """Valid peer ID passes."""
        result = validate_peer_id("peer_123", tenant_id="default")
        assert result.is_valid is True

    def test_non_string_peer_id_rejected(self) -> None:
        """Non-string peer ID rejected."""
        result = validate_peer_id(123, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_peer_id_too_long(self) -> None:
        """Peer ID > 64 chars rejected."""
        result = validate_peer_id("x" * 65, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_length"

    def test_peer_id_empty(self) -> None:
        """Empty peer ID rejected."""
        result = validate_peer_id("", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_length"

    def test_peer_id_with_dash_rejected(self) -> None:
        """Peer ID with dash rejected."""
        result = validate_peer_id("peer-123", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_format"

    def test_peer_id_uppercase(self) -> None:
        """Uppercase peer ID passes."""
        result = validate_peer_id("PEER_123", tenant_id="default")
        assert result.is_valid is True


class TestFlagIdValidator:
    """Test flag_id validator."""

    def test_valid_flag_id(self) -> None:
        """Valid flag ID passes."""
        result = validate_flag_id("my_feature_flag", tenant_id="default")
        assert result.is_valid is True

    def test_flag_id_must_start_lowercase(self) -> None:
        """Flag ID must start with lowercase letter."""
        result = validate_flag_id("MyFeature", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_format"

    def test_flag_id_cannot_start_digit(self) -> None:
        """Flag ID cannot start with digit."""
        result = validate_flag_id("1my_feature", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_format"

    def test_flag_id_with_dash_rejected(self) -> None:
        """Flag ID with dash rejected."""
        result = validate_flag_id("my-feature", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_format"

    def test_flag_id_too_short(self) -> None:
        """Flag ID < 3 chars rejected."""
        result = validate_flag_id("ab", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_format"


class TestUUIDValidator:
    """Test UUID validator."""

    def test_valid_uuid_v4(self) -> None:
        """Valid UUID v4 passes."""
        result = validate_uuid(
            "550e8400-e29b-41d4-a716-446655440000",
            tenant_id="default",
        )
        assert result.is_valid is True

    def test_non_string_uuid_rejected(self) -> None:
        """Non-string UUID rejected."""
        result = validate_uuid(123, tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"

    def test_uuid_v3_rejected(self) -> None:
        """UUID v3 rejected (v4 only)."""
        result = validate_uuid(
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            tenant_id="default",
        )
        assert result.is_valid is False
        assert result.error_code == "invalid_uuid"

    def test_invalid_uuid_format(self) -> None:
        """Invalid UUID format rejected."""
        result = validate_uuid("not-a-uuid", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "invalid_uuid"


class TestValidatorFactory:
    """Test ValidatorFactory."""

    def test_factory_has_builtins(self) -> None:
        """Factory registers built-in validators."""
        validators = FACTORY.list_validators()
        assert "string" in validators
        assert "integer" in validators
        assert "email" in validators
        assert "url" in validators
        assert "peer_id" in validators
        assert "flag_id" in validators
        assert "uuid" in validators

    def test_register_custom_validator(self) -> None:
        """Register custom validator."""
        factory = ValidatorFactory()

        def custom_validator(value: any, *, tenant_id: str) -> ValidationResult:
            if value == "special":
                return ValidationResult(is_valid=True, value=value)
            return ValidationResult(
                is_valid=False,
                error_message="Value must be 'special'",
                error_code="not_special",
            )

        factory.register("custom", custom_validator)
        result = factory.validate("custom", "special", tenant_id="default")
        assert result.is_valid is True

    def test_register_duplicate_raises(self) -> None:
        """Registering duplicate name raises."""
        factory = ValidatorFactory()
        with pytest.raises(ValueError):
            factory.register("string", lambda v, *, tenant_id: ValidationResult(is_valid=True))

    def test_unregister_validator(self) -> None:
        """Unregister a validator."""
        factory = ValidatorFactory()
        factory.register("temp", lambda v, *, tenant_id: ValidationResult(is_valid=True))
        factory.unregister("temp")
        result = factory.validate("temp", "value", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "unknown_validator"

    def test_unregister_nonexistent_raises(self) -> None:
        """Unregistering nonexistent validator raises."""
        factory = ValidatorFactory()
        with pytest.raises(KeyError):
            factory.unregister("nonexistent")

    def test_validate_unknown_validator_fails_closed(self) -> None:
        """Unknown validator rejected (fail-closed)."""
        result = FACTORY.validate("nonexistent", "value", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "unknown_validator"

    def test_validate_with_kwargs(self) -> None:
        """Validate passes kwargs to validator."""
        result = FACTORY.validate(
            "string",
            "hello",
            tenant_id="default",
            min_length=3,
            max_length=10,
        )
        assert result.is_valid is True


class TestAndValidator:
    """Test AND composite validator."""

    def test_all_validators_pass(self) -> None:
        """AND passes if all validators pass."""
        and_validator = AndValidator([
            validate_string,
            lambda v, *, tenant_id: (
                ValidationResult(is_valid=True, value=v)
                if len(v) > 0 else
                ValidationResult(is_valid=False, error_message="Empty", error_code="empty")
            ),
        ])
        result = and_validator.validate("test", tenant_id="default")
        assert result.is_valid is True

    def test_first_validator_fails(self) -> None:
        """AND fails if first validator fails."""
        and_validator = AndValidator([
            lambda v, *, tenant_id: ValidationResult(is_valid=False, error_message="Fail", error_code="fail"),
            validate_string,
        ])
        result = and_validator.validate("test", tenant_id="default")
        assert result.is_valid is False

    def test_second_validator_fails(self) -> None:
        """AND fails if second validator fails."""
        and_validator = AndValidator([
            validate_string,
            lambda v, *, tenant_id: ValidationResult(is_valid=False, error_message="Fail", error_code="fail"),
        ])
        result = and_validator.validate("test", tenant_id="default")
        assert result.is_valid is False


class TestOrValidator:
    """Test OR composite validator."""

    def test_first_validator_passes(self) -> None:
        """OR passes if first validator passes."""
        or_validator = OrValidator([
            validate_string,
            validate_integer,
        ])
        result = or_validator.validate("test", tenant_id="default")
        assert result.is_valid is True

    def test_second_validator_passes(self) -> None:
        """OR passes if second validator passes."""
        or_validator = OrValidator([
            validate_integer,
            validate_string,
        ])
        result = or_validator.validate("test", tenant_id="default")
        assert result.is_valid is True

    def test_no_validators_pass(self) -> None:
        """OR fails if no validators pass."""
        or_validator = OrValidator([
            validate_integer,
            validate_email,
        ])
        result = or_validator.validate("test", tenant_id="default")
        assert result.is_valid is False


class TestNotValidator:
    """Test NOT validator."""

    def test_not_inverts_success(self) -> None:
        """NOT inverts success to failure."""
        not_validator = NotValidator(validate_string)
        result = not_validator.validate("test", tenant_id="default")
        assert result.is_valid is False

    def test_not_inverts_failure(self) -> None:
        """NOT inverts failure to success."""
        not_validator = NotValidator(validate_integer)
        result = not_validator.validate("test", tenant_id="default")
        assert result.is_valid is True


class TestFactoryRegisterComposite:
    """Test registering composite validators in factory."""

    def test_register_and_validator(self) -> None:
        """Register AND composite in factory."""
        factory = ValidatorFactory()
        and_validator = AndValidator([validate_string, validate_email])
        factory.register_composite("email_string", and_validator)
        result = factory.validate("email_string", "user@example.com", tenant_id="default")
        assert result.is_valid is True

    def test_register_or_validator(self) -> None:
        """Register OR composite in factory."""
        factory = ValidatorFactory()
        or_validator = OrValidator([validate_integer, validate_email])
        factory.register_composite("int_or_email", or_validator)
        result = factory.validate("int_or_email", 42, tenant_id="default")
        assert result.is_valid is True

    def test_register_composite_duplicate_raises(self) -> None:
        """Registering duplicate composite name raises."""
        factory = ValidatorFactory()
        and_validator = AndValidator([validate_string])
        factory.register_composite("temp", and_validator)
        with pytest.raises(ValueError):
            factory.register_composite("temp", and_validator)


class TestTenantIsolation:
    """Test tenant isolation via keyword-only tenant_id."""

    def test_validate_requires_tenant_id(self) -> None:
        """validate() requires tenant_id keyword argument."""
        # This should work
        result = validate("string", "test", tenant_id="tenant1")
        assert result.is_valid is True

    def test_factory_validate_requires_tenant_id(self) -> None:
        """FACTORY.validate() requires tenant_id keyword argument."""
        # This should work
        result = FACTORY.validate("string", "test", tenant_id="tenant1")
        assert result.is_valid is True

    def test_different_tenants_isolated(self) -> None:
        """Different tenants can validate independently."""
        # Both should work independently
        result1 = validate("email", "user1@example.com", tenant_id="tenant1")
        result2 = validate("email", "user2@example.com", tenant_id="tenant2")
        assert result1.is_valid is True
        assert result2.is_valid is True


class TestFailClosedBehavior:
    """Test fail-closed behavior."""

    def test_unknown_validator_returns_failure(self) -> None:
        """Unknown validator returns failure, not exception."""
        result = FACTORY.validate("unknown_validator_xyz", "value", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "unknown_validator"

    def test_validator_exception_caught(self) -> None:
        """Validator exception caught and converted to failure."""
        factory = ValidatorFactory()

        def bad_validator(value: any, *, tenant_id: str) -> ValidationResult:
            raise RuntimeError("Intentional error")

        factory.register("bad", bad_validator)
        result = factory.validate("bad", "value", tenant_id="default")
        assert result.is_valid is False
        assert result.error_code == "validation_error"


class TestConvenienceFunction:
    """Test convenience validate() function."""

    def test_convenience_validate(self) -> None:
        """Convenience function works correctly."""
        result = validate("email", "user@example.com", tenant_id="default")
        assert result.is_valid is True

    def test_convenience_validate_with_kwargs(self) -> None:
        """Convenience function passes kwargs."""
        result = validate(
            "string",
            "hello",
            tenant_id="default",
            min_length=3,
            max_length=10,
        )
        assert result.is_valid is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
