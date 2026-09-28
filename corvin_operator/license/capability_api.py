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

from corvin_operator.forge.forge.paths import corvin_home
# `validate_tenant_id` is NOT in forge.paths — that module keeps a private
# `_validate_tenant_id` copy (raising ValueError). The canonical validator,
# the one CLAUDE.md § Multi-tenant Axis names, lives in forge.tenants and
# raises InvalidTenantID. Importing the non-existent public name made this
# whole module — and every licensing capability check that goes through
# it — raise ImportError (2026-09-20 review).
from corvin_operator.forge.forge.tenants import validate_tenant_id
from corvin_operator.forge.forge.tenants import current_tenant

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
    CAPABILITIES = {
        "compute.run": {"class": "L", "free": {"limit": 10}, "member": {"limit": None}},
        "forge.create": {"class": "L", "free": {"limit": 0}, "member": {"limit": None}},
        "a2a.network": {"class": "N", "free": {"limit": 0}, "member": {"limit": None}},
        "chat.turns": {"class": "B", "free": {"limit": None}, "member": {"limit": None}},
    }
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
        requested: quantity — a positive ``int`` (``ValueError`` otherwise)
        tenant_id: validated via validate_tenant_id()
        entry_point: lom source (file:line or module path)

    Returns:
        CapabilityDecision whose ``decision`` is ALWAYS ``Decision.ALLOW``.
        ``allowed`` is the tier LIMIT (``None`` = unlimited), not a verdict —
        callers must test ``decision.decision``, never ``decision.allowed``.

    Raises:
        LicenseDenied: on every non-allow outcome — a tier/quota denial, an
            unknown capability, an invalid tenant (``reason="invalid_tenant"``)
            and an enforcement failure that leaves only the free allowance,
            where that allowance does not cover the request.
        ValueError: ``requested`` is not a positive integer.

    Audit:
        Emits ``license.capability_decision`` on the tenant's chain
        (``tenant_audit_chain``) before the verdict takes effect; a write that
        does not commit is logged (see the comment at the call).

    Adversarial review 2026-09-27: an invalid tenant used to be RETURNED as
    ``ENFORCEMENT_UNAVAILABLE`` — every caller that only caught
    ``LicenseDenied`` (G1, G5) then went ahead as if allowed; ``requested <= 0``
    passed the ``limit >= requested`` test on every finite tier; and the audit
    writer imported a module that does not exist, so no decision was ever
    recorded.
    """
    # A quantity is a positive integer. 0 / negative values satisfied
    # ``limit >= requested`` on every finite tier (bool is an int subclass).
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError(
            f"requested must be a positive integer, got {requested!r}"
        )

    # Validate tenant_id — an unresolvable tenant is a DENY, never a verdict
    # the caller has to remember to inspect.
    try:
        validate_tenant_id(tenant_id)
    except Exception as e:
        _log.error("Invalid tenant_id %r: %s", tenant_id, e)
        raise LicenseDenied(
            capability=capability, tier=Tier.FREE, reason="invalid_tenant",
        ) from None

    # Get capability limits for this tier
    try:
        tier_str = active_tier()
        tier = Tier.MEMBER if tier_str == "member" else Tier.FREE
    except Exception as e:
        _log.warning("Failed to resolve tier: %s; assuming free", e)
        tier = Tier.FREE

    decision_enum, allowed, reason = _evaluate(capability, requested, tier)

    # Recorded before it takes effect. A write that does not commit is logged
    # and does NOT change the verdict: ``write_event`` refuses a record whose
    # tenant differs from the PROCESS tenant (AuditTenantMismatch), so in a
    # console serving several tenants, making the grant depend on the write
    # would lock every member tenant but the process's own out of member
    # capabilities. The chain's health is the ADR-0232 boot tripwire's job.
    _audit_capability_decision(
        tenant_id=tenant_id,
        capability=capability,
        tier=tier.value,
        decision=decision_enum.value,
        reason=reason,
        requested=requested,
        allowed=allowed,
        entry_point=entry_point,
    )

    # Raise LicenseDenied if denied
    if decision_enum == Decision.DENY:
        raise LicenseDenied(
            capability=capability,
            tier=tier,
            reason=reason or "denied",
        )

    return CapabilityDecision(
        decision=decision_enum,
        tier=tier,
        capability=capability,
        requested=requested,
        allowed=allowed,
        reason=None,
    )


def _evaluate(capability: str, requested: int, tier: Tier):
    """Pure lookup in the CAPABILITIES matrix → (Decision, limit, reason)."""
    if capability not in CAPABILITIES:
        _log.warning("Unknown capability: %s", capability)
        return Decision.DENY, 0, "unknown_capability"
    tier_limits = CAPABILITIES[capability].get(tier.value, {})
    allowed = tier_limits.get("limit", 0)
    if allowed is None:
        return Decision.ALLOW, None, None          # None = unlimited
    if isinstance(allowed, bool) or not isinstance(allowed, int):
        return Decision.DENY, 0, "enforcement_unavailable"   # malformed matrix
    if allowed == 0:
        return Decision.DENY, 0, "not_available_in_tier"
    if allowed >= requested:
        return Decision.ALLOW, allowed, None
    return Decision.DENY, allowed, "quota_exceeded"


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


def _chain_writer():
    """Return ``(write_event, tenant_audit_chain)`` — THE core chain writer.

    Imported as the bare ``security_events`` / ``paths`` modules (with the
    forge package dir on ``sys.path``), exactly like ``validator.py`` does, so
    the process-wide instance that carries the chain-DNA seed does the write.
    """
    import sys as _sys
    _forge_inner = Path(__file__).resolve().parents[1] / "forge" / "forge"
    if str(_forge_inner) not in _sys.path:
        _sys.path.insert(0, str(_forge_inner))
    from security_events import write_event  # type: ignore[import]
    from paths import tenant_audit_chain  # type: ignore[import]
    return write_event, tenant_audit_chain


def _audit_capability_decision(
    tenant_id: str,
    capability: str,
    tier: str,
    decision: str,
    requested: int,
    allowed: int | None,
    entry_point: str,
    reason: str | None = None,
) -> bool:
    """Write ``license.capability_decision`` to the tenant chain; True iff committed.

    Never raises — the caller decides what an unrecorded decision means.
    """
    try:
        write_event, tenant_audit_chain = _chain_writer()
        write_event(
            tenant_audit_chain(tenant_id),
            "license.capability_decision",
            details={
                "tenant_id": tenant_id,
                "capability": capability,
                "tier": tier,
                "decision": decision,
                "reason": reason,
                "requested": requested,
                "allowed": allowed,
                "entry_point": entry_point,
                "lom": "corvin_operator/license/capability_api.py:require_capability",
            },
        )
        return True
    except Exception as e:  # noqa: BLE001 — reported to the caller as False
        _log.warning("Failed to write capability_decision audit: %s", e)
        return False
