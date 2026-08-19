"""Quality Layers (ADR Gate, docs-as-definition-of-done, etc.) configuration.

Wraps the shared ``operator/bridges/shared/quality_layers.py`` module which owns
the canonical config at ``~/.corvin/global/quality-layers.json``. The module's
load/save/enable_layer/disable_layer functions are the single source of truth —
this route only exposes them via REST.

Endpoints
---------
  GET  /v1/console/quality-layers                  → all layers and status
  PUT  /v1/console/quality-layers/master           → toggle master switch
  PUT  /v1/console/quality-layers/layers/{layer}   → toggle a single layer

All mutations require CSRF + re-auth.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

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

try:
    import quality_layers as _ql  # noqa: E402
except ImportError:
    _ql = None  # type: ignore


router = APIRouter()


class MasterToggleRequest(BaseModel):
    """Request to toggle all quality layers."""
    enabled: bool


class LayerToggleRequest(BaseModel):
    """Request to toggle a single quality layer."""
    enabled: bool


def _snapshot() -> dict[str, Any]:
    """Return snapshot of all quality layers and their status."""
    if _ql is None:
        return {"error": "quality_layers module not available"}

    config = _ql.load_config()
    layers: list[dict[str, Any]] = []

    # Standard layer names (Quality subsystem tools)
    layer_names = [
        "adr_gate",
        "docs_as_definition_of_done",
        "e2e_driven_iteration",
        "usability_first",
        "skill_creator",           # Autonomous 6-phase skill builder
        "concept_gate",            # Concept archiving for patterns
    ]

    for layer_name in layer_names:
        layers.append({
            "id": layer_name,
            "name": layer_name.replace("_", "-"),
            "configured": config.get("layers", {}).get(layer_name, True),
            "category": "quality",
            "icon": "🧠" if layer_name == "skill_creator" else "📋",  # Skill-Creator icon
            "description": "Autonomous skill builder (6-phase orchestration)" if layer_name == "skill_creator" else None,
        })

    return {
        "globally_enabled": config.get("enabled", True),
        "layers": layers,
    }


@router.get("/quality-layers")
def get_quality_layers(
    _: Annotated[session_auth.SessionRecord, Depends(require_session)]
) -> dict[str, Any]:
    """Return all quality layers and their current status."""
    return _snapshot()


@router.put("/quality-layers/master")
def put_master(
    body: MasterToggleRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Toggle all quality layers globally on/off."""
    if _ql is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="quality_layers module not available"
        )

    try:
        if body.enabled:
            _ql.enable_all()
        else:
            _ql.disable_all()
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="quality_layers.master.set",
            target_kind="quality_layers",
            target_id="master"
        )
        return {"ok": True, **_snapshot()}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Quality layers master toggle failed", exc_info=True)
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="quality_layers.master.set",
            target_kind="quality_layers",
            target_id="master",
            reason="quality_layers_master_toggle_failed"
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Quality layers master toggle failed"
        )


@router.put("/quality-layers/layers/{layer_name}")
def put_layer(
    layer_name: str,
    body: LayerToggleRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Toggle a single quality layer on/off."""
    if _ql is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="quality_layers module not available"
        )

    # Normalize layer name (dashes to underscores)
    normalized_layer = layer_name.replace("-", "_")

    try:
        if body.enabled:
            _ql.enable_layer(normalized_layer)
        else:
            _ql.disable_layer(normalized_layer)
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="quality_layers.layer.set",
            target_kind="quality_layers",
            target_id=normalized_layer
        )
        return {"ok": True, **_snapshot()}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Quality layer update failed", exc_info=True)
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="quality_layers.layer.set",
            target_kind="quality_layers",
            target_id=normalized_layer,
            reason="quality_layer_update_failed"
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Quality layer update failed"
        )
