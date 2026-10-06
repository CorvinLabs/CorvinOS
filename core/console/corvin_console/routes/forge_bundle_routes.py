"""Forge Bundle Import Routes (ADR-2229 Phase 3).

POST /v1/console/forge-bundles/import — receives .zip file, validates,
and imports into all four forges (audit-first pattern).
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status as http_status
from typing import Any, Optional

from core.forge_bundle.import_module import import_bundle, extract_bundle
from core.forge_bundle.validate import BundleRejected
from core.forge_bundle.audit import ForgeBundleAuditError

from .. import auth as session_auth
from ..deps import require_session, require_session_csrf_on_mutation

router = APIRouter(prefix="/v1/console/forge-bundles", tags=["forge-bundles"])


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
    import io
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
