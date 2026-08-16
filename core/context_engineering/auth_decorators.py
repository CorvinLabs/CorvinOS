"""
Auth Decorators for Transport Layer.

@auth_required — resolve persona + role from request
@requires_auth_capability — stack auth + capability check
Fail-closed: unauthorized requests return 403.
"""

import functools
from typing import Any, Callable

from core.context_engineering.persona_model import (
    set_current_persona,
    set_current_role,
)
from core.context_engineering.transport_resolvers import (
    AuthError,
    TransportResolver,
)


# Audit logging placeholder (will be implemented in ADR-0299)
def _audit_auth_event(action: str, result: str, reason: str = None) -> None:
    """Log auth event to audit trail (stub; real implementation in ADR-0299)."""
    pass


class AuthDecoratorError(Exception):
    """Raised when decorator encounters an error."""
    pass


def auth_required_flask(func: Callable) -> Callable:
    """
    Decorator for Flask routes: resolve persona/role from X-Persona header.
    Fail-closed: missing/invalid persona → 403 Forbidden.

    Usage:
        @app.route("/admin")
        @auth_required_flask
        def admin_panel():
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, request=None, **kwargs) -> Any:
        if not request:
            raise AuthDecoratorError("@auth_required_flask requires Flask request object")

        try:
            persona, role = TransportResolver.resolve_flask_request(request)
        except AuthError as e:
            _audit_auth_event(
                action="auth_check",
                result="DENIED",
                reason=e.code
            )
            return _forbidden_response(e.message)

        # Set context for downstream logic
        set_current_persona(persona)
        set_current_role(role)

        _audit_auth_event(
            action="auth_check",
            result="ALLOWED",
            reason=f"{persona.value}:{role.value}"
        )

        return func(*args, request=request, **kwargs)
    return wrapper


def auth_required_cli(persona_arg: str = None, role_arg: str = None) -> Callable:
    """
    Decorator for CLI commands: extract persona/role from args.
    Default: CONSOLE_OPERATOR + ADMIN.

    Usage:
        @click.command()
        @auth_required_cli()
        def cli_command():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                persona, role = TransportResolver.resolve_cli_context(persona_arg, role_arg)
            except AuthError as e:
                _audit_auth_event(
                    action="auth_check",
                    result="DENIED",
                    reason=e.code
                )
                raise

            set_current_persona(persona)
            set_current_role(role)

            _audit_auth_event(
                action="auth_check",
                result="ALLOWED",
                reason=f"{persona.value}:{role.value}"
            )

            return func(*args, **kwargs)
        return wrapper
    return decorator


def requires_auth_capability(capability_id: str) -> Callable:
    """
    Decorator that stacks @auth_required (Flask) + @requires_capability.
    Resolves persona + checks capability in one step.

    Usage:
        @app.route("/audit/verify", methods=["POST"])
        @requires_auth_capability("audit_log_verify")
        def verify_audit(request):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, request=None, **kwargs) -> Any:
            # Step 1: Auth (resolve persona/role)
            if not request:
                raise AuthDecoratorError("requires_auth_capability requires Flask request object")

            try:
                persona, role = TransportResolver.resolve_flask_request(request)
            except AuthError as e:
                _audit_auth_event(
                    action="auth_capability_check",
                    result="DENIED",
                    reason=f"auth_failed:{e.code}"
                )
                return _forbidden_response(e.message)

            set_current_persona(persona)
            set_current_role(role)

            # Step 2: Capability check (via CapabilityRegistry)
            from core.context_engineering.persona_model import has_capability

            if not has_capability(capability_id):
                _audit_auth_event(
                    action="auth_capability_check",
                    result="DENIED",
                    reason=f"missing_capability:{capability_id}"
                )
                return _forbidden_response(
                    f"Capability required: {capability_id}"
                )

            _audit_auth_event(
                action="auth_capability_check",
                result="ALLOWED",
                reason=f"{persona.value}:{capability_id}"
            )

            return func(*args, request=request, **kwargs)
        return wrapper
    return decorator


def _forbidden_response(message: str) -> tuple:
    """Return 403 Forbidden JSON response."""
    return ({"error": message}, 403)
