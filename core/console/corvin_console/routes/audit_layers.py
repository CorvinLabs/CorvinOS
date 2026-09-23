"""Custom Audit Layers (ADR-0124 M6).

Operators define custom event-type namespaces with allowed field schemas.
External systems emit events via POST /audit/emit; events go through the
L16 hash chain just like built-in events.

Routes:
  GET    /audit/layers                  list registered custom layers
  PUT    /audit/layers/{layer_id}       register or update a layer
  DELETE /audit/layers/{layer_id}       remove a layer
  POST   /audit/emit                    emit a custom event (any authenticated client)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
from forge import security_events as _security_events  # noqa: E402

router = APIRouter()

_LAYER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_EVENT_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Fields that are always forbidden (security invariant)
_FORBIDDEN_FIELDS = frozenset({
    "sid", "session_id", "csrf", "token", "password", "secret",
    "cleartext_sid", "bearer_token",
    "email", "name", "ip", "phone", "address",
    "text", "transcript", "content", "prompt", "body",
})


# ── Storage ───────────────────────────────────────────────────────────────────

def _layers_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "audit_layers"


def _layer_path(tid: str, layer_id: str) -> Path:
    return _layers_dir(tid) / f"{layer_id}.json"


def _load_layer(tid: str, layer_id: str) -> dict[str, Any] | None:
    p = _layer_path(tid, layer_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_layers(tid: str) -> list[dict[str, Any]]:
    d = _layers_dir(tid)
    if not d.is_dir():
        return []
    results = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                results.append(data)
        except (OSError, json.JSONDecodeError):
            pass
    return results


def _write_layer(tid: str, layer_id: str, data: dict[str, Any]) -> None:
    p = _layer_path(tid, layer_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _audit_chain_path(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl"


# ── Models ────────────────────────────────────────────────────────────────────

class AuditLayerRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    event_types: list[str] = Field(
        ...,
        min_length=1,
        description="Event type patterns this layer owns (e.g. 'my-app.user_action')",
    )
    allowed_fields: list[str] = Field(
        default_factory=list,
        description="Field names allowed in emitted events; [] = no fields (deny-all default); null is disallowed and will be rejected with HTTP 400",
    )
    description: str = Field("", max_length=500)
    model_config = {"extra": "forbid"}


class AuditEmitRequest(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=64)
    event_type: str = Field(..., min_length=1, max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)
    severity: str = Field("INFO", description="DEBUG | INFO | WARNING | ERROR | CRITICAL")
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/audit/layers")
def list_audit_layers(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    layers = _list_layers(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(layers), "layers": layers}


@router.put("/audit/layers/{layer_id}")
def register_audit_layer(
    layer_id: str,
    body: AuditLayerRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if rec.tier not in {"owner", "admin"}:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="audit.layer_registered",
            target_kind="audit_layer",
            target_id=layer_id,
            reason="insufficient_role",
        )
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "insufficient role")
    if not _LAYER_ID_RE.match(layer_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "layer_id must be lowercase alphanumeric with _ or -",
        )
    for et in body.event_types:
        if not _EVENT_TYPE_RE.match(et):
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                f"invalid event_type {et!r}; must be lowercase alphanumeric with . : or -",
            )
        if not et.startswith(layer_id + ".") and not et.startswith(layer_id + ":"):
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                f"event_type {et!r} must be namespaced under {layer_id!r} (e.g. '{layer_id}.my_event')",
            )

    bad_fields = _FORBIDDEN_FIELDS.intersection(body.allowed_fields)
    if bad_fields:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"forbidden fields: {sorted(bad_fields)}",
        )
    for f in body.allowed_fields:
        if not _FIELD_NAME_RE.match(f):
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                f"invalid field name {f!r}",
            )

    existing = _load_layer(rec.tenant_id, layer_id)
    is_update = existing is not None

    manifest: dict[str, Any] = {
        "layer_id": layer_id,
        "display_name": body.display_name,
        "event_types": body.event_types,
        "allowed_fields": body.allowed_fields,
        "description": body.description,
        "created_at": existing.get("created_at", time.time()) if existing else time.time(),
        "updated_at": time.time(),
    }

    try:
        _write_layer(rec.tenant_id, layer_id, manifest)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="audit.layer_updated" if is_update else "audit.layer_registered",
        target_kind="audit_layer",
        target_id=layer_id,
    )
    return {"ok": True, "layer_id": layer_id, "updated": is_update}


@router.delete("/audit/layers/{layer_id}")
def remove_audit_layer(
    layer_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if rec.tier not in {"owner", "admin"}:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="audit.layer_removed",
            target_kind="audit_layer",
            target_id=layer_id,
            reason="insufficient_role",
        )
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "insufficient role")
    p = _layer_path(rec.tenant_id, layer_id)
    if not p.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"layer {layer_id!r} not found")
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="audit.layer_removed",
        target_kind="audit_layer",
        target_id=layer_id,
    )
    return {"ok": True, "layer_id": layer_id}


@router.post("/audit/emit")
def emit_custom_event(
    body: AuditEmitRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Emit a custom audit event through the L16 hash chain.

    Validates event_type and detail fields against the layer manifest
    before writing to the chain.
    """
    layer = _load_layer(rec.tenant_id, body.layer_id)
    if layer is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"layer {body.layer_id!r} not found")

    if body.event_type not in layer.get("event_types", []):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"event_type {body.event_type!r} not declared in layer {body.layer_id!r}",
        )

    # _FORBIDDEN_FIELDS is always enforced regardless of allowed_fields policy.
    bad_fields = _FORBIDDEN_FIELDS.intersection(body.details.keys())
    if bad_fields:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"forbidden fields in details: {sorted(bad_fields)}",
        )
    # allowed_fields semantics: [] = deny-all (default); [...] = only listed fields.
    # None stored in legacy layers is treated as deny-all (fail-closed).
    stored_fields = layer.get("allowed_fields") or []
    allowed_fields = set(stored_fields)
    extra = set(body.details.keys()) - allowed_fields
    if extra:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"fields not in layer allowlist: {sorted(extra)}; allowed={sorted(allowed_fields)}",
        )

    valid_severities = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if body.severity not in valid_severities:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"severity must be one of {sorted(valid_severities)}",
        )

    chain = _audit_chain_path(rec.tenant_id)
    try:
        _security_events.write_event(
            chain,
            body.event_type,
            details={**body.details, "layer_id": body.layer_id},
            severity=body.severity,
        )
    except Exception as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "audit chain write failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="audit.custom_event_emitted",
        target_kind="audit_layer",
        target_id=body.layer_id,
    )
    return {
        "ok": True,
        "layer_id": body.layer_id,
        "event_type": body.event_type,
        "ts": time.time(),
    }
