"""Licensing Verification Endpoint (ADR-0700/0703)

POST /v1/licensing/verify — Capability verification for E2E testing and client-side checks.

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

try:
    from license.capability_api import require_capability, LicenseDenied, Decision
except ImportError:
    LicenseDenied = Exception
    def require_capability(*a, **k):
        raise RuntimeError("Licensing API unavailable")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/licensing", tags=["licensing"])


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
async def verify_capability(req: VerifyRequest, tenant_id: str = "_default") -> VerifyResponse:
    """Verify if a capability is available for the current tier.
    
    Query params:
        tenant_id: tenant scope (default: "_default")
    
    Request body:
        capability: str — e.g. "compute.run", "forge.create", "a2a.network"
        requested: int — quantity (default: 1)
        tier: str — optional; override tier for testing
    
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
        200 OK — decision made (allowed or denied)
        402 Payment Required — same as 200; included for HTTP spec compliance
        400 Bad Request — invalid request
        500 Server Error — enforcement unavailable (fail-closed: deny)
    """
    try:
        # TODO: If tier is provided, use mock credential for testing.
        # Otherwise, resolve from active credential.
        decision = require_capability(
            capability=req.capability,
            requested=req.requested,
            tenant_id=tenant_id,
            entry_point=f"verify_endpoint:{__name__}:68"
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
            tier=getattr(e, 'tier', 'unknown'),
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
