"""Settings → Features — the ship-dark feature-flag panel + worker-engine choice.

Two surfaces over ``corvin_console.feature_flags``:

  * ``/settings/features``               — registry + resolved per-tenant state
  * ``/settings/features/{flag_id}``     — flip one flag
  * ``/settings/worker-engine``          — read / set native | acs | tde

Every write is tenant-scoped via the authenticated ``SessionRecord`` (never an
env var, per ADR-0007) and lands in the console audit trail.

Compliance mechanisms are NOT reachable from here: the registry refuses to
carry them (``feature_flags._validate_registry``), and this route can only
write ids that are in the registry.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel

from .. import audit as console_audit
from .. import auth as session_auth
from .. import feature_flags
from ..deps import require_csrf, require_session

router = APIRouter()


class FeatureToggleRequest(BaseModel):
    enabled: bool
    model_config = {"extra": "forbid"}


class WorkerEngineRequest(BaseModel):
    mode: str
    model_config = {"extra": "forbid"}


@router.get("/settings/features")
def list_features(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Registry + resolved state for this tenant."""
    return {"features": feature_flags.describe_all(rec.tenant_id)}


@router.put("/settings/features/{flag_id}")
def set_feature(
    flag_id: str,
    body: FeatureToggleRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Enable or disable one registered feature flag for this tenant."""
    try:
        feature_flags.flag(flag_id)
    except feature_flags.UnknownFlagError:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.feature_toggle",
            target_kind="feature_flag",
            target_id=flag_id,
            reason="unknown-flag",
        )
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="unknown_flag",
        ) from None
    try:
        enabled = feature_flags.set_enabled(flag_id, body.enabled, rec.tenant_id)
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.feature_toggle",
            target_kind="feature_flag",
            target_id=flag_id,
            reason="io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write_failed",
        ) from e
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="settings.feature_toggle",
        target_kind="feature_flag",
        target_id=f"{flag_id}={'on' if enabled else 'off'}",
    )
    return {"id": flag_id, "enabled": enabled, "ok": True}


@router.get("/settings/worker-engine")
def get_worker_engine(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Current worker-engine selection plus the selectable modes."""
    return {
        "mode": feature_flags.worker_engine_mode(rec.tenant_id),
        "modes": list(feature_flags.WORKER_ENGINE_MODES),
        "default": feature_flags.WORKER_ENGINE_DEFAULT,
    }


@router.put("/settings/worker-engine")
def put_worker_engine(
    body: WorkerEngineRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Select the worker engine: native | acs | tde."""
    try:
        mode = feature_flags.set_worker_engine_mode(body.mode, rec.tenant_id)
    except ValueError:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.worker_engine",
            target_kind="worker_engine",
            target_id="invalid",
            reason="unknown-mode",
        )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="unknown_mode",
        ) from None
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.worker_engine",
            target_kind="worker_engine",
            target_id=body.mode,
            reason="io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write_failed",
        ) from e
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="settings.worker_engine",
        target_kind="worker_engine",
        target_id=mode,
    )
    return {"mode": mode, "ok": True}
