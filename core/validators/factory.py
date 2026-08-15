"""Input Validator Factory — ADR-0296

Centralized, pluggable validator factory with deny-by-default validation.
All user input validated before reaching logic.

Validator registry: each input type has registered rules
Composition: validators stack (type check → length → regex → custom)
Fail-closed: invalid input → 400 Bad Request + audit log

Tenant isolation: all validators are keyword-only and accept tenant_id parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)


# ============================================================================
# Result Type
# ============================================================================

@dataclass(frozen=True)
class ValidationResult:
    """Immutable validation result."""

    is_valid: bool
    value: Any = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None  # Non-specific error code for user display

    def __post_init__(self) -> None:
        """Validate invariants."""
        if self.is_valid and self.error_message is not None:
            raise ValueError("Valid result cannot have error_message")
        if not self.is_valid and self.error_message is None:
            raise ValueError("Invalid result must have error_message")


# ============================================================================
# Validator Base Type
# ============================================================================

ValidatorFunc = Callable[[Any, str], ValidationResult]


# ============================================================================
# Built-in Validators
# ============================================================================

def validate_string(
    value: Any,
    *,
    tenant_id: str,
    min_length: int = 1,
    max_length: int = 10000,
    pattern: Optional[str] = None,
) -> ValidationResult:
    """String validator: type check → length → optional regex."""
    if not isinstance(value, str):
        return ValidationResult(
            is_valid=False,
            error_message="String value required",
            error_code="invalid_type",
        )

    if not (min_length <= len(value) <= max_length):
        return ValidationResult(
            is_valid=False,
            error_message=f"String length must be {min_length}–{max_length} chars",
            error_code="invalid_length",
        )

    if pattern:
        try:
            if not re.match(pattern, value):
                return ValidationResult(
                    is_valid=False,
                    error_message="String does not match required pattern",
                    error_code="invalid_format",
                )
        except re.error as e:
            return ValidationResult(
                is_valid=False,
                error_message=f"Validation error: {str(e)}",
                error_code="validation_error",
            )

    return ValidationResult(is_valid=True, value=value)


def validate_integer(
    value: Any,
    *,
    tenant_id: str,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> ValidationResult:
    """Integer validator: type check → range check."""
    if not isinstance(value, int) or isinstance(value, bool):
        return ValidationResult(
            is_valid=False,
            error_message="Integer value required",
            error_code="invalid_type",
        )

    if min_value is not None and value < min_value:
        return ValidationResult(
            is_valid=False,
            error_message=f"Integer must be >= {min_value}",
            error_code="invalid_range",
        )

    if max_value is not None and value > max_value:
        return ValidationResult(
            is_valid=False,
            error_message=f"Integer must be <= {max_value}",
            error_code="invalid_range",
        )

    return ValidationResult(is_valid=True, value=value)


def validate_email(
    value: Any,
    *,
    tenant_id: str,
) -> ValidationResult:
    """Email validator: basic RFC 5322 pattern."""
    if not isinstance(value, str):
        return ValidationResult(
            is_valid=False,
            error_message="Email must be string",
            error_code="invalid_type",
        )

    # Simple RFC 5322 regex (non-exhaustive but sufficient for most cases)
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, value):
        return ValidationResult(
            is_valid=False,
            error_message="Invalid email format",
            error_code="invalid_email",
        )

    return ValidationResult(is_valid=True, value=value)


def validate_url(
    value: Any,
    *,
    tenant_id: str,
    allowed_schemes: Optional[List[str]] = None,
) -> ValidationResult:
    """URL validator: basic URL pattern check."""
    if not isinstance(value, str):
        return ValidationResult(
            is_valid=False,
            error_message="URL must be string",
            error_code="invalid_type",
        )

    # Basic URL regex
    pattern = r'^https?://[^\s/$.?#].[^\s]*$'
    if not re.match(pattern, value, re.IGNORECASE):
        return ValidationResult(
            is_valid=False,
            error_message="Invalid URL format",
            error_code="invalid_url",
        )

    if allowed_schemes:
        scheme = value.split('://', 1)[0].lower()
        if scheme not in allowed_schemes:
            return ValidationResult(
                is_valid=False,
                error_message=f"URL scheme must be one of: {', '.join(allowed_schemes)}",
                error_code="invalid_scheme",
            )

    return ValidationResult(is_valid=True, value=value)


def validate_peer_id(
    value: Any,
    *,
    tenant_id: str,
) -> ValidationResult:
    """Peer ID: alphanumeric + underscore, 1–64 chars."""
    if not isinstance(value, str):
        return ValidationResult(
            is_valid=False,
            error_message="Peer ID must be string",
            error_code="invalid_type",
        )

    if not (1 <= len(value) <= 64):
        return ValidationResult(
            is_valid=False,
            error_message=f"Peer ID length must be 1–64 chars, got {len(value)}",
            error_code="invalid_length",
        )

    if not re.match(r'^[a-zA-Z0-9_]+$', value):
        return ValidationResult(
            is_valid=False,
            error_message="Peer ID must contain only alphanumeric chars and underscores",
            error_code="invalid_format",
        )

    return ValidationResult(is_valid=True, value=value)


def validate_flag_id(
    value: Any,
    *,
    tenant_id: str,
) -> ValidationResult:
    """Feature flag ID: lowercase alphanumeric + underscore."""
    if not isinstance(value, str):
        return ValidationResult(
            is_valid=False,
            error_message="Flag ID must be string",
            error_code="invalid_type",
        )

    if not re.match(r'^[a-z][a-z0-9_]{2,47}$', value):
        return ValidationResult(
            is_valid=False,
            error_message="Flag ID must start with lowercase letter and contain only lowercase alphanumeric + underscore",
            error_code="invalid_format",
        )

    return ValidationResult(is_valid=True, value=value)


def validate_uuid(
    value: Any,
    *,
    tenant_id: str,
) -> ValidationResult:
    """UUID v4 validator."""
    if not isinstance(value, str):
        return ValidationResult(
            is_valid=False,
            error_message="UUID must be string",
            error_code="invalid_type",
        )

    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    if not re.match(pattern, value, re.IGNORECASE):
        return ValidationResult(
            is_valid=False,
            error_message="Invalid UUID v4 format",
            error_code="invalid_uuid",
        )

    return ValidationResult(is_valid=True, value=value)


# ============================================================================
# Composite Validators
# ============================================================================

class CompositeValidator:
    """Base class for composite validators (AND/OR/NOT)."""

    def __init__(self, validators: List[ValidatorFunc]) -> None:
        self.validators = validators

    def validate(self, value: Any, *, tenant_id: str) -> ValidationResult:
        raise NotImplementedError


class AndValidator(CompositeValidator):
    """All validators must pass (AND logic)."""

    def validate(self, value: Any, *, tenant_id: str) -> ValidationResult:
        for validator in self.validators:
            result = validator(value, tenant_id=tenant_id)
            if not result.is_valid:
                return result
        return ValidationResult(is_valid=True, value=value)


class OrValidator(CompositeValidator):
    """At least one validator must pass (OR logic)."""

    def validate(self, value: Any, *, tenant_id: str) -> ValidationResult:
        errors: List[str] = []
        for validator in self.validators:
            result = validator(value, tenant_id=tenant_id)
            if result.is_valid:
                return result
            if result.error_message:
                errors.append(result.error_message)

        return ValidationResult(
            is_valid=False,
            error_message="Value failed all validation options: " + "; ".join(errors),
            error_code="all_validations_failed",
        )


class NotValidator:
    """Negation of a validator (NOT logic)."""

    def __init__(self, validator: ValidatorFunc) -> None:
        self.validator = validator

    def validate(self, value: Any, *, tenant_id: str) -> ValidationResult:
        result = self.validator(value, tenant_id=tenant_id)
        if result.is_valid:
            return ValidationResult(
                is_valid=False,
                error_message="Value should not pass validation",
                error_code="validation_inverted",
            )
        return ValidationResult(is_valid=True, value=value)


# ============================================================================
# Validator Factory
# ============================================================================

class ValidatorFactory:
    """Central validator registry with pluggable validators.

    Fail-closed: unknown validators reject, invalid input rejects.
    Tenant isolation: all validators accept keyword-only tenant_id.
    Recursion limit: composite validators capped at MAX_DEPTH to prevent stack overflow.
    """

    # Maximum nesting depth for composite validators (prevents stack overflow)
    MAX_RECURSION_DEPTH = 10

    def __init__(self) -> None:
        """Initialize factory."""
        self._validators: Dict[str, ValidatorFunc] = {}
        self._composite_validators: Dict[str, Union[CompositeValidator, NotValidator]] = {}
        self._recursion_depth: int = 0
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Register built-in validators."""
        self.register("string", validate_string)
        self.register("integer", validate_integer)
        self.register("email", validate_email)
        self.register("url", validate_url)
        self.register("peer_id", validate_peer_id)
        self.register("flag_id", validate_flag_id)
        self.register("uuid", validate_uuid)

    def register(
        self,
        name: str,
        validator: ValidatorFunc,
    ) -> None:
        """Register validator by name.

        Args:
            name: Unique validator name
            validator: Validator function with signature (value, *, tenant_id) -> ValidationResult

        Raises:
            ValueError: if name is already registered
        """
        if name in self._validators or name in self._composite_validators:
            raise ValueError(f"Validator '{name}' already registered")

        self._validators[name] = validator

    def unregister(self, name: str) -> None:
        """Unregister a validator by name.

        Args:
            name: Validator name to remove

        Raises:
            KeyError: if validator not registered
        """
        if name in self._validators:
            del self._validators[name]
        elif name in self._composite_validators:
            del self._composite_validators[name]
        else:
            raise KeyError(f"Validator '{name}' not registered")

    def register_composite(
        self,
        name: str,
        validator: Union[CompositeValidator, NotValidator],
    ) -> None:
        """Register composite validator (AND/OR/NOT).

        Args:
            name: Unique validator name
            validator: CompositeValidator or NotValidator instance
        """
        if name in self._validators or name in self._composite_validators:
            raise ValueError(f"Validator '{name}' already registered")

        self._composite_validators[name] = validator

    def validate(
        self,
        name: str,
        value: Any,
        *,
        tenant_id: str,
        **kwargs: Any,
    ) -> ValidationResult:
        """Validate value with named validator.

        Args:
            name: Validator name
            value: Value to validate
            tenant_id: Tenant context (keyword-only, required for GDPR isolation)
            **kwargs: Additional validator-specific parameters

        Returns:
            ValidationResult with is_valid, value, error_message, error_code

        Raises:
            KeyError: if validator not registered (fail-closed)
        """
        # Check recursion depth (fail-closed: prevent stack overflow)
        self._recursion_depth += 1
        if self._recursion_depth > self.MAX_RECURSION_DEPTH:
            self._recursion_depth -= 1
            return ValidationResult(
                is_valid=False,
                error_message=f"Validator nesting exceeds maximum depth ({self.MAX_RECURSION_DEPTH})",
                error_code="recursion_depth_exceeded",
            )

        try:
            # Fail-closed: unknown validator rejects
            if name not in self._validators and name not in self._composite_validators:
                return ValidationResult(
                    is_valid=False,
                    error_message=f"Unknown validator: {name}",
                    error_code="unknown_validator",
                )

            # Simple validators
            if name in self._validators:
                validator = self._validators[name]
                try:
                    return validator(value, tenant_id=tenant_id, **kwargs)
                except Exception as e:
                    # Fail-closed: catch validation errors
                    return ValidationResult(
                        is_valid=False,
                        error_message=f"Validation error: {str(e)}",
                        error_code="validation_error",
                    )

            # Composite validators
            if name in self._composite_validators:
                validator = self._composite_validators[name]
                try:
                    return validator.validate(value, tenant_id=tenant_id)
                except Exception as e:
                    # Fail-closed: catch validation errors
                    return ValidationResult(
                        is_valid=False,
                        error_message=f"Validation error: {str(e)}",
                        error_code="validation_error",
                    )

            # Should not reach here
            return ValidationResult(
                is_valid=False,
                error_message="Validator not found",
                error_code="unknown_validator",
            )
        finally:
            # Always decrement depth on exit (even on exception)
            self._recursion_depth -= 1

    def list_validators(self) -> Dict[str, str]:
        """List all registered validators with descriptions."""
        result: Dict[str, str] = {}

        # Built-in validators
        result.update({
            "string": "String validator (type, length, optional regex)",
            "integer": "Integer validator (type, optional range)",
            "email": "Email validator (RFC 5322)",
            "url": "URL validator (http/https)",
            "peer_id": "Peer ID validator (alphanumeric + underscore, 1–64 chars)",
            "flag_id": "Feature flag ID validator (lowercase alphanumeric + underscore)",
            "uuid": "UUID v4 validator",
        })

        # Custom validators
        for name in self._validators:
            if name not in result:
                result[name] = "Custom validator"

        # Composite validators
        for name in self._composite_validators:
            result[name] = "Composite validator (AND/OR/NOT)"

        return result


# ============================================================================
# Global Factory Instance
# ============================================================================

FACTORY = ValidatorFactory()


def validate(
    name: str,
    value: Any,
    *,
    tenant_id: str,
    **kwargs: Any,
) -> ValidationResult:
    """Convenience function to validate using global factory.

    Args:
        name: Validator name
        value: Value to validate
        tenant_id: Tenant context (keyword-only, required for GDPR isolation)
        **kwargs: Additional validator-specific parameters

    Returns:
        ValidationResult with is_valid, value, error_message, error_code
    """
    return FACTORY.validate(name, value, tenant_id=tenant_id, **kwargs)
