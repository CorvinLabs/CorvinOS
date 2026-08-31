"""
Input Validator Factory — ADR-0296

Central, pluggable validator registry with deny-by-default validation.
All user input validated before reaching business logic.

Two result conventions coexist here, deliberately:

* ADR-0296 documents validators as returning ``(is_valid, error_message)``.
* The later ``core/validation/`` layer (ADR-0297) and its tests were written
  against a richer ``ValidationResult`` carrying an ``error_code`` and the
  coerced ``value``, which is what a route/CLI decorator needs to map a
  failure onto an HTTP status.

Rather than fork the registry into two incompatible halves -- which is how it
was found: `core/validation/*` imported a `ValidationResult` that had never
been written, so that entire layer raised ImportError on import and could
never have run -- ``ValidationResult`` unpacks as the documented 2-tuple.
``is_valid, error = validate_peer_id(x)`` and ``result.error_code`` are both
valid reads of the same object, so ADR-0296's contract is preserved while the
richer consumers get what they need.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Union


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a single validation.

    Immutable: a result is evidence of a decision already made, and callers
    must not be able to flip ``is_valid`` after a gate has read it.

    Iterable as ``(is_valid, error_message)`` so that ADR-0296's documented
    tuple contract keeps working unchanged.
    """

    is_valid: bool
    value: Any = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None

    def __post_init__(self) -> None:
        # A "valid" result carrying an error message is a contradiction that
        # would let a caller pass the gate while an error is on the record.
        if self.is_valid and self.error_message is not None:
            raise ValueError(
                "ValidationResult(is_valid=True) must not carry an error_message"
            )
        # Fail-closed: an invalid result MUST say why, otherwise a rejection
        # reaches the audit log with an empty reason.
        if not self.is_valid and self.error_message is None:
            raise ValueError(
                "ValidationResult(is_valid=False) requires an error_message"
            )

    def __iter__(self) -> Iterator[Any]:
        """Unpack as the ADR-0296 tuple: ``is_valid, error = result``."""
        yield self.is_valid
        yield self.error_message


# A validator takes the value plus keyword-only context and returns either a
# ValidationResult or -- for validators predating ADR-0297 -- a plain tuple.
ValidatorReturn = Union[ValidationResult, tuple]
ValidatorFunc = Callable[..., ValidatorReturn]


def _coerce(result: ValidatorReturn, value: Any) -> ValidationResult:
    """Normalise a legacy ``(is_valid, error)`` tuple into a ValidationResult.

    Keeps hand-registered tuple validators (the shape ADR-0296 documents, and
    what third-party/plugin code registers) working through the same factory
    as the richer built-ins.
    """
    if isinstance(result, ValidationResult):
        return result
    if isinstance(result, tuple) and len(result) == 2:
        is_valid, error = result
        if is_valid:
            return ValidationResult(is_valid=True, value=value)
        return ValidationResult(
            is_valid=False,
            error_message=error or "validation failed",
            error_code="invalid_format",
        )
    raise TypeError(
        f"validator returned {type(result).__name__}, expected "
        "ValidationResult or (is_valid, error_message) tuple"
    )


# ---------------------------------------------------------------------------
# Composite validators
# ---------------------------------------------------------------------------


class CompositeValidator:
    """Base for validators built out of other validators."""

    def validate(self, value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
        raise NotImplementedError

    # Callable so a composite can be passed anywhere a plain validator is.
    def __call__(self, value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
        return self.validate(value, tenant_id=tenant_id, **kwargs)


class AndValidator(CompositeValidator):
    """Passes only if every child validator passes (short-circuits on failure)."""

    def __init__(self, validators: Sequence[ValidatorFunc]) -> None:
        self._validators = list(validators)

    def validate(self, value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
        for validator in self._validators:
            result = _coerce(validator(value, tenant_id=tenant_id, **kwargs), value)
            if not result.is_valid:
                return result
        return ValidationResult(is_valid=True, value=value)


class OrValidator(CompositeValidator):
    """Passes if any child validator passes (short-circuits on success)."""

    def __init__(self, validators: Sequence[ValidatorFunc]) -> None:
        self._validators = list(validators)

    def validate(self, value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
        last_error: Optional[ValidationResult] = None
        for validator in self._validators:
            result = _coerce(validator(value, tenant_id=tenant_id, **kwargs), value)
            if result.is_valid:
                return result
            last_error = result
        # Fail-closed: an OR over zero validators rejects rather than admits.
        if last_error is None:
            return ValidationResult(
                is_valid=False,
                error_message="OrValidator has no child validators",
                error_code="validation_error",
            )
        return last_error


class NotValidator(CompositeValidator):
    """Inverts a validator: passes exactly when the wrapped one fails."""

    def __init__(self, validator: ValidatorFunc) -> None:
        self._validator = validator

    def validate(self, value: Any, *, tenant_id: str = "_default", **kwargs: Any) -> ValidationResult:
        result = _coerce(self._validator(value, tenant_id=tenant_id, **kwargs), value)
        if result.is_valid:
            return ValidationResult(
                is_valid=False,
                error_message="value matched a validator it must not match",
                error_code="invalid_format",
            )
        return ValidationResult(is_valid=True, value=value)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ValidatorFactory:
    """Central validator registry for input validation.

    Deny-by-default: an unknown validator name is a rejection, never a pass,
    and an exception raised inside a validator is converted into a rejection
    rather than propagating (a crashing validator must not become an open
    gate).

    A fresh instance is pre-populated with the built-in validators so that
    every consumer sees the same baseline without an import-order dependency.
    """

    def __init__(self, *, register_builtins: bool = True) -> None:
        self._validators: Dict[str, ValidatorFunc] = {}
        if register_builtins:
            self._register_builtins()

    def _register_builtins(self) -> None:
        # Imported lazily: rules.py imports ValidationResult from this module,
        # so a module-level import here would be circular.
        from core.validators import rules

        for name, validator in rules.BUILTIN_VALIDATORS.items():
            self._validators[name] = validator

    def register(self, name: str, validator: ValidatorFunc) -> None:
        """Register a validator under ``name``.

        Raises:
            ValueError: if ``name`` is already registered. Silent replacement
                of a validator is how a gate gets weakened without a diff that
                looks like it touched the gate.
        """
        if name in self._validators:
            raise ValueError(f"Validator already registered: {name}")
        self._validators[name] = validator

    def register_composite(self, name: str, composite: CompositeValidator) -> None:
        """Register a composite (And/Or/Not) validator under ``name``."""
        if name in self._validators:
            raise ValueError(f"Validator already registered: {name}")
        self._validators[name] = composite

    def unregister(self, name: str) -> None:
        """Remove a validator.

        Raises:
            KeyError: if ``name`` is not registered.
        """
        if name not in self._validators:
            raise KeyError(f"Unknown validator: {name}")
        del self._validators[name]

    def validate(
        self,
        name: str,
        value: Any,
        *,
        tenant_id: str = "_default",
        **kwargs: Any,
    ) -> ValidationResult:
        """Validate ``value`` against the validator registered as ``name``."""
        validator = self._validators.get(name)
        if validator is None:
            return ValidationResult(
                is_valid=False,
                error_message=f"Unknown validator: {name}",
                error_code="unknown_validator",
            )
        try:
            return _coerce(validator(value, tenant_id=tenant_id, **kwargs), value)
        except TypeError:
            # A legacy tuple validator registered without the keyword-only
            # context still has to run -- retry positionally before giving up.
            try:
                return _coerce(validator(value), value)
            except Exception as exc:  # noqa: BLE001 - fail closed, never open
                return ValidationResult(
                    is_valid=False,
                    error_message=f"Validator {name} failed: {exc}",
                    error_code="validation_error",
                )
        except Exception as exc:  # noqa: BLE001 - fail closed, never open
            return ValidationResult(
                is_valid=False,
                error_message=f"Validator {name} failed: {exc}",
                error_code="validation_error",
            )

    def has_validator(self, name: str) -> bool:
        """Check if a validator is registered."""
        return name in self._validators

    def list_validators(self) -> List[str]:
        """Names of every registered validator."""
        return sorted(self._validators)


# Global singleton instance
FACTORY = ValidatorFactory()


def validate(
    name: str,
    value: Any,
    *,
    tenant_id: str = "_default",
    **kwargs: Any,
) -> ValidationResult:
    """Convenience wrapper around the global :data:`FACTORY`."""
    return FACTORY.validate(name, value, tenant_id=tenant_id, **kwargs)
