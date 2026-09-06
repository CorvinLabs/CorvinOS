"""
os.identity_resolver Skill — Replaces TransportResolver (ADR-0537 Phase 1)

Extracts (role, persona) from Flask headers OR CLI args.
Validates role+persona pairs (privilege escalation prevention).
Immutable input/output, fail-closed design.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Set


class TransportType(Enum):
    """Supported transport types (ContextVar support deferred to Phase 2)."""
    FLASK_HTTP = "flask_http"
    CLI = "cli"


# Valid (role, persona) pairs — prevents privilege escalation
VALID_PAIRS: Set[tuple] = {
    ("admin", "console_operator"),
    ("admin", "mcp_tool"),
    ("operator", "console_operator"),
    ("operator", "bridge_adapter"),
    ("user", "voice_user"),
    ("user", "mcp_tool"),
}


@dataclass(frozen=True)
class IdentityResolverInput:
    """Immutable input for identity resolution."""
    transport_type: str  # Must be TransportType value
    tenant_id: str  # Scoped; fail-closed if null
    headers: Optional[Dict[str, str]] = None  # X-Role, X-Persona (Flask)
    cli_args: Optional[Dict[str, str]] = None  # --role, --persona (CLI)


@dataclass(frozen=True)
class IdentityResolverOutput:
    """Immutable output from identity resolution."""
    role: str  # "admin", "operator", "user"
    persona: str  # "console_operator", "voice_user", "bridge_adapter", "mcp_tool"
    tenant_id: str
    resolved_from: str  # "flask_header", "cli_arg" (context_var support deferred to Phase 2)


class IdentityResolverSkill:
    """
    Replaces TransportResolver.resolve_*() methods.

    Characteristics:
    - Immutable input/output (dataclasses, frozen)
    - Fail-closed: null tenant_id → SkillExecutionError (deny)
    - Fail-closed: invalid role/persona → SkillExecutionError (deny)
    - Fail-closed: invalid pair → SkillExecutionError (deny, prevents privilege escalation)
    - Supported transports: Flask HTTP headers, CLI args
    - ContextVar support deferred to Phase 2 (thread-safety, async-context issues in Phase 1)
    """

    skill_id = "os.identity_resolver"
    version = "1.0.0"
    tier = "standard"

    VALID_ROLES = {"admin", "operator", "user"}
    VALID_PERSONAS = {"console_operator", "voice_user", "bridge_adapter", "mcp_tool"}

    def execute(self, input: IdentityResolverInput) -> IdentityResolverOutput:
        """
        Resolve identity from transport; fail-closed on error.

        Args:
            input: IdentityResolverInput (immutable)

        Returns:
            IdentityResolverOutput (immutable)

        Raises:
            SkillExecutionError: on any validation failure (fail-closed)
        """
        # Fail-closed: null tenant_id
        if not input.tenant_id:
            raise SkillExecutionError("tenant_id required (fail-closed)")

        # Route to appropriate resolver
        if input.transport_type == TransportType.FLASK_HTTP.value:
            return self._resolve_flask(input)
        elif input.transport_type == TransportType.CLI.value:
            return self._resolve_cli(input)
        else:
            raise SkillExecutionError(
                f"unknown transport: {input.transport_type} (supported: flask_http, cli)"
            )

    def _resolve_flask(self, input: IdentityResolverInput) -> IdentityResolverOutput:
        """
        Extract identity from Flask HTTP headers.

        Expected headers: X-Role, X-Persona
        Fail-closed: invalid values → SkillExecutionError
        """
        headers = input.headers or {}

        role = headers.get("X-Role", "user").lower()
        persona = headers.get("X-Persona", "mcp_tool").lower()

        # Fail-closed: invalid individual values
        if role not in self.VALID_ROLES:
            raise SkillExecutionError(f"invalid role header: {role}")
        if persona not in self.VALID_PERSONAS:
            raise SkillExecutionError(f"invalid persona header: {persona}")

        # Fail-closed: invalid pair (prevents privilege escalation)
        if (role, persona) not in VALID_PAIRS:
            raise SkillExecutionError(
                f"invalid (role, persona) pair: ({role}, {persona}) not in allowed pairs"
            )

        return IdentityResolverOutput(
            role=role,
            persona=persona,
            tenant_id=input.tenant_id,
            resolved_from="flask_header"
        )

    def _resolve_cli(self, input: IdentityResolverInput) -> IdentityResolverOutput:
        """
        Extract identity from CLI arguments.

        Expected args: --role, --persona
        Defaults: role=admin, persona=console_operator
        Fail-closed: invalid values → SkillExecutionError
        """
        args = input.cli_args or {}

        role = args.get("role", "admin").lower()  # CLI default: admin
        persona = args.get("persona", "console_operator").lower()  # CLI default: console_operator

        # Fail-closed: invalid individual values
        if role not in self.VALID_ROLES:
            raise SkillExecutionError(f"invalid CLI role: {role}")
        if persona not in self.VALID_PERSONAS:
            raise SkillExecutionError(f"invalid CLI persona: {persona}")

        # Fail-closed: invalid pair (prevents privilege escalation)
        if (role, persona) not in VALID_PAIRS:
            raise SkillExecutionError(
                f"invalid (role, persona) pair: ({role}, {persona}) not in allowed pairs"
            )

        return IdentityResolverOutput(
            role=role,
            persona=persona,
            tenant_id=input.tenant_id,
            resolved_from="cli_arg"
        )


class SkillExecutionError(Exception):
    """Raised when Skill execution fails (fail-closed)."""
    pass
