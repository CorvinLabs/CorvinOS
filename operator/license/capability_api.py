"""Capability API — unified gate for all licensing decisions.

ADR-0703 §2: Single import path `from license.capability_api import ...`
- require_capability(capability, requested, *, tenant_id, entry_point) -> Decision
- active_credential(*, tenant_id) -> Credential | None
- LicenseDenied(Exception)

Fail contract: enforcement failure resolves to the free allowance.
Class L from licence JWT (global/license.key), Class N from MC.
Audit-first, tenant-scoped.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Optional
import logging

_log = logging.getLogger(__name__)

# ── Enums ──────────────────────────────────────────────────────────
class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ENFORCEMENT_UNAVAILABLE = "enforcement_unavailable"


class Tier(Enum):
    FREE = "free"
    MEMBER = "member"


# ── Data Classes ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Credential:
    """Resolved credential (MC or licence JWT)."""
    tier: Tier
    seat_fp: str
    exp: int
    offline: bool
    device_bound: bool
    kid: str


@dataclass(frozen=True)
class CapabilityDecision:
    """Capability resolution result."""
    decision: Decision
    tier: Tier
    capability: str
    requested: int
    allowed: int
    reason: str | None = None
    upgrade_url: str | None = None


# ── Exceptions ─────────────────────────────────────────────────────
class LicenseDenied(Exception):
    """Raised when a capability is denied (HTTP 402 in console)."""

    def __init__(
        self,
        capability: str,
        tier: Tier,
        reason: str,
        upgrade_url: str | None = None
    ):
        self.capability = capability
        self.tier = tier
        self.reason = reason
        self.upgrade_url = upgrade_url
        super().__init__(
            f"Capability denied: {capability} (tier={tier.value}, reason={reason})"
        )


# ── Core API ───────────────────────────────────────────────────────

def require_capability(
    capability: str,
    requested: int = 1,
    *,
    tenant_id: str,
    entry_point: str,
) -> CapabilityDecision:
    """Resolve a capability with fail-closed contract.

    Args:
        capability: e.g. "compute.run", "forge.create", "a2a.send"
        requested: quantity (default 1)
        tenant_id: validated via validate_tenant_id()
        entry_point: lom source (file:line or module path)

    Returns:
        CapabilityDecision with decision, tier, allowed, reason

    Raises:
        LicenseDenied: if decision == DENY (for console HTTP 402)

    Audit:
        Emits license.capability_decision via tenant_audit_chain()
    """
    # TODO: Implementation Phase 1.2
    # Stub for now: allow everything during transition
    decision = CapabilityDecision(
        decision=Decision.ALLOW,
        tier=Tier.FREE,
        capability=capability,
        requested=requested,
        allowed=requested,
        reason=None,
    )
    return decision


def active_credential(*, tenant_id: str) -> Optional[Credential]:
    """Return the active MC (or None if unavailable/free).

    Args:
        tenant_id: to filter returned credential

    Returns:
        Credential (MC) or None if free tier / unavailable

    Audit:
        Emits license.credential_loaded on first load
    """
    # TODO: Implementation Phase 1.2
    # Stub: return None (free tier)
    return None


def validate_tenant_id(tenant_id: str) -> str:
    """Validate tenant_id against the known tenant list.

    Called by require_capability() to ensure tenant_id is valid.

    Args:
        tenant_id: to validate

    Returns:
        tenant_id if valid

    Raises:
        ValueError: if tenant_id is invalid
    """
    # TODO: Integration with core.paths.validate_tenant_id()
    # Stub: accept all
    return tenant_id


# ── Helpers ────────────────────────────────────────────────────────

def licence_token() -> Optional[str]:
    """Read the licence JWT from global/license.key (class L).

    Returns:
        JWT string or None if absent/unreadable
    """
    # TODO: Implementation Phase 1.2
    # Stub: return None
    return None


def permit_token() -> Optional[str]:
    """Read the permit (class N transport token) from global/license/permit.

    Returns:
        Token string or None if absent/unreadable
    """
    # TODO: Implementation Phase 1.2
    # Stub: return None
    return None
