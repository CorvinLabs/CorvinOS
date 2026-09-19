"""License gating dependencies for FastAPI routes (ADR-0701)

This module provides FastAPI dependencies for checking forge/marketplace capabilities.
All routes that create or promote artifacts must use these gates.
"""

from fastapi import Depends, HTTPException
from pydantic import BaseModel

# ``corvin_console.middleware.auth`` does not exist (commit eaf3a2a1 imported it;
# a fresh ``import corvin_console.app`` then failed at routes/promote.py, which
# means the NEXT restart of corvin-webui would have booted WITHOUT the console —
# ADR-0015 mounts it through ``try: import corvin_console``). The session
# record and the dependency that produces it live in auth.py / deps.py.
from ..auth import SessionRecord
from ..deps import require_session as get_session

try:
    from corvin_operator.license.capability_api import (
        require_capability, LicenseDenied
    )
except ImportError:
    def require_capability(*a, **k):
        raise RuntimeError("License API unavailable")


async def require_forge_capability(
    rec: SessionRecord = Depends(get_session)
) -> SessionRecord:
    """FastAPI dependency: gate forge.create capability.

    Applied to: `/skill-creator/generate`, `/skills/manual`, `/tools/*/promote`,
    `/skills/*/promote`, `/panels` (POST/PUT), and plugin lifecycle `install()`.

    On deny: returns HTTP 402 Payment Required with error details.
    On error: fail-closed, deny.

    Args:
        rec: SessionRecord with tenant_id and user context

    Returns:
        rec if allowed (passing through to the route)

    Raises:
        HTTPException(402) if capability is denied
        HTTPException(500) if enforcement fails
    """
    try:
        decision = require_capability(
            "forge.create",
            requested=1,
            tenant_id=rec.tenant_id,
            entry_point=f"console:routes:forge"
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "license_required",
                    "capability": "forge.create",
                    "reason": decision.reason or "Forge is a member-only feature",
                    "upgrade_url": "https://corvin-labs.com/upgrade"
                }
            )
        return rec
    except (ImportError, LicenseDenied, Exception) as e:
        # Fail-closed on enforcement error
        raise HTTPException(
            status_code=500,
            detail={"error": "license_enforcement_unavailable", "reason": str(e)}
        )


async def require_marketplace_capability(
    rec: SessionRecord = Depends(get_session)
) -> SessionRecord:
    """FastAPI dependency: gate marketplace.publish capability.

    Applied to: POST /marketplace/submit

    On deny: returns HTTP 402 Payment Required
    """
    try:
        decision = require_capability(
            "marketplace.publish",
            requested=1,
            tenant_id=rec.tenant_id,
            entry_point=f"console:routes:marketplace"
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "license_required",
                    "capability": "marketplace.publish",
                    "reason": "Publishing to the marketplace is a member-only feature"
                }
            )
        return rec
    except (ImportError, LicenseDenied, Exception) as e:
        # Fail-closed on enforcement error
        raise HTTPException(
            status_code=500,
            detail={"error": "license_enforcement_unavailable", "reason": str(e)}
        )
