"""Click CLI argument validation — Phase 10 (ADR-0297)

Decorator that validates Click command arguments before the handler runs.
Fail-closed: invalid arguments → exit code 1 + audit log.

Supports:
- Required arguments validation
- Optional argument validation
- Type coercion with feedback
- Tenant-scoped error messages
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable, Dict, Optional, TypeVar

import click
from core.validators.factory import ValidatorFactory, ValidationResult
from corvin_compliance_reports.audit import audit_log


F = TypeVar("F", bound=Callable[..., Any])


class ClickValidateError(Exception):
    """Raised when Click argument validation fails."""

    def __init__(self, message: str, error_code: str = "validation_error"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


def click_validate(
    *,
    arguments: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, str]] = None,
    tenant_id_option: Optional[str] = None,
) -> Callable[[F], F]:
    """Click decorator for argument validation.

    Validates Click command arguments/options before handler runs.
    Fail-closed: any validation failure → exit code 1, audit logged.

    Args:
        arguments: Dict of {arg_name: validator_name}, e.g., {"user_id": "peer_id"}
        options: Dict of {option_name: validator_name}, e.g., {"--flag": "flag_id"}
        tenant_id_option: Option name for tenant_id (e.g., "--tenant-id")

    Example:
        @click.command()
        @click.argument("user_id")
        @click.option("--flag-id", default="main")
        @click.option("--tenant-id", required=True)
        @click_validate(
            arguments={"user_id": "peer_id"},
            options={"flag_id": "flag_id"},
            tenant_id_option="tenant_id",
        )
        def update_user(user_id: str, flag_id: str, tenant_id: str):
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            factory = ValidatorFactory()

            # Extract tenant_id
            tenant_id = None
            if tenant_id_option:
                tenant_id = kwargs.get(tenant_id_option)

            if not tenant_id:
                click.secho(
                    f"Error: Tenant ID required (use --{tenant_id_option})",
                    fg="red",
                    err=True,
                )
                audit_log(
                    action="cli_validation_failed",
                    reason="tenant_id_not_found",
                    source="cli_validator",
                    tenant_id="unknown",
                )
                sys.exit(1)

            # Validate arguments
            if arguments:
                for arg_name, validator_name in arguments.items():
                    if arg_name in kwargs:
                        value = kwargs[arg_name]
                        result = factory.validate(
                            validator_name, value, tenant_id=tenant_id
                        )
                        if not result.is_valid:
                            click.secho(
                                f"Error: Invalid {arg_name}: {result.error_message}",
                                fg="red",
                                err=True,
                            )
                            audit_log(
                                action="cli_validation_failed",
                                reason=result.error_code,
                                source=f"cli_argument_{arg_name}",
                                tenant_id=tenant_id,
                            )
                            sys.exit(1)

            # Validate options
            if options:
                for option_name, validator_name in options.items():
                    if option_name in kwargs:
                        value = kwargs[option_name]
                        if value is not None:  # Optional options may be None
                            result = factory.validate(
                                validator_name, value, tenant_id=tenant_id
                            )
                            if not result.is_valid:
                                click.secho(
                                    f"Error: Invalid --{option_name}: {result.error_message}",
                                    fg="red",
                                    err=True,
                                )
                                audit_log(
                                    action="cli_validation_failed",
                                    reason=result.error_code,
                                    source=f"cli_option_{option_name}",
                                    tenant_id=tenant_id,
                                )
                                sys.exit(1)

            # All validation passed
            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator
