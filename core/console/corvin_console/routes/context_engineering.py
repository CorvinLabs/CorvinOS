"""Context Engineering Configuration API Routes.

GET/POST/PUT/DELETE /v1/console/context-engineering/config
GET /v1/console/context-engineering/quota
POST /v1/console/context-engineering/quota/reset
GET /v1/console/context-engineering/metrics

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — this
router is not mounted by ``corvin_console.app``.

Defused 2026-09-27. The routes had NO authentication (``require_operator``
returned "operator" for every caller), took ``tenant_id`` from the query
string (any caller could read/write any tenant's config, and the unvalidated
id was a path component), and ``/quota``, ``/quota/reset`` and ``/metrics``
returned fabricated values (0 units used, "reset" without a counter, zero
turns) as if measured. Now: every route needs a console session, mutations a
CSRF-signed one; the tenant is the session's; config mutations are audited on
that tenant's chain; quota/metrics answer 501 ``not_implemented``.
"""

from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from .. import audit as console_audit
from .. import auth as session_auth
from ..context_engineering_config import (
    ContextEngineeringConfigManager,
    ConfigValidationError,
)
from ..deps import require_csrf, require_session

router = APIRouter(
    prefix="/v1/console/context-engineering",
    tags=["context-engineering"],
)

Session = Annotated[session_auth.SessionRecord, Depends(require_session)]
Mutation = Annotated[session_auth.SessionRecord, Depends(require_csrf)]


def _audit(rec: session_auth.SessionRecord, action: str) -> None:
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=f"context_engineering.{action}",
        target_kind="config",
        target_id="context-engineering",
    )


def _not_implemented() -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={"status": "not_implemented",
                "reason": "context-engineering quota/metrics are not measured on this build"},
    )


@router.get("/config")
async def get_config(rec: Session) -> Dict[str, Any]:
    """Get the session tenant's CE config."""
    return ContextEngineeringConfigManager(rec.tenant_id).dict()


@router.post("/config")
async def update_config(changes: Dict[str, Any], rec: Mutation) -> Dict[str, Any]:
    """Update CE config (atomic, merges with current)."""
    mgr = ContextEngineeringConfigManager(rec.tenant_id)
    try:
        new_config = await mgr.update(changes)
    except ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(rec, "config_updated")
    return {"status": "updated", "config": new_config.dict()}


@router.put("/config")
async def replace_config(config: Dict[str, Any], rec: Mutation) -> Dict[str, Any]:
    """Replace entire config (validation first)."""
    mgr = ContextEngineeringConfigManager(rec.tenant_id)
    try:
        new_config = await mgr.update(config)
    except ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(rec, "config_replaced")
    return {"status": "replaced", "config": new_config.dict()}


@router.delete("/config")
async def reset_config(rec: Mutation) -> Dict[str, Any]:
    """Reset to defaults."""
    new_config = await ContextEngineeringConfigManager(rec.tenant_id).reset_to_defaults()
    _audit(rec, "config_reset")
    return {"status": "reset", "config": new_config.dict()}


@router.get("/quota")
async def get_quota(rec: Session) -> Dict[str, Any]:
    """NOT IMPLEMENTED (501): no quota counter exists."""
    raise _not_implemented()


@router.post("/quota/reset")
async def reset_quota(rec: Mutation) -> Dict[str, Any]:
    """NOT IMPLEMENTED (501): there is no counter to reset."""
    raise _not_implemented()


@router.get("/metrics")
async def get_metrics(rec: Session) -> Dict[str, Any]:
    """NOT IMPLEMENTED (501): no CE usage metrics are collected."""
    raise _not_implemented()
