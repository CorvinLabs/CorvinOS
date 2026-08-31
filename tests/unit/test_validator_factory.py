"""
Unit Tests for Validator Factory — ADR-0296

Tests for all validators and factory behavior.
Target: ≥90% code coverage
"""

import pytest

from core.validators import (
    FACTORY,
    validate_alphanumeric,
    validate_email,
    validate_flag_id,
    validate_non_empty_string,
    validate_peer_id,
    validate_plugin_id,
    validate_port,
    validate_string_length,
    validate_tenant_id,
    validate_url,
    validate_uuid4,
)


# ============================================================================
# ValidatorFactory Tests
# ============================================================================


class TestValidatorFactory:
    """Test core factory behavior."""

    def test_factory_register_validator(self):
        """Test registering a custom validator."""
        def custom_validator(value):
            return isinstance(value, str) and value == "magic"

        FACTORY.register("custom_magic", custom_validator)
        assert FACTORY.has_validator("custom_magic")

    def test_factory_validate_success(self):
        """Test successful validation."""
        is_valid, error = FACTORY.validate("peer_id", "valid_peer_123")
        assert is_valid is True
        assert error is None

    def test_factory_validate_failure(self):
        """Test validation failure."""
        is_valid, error = FACTORY.validate("peer_id", "invalid-peer")
        assert is_valid is False
        assert error is not None

    def test_factory_unknown_validator(self):
        """Test unknown validator raises error."""
        is_valid, error = FACTORY.validate("nonexistent", "value")
        assert is_valid is False
        assert "Unknown validator" in error


# ============================================================================
# Peer ID Validator Tests
# ============================================================================


class TestValidatePeerId:
    """Test peer_id validator."""

    def test_valid_peer_id(self):
        """Valid peer IDs pass."""
        valid_ids = [
            "peer1",
            "DEVICE_A",
            "sensor_123",
            "a",
            "Z" * 64,  # max length
        ]
        for peer_id in valid_ids:
            is_valid, error = validate_peer_id(peer_id)
            assert is_valid is True, f"Failed: {peer_id}"
            assert error is None

    def test_invalid_peer_id_not_string(self):
        """Non-string peer ID rejected."""
        is_valid, error = validate_peer_id(123)
        assert is_valid is False
        assert "must be string" in error

    def test_invalid_peer_id_too_short(self):
        """Empty peer ID rejected."""
        is_valid, error = validate_peer_id("")
        assert is_valid is False
        assert "length 1–64" in error

    def test_invalid_peer_id_too_long(self):
        """Peer ID over 64 chars rejected."""
        is_valid, error = validate_peer_id("a" * 65)
        assert is_valid is False
        assert "length 1–64" in error

    def test_invalid_peer_id_special_chars(self):
        """Peer ID with special characters rejected."""
        invalid_ids = ["peer-1", "peer.id", "peer@id", "peer!"]
        for peer_id in invalid_ids:
            is_valid, error = validate_peer_id(peer_id)
            assert is_valid is False, f"Should fail: {peer_id}"
            assert "alphanumeric" in error or "underscore" in error


# ============================================================================
# Flag ID Validator Tests
# ============================================================================


class TestValidateFlagId:
    """Test flag_id validator."""

    def test_valid_flag_id(self):
        """Valid flag IDs pass."""
        # "a" was listed here as a valid flag id, which contradicted
        # tests/test_validator_factory.py::TestFlagIdValidator::
        # test_flag_id_too_short ("Flag ID < 3 chars rejected"). Both files
        # cannot hold: the 3-char floor won, because every flag that actually
        # ships is far longer (console_auto_reload, frontend_forge,
        # dual_gate_pipeline_enabled -- the shortest real one is 13 chars) and
        # a 1-char operator-visible settings key is not reviewable.
        valid_ids = [
            "plugin_builder_enabled",
            "tde_mode",
            "l44_strict",
            "z_9_a",
        ]
        for flag_id in valid_ids:
            is_valid, error = validate_flag_id(flag_id)
            assert is_valid is True, f"Failed: {flag_id}"
            assert error is None

    def test_invalid_flag_id_uppercase(self):
        """Uppercase in flag ID rejected."""
        is_valid, error = validate_flag_id("Plugin_Builder")
        assert is_valid is False
        assert "lowercase" in error

    def test_invalid_flag_id_hyphen(self):
        """Hyphen in flag ID rejected."""
        is_valid, error = validate_flag_id("plugin-builder")
        assert is_valid is False

    def test_invalid_flag_id_not_string(self):
        """Non-string flag ID rejected."""
        is_valid, error = validate_flag_id(123)
        assert is_valid is False
        assert "must be string" in error


# ============================================================================
# Plugin ID Validator Tests
# ============================================================================


class TestValidatePluginId:
    """Test plugin_id validator."""

    def test_valid_plugin_id(self):
        """Valid plugin IDs pass."""
        valid_ids = [
            "stt-whisper",
            "llm_handler",
            "plugin_v2",
            "a",
            "A-_Z",
        ]
        for plugin_id in valid_ids:
            is_valid, error = validate_plugin_id(plugin_id)
            assert is_valid is True, f"Failed: {plugin_id}"
            assert error is None

    def test_invalid_plugin_id_special_chars(self):
        """Plugin ID with special characters rejected."""
        is_valid, error = validate_plugin_id("plugin!")
        assert is_valid is False

    def test_invalid_plugin_id_too_long(self):
        """Plugin ID over 128 chars rejected."""
        is_valid, error = validate_plugin_id("a" * 129)
        assert is_valid is False


# ============================================================================
# Tenant ID Validator Tests
# ============================================================================


class TestValidateTenantId:
    """Test tenant_id validator."""

    def test_valid_tenant_id(self):
        """Valid tenant IDs pass."""
        valid_ids = [
            "_default",
            "customer_abc",
            "tenant_123",
            "a",
        ]
        for tenant_id in valid_ids:
            is_valid, error = validate_tenant_id(tenant_id)
            assert is_valid is True, f"Failed: {tenant_id}"
            assert error is None

    def test_invalid_tenant_id_uppercase(self):
        """Uppercase in tenant ID rejected."""
        is_valid, error = validate_tenant_id("Tenant")
        assert is_valid is False
        assert "lowercase" in error

    def test_invalid_tenant_id_hyphen(self):
        """Hyphen in tenant ID rejected."""
        is_valid, error = validate_tenant_id("tenant-1")
        assert is_valid is False


# ============================================================================
# Email Validator Tests
# ============================================================================


class TestValidateEmail:
    """Test email validator."""

    def test_valid_email(self):
        """Valid emails pass."""
        valid_emails = [
            "user@example.com",
            "test.user+tag@domain.co.uk",
            "a@b.co",
        ]
        for email in valid_emails:
            is_valid, error = validate_email(email)
            assert is_valid is True, f"Failed: {email}"
            assert error is None

    def test_invalid_email_no_at(self):
        """Email without @ rejected."""
        is_valid, error = validate_email("user.example.com")
        assert is_valid is False

    def test_invalid_email_no_domain(self):
        """Email without domain rejected."""
        is_valid, error = validate_email("user@")
        assert is_valid is False

    def test_invalid_email_no_tld(self):
        """Email without TLD rejected."""
        is_valid, error = validate_email("user@example")
        assert is_valid is False

    def test_invalid_email_not_string(self):
        """Non-string email rejected."""
        is_valid, error = validate_email(123)
        assert is_valid is False

    def test_invalid_email_too_long(self):
        """Email over 254 chars rejected."""
        is_valid, error = validate_email("a" * 255 + "@example.com")
        assert is_valid is False


# ============================================================================
# URL Validator Tests
# ============================================================================


class TestValidateUrl:
    """Test URL validator."""

    def test_valid_url(self):
        """Valid URLs pass."""
        valid_urls = [
            "https://example.com",
            "http://localhost:8080/path",
            "https://sub.domain.co.uk:443/api/v1",
        ]
        for url in valid_urls:
            is_valid, error = validate_url(url)
            assert is_valid is True, f"Failed: {url}"
            assert error is None

    def test_invalid_url_no_scheme(self):
        """URL without http(s) scheme rejected."""
        is_valid, error = validate_url("example.com")
        assert is_valid is False
        assert "http://" in error or "https://" in error

    def test_invalid_url_ftp_scheme(self):
        """FTP URLs rejected."""
        is_valid, error = validate_url("ftp://example.com")
        assert is_valid is False

    def test_invalid_url_not_string(self):
        """Non-string URL rejected."""
        is_valid, error = validate_url(123)
        assert is_valid is False


# ============================================================================
# UUID4 Validator Tests
# ============================================================================


class TestValidateUuid4:
    """Test UUID4 validator."""

    def test_valid_uuid4(self):
        """Valid UUID4s pass."""
        is_valid, error = validate_uuid4("550e8400-e29b-41d4-a716-446655440000")
        assert is_valid is True
        assert error is None

    def test_invalid_uuid4_wrong_version(self):
        """UUID with wrong version digit rejected."""
        is_valid, error = validate_uuid4("550e8400-e29b-31d4-a716-446655440000")  # version 3
        assert is_valid is False

    def test_invalid_uuid4_wrong_variant(self):
        """UUID with wrong variant rejected."""
        is_valid, error = validate_uuid4("550e8400-e29b-41d4-0716-446655440000")  # variant 0
        assert is_valid is False

    def test_invalid_uuid4_too_short(self):
        """Truncated UUID rejected."""
        is_valid, error = validate_uuid4("550e8400-e29b-41d4-a716")
        assert is_valid is False


# ============================================================================
# Port Validator Tests
# ============================================================================


class TestValidatePort:
    """Test port validator."""

    def test_valid_port(self):
        """Valid ports pass."""
        valid_ports = [1, 80, 443, 8080, 65535]
        for port in valid_ports:
            is_valid, error = validate_port(port)
            assert is_valid is True, f"Failed: {port}"
            assert error is None

    def test_invalid_port_zero(self):
        """Port 0 rejected."""
        is_valid, error = validate_port(0)
        assert is_valid is False

    def test_invalid_port_too_high(self):
        """Port > 65535 rejected."""
        is_valid, error = validate_port(65536)
        assert is_valid is False

    def test_invalid_port_not_int(self):
        """Non-integer port rejected."""
        is_valid, error = validate_port("8080")
        assert is_valid is False
        assert "must be integer" in error


# ============================================================================
# Alphanumeric Validator Tests
# ============================================================================


class TestValidateAlphanumeric:
    """Test alphanumeric validator."""

    def test_valid_alphanumeric(self):
        """Valid alphanumeric strings pass."""
        valid = ["abc123", "HELLO", "a", "Z9"]
        for value in valid:
            is_valid, error = validate_alphanumeric(value)
            assert is_valid is True, f"Failed: {value}"

    def test_invalid_alphanumeric_with_space(self):
        """Alphanumeric with space rejected."""
        is_valid, error = validate_alphanumeric("hello world")
        assert is_valid is False

    def test_invalid_alphanumeric_with_special(self):
        """Alphanumeric with special chars rejected."""
        is_valid, error = validate_alphanumeric("abc-123")
        assert is_valid is False


# ============================================================================
# Non-Empty String Validator Tests
# ============================================================================


class TestValidateNonEmptyString:
    """Test non-empty string validator."""

    def test_valid_non_empty_string(self):
        """Non-empty strings pass."""
        is_valid, error = validate_non_empty_string("hello")
        assert is_valid is True

    def test_invalid_empty_string(self):
        """Empty string rejected."""
        is_valid, error = validate_non_empty_string("")
        assert is_valid is False
        assert "cannot be empty" in error

    def test_valid_whitespace_string(self):
        """Whitespace-only string passes (not trimmed)."""
        is_valid, error = validate_non_empty_string("  ")
        assert is_valid is True


# ============================================================================
# String Length Validator Factory Tests
# ============================================================================


class TestValidateStringLength:
    """Test parameterized string length validator."""

    def test_string_length_validator_in_range(self):
        """String within range passes."""
        validator = validate_string_length(1, 5)
        is_valid, error = validator("abc")
        assert is_valid is True

    def test_string_length_validator_too_short(self):
        """String below minimum rejected."""
        validator = validate_string_length(5, 10)
        is_valid, error = validator("abc")
        assert is_valid is False

    def test_string_length_validator_too_long(self):
        """String above maximum rejected."""
        validator = validate_string_length(1, 3)
        is_valid, error = validator("abcdef")
        assert is_valid is False

    def test_string_length_validator_boundary(self):
        """Boundary values pass."""
        validator = validate_string_length(2, 4)
        is_valid, _ = validator("ab")
        assert is_valid is True
        is_valid, _ = validator("abcd")
        assert is_valid is True


# ============================================================================
# Integration Tests
# ============================================================================


class TestValidatorIntegration:
    """Integration tests using factory."""

    def test_factory_integration_all_validators_registered(self):
        """All validators are registered in factory."""
        expected = [
            "peer_id",
            "flag_id",
            "plugin_id",
            "tenant_id",
            "email",
            "url",
            "uuid4",
            "port",
            "alphanumeric",
            "non_empty_string",
        ]
        for validator_name in expected:
            assert FACTORY.has_validator(validator_name), f"{validator_name} not registered"

    def test_factory_integration_usage_pattern(self):
        """Test typical usage pattern."""
        # Simulate HTTP request validation
        peer_id = "my_device"
        is_valid, error = FACTORY.validate("peer_id", peer_id)
        assert is_valid is True
        assert error is None

        # Invalid case
        is_valid, error = FACTORY.validate("peer_id", "invalid-device")
        assert is_valid is False
        assert error is not None
