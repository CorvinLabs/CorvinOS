"""API endpoints for feature status (preset + dashboard). Phase 5, ADR-0287/0288."""

import os
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Depends

from core.console.corvin_console.feature_flags import REGISTRY
from core.console.corvin_console.deps import require_session
from core.telemetry import get_flag_metrics

router = APIRouter(prefix="/api/feature-status", tags=["feature-status"])


def _get_tenant_yaml_path() -> Path:
    """Get the path to tenant.corvin.yaml (in ~/.corvin/tenants/_default/)."""
    home = Path.home()
    return home / ".corvin" / "tenants" / "_default" / "tenant.corvin.yaml"


def _load_tenant_spec() -> dict:
    """Load tenant.corvin.yaml and return the spec dict."""
    path = _get_tenant_yaml_path()
    if not path.exists():
        return {"preset": "standard"}

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return data.get("spec", {})
    except Exception:
        return {"preset": "standard"}


def _save_tenant_spec(spec: dict) -> None:
    """Save updated spec back to tenant.corvin.yaml."""
    path = _get_tenant_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing YAML or create new
    if path.exists():
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    # Update spec
    data["spec"] = spec

    # Write back with restricted permissions (GDPR Art. 32)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    os.chmod(path, 0o600)


@router.get("/preset")
async def get_preset():
    """Get current installation preset (minimal|standard|advanced)."""
    spec = _load_tenant_spec()
    preset = spec.get("preset", "standard")
    return {"preset": preset}


@router.post("/preset")
async def set_preset(body: dict, session=Depends(require_session)):
    """Set installation preset. Requires restart to take effect."""
    preset = body.get("preset")
    if preset not in ("minimal", "standard", "advanced"):
        raise HTTPException(status_code=400, detail="Invalid preset")

    spec = _load_tenant_spec()
    spec["preset"] = preset
    _save_tenant_spec(spec)

    return {"preset": preset, "requires_restart": True, "status_code": 201}


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
