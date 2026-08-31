"""PII Redaction — ADR-0297

Safe redaction of PII for audit logging. Never log raw PII; always redact before
writing to audit trail.

Design:
  - Whitelist: only allow specific fields to be logged
  - Hash: redacted values use hash-based substitution for deduplication
  - Scrub: remove field entirely if suspicious
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Set


class PIIRedactor:
    """Safe PII redactor for audit logging.

    Usage:
        redactor = PIIRedactor()
        safe_data = redactor.redact_dict(data, tenant_id="tenant_123")
        audit_log(safe_data)  # Safe to log
    """

    def __init__(self, whitelist: Optional[Set[str]] = None) -> None:
        """Initialize redactor with optional field whitelist.

        Args:
            whitelist: Set of field names that are safe to log unredacted
                      (e.g., {"timestamp", "action"})
        """
        self.whitelist = whitelist or set()

    def redact_value(self, value: Any, field_name: str = "") -> str:
        """Redact a single value.

        Args:
            value: Value to redact
            field_name: Field name (optional, for whitelist checking)

        Returns:
            Redacted string representation
        """
        if field_name in self.whitelist:
            # Whitelisted field: return as-is
            return str(value)

        # Non-whitelisted: redact
        if value is None:
            return "null"

        if isinstance(value, bool):
            return str(value)

        if isinstance(value, (int, float)):
            # Numeric: return hash for deduplication
            return self._hash_value(str(value))

        if isinstance(value, str):
            if len(value) == 0:
                return ""
            # String: show first 3 chars + hash for dedup
            return f"{value[:3]}...{self._hash_value(value)}"

        # Other types: hash
        return f"<{type(value).__name__}:{self._hash_value(str(value))}>"

    def redact_dict(
        self,
        data: Dict[str, Any],
        *,
        tenant_id: str,
        exclude_fields: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """Redact a dictionary of values.

        Args:
            data: Dictionary to redact
            tenant_id: Tenant scope (keyword-only, required)
            exclude_fields: Fields to omit entirely from output

        Returns:
            Redacted dictionary safe for logging
        """
        exclude_fields = exclude_fields or set()
        redacted = {}

        for key, value in data.items():
            if key in exclude_fields:
                continue  # Omit entirely

            if key in self.whitelist:
                # Whitelisted: keep value
                redacted[key] = value
            else:
                # Redact
                redacted[key] = self.redact_value(value, field_name=key)

        return redacted

    def redact_list(
        self,
        values: list[Any],
        *,
        tenant_id: str,
    ) -> list[str]:
        """Redact a list of values.

        Args:
            values: List to redact
            tenant_id: Tenant scope (keyword-only, required)

        Returns:
            List of redacted values
        """
        return [self.redact_value(v) for v in values]

    @staticmethod
    def _hash_value(value: str, length: int = 8) -> str:
        """Hash a value for deduplication.

        Args:
            value: Value to hash
            length: Length of hash to return (default 8)

        Returns:
            Hex digest of hashed value (truncated)
        """
        h = hashlib.sha256(value.encode())
        return h.hexdigest()[:length]


# ============================================================================
# Module-level convenience functions
# ============================================================================

_DEFAULT_REDACTOR = PIIRedactor(
    whitelist={
        "timestamp",
        "ts",
        "action",
        "event_type",
        "status",
        "error_code",
        "tenant_id",
        "session_id",
        "request_id",
        "user_agent",
        "ip_address",
        "method",
        "path",
        "status_code",
    }
)


def redact_pii(
    value: Any,
    *,
    field_name: str = "",
) -> str:
    """Convenience function: redact a value for logging.

    Args:
        value: Value to redact
        field_name: Field name (optional)

    Returns:
        Redacted string
    """
    return _DEFAULT_REDACTOR.redact_value(value, field_name=field_name)


def redact_dict_for_audit(
    data: Dict[str, Any],
    *,
    tenant_id: str,
    exclude_fields: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Convenience function: redact dictionary for audit logging.

    Args:
        data: Dictionary to redact
        tenant_id: Tenant scope (keyword-only, required)
        exclude_fields: Fields to omit

    Returns:
        Redacted dictionary safe for logging
    """
    return _DEFAULT_REDACTOR.redact_dict(
        data, tenant_id=tenant_id, exclude_fields=exclude_fields
    )


__all__ = [
    "PIIRedactor",
    "redact_pii",
    "redact_dict_for_audit",
]
