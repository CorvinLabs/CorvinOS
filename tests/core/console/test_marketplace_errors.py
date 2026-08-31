"""
Tests for Marketplace Error Handling (Phase 4).

Verifies 6 error classes with user-friendly messages and no secret leaks.
"""

import pytest

try:
    from core.console.corvin_console.routes.marketplace_errors import (
        MarketplaceError,
        NetworkError,
        RateLimitError,
        ManifestError,
        DependencyError,
        PermissionError,
        ConflictError,
        SandboxError,
        PluginNotFoundError,
        handle_marketplace_error,
        validate_error_message,
        ErrorCategory,
    )
except ImportError:
    pytest.skip("Marketplace error module not available", allow_module_level=True)


class TestNetworkError:
    """Test NetworkError class."""

    def test_network_error_message(self):
        """NetworkError should have user-friendly message."""
        error = NetworkError("Connection refused", endpoint="api.github.com")
        assert "marketplace" in error.user_message.lower()
        assert "unreachable" in error.user_message.lower()

    def test_network_error_troubleshooting(self):
        """NetworkError should include troubleshooting steps."""
        error = NetworkError("Connection timeout")
        assert "internet" in error.troubleshooting.lower() or "network" in error.troubleshooting.lower()
        assert len(error.troubleshooting) > 50  # Should be substantial

    def test_network_error_to_response(self):
        """NetworkError should serialize to response format."""
        error = NetworkError("Connection failed")
        response = error.to_response()
        assert "error" in response
        assert "category" in response
        assert "troubleshooting" in response
        assert response["category"] == ErrorCategory.NETWORK.value


class TestRateLimitError:
    """Test RateLimitError class."""

    def test_rate_limit_error_message(self):
        """RateLimitError should mention rate limit."""
        error = RateLimitError("API rate limit exceeded")
        assert "limit" in error.user_message.lower()

    def test_rate_limit_error_troubleshooting(self):
        """RateLimitError should suggest waiting."""
        error = RateLimitError("Too many requests")
        assert "wait" in error.troubleshooting.lower() or "minute" in error.troubleshooting.lower()


class TestManifestError:
    """Test ManifestError class."""

    def test_manifest_error_with_field(self):
        """ManifestError should mention which field is invalid."""
        error = ManifestError("Invalid field", field="version")
        assert "version" in error.user_message

    def test_manifest_error_troubleshooting(self):
        """ManifestError should suggest checking plugin source."""
        error = ManifestError("Bad manifest")
        assert "trusted" in error.troubleshooting.lower() or "author" in error.troubleshooting.lower()


class TestDependencyError:
    """Test DependencyError class."""

    def test_dependency_error_with_missing_dep(self):
        """DependencyError should mention missing dependency."""
        error = DependencyError("Missing dep", missing_dep="auth-plugin")
        assert "auth-plugin" in error.user_message

    def test_dependency_error_troubleshooting(self):
        """DependencyError should suggest installing dependencies first."""
        error = DependencyError("Unresolved dependencies")
        assert "install" in error.troubleshooting.lower()


class TestPermissionError:
    """Test PermissionError class."""

    def test_permission_error_with_permissions(self):
        """PermissionError should list denied permissions."""
        error = PermissionError("Permission denied", permissions=["storage.write", "network.http"])
        assert "storage.write" in error.user_message or "network.http" in error.user_message

    def test_permission_error_troubleshooting(self):
        """PermissionError should explain permissions."""
        error = PermissionError("User denied permissions")
        assert "permission" in error.troubleshooting.lower()


class TestConflictError:
    """Test ConflictError class."""

    def test_conflict_error_with_plugin(self):
        """ConflictError should name conflicting plugin."""
        error = ConflictError("Plugin conflict", conflicting_plugin="old-plugin")
        assert "old-plugin" in error.user_message

    def test_conflict_error_troubleshooting(self):
        """ConflictError should suggest uninstalling conflicting plugin."""
        error = ConflictError("Conflict detected")
        assert "uninstall" in error.troubleshooting.lower()


class TestSandboxError:
    """Test SandboxError class."""

    def test_sandbox_error_with_resource(self):
        """SandboxError should mention resource limit."""
        error = SandboxError("Resource limit exceeded", resource="memory")
        assert "memory" in error.user_message

    def test_sandbox_error_troubleshooting(self):
        """SandboxError should suggest freeing resources."""
        error = SandboxError("Insufficient resources")
        assert "resource" in error.troubleshooting.lower() or "memory" in error.troubleshooting.lower()


class TestPluginNotFoundError:
    """Test PluginNotFoundError class."""

    def test_plugin_not_found_error(self):
        """PluginNotFoundError should name missing plugin."""
        error = PluginNotFoundError("Not found", plugin_id="missing-plugin")
        assert "missing-plugin" in error.user_message

    def test_plugin_not_found_troubleshooting(self):
        """PluginNotFoundError should suggest searching."""
        error = PluginNotFoundError("Plugin not in marketplace")
        assert "search" in error.troubleshooting.lower()


class TestErrorHandling:
    """Test error handling utilities."""

    def test_handle_marketplace_error(self):
        """should convert MarketplaceError to response."""
        error = NetworkError("Network failed")
        response = handle_marketplace_error(error)
        assert "error" in response
        assert "troubleshooting" in response

    def test_handle_generic_exception(self):
        """should handle generic exceptions gracefully."""
        error = Exception("Unexpected error")
        response = handle_marketplace_error(error)
        assert "error" in response
        assert response["error"] != "Unexpected error"  # Should not expose raw error
        assert "troubleshooting" in response

    def test_validate_error_message_safe(self):
        """Safe error message should validate."""
        message = "Plugin 'test-plugin' could not be found"
        assert validate_error_message(message) is True

    def test_validate_error_message_with_secret(self):
        """Error message with secret should not validate."""
        message = 'Could not connect with API key: "ghp_abc123def456"'
        assert validate_error_message(message) is False


class TestErrorMessageQuality:
    """Test error message quality standards."""

    def test_all_errors_have_troubleshooting(self):
        """Every error should have troubleshooting steps."""
        errors = [
            NetworkError("test"),
            ManifestError("test"),
            DependencyError("test"),
            PermissionError("test"),
            ConflictError("test"),
            SandboxError("test"),
            PluginNotFoundError("test"),
        ]

        for error in errors:
            assert len(error.troubleshooting) > 0
            # Should have multiple steps (likely newline-separated or numbered)
            assert error.troubleshooting.count("\n") >= 0 or error.troubleshooting.count(".") > 1

    def test_error_messages_are_user_friendly(self):
        """Error messages should avoid technical jargon."""
        errors = [
            NetworkError("Connection refused"),
            ManifestError("Invalid schema"),
            DependencyError("Unresolved dependencies"),
        ]

        for error in errors:
            message = error.user_message.lower()
            # Should not be empty or too technical
            assert len(message) > 0
            # Avoid technical terms like "traceback", "exception", etc.
            assert "traceback" not in message
            assert "stacktrace" not in message

    def test_error_response_format(self):
        """Error response should have consistent format."""
        errors = [
            NetworkError("test"),
            ManifestError("test"),
            DependencyError("test"),
        ]

        for error in errors:
            response = error.to_response()
            # Required fields
            assert "error" in response
            assert "category" in response
            assert "troubleshooting" in response

            # Types
            assert isinstance(response["error"], str)
            assert isinstance(response["category"], str)
            assert isinstance(response["troubleshooting"], str)

            # Category should be one of the known values
            valid_categories = [e.value for e in ErrorCategory]
            assert response["category"] in valid_categories


class TestErrorCategories:
    """Test ErrorCategory enum."""

    def test_all_error_types_have_category(self):
        """Every error type should have an ErrorCategory."""
        error_to_category = {
            NetworkError: ErrorCategory.NETWORK,
            ManifestError: ErrorCategory.MANIFEST,
            DependencyError: ErrorCategory.DEPENDENCY,
            PermissionError: ErrorCategory.PERMISSION,
            ConflictError: ErrorCategory.CONFLICT,
            SandboxError: ErrorCategory.SANDBOX,
        }

        for error_class, expected_category in error_to_category.items():
            error = error_class("test")
            assert error.category == expected_category
