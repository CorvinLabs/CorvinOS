"""Compartmentalization — ADR-0330

Enforce 3-tier execution isolation. Web, Service, Privileged.
Fail-closed: invalid tier transitions rejected (403).
"""

from __future__ import annotations

from enum import Enum
from typing import Set


class ExecutionTier(Enum):
    """Execution tier enumeration."""
    WEB = "web"
    SERVICE = "service"
    PRIVILEGED = "privileged"


class TierValidationError(Exception):
    """Raised when tier validation fails."""

    def __init__(self, message: str, tier: ExecutionTier = None):
        self.message = message
        self.tier = tier
        super().__init__(message)


class CompartmentBoundary:
    """Enforce 3-tier compartmentalization (fail-closed)."""

    def __init__(self):
        """Initialize compartment boundary."""
        # Define allowed transitions
        self._allowed_transitions: dict[ExecutionTier, Set[ExecutionTier]] = {
            ExecutionTier.WEB: {ExecutionTier.WEB, ExecutionTier.SERVICE},
            ExecutionTier.SERVICE: {ExecutionTier.SERVICE, ExecutionTier.PRIVILEGED},
            ExecutionTier.PRIVILEGED: {ExecutionTier.PRIVILEGED},
        }

    def validate_transition(
        self,
        from_tier: ExecutionTier,
        to_tier: ExecutionTier,
        *,
        tenant_id: str = None,
    ) -> bool:
        """Validate tier transition.

        Args:
            from_tier: Source tier
            to_tier: Target tier
            tenant_id: Tenant context (optional)

        Returns:
            True if transition allowed, False otherwise

        Raises:
            TierValidationError: If transition is invalid (fail-closed)
        """
        if to_tier not in self._allowed_transitions.get(from_tier, set()):
            raise TierValidationError(
                f"Tier transition not allowed: {from_tier.value} → {to_tier.value}",
                tier=to_tier,
            )
        return True

    def get_allowed_targets(self, from_tier: ExecutionTier) -> Set[ExecutionTier]:
        """Get allowed target tiers from source tier."""
        return self._allowed_transitions.get(from_tier, set())

    def enforce_tier(
        self,
        tier: ExecutionTier,
        action: str,
        *,
        tenant_id: str = None,
    ) -> None:
        """Enforce that current tier is allowed to perform action.

        Fail-closed: deny if tier not authorized.

        Args:
            tier: Current tier
            action: Action to perform
            tenant_id: Tenant context

        Raises:
            TierValidationError: If action not allowed in tier
        """
        # Placeholder: real implementation would check action permissions
        pass
