"""API endpoints for feature status (preset + dashboard). Phase 5, ADR-0287/0288."""

import os
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException

from ..deps import require_session, require_csrf
from ..feature_flags import REGISTRY

# Optional: telemetry integration (may not be available in all environments)
try:
    from core.telemetry import get_flag_metrics
except ImportError:
    get_flag_metrics = None  # type: ignore

router = APIRouter(prefix="/api/feature-status", tags=["feature-status"])


def _get_tenant_yaml_path(tenant_id: str) -> Path:
    """The session tenant's tenant.corvin.yaml - the file every other reader uses.

    Used to be a hand-composed ``~/.corvin/tenants/_default/tenant.corvin.yaml``
    (no ``global/``, ignoring CORVIN_HOME and the session's tenant): a file no
    reader opens, written non-atomically at umask before a late chmod.
    """
    from forge import paths as _forge_paths  # noqa: PLC0415
    from .. import tenant_yaml as _ty  # noqa: PLC0415
    return _forge_paths.tenant_global_dir(tenant_id) / _ty.FILENAME


def _load_tenant_spec(tenant_id: str) -> dict:
    """Load tenant.corvin.yaml and return the spec dict (read-only view)."""
    from .. import tenant_yaml as _ty  # noqa: PLC0415
    try:
        data = _ty.load_for_write(_get_tenant_yaml_path(tenant_id))
    except _ty.TenantYamlUnreadable:
        return {"preset": "standard"}
    spec = data.get("spec")
    return spec if isinstance(spec, dict) else {"preset": "standard"}


def _save_preset(tenant_id: str, preset: str) -> None:
    """Set spec.preset through the one console tenant-YAML writer (locked, atomic,
    0o600); an unreadable file raises TenantYamlUnreadable and nothing is written."""
    from .. import tenant_yaml as _ty  # noqa: PLC0415

    def _set(data: dict) -> None:
        spec = data.get("spec")
        if not isinstance(spec, dict):
            spec = data["spec"] = {}
        spec["preset"] = preset

    _ty.update(_get_tenant_yaml_path(tenant_id), _set)


@router.get("/preset")
async def get_preset(session=Depends(require_session)):
    """Get current installation preset (minimal|standard|advanced). Requires auth."""
    spec = _load_tenant_spec(session.tenant_id)
    preset = spec.get("preset", "standard")
    return {"preset": preset}


@router.post("/preset")
async def set_preset(body: dict, session=Depends(require_session), csrf=Depends(require_csrf)):
    """Set installation preset. Requires restart to take effect."""
    from fastapi.responses import JSONResponse

    preset = body.get("preset")
    if preset not in ("minimal", "standard", "advanced"):
        raise HTTPException(status_code=400, detail="Invalid preset")

    from .. import tenant_yaml as _ty  # noqa: PLC0415
    try:
        _save_preset(session.tenant_id, preset)
    except _ty.TenantYamlUnreadable:
        raise HTTPException(status_code=409,
                            detail="tenant configuration is unreadable; fix or restore it first") from None

    return JSONResponse(
        status_code=201,
        content={"preset": preset, "requires_restart": True}
    )


@router.get("")
async def get_all_features(session=Depends(require_session)):
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
async def get_feature(flag_id: str, session=Depends(require_session)):
    """Get status for a single feature (requires auth to prevent info disclosure)."""
    # Find flag in registry
    flag = None
    for f in REGISTRY:
        if f.id == flag_id:
            flag = f
            break

    if not flag:
        raise HTTPException(status_code=404, detail="Not found")

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
