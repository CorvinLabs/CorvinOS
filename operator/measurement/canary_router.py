"""Deterministic canary router for Phase 1-3 context engineering optimization.

ADR-0392 § Phase 1: Measurement

Provides per-tenant percentage-based canary routing without state or database
dependency. Uses stable hash(tenant_id) % 100 to assign tenants to either
control (90%) or canary (10%) groups, ensuring the same tenant always gets
the same assignment.

Example:
    >>> router = CanaryRouter()
    >>> flags = router.route_by_tenant_percentage(
    ...     tenant_id="user_42",
    ...     feature_flags={
    ...         "vibe_engineering": True,
    ...         "per_stage_token_budgeting": True,
    ...         "adaptive_context_routing": True,
    ...     },
    ...     canary_pct=10
    ... )
    >>> flags
    {'vibe_engineering': True if hash(user_42) % 100 < 10 else False, ...}
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CanaryRouter",
]


@dataclass(frozen=True)
class CanaryRouter:
    """Deterministic, stateless canary router for A/B testing feature flags.

    Routes tenants to control (90%) or canary (10%) groups based on a stable
    hash of tenant_id. The same tenant_id always gets the same assignment.
    """

    def _tenant_percentage(self, tenant_id: str) -> int:
        """Return the percentage bucket (0-99) for a tenant, deterministically.

        Uses SHA256(tenant_id) to produce a stable hash, then mods 100.
        This ensures the same tenant_id always lands in the same bucket.
        """
        h = hashlib.sha256(tenant_id.encode("utf-8"), usedforsecurity=False)
        return int(h.hexdigest()[:8], 16) % 100

    def is_canary_tenant(self, tenant_id: str, canary_pct: int = 10) -> bool:
        """Check if a tenant is assigned to the canary group.

        Args:
            tenant_id: Unique identifier for the tenant.
            canary_pct: Percentage of tenants in the canary group (0-100).

        Returns:
            True if the tenant is in the canary group, False otherwise.
        """
        if not 0 <= canary_pct <= 100:
            raise ValueError(f"canary_pct must be in [0, 100], got {canary_pct}")
        return self._tenant_percentage(tenant_id) < canary_pct

    def route_by_tenant_percentage(
        self,
        tenant_id: str,
        feature_flags: dict[str, bool],
        canary_pct: int = 10,
    ) -> dict[str, bool]:
        """Route a tenant's feature flags based on canary percentage.

        For each flag in feature_flags, if the flag is enabled AND the tenant
        is in the canary group, the flag stays enabled. Otherwise, it's
        disabled (control group gets the baseline with flags OFF).

        This implements the "Phase 1-3 OFF for control, ON for canary" pattern:
        - Control (90%): all Phase 1-3 flags OFF, measure baseline
        - Canary (10%): Phase 1-3 flags ON, measure new behavior

        Args:
            tenant_id: Unique identifier for the tenant.
            feature_flags: Dict of {flag_id: currently_enabled}.
            canary_pct: Percentage of tenants in canary group (default 10).

        Returns:
            Dict of {flag_id: enabled_for_this_tenant}.
        """
        if not 0 <= canary_pct <= 100:
            raise ValueError(f"canary_pct must be in [0, 100], got {canary_pct}")

        is_canary = self.is_canary_tenant(tenant_id, canary_pct)

        # Canary: keep flags as-is; Control: disable all Phase 1-3 flags
        result = {}
        for flag_id, enabled in feature_flags.items():
            if is_canary:
                # Canary group: use the flag as-is
                result[flag_id] = enabled
            else:
                # Control group: disable all flags for measurement
                # (Phase 1-3 flags must be OFF in the baseline)
                result[flag_id] = False

        return result

    def report_assignment(
        self, tenant_id: str, canary_pct: int = 10
    ) -> dict[str, Any]:
        """Return diagnostic info about a tenant's canary assignment.

        Useful for debugging and audit logs.

        Args:
            tenant_id: Unique identifier for the tenant.
            canary_pct: Percentage of tenants in canary group (default 10).

        Returns:
            Dict with 'tenant_id', 'percentage_bucket', 'is_canary', 'group'.
        """
        bucket = self._tenant_percentage(tenant_id)
        is_canary = bucket < canary_pct
        return {
            "tenant_id": tenant_id,
            "percentage_bucket": bucket,
            "is_canary": is_canary,
            "group": "canary" if is_canary else "control",
            "canary_pct": canary_pct,
        }
