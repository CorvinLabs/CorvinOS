"""Instance identity / IBC status — GET /v1/console/settings/instance-identity.

ADR-0145 M4: read-only console surface for the instance's IBC (Instance
Binding Certificate) status, so an operator can see their instance_id and
binding state without a CLI. All fields come from local disk state only —
this route performs no outbound network calls (no CRL fetch, no bind
request), so a page load never blocks on Corvin Labs reachability.

Auth: owner tier only, same bar as ``license.py``'s ``/status`` — the
instance_id itself is non-secret (ADR-0145 threat model), but IBC binding
state is still account-adjacent detail not meant for non-owner tenant
members.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import auth as session_auth
from ..deps import require_session

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]

_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

try:
    from instance_identity import (  # type: ignore[import-not-found]
        check_hardware_binding,
        get_ibc,
        instance_id_metadata,
        revocation_status_cached,
    )
    _IDENTITY_OK = True
except ImportError:
    _IDENTITY_OK = False


router = APIRouter(prefix="/settings")


class InstanceIdentityStatus(BaseModel):
    """Local-only snapshot of this instance's identity + IBC binding state."""

    instance_id: str
    label: str
    ibc_bound: bool
    plan: str | None = None
    email: str | None = None
    expires_at: int | None = None
    hardware_bound: bool
    hardware_matches: bool | None = None
    revocation_status: str  # "revoked" | "clean" | "unknown"

    model_config = {"extra": "forbid"}


@router.get("/instance-identity", response_model=InstanceIdentityStatus)
async def get_instance_identity_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> InstanceIdentityStatus:
    """Local instance_id + IBC binding snapshot for the console Dashboard."""
    if rec.tier != "owner":
        from fastapi import HTTPException, status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )
    if not _IDENTITY_OK:
        return InstanceIdentityStatus(
            instance_id="",
            label="",
            ibc_bound=False,
            hardware_bound=False,
            revocation_status="unknown",
        )

    meta = instance_id_metadata()
    ibc = get_ibc()
    hw = check_hardware_binding()

    # IBC claims carry email/license/plan — display-only, never written to
    # audit details (ADR-0145 "what not to do": no email/license_id in the
    # audit chain). This is a direct, authenticated owner-only read, not an
    # audit event, so surfacing them here is fine.
    plan = ibc.get("plan") if ibc else None
    email = ibc.get("email") if ibc else None
    expires_at = ibc.get("exp") if ibc else None

    return InstanceIdentityStatus(
        instance_id=meta.get("instance_id", ""),
        label=meta.get("label", ""),
        ibc_bound=ibc is not None,
        plan=plan,
        email=email,
        expires_at=expires_at,
        hardware_bound=hw["bound"],
        hardware_matches=hw["matches"],
        revocation_status=revocation_status_cached(),
    )
