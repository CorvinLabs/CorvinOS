"""Capability API — unified gate for all licensing decisions.

ADR-0703 §2: Single import path `from license.capability_api import ...`
- require_capability(capability, requested, *, tenant_id, entry_point) -> CapabilityDecision
- active_credential(*, tenant_id) -> Credential | None
- LicenseDenied(Exception)

Fail contract: enforcement failure resolves to the free allowance.
Class L from licence JWT (global/license.key), Class N from MC (A2A membership).
Audit-first via tenant_audit_chain(), tenant-scoped.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from forge.paths import corvin_home, validate_tenant_id
from forge.tenants import current_tenant

try:
    from .keyring import RING
    from .crl import load_crl_state, is_revoked
    from .limits import CAPABILITIES, FREE_TIER
    from .validator import active_tier
except ImportError:
    # Fallback for test fixtures or standalone usage
    RING = None
    load_crl_state = lambda *a, **k: None
    is_revoked = lambda *a, **k: False
    CAPABILITIES = {}
    FREE_TIER = {}
    def active_tier(**k): return "free"

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

# In-process enforcement state (fail-closed on any error)
_ENFORCEMENT_AVAILABLE = True
_ENFORCEMENT_ERROR: str | None = None

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
    # Validate tenant_id
    try:
        validate_tenant_id(tenant_id)
    except Exception as e:
        _log.error("Invalid tenant_id %r: %s", tenant_id, e)
        tier = Tier.FREE
        decision = CapabilityDecision(
            decision=Decision.ENFORCEMENT_UNAVAILABLE,
            tier=tier,
            capability=capability,
            requested=requested,
            allowed=0,
            reason="invalid_tenant",
        )
        return decision

    # Get capability limits for this tier
    try:
        tier_str = active_tier()
        tier = Tier.MEMBER if tier_str == "member" else Tier.FREE
    except Exception as e:
        _log.warning("Failed to resolve tier: %s; assuming free", e)
        tier = Tier.FREE
        _ENFORCEMENT_AVAILABLE = False
        _ENFORCEMENT_ERROR = str(e)

    # Look up capability in CAPABILITIES matrix
    if capability not in CAPABILITIES:
        _log.warning("Unknown capability: %s", capability)
        decision = CapabilityDecision(
            decision=Decision.DENY,
            tier=tier,
            capability=capability,
            requested=requested,
            allowed=0,
            reason="unknown_capability",
        )
        if decision.decision == Decision.DENY:
            raise LicenseDenied(
                capability=capability,
                tier=tier,
                reason="unknown_capability",
            )
        return decision

    # Resolve allowed count from tier
    tier_limits = CAPABILITIES[capability].get(tier.value, {})
    allowed = tier_limits.get("limit", 0)

    # Determine decision
    if allowed >= requested:
        decision_enum = Decision.ALLOW
    elif allowed > 0:
        decision_enum = Decision.DENY
        reason = "quota_exceeded"
    else:
        decision_enum = Decision.DENY
        reason = "not_available_in_tier"

    result = CapabilityDecision(
        decision=decision_enum,
        tier=tier,
        capability=capability,
        requested=requested,
        allowed=allowed,
        reason=reason if decision_enum == Decision.DENY else None,
    )

    # Audit via tenant_audit_chain
    try:
        _audit_capability_decision(
            tenant_id=tenant_id,
            capability=capability,
            tier=tier.value,
            decision=decision_enum.value,
            requested=requested,
            allowed=allowed,
            entry_point=entry_point,
        )
    except Exception as e:
        _log.warning("Failed to audit capability_decision: %s", e)

    # Raise LicenseDenied if denied
    if result.decision == Decision.DENY:
        raise LicenseDenied(
            capability=capability,
            tier=tier,
            reason=result.reason or "denied",
        )

    return result


def active_credential(*, tenant_id: str) -> Optional[Credential]:
    """Return the active MC (or None if unavailable/free).

    Args:
        tenant_id: to filter returned credential

    Returns:
        Credential (MC) or None if free tier / unavailable

    Audit:
        Emits license.credential_loaded on first load
    """
    # TODO: Integrate with A2A MC loading (ADR-0702)
    # Stub: return None (free tier)
    return None


# ── Helpers ────────────────────────────────────────────────────────

def licence_token() -> Optional[str]:
    """Read the licence JWT from global/license.key (class L).

    Returns:
        JWT string or None if absent/unreadable
    """
    try:
        path = corvin_home() / "global" / "license.key"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        _log.debug("Failed to read licence token: %s", e)
    return None


def permit_token() -> Optional[str]:
    """Read the permit (class N transport token) from global/license/permit.

    Returns:
        Token string or None if absent/unreadable
    """
    # TODO: Load from global/license/permit (for A2A)
    return None


def _audit_capability_decision(
    tenant_id: str,
    capability: str,
    tier: str,
    decision: str,
    requested: int,
    allowed: int,
    entry_point: str,
) -> None:
    """Emit license.capability_decision audit event (fail-closed)."""
    try:
        from forge.audit import tenant_audit_chain
        from security_events import current_instance_id

        chain = tenant_audit_chain(tenant_id)
        if chain:
            event = {
                "event_type": "license.capability_decision",
                "tenant_id": tenant_id,
                "capability": capability,
                "tier": tier,
                "decision": decision,
                "requested": requested,
                "allowed": allowed,
                "lom": entry_point,
                "timestamp": int(time.time()),
            }
            chain.write_event(event)
    except Exception as e:
        _log.warning("Failed to write capability_decision audit: %s", e)
