"""
Secret Masking for Marketplace Audit Trail (Phase 4).

Prevents API keys, tokens, and passwords from being logged to audit trail.
All secrets are hashed before serialization.

ADR-0385 Phase 4: Security Hardening
"""

import hashlib
import logging
import re
from typing import Any, Dict, List
from dataclasses import asdict

logger = logging.getLogger(__name__)


class SecretPatterns:
    """Common patterns for detecting secrets."""

    PATTERNS = {
        "api_key": r"(?i)(?:api[_-]?key|apikey|api_secret|access_key)",
        "token": r"(?i)(?:token|auth_token|access_token|refresh_token|bearer)",
        "password": r"(?i)(?:password|passwd|pwd|pass)",
        "secret": r"(?i)(?:secret|client_secret|signing_key)",
        "credential": r"(?i)(?:credential|cred|auth)",
        "oauth": r"(?i)(?:oauth|client_id|client_secret|consumer_key|consumer_secret)",
    }

    COMPILED = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}


def is_secret_like(key: str) -> bool:
    """
    Heuristically detect if a key name looks like a secret.

    Args:
        key: Field name or key

    Returns:
        True if the key matches secret patterns
    """
    for pattern in SecretPatterns.COMPILED.values():
        if pattern.search(key):
            return True
    return False


def mask_secret(value: Any, key_name: str = "") -> str:
    """
    Mask a secret value with a hash.

    Args:
        value: The secret value (any type)
        key_name: Optional field name (for logging)

    Returns:
        Hashed secret as 'sha256:abcd1234...'
    """
    if value is None:
        return "sha256:null"

    # Convert to string
    if not isinstance(value, str):
        value_str = str(value)
    else:
        value_str = value

    # Hash it
    hash_obj = hashlib.sha256(value_str.encode("utf-8"))
    hash_hex = hash_obj.hexdigest()[:16]  # First 16 chars for readability

    logger.debug(f"Masked secret: {key_name or 'unknown'} -> sha256:{hash_hex}")

    return f"sha256:{hash_hex}"


def sanitize_for_audit(obj: Any) -> Any:
    """
    Recursively sanitize an object for audit logging.

    Replaces secret values with hashes. Works on:
    - Dataclasses: converts to dict, sanitizes values
    - Dicts: recursively sanitizes values
    - Lists: recursively sanitizes items
    - Strings: hashes if key name looks like a secret

    Args:
        obj: Object to sanitize (dataclass, dict, list, or string)

    Returns:
        Sanitized object with secrets masked
    """
    return _sanitize_recursive(obj, parent_key="")


def _sanitize_recursive(obj: Any, parent_key: str = "") -> Any:
    """Recursive helper for sanitize_for_audit."""

    # Handle dataclasses
    if hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)

    # Handle dicts
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if is_secret_like(key):
                # This key looks like a secret, mask it
                result[key] = mask_secret(value, key)
            else:
                # Recurse into nested structures
                result[key] = _sanitize_recursive(value, parent_key=key)
        return result

    # Handle lists
    if isinstance(obj, list):
        return [_sanitize_recursive(item, parent_key=parent_key) for item in obj]

    # Handle primitives
    return obj


def contains_secrets(text: str) -> bool:
    """
    Check if text contains any apparent secrets.

    Looks for common patterns like:
    - "api_key": "..."
    - "token": "..."
    - Long alphanumeric strings that look like keys

    Args:
        text: Text to check

    Returns:
        True if apparent secrets found
    """
    # Check for key patterns followed by values
    for pattern in SecretPatterns.COMPILED.values():
        if pattern.search(text):
            return True

    # Check for long alphanumeric sequences (possible tokens)
    if re.search(r'[a-zA-Z0-9]{32,}', text):
        return True

    return False


def audit_line_is_safe(line: str) -> bool:
    """
    Check if an audit log line is safe to write (no secrets exposed).

    Args:
        line: Audit log line

    Returns:
        False if secrets detected, True otherwise
    """
    if not line:
        return True

    return not contains_secrets(line)


def validate_audit_safe(audit_event: Dict[str, Any]) -> tuple[bool, List[str]]:
    """
    Validate that an audit event is safe to write (no secrets).

    Args:
        audit_event: Event dict from audit trail

    Returns:
        (is_safe, error_messages) tuple
    """
    errors = []

    # Check each field
    for key, value in audit_event.items():
        if is_secret_like(key):
            if value and not isinstance(value, (int, float, bool)):
                if not (isinstance(value, str) and value.startswith("sha256:")):
                    errors.append(f"Secret field not masked: {key}")

        # Check nested objects
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if is_secret_like(nested_key):
                    if nested_value and not isinstance(nested_value, (int, float, bool)):
                        if not (isinstance(nested_value, str) and nested_value.startswith("sha256:")):
                            errors.append(f"Secret field not masked: {key}.{nested_key}")

    return (len(errors) == 0, errors)
