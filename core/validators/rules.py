"""
Validator Rules — ADR-0296

Built-in validator functions for common input types.
All validators return (is_valid: bool, error_message: Optional[str]).
"""

import re
from typing import Optional, Tuple


def validate_peer_id(value: str) -> Tuple[bool, Optional[str]]:
    """
    Peer ID validator: alphanumeric + underscore, 1–64 chars.

    Valid: "peer_1", "DEVICE_A", "sensor_123"
    Invalid: "peer-1" (hyphen), "a" * 65 (too long), 123 (not string)
    """
    if not isinstance(value, str):
        return False, "peer_id must be string"
    if not (1 <= len(value) <= 64):
        return False, f"peer_id length 1–64, got {len(value)}"
    if not re.match(r'^[a-zA-Z0-9_]+$', value):
        return False, "peer_id must contain only alphanumeric characters and underscore"
    return True, None


def validate_flag_id(value: str) -> Tuple[bool, Optional[str]]:
    """
    Feature flag ID validator: lowercase alphanumeric + underscore.

    Valid: "plugin_builder_enabled", "tde_mode", "l44_strict"
    Invalid: "Plugin_Builder" (uppercase), "plugin-builder" (hyphen)
    """
    if not isinstance(value, str):
        return False, "flag_id must be string"
    if not re.match(r'^[a-z0-9_]+$', value):
        return False, "flag_id must contain only lowercase alphanumeric characters and underscore"
    return True, None


def validate_plugin_id(value: str) -> Tuple[bool, Optional[str]]:
    """
    Plugin ID validator: alphanumeric + underscore + hyphen, 1–128 chars.

    Valid: "stt-whisper", "llm_handler", "plugin_v2"
    Invalid: "plugin!" (special char), "" (empty)
    """
    if not isinstance(value, str):
        return False, "plugin_id must be string"
    if not (1 <= len(value) <= 128):
        return False, f"plugin_id length 1–128, got {len(value)}"
    if not re.match(r'^[a-zA-Z0-9_-]+$', value):
        return False, "plugin_id must contain only alphanumeric, underscore, and hyphen"
    return True, None


def validate_tenant_id(value: str) -> Tuple[bool, Optional[str]]:
    """
    Tenant ID validator: lowercase alphanumeric + underscore, 1–64 chars.

    Valid: "_default", "customer_abc", "tenant_123"
    Invalid: "Tenant" (uppercase), "tenant-1" (hyphen)
    """
    if not isinstance(value, str):
        return False, "tenant_id must be string"
    if not (1 <= len(value) <= 64):
        return False, f"tenant_id length 1–64, got {len(value)}"
    if not re.match(r'^[a-z0-9_]+$', value):
        return False, "tenant_id must contain only lowercase alphanumeric and underscore"
    return True, None


def validate_email(value: str) -> Tuple[bool, Optional[str]]:
    """
    Email validator: basic RFC 5322 pattern (simplified).

    Valid: "user@example.com", "test.user+tag@domain.co.uk"
    Invalid: "user@", "@example.com", "user@.com"
    """
    if not isinstance(value, str):
        return False, "email must be string"
    if len(value) > 254:
        return False, "email too long (max 254 chars)"
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, value):
        return False, "invalid email format"
    return True, None


def validate_url(value: str) -> Tuple[bool, Optional[str]]:
    """
    URL validator: basic HTTP(S) URL pattern.

    Valid: "https://example.com", "http://localhost:8080/path"
    Invalid: "example.com" (no scheme), "ftp://example.com" (ftp not allowed)
    """
    if not isinstance(value, str):
        return False, "url must be string"
    if len(value) > 2048:
        return False, "url too long (max 2048 chars)"
    # Allows: http(s)://host[:port][/path] where host can be domain or localhost
    pattern = r'^https?://[a-zA-Z0-9.-]+(:[0-9]+)?(/.*)?$'
    if not re.match(pattern, value):
        return False, "invalid URL format (must start with http:// or https://)"
    return True, None


def validate_uuid4(value: str) -> Tuple[bool, Optional[str]]:
    """
    UUID4 validator: standard UUID v4 format.

    Valid: "550e8400-e29b-41d4-a716-446655440000"
    Invalid: "550e8400-e29b-41d4-a716" (too short)
    """
    if not isinstance(value, str):
        return False, "uuid4 must be string"
    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    if not re.match(pattern, value, re.IGNORECASE):
        return False, "invalid UUID4 format"
    return True, None


def validate_port(value: int) -> Tuple[bool, Optional[str]]:
    """
    Port validator: integer between 1 and 65535.

    Valid: 8080, 443, 3000
    Invalid: 0, 65536, "8080" (string)
    """
    if not isinstance(value, int):
        return False, "port must be integer"
    if not (1 <= value <= 65535):
        return False, f"port must be 1–65535, got {value}"
    return True, None


def validate_alphanumeric(value: str) -> Tuple[bool, Optional[str]]:
    """
    Alphanumeric validator: letters and digits only.

    Valid: "abc123", "HELLO"
    Invalid: "abc-123", "hello world"
    """
    if not isinstance(value, str):
        return False, "value must be string"
    if not value.isalnum():
        return False, "value must contain only alphanumeric characters"
    return True, None


def validate_non_empty_string(value: str) -> Tuple[bool, Optional[str]]:
    """
    Non-empty string validator.

    Valid: "hello", " " (space)
    Invalid: "" (empty)
    """
    if not isinstance(value, str):
        return False, "value must be string"
    if len(value) == 0:
        return False, "value cannot be empty"
    return True, None


def validate_string_length(min_len: int = 0, max_len: int = 65535):
    """
    String length validator factory (parameterized).

    Usage: validate_string_length(1, 255) returns a validator for strings 1–255 chars.
    """
    def validator(value: str) -> Tuple[bool, Optional[str]]:
        if not isinstance(value, str):
            return False, "value must be string"
        if not (min_len <= len(value) <= max_len):
            return False, f"string length must be {min_len}–{max_len}, got {len(value)}"
        return True, None

    return validator
