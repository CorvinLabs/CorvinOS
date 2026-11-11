"""Projects API (ADR-2315): ``/v1/console/projects``.

Behind a session; every mutation also needs the CSRF token. The tenant is ALWAYS the session's
(``rec.tenant_id``); a project of another tenant is 404, never a hint that it exists. Paths from
the client exist only in ``plan`` and ``adopt`` and are judged by ``core.projects.registry``;
every other call carries a project id and resolves the path from the registry.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Annotated, Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.projects import activity

from core.projects import fragments as F
from core.projects import manifest, registry
from core.projects.fsutil import LockBusy, UnsafePath, WriteFailed
from core.task_tracking import service as tt

from .. import auth as session_auth
from .. import projects_service as svc
from ..deps import require_csrf, require_session

router = APIRouter(prefix="/projects")

Sess = Annotated[session_auth.SessionRecord, Depends(require_session)]
SessCsrf = Annotated[session_auth.SessionRecord, Depends(require_csrf)]


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    project_id: Optional[str] = Field(default=None, max_length=28)
    location: Optional[str] = Field(default=None, max_length=1000)
    features: Optional[list[str]] = Field(default=None, max_length=64)
    docs_mode: Optional[str] = Field(default=None, max_length=10)
    classification: Optional[str] = Field(default=None, max_length=16)
    assistant_access: Optional[str] = Field(default=None, max_length=8)
    storage: Optional[str] = Field(default=None, max_length=8)


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=8, max_length=80)


class AdoptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str = Field(min_length=1, max_length=1000)
    project_id: Optional[str] = Field(default=None, max_length=28)
    title: Optional[str] = Field(default=None, max_length=120)


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: Optional[str] = Field(default=None, max_length=16)
    assistant_access: Optional[str] = Field(default=None, max_length=8)
    confirm: bool = False


class RootBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=1000)


class AttachBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)


class MoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str = Field(min_length=1, max_length=1000)


class FeaturesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set: dict[str, bool] = Field(min_length=1, max_length=64)
    accept_dependencies: bool = False
    cascade: bool = False


class VideoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class RepairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = False


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except svc.Conflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "reason_class": exc.reason_class, **exc.extra})
    except svc.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except svc.ProjectsError as exc:
        raise HTTPException(status_code=exc.status, detail={"message": str(exc), "reason_class": exc.reason_class})
    except registry.ProjectPathError as exc:
        detail = {"message": str(exc), "reason_class": getattr(exc, "reason_class", "path_rule")}
        if getattr(exc, "warnings", None):
            detail["warnings"] = list(exc.warnings)
        raise HTTPException(status_code=422, detail=detail)
    except registry.RegistryError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "reason_class": "registry"})
    except (F.FragmentError, manifest.ManifestError, UnsafePath) as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)[:300], "reason_class": "invalid"})
    except tt.AuditUnavailable:
        raise HTTPException(status_code=503, detail="audit chain unavailable - nothing was written")
    except LockBusy as exc:                        # another request on the same project/registry is still running
        raise HTTPException(status_code=409, detail={"message": str(exc), "reason_class": "busy"})
    except WriteFailed as exc:                     # read-only project.yaml, a file held open by another program
        raise HTTPException(status_code=409, detail={"message": str(exc)[:300], "reason_class": "write_failed"})
    except OSError as exc:
        # the filesystem said no (permission, a lock, a vanished drive, a too-long path): the reason class, never the path, never a 500
        raise HTTPException(status_code=409, detail={"message": f"the filesystem refused the operation ({type(exc).__name__})",
                                                     "reason_class": "filesystem"})
    except UnicodeError:
        raise HTTPException(status_code=422, detail={"message": "a project file is not UTF-8 or UTF-16 text", "reason_class": "invalid"})
    except ValueError as exc:                      # a project file that does not parse the way the engine expects (markers, sizes)
        raise HTTPException(status_code=422, detail={"message": f"a project file could not be processed ({str(exc)[:120]})", "reason_class": "invalid"})


@router.get("")
def list_projects(rec: Sess) -> dict:
    return _call(svc.list_projects, rec.tenant_id)


@router.get("/templates")
def templates(rec: Sess) -> dict:
    return _call(svc.list_templates, rec.tenant_id)


@router.post("/plan")
def plan(body: PlanBody, rec: SessCsrf) -> dict:
    return _call(svc.plan_project, rec.tenant_id, template=body.template, title=body.title,
                 project_id=body.project_id, location=body.location, features=body.features,
                 docs_mode=body.docs_mode, classification=body.classification,
                 assistant_access=body.assistant_access, storage=body.storage)


@router.post("", status_code=201)
def create(body: CreateBody, rec: SessCsrf) -> dict:
    return _call(svc.create_project, rec.tenant_id, body.plan_id)


@router.post("/adopt", status_code=201)
def adopt(body: AdoptBody, rec: SessCsrf) -> dict:
    return _call(svc.adopt_project, rec.tenant_id, body.location, project_id=body.project_id, title=body.title)


@router.put("/root")
def set_root(body: RootBody, rec: SessCsrf) -> dict:
    return _call(svc.set_root, rec.tenant_id, body.path)


class StorageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_storage: Optional[str] = Field(default=None, max_length=8)
    projects_root: Optional[str] = Field(default=None, max_length=1000)
    rekey: bool = False


class RelocateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str = Field(min_length=1, max_length=1000)


@router.put("/settings/storage")
def put_storage(body: StorageBody, rec: SessCsrf) -> dict:
    """Where new projects go (ADR-2321 Decision 8); ``{rekey: true}`` starts a new workspace for a copied installation."""
    if body.rekey:
        return _call(svc.rekey_workspace, rec.tenant_id)
    if body.default_storage is None:
        raise HTTPException(status_code=400, detail={"message": "default_storage is required", "reason_class": "error"})
    return _call(svc.set_default_storage, rec.tenant_id, body.default_storage, body.projects_root)


# ── the collection namespace /projects/-/... (ADR-2321 Decision 16, REVIEW-0061 M4) ────────────────────────────────────
# '-' can never be a project id (manifest.PROJECT_ID_RE), so collection-level routes of ADR-2321..2323 (browse, export, import,
# trash) live under it and RESERVED_IDS stays as it is (extending it would invalidate existing projects on load). Registered
# BEFORE /{project_id}; later tasks add their routes to `collection`. Anything not defined there is 404 from here, never a lookup.
collection = APIRouter(prefix="/-")


@collection.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
def _collection_unknown(rest: str, rec: Sess) -> dict:
    raise HTTPException(status_code=404, detail={"message": "no such collection route", "reason_class": "not_found"})


@router.get("/chat-attachment")
def chat_attachment(session_id: str, rec: Sess) -> dict:
    return _call(svc.chat_attachment, rec.tenant_id, session_id)


@router.post("/detach")
def detach(body: AttachBody, rec: SessCsrf) -> dict:
    return _call(svc.detach_chat, rec.tenant_id, body.session_id)


router.include_router(collection)


@router.post("/{project_id}/attach")
def attach(project_id: str, body: AttachBody, rec: SessCsrf) -> dict:
    return _call(svc.attach_chat, rec.tenant_id, project_id, body.session_id)


@router.get("/{project_id}")
def get_project(project_id: str, rec: Sess) -> dict:
    return _call(svc.get_project, rec.tenant_id, project_id)


@router.get("/{project_id}/structure")
def get_structure(project_id: str, rec: Sess, depth: int = 2) -> dict:
    """The project folder's entries (read-only), its knowledge-base counts and its video rows (T-0359)."""
    return _call(svc.structure, rec.tenant_id, project_id, depth)


@router.get("/{project_id}/health")
def get_health(project_id: str, rec: Sess) -> dict:
    return _call(svc.health, rec.tenant_id, project_id)


@router.put("/{project_id}/features")
def put_features(project_id: str, body: FeaturesBody, rec: SessCsrf) -> dict:
    return _call(svc.set_features, rec.tenant_id, project_id, body.set,
                 accept_dependencies=body.accept_dependencies, cascade=body.cascade)


@router.post("/{project_id}/repair")
def post_repair(project_id: str, body: RepairBody, rec: SessCsrf) -> dict:
    return _call(svc.repair, rec.tenant_id, project_id, dry_run=body.dry_run)


@router.post("/{project_id}/videos", status_code=201)
def post_video(project_id: str, body: VideoBody, rec: SessCsrf) -> dict:
    """Creates one video's folder, sources.md and (with the knowledge base on) its epic and tasks (T-0361)."""
    return _call(svc.new_video, rec.tenant_id, project_id, body.name)


# ── live work on a project (CONCEPT-0128, ADR-2320) ───────────────────────────────────────────

def _activity_root(tenant_id: str, project_id: str):
    """The registered project's folder for THIS tenant, or 404. The only way into a channel: a viewer of another tenant never gets here."""
    e = svc._entry(tenant_id, project_id)
    return e.get("path")


def _sse(event: str, data: dict, *, id_: Optional[str] = None) -> str:
    head = f"id: {id_}\n" if id_ else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _activity_stream(request: Request, tenant_id: str, project_id: str, cursor: Optional[str]) -> AsyncIterator[str]:
    """One short stream: snapshot first, live events, a heartbeat, an ``end`` event, then the response closes (``activity.STREAM_LIFETIME_S``,
    because uvicorn waits for an open response at shutdown and the app is never told). The browser reconnects with ``Last-Event-ID``."""
    def _root():
        if project_id == "corvin":                    # built in: the tenant's own knowledge base (CONCEPT-0129)
            from core.projects import builtin  # noqa: PLC0415
            return builtin.entry(tenant_id).get("path") or None
        e = registry.get(tenant_id, project_id)
        return e.get("path") if e else None

    try:
        q = activity.subscribe(tenant_id, project_id, _root)
    except activity.StreamLimit:                      # lost the race against the pre-check: say so instead of failing mid-stream
        yield _sse("end", {"reason": "stream_limit"})
        return
    try:
        snap = activity.snapshot(tenant_id, project_id, cursor)
        yield "retry: 2000\n\n"
        yield _sse("snapshot", snap, id_=f"{snap['epoch']}:{snap['seq']}")
        sent = snap["seq"]                             # subscribed BEFORE the snapshot: queued events at or below it are duplicates
        deadline = time.monotonic() + activity.STREAM_LIFETIME_S
        last_beat = time.monotonic()
        tick = 0
        reason = "lifetime"
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            try:
                ev = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                ev = None
            tick += 1
            if ev is not None:
                if ev["seq"] > sent:
                    sent = ev["seq"]
                    yield _sse("activity", ev, id_=ev["id"])
                last_beat = time.monotonic()
            elif time.monotonic() - last_beat >= 5.0:
                last_beat = time.monotonic()
                yield ": ping\n\n"
            if tick % 3 == 0 and project_id != "corvin" and await asyncio.to_thread(registry.get, tenant_id, project_id) is None:
                reason = "project_removed"
                break
        yield _sse("end", {"reason": reason})
    finally:
        activity.unsubscribe(tenant_id, project_id, q)


@router.get("/{project_id}/activity")
def project_activity(project_id: str, rec: Sess, after: Optional[str] = None) -> dict:
    """The snapshot (agents, backlog after the cursor). Cheap: it starts no watcher - the live dot polls this."""
    _call(_activity_root, rec.tenant_id, project_id)
    return activity.snapshot(rec.tenant_id, project_id, after)


@router.get("/{project_id}/activity/stream")
async def project_activity_stream(project_id: str, request: Request, rec: Sess, after: Optional[str] = None) -> StreamingResponse:
    await asyncio.to_thread(_call, _activity_root, rec.tenant_id, project_id)
    cursor = request.headers.get("last-event-id") or after
    if activity.stream_count(rec.tenant_id) >= activity.MAX_STREAMS_PER_TENANT:       # a real HTTP status (EventSource cannot show one)
        raise HTTPException(status_code=429, detail={"message": "too many open explorers; close one and try again", "reason_class": "stream_limit"})
    return StreamingResponse(_activity_stream(request, rec.tenant_id, project_id, cursor), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.put("/{project_id}/settings")
def settings(project_id: str, body: SettingsBody, rec: SessCsrf) -> dict:
    return _call(svc.set_settings, rec.tenant_id, project_id, classification=body.classification,
                 assistant_access=body.assistant_access, confirm=body.confirm)


@router.get("/{project_id}/changes")
def changes(project_id: str, rec: Sess, commit: Optional[str] = None) -> dict:
    return _call(svc.project_changes, rec.tenant_id, project_id, commit)


@router.get("/{project_id}/changes/file")
def change_file(project_id: str, path: str, rec: Sess, commit: Optional[str] = None) -> dict:
    return _call(svc.project_change_file, rec.tenant_id, project_id, path, commit)


@router.put("/{project_id}/title")
def rename(project_id: str, body: RenameBody, rec: SessCsrf) -> dict:
    return _call(svc.rename_project, rec.tenant_id, project_id, body.title)


@router.post("/{project_id}/move")
def move(project_id: str, body: MoveBody, rec: SessCsrf) -> dict:
    return _call(svc.move_project, rec.tenant_id, project_id, body.location)


@router.post("/{project_id}/relocate")
def relocate(project_id: str, body: RelocateBody, rec: SessCsrf) -> dict:
    """"Locate folder": re-point a missing project at the folder that holds its manifest (ADR-2321 Decision 11)."""
    return _call(svc.relocate_project, rec.tenant_id, project_id, body.location)


@router.post("/{project_id}/open")
def post_open(project_id: str, rec: SessCsrf) -> dict:
    return _call(svc.open_project, rec.tenant_id, project_id)


@router.delete("/{project_id}")
def delete_project(project_id: str, rec: SessCsrf) -> dict:
    """Removes the registry entry ONLY. The directory and every file in it stay."""
    return _call(svc.remove_from_registry, rec.tenant_id, project_id)
