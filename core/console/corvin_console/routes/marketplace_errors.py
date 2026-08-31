"""
Error Handling for Marketplace Routes (Phase 4).

Defines 6 error classes with user-friendly messages and troubleshooting steps.
Ensures no secrets leak in error responses.

ADR-0385 Phase 4: Production Hardening
"""

import logging
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """6 error categories for marketplace operations."""
    NETWORK = "network"
    MANIFEST = "manifest"
    DEPENDENCY = "dependency"
    PERMISSION = "permission"
    CONFLICT = "conflict"
    SANDBOX = "sandbox"


class MarketplaceError(Exception):
    """Base class for marketplace errors."""

    category: ErrorCategory
    user_message: str
    troubleshooting: str

    def __init__(
        self,
        message: str,
        user_message: Optional[str] = None,
        troubleshooting: Optional[str] = None,
    ):
        self.message = message
        self.user_message = user_message or self._default_user_message()
        self.troubleshooting = troubleshooting or self._default_troubleshooting()
        super().__init__(message)

    def _default_user_message(self) -> str:
        return "An error occurred. Please try again."

    def _default_troubleshooting(self) -> str:
        return "If the problem persists, contact your administrator."

    def to_response(self) -> Dict[str, Any]:
        """Convert to JSON response."""
        return {
            "error": self.user_message,
            "category": self.category.value,
            "troubleshooting": self.troubleshooting,
        }


class NetworkError(MarketplaceError):
    """Network connectivity or API errors."""

    category = ErrorCategory.NETWORK

    def __init__(self, message: str, endpoint: Optional[str] = None):
        user_msg = f"Could not reach the marketplace service."
        if endpoint:
            user_msg += f" ({endpoint} is unreachable)"

        troubleshooting = (
            "1. Check your internet connection.\n"
            "2. If you are on a restricted network, contact your IT admin.\n"
            "3. Try again in a few minutes (the service may be temporarily unavailable)."
        )

        super().__init__(message, user_message=user_msg, troubleshooting=troubleshooting)


class RateLimitError(NetworkError):
    """GitHub API rate limit exceeded."""

    def __init__(self, message: str):
        user_msg = "Marketplace request limit exceeded. Please wait a moment and try again."
        troubleshooting = (
            "1. Wait at least 1 minute before retrying.\n"
            "2. If you are using GitHub API tokens, verify the token is valid.\n"
            "3. Contact your administrator if this persists (rate limit pool exhausted)."
        )
        # Skip the parent __init__ to avoid modifying endpoint
        MarketplaceError.__init__(self, message, user_message=user_msg, troubleshooting=troubleshooting)


class ManifestError(MarketplaceError):
    """Invalid plugin manifest."""

    category = ErrorCategory.MANIFEST

    def __init__(self, message: str, field: Optional[str] = None):
        if field:
            user_msg = f"Plugin manifest is invalid (missing or invalid: {field})."
        else:
            user_msg = "Plugin manifest is invalid."

        troubleshooting = (
            "1. Check that the plugin is from a trusted source.\n"
            "2. Try reinstalling the plugin.\n"
            "3. If the problem persists, report it to the plugin author."
        )

        super().__init__(message, user_message=user_msg, troubleshooting=troubleshooting)


class DependencyError(MarketplaceError):
    """Plugin dependency resolution failed."""

    category = ErrorCategory.DEPENDENCY

    def __init__(self, message: str, missing_dep: Optional[str] = None):
        if missing_dep:
            user_msg = f"Plugin requires '{missing_dep}' which is not installed."
        else:
            user_msg = "Plugin has unresolved dependencies."

        troubleshooting = (
            "1. Install required dependencies first (check the plugin's description).\n"
            "2. Verify all dependencies are compatible with your CorvinOS version.\n"
            "3. Contact the plugin author if dependencies are unclear."
        )

        super().__init__(message, user_message=user_msg, troubleshooting=troubleshooting)


class PermissionError(MarketplaceError):
    """User rejected required permissions."""

    category = ErrorCategory.PERMISSION

    def __init__(self, message: str, permissions: Optional[list] = None):
        if permissions:
            user_msg = f"Plugin was not installed: you denied required permissions ({', '.join(permissions)})."
        else:
            user_msg = "Plugin installation was cancelled due to missing permissions."

        troubleshooting = (
            "1. Review the plugin's required permissions.\n"
            "2. If you trust the plugin, reinstall it and grant permissions.\n"
            "3. Contact your administrator if you have questions about a permission."
        )

        super().__init__(message, user_message=user_msg, troubleshooting=troubleshooting)


class ConflictError(MarketplaceError):
    """Plugin conflicts with existing installation."""

    category = ErrorCategory.CONFLICT

    def __init__(self, message: str, conflicting_plugin: Optional[str] = None):
        if conflicting_plugin:
            user_msg = f"Plugin conflicts with '{conflicting_plugin}' which is already installed."
        else:
            user_msg = "Plugin conflicts with an already-installed plugin."

        troubleshooting = (
            "1. Uninstall the conflicting plugin first.\n"
            "2. Then install the new plugin.\n"
            "3. If both plugins are needed, contact the plugin authors to resolve the conflict."
        )

        super().__init__(message, user_message=user_msg, troubleshooting=troubleshooting)


class SandboxError(MarketplaceError):
    """Sandbox resource limits exceeded."""

    category = ErrorCategory.SANDBOX

    def __init__(self, message: str, resource: Optional[str] = None):
        if resource:
            user_msg = f"Plugin cannot be installed: insufficient {resource}."
        else:
            user_msg = "Plugin cannot be installed: resource limits exceeded."

        troubleshooting = (
            "1. Check available system resources (CPU, memory, disk).\n"
            "2. Close other applications to free up resources.\n"
            "3. Contact your administrator if resource limits are too restrictive."
        )

        super().__init__(message, user_message=user_msg, troubleshooting=troubleshooting)


class PluginNotFoundError(MarketplaceError):
    """Plugin not found in marketplace."""

    category = ErrorCategory.MANIFEST

    def __init__(self, message: str, plugin_id: Optional[str] = None):
        if plugin_id:
            user_msg = f"Plugin '{plugin_id}' not found in the marketplace."
        else:
            user_msg = "Plugin not found."

        troubleshooting = (
            "1. Check the plugin ID spelling.\n"
            "2. Search the marketplace to find similar plugins.\n"
            "3. Verify the plugin is available for your CorvinOS version."
        )

        super().__init__(message, user_message=user_msg, troubleshooting=troubleshooting)


def handle_marketplace_error(error: Exception) -> Dict[str, Any]:
    """
    Convert any exception to a marketplace error response.

    Ensures no secrets leak in the response.

    Args:
        error: Exception to handle

    Returns:
        JSON-serializable dict with user message and troubleshooting
    """
    if isinstance(error, MarketplaceError):
        return error.to_response()

    # Generic error
    logger.error(f"Unhandled marketplace error: {error}", exc_info=True)
    return {
        "error": "An unexpected error occurred. Please try again.",
        "category": "unknown",
        "troubleshooting": "If the problem persists, contact your administrator.",
    }


def validate_error_message(message: str) -> bool:
    """
    Check if an error message is safe to send (no secrets exposed).

    Args:
        message: Error message text

    Returns:
        True if message is safe, False if it likely contains secrets
    """
    from core.plugins.marketplace_secrets import contains_secrets
    return not contains_secrets(message)
