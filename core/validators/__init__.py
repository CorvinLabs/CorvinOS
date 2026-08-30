"""
Validator Factory for Input Validation — ADR-0296

Central, pluggable validator registry with deny-by-default validation.
All user input validated before reaching business logic.

The built-in validators are registered by ``ValidatorFactory`` itself (see
``rules.BUILTIN_VALIDATORS``), not here: registering them at package-import
time meant a ``ValidatorFactory()`` built anywhere else started empty and
failed every lookup closed with "unknown_validator".
"""

from core.validators.factory import (
    FACTORY,
    AndValidator,
    CompositeValidator,
    NotValidator,
    OrValidator,
    ValidationResult,
    ValidatorFactory,
    validate,
)
from core.validators.rules import (
    BUILTIN_VALIDATORS,
    validate_alphanumeric,
    validate_email,
    validate_flag_id,
    validate_integer,
    validate_non_empty_string,
    validate_peer_id,
    validate_plugin_id,
    validate_port,
    validate_string,
    validate_string_length,
    validate_tenant_id,
    validate_url,
    validate_uuid,
    validate_uuid4,
)

__all__ = [
    "FACTORY",
    "ValidatorFactory",
    "ValidationResult",
    "CompositeValidator",
    "AndValidator",
    "OrValidator",
    "NotValidator",
    "validate",
    "BUILTIN_VALIDATORS",
    "validate_string",
    "validate_integer",
    "validate_peer_id",
    "validate_flag_id",
    "validate_plugin_id",
    "validate_tenant_id",
    "validate_email",
    "validate_url",
    "validate_uuid",
    "validate_uuid4",
    "validate_port",
    "validate_alphanumeric",
    "validate_non_empty_string",
    "validate_string_length",
]
