"""Async task input validation — Phase 10 (ADR-0297)

Validators for async tasks (asyncio.create_task, background jobs, etc.).
Fail-closed: invalid input → rejection before task submission.

Compatible with asyncio patterns; never blocks on validation.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, TypeVar

from core.validators.factory import ValidatorFactory, ValidationResult
from core.compliance.corvin_compliance_reports.audit import audit_log


T = TypeVar("T")


class AsyncValidationError(Exception):
    """Raised when async input validation fails."""

    def __init__(
        self, message: str, error_code: str = "async_validation_error"
    ):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


async def validate_async_input(
    *,
    task_name: str,
    payload: Dict[str, Any],
    schema: Optional[Dict[str, str]] = None,
    tenant_id: str,
) -> Dict[str, Any]:
    """Validate async task input before submission.

    Validates payload against schema synchronously (fail-closed).
    Never blocks; runs inline validation then returns.

    Args:
        task_name: Name of the task for audit logging
        payload: Payload to validate
        schema: Dict of {field_name: validator_name}, e.g., {"user_id": "peer_id"}
        tenant_id: Tenant context for audit logging

    Returns:
        Validated payload

    Raises:
        AsyncValidationError: If validation fails
    """
    factory = ValidatorFactory()

    if not schema:
        return payload

    for field_name, validator_name in schema.items():
        if field_name in payload:
            value = payload[field_name]
            result = factory.validate(validator_name, value, tenant_id=tenant_id)

            if not result.is_valid:
                audit_log(
                    action="async_validation_failed",
                    reason=result.error_code,
                    source=f"async_task_{task_name}_{field_name}",
                    tenant_id=tenant_id,
                )
                raise AsyncValidationError(
                    f"Invalid {field_name} in {task_name}: {result.error_message}",
                    error_code=result.error_code,
                )

    return payload


async def create_validated_task(
    *,
    coro: Callable[..., Any],
    task_name: str,
    payload: Dict[str, Any],
    schema: Optional[Dict[str, str]] = None,
    tenant_id: str,
) -> asyncio.Task[Any]:
    """Create an async task with input validation.

    Validates payload before wrapping in asyncio.create_task.
    Fail-closed: validation error raises, task never created.

    Args:
        coro: Coroutine factory (function that returns awaitable)
        task_name: Task name for audit logging
        payload: Payload to validate and pass to coro
        schema: Validation schema
        tenant_id: Tenant context

    Returns:
        asyncio.Task if validation passed

    Raises:
        AsyncValidationError: If validation fails
    """
    validated_payload = await validate_async_input(
        task_name=task_name,
        payload=payload,
        schema=schema,
        tenant_id=tenant_id,
    )

    # Now safe to create the task
    task = asyncio.create_task(coro(**validated_payload))
    return task


def validate_async_input_sync(
    *,
    task_name: str,
    payload: Dict[str, Any],
    schema: Optional[Dict[str, str]] = None,
    tenant_id: str,
) -> Dict[str, Any]:
    """Synchronous version of validate_async_input for non-async contexts.

    Use this if you need validation before asyncio.create_task()
    but are not in an async context.

    Args:
        task_name: Name of the task for audit logging
        payload: Payload to validate
        schema: Dict of {field_name: validator_name}
        tenant_id: Tenant context

    Returns:
        Validated payload

    Raises:
        AsyncValidationError: If validation fails
    """
    factory = ValidatorFactory()

    if not schema:
        return payload

    for field_name, validator_name in schema.items():
        if field_name in payload:
            value = payload[field_name]
            result = factory.validate(validator_name, value, tenant_id=tenant_id)

            if not result.is_valid:
                audit_log(
                    action="async_validation_failed",
                    reason=result.error_code,
                    source=f"async_sync_validator_{task_name}_{field_name}",
                    tenant_id=tenant_id,
                )
                raise AsyncValidationError(
                    f"Invalid {field_name} in {task_name}: {result.error_message}",
                    error_code=result.error_code,
                )

    return payload
