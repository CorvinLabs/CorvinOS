"""LDD (Loss-Driven-Development) layer-toggle config.

Wraps the shared ``operator/bridges/shared/ldd.py`` module which owns
the canonical config at ``<tenant>/global/ldd.json``. The module's
load/save/set_layer/set_master/apply_preset functions are the single
source of truth — this route only exposes them.

Endpoints
---------
  GET  /v1/console/ldd                    → layers, master, config, presets
  PUT  /v1/console/ldd/master             → toggle master switch
  PUT  /v1/console/ldd/layers/{layer}     → toggle a single layer
  POST /v1/console/ldd/presets/{name}     → apply a named preset

All mutations require CSRF + re-auth.

The shared ``ldd.py`` is itself project-default (per CLAUDE.md § Layer 14)
and never imports anthropic; this route is a thin REST surface, not
an alternative storage path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import ldd as _ldd  # noqa: E402


router = APIRouter()


def _snapshot() -> dict[str, Any]:
    cfg = _ldd.load_config()
    layers: list[dict[str, Any]] = []
    for layer in _ldd.LAYERS:
        active, _src = _ldd.effective_state(layer, profile=None)
        direct = bool(_ldd._direct_state(layer, profile={}, cfg=cfg))  # type: ignore[attr-defined]
        layers.append({
            "id":             layer,
            "label":          layer.replace("_", " "),
            "configured":     direct,
            "effective":      active,
            "depends_on":     _ldd.DEPENDS_ON.get(layer),
        })
    return {
        "layers":           layers,
        "master_enabled":   _ldd.master_enabled(profile=None),
        "presets":          sorted(_ldd.PRESETS.keys()),
        "depends_on":       dict(_ldd.DEPENDS_ON),
        "auto_optin_active": os.environ.get("LDD_AUTO_OPTIN") == "1",
    }


@router.get("/ldd")
def get_ldd(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    return _snapshot()


class MasterToggleRequest(BaseModel):
    enabled: bool
    model_config = {"extra": "forbid"}


@router.put("/ldd/master")
def put_master(
    body: MasterToggleRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    _ldd.set_master(body.enabled)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="ldd.master.set",
        target_kind="ldd",
        target_id="master",
    )
    return _snapshot()


class LayerToggleRequest(BaseModel):
    enabled: bool
    model_config = {"extra": "forbid"}


@router.put("/ldd/layers/{layer}")
def put_layer(
    layer: str,
    body: LayerToggleRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if layer not in _ldd.LAYERS:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"unknown layer: {layer!r}")
    _ldd.set_layer(layer, body.enabled)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="ldd.layer.set",
        target_kind="ldd_layer",
        target_id=layer,
    )
    return _snapshot()


class PresetApplyRequest(BaseModel):
    model_config = {"extra": "forbid"}


@router.post("/ldd/presets/{name}")
def apply_preset(
    name: str,
    body: PresetApplyRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if name not in _ldd.PRESETS:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"unknown preset: {name!r}")
    _ldd.apply_preset(name)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="ldd.preset.apply",
        target_kind="ldd_preset",
        target_id=name,
    )
    return _snapshot()
