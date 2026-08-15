"""Persona Capability Axis — ADR-0302

Centralized, deny-by-default capability model for personas and roles.
Every (persona, role, capability) tuple defaults to DENIED.
Only explicit grants are permitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Set, Dict, Tuple, Optional, Callable
from datetime import datetime
import functools
from contextvars import ContextVar


# ============================================================================
# Enums
# ============================================================================


class Persona(Enum):
    """Identity + environment bundles for CorvinOS access."""

    CONSOLE_OPERATOR = "console_operator"
    VOICE_USER = "voice_user"
    BRIDGE_ADAPTER = "bridge_adapter"
    MCP_TOOL = "mcp_tool"


class Role(Enum):
    """Role partitions capabilities."""

    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


class Tier(Enum):
    """Capability tier for bootstrap locking."""

    COMPLIANCE = "compliance"
    STANDARD = "standard"
    USER = "user"


# ============================================================================
# Data Structures
# ============================================================================


@dataclass(frozen=True)
class Capability:
    """Atomic permission definition."""

    id: str  # "read_audit_log", "write_feature_flag"
    description: str
    tier: Tier
    requires_mfa: bool = False


@dataclass
class PersonaRoleCapabilities:
    """Capabilities for a (persona, role) pair."""

    persona: Persona
    role: Role
    capabilities: Set[str] = field(default_factory=set)
    mfa_verified: bool = False


PersonaRoleTuple = Tuple[Persona, Role]


# ============================================================================
# Exceptions
# ============================================================================


class CapabilityLockError(Exception):
    """Cannot register Tier.COMPLIANCE capability after boot."""

    pass


class CapabilityDeniedError(Exception):
    """Capability check resulted in denial."""

    pass


class PersonaResolutionError(Exception):
    """Cannot resolve current persona/role from context."""

    pass


# ============================================================================
# Context Variables
# ============================================================================

current_persona: ContextVar[Persona] = ContextVar(
    "current_persona", default=Persona.MCP_TOOL
)
current_role: ContextVar[Role] = ContextVar("current_role", default=Role.USER)


def get_current_persona() -> Persona:
    """Get current persona from context."""
    return current_persona.get()


def set_current_persona(persona: Persona) -> None:
    """Set current persona in context."""
    current_persona.set(persona)


def get_current_role() -> Role:
    """Get current role from context."""
    return current_role.get()


def set_current_role(role: Role) -> None:
    """Set current role in context."""
    current_role.set(role)


# ============================================================================
# Capability Registry
# ============================================================================


class CapabilityRegistry:
    """Central capability registry — deny-by-default.

    Every (persona, role, capability) tuple is DENIED by default.
    Only explicit grants are permitted.
    Tier.COMPLIANCE capabilities cannot be registered after lock_tier1() is called.
    """

    def __init__(self):
        """Initialize empty registry (all denied by default)."""
        self._capabilities: Dict[PersonaRoleTuple, Set[str]] = {}
        self._capability_defs: Dict[str, Capability] = {}
        self._tier1_locked = False

    def register_capability(
        self,
        capability_id: str,
        description: str,
        tier: Tier = Tier.STANDARD,
        requires_mfa: bool = False,
    ) -> Capability:
        """Register a capability definition.

        Args:
            capability_id: Unique capability identifier
            description: Human-readable description
            tier: Capability tier (COMPLIANCE, STANDARD, USER)
            requires_mfa: Whether this capability requires MFA

        Returns:
            Capability object

        Raises:
            CapabilityLockError if trying to register COMPLIANCE tier after lock_tier1()
        """
        if self._tier1_locked and tier == Tier.COMPLIANCE:
            raise CapabilityLockError(
                f"Cannot register Tier.COMPLIANCE capability '{capability_id}' after boot lock"
            )

        cap = Capability(
            id=capability_id,
            description=description,
            tier=tier,
            requires_mfa=requires_mfa,
        )
        self._capability_defs[capability_id] = cap
        return cap

    def grant(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
    ) -> None:
        """Grant a capability to (persona, role) pair.

        Args:
            persona: Persona that gets the capability
            role: Role that gets the capability
            capability_id: Capability to grant

        Raises:
            ValueError if capability_id is not registered
        """
        if capability_id not in self._capability_defs:
            raise ValueError(f"Capability '{capability_id}' not registered")

        key = (persona, role)
        if key not in self._capabilities:
            self._capabilities[key] = set()
        self._capabilities[key].add(capability_id)

    def revoke(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
    ) -> None:
        """Revoke a capability from (persona, role) pair.

        Args:
            persona: Persona to revoke from
            role: Role to revoke from
            capability_id: Capability to revoke
        """
        key = (persona, role)
        if key in self._capabilities:
            self._capabilities[key].discard(capability_id)

    def has_capability(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
    ) -> bool:
        """Check if (persona, role) has capability (deny-by-default).

        Args:
            persona: Persona to check
            role: Role to check
            capability_id: Capability to check

        Returns:
            True if capability granted, False if denied (default)
        """
        key = (persona, role)
        return capability_id in self._capabilities.get(key, set())

    def get_capabilities(
        self,
        persona: Persona,
        role: Role,
    ) -> Set[str]:
        """Get all capabilities for (persona, role).

        Args:
            persona: Persona
            role: Role

        Returns:
            Set of capability IDs
        """
        key = (persona, role)
        return self._capabilities.get(key, set()).copy()

    def lock_tier1(self) -> None:
        """Lock Tier.COMPLIANCE capabilities (called at boot).

        After this is called, no new COMPLIANCE tier capabilities can be registered.
        """
        self._tier1_locked = True

    def is_locked(self) -> bool:
        """Check if Tier.COMPLIANCE capabilities are locked."""
        return self._tier1_locked

    def get_capability_def(self, capability_id: str) -> Optional[Capability]:
        """Get capability definition by ID."""
        return self._capability_defs.get(capability_id)

    def list_capabilities(self) -> Dict[str, Capability]:
        """Get all registered capabilities."""
        return self._capability_defs.copy()


# ============================================================================
# Global Registry
# ============================================================================

REGISTRY = CapabilityRegistry()


# ============================================================================
# Decorator: requires_capability
# ============================================================================


def requires_capability(capability_id: str) -> Callable:
    """Decorator: require a capability for function execution.

    Resolves persona/role from context and checks capability.
    Raises CapabilityDeniedError if capability is not granted.

    Usage:
        @requires_capability("read_audit_log")
        def verify_audit():
            ...

    Args:
        capability_id: Capability required

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            persona = get_current_persona()
            role = get_current_role()

            if not REGISTRY.has_capability(persona, role, capability_id):
                raise CapabilityDeniedError(
                    f"Persona {persona.value} role {role.value} "
                    f"missing capability {capability_id}"
                )

            return func(*args, **kwargs)

        return wrapper

    return decorator
