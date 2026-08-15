"""Transport-aware persona/role resolution — ADR-0294

Extract identity from Flask requests, CLI args, async context, and bridges.
This layer sits between transport and logic.
"""

from __future__ import annotations

from typing import Tuple, Optional, Any
from core.context_engineering.persona_model import (
    Persona,
    Role,
    PersonaResolutionError,
)


# ============================================================================
# Transport Resolver
# ============================================================================


class TransportResolver:
    """Extract persona/role from request context (transport-agnostic)."""

    @staticmethod
    def resolve_flask_request(request: Any) -> Tuple[Persona, Role]:
        """Extract persona/role from Flask request headers.

        Headers:
            X-Persona: persona enum value (console_operator, voice_user, etc.)
            X-Role: role enum value (admin, operator, user)

        Falls back to sensible defaults if headers missing:
            - Defaults to CONSOLE_OPERATOR persona for Flask (local admin interface)
            - Defaults to ADMIN role for console

        Args:
            request: Flask request object

        Returns:
            Tuple of (Persona, Role)

        Raises:
            PersonaResolutionError if headers are invalid
        """
        try:
            # Try to extract from headers
            persona_header = request.headers.get("X-Persona", "console_operator")
            role_header = request.headers.get("X-Role", "admin")

            # Validate and convert to enums
            try:
                persona = Persona(persona_header)
            except ValueError:
                raise PersonaResolutionError(
                    f"Invalid persona in X-Persona header: {persona_header}"
                )

            try:
                role = Role(role_header)
            except ValueError:
                raise PersonaResolutionError(
                    f"Invalid role in X-Role header: {role_header}"
                )

            return persona, role

        except PersonaResolutionError:
            raise
        except Exception as e:
            raise PersonaResolutionError(f"Failed to resolve Flask request context: {e}")

    @staticmethod
    def resolve_flask_request_strict(
        request: Any,
        require_headers: bool = True,
    ) -> Tuple[Persona, Role]:
        """Strict Flask resolver: requires explicit headers.

        Args:
            request: Flask request object
            require_headers: If True, raise if headers missing; if False, use defaults

        Returns:
            Tuple of (Persona, Role)

        Raises:
            PersonaResolutionError if headers missing and require_headers=True
        """
        try:
            persona_header = request.headers.get("X-Persona")
            role_header = request.headers.get("X-Role")

            if require_headers and (persona_header is None or role_header is None):
                raise PersonaResolutionError(
                    "X-Persona and X-Role headers required (strict mode)"
                )

            # Fallback to defaults if not in strict mode
            if persona_header is None:
                persona_header = "console_operator"
            if role_header is None:
                role_header = "admin"

            try:
                persona = Persona(persona_header)
            except ValueError:
                raise PersonaResolutionError(
                    f"Invalid persona in X-Persona header: {persona_header}"
                )

            try:
                role = Role(role_header)
            except ValueError:
                raise PersonaResolutionError(
                    f"Invalid role in X-Role header: {role_header}"
                )

            return persona, role

        except PersonaResolutionError:
            raise
        except Exception as e:
            raise PersonaResolutionError(f"Failed to resolve Flask request context: {e}")

    @staticmethod
    def resolve_cli_context(
        persona_override: Optional[str] = None,
        role_override: Optional[str] = None,
    ) -> Tuple[Persona, Role]:
        """Extract persona/role for CLI invocation.

        For CLI: assume CONSOLE_OPERATOR persona (local operator).
        Optionally allow role override via args.

        Args:
            persona_override: Optional explicit persona value
            role_override: Optional explicit role value

        Returns:
            Tuple of (Persona, Role)

        Raises:
            PersonaResolutionError if persona/role values invalid
        """
        try:
            # CLI always CONSOLE_OPERATOR
            persona_str = persona_override or "console_operator"
            role_str = role_override or "admin"

            try:
                persona = Persona(persona_str)
            except ValueError:
                raise PersonaResolutionError(
                    f"Invalid persona for CLI: {persona_str}"
                )

            try:
                role = Role(role_str)
            except ValueError:
                raise PersonaResolutionError(f"Invalid role for CLI: {role_str}")

            return persona, role

        except PersonaResolutionError:
            raise
        except Exception as e:
            raise PersonaResolutionError(f"Failed to resolve CLI context: {e}")

    @staticmethod
    def resolve_async_context(
        persona: Optional[Persona] = None,
        role: Optional[Role] = None,
    ) -> Tuple[Persona, Role]:
        """Extract persona/role for async worker.

        For async tasks: use ContextVar values set by parent task.
        Optionally allow explicit override.

        Args:
            persona: Optional explicit persona (if None, use ContextVar)
            role: Optional explicit role (if None, use ContextVar)

        Returns:
            Tuple of (Persona, Role)

        Raises:
            PersonaResolutionError if persona/role not available in context
        """
        try:
            from core.context_engineering.persona_model import (
                get_current_persona,
                get_current_role,
            )

            # Use explicit values if provided, else fall back to context
            resolved_persona = persona or get_current_persona()
            resolved_role = role or get_current_role()

            return resolved_persona, resolved_role

        except Exception as e:
            raise PersonaResolutionError(f"Failed to resolve async context: {e}")

    @staticmethod
    def resolve_bridge_context(
        bridge_type: str,
        message_source: Optional[str] = None,
    ) -> Tuple[Persona, Role]:
        """Extract persona/role for bridge adapter (Discord, WhatsApp, etc.).

        Bridges always use BRIDGE_ADAPTER persona.
        Role depends on bridge type and message source.

        Args:
            bridge_type: Type of bridge (discord, whatsapp, signal, etc.)
            message_source: Optional source identifier (user ID, channel ID, etc.)

        Returns:
            Tuple of (Persona, Role)
        """
        try:
            # All bridges use BRIDGE_ADAPTER persona
            persona = Persona.BRIDGE_ADAPTER

            # Role depends on context
            # For now, default to USER
            role = Role.USER

            return persona, role

        except Exception as e:
            raise PersonaResolutionError(f"Failed to resolve bridge context: {e}")

    @staticmethod
    def resolve_mcp_context() -> Tuple[Persona, Role]:
        """Extract persona/role for MCP tool invocation.

        MCP tools are external agents, so use MCP_TOOL persona with USER role.

        Returns:
            Tuple of (Persona, Role)
        """
        persona = Persona.MCP_TOOL
        role = Role.USER
        return persona, role
