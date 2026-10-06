"""Forge Bundle console routes (ADR-2229 Phase 3/4).

    GET  /forge-bundles/exportable                  what this tenant can export (session)
    POST /forge-bundles/export                      build + download a bundle (session + CSRF)
    POST /forge-bundles/validate                    validate an upload, write nothing (session + CSRF)
    POST /forge-bundles/import                      validate + per-forge intake (session + CSRF)
    GET  /forge-bundles/quarantine                  tools awaiting review (session)
    POST /forge-bundles/quarantine/{qid}/accept     create the tool (session + CSRF)
    POST /forge-bundles/quarantine/{qid}/reject     drop the tool (session + CSRF)

The router carries NO prefix of its own: the console router is mounted under
``/v1/console`` by the gateway, so a router-level ``/v1/console/...`` prefix
lands at ``/v1/console/v1/console/...`` — which is where every route of this
module lived until 2026-10-06.

Tenant is always the session's ``rec.tenant_id``. Imported plugins are staged
in the existing plugin-upload store; they are approved under
``/plugin-uploads/{id}/approve``, not here. Handlers are sync ``def``: a layer
import runs Layer Forge's quality gates (pytest subprocesses).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_csrf, require_session

router = APIRouter()

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_ACTOR = "console"
# Forged and installed skills live in host-wide stores, not per tenant. Only
# the install's own tenant (the process tenant — this identifies the HOST
# owner, it does not route the request) with an owner/admin session may read
# or write them through a bundle; any other tenant would see or change another
# tenant's skills.
_HOST_SKILL_TIERS = frozenset({"owner", "admin"})


def _may_touch_host_skills(rec: session_auth.SessionRecord) -> bool:
    from forge.tenants import current_tenant

    return getattr(rec, "tier", None) in _HOST_SKILL_TIERS and rec.tenant_id == current_tenant()


class ArtifactSelection(BaseModel):
    kind: Literal["skill", "tool", "layer", "plugin"]
    id: str
    version: str


class ExportBody(BaseModel):
    bundle_id: str
    bundle_version: str
    description: str | None = Field(default=None, max_length=2000)
    selections: list[ArtifactSelection]


def _audit_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail="audit chain unavailable; nothing was changed")


def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(_MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="bundle larger than 50 MiB")
    return data



def _report_dict(report) -> dict[str, Any]:
    env = report.envelope
    return {
        "bundle_id": env.id,
        "bundle_version": env.version,
        "description": env.description,
        "created_at": env.created_at,
        "artifacts": [
            {"kind": a.kind, "id": a.id, "version": a.version, "file_count": len(a.files),
             "requires": [{"kind": r.kind, "id": r.id, "version": r.version} for r in a.requires]}
            for a in env.artifacts
        ],
        "total_uncompressed_bytes": report.total_uncompressed_bytes,
        "origin_verified": report.origin_verified,
    }


@router.get("/forge-bundles/exportable")
def exportable(rec: Annotated[session_auth.SessionRecord, Depends(require_session)]) -> dict[str, Any]:
    from core.forge_bundle.inventory import exportable as _exportable

    return _exportable(rec.tenant_id, include_skills=_may_touch_host_skills(rec))


@router.post("/forge-bundles/export")
def export_bundle(
    body: ExportBody,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    from core.forge_bundle.audit import ForgeBundleAuditError
    from core.forge_bundle.export import (
        ExportError, LayerSelection, SkillSelection, ToolSelection, build_bundle,
    )

    try:
        selections = []
        for s in body.selections:
            if s.kind == "skill" and not _may_touch_host_skills(rec):
                raise HTTPException(status_code=403, detail="skills live in a host-wide store; only the install owner may export them")
            if s.kind == "plugin":
                raise HTTPException(
                    status_code=400,
                    detail="plugin export needs a built package on disk; use the CLI: scripts/forge_bundle_cli.py export --plugin ID@VER:PATH",
                )
            cls = {"skill": SkillSelection, "tool": ToolSelection, "layer": LayerSelection}[s.kind]
            selections.append(cls(s.id, s.version))
        result = build_bundle(
            bundle_id=body.bundle_id, bundle_version=body.bundle_version,
            selections=selections, tenant_id=rec.tenant_id, description=body.description,
        )
    except ExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ForgeBundleAuditError as exc:
        raise _audit_unavailable(exc) from None
    return Response(
        content=result.data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{result.bundle_id}-{result.bundle_version}.zip"'},
    )


@router.post("/forge-bundles/validate")
def validate_upload(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    from core.forge_bundle.import_module import check_bundle
    from core.forge_bundle.validate import BundleRejected

    data = _read_upload(file)
    try:
        report = check_bundle(data, tenant_id=rec.tenant_id)
    except BundleRejected as exc:
        if exc.stage == "inventory":
            raise HTTPException(status_code=503, detail=exc.reason) from None
        return {"valid": False, "stage": exc.stage, "reason": exc.reason, "origin_verified": False}
    return {"valid": True, **_report_dict(report)}


@router.post("/forge-bundles/import")
def import_upload(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    from core.forge_bundle.audit import ForgeBundleAuditError
    from core.forge_bundle.import_module import BundleImportAborted, BundleImportError, import_bundle

    data = _read_upload(file)
    try:
        result = import_bundle(data, tenant_id=rec.tenant_id, actor=_ACTOR,
                               may_install_skills=_may_touch_host_skills(rec))
    except BundleImportError as exc:
        code = 503 if exc.stage == "inventory" else 422
        raise HTTPException(status_code=code, detail={"stage": exc.stage, "reason": exc.reason}) from None
    except BundleImportAborted as exc:
        raise HTTPException(status_code=503, detail={
            "message": "audit chain unavailable; the import stopped — see which artifacts landed",
            **exc.result.to_dict()}) from None
    except ForgeBundleAuditError as exc:
        raise _audit_unavailable(exc) from None
    return result.to_dict()


@router.get("/forge-bundles/quarantine")
def list_quarantine(rec: Annotated[session_auth.SessionRecord, Depends(require_session)]) -> dict[str, Any]:
    from core.forge_bundle.tool_quarantine import ToolQuarantine

    items = [e.to_public() for e in ToolQuarantine(rec.tenant_id).list()]
    return {"items": items, "count": len(items)}


def _decide(qid: str, rec: session_auth.SessionRecord, action: str) -> dict[str, Any]:
    from core.forge_bundle import tool_quarantine as tq
    from core.forge_bundle.audit import ForgeBundleAuditError

    try:
        entry = (tq.accept if action == "accept" else tq.reject)(rec.tenant_id, qid, actor=_ACTOR)
    except tq.QuarantineNotFound:
        raise HTTPException(status_code=404, detail="no such quarantine entry") from None
    except tq.QuarantineConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except tq.QuarantineForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except tq.OutcomeNotRecorded as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except tq.QuarantineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ForgeBundleAuditError:
        raise HTTPException(status_code=503,
                            detail="audit chain unavailable; the tool was not created and is back in the review queue") from None
    except Exception as exc:  # noqa: BLE001 — never echo raw exception text to the client
        raise HTTPException(status_code=500,
                            detail=f"the decision failed ({type(exc).__name__}); the tool is back in the review queue") from None
    return {"status": "accepted" if action == "accept" else "rejected",
            "tool_id": entry.tool_id, "version": entry.version, "quarantine_id": qid}


@router.post("/forge-bundles/quarantine/{qid}/accept")
def accept_quarantined(qid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict[str, Any]:
    return _decide(qid, rec, "accept")


@router.post("/forge-bundles/quarantine/{qid}/reject")
def reject_quarantined(qid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict[str, Any]:
    return _decide(qid, rec, "reject")
