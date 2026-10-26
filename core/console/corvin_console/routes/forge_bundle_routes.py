"""Forge Bundle console routes (ADR-2229 Phase 3/4 + per-item export Phase 4).

    GET  /forge-bundles/exportable                  what this tenant can export (session)
    GET  /forge-bundles/export/{type}/{id}          single-item download as ZIP (session)
    POST /forge-bundles/export                      build + download a bundle (session + CSRF)
    POST /forge-bundles/validate                    validate an upload, write nothing (session + CSRF)
    POST /forge-bundles/import                      validate + per-forge intake (session + CSRF)
                                                    (validate and import: at most 2 of each at once, else 429)
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

import threading
from contextlib import contextmanager
from typing import Annotated, Any, Iterator, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_csrf, require_session

def _host_tenant_session(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> session_auth.SessionRecord:
    """Every forge-bundle route belongs to the install's own tenant.

    The audit chokepoint accepts records only for the process tenant
    (ADR-0562 D2), and the skill and project-scope tool stores are host-wide,
    so another tenant could neither complete an import nor be kept out of
    host-wide data. Refused up front, with the real reason."""
    from forge.tenants import current_tenant

    if rec.tenant_id != current_tenant():
        raise HTTPException(status_code=403, detail="Forge Bundles are available to the install owner's tenant only")
    return rec


router = APIRouter(dependencies=[Depends(_host_tenant_session)])

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_ACTOR = "console"
# Forged and installed skills live in host-wide stores, not per tenant. Only
# the install's own tenant (the process tenant — this identifies the HOST
# owner, it does not route the request) with an owner/admin session may read
# or write them through a bundle; any other tenant would see or change another
# tenant's skills.
_HOST_SKILL_TIERS = frozenset({"owner", "admin"})


def _may_touch_host_skills(rec: session_auth.SessionRecord) -> bool:
    # The router dependency already pinned the session to the install's tenant.
    return getattr(rec, "tier", None) in _HOST_SKILL_TIERS


class ArtifactSelection(BaseModel):
    kind: Literal["skill", "tool", "layer", "plugin"]
    id: str
    version: str


class ExportBody(BaseModel):
    bundle_id: str
    bundle_version: str
    description: str | None = Field(default=None, max_length=2000)
    selections: list[ArtifactSelection]


# ADV-03 (2026-10-08): validate/import are sync handlers that hold a thread
# pool worker for the whole request — a layer import runs pytest gates and the
# LLM review inside it. 48 concurrent imports from one session held every
# worker and stalled ALL sync console routes for the review's duration (5.5 s
# measured; async /healthz stayed fast, so a liveness probe never noticed).
# At most _HEAVY_SLOTS of EACH kind run at once; a request that finds no free
# slot is answered 429 at once — it never WAITS, because a waiting handler
# would hold the very worker this guard exists to keep free.
#
# Two pools, not one (R4-A3-1): validate is cheap and the UI calls it on every
# file pick; with one shared pool a validate flood made the operator's own
# import answer 429 in 6 of 8 attempts. Worst case is now 2 + 2 workers.
# Process-wide on purpose: Forge Bundles serve the install owner's tenant only.
_HEAVY_SLOTS = 2
_heavy = {"validate": threading.BoundedSemaphore(_HEAVY_SLOTS),
          "import": threading.BoundedSemaphore(_HEAVY_SLOTS)}
_RETRY_AFTER_S = {"validate": "2", "import": "15"}  # a validate takes seconds, an import (gates + review) longer


@contextmanager
def _heavy_slot(kind: str) -> Iterator[None]:
    if not _heavy[kind].acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f"another bundle {kind} is running on this install; try again in a few seconds",
            headers={"Retry-After": _RETRY_AFTER_S[kind]},
        )
    try:
        yield
    finally:
        _heavy[kind].release()


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
    from core.forge_bundle.inventory import InventoryUnavailable, exportable as _exportable

    try:
        return _exportable(rec.tenant_id, include_skills=_may_touch_host_skills(rec))
    except InventoryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


_KIND_ALIASES = {"tools": "tool", "skills": "skill", "os_skills": "os_skill", "layers": "layer", "plugins": "plugin"}


def _default_item_version(artifact_type: str, artifact_id: str, rec: session_auth.SessionRecord) -> str:
    """The version an export carries when the caller names none.

    Skills and layers have one in the inventory (the newest listed wins); tools
    and OS-Skills carry none of their own, so the file gets a fixed label."""
    if artifact_type == "skill":
        # A SkillForge skill (what the Skills tab lists) has no version of its own.
        from core.forge_bundle.item_export import DEFAULT_PROMPT_SKILL_VERSION, find_prompt_skill
        found = find_prompt_skill(artifact_id, rec.tenant_id)
        if found is not None:
            v = found[1].get("version")
            return v if isinstance(v, str) and v.strip() else DEFAULT_PROMPT_SKILL_VERSION
    if artifact_type in ("skill", "layer"):
        from core.forge_bundle.inventory import InventoryUnavailable, exportable
        try:
            inv = exportable(rec.tenant_id, include_skills=_may_touch_host_skills(rec))
        except InventoryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        found = [e["version"] for e in inv["skills" if artifact_type == "skill" else "layers"]
                 if e["id"] == artifact_id and e.get("version")]
        if not found:
            raise HTTPException(status_code=404, detail=f"{artifact_type} {artifact_id} not found")
        return sorted(found)[-1]
    return "1.0.0"


@router.get("/forge-bundles/export/{artifact_type}/{artifact_id}")
def export_single_item(
    artifact_type: str,
    artifact_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    version: str | None = None,
) -> Response:
    """Download a single artifact as a ZIP.

    For skills/os_skills: Claude-Web-compatible format with SKILL.md.
    For other types: native format (tool: spec.json + impl, layer: manifest.json, plugin: pre-built ZIP).
    """
    from core.forge_bundle.audit import ForgeBundleAuditError
    from core.forge_bundle.item_export import ItemExportError, export_single_item as _export_item

    # Accept the plural spellings the forge tabs use as well.
    artifact_type = _KIND_ALIASES.get(artifact_type, artifact_type)

    # Packaged (skills_gen) skills are host-wide; only owner/admin may export them.
    # A SkillForge skill is read from the session's own tenant, like the Skills tab.
    if artifact_type == "skill" and not _may_touch_host_skills(rec):
        from core.forge_bundle.item_export import find_prompt_skill
        if find_prompt_skill(artifact_id, rec.tenant_id) is None:
            raise HTTPException(
                status_code=403,
                detail="skills live in a host-wide store; only the install owner may export them",
            )

    # OS-Skills are read-only descriptions, accessible to all
    if artifact_type == "os_skills":
        artifact_type = "os_skill"  # normalize to singular for internal use

    # Plugins require a package path on disk (CLI only for now)
    if artifact_type == "plugin":
        raise HTTPException(
            status_code=400,
            detail="plugin export requires a built package on disk; use the CLI: scripts/forge_bundle_cli.py export --plugin ID@VER:PATH",
        )

    if artifact_type not in ("skill", "os_skill", "tool", "layer", "plugin"):
        raise HTTPException(status_code=400, detail=f"unknown artifact type: {artifact_type}")

    if not version:
        version = _default_item_version(artifact_type, artifact_id, rec)

    try:
        result = _export_item(artifact_type, artifact_id, version, rec.tenant_id, package_path=None)
    except ItemExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ForgeBundleAuditError as exc:
        raise _audit_unavailable(exc) from None

    # Audit the per-item export
    try:
        from core.forge_bundle.audit import emit as audit_emit
        audit_emit(
            "forge_bundle.item_exported",
            tenant_id=rec.tenant_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            artifact_version=version,
            size_bytes=result.size_bytes,
            sha256=result.sha256,
        )
    except ForgeBundleAuditError:
        # Log but don't fail the response — the export itself succeeded
        pass

    return Response(
        content=result.data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact_id}-{version}.zip"',
        },
    )


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

    with _heavy_slot("validate"):
        data = _read_upload(file)
        try:
            report = check_bundle(data, tenant_id=rec.tenant_id)
        except BundleRejected as exc:
            if exc.stage == "inventory":
                raise HTTPException(status_code=503, detail={"stage": exc.stage, "reason": exc.reason}) from None
            return {"valid": False, "stage": exc.stage, "reason": exc.reason, "origin_verified": False}
        return {"valid": True, **_report_dict(report)}


@router.post("/forge-bundles/import")
def import_upload(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    from core.forge_bundle.audit import ForgeBundleAuditError
    from core.forge_bundle.import_module import BundleImportAborted, BundleImportError, import_bundle
    from core.forge_bundle.item_import import ItemImportError, detect_item_type_from_zip, import_single_item

    with _heavy_slot("import"):
        data = _read_upload(file)

        # Try to detect if this is a single-item ZIP (Phase 4 enhancement)
        detected = None
        try:
            detected = detect_item_type_from_zip(data)
        except ItemImportError:
            # If detection fails, treat it as a bundle and let the bundle importer handle it
            pass

        # If detected as single-item, import it directly
        if detected is not None:
            # Single-item import (Phase 4)
            if detected.item_type == "skill" and not _may_touch_host_skills(rec):
                raise HTTPException(
                    status_code=403,
                    detail="skills live in a host-wide store; only the install owner may import them",
                )
            try:
                outcome = import_single_item(detected, tenant_id=rec.tenant_id, actor=_ACTOR)
                return {
                    "bundle_id": "single-item",
                    "bundle_version": detected.item_version,
                    "artifact_count": 1,
                    "failed_count": 0,
                    "outcomes": [
                        {
                            "kind": detected.item_type,
                            "id": detected.item_id,
                            "version": detected.item_version,
                            "status": outcome.get("status", "unknown"),
                            "detail": outcome.get("detail", ""),
                        }
                    ],
                    "origin_verified": False,
                }
            except ItemImportError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            except ForgeBundleAuditError as exc:
                raise _audit_unavailable(exc) from None

        # Otherwise, import as a bundle (existing logic)
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
        outcome = ("the tool was not created" if action == "accept" else "the tool was not rejected")
        raise HTTPException(status_code=503,
                            detail=f"audit chain unavailable; {outcome} and is still in the review queue") from None
    except Exception as exc:  # noqa: BLE001 — never echo raw exception text to the client
        raise HTTPException(status_code=500,
                            detail=f"the decision failed ({type(exc).__name__}); the tool is still in the review queue") from None
    return {"status": "accepted" if action == "accept" else "rejected",
            "tool_id": entry.tool_id, "version": entry.version, "quarantine_id": qid}


@router.post("/forge-bundles/quarantine/{qid}/accept")
def accept_quarantined(qid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict[str, Any]:
    return _decide(qid, rec, "accept")


@router.post("/forge-bundles/quarantine/{qid}/reject")
def reject_quarantined(qid: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict[str, Any]:
    return _decide(qid, rec, "reject")
