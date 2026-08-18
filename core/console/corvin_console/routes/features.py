"""Feature Whitelist Management — operator controls which features are enabled.

ADR-0XXX: Whitelist strategy allows operator to declare tested & verified features.
Only whitelisted features are ON; all others are OFF (deny-all-else).

Endpoints:
  GET /v1/console/features/whitelist    — read current whitelist
  POST /v1/console/features/toggle      — add/remove feature from whitelist
"""
from __future__ import annotations

import yaml
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from corvin_console import feature_flags
from corvin_console.auth import SessionRecord
from corvin_console.deps import require_session
from forge import paths as forge_paths

router = APIRouter()


class WhitelistResponse(BaseModel):
    whitelist: list[str]
    mode: str  # "whitelist" or "legacy"
    total_features: int


class ToggleRequest(BaseModel):
    feature_id: str
    enabled: bool


def _get_tenant_config_path(tenant_id: str) -> Path:
    return forge_paths.corvin_home() / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"


def _read_tenant_config(tenant_id: str) -> dict:
    path = _get_tenant_config_path(tenant_id)
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text("utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_tenant_config(tenant_id: str, config: dict) -> None:
    path = _get_tenant_config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False), "utf-8")


@router.get("/features/whitelist", response_model=WhitelistResponse)
async def get_whitelist(session: SessionRecord = Depends(require_session)):
    """Fetch the current feature whitelist."""
    config = _read_tenant_config(session.tenant_id)
    spec = config.get("spec", {})
    whitelist = spec.get("features_whitelist", [])

    mode = "whitelist" if isinstance(whitelist, list) else "legacy"
    return WhitelistResponse(
        whitelist=whitelist if isinstance(whitelist, list) else [],
        mode=mode,
        total_features=len(feature_flags.REGISTRY),
    )


@router.post("/features/toggle")
async def toggle_feature(
    request: ToggleRequest,
    session: SessionRecord = Depends(require_session),
) -> dict:
    """Add or remove a feature from the whitelist."""
    # Validate feature exists
    feature_flags.flag(request.feature_id)

    # Read current config
    config = _read_tenant_config(session.tenant_id)
    spec = config.setdefault("spec", {})

    # Get or create whitelist
    whitelist = spec.get("features_whitelist", [])
    if not isinstance(whitelist, list):
        whitelist = []

    # Toggle
    if request.enabled:
        if request.feature_id not in whitelist:
            whitelist.append(request.feature_id)
            whitelist.sort()
    else:
        whitelist = [f for f in whitelist if f != request.feature_id]

    # Write back
    spec["features_whitelist"] = whitelist
    config["spec"] = spec
    _write_tenant_config(session.tenant_id, config)

    # Clear cache so next read is fresh
    feature_flags._spec_cache.clear()

    return {
        "status": "success",
        "feature_id": request.feature_id,
        "enabled": request.enabled,
        "whitelist": whitelist,
    }
