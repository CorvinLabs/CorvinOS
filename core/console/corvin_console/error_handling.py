"""Error handling utilities for safe error response wrapping.

Prevents information disclosure by sanitizing error messages:
- Never expose raw exception text to clients
- Never include stack traces, file paths, or internal details
- Return safe, user-friendly error messages
- Log full errors internally for debugging
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def safe_error_response(
    error: Exception,
    message: str = "An error occurred",
    log_level: str = "error"
) -> str:
    """
    Create a safe error response message that doesn't expose internals.

    Args:
        error: The exception that occurred
        message: Safe user-facing message to return
        log_level: Log level for internal logging (error, warning, info)

    Returns:
        Safe error message for client response

    Example:
        >>> try:
        ...     do_something()
        ... except ValueError as e:
        ...     safe_msg = safe_error_response(e, "Failed to process request")
        ...     raise HTTPException(status_code=400, detail=safe_msg)
    """
    # Log the full error internally for debugging
    log_func = getattr(logger, log_level, logger.error)
    log_func(f"Internal error: {type(error).__name__}: {str(error)}", exc_info=True)

    # Return safe message to client (no exception details)
    return message


def safe_snapshot_error(error: Exception) -> str:
    """Safe error response for snapshot operations."""
    if isinstance(error, ValueError):
        # Map common ValueError messages to safe versions
        msg = str(error).lower()
        if "not found" in msg:
            return "Snapshot not found or access denied"
        elif "mismatch" in msg or "corrupted" in msg:
            return "Snapshot integrity check failed"
        elif "access" in msg:
            return "Access denied to this snapshot"

    return "Failed to complete snapshot operation"


def safe_plugin_error(error: Exception) -> str:
    """Safe error response for plugin operations."""
    if isinstance(error, ValueError):
        msg = str(error).lower()
        if "not found" in msg:
            return "Plugin not found"
        elif "already" in msg or "exists" in msg:
            return "Plugin already installed or in use"
        elif "dependency" in msg:
            return "Cannot complete operation due to plugin dependencies"

    return "Failed to complete plugin operation"


def safe_subsystem_error(error: Exception) -> str:
    """Safe error response for subsystem operations."""
    if isinstance(error, ValueError):
        msg = str(error).lower()
        if "not found" in msg:
            return "Subsystem not found"
        elif "already" in msg:
            return "Subsystem already configured"

    return "Failed to complete subsystem operation"


def safe_override_error(error: Exception) -> str:
    """Safe error response for override authority operations."""
    if isinstance(error, ValueError):
        msg = str(error).lower()
        if "not found" in msg:
            return "Override request not found"
        elif "permission" in msg or "denied" in msg:
            return "Operation not permitted or access denied"

    return "Failed to complete override operation"
