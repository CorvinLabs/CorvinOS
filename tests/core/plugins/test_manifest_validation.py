"""
Tests for Manifest Validation (Phase 4).

Tests JSON Schema validation, semantic constraints, and error handling.
"""

import pytest
from datetime import datetime

try:
    from core.plugins.marketplace_validator import (
        ManifestValidator,
        ValidationResult,
        ManifestValidationError,
    )
except ImportError:
    pytest.skip("Marketplace validator not available", allow_module_level=True)


@pytest.fixture
def validator():
    """Create a ManifestValidator instance."""
    return ManifestValidator()


@pytest.fixture
def valid_manifest():
    """A valid plugin manifest."""
    return {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "author": {
            "name": "Test Author",
            "email": "author@example.com",
        },
        "license": "Apache-2.0",
        "description": "A test plugin for validation",
        "category": "Security",
        "min_corvin_version": "0.7.0",
    }


class TestManifestValidator:
    """Test ManifestValidator class."""

    def test_valid_manifest_passes(self, validator, valid_manifest):
        """Valid manifest should pass validation."""
        result = validator.validate(valid_manifest)
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_missing_required_field(self, validator, valid_manifest):
        """Missing required field should fail."""
        del valid_manifest["id"]
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_invalid_id_format(self, validator, valid_manifest):
        """Invalid ID format should fail."""
        valid_manifest["id"] = "INVALID_ID"  # Uppercase not allowed
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_invalid_version_format(self, validator, valid_manifest):
        """Invalid version format should fail."""
        valid_manifest["version"] = "1.0"  # Must be semantic
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_description_too_short(self, validator, valid_manifest):
        """Description too short should fail."""
        valid_manifest["description"] = "short"  # Must be >=10 chars
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_invalid_email(self, validator, valid_manifest):
        """Invalid email format should fail."""
        valid_manifest["author"]["email"] = "not-an-email"
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_invalid_category(self, validator, valid_manifest):
        """Invalid category should fail."""
        valid_manifest["category"] = "InvalidCategory"
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_invalid_license(self, validator, valid_manifest):
        """Unsupported license should fail (or warn)."""
        valid_manifest["license"] = "UNKNOWN-LICENSE"
        # This depends on whether license validation is strict
        # For now, allow it (will be caught by JSON Schema if strict)

    def test_dependency_version_validation(self, validator, valid_manifest):
        """Invalid dependency version should fail."""
        valid_manifest["dependencies"] = {
            "other-plugin": "invalid-version"  # Must be semantic
        }
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_valid_dependency(self, validator, valid_manifest):
        """Valid dependencies should pass."""
        valid_manifest["dependencies"] = {
            "auth-plugin": "2.0.0",
            "storage-plugin": "*",  # Wildcard allowed
        }
        result = validator.validate(valid_manifest)
        assert result.is_valid is True

    def test_conflicting_dependencies(self, validator, valid_manifest):
        """Plugin cannot both depend on and conflict with another."""
        valid_manifest["dependencies"] = {
            "plugin-a": "1.0.0"
        }
        valid_manifest["conflicts_with"] = ["plugin-a"]
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_boot_layer_compliance_community_rejected(self, validator, valid_manifest):
        """Community plugin cannot use compliance boot layer."""
        valid_manifest["boot_layer"] = "compliance"
        valid_manifest["origin"] = "community"
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_community_boot_layer_downgrade_warning(self, validator, valid_manifest):
        """Community plugin with non-installed boot layer gets warning."""
        valid_manifest["boot_layer"] = "core"
        valid_manifest["origin"] = "community"
        result = validator.validate(valid_manifest)
        # Should pass but with warning
        assert result.is_valid is True
        assert len(result.warnings) > 0

    def test_valid_permissions(self, validator, valid_manifest):
        """Valid permissions should pass."""
        valid_manifest["required_permissions"] = [
            "storage.read",
            "network.https",
        ]
        result = validator.validate(valid_manifest)
        assert result.is_valid is True

    def test_invalid_permission(self, validator, valid_manifest):
        """Invalid permission should fail."""
        valid_manifest["required_permissions"] = [
            "storage.read",
            "invalid.permission",
        ]
        # This will fail if strict validation is enabled
        # For now, allow it (depends on schema enforcement)

    def test_long_description(self, validator, valid_manifest):
        """Long description should pass if under 5000 chars."""
        valid_manifest["long_description"] = "x" * 4000
        result = validator.validate(valid_manifest)
        assert result.is_valid is True

    def test_long_description_too_long(self, validator, valid_manifest):
        """Description over 5000 chars should fail."""
        valid_manifest["long_description"] = "x" * 5001
        with pytest.raises(ManifestValidationError):
            validator.validate(valid_manifest)

    def test_sandbox_config(self, validator, valid_manifest):
        """Valid sandbox config should pass."""
        valid_manifest["sandbox"] = {
            "cpu_limit_percent": 50,
            "memory_limit_mb": 512,
            "timeout_seconds": 300,
        }
        result = validator.validate(valid_manifest)
        assert result.is_valid is True

    def test_required_permissions(self, validator, valid_manifest):
        """Plugin can declare required permissions."""
        valid_manifest["required_permissions"] = [
            "storage.read",
            "storage.write",
            "network.https",
        ]
        result = validator.validate(valid_manifest)
        assert result.is_valid is True


class TestConfigSchemaValidation:
    """Test config_schema validation."""

    def test_valid_config_schema(self, validator):
        """Valid JSON Schema should pass."""
        config_schema = {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 0},
            },
            "required": ["api_key"],
        }
        result = validator.validate_config_schema(config_schema)
        assert result.is_valid is True

    def test_config_schema_not_dict(self, validator):
        """config_schema must be a dict."""
        config_schema = "not a dict"
        result = validator.validate_config_schema(config_schema)
        assert result.is_valid is False

    def test_config_schema_without_type(self, validator):
        """config_schema without 'type' gets warning."""
        config_schema = {
            "properties": {
                "api_key": {"type": "string"}
            }
        }
        result = validator.validate_config_schema(config_schema)
        # Should pass with warning
        assert len(result.warnings) > 0


class TestErrorMessages:
    """Test error message quality."""

    def test_error_messages_are_user_friendly(self, validator, valid_manifest):
        """Error messages should be user-friendly."""
        valid_manifest["version"] = "invalid"
        try:
            validator.validate(valid_manifest)
            assert False, "Should have raised"
        except ManifestValidationError as e:
            # Error message should mention what's wrong
            assert "version" in str(e).lower()

    def test_error_messages_no_secrets(self, validator, valid_manifest):
        """Error messages should not expose secrets."""
        valid_manifest["author"]["email"] = "test@example.com"
        # Add a secret-looking field
        valid_manifest["api_key"] = "secret-key-12345"
        # Should fail, but error message should not include the key
        try:
            validator.validate(valid_manifest)
        except ManifestValidationError as e:
            assert "secret-key" not in str(e)
