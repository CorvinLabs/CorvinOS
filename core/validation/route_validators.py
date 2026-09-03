"""Flask route parameter validation — Phase 10 (ADR-0297)

Decorator that validates Flask route parameters and JSON payloads before
the handler runs. Fail-closed: invalid input → 400 Bad Request + audit log.

Supports:
- Path parameters (e.g., /users/<user_id>)
- Query parameters (e.g., ?flag_id=feature_x)
- JSON body validation
- Tenant-scoped error responses (403, 400, 422)
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Optional, Type, TypeVar

from flask import request, jsonify, current_app
from core.validators.factory import ValidatorFactory, ValidationResult
from corvin_compliance_reports.audit import audit_log


F = TypeVar("F", bound=Callable[..., Any])


class ValidateInputError(Exception):
    """Raised when input validation fails."""

    def __init__(
        self,
        message: str,
        error_code: str = "validation_error",
        status_code: int = 400,
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(message)


def validate_input(
    *,
    path_params: Optional[Dict[str, str]] = None,
    query_params: Optional[Dict[str, str]] = None,
    json_schema: Optional[Dict[str, str]] = None,
    tenant_id_from: str = "header",  # 'header', 'session', or 'path'
    tenant_id_field: str = "X-Tenant-ID",
) -> Callable[[F], F]:
    """Flask decorator for input validation.

    Validates path params, query params, and JSON body before handler runs.
    Fail-closed: any validation failure → 400 Bad Request, audit logged.

    Args:
        path_params: Dict of {param_name: validator_name}, e.g., {"user_id": "peer_id"}
        query_params: Dict of {param_name: validator_name}, e.g., {"flag_id": "flag_id"}
        json_schema: Dict of {field_name: validator_name} for JSON body
        tenant_id_from: Source of tenant_id ('header', 'session', 'path')
        tenant_id_field: Field name for tenant_id (e.g., "X-Tenant-ID" or "tenant_id")

    Example:
        @app.route('/api/users/<user_id>')
        @validate_input(
            path_params={"user_id": "peer_id"},
            query_params={"filter": "flag_id"},
            tenant_id_from="header",
        )
        def get_user(user_id: str):
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            factory = ValidatorFactory()
            tenant_id = _extract_tenant_id(tenant_id_from, tenant_id_field)

            if not tenant_id:
                audit_log(
                    action="input_validation_failed",
                    reason="tenant_id_not_found",
                    source="route_validator",
                    tenant_id="unknown",
                )
                return (
                    jsonify({"error": "Tenant ID required", "code": "missing_tenant_id"}),
                    403,
                )

            # Validate path parameters
            if path_params:
                for param_name, validator_name in path_params.items():
                    if param_name in kwargs:
                        value = kwargs[param_name]
                        result = factory.validate(
                            validator_name, value, tenant_id=tenant_id
                        )
                        if not result.is_valid:
                            audit_log(
                                action="input_validation_failed",
                                reason=result.error_code,
                                source=f"route_path_{param_name}",
                                tenant_id=tenant_id,
                            )
                            return (
                                jsonify(
                                    {
                                        "error": f"Invalid {param_name}: {result.error_message}",
                                        "code": result.error_code,
                                    }
                                ),
                                400,
                            )

            # Validate query parameters
            if query_params:
                for param_name, validator_name in query_params.items():
                    value = request.args.get(param_name)
                    if value is not None:
                        result = factory.validate(
                            validator_name, value, tenant_id=tenant_id
                        )
                        if not result.is_valid:
                            audit_log(
                                action="input_validation_failed",
                                reason=result.error_code,
                                source=f"route_query_{param_name}",
                                tenant_id=tenant_id,
                            )
                            return (
                                jsonify(
                                    {
                                        "error": f"Invalid {param_name}: {result.error_message}",
                                        "code": result.error_code,
                                    }
                                ),
                                400,
                            )

            # Validate JSON body
            if json_schema:
                try:
                    data = request.get_json() or {}
                except Exception as e:
                    audit_log(
                        action="input_validation_failed",
                        reason="malformed_json",
                        source="route_json_parse",
                        tenant_id=tenant_id,
                    )
                    return (
                        jsonify({"error": "Malformed JSON", "code": "malformed_json"}),
                        400,
                    )

                for field_name, validator_name in json_schema.items():
                    if field_name in data:
                        value = data[field_name]
                        result = factory.validate(
                            validator_name, value, tenant_id=tenant_id
                        )
                        if not result.is_valid:
                            audit_log(
                                action="input_validation_failed",
                                reason=result.error_code,
                                source=f"route_json_{field_name}",
                                tenant_id=tenant_id,
                            )
                            return (
                                jsonify(
                                    {
                                        "error": f"Invalid {field_name}: {result.error_message}",
                                        "code": result.error_code,
                                    }
                                ),
                                422,
                            )

            # All validation passed
            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def _extract_tenant_id(source: str, field_name: str) -> Optional[str]:
    """Extract tenant_id from request based on source.

    Args:
        source: 'header', 'session', or 'path'
        field_name: Field name for lookup

    Returns:
        tenant_id or None
    """
    if source == "header":
        return request.headers.get(field_name)
    elif source == "session":
        # Session-based extraction: check Flask session dictionary
        # Example: if session is used, tenant_id would be stored under field_name
        try:
            from flask import session
            if session and field_name in session:
                tenant_id = session.get(field_name)
                if isinstance(tenant_id, str) and tenant_id:
                    return tenant_id
        except (ImportError, RuntimeError):
            # Flask session not available (e.g., outside request context)
            pass
        return None
    elif source == "path":
        # Path-based extraction: check URL path variables (Flask url_map)
        # Example: /tenants/<tenant_id>/resource → extract from view_args
        try:
            if hasattr(request, 'view_args') and request.view_args:
                # field_name typically matches the URL variable name
                # e.g., field_name="tenant_id" in /tenants/<tenant_id>/...
                tenant_id = request.view_args.get(field_name)
                if isinstance(tenant_id, str) and tenant_id:
                    return tenant_id
        except (AttributeError, RuntimeError):
            # No view_args available
            pass
        return None
    return None
