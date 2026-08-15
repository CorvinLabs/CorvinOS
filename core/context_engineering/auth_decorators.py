"""Auth Decorator Layer — ADR-0294

Decorators that resolve persona/role from transport context
and enforce capability requirements.
Sits between transport (Flask, CLI) and logic.
"""

from __future__ import annotations

import functools
from typing import Callable, Optional, Any, Tuple

from core.context_engineering.persona_model import (
    Persona,
    Role,
    set_current_persona,
    set_current_role,
    get_current_persona,
    get_current_role,
    REGISTRY,
    CapabilityDeniedError,
)
from core.context_engineering.transport_resolvers import TransportResolver


# ============================================================================
# Exceptions
# ============================================================================


class AuthError(Exception):
    """Base auth error."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


class UnresolvablePersona(AuthError):
    """Cannot determine persona from request."""

    def __init__(self, message: str):
        super().__init__("unresolvable_persona", message)


class MissingCapability(AuthError):
    """Persona doesn't have required capability."""

    def __init__(self, message: str):
        super().__init__("missing_capability", message)


# ============================================================================
# Flask Decorators
# ============================================================================


def auth_required(func: Callable) -> Callable:
    """Decorator: resolve persona + role from Flask request.

    Fail-closed: if auth cannot be resolved, deny access with 403.

    Usage:
        @app.route('/api/audit')
        @auth_required
        def get_audit():
            return {'status': 'ok'}

    Args:
        func: Flask route function

    Returns:
        Decorated function
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            try:
                from flask import request, jsonify
            except ImportError:
                # Flask not available (testing context)
                return func(*args, **kwargs)

            # Resolve persona/role from request
            persona, role = TransportResolver.resolve_flask_request(request)

            # Set context for logic layer
            set_current_persona(persona)
            set_current_role(role)

            return func(*args, **kwargs)

        except Exception as e:
            # Log + return 403
            try:
                from flask import jsonify

                return jsonify({"error": "Unauthorized", "code": "auth_failed"}), 403
            except:
                # Flask not available, re-raise original exception
                raise

    return wrapper


def requires_auth_capability(
    capability_id: str,
    strict: bool = False,
) -> Callable:
    """Decorator: resolve auth + check capability.

    Stacks @auth_required → @requires_capability.
    Fail-closed: if auth or capability check fails, deny with 403.

    Usage:
        @app.route('/api/audit/verify', methods=['POST'])
        @requires_auth_capability("audit_log_verify")
        def verify_audit():
            return {'status': 'verified'}

    Args:
        capability_id: Required capability
        strict: If True, require explicit X-Persona/X-Role headers

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # Check for Flask context
                has_flask = False
                request = None
                jsonify = None
                has_request_context = False

                try:
                    from flask import request as flask_request
                    from flask import jsonify as flask_jsonify
                    from flask import has_request_context as flask_has_request_context

                    has_flask = True
                    request = flask_request
                    jsonify = flask_jsonify
                    has_request_context = flask_has_request_context()
                except (ImportError, RuntimeError):
                    # Flask not available or not in request context
                    pass

                # Determine persona/role
                if has_flask and has_request_context:
                    # Resolve from Flask request
                    if strict:
                        persona, role = (
                            TransportResolver.resolve_flask_request_strict(
                                request, require_headers=True
                            )
                        )
                    else:
                        persona, role = TransportResolver.resolve_flask_request(request)
                    set_current_persona(persona)
                    set_current_role(role)
                else:
                    # Use current context
                    persona = get_current_persona()
                    role = get_current_role()

                # Check capability
                if not REGISTRY.has_capability(persona, role, capability_id):
                    if has_flask and has_request_context:
                        # Return Flask 403 response
                        return (
                            jsonify(
                                {
                                    "error": "Forbidden",
                                    "code": "missing_capability",
                                }
                            ),
                            403,
                        )
                    else:
                        # Raise error in non-Flask context
                        raise CapabilityDeniedError(
                            f"{persona.value} {role.value} missing capability {capability_id}"
                        )

                # Both auth + capability check passed
                return func(*args, **kwargs)

            except CapabilityDeniedError:
                raise
            except Exception as e:
                if has_flask and has_request_context:
                    try:
                        from flask import jsonify as flask_jsonify

                        return (
                            flask_jsonify({"error": "Unauthorized", "code": "auth_failed"}),
                            403,
                        )
                    except:
                        raise
                else:
                    raise

        return wrapper

    return decorator


def audit_request(event_type: str) -> Callable:
    """Decorator: manually audit a request (no capability gate).

    Resolves persona/role and logs the request action.

    Usage:
        @app.route('/health')
        @audit_request('health_check')
        def health():
            return {'status': 'ok'}

    Args:
        event_type: Type of event to audit

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                from flask import request
                from core.context_engineering.persona_model import (
                    get_current_persona,
                    get_current_role,
                )

                # Try to resolve persona/role from request
                try:
                    persona, role = TransportResolver.resolve_flask_request(request)
                    set_current_persona(persona)
                    set_current_role(role)
                except Exception:
                    # If resolution fails, still allow request but use defaults
                    persona = get_current_persona()
                    role = get_current_role()

                # Log the request (implementation depends on audit backend)
                # TODO: integrate with audit trail when available

                return func(*args, **kwargs)

            except Exception as e:
                raise

        return wrapper

    return decorator


# ============================================================================
# CLI Decorators
# ============================================================================


def cli_auth_required(
    persona_override: Optional[str] = None,
    role_override: Optional[str] = None,
) -> Callable:
    """Decorator for CLI commands: resolve persona/role.

    For CLI: assume CONSOLE_OPERATOR persona and ADMIN role.
    Optionally allow override via args.

    Usage:
        @cli.command()
        @cli_auth_required(role_override='operator')
        def list_audit():
            ...

    Args:
        persona_override: Optional explicit persona
        role_override: Optional explicit role

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # Resolve CLI context
                persona, role = TransportResolver.resolve_cli_context(
                    persona_override=persona_override,
                    role_override=role_override,
                )

                # Set context
                set_current_persona(persona)
                set_current_role(role)

                return func(*args, **kwargs)

            except Exception as e:
                print(f"Auth failed: {e}")
                raise

        return wrapper

    return decorator


def cli_requires_capability(capability_id: str) -> Callable:
    """Decorator for CLI commands: require capability.

    Usage:
        @cli.command()
        @cli_requires_capability("audit_log_read")
        def show_audit():
            ...

    Args:
        capability_id: Required capability

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                from core.context_engineering.persona_model import (
                    get_current_persona,
                    get_current_role,
                )

                # Resolve CLI context
                persona, role = TransportResolver.resolve_cli_context()
                set_current_persona(persona)
                set_current_role(role)

                # Check capability
                if not REGISTRY.has_capability(persona, role, capability_id):
                    print(
                        f"Error: {persona.value} {role.value} "
                        f"missing capability {capability_id}"
                    )
                    raise CapabilityDeniedError(
                        f"Missing capability: {capability_id}"
                    )

                return func(*args, **kwargs)

            except CapabilityDeniedError:
                raise
            except Exception as e:
                print(f"Auth failed: {e}")
                raise

        return wrapper

    return decorator


# ============================================================================
# Async Decorators
# ============================================================================


def async_auth_required(
    persona: Optional[Persona] = None,
    role: Optional[Role] = None,
) -> Callable:
    """Decorator for async functions: resolve persona/role from context.

    For async tasks: use ContextVar values set by parent task.
    Optionally allow explicit override.

    Usage:
        @async_auth_required()
        async def process_message(msg):
            ...

    Args:
        persona: Optional explicit persona
        role: Optional explicit role

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                # Resolve async context
                resolved_persona, resolved_role = (
                    TransportResolver.resolve_async_context(
                        persona=persona, role=role
                    )
                )

                # Set context
                set_current_persona(resolved_persona)
                set_current_role(resolved_role)

                return await func(*args, **kwargs)

            except Exception as e:
                raise

        return wrapper

    return decorator


def async_requires_capability(capability_id: str) -> Callable:
    """Decorator for async functions: require capability.

    Usage:
        @async_requires_capability("process_message")
        async def handle_bridge_message(msg):
            ...

    Args:
        capability_id: Required capability

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                from core.context_engineering.persona_model import (
                    get_current_persona,
                    get_current_role,
                )

                # Get persona/role from context (should be set by parent)
                persona = get_current_persona()
                role = get_current_role()

                # Check capability
                if not REGISTRY.has_capability(persona, role, capability_id):
                    raise CapabilityDeniedError(
                        f"{persona.value} {role.value} missing capability {capability_id}"
                    )

                return await func(*args, **kwargs)

            except CapabilityDeniedError:
                raise
            except Exception as e:
                raise

        return wrapper

    return decorator
