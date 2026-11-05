"""Feature Whitelist Management — operator controls which features are enabled.

ADR-0XXX: Whitelist strategy allows operator to declare tested & verified features.
Only whitelisted features are ON; all others are OFF (deny-all-else).

Endpoints:
  GET /v1/console/features/whitelist    — read current whitelist
  POST /v1/console/features/toggle      — add/remove feature from whitelist
"""
from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from corvin_console import feature_flags
from corvin_console.auth import SessionRecord
from corvin_console import audit as console_audit
from corvin_console import tenant_yaml as _tenant_yaml
from corvin_console.deps import require_csrf, require_session
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
    """Read-only view for GET: a missing or unreadable file shows an empty whitelist.

    Never used to decide what to WRITE back - the toggle goes through
    ``tenant_yaml.update``, which refuses an unreadable file (review SRF-4)."""
    try:
        return _tenant_yaml.load_for_write(_get_tenant_config_path(tenant_id))
    except _tenant_yaml.TenantYamlUnreadable:
        return {}


def _write_tenant_config(tenant_id: str, config: dict) -> None:
    """Whole-document write through the one console writer (atomic, 0o600)."""
    _tenant_yaml.write_mapping(_get_tenant_config_path(tenant_id), config)


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
    session: SessionRecord = Depends(require_csrf),
) -> dict:
    """Add or remove a feature from the whitelist.

    Mutation → session + CSRF token, and one ``console.action_performed``
    event in the core hash-chained audit log (adversarial review E-06/E-07,
    2026-09-03 — this route rewrote ``tenant.corvin.yaml`` with neither).
    """
    # Validate feature exists
    feature_flags.flag(request.feature_id)

    # Locked read-modify-write through the one tenant-YAML writer (SRF-3/SRF-4):
    # a file that exists but cannot be parsed is REFUSED - it used to read as {}
    # and be rewritten holding only spec.features_whitelist, dropping engine pins,
    # data residency, budget and the telemetry opt-outs.
    def _toggle(config: dict) -> list:
        spec = config.get("spec")
        if not isinstance(spec, dict):
            spec = config["spec"] = {}
        whitelist = spec.get("features_whitelist", [])
        if not isinstance(whitelist, list):
            whitelist = []
        if request.enabled:
            if request.feature_id not in whitelist:
                whitelist.append(request.feature_id)
                whitelist.sort()
        else:
            whitelist = [f for f in whitelist if f != request.feature_id]
        spec["features_whitelist"] = whitelist
        return whitelist

    try:
        whitelist = _tenant_yaml.update(_get_tenant_config_path(session.tenant_id), _toggle)
    except _tenant_yaml.TenantYamlUnreadable:
        console_audit.action_failed(
            tenant_id=session.tenant_id,
            sid_fingerprint=session.sid_fingerprint,
            action="feature.whitelist_enable" if request.enabled else "feature.whitelist_disable",
            target_kind="feature_flag",
            target_id=request.feature_id,
            reason="tenant-yaml-unreadable",
        )
        raise HTTPException(
            status_code=409,
            detail="tenant configuration is unreadable; fix or restore it before toggling features",
        ) from None

    # Clear cache so next read is fresh
    feature_flags._spec_cache.clear()
    try:  # the manifest's 5 s flag cache must not outlive an operator decision
        from . import capabilities as _capabilities  # noqa: PLC0415

        _capabilities.invalidate_flags_cache(session.tenant_id)
    except Exception:  # noqa: BLE001 — cache miss at worst
        pass

    console_audit.action_performed(
        tenant_id=session.tenant_id,
        sid_fingerprint=session.sid_fingerprint,
        action="feature.whitelist_enable" if request.enabled else "feature.whitelist_disable",
        target_kind="feature_flag",
        target_id=request.feature_id,
    )

    return {
        "status": "success",
        "feature_id": request.feature_id,
        "enabled": request.enabled,
        "whitelist": whitelist,
    }
