"""Tenant management and validation (ADR-0007 multi-tenant axis).

This module provides fail-closed tenant ID and session ID validation to prevent
path traversal and other security issues. Every tenant-scoped operation must
validate its tenant_id and session_id before constructing paths.

GDPR Art. 5, 6, 32 — tenant isolation is a data protection boundary.
"""

from .validation import (
    CHANNEL_ID_REGEX,
    RESERVED_TENANT_NAMES,
    SESSION_ID_MAX_LEN,
    TENANT_ID_REGEX,
    validate_channel_id,
    validate_session_id,
    validate_tenant_id,
)

__all__ = [
    "validate_tenant_id",
    "validate_session_id",
    "validate_channel_id",
    "TENANT_ID_REGEX",
    "CHANNEL_ID_REGEX",
    "SESSION_ID_MAX_LEN",
    "RESERVED_TENANT_NAMES",
]
