"""Tenant ID Validation (TENANT-002 Fix).

Centralizes tenant_id validation to prevent path traversal and other attacks.
Used by all skill_management modules that accept tenant_id parameter.
"""

import re
from typing import Optional


def validate_tenant_id(tenant_id: str) -> str:
    """Validate tenant ID (GDPR Art. 5 integrity, ADR-0007 multi-tenant axis).

    Ensures tenant_id cannot be used for path traversal or escape attempts.

    Args:
        tenant_id: Tenant identifier to validate

    Returns:
        The validated tenant_id (pass-through if valid)

    Raises:
        ValueError: If tenant_id is invalid (empty, contains path chars, etc.)
    """
    if not isinstance(tenant_id, str):
        raise ValueError(f"tenant_id must be string, got {type(tenant_id)}")

    if not tenant_id or len(tenant_id.strip()) == 0:
        raise ValueError("tenant_id cannot be empty")

    # Reject path traversal attempts
    if ".." in tenant_id or "/" in tenant_id or "\\" in tenant_id:
        raise ValueError(
            f"tenant_id contains path traversal characters: {tenant_id}"
        )

    # Only allow alphanumeric, underscore, and hyphen
    # Pattern: ^[a-zA-Z0-9_-]+$
    if not re.match(r"^[a-zA-Z0-9_-]+$", tenant_id):
        raise ValueError(
            f"tenant_id contains invalid characters: {tenant_id}. "
            f"Only alphanumeric, underscore, and hyphen allowed."
        )

    # Maximum length: 255 (typical filesystem limit)
    if len(tenant_id) > 255:
        raise ValueError(f"tenant_id exceeds maximum length (255): {tenant_id}")

    return tenant_id
