"""API endpoints for feature status (preset + dashboard). Phase 5, ADR-0287/0288."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from core.console.corvin_console.feature_flags import REGISTRY, tier_of
from core.telemetry import get_flag_metrics

router = APIRouter(prefix="/v1/console/api/feature-status", tags=["feature-status"])


@router.get("/preset")
async def get_preset():
    """Get current installation preset (minimal|standard|advanced)."""
    # TODO: Load from tenant.corvin.yaml spec.preset
    # For now, return default
    return {"preset": "standard"}


@router.post("/preset")
async def set_preset(body: dict):
    """Set installation preset. Requires restart to take effect."""
    preset = body.get("preset")
    if preset not in ("minimal", "standard", "advanced"):
        raise HTTPException(status_code=400, detail="Invalid preset")

    # TODO: Update tenant.corvin.yaml spec.preset
    # TODO: Log audit event
    return {"preset": preset, "requires_restart": True}


@router.get("")
async def get_all_features():
    """Get all features with tier, error rate, status."""
    flags_enabled = []

    for flag in REGISTRY:
        metrics = get_flag_metrics(flag.id).get_24h_stats()

        error_rate = metrics.get("error_rate_24h", 0.0)

        # Determine status
        if error_rate > 0.05:
            status = "failed"
        elif error_rate > 0.02:
            status = "degraded"
        else:
            status = "active"

        flags_enabled.append(
            {
                "flag_id": flag.id,
                "release_tier": flag.release_tier,
                "error_rate_24h": error_rate,
                "invocation_count_24h": metrics.get("invocation_count_24h", 0),
                "days_since_last_error": metrics.get("days_since_last_error"),
                "status": status,
            }
        )

    return {
        "flags_enabled": flags_enabled,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/{flag_id}")
async def get_feature(flag_id: str):
    """Get status for a single feature."""
    # Find flag in registry
    flag = None
    for f in REGISTRY:
        if f.id == flag_id:
            flag = f
            break

    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")

    metrics = get_flag_metrics(flag_id).get_24h_stats()
    error_rate = metrics.get("error_rate_24h", 0.0)

    if error_rate > 0.05:
        status = "failed"
    elif error_rate > 0.02:
        status = "degraded"
    else:
        status = "active"

    return {
        "flag_id": flag.id,
        "release_tier": flag.release_tier,
        "released_date": flag.released_date.isoformat() if flag.released_date else None,
        "error_rate_24h": error_rate,
        "invocation_count_24h": metrics.get("invocation_count_24h", 0),
        "days_since_last_error": metrics.get("days_since_last_error"),
        "status": status,
    }
