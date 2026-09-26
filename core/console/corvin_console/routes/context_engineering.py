"""Context Engineering Configuration API Routes.

GET/POST/PUT/DELETE /v1/console/context-engineering/config
GET /v1/console/context-engineering/quota
POST /v1/console/context-engineering/quota/reset
GET /v1/console/context-engineering/metrics

Operator-only (requires role check).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Any, Optional
from ..context_engineering_config import (
    ContextEngineeringConfigManager,
    ConfigValidationError,
)

router = APIRouter(
    prefix="/v1/console/context-engineering",
    tags=["context-engineering"],
)


# Dependency: verify operator role (TODO: implement RBAC)
async def require_operator(request) -> str:
    """Dependency: verify operator role."""
    # TODO: Implement RBAC check (ADR-0035, house-rules)
    # For now: allow all (TODO: fix in Phase 1, Week 3 with auth)
    return "operator"


@router.get("/config")
async def get_config(
    tenant_id: str = Query("_default"),
    _: str = Depends(require_operator),
) -> Dict[str, Any]:
    """Get current CE config."""
    mgr = ContextEngineeringConfigManager(tenant_id)
    return mgr.dict()


@router.post("/config")
async def update_config(
    changes: Dict[str, Any],
    tenant_id: str = Query("_default"),
    _: str = Depends(require_operator),
) -> Dict[str, Any]:
    """Update CE config (atomic, merges with current)."""
    mgr = ContextEngineeringConfigManager(tenant_id)
    try:
        new_config = await mgr.update(changes)
        return {"status": "updated", "config": new_config.dict()}
    except ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/config")
async def replace_config(
    config: Dict[str, Any],
    tenant_id: str = Query("_default"),
    _: str = Depends(require_operator),
) -> Dict[str, Any]:
    """Replace entire config (validation first)."""
    mgr = ContextEngineeringConfigManager(tenant_id)
    try:
        new_config = await mgr.update(config)
        return {"status": "replaced", "config": new_config.dict()}
    except ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/config")
async def reset_config(
    tenant_id: str = Query("_default"),
    _: str = Depends(require_operator),
) -> Dict[str, Any]:
    """Reset to defaults."""
    mgr = ContextEngineeringConfigManager(tenant_id)
    new_config = await mgr.reset_to_defaults()
    return {"status": "reset", "config": new_config.dict()}


@router.get("/quota")
async def get_quota(
    tenant_id: str = Query("_default"),
    _: str = Depends(require_operator),
) -> Dict[str, Any]:
    """Get quota usage (units_used / daily_units)."""
    mgr = ContextEngineeringConfigManager(tenant_id)
    config = mgr.get()
    daily_units = config.quota["daily_units"]

    # TODO: Fetch actual units_used from counter (Phase 1, Week 3)
    units_used = 0

    return {
        "daily_units": daily_units,
        "units_used": units_used,
        "units_remaining": daily_units - units_used,
        "percent_used": (units_used / daily_units * 100) if daily_units > 0 else 0,
    }


@router.post("/quota/reset")
async def reset_quota(
    tenant_id: str = Query("_default"),
    _: str = Depends(require_operator),
) -> Dict[str, Any]:
    """Reset quota counter (operator only)."""
    # TODO: Reset quota counter (Phase 1, Week 3)
    return {"status": "reset"}


@router.get("/metrics")
async def get_metrics(
    tenant_id: str = Query("_default"),
    hours: int = Query(24, ge=1, le=720),
    _: str = Depends(require_operator),
) -> Dict[str, Any]:
    """Get usage stats, degradation rate, confidence distribution."""
    # TODO: Fetch metrics from audit trail (Phase 1, Week 3)
    return {
        "period_hours": hours,
        "turns_total": 0,
        "ce_turns_enriched": 0,
        "degradation_count": 0,
        "degradation_rate": 0.0,
        "avg_confidence": 0.0,
        "stage_breakdown": {},
    }
