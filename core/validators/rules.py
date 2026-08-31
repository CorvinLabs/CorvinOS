"""
Validator Rules — ADR-0296

Built-in validator functions for common input types.

Every validator returns a :class:`~core.validators.factory.ValidationResult`,
which unpacks as ADR-0296's documented ``(is_valid, error_message)`` tuple, so
both ``is_valid, error = validate_peer_id(x)`` and ``result.error_code`` are
supported reads of the same return value.

``tenant_id`` is keyword-only with a default: it scopes an eventual audit
record for a rejection, and must never be positional (ADR-0007 keeps every
tenant axis keyword-only so it cannot be filled in by argument order).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict

from core.validators.factory import ValidationResult

# Reused patterns
_PEER_ID_RE = re.compile(r'^[a-zA-Z0-9_]+$')
# A flag id must start with a letter: "1my_feature" is not a usable Python-ish
# identifier and would sort oddly in the Settings panel.
_FLAG_ID_RE = re.compile(r'^[a-z][a-z0-9_]*$')
_FLAG_ID_MIN_LENGTH = 3
_PLUGIN_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')
_TENANT_ID_RE = re.compile(r'^[a-z0-9_]+$')
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
_URL_RE = re.compile(r'^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://[a-zA-Z0-9.-]+(:[0-9]+)?(/.*)?$')
_UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def _ok(value: Any) -> ValidationResult:
    return ValidationResult(is_valid=True, value=value)


def _fail(message: str, code: str) -> ValidationResult:
    return ValidationResult(is_valid=False, error_message=message, error_code=code)


# ---------------------------------------------------------------------------
# Generic validators
# ---------------------------------------------------------------------------


def validate_string(
    value: Any,
    *,
    tenant_id: str = "_default",
    min_length: int = 1,
    max_length: int = 65535,
    pattern: str | None = None,
    **kwargs: Any,
) -> ValidationResult:
    """String validator with optional length bounds and regex pattern.

    ``min_length`` defaults to 1: an empty string is a missing value, and a
    validator that admits it by default turns a required field into an
    optional one at every call site that does not think to pass a bound.
    """
    if not isinstance(value, str):
        return _fail(f"value must be string, got {type(value).__name__}", "invalid_type")
    if not (min_length <= len(value) <= max_length):
        return _fail(
            f"string length must be {min_length}-{max_length}, got {len(value)}",
            "invalid_length",
        )
    if pattern is not None:
        try:
            matched = re.match(pattern, value)
        except re.error as exc:
            # A malformed pattern is a programming error, but it must not
            # become an open gate -- reject and name the cause.
            return _fail(f"invalid validation pattern: {exc}", "validation_error")
        if not matched:
            return _fail("value does not match required pattern", "invalid_format")
    return _ok(value)


def validate_integer(
    value: Any,
    *,
    tenant_id: str = "_default",
    min_value: int | None = None,
    max_value: int | None = None,
    **kwargs: Any,
) -> ValidationResult:
    """Integer validator with optional range bounds.

    ``bool`` is rejected: it is an ``int`` subclass in Python, so accepting it
    would let ``True`` satisfy a numeric field.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return _fail(f"value must be integer, got {type(value).__name__}", "invalid_type")
    if min_value is not None and value < min_value:
        return _fail(f"value must be >= {min_value}, got {value}", "invalid_range")
    if max_value is not None and value > max_value:
        return _fail(f"value must be <= {max_value}, got {value}", "invalid_range")
    return _ok(value)


# ---------------------------------------------------------------------------
# Domain validators
# ---------------------------------------------------------------------------


def validate_peer_id(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Peer ID: alphanumeric + underscore, 1-64 chars."""
    if not isinstance(value, str):
        return _fail("peer_id must be string", "invalid_type")
    if not (1 <= len(value) <= 64):
        return _fail(f"peer_id length 1\u201364, got {len(value)}", "invalid_length")
    if not _PEER_ID_RE.match(value):
        return _fail(
            "peer_id must contain only alphanumeric characters and underscore",
            "invalid_format",
        )
    return _ok(value)


def validate_flag_id(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Feature flag ID: lowercase, starts with a letter, >= 3 chars.

    The 3-char floor is deliberate: a flag id is an operator-visible key under
    ``spec.features.<flag_id>``, and every flag that actually ships is far
    longer (``console_auto_reload``, ``frontend_forge``, ...). A one- or
    two-character flag is indistinguishable from an abbreviation and cannot be
    reviewed meaningfully in the Settings panel.
    """
    if not isinstance(value, str):
        return _fail("flag_id must be string", "invalid_type")
    if len(value) < _FLAG_ID_MIN_LENGTH:
        return _fail(
            f"flag_id must be at least {_FLAG_ID_MIN_LENGTH} characters, got {len(value)}",
            "invalid_format",
        )
    if not _FLAG_ID_RE.match(value):
        return _fail(
            "flag_id must start with a lowercase letter and contain only "
            "lowercase alphanumeric characters and underscore",
            "invalid_format",
        )
    return _ok(value)


def validate_plugin_id(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Plugin ID: alphanumeric + underscore + hyphen, 1-128 chars."""
    if not isinstance(value, str):
        return _fail("plugin_id must be string", "invalid_type")
    if not (1 <= len(value) <= 128):
        return _fail(f"plugin_id length 1\u2013128, got {len(value)}", "invalid_length")
    if not _PLUGIN_ID_RE.match(value):
        return _fail(
            "plugin_id must contain only alphanumeric, underscore, and hyphen",
            "invalid_format",
        )
    return _ok(value)


def validate_tenant_id(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Tenant ID: lowercase alphanumeric + underscore, 1-64 chars (ADR-0007)."""
    if not isinstance(value, str):
        return _fail("tenant_id must be string", "invalid_type")
    if not (1 <= len(value) <= 64):
        return _fail(f"tenant_id length 1\u201364, got {len(value)}", "invalid_length")
    if not _TENANT_ID_RE.match(value):
        return _fail(
            "tenant_id must contain only lowercase alphanumeric and underscore",
            "invalid_format",
        )
    return _ok(value)


def validate_email(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Email validator: simplified RFC 5322 pattern."""
    if not isinstance(value, str):
        return _fail("email must be string", "invalid_type")
    if len(value) > 254:
        return _fail("email too long (max 254 chars)", "invalid_length")
    if not _EMAIL_RE.match(value):
        return _fail("invalid email format", "invalid_email")
    return _ok(value)


def validate_url(
    value: Any,
    *,
    tenant_id: str = "_default",
    allowed_schemes: tuple[str, ...] | list[str] | None = None,
    **kwargs: Any,
) -> ValidationResult:
    """URL validator. Defaults to http/https; ``allowed_schemes`` narrows or widens it."""
    if not isinstance(value, str):
        return _fail("url must be string", "invalid_type")
    if len(value) > 2048:
        return _fail("url too long (max 2048 chars)", "invalid_length")
    match = _URL_RE.match(value)
    if not match:
        return _fail(
            "invalid URL format (must start with http:// or https://)",
            "invalid_url",
        )
    explicit = allowed_schemes is not None
    schemes = tuple(allowed_schemes) if explicit else ("http", "https")
    if match.group("scheme").lower() not in {s.lower() for s in schemes}:
        if explicit:
            # The caller narrowed the scheme set on purpose, so name that as
            # the cause -- it is actionable, "not a URL" would not be.
            return _fail(
                f"URL scheme must be one of {', '.join(schemes)}",
                "invalid_scheme",
            )
        return _fail(
            "invalid URL format (must start with http:// or https://)",
            "invalid_url",
        )
    return _ok(value)


def validate_uuid(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """UUID v4 validator."""
    if not isinstance(value, str):
        return _fail("uuid must be string", "invalid_type")
    if not _UUID4_RE.match(value):
        return _fail("invalid UUID4 format", "invalid_uuid")
    return _ok(value)


# ADR-0296 registered this validator as "uuid4"; the richer consumers call it
# "uuid". Same rule, both names, one implementation.
validate_uuid4 = validate_uuid


def validate_port(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Port validator: integer between 1 and 65535."""
    if isinstance(value, bool) or not isinstance(value, int):
        return _fail("port must be integer", "invalid_type")
    if not (1 <= value <= 65535):
        return _fail(f"port must be 1\u201365535, got {value}", "invalid_range")
    return _ok(value)


def validate_alphanumeric(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Alphanumeric validator: letters and digits only."""
    if not isinstance(value, str):
        return _fail("value must be string", "invalid_type")
    if not value.isalnum():
        return _fail("value must contain only alphanumeric characters", "invalid_format")
    return _ok(value)


def validate_non_empty_string(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
    """Non-empty string validator."""
    if not isinstance(value, str):
        return _fail("value must be string", "invalid_type")
    if len(value) == 0:
        return _fail("value cannot be empty", "invalid_length")
    return _ok(value)


def validate_string_length(min_len: int = 0, max_len: int = 65535) -> Callable[..., ValidationResult]:
    """Parameterised string-length validator factory.

    Usage: ``validate_string_length(1, 255)`` returns a validator for strings
    of 1-255 chars.
    """

    def validator(value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
        return validate_string(
            value, tenant_id=tenant_id, min_length=min_len, max_length=max_len
        )

    return validator


# ---------------------------------------------------------------------------
# The baseline every ValidatorFactory starts with.
#
# Registered here rather than in __init__.py so that a freshly constructed
# ValidatorFactory() carries the same built-ins as the global FACTORY --
# otherwise the registry a caller builds for itself silently admits nothing
# and every lookup fails closed with "unknown_validator".
# ---------------------------------------------------------------------------
BUILTIN_VALIDATORS: Dict[str, Callable[..., ValidationResult]] = {
    "string": validate_string,
    "integer": validate_integer,
    "peer_id": validate_peer_id,
    "flag_id": validate_flag_id,
    "plugin_id": validate_plugin_id,
    "tenant_id": validate_tenant_id,
    "email": validate_email,
    "url": validate_url,
    "uuid": validate_uuid,
    "uuid4": validate_uuid4,
    "port": validate_port,
    "alphanumeric": validate_alphanumeric,
    "non_empty_string": validate_non_empty_string,
}
