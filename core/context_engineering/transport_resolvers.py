"""
Transport Layer Resolvers for Auth.

Extract persona/role from Flask requests, CLI args, async context, etc.
Fail-closed: if persona cannot be resolved, raise AuthError.
"""

from typing import Tuple

from core.context_engineering.capabilities import Persona, Role


class AuthError(Exception):
    """Base class for auth errors."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class UnresolvablePersona(AuthError):
    """Cannot determine persona from request context."""
    def __init__(self, context: str):
        super().__init__(
            "unresolvable_persona",
            f"Cannot determine persona from {context}"
        )


class InvalidPersona(AuthError):
    """Persona value is invalid or not in enum."""
    def __init__(self, value: str):
        super().__init__(
            "invalid_persona",
            f"Invalid persona value: {value}"
        )


class TransportResolver:
    """Extract persona/role from various transport contexts."""

    # Mapping: header/arg value → Persona enum
    PERSONA_MAP = {
        "console_operator": Persona.CONSOLE_OPERATOR,
        "voice_user": Persona.VOICE_USER,
        "bridge_adapter": Persona.BRIDGE_ADAPTER,
        "mcp_tool": Persona.MCP_TOOL,
    }

    ROLE_MAP = {
        "admin": Role.ADMIN,
        "operator": Role.OPERATOR,
        "user": Role.USER,
    }

    @staticmethod
    def resolve_flask_request(request) -> Tuple[Persona, Role]:
        """
        Extract persona/role from Flask request headers.
        Headers: X-Persona (required), X-Role (optional, defaults to 'user')
        """
        persona_header = request.headers.get("X-Persona")
        role_header = request.headers.get("X-Role", "user")

        if not persona_header:
            raise UnresolvablePersona("Flask request headers")

        try:
            persona = TransportResolver.PERSONA_MAP[persona_header.lower()]
        except KeyError:
            raise InvalidPersona(persona_header)

        try:
            role = TransportResolver.ROLE_MAP[role_header.lower()]
        except KeyError:
            raise InvalidPersona(role_header)

        return persona, role

    @staticmethod
    def resolve_cli_context(persona_arg: str = None, role_arg: str = None) -> Tuple[Persona, Role]:
        """
        Extract persona/role from CLI arguments.
        Default: CONSOLE_OPERATOR + ADMIN (if running CLI, assume operator).
        """
        if not persona_arg:
            persona_arg = "console_operator"
        if not role_arg:
            role_arg = "admin"

        try:
            persona = TransportResolver.PERSONA_MAP[persona_arg.lower()]
            role = TransportResolver.ROLE_MAP[role_arg.lower()]
        except KeyError as e:
            raise InvalidPersona(str(e))

        return persona, role

    @staticmethod
    def resolve_async_context() -> Tuple[Persona, Role]:
        """
        Extract from ContextVar (parent task must have set it).
        Fail-closed: if not set, raise error.
        """
        from core.context_engineering.persona_model import (
            get_current_persona,
            get_current_role,
        )

        persona = get_current_persona()
        role = get_current_role()

        if persona == Persona.MCP_TOOL:  # Default value, not explicitly set
            raise UnresolvablePersona("async context (ContextVar)")

        return persona, role
