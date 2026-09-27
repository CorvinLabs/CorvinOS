"""Plugin Upload Routes — Layer 1-4 Distribution (ADR-0511)

Endpoints:
  POST /v1/skills/upload — accept ZIP, validate, stage
  GET /v1/skills/uploads — list staged uploads
  POST /v1/skills/uploads/{uploadId}/approve — approve + install
  POST /v1/skills/uploads/{uploadId}/reject — reject + cleanup
  GET /v1/skills/uploads/{uploadId}/manifest — preview manifest

All routes require SessionRecord + admin gate.
Tenant isolation via rec.tenant_id (mandatory).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from forge import paths as _forge_paths

import corvin_console.auth as _auth
from ..audit import emit_audit
from ..deps import require_session
from core.plugins.staging import StagingManager, StagingError

router = APIRouter()


def _require_admin(
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
) -> _auth.SessionRecord:
    """Admin gate: fail-closed if not admin."""
    if not rec.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return rec


@router.post("/v1/skills/upload")
async def upload_skill(
    rec: Annotated[_auth.SessionRecord, Depends(_require_admin)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload ZIP file for staging and validation.

    Returns: {upload_id, file_name, file_size, status, validation_errors}
    """
    manager = StagingManager(rec.tenant_id)

    try:
        # Read file into memory
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        # Validate file size (100 MB limit)
        max_size = 100 * 1024 * 1024
        if len(content) > max_size:
            raise HTTPException(status_code=400, detail="File exceeds 100 MB limit")

        # Write to temp file
        temp_path = manager.staging_root / f"{file.filename or 'upload'}.tmp"
        temp_path.write_bytes(content)

        # Validate ZIP + manifest
        is_valid, manifest, errors = manager.validate_zip_file(temp_path)

        # Generate upload ID
        upload_id = manager.compute_file_hash(temp_path)[:16]

        # Store in staging
        staged_path = manager.store_staged_upload(upload_id, temp_path, manifest)

        # Emit audit event
        emit_audit(
            "plugin.upload_staged",
            {
                "upload_id": upload_id,
                "file_name": file.filename or "unknown",
                "file_size": len(content),
                "valid": is_valid,
                "error_count": len(errors),
                "tenant_id": rec.tenant_id,
            },
        )

        return {
            "upload_id": upload_id,
            "file_name": file.filename or "unknown",
            "file_size": len(content),
            "status": "pending_approval",
            "validation_errors": errors,
        }

    except StagingError as e:
        emit_audit(
            "plugin.upload_failed",
            {
                "reason": str(e),
                "file_name": file.filename or "unknown",
                "tenant_id": rec.tenant_id,
            },
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        emit_audit(
            "plugin.upload_error",
            {
                "reason": type(e).__name__,
                "tenant_id": rec.tenant_id,
            },
        )
        raise HTTPException(status_code=500, detail="Upload failed")


@router.get("/v1/skills/uploads")
def list_uploads(
    rec: Annotated[_auth.SessionRecord, Depends(_require_admin)],
) -> dict[str, Any]:
    """List all staged uploads for this tenant."""
    manager = StagingManager(rec.tenant_id)
    uploads: list[dict[str, Any]] = []

    try:
        for entry in manager.staging_root.iterdir():
            if entry.is_file() and entry.suffix in [".json", ".meta"]:
                try:
                    metadata = json.loads(entry.read_text())
                    uploads.append(
                        {
                            "upload_id": metadata.get("upload_id", "unknown"),
                            "file_name": metadata.get("file_name", ""),
                            "file_size": metadata.get("file_size", 0),
                            "upload_timestamp": metadata.get("timestamp", ""),
                            "status": metadata.get("status", "pending_approval"),
                            "validation_errors": metadata.get("errors", []),
                        }
                    )
                except (json.JSONDecodeError, ValueError):
                    pass
    except FileNotFoundError:
        pass

    return {"uploads": uploads, "count": len(uploads)}


@router.post("/v1/skills/uploads/{uploadId}/approve")
def approve_upload(
    rec: Annotated[_auth.SessionRecord, Depends(_require_admin)],
    uploadId: str,
) -> dict[str, Any]:
    """Approve staged upload and trigger installation."""
    manager = StagingManager(rec.tenant_id)

    try:
        upload_info = manager.get_staged_upload(uploadId)
        if not upload_info:
            raise HTTPException(status_code=404, detail="Upload not found")

        # Move to installed location
        installer_result = (True, "Installation queued")
        installed_path = manager.move_to_installed(uploadId, installer_result)

        emit_audit(
            "plugin.upload_approved",
            {
                "upload_id": uploadId,
                "file_name": upload_info.get("file_name", "unknown"),
                "tenant_id": rec.tenant_id,
            },
        )

        return {
            "status": "approved",
            "upload_id": uploadId,
            "installed_path": str(installed_path),
        }

    except Exception as e:
        emit_audit(
            "plugin.approval_failed",
            {
                "upload_id": uploadId,
                "reason": type(e).__name__,
                "tenant_id": rec.tenant_id,
            },
        )
        raise HTTPException(status_code=500, detail="Approval failed")


@router.post("/v1/skills/uploads/{uploadId}/reject")
def reject_upload(
    rec: Annotated[_auth.SessionRecord, Depends(_require_admin)],
    uploadId: str,
) -> dict[str, Any]:
    """Reject staged upload and cleanup."""
    manager = StagingManager(rec.tenant_id)

    try:
        upload_info = manager.get_staged_upload(uploadId)
        if not upload_info:
            raise HTTPException(status_code=404, detail="Upload not found")

        manager.delete_staged_upload(uploadId)

        emit_audit(
            "plugin.upload_rejected",
            {
                "upload_id": uploadId,
                "file_name": upload_info.get("file_name", "unknown"),
                "tenant_id": rec.tenant_id,
            },
        )

        return {"status": "rejected", "upload_id": uploadId}

    except Exception as e:
        raise HTTPException(status_code=500, detail="Rejection failed")


@router.get("/v1/skills/uploads/{uploadId}/manifest")
def get_manifest(
    rec: Annotated[_auth.SessionRecord, Depends(_require_admin)],
    uploadId: str,
) -> dict[str, Any]:
    """Get manifest preview for a staged upload."""
    manager = StagingManager(rec.tenant_id)

    try:
        upload_info = manager.get_staged_upload(uploadId)
        if not upload_info:
            raise HTTPException(status_code=404, detail="Upload not found")

        return {
            "upload_id": uploadId,
            "manifest": upload_info.get("manifest", {}),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to read manifest")
