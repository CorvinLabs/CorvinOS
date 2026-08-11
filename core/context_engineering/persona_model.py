"""
Persona and Capability Model.

Central registry for persona/role/capability checking. Deny-by-default:
a capability is only True if explicitly in the registry.
"""

import contextvars
import functools
from typing import Any, Callable, Dict, Set, Tuple

from core.context_engineering.capabilities import Persona, Role, Tier

# ContextVars: set by transport layer, used by logic layer
_current_persona: contextvars.ContextVar[Persona] = contextvars.ContextVar(
    'current_persona',
    default=Persona.MCP_TOOL
)

_current_role: contextvars.ContextVar[Role] = contextvars.ContextVar(
    'current_role',
    default=Role.USER
)

_current_tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'current_tenant_id',
    default='_default'
)


class CapabilityLockError(Exception):
    """Raised when attempting to modify Tier.COMPLIANCE capabilities after boot lock."""
    pass


class CapabilityDenied(Exception):
    """Raised when a capability check fails."""
    def __init__(self, persona: Persona, role: Role, capability_id: str):
        self.persona = persona
        self.role = role
        self.capability_id = capability_id
        super().__init__(
            f"Persona {persona.value} role {role.value} missing capability {capability_id}"
        )


class CapabilityRegistry:
    """
    Central capability registry — deny-by-default.

    Load-bearing invariants:
    1. If a capability is not in the registry, it is always False
    2. Tier.COMPLIANCE capabilities cannot be revoked after boot lock
    3. Capabilities are scoped to (tenant_id, persona, role)
    """

    def __init__(self):
        # Shape: (tenant_id, persona, role) -> Set[capability_id]
        self._capabilities: Dict[Tuple[str, Persona, Role], Set[str]] = {}
        self._tier1_locked = False

    def register_capability(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
        tier: Tier = Tier.STANDARD,
        tenant_id: str = '_default',
    ) -> None:
        """Register a capability for (tenant_id, persona, role)."""
        if self._tier1_locked and tier == Tier.COMPLIANCE:
            raise CapabilityLockError(
                f"Cannot register Tier.COMPLIANCE capability {capability_id} after boot lock"
            )

        key = (tenant_id, persona, role)
        if key not in self._capabilities:
            self._capabilities[key] = set()
        self._capabilities[key].add(capability_id)

    def revoke_capability(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
        tier: Tier = Tier.STANDARD,
        tenant_id: str = '_default',
    ) -> None:
        """Revoke a capability. Tier.COMPLIANCE cannot be revoked after boot lock."""
        if self._tier1_locked and tier == Tier.COMPLIANCE:
            raise CapabilityLockError(
                f"Cannot revoke Tier.COMPLIANCE capability {capability_id} after boot lock"
            )

        key = (tenant_id, persona, role)
        if key in self._capabilities:
            self._capabilities[key].discard(capability_id)

    def has_capability(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
        tenant_id: str = '_default',
    ) -> bool:
        """
        Check: does (tenant_id, persona, role) have this capability?
        Deny-by-default: if not explicitly granted, return False.
        """
        key = (tenant_id, persona, role)
        return capability_id in self._capabilities.get(key, set())

    def lock_tier1(self) -> None:
        """Lock Tier.COMPLIANCE capabilities (called at boot, fail-closed tripwire)."""
        self._tier1_locked = True

    def unlock_tier1(self) -> None:
        """Unlock Tier.COMPLIANCE (for testing only)."""
        self._tier1_locked = False

    def get_capabilities(
        self,
        persona: Persona,
        role: Role,
        tenant_id: str = '_default',
    ) -> Set[str]:
        """Get all capabilities for (tenant_id, persona, role)."""
        key = (tenant_id, persona, role)
        return self._capabilities.get(key, set()).copy()


# Global registry instance
_REGISTRY = CapabilityRegistry()


# Public API
def get_registry() -> CapabilityRegistry:
    """Get the global capability registry."""
    return _REGISTRY


def get_current_persona() -> Persona:
    """Get current persona from context."""
    return _current_persona.get()


def set_current_persona(persona: Persona) -> None:
    """Set current persona in context (called by transport layer)."""
    _current_persona.set(persona)


def get_current_role() -> Role:
    """Get current role from context."""
    return _current_role.get()


def set_current_role(role: Role) -> None:
    """Set current role in context (called by transport layer)."""
    _current_role.set(role)


def get_current_tenant_id() -> str:
    """Get current tenant_id from context."""
    return _current_tenant_id.get()


def set_current_tenant_id(tenant_id: str) -> None:
    """Set current tenant_id in context (called by transport layer)."""
    _current_tenant_id.set(tenant_id)


def has_capability(capability_id: str) -> bool:
    """
    Check if current context (persona, role, tenant) has a capability.
    Convenience wrapper around registry.has_capability().
    """
    persona = get_current_persona()
    role = get_current_role()
    tenant_id = get_current_tenant_id()
    return _REGISTRY.has_capability(persona, role, capability_id, tenant_id)


def requires_capability(capability_id: str) -> Callable:
    """
    Decorator: enforce capability check before function execution.

    Example:
        @requires_capability("read_audit_log")
        def verify_audit():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            persona = get_current_persona()
            role = get_current_role()
            tenant_id = get_current_tenant_id()

            if not _REGISTRY.has_capability(persona, role, capability_id, tenant_id):
                raise CapabilityDenied(persona, role, capability_id)

            return func(*args, **kwargs)
        return wrapper
    return decorator
