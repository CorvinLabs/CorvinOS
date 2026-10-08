"""Plugin Upload Routes — Layer 1-4 Distribution (ADR-0511)

Endpoints (mounted under /v1/console by the console router):
  POST /plugin-uploads                      — accept ZIP, validate, stage
  GET  /plugin-uploads                      — list staged uploads
  POST /plugin-uploads/{upload_id}/approve  — approve (moves the ZIP out of staging)
  POST /plugin-uploads/{upload_id}/reject   — reject + cleanup
  GET  /plugin-uploads/{upload_id}/manifest — preview manifest

Every route needs a live console session and the owner tier; every mutation
additionally needs the CSRF token. Tenant isolation via ``rec.tenant_id``.

The routes used to be declared as ``/v1/skills/...`` and so were served at
``/v1/console/v1/skills/...``; ``/skills/...`` is not usable either, because the
generic ``GET /skills/{name}`` route is registered first and would capture
``/skills/uploads``. Hence the own ``/plugin-uploads`` namespace.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

import corvin_console.auth as _auth
from .. import audit
from ..deps import require_csrf, require_session, require_session_csrf_on_mutation
from core.plugins.staging import StagingManager, StagingError

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# upload ids are the first 16 hex chars of the ZIP's SHA-256.
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_TARGET_KIND = "plugin_upload"


def _require_owner(rec: _auth.SessionRecord) -> _auth.SessionRecord:
    """Owner/admin gate, fail-closed."""
    if getattr(rec, "tier", None) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="owner or admin required")
    return rec


def _owner_read(
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> _auth.SessionRecord:
    return _require_owner(rec)


def _owner_mutation(
    rec: Annotated[_auth.SessionRecord, Depends(require_csrf)],
) -> _auth.SessionRecord:
    return _require_owner(rec)


def _check_upload_id(upload_id: str) -> str:
    if not _UPLOAD_ID_RE.match(upload_id):
        raise HTTPException(status_code=400, detail="invalid upload id")
    return upload_id


def _performed(rec: _auth.SessionRecord, action: str, target_id: str) -> None:
    audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action=action, target_kind=_TARGET_KIND, target_id=target_id,
    )


def _failed(rec: _auth.SessionRecord, action: str, target_id: str, reason: str) -> None:
    audit.action_failed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action=action, target_kind=_TARGET_KIND, target_id=target_id, reason=reason,
    )


def _safe_name(filename: str | None) -> str:
    """Client file name reduced to a harmless basename (never used as a path
    component without this — ``../../x`` must not leave the staging dir)."""
    base = os.path.basename((filename or "").replace("\\", "/")) or "upload"
    return (_SAFE_NAME_RE.sub("_", base) or "upload")[:64]


@router.post("/plugin-uploads")
async def upload_skill(
    rec: Annotated[_auth.SessionRecord, Depends(_owner_mutation)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a ZIP for staging and validation.

    An invalid ZIP is rejected (400) and never staged — only a validated
    package can reach the approval step.
    """
    manager = StagingManager(rec.tenant_id)
    safe_name = _safe_name(file.filename)

    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 100 MB limit")

    fd, tmp_name = tempfile.mkstemp(
        dir=manager.staging_root, prefix=f"{safe_name}.", suffix=".tmp",
    )
    temp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)

        is_valid, manifest, errors = manager.validate_zip_file(temp_path)
        upload_id = manager.compute_file_hash(temp_path)[:16]
        if not is_valid:
            _failed(rec, "plugin.upload", upload_id, "validation_failed")
            raise HTTPException(
                status_code=400,
                detail={"message": "invalid plugin package", "validation_errors": errors},
            )

        manager.store_staged_upload(upload_id, temp_path, manifest)
    except HTTPException:
        raise
    except StagingError:
        _failed(rec, "plugin.upload", "-", "staging_error")
        raise HTTPException(status_code=400, detail="Upload could not be staged")
    except Exception:
        _failed(rec, "plugin.upload", "-", "internal_error")
        raise HTTPException(status_code=500, detail="Upload failed")
    finally:
        temp_path.unlink(missing_ok=True)

    _performed(rec, "plugin.upload_staged", upload_id)
    return {
        "upload_id": upload_id,
        "file_name": safe_name,
        "file_size": len(content),
        "status": "pending_approval",
        "validation_errors": [],
    }


@router.get("/plugin-uploads")
def list_uploads(
    rec: Annotated[_auth.SessionRecord, Depends(_owner_read)],
) -> dict[str, Any]:
    """List all staged uploads for this tenant."""
    manager = StagingManager(rec.tenant_id)
    uploads: list[dict[str, Any]] = []

    for entry in sorted(manager.staging_root.glob("*.meta")):
        try:
            metadata = json.loads(entry.read_text())
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        uploads.append(
            {
                "upload_id": metadata.get("upload_id", "unknown"),
                "file_name": metadata.get("file_name", ""),
                "file_size": metadata.get("file_size", 0),
                "upload_timestamp": metadata.get("timestamp", ""),
                "status": metadata.get("status", "pending_approval"),
                "validation_errors": metadata.get("errors", []),
                "manifest": metadata.get("manifest", {}),
            }
        )

    return {"uploads": uploads, "count": len(uploads)}


@router.post("/plugin-uploads/{upload_id}/approve")
def approve_upload(
    rec: Annotated[_auth.SessionRecord, Depends(_owner_mutation)],
    upload_id: str,
) -> dict[str, Any]:
    """Approve a staged upload: the ZIP leaves staging for the tenant's
    ``plugins_installed/`` store. Nothing is loaded or activated here."""
    _check_upload_id(upload_id)
    manager = StagingManager(rec.tenant_id)

    if not manager.get_staged_upload(upload_id):
        raise HTTPException(status_code=404, detail="Upload not found")
    try:
        manager.move_to_installed(upload_id, (True, "approved"))
    except Exception:
        _failed(rec, "plugin.upload_approve", upload_id, "internal_error")
        raise HTTPException(status_code=500, detail="Approval failed")

    _performed(rec, "plugin.upload_approved", upload_id)
    return {"status": "approved", "upload_id": upload_id, "activated": False}


@router.post("/plugin-uploads/{upload_id}/reject")
def reject_upload(
    rec: Annotated[_auth.SessionRecord, Depends(_owner_mutation)],
    upload_id: str,
) -> dict[str, Any]:
    """Reject staged upload and cleanup."""
    _check_upload_id(upload_id)
    manager = StagingManager(rec.tenant_id)

    if not manager.get_staged_upload(upload_id):
        raise HTTPException(status_code=404, detail="Upload not found")
    try:
        manager.reject_staged_upload(upload_id)  # remembered, then deleted (ADV-09)
    except Exception:
        _failed(rec, "plugin.upload_reject", upload_id, "internal_error")
        raise HTTPException(status_code=500, detail="Rejection failed")

    _performed(rec, "plugin.upload_rejected", upload_id)
    return {"status": "rejected", "upload_id": upload_id}


@router.get("/plugin-uploads/{upload_id}/manifest")
def get_manifest(
    rec: Annotated[_auth.SessionRecord, Depends(_owner_read)],
    upload_id: str,
) -> dict[str, Any]:
    """Get manifest preview for a staged upload."""
    _check_upload_id(upload_id)
    manager = StagingManager(rec.tenant_id)
    upload_info = manager.get_staged_upload(upload_id)
    if not upload_info:
        raise HTTPException(status_code=404, detail="Upload not found")
    return {"upload_id": upload_id, "manifest": upload_info.get("manifest", {})}
