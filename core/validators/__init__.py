"""Input Validator Factory — ADR-0296

Centralized, pluggable validator factory with deny-by-default validation.
All user input validated before reaching logic.

Validator registry: each input type has registered rules
Composition: validators stack (type check → length → regex → custom)
Fail-closed: invalid input → 400 Bad Request + audit log

Tenant isolation: all validators are keyword-only and accept tenant_id parameter.
"""

from core.validators.factory import (
    FACTORY,
    ValidationResult,
    ValidatorFactory,
    ValidatorFunc,
    AndValidator,
    OrValidator,
    NotValidator,
    validate_string,
    validate_integer,
    validate_email,
    validate_url,
    validate_peer_id,
    validate_flag_id,
    validate_uuid,
    validate,
)

__all__ = [
    # Factory
    "FACTORY",
    "ValidatorFactory",
    "validate",
    # Result type
    "ValidationResult",
    # Validator type
    "ValidatorFunc",
    # Built-in validators
    "validate_string",
    "validate_integer",
    "validate_email",
    "validate_url",
    "validate_peer_id",
    "validate_flag_id",
    "validate_uuid",
    # Composite validators
    "AndValidator",
    "OrValidator",
    "NotValidator",
]
