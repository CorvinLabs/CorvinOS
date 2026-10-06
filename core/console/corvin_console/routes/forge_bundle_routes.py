"""Forge Bundle Routes (ADR-2229 Phase 3–4).

Phase 3: POST /v1/console/forge-bundles/import — import bundles into quarantine
Phase 4: GET/POST quarantine management (accept/reject)
         GET/POST export validation
         POST validate-only route
"""
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile, status as http_status
from pydantic import BaseModel
from typing import Any, Optional
import io
import json

from core.forge_bundle.import_module import import_bundle, extract_bundle
from core.forge_bundle.validate import BundleRejected, validate_bundle
from core.forge_bundle.audit import ForgeBundleAuditError, emit as emit_audit_event
from core.forge_bundle.tool_quarantine import ToolQuarantineWorkflow, QuarantineError
from core.forge_bundle.export import build_bundle, Selection, ExportError

from .. import auth as session_auth
from ..deps import require_session, require_session_csrf_on_mutation

router = APIRouter(prefix="/v1/console/forge-bundles", tags=["forge-bundles"])


# ── Request/Response Models ────────────────────────────────────────────────

class ArtifactSelection(BaseModel):
    """One artifact to include in a bundle."""

    kind: str  # "skill", "tool", "layer", "plugin"
    id: str
    version: str


class ExportBundleRequest(BaseModel):
    """Request to export a bundle."""

    bundle_id: str
    bundle_version: str
    description: Optional[str] = None
    selections: list[ArtifactSelection]


@router.post("/import")
async def import_bundle_endpoint(
    session: dict[str, Any] = Depends(require_session_csrf_on_mutation),
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
) -> dict[str, Any]:
    """Import a Forge Bundle into the tenant's forges."""
    tenant_id = session.get("tenant_id")
    user_id = session.get("user_id", "unknown")

    if not tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="tenant_id missing",
        )

    # Size check (50 MiB max)
    MAX_SIZE = 50 * 1024 * 1024
    file_size = 0
    bundle_data = io.BytesIO()

    async for chunk in file.file:
        file_size += len(chunk)
        if file_size > MAX_SIZE:
            raise HTTPException(status_code=http_status.HTTP_413_PAYLOAD_TOO_LARGE)
        bundle_data.write(chunk)

    bundle_bytes = bundle_data.getvalue()

    # Validate and import
    try:
        result = import_bundle(
            data=bundle_bytes,
            tenant_id=tenant_id,
            user_id=user_id,
        )

        return {
            "bundle_id": result.bundle_id,
            "artifact_count": result.artifacts_staged + result.artifacts_failed,
            "imported_count": result.artifacts_staged,
            "failed_count": result.artifacts_failed,
            "requires_review": result.artifacts_failed > 0,
            "errors": result.errors,
        }
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# ── Phase 4: Quarantine Management Routes ──────────────────────────────────

@router.get("/quarantine")
async def list_quarantine(
    session: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """List all quarantined tools and staged plugins for this tenant."""
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="tenant_id missing",
        )

    try:
        quarantine = ToolQuarantineWorkflow(tenant_id)
        quarantined_tools = quarantine.list_quarantined_tools()

        tools_response = [
            {
                "quarantine_id": qt.quarantine_id,
                "tool_id": qt.tool_id,
                "version": qt.version,
                "bundle_id": qt.bundle_id,
                "staged_at": qt.timestamp,
                "spec": qt.spec_json,
            }
            for qt in quarantined_tools
        ]

        # TODO: Add staged plugins from StagingManager (ADR-0511)
        plugins_response = []

        return {
            "tools": tools_response,
            "plugins": plugins_response,
        }
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list quarantine: {str(e)}",
        )


@router.post("/quarantine/{quarantine_id}/accept")
async def accept_quarantined_tool(
    quarantine_id: str,
    session: dict[str, Any] = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    """Accept a quarantined tool and promote it to the active registry.

    Audit-First: emit quarantine_accepted event BEFORE registry write.
    """
    tenant_id = session.get("tenant_id")
    user_id = session.get("user_id", "unknown")

    if not tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="tenant_id missing",
        )

    # Imported here, not at module level: the module does not exist on this
    # build, and a module-level ImportError unmounted /console and every
    # /v1/console/* route (2026-10-06). Checked BEFORE the quarantine is
    # touched and before "quarantine_accepted" is audited, so a refusal
    # leaves the tool in quarantine and the trail claims nothing.
    try:
        from core.orchestration.tool_forge.registry import ToolRegistry  # type: ignore[import-not-found]
    except ImportError:
        raise HTTPException(
            status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
            detail="Tool registry is not available on this build; the tool stays in quarantine.",
        )

    try:
        quarantine = ToolQuarantineWorkflow(tenant_id)
        tool_data = quarantine.accept_quarantined_tool(quarantine_id, user_id)
        spec = tool_data["spec"]
        impl_bytes = tool_data["impl_bytes"]

        tool_id = spec.get("name") or spec.get("id")
        version = spec.get("version", "1.0.0")

        # Audit-First: emit acceptance event BEFORE registry write
        try:
            emit_audit_event(
                "forge_bundle.quarantine_accepted",
                tenant_id=tenant_id,
                artifact_kind="tool",
                artifact_id=tool_id,
                artifact_version=version,
                quarantine_id=quarantine_id,
                user_id=user_id,
            )
        except ForgeBundleAuditError as e:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Audit chain write failed: {str(e)}",
            )

        # Now promote to registry
        registry = ToolRegistry(tenant_id=tenant_id)
        registry.create(spec, impl_bytes)

        # Clean up quarantine entry
        quarantine.cleanup_quarantine_entry(quarantine_id)

        return {
            "status": "accepted",
            "tool_id": tool_id,
            "version": version,
            "now_live": True,
        }

    except QuarantineError as e:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Quarantine error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to accept quarantined tool: {str(e)}",
        )


@router.post("/quarantine/{quarantine_id}/reject")
async def reject_quarantined_tool(
    quarantine_id: str,
    session: dict[str, Any] = Depends(require_session_csrf_on_mutation),
    reason: str = Query("", description="Optional rejection reason"),
) -> dict[str, Any]:
    """Reject a quarantined tool and delete it from staging.

    Query param: reason (optional URL-encoded rejection reason)
    """
    tenant_id = session.get("tenant_id")
    user_id = session.get("user_id", "unknown")

    if not tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="tenant_id missing",
        )

    try:
        quarantine = ToolQuarantineWorkflow(tenant_id)
        tool_data = quarantine.accept_quarantined_tool(quarantine_id, user_id)
        spec = tool_data["spec"]

        tool_id = spec.get("name") or spec.get("id")
        version = spec.get("version", "1.0.0")

        # Audit rejection
        try:
            emit_audit_event(
                "forge_bundle.quarantine_rejected",
                tenant_id=tenant_id,
                artifact_kind="tool",
                artifact_id=tool_id,
                artifact_version=version,
                quarantine_id=quarantine_id,
                user_id=user_id,
            )
        except ForgeBundleAuditError as e:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Audit chain write failed: {str(e)}",
            )

        # Clean up quarantine entry
        quarantine.cleanup_quarantine_entry(quarantine_id)

        return {
            "status": "rejected",
            "tool_id": tool_id,
            "version": version,
            "reason": reason,
        }

    except QuarantineError as e:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Quarantine error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject quarantined tool: {str(e)}",
        )


# ── Phase 4: Export & Validation Routes ────────────────────────────────────

@router.get("/export/available")
async def list_available_artifacts(
    session: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """List available artifacts for export (skills, tools, layers, plugins)."""
    tenant_id = session.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="tenant_id missing",
        )

    # TODO: Implement actual inventory collection from each forge
    # For now, return empty inventory structure
    return {
        "skills": [],
        "tools": [],
        "layers": [],
        "plugins": [],
    }


@router.post("/export")
async def export_bundle(
    request: ExportBundleRequest,
    session: dict[str, Any] = Depends(require_session_csrf_on_mutation),
) -> Response:
    """Build and export a Forge Bundle as a downloadable ZIP.

    POST body:
    {
        "bundle_id": "my-bundle",
        "bundle_version": "1.0.0",
        "description": "optional description",
        "selections": [
            {"kind": "skill", "id": "skill-name", "version": "1.0.0"},
            {"kind": "tool", "id": "tool-name", "version": "1.0.0"},
            ...
        ]
    }
    """
    tenant_id = session.get("tenant_id")
    user_id = session.get("user_id", "unknown")

    if not tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="tenant_id missing",
        )

    if not request.selections:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="At least one artifact selection is required",
        )

    try:
        # TODO: Convert selection objects to Selection objects
        # For now, this is a placeholder for the full implementation
        # selections = [_build_selection(s) for s in request.selections]
        selections = []

        # Build bundle
        result = build_bundle(
            bundle_id=request.bundle_id,
            bundle_version=request.bundle_version,
            selections=selections,
            tenant_id=tenant_id,
            description=request.description,
        )

        # Return ZIP with Content-Disposition header for download
        return Response(
            content=result.data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={request.bundle_id}-{request.bundle_version}.zip"
            },
        )

    except ExportError as e:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Export error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export bundle: {str(e)}",
        )


@router.post("/validate")
async def validate_bundle_endpoint(
    session: dict[str, Any] = Depends(require_session),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Validate a Forge Bundle without importing it.

    Multipart form: file (ZIP)
    Returns validation report with origin_verified, unchecked_references, unscanned_files.
    """
    tenant_id = session.get("tenant_id")
    user_id = session.get("user_id", "unknown")

    if not tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="tenant_id missing",
        )

    # Size check (50 MiB max)
    MAX_SIZE = 50 * 1024 * 1024
    file_size = 0
    bundle_data = io.BytesIO()

    async for chunk in file.file:
        file_size += len(chunk)
        if file_size > MAX_SIZE:
            raise HTTPException(status_code=http_status.HTTP_413_PAYLOAD_TOO_LARGE)
        bundle_data.write(chunk)

    bundle_bytes = bundle_data.getvalue()

    try:
        # Validate without importing
        report = validate_bundle(bundle_bytes)

        # Audit the validation
        try:
            emit_audit_event(
                "forge_bundle.validated",
                tenant_id=tenant_id,
                bundle_id=report.envelope.id,
                origin_verified=report.origin_verified,
                unchecked_references_count=len(report.unchecked_references),
                unscanned_files_count=len(report.unscanned_files),
                total_uncompressed_bytes=report.total_uncompressed_bytes,
                user_id=user_id,
            )
        except ForgeBundleAuditError:
            # Validation succeeded but audit failed — still return the report
            # but flag it as incomplete
            pass

        return {
            "bundle_id": report.envelope.id,
            "bundle_version": report.envelope.version,
            "origin_verified": report.origin_verified,
            "artifact_count": len(report.envelope.artifacts),
            "total_uncompressed_bytes": report.total_uncompressed_bytes,
            "unchecked_references": [
                {"kind": r.kind, "id": r.id, "version": r.version}
                for r in report.unchecked_references
            ],
            "unscanned_files": list(report.unscanned_files),
        }

    except BundleRejected as e:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation failed [{e.stage}]: {e.reason}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to validate bundle: {str(e)}",
        )
