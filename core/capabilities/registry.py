"""
Capability Registry — deny-by-default access control.

Every actor/tenant/capability tuple defaults to DENIED.
Only explicit grants are permitted.
All grants are immutable (no revocation without audit trail).
"""

from contextvars import ContextVar
from typing import Set, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime


CapabilityTuple = Tuple[str, str, str]  # (actor, capability, tenant_id)


class CapabilityGrantError(Exception):
    """Failed to grant capability."""

    pass


class CapabilityDeniedError(Exception):
    """Capability check resulted in denial."""

    pass


@dataclass
class CapabilityGrant:
    """One capability grant record."""

    actor: str
    capability: str
    tenant_id: str
    granted_at: str
    granted_by: str  # Who/what process granted this
    reason: str  # Why is this capability granted


class CapabilityRegistry:
    """Deny-by-default capability registry."""

    def __init__(self):
        """Initialize empty registry (all denied by default)."""
        self._grants: dict[CapabilityTuple, CapabilityGrant] = {}
        self._readonly = False

    def grant(
        self,
        actor: str,
        capability: str,
        tenant_id: str,
        granted_by: str = "system",
        reason: str = "administrative grant",
    ) -> CapabilityGrant:
        """
        Grant a capability to an actor (idempotent).

        Args:
            actor: Who gets the capability (user ID, service, etc)
            capability: Capability name (e.g., "read_settings", "admin")
            tenant_id: Tenant scope
            granted_by: Who/what process granted this (for audit)
            reason: Reason for grant (for audit)

        Returns:
            CapabilityGrant record

        Raises:
            CapabilityGrantError if registry is readonly
        """
        if self._readonly:
            raise CapabilityGrantError("Registry is readonly (in production mode)")

        key = (actor, capability, tenant_id)

        # Check if already granted (idempotent)
        if key in self._grants:
            return self._grants[key]

        # Record grant with timestamp
        grant = CapabilityGrant(
            actor=actor,
            capability=capability,
            tenant_id=tenant_id,
            granted_at=datetime.utcnow().isoformat(),
            granted_by=granted_by,
            reason=reason,
        )

        self._grants[key] = grant
        return grant

    def has_capability(
        self, actor: str, capability: str, tenant_id: str
    ) -> bool:
        """
        Check if actor has capability (deny-by-default).

        Args:
            actor: Actor to check
            capability: Capability to check
            tenant_id: Tenant scope

        Returns:
            True if capability granted, False if denied (default)
        """
        key = (actor, capability, tenant_id)
        return key in self._grants

    def check_capability(
        self, actor: str, capability: str, tenant_id: str
    ) -> None:
        """
        Check capability and raise if denied.

        Args:
            actor: Actor to check
            capability: Capability required
            tenant_id: Tenant scope

        Raises:
            CapabilityDeniedError if capability not granted
        """
        if not self.has_capability(actor, capability, tenant_id):
            raise CapabilityDeniedError(
                f"Actor {actor} denied {capability} on tenant {tenant_id}"
            )

    def get_grants_for_actor(self, actor: str, tenant_id: str) -> list[str]:
        """Get all capabilities granted to actor."""
        return [
            cap
            for (a, cap, t), _ in self._grants.items()
            if a == actor and t == tenant_id
        ]

    def get_grant_record(
        self, actor: str, capability: str, tenant_id: str
    ) -> Optional[CapabilityGrant]:
        """Get detailed grant record (for audit trail)."""
        key = (actor, capability, tenant_id)
        return self._grants.get(key)

    def freeze(self) -> None:
        """Make registry readonly (for production mode)."""
        self._readonly = True

    def is_readonly(self) -> bool:
        """Check if registry is frozen."""
        return self._readonly

    def grant_count(self) -> int:
        """Total number of grants."""
        return len(self._grants)

    def get_all_grants(self) -> list[CapabilityGrant]:
        """Get all grant records (for audit/migration)."""
        return list(self._grants.values())


# Global registry instance
_REGISTRY = CapabilityRegistry()

# ContextVar for current registry (allows test isolation)
_current_registry: ContextVar[CapabilityRegistry] = ContextVar(
    "capability_registry", default=_REGISTRY
)


def get_registry() -> CapabilityRegistry:
    """Get current capability registry."""
    return _current_registry.get()


def set_registry(registry: CapabilityRegistry) -> None:
    """Set current registry (for testing)."""
    _current_registry.set(registry)
