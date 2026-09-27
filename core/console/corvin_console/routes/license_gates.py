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

import logging

_log = logging.getLogger(__name__)

try:
    from corvin_operator.license.capability_api import (
        require_capability, LicenseDenied
    )
except ImportError:
    class LicenseDenied(Exception):  # type: ignore[no-redef]
        """Placeholder so the ``except`` clauses below stay valid."""

    def require_capability(*a, **k):
        raise RuntimeError("License API unavailable")


def _gate(
    rec: SessionRecord,
    *,
    capability: str,
    entry_point: str,
    deny_reason: str,
    upgrade_url: str | None = None,
) -> SessionRecord:
    """Resolve ``capability`` for the session's tenant — 402 on deny, 503 on error.

    ``require_capability`` signals a denial by RAISING ``LicenseDenied``; it
    returns a decision only for ALLOW or ENFORCEMENT_UNAVAILABLE. The 402 must
    therefore be produced from the ``LicenseDenied`` branch, and must not sit
    inside a catch-all ``except Exception`` — an ``HTTPException(402)`` raised
    inside such a block was converted into a 500 until 2026-09-27.
    Enforcement failures stay fail-closed (never pass-through).
    """
    try:
        decision = require_capability(
            capability,
            requested=1,
            tenant_id=rec.tenant_id,
            entry_point=entry_point,
        )
    except LicenseDenied as exc:
        detail: dict = {
            "error": "license_required",
            "capability": capability,
            "reason": getattr(exc, "reason", None) or deny_reason,
        }
        if upgrade_url:
            detail["upgrade_url"] = upgrade_url
        raise HTTPException(status_code=402, detail=detail) from None
    except Exception:  # noqa: BLE001 — fail-closed on enforcement error
        _log.exception("license enforcement failed for %s", capability)
        raise HTTPException(
            status_code=503,
            detail={"error": "license_enforcement_unavailable", "capability": capability},
        ) from None

    verdict = getattr(getattr(decision, "decision", None), "value", None)
    if verdict != "allow":
        # ENFORCEMENT_UNAVAILABLE (e.g. invalid tenant) comes back as a
        # decision instead of an exception — still a deny. Keyed on the
        # verdict, NOT on ``allowed``: an unlimited member tier reports
        # ``allowed=None``, which is falsy.
        raise HTTPException(
            status_code=503,
            detail={"error": "license_enforcement_unavailable", "capability": capability},
        )
    return rec


async def require_forge_capability(
    rec: SessionRecord = Depends(get_session)
) -> SessionRecord:
    """FastAPI dependency: gate forge.create capability.

    Applied to: `/skill-creator/generate`, `/tools/*/promote`,
    `/skills/*/promote`, `/panels` (POST/PUT).

    Raises:
        HTTPException(402) if the capability is denied for the tenant's tier
        HTTPException(503) if enforcement is unavailable (fail-closed)
    """
    return _gate(
        rec,
        capability="forge.create",
        entry_point="console:routes:forge",
        deny_reason="Forge is a member-only feature",
        upgrade_url="https://corvin-labs.com/upgrade",
    )


async def require_marketplace_capability(
    rec: SessionRecord = Depends(get_session)
) -> SessionRecord:
    """FastAPI dependency: gate marketplace.publish capability.

    Raises:
        HTTPException(402) if the capability is denied for the tenant's tier
        HTTPException(503) if enforcement is unavailable (fail-closed)
    """
    return _gate(
        rec,
        capability="marketplace.publish",
        entry_point="console:routes:marketplace",
        deny_reason="Publishing to the marketplace is a member-only feature",
    )
