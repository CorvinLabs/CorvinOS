"""Unit Tests for Input Validator Factory — ADR-0296

Tests for centralized, deny-by-default input validation.
"""

import pytest
from core.validators import (
    FACTORY,
    ValidatorFactory,
    ValidationResult,
    validate_string,
    validate_integer,
    validate_email,
    validate_url,
    validate_peer_id,
    validate_flag_id,
    validate_uuid,
    validate,
    AndValidator,
    OrValidator,
    NotValidator,
)


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_valid_result(self):
        """Create valid result."""
        result = ValidationResult(is_valid=True, value="test")
        assert result.is_valid is True
        assert result.value == "test"
        assert result.error_message is None

    def test_invalid_result(self):
        """Create invalid result."""
        result = ValidationResult(
            is_valid=False,
            error_message="Invalid value",
            error_code="invalid_format",
        )
        assert result.is_valid is False
        assert result.error_message == "Invalid value"
        assert result.error_code == "invalid_format"

    def test_valid_with_error_message_raises(self):
        """Valid result cannot have error_message."""
        with pytest.raises(ValueError):
            ValidationResult(is_valid=True, value="test", error_message="error")

    def test_invalid_without_error_message_raises(self):
        """Invalid result must have error_message."""
        with pytest.raises(ValueError):
            ValidationResult(is_valid=False, value="test")

    def test_result_frozen(self):
        """ValidationResult is frozen."""
        result = ValidationResult(is_valid=True, value="test")
        with pytest.raises(AttributeError):
            result.is_valid = False


class TestValidatorFactory:
    """Test ValidatorFactory registry."""

    @pytest.fixture
    def factory(self):
        """Create fresh factory for each test."""
        return ValidatorFactory()

    def test_register_validator(self, factory):
        """Register a validator."""

        def my_validator(value, *, tenant_id):
            return ValidationResult(is_valid=True, value=value)

        factory.register("my_validator", my_validator)
        result = factory.validate("my_validator", "test", tenant_id="t1")
        assert result.is_valid is True

    def test_validate_unregistered_raises(self, factory):
        """Validating unregistered validator returns invalid result."""
        result = factory.validate("nonexistent", "value", tenant_id="t1")
        assert result.is_valid is False
        assert "Unknown validator" in result.error_message

    def test_factory_is_singleton(self):
        """FACTORY is a singleton."""
        # Both should be the same object
        assert FACTORY is FACTORY


class TestStringValidator:
    """Test validate_string."""

    def test_valid_string(self):
        """Valid string passes."""
        result = validate_string("test", tenant_id="t1")
        assert result.is_valid is True
        assert result.value == "test"

    def test_empty_string_invalid_by_default(self):
        """Empty string invalid by default (min_length=1)."""
        result = validate_string("", tenant_id="t1")
        assert result.is_valid is False
        assert "length" in result.error_message

    def test_string_too_long(self):
        """String exceeding max_length invalid."""
        result = validate_string("x" * 10001, tenant_id="t1", max_length=10000)
        assert result.is_valid is False
        assert "length" in result.error_message

    def test_string_custom_length(self):
        """Custom min/max length."""
        result = validate_string("ab", tenant_id="t1", min_length=1, max_length=2)
        assert result.is_valid is True

    def test_string_pattern_match(self):
        """String matching pattern."""
        result = validate_string(
            "test123", tenant_id="t1", pattern=r"^[a-z0-9]+$"
        )
        assert result.is_valid is True

    def test_string_pattern_mismatch(self):
        """String not matching pattern."""
        result = validate_string(
            "TEST", tenant_id="t1", pattern=r"^[a-z]+$"
        )
        assert result.is_valid is False
        assert "pattern" in result.error_message

    def test_non_string_type(self):
        """Non-string value invalid."""
        result = validate_string(123, tenant_id="t1")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"


class TestIntegerValidator:
    """Test validate_integer."""

    def test_valid_integer(self):
        """Valid integer passes."""
        result = validate_integer(42, tenant_id="t1")
        assert result.is_valid is True
        assert result.value == 42

    def test_integer_in_range(self):
        """Integer within range."""
        result = validate_integer(50, tenant_id="t1", min_value=0, max_value=100)
        assert result.is_valid is True

    def test_integer_below_min(self):
        """Integer below minimum."""
        result = validate_integer(-1, tenant_id="t1", min_value=0)
        assert result.is_valid is False
        assert result.error_code == "invalid_range"

    def test_integer_above_max(self):
        """Integer above maximum."""
        result = validate_integer(101, tenant_id="t1", max_value=100)
        assert result.is_valid is False
        assert result.error_code == "invalid_range"

    def test_non_integer_type(self):
        """Non-integer value invalid."""
        result = validate_integer("42", tenant_id="t1")
        assert result.is_valid is False
        assert result.error_code == "invalid_type"


class TestEmailValidator:
    """Test validate_email."""

    def test_valid_email(self):
        """Valid email passes."""
        result = validate_email("user@example.com", tenant_id="t1")
        assert result.is_valid is True

    def test_email_missing_at(self):
        """Email without @ is invalid."""
        result = validate_email("userexample.com", tenant_id="t1")
        assert result.is_valid is False

    def test_email_missing_domain(self):
        """Email without domain is invalid."""
        result = validate_email("user@", tenant_id="t1")
        assert result.is_valid is False

    def test_email_missing_local(self):
        """Email without local part is invalid."""
        result = validate_email("@example.com", tenant_id="t1")
        assert result.is_valid is False

    def test_non_string_email(self):
        """Non-string email invalid."""
        result = validate_email(123, tenant_id="t1")
        assert result.is_valid is False


class TestURLValidator:
    """Test validate_url."""

    def test_valid_url_http(self):
        """Valid HTTP URL passes."""
        result = validate_url("http://example.com", tenant_id="t1")
        assert result.is_valid is True

    def test_valid_url_https(self):
        """Valid HTTPS URL passes."""
        result = validate_url("https://example.com/path", tenant_id="t1")
        assert result.is_valid is True

    def test_invalid_url_no_scheme(self):
        """URL without scheme is invalid."""
        result = validate_url("example.com", tenant_id="t1")
        assert result.is_valid is False

    def test_invalid_url_bad_scheme(self):
        """URL with bad scheme is invalid."""
        result = validate_url("ftp://example.com", tenant_id="t1")
        assert result.is_valid is False

    def test_non_string_url(self):
        """Non-string URL invalid."""
        result = validate_url(123, tenant_id="t1")
        assert result.is_valid is False


class TestPeerIDValidator:
    """Test validate_peer_id."""

    def test_valid_peer_id(self):
        """Valid peer ID passes."""
        result = validate_peer_id("peer_1", tenant_id="t1")
        assert result.is_valid is True

    def test_peer_id_alphanumeric_underscore(self):
        """Peer ID with alphanumeric and underscore."""
        result = validate_peer_id("peer_abc_123", tenant_id="t1")
        assert result.is_valid is True

    def test_peer_id_too_short(self):
        """Peer ID less than 1 char invalid."""
        result = validate_peer_id("", tenant_id="t1")
        assert result.is_valid is False

    def test_peer_id_too_long(self):
        """Peer ID over 64 chars invalid."""
        result = validate_peer_id("x" * 65, tenant_id="t1")
        assert result.is_valid is False

    def test_peer_id_invalid_chars(self):
        """Peer ID with invalid chars (dash, dot)."""
        result = validate_peer_id("peer-1", tenant_id="t1")
        assert result.is_valid is False
        result = validate_peer_id("peer.1", tenant_id="t1")
        assert result.is_valid is False

    def test_peer_id_non_string(self):
        """Non-string peer ID invalid."""
        result = validate_peer_id(123, tenant_id="t1")
        assert result.is_valid is False


class TestFlagIDValidator:
    """Test validate_flag_id."""

    def test_valid_flag_id(self):
        """Valid flag ID passes."""
        result = validate_flag_id("feature_flag_1", tenant_id="t1")
        assert result.is_valid is True

    def test_flag_id_lowercase(self):
        """Flag ID must be lowercase."""
        result = validate_flag_id("FEATURE_FLAG", tenant_id="t1")
        assert result.is_valid is False

    def test_flag_id_uppercase_invalid(self):
        """Flag ID with uppercase invalid."""
        result = validate_flag_id("feature_Flag", tenant_id="t1")
        assert result.is_valid is False

    def test_flag_id_alphanumeric_underscore(self):
        """Flag ID with alphanumeric and underscore."""
        result = validate_flag_id("feature_1_test", tenant_id="t1")
        assert result.is_valid is True

    def test_flag_id_dash_invalid(self):
        """Flag ID with dash invalid."""
        result = validate_flag_id("feature-flag", tenant_id="t1")
        assert result.is_valid is False

    def test_flag_id_non_string(self):
        """Non-string flag ID invalid."""
        result = validate_flag_id(123, tenant_id="t1")
        assert result.is_valid is False


class TestUUIDValidator:
    """Test validate_uuid."""

    def test_valid_uuid4(self):
        """Valid UUID4 passes."""
        uuid_str = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        result = validate_uuid(uuid_str, tenant_id="t1")
        assert result.is_valid is True

    def test_uuid_without_dashes(self):
        """UUID without dashes is invalid (requires dashes)."""
        uuid_str = "f47ac10b58cc4372a5670e02b2c3d479"
        result = validate_uuid(uuid_str, tenant_id="t1")
        assert result.is_valid is False
        assert result.error_code == "invalid_uuid"

    def test_invalid_uuid(self):
        """Invalid UUID format."""
        result = validate_uuid("not-a-uuid", tenant_id="t1")
        assert result.is_valid is False

    def test_uuid_non_string(self):
        """Non-string UUID invalid."""
        result = validate_uuid(123, tenant_id="t1")
        assert result.is_valid is False


class TestCompositeValidators:
    """Test AndValidator, OrValidator, NotValidator."""

    @pytest.fixture
    def validators(self):
        """Create test validators."""

        def even(value, *, tenant_id):
            if value % 2 == 0:
                return ValidationResult(is_valid=True, value=value)
            return ValidationResult(
                is_valid=False, error_message="Not even", error_code="not_even"
            )

        def positive(value, *, tenant_id):
            if value > 0:
                return ValidationResult(is_valid=True, value=value)
            return ValidationResult(
                is_valid=False,
                error_message="Not positive",
                error_code="not_positive",
            )

        return {"even": even, "positive": positive}

    def test_and_validator_both_pass(self, validators):
        """AndValidator: both validators pass."""
        validator = AndValidator([validators["even"], validators["positive"]])
        result = validator.validate(4, tenant_id="t1")
        assert result.is_valid is True

    def test_and_validator_first_fails(self, validators):
        """AndValidator: first validator fails."""
        validator = AndValidator([validators["even"], validators["positive"]])
        result = validator.validate(3, tenant_id="t1")  # odd, but positive
        assert result.is_valid is False

    def test_and_validator_second_fails(self, validators):
        """AndValidator: second validator fails."""
        validator = AndValidator([validators["even"], validators["positive"]])
        result = validator.validate(-2, tenant_id="t1")  # even, but negative
        assert result.is_valid is False

    def test_or_validator_first_passes(self, validators):
        """OrValidator: first validator passes."""
        validator = OrValidator([validators["even"], validators["positive"]])
        result = validator.validate(2, tenant_id="t1")
        assert result.is_valid is True

    def test_or_validator_second_passes(self, validators):
        """OrValidator: second validator passes."""
        validator = OrValidator([validators["even"], validators["positive"]])
        result = validator.validate(3, tenant_id="t1")  # odd, but positive
        assert result.is_valid is True

    def test_or_validator_both_fail(self, validators):
        """OrValidator: both validators fail."""
        validator = OrValidator([validators["even"], validators["positive"]])
        result = validator.validate(-3, tenant_id="t1")  # odd and negative
        assert result.is_valid is False

    def test_not_validator_passes(self, validators):
        """NotValidator: negates passing validator."""
        validator = NotValidator(validators["even"])
        result = validator.validate(3, tenant_id="t1")  # odd (even fails, so not passes)
        assert result.is_valid is True

    def test_not_validator_fails(self, validators):
        """NotValidator: negates failing validator."""
        validator = NotValidator(validators["even"])
        result = validator.validate(2, tenant_id="t1")  # even (even passes, so not fails)
        assert result.is_valid is False


class TestValidateFunctionShorthand:
    """Test validate() shorthand function."""

    def test_validate_peer_id(self):
        """validate() shorthand for peer_id."""
        result = validate("peer_id", "peer_1", tenant_id="t1")
        assert result.is_valid is True

    def test_validate_flag_id(self):
        """validate() shorthand for flag_id."""
        result = validate("flag_id", "feature_flag", tenant_id="t1")
        assert result.is_valid is True

    def test_validate_string(self):
        """validate() shorthand for string."""
        result = validate("string", "test", tenant_id="t1")
        assert result.is_valid is True

    def test_validate_unknown_type(self):
        """validate() with unknown type."""
        result = validate("unknown_type", "value", tenant_id="t1")
        assert result.is_valid is False


class TestValidatorTenantIsolation:
    """Test tenant_id parameter."""

    def test_string_validator_tenant_param(self):
        """String validator requires tenant_id."""
        result = validate_string("test", tenant_id="tenant_1")
        assert result.is_valid is True

    def test_integer_validator_tenant_param(self):
        """Integer validator requires tenant_id."""
        result = validate_integer(42, tenant_id="tenant_1")
        assert result.is_valid is True


class TestValidationErrorCodes:
    """Test error codes in validation results."""

    def test_type_error_code(self):
        """Type validation error has correct code."""
        result = validate_string(123, tenant_id="t1")
        assert result.error_code == "invalid_type"

    def test_length_error_code(self):
        """Length validation error has correct code."""
        result = validate_string("x" * 10001, tenant_id="t1", max_length=10000)
        assert result.error_code == "invalid_length"

    def test_format_error_code(self):
        """Format validation error has correct code."""
        result = validate_string("TEST", tenant_id="t1", pattern=r"^[a-z]+$")
        assert result.error_code == "invalid_format"
