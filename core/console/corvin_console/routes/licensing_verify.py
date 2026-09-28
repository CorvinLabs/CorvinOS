"""Licensing Verification Endpoint (ADR-0700/0703)

POST /v1/console/licensing/verify — Capability verification for E2E testing and
client-side checks. (The router is included in the console router, which both
hosts mount under ``/v1/console``; until 2026-09-27 it carried its own
``/v1/licensing`` prefix and was therefore only reachable at the doubled
``/v1/console/v1/licensing/verify``.)

This endpoint is **NOT** the enforcement gate; enforcement happens at each chokepoint
via require_capability(). This endpoint is for:
1. E2E testing (verify the API returns correct decisions)
2. Client-side "check if you have access" calls (optional)
3. Audit verification (confirm audit trail recorded)

License: Apache-2.0
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi import Depends
from ..auth import SessionRecord
from ..deps import require_session, require_session_csrf_on_mutation

# The canonical package path — the one license_gates.py and every G-gate use.
# The bare ``license.capability_api`` is a SECOND module instance (own
# LicenseDenied class, own tier resolver) and is not importable at all on the
# shipped hosts, whose PYTHONPATH does not carry ``corvin_operator/``.
try:
    from corvin_operator.license.capability_api import (
        require_capability, LicenseDenied, Decision,
    )
except ImportError:
    class LicenseDenied(Exception):  # type: ignore[no-redef]
        """Placeholder so the ``except`` clause below stays valid."""

    def require_capability(*a, **k):
        raise RuntimeError("Licensing API unavailable")

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], prefix="/licensing", tags=["licensing"])


# ============================================================================
# Request/Response Models
# ============================================================================

class VerifyRequest(BaseModel):
    """Capability verification request."""
    capability: str
    requested: int = 1
    tier: Optional[str] = None  # optional; resolved from credential if absent


class VerifyResponse(BaseModel):
    """Capability verification response."""
    allowed: bool
    capability: str
    requested: int
    tier: str
    quota_remaining: Optional[int] = None
    reason: Optional[str] = None
    upgrade_url: Optional[str] = None


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/verify", response_model=VerifyResponse)
async def verify_capability(
    req: VerifyRequest,
    rec: SessionRecord = Depends(require_session),
) -> VerifyResponse:
    """Verify if a capability is available for the current tier.

    SECURITY FIX (2026-09-22):
    - Added authentication requirement (require_session)
    - Tenant is determined from authenticated session (not user input)
    - Prevents unauthenticated reconnaissance of other tenants' license info

    Request body:
        capability: str — e.g. "compute.run", "forge.create", "a2a.network"
        requested: int — quantity (default: 1)
        tier: str — IGNORED; the tier always comes from the installed licence

    Response:
        {
            "allowed": bool,
            "capability": str,
            "requested": int,
            "tier": str,
            "quota_remaining": int | null,
            "reason": str | null,
            "upgrade_url": str | null
        }
    
    Status codes:
        200 OK — decision made (allowed or denied; a denial is ``allowed: false``)
        401 / 403 — no session / missing CSRF token
        422 Unprocessable — malformed body or ``requested < 1``
        Enforcement failure answers 200 with ``reason: enforcement_unavailable``
        (fail-closed: deny).
    """
    if req.requested < 1:
        raise HTTPException(status_code=422, detail="requested must be a positive integer")
    try:
        # The tier ALWAYS comes from the licence; ``req.tier`` is ignored.
        # The tenant comes from the authenticated session — until 2026-09-27
        # this read an undefined ``tenant_id`` and the swallowed NameError
        # answered "enforcement_unavailable" for every request.
        decision = require_capability(
            capability=req.capability,
            requested=req.requested,
            tenant_id=rec.tenant_id,
            entry_point="http:licensing_verify",
        )
        
        response = VerifyResponse(
            allowed=decision.decision == Decision.ALLOW,
            capability=req.capability,
            requested=req.requested,
            tier=decision.tier.value,
            quota_remaining=decision.allowed,
            reason=decision.reason,
            upgrade_url=decision.upgrade_url
        )
        
        # HTTP 402 if denied (optional; 200 is also valid per spec)
        if not response.allowed:
            return response  # Return 200 with allowed=false
        
        return response
    
    except LicenseDenied as e:
        return VerifyResponse(
            allowed=False,
            capability=req.capability,
            requested=req.requested,
            tier=getattr(getattr(e, "tier", None), "value", "free"),
            reason=e.reason,
            upgrade_url=e.upgrade_url
        )
    
    except Exception as e:
        logger.exception(f"Licensing verification failed: {e}")
        # Fail-closed: deny on any error
        return VerifyResponse(
            allowed=False,
            capability=req.capability,
            requested=req.requested,
            tier="free",
            reason="enforcement_unavailable",
            upgrade_url=None
        )
