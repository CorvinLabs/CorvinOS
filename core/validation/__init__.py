"""Input validation integration — Phase 10 (ADR-0297 + ADR-0296)

Wires Phase-9 validators (ValidatorFactory) into real Flask routes, CLI commands,
async handlers. All user input validated before reaching logic (fail-closed).

Modules:
- route_validators: Flask @validate_input decorator
- cli_validators: Click @click_validate decorator
- async_validators: Async task validation
- integration: Middleware registration + test utilities

Tenant isolation: All validators accept tenant_id (keyword-only).
"""

from core.validation.route_validators import validate_input, ValidateInputError
from core.validation.cli_validators import click_validate, ClickValidateError
from core.validation.async_validators import validate_async_input
from core.validation.integration import register_validation_middleware

__all__ = [
    "validate_input",
    "ValidateInputError",
    "click_validate",
    "ClickValidateError",
    "validate_async_input",
    "register_validation_middleware",
]
