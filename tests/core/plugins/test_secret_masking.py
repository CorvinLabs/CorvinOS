"""
Tests for Secret Masking (Phase 4).

Ensures API keys, tokens, and passwords are masked in audit logs.
"""

import pytest

try:
    from core.plugins.marketplace_secrets import (
        is_secret_like,
        mask_secret,
        sanitize_for_audit,
        contains_secrets,
        audit_line_is_safe,
        validate_audit_safe,
    )
except ImportError:
    pytest.skip("Secret masking not available", allow_module_level=True)


class TestSecretDetection:
    """Test secret detection patterns."""

    def test_api_key_detected(self):
        """Should detect api_key field."""
        assert is_secret_like("api_key") is True
        assert is_secret_like("apiKey") is True
        assert is_secret_like("API_KEY") is True

    def test_token_detected(self):
        """Should detect token field."""
        assert is_secret_like("token") is True
        assert is_secret_like("auth_token") is True
        assert is_secret_like("access_token") is True

    def test_password_detected(self):
        """Should detect password field."""
        assert is_secret_like("password") is True
        assert is_secret_like("passwd") is True
        assert is_secret_like("pwd") is True

    def test_secret_detected(self):
        """Should detect secret field."""
        assert is_secret_like("secret") is True
        assert is_secret_like("client_secret") is True

    def test_regular_field_not_detected(self):
        """Regular fields should not be detected as secrets."""
        assert is_secret_like("name") is False
        assert is_secret_like("description") is False
        assert is_secret_like("version") is False


class TestSecretMasking:
    """Test secret masking."""

    def test_mask_string_secret(self):
        """Should mask string secrets."""
        masked = mask_secret("my-secret-key")
        assert masked.startswith("sha256:")
        assert "secret" not in masked

    def test_mask_preserves_type_info(self):
        """Masked value should be hashable."""
        masked = mask_secret("api-key-123")
        assert isinstance(masked, str)
        assert masked.startswith("sha256:")

    def test_mask_null_secret(self):
        """Should handle None values."""
        masked = mask_secret(None)
        assert masked == "sha256:null"

    def test_mask_non_string(self):
        """Should convert non-strings to string before masking."""
        masked = mask_secret(12345)
        assert masked.startswith("sha256:")

    def test_consistent_masking(self):
        """Same secret should produce same mask."""
        secret = "api-key-123"
        mask1 = mask_secret(secret)
        mask2 = mask_secret(secret)
        assert mask1 == mask2


class TestSanitizeForAudit:
    """Test audit log sanitization."""

    def test_sanitize_dict_with_secrets(self):
        """Dict with secrets should be sanitized."""
        config = {
            "name": "test-plugin",
            "api_key": "secret-key-123",
            "version": "1.0.0",
        }
        sanitized = sanitize_for_audit(config)
        assert sanitized["name"] == "test-plugin"
        assert sanitized["version"] == "1.0.0"
        assert sanitized["api_key"].startswith("sha256:")
        assert "secret-key" not in sanitized["api_key"]

    def test_sanitize_nested_dict(self):
        """Nested dicts should be sanitized."""
        config = {
            "plugin": {
                "name": "test",
                "auth": {
                    "token": "secret-token-xyz",
                }
            }
        }
        sanitized = sanitize_for_audit(config)
        assert sanitized["plugin"]["name"] == "test"
        assert sanitized["plugin"]["auth"]["token"].startswith("sha256:")

    def test_sanitize_list(self):
        """Lists should be sanitized."""
        config = {
            "dependencies": [
                {"name": "dep1", "password": "pass123"},
                {"name": "dep2", "password": "pass456"},
            ]
        }
        sanitized = sanitize_for_audit(config)
        for dep in sanitized["dependencies"]:
            assert dep["password"].startswith("sha256:")

    def test_sanitize_preserves_non_secrets(self):
        """Non-secret fields should be preserved."""
        config = {
            "id": "my-plugin",
            "version": "1.0.0",
            "description": "A test plugin",
            "rating": 4.5,
        }
        sanitized = sanitize_for_audit(config)
        assert sanitized["id"] == "my-plugin"
        assert sanitized["version"] == "1.0.0"
        assert sanitized["description"] == "A test plugin"
        assert sanitized["rating"] == 4.5


class TestSecretDetectionInText:
    """Test secret detection in free text."""

    def test_contains_secrets_with_api_key_pattern(self):
        """Should detect api_key patterns in text."""
        text = '"api_key": "secret-key-123"'
        assert contains_secrets(text) is True

    def test_contains_secrets_with_token_pattern(self):
        """Should detect token patterns in text."""
        text = 'auth_token="ghp_abc123def456ghi789jkl"'
        assert contains_secrets(text) is True

    def test_contains_long_alphanumeric(self):
        """Should detect long alphanumeric sequences (likely tokens)."""
        text = "abcd1234efgh5678ijkl9012mnop3456qrst7890uvwx"
        assert contains_secrets(text) is True

    def test_regular_text_no_secrets(self):
        """Regular text should not trigger detection."""
        text = "This is a normal plugin description without secrets"
        assert contains_secrets(text) is False


class TestAuditLineSafety:
    """Test audit line safety validation."""

    def test_safe_line(self):
        """Normal log line should be safe."""
        line = "Plugin 'test-plugin' installed successfully"
        assert audit_line_is_safe(line) is True

    def test_unsafe_line_with_token(self):
        """Line with token should be unsafe."""
        line = "Connecting to GitHub with token ghp_abc123def456ghi789jkl"
        assert audit_line_is_safe(line) is False

    def test_empty_line_is_safe(self):
        """Empty line should be safe."""
        assert audit_line_is_safe("") is True

    def test_none_is_safe(self):
        """None should be safe."""
        assert audit_line_is_safe(None) is True


class TestAuditEventValidation:
    """Test audit event safety validation."""

    def test_safe_event(self):
        """Event with no secrets should be safe."""
        event = {
            "event_type": "install",
            "plugin_id": "test-plugin",
            "status": "success",
        }
        is_safe, errors = validate_audit_safe(event)
        assert is_safe is True
        assert len(errors) == 0

    def test_unsafe_event_with_raw_secret(self):
        """Event with raw secret in field should be unsafe."""
        event = {
            "event_type": "config_update",
            "plugin_id": "test-plugin",
            "api_key": "secret-key-123",  # Not masked
        }
        is_safe, errors = validate_audit_safe(event)
        assert is_safe is False
        assert len(errors) > 0

    def test_safe_event_with_masked_secret(self):
        """Event with masked secret should be safe."""
        event = {
            "event_type": "config_update",
            "plugin_id": "test-plugin",
            "api_key": "sha256:abc123def456",  # Masked
        }
        is_safe, errors = validate_audit_safe(event)
        assert is_safe is True

    def test_unsafe_nested_secret(self):
        """Nested unmasked secret should be unsafe."""
        event = {
            "event_type": "install",
            "config": {
                "auth": {
                    "token": "raw-token-value"
                }
            }
        }
        is_safe, errors = validate_audit_safe(event)
        assert is_safe is False
        assert len(errors) > 0

    def test_safe_nested_masked_secret(self):
        """Nested masked secret should be safe."""
        event = {
            "event_type": "install",
            "config": {
                "auth": {
                    "token": "sha256:masked123"
                }
            }
        }
        is_safe, errors = validate_audit_safe(event)
        assert is_safe is True


class TestEndToEndAuditSafety:
    """End-to-end audit safety scenarios."""

    def test_install_plugin_with_config(self):
        """Installing plugin with API key config should mask the key."""
        plugin_config = {
            "name": "GitHub Integration",
            "github_api_key": "ghp_abc123def456ghi789jkl",
            "timeout": 30,
        }

        sanitized = sanitize_for_audit(plugin_config)

        # Verify original secret is not in sanitized
        sanitized_str = str(sanitized)
        assert "ghp_abc123def456ghi789jkl" not in sanitized_str

        # Verify masked key is present
        assert sanitized["github_api_key"].startswith("sha256:")

    def test_multiple_secrets_all_masked(self):
        """Plugin with multiple secrets should mask all."""
        config = {
            "name": "Auth Plugin",
            "api_key": "key-123",
            "token": "token-456",
            "password": "pass-789",
            "client_secret": "secret-000",
        }

        sanitized = sanitize_for_audit(config)

        # All secret fields should be masked
        for field in ["api_key", "token", "password", "client_secret"]:
            assert sanitized[field].startswith("sha256:")
            assert field not in sanitized[field]  # Field name shouldn't be in value
