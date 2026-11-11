"""Task-Tracking SSOT console surface — ``/v1/console/task-tracking/*`` (ADR-2056 §5).

Tenant = ``rec.tenant_id`` from the authenticated session, never from the
request. Reads need a session; every write needs CSRF and is audited by the
service itself (``task_item.*`` in the core chain, audit-first — a chain that
does not commit answers 503 and nothing is written).

Evidence for items imported from ``initiatives.json`` is read from that file's
verification results by ``external_ref`` on every request — derived, never
stored. Linked runs are resolved against ``task_sources`` (cached aggregate).

Handlers are sync on purpose: SQLite + the chain append run in the threadpool,
never on the event loop.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.task_tracking import service
from core.task_tracking.models import (
    DecisionBody,
    DependencyBody,
    ItemCreate,
    ItemPatch,
    RunLinkBody,
    Status,
)

from .. import auth as session_auth
from ..deps import require_csrf, require_session

router = APIRouter(prefix="/task-tracking")

_ACTOR = "operator"
_EVIDENCE_PREFIX = "initiatives.json#"


def _fail(exc: Exception) -> None:
    if isinstance(exc, service.Conflict):
        raise HTTPException(status_code=409, detail={"message": str(exc), "current": exc.current})
    if isinstance(exc, service.KbOwned):
        raise HTTPException(status_code=409, detail={"message": str(exc), "kb_owned": True})
    from .. import kb_projection as _kbp  # noqa: PLC0415
    if isinstance(exc, _kbp.KbTransitionRefused):
        raise HTTPException(status_code=409, detail={"message": str(exc), "kb_refused": True})
    if isinstance(exc, service.NotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.TaskTrackingError):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, service.AuditUnavailable):
        raise HTTPException(status_code=503, detail="audit chain unavailable — nothing was written")
    if isinstance(exc, sqlite3.OperationalError):  # "database is locked" after the busy timeout
        raise HTTPException(status_code=503, detail="task store busy — nothing was written, retry")
    if isinstance(exc, ValueError):  # pydantic validation inside the import, invalid tenant id
        raise HTTPException(status_code=400, detail=str(exc)[:300])
    raise exc


# ── Evidence (derived from initiatives.json verification, by external_ref) ───

def _evidence_index(tenant_id: str, now: float) -> dict[str, dict[str, Any]]:
    try:
        from .. import initiatives as board_mod  # noqa: PLC0415

        raw, _ = board_mod._load_raw(tenant_id)
    except Exception:  # noqa: BLE001 — no file / unreadable file = no evidence, never a failed page
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ini in raw.get("initiatives") or []:
        iid = str(ini.get("id") or "")
        for t in ini.get("tasks") or []:
            try:
                v = board_mod._derive_verification(t, now)
            except Exception:  # noqa: BLE001
                v = None
            if v:
                out[f"{_EVIDENCE_PREFIX}{iid}/task/{t.get('id')}"] = v
        for n, c in enumerate(ini.get("preconditions") or []):
            try:
                v = board_mod._derive_verification(c, now) if isinstance(c, dict) else None
            except Exception:  # noqa: BLE001
                v = None
            if v:
                out[f"{_EVIDENCE_PREFIX}{iid}/precondition/{n}"] = v
    return out


def _attach_evidence(items: list[dict[str, Any]], index: dict[str, dict[str, Any]]) -> None:
    for it in items:
        v = index.get(it.get("external_ref") or "")
        it["evidence"] = v
        it["claim_conflict"] = bool(
            v and it["status"] == "complete" and v["state"] in ("failing", "partial"))


# ── Live runs (derived per read from the run stores, never stored) ───────────

_LIVE = ("running", "paused", "queued", "scheduled")


def _attach_live_runs(tenant_id: str, items: list[dict[str, Any]]) -> None:
    """``live_runs`` (linked runs still active), ``running_runs`` (of those, running
    now) and up to three ``live_run_titles`` per item. Titles come from
    ``task_sources`` records, which never carry third-party message text."""
    for it in items:
        it["live_runs"], it["running_runs"], it["live_run_titles"] = 0, 0, []
    try:
        links = service.run_links(tenant_id)
        if not links:
            return
        from .. import task_sources  # noqa: PLC0415

        recs = task_sources.collect(tenant_id)["records"]
    except Exception:  # noqa: BLE001 — no live overlay is a degraded page, never a failed one
        return
    live = {(r["type"], r["id"]): r for r in recs if r["status"] in _LIVE}
    for it in items:
        for key in links.get(it["id"], ()):
            r = live.get(key)
            if r is None:
                continue
            it["live_runs"] += 1
            it["running_runs"] += r["status"] == "running"
            if len(it["live_run_titles"]) < 3:
                it["live_run_titles"].append(f"{r['type_label']}: {r['title']}"[:140])


# ── Reads ────────────────────────────────────────────────────────────────────

def _project_of(item: dict[str, Any]) -> Any:
    """The project a card belongs to - from its KB reference, which only the projector writes; never from a label a person can
    type (REVIEW-0082 N30: a manual card labelled ``prj-alpha`` vanished from every view, or showed on alpha's board)."""
    from core.task_tracking import projection  # noqa: PLC0415

    return projection.project_of_card(item)


@router.get("/items")
def get_items(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    include_deleted: bool = False,
    scope: str | None = Query(default=None, max_length=40,
                              description="bound = the tenant's own board only; project:<id> = one project's KB"),
) -> dict:
    """ADR-2316: every item carries ``project`` (its project id, or null), so a client can show a
    qualified id (``<project>/T-0001``: KB ids are local). ``scope`` narrows the list; a project
    that is not registered for THIS tenant is 404 (never a hint that another tenant has it)."""
    want: Any = ...
    if scope is not None:
        if scope == "bound":
            want = None
        elif scope.startswith("project:"):
            from .. import kb_projection  # noqa: PLC0415

            pid = scope.split(":", 1)[1]
            if pid == "corvin":                       # the built-in Corvin project IS the tenant's own board (CONCEPT-0129)
                pid = None
            elif pid not in {p for p, _ in kb_projection.project_kbs(rec.tenant_id, only=pid)}:
                raise HTTPException(status_code=404, detail="no such project")
            want = pid
        else:
            raise HTTPException(status_code=422, detail="scope must be 'bound' or 'project:<id>'")
    try:
        body = service.list_items(rec.tenant_id, include_deleted=include_deleted)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        raise
    for it in body["items"]:
        it["project"] = _project_of(it)
    _carried_only(rec.tenant_id, body)
    if want is not ...:
        body["items"] = [it for it in body["items"] if it["project"] == want]
        body["scope"] = scope
        # the counters describe the list they come with: a scoped list gets a scoped summary
        body["summary"] = service.summary(body["items"], service.parse_ts(body["server_time"]) or time.time())
    _attach_evidence(body["items"], _evidence_index(rec.tenant_id, time.time()))
    _attach_live_runs(rec.tenant_id, body["items"])
    # Offer the import only on a store that never held anything: once items
    # exist (even deleted ones) the import would skip them all.
    body["import_available"] = False   # the initiatives.json import is retired (ADR-2205)
    return body


def _carried_only(tenant_id: str, body: dict) -> None:
    """A project's cards stay in the store when the projector stops carrying it (removed, in the Trash, knowledge base off, a
    project.yaml that names another project). The board and its counters show only the projects the projector carries NOW, so
    the "all" view never shows what every scoped view refuses (REVIEW-0081 N27)."""
    from .. import kb_projection as _kp  # noqa: PLC0415
    carried = set(_kp.project_status(tenant_id))
    proj = [(it, it.get("project", _project_of(it))) for it in body["items"]]
    if any(p is not None and p not in carried for _, p in proj):
        body["items"] = [it for it, p in proj if p is None or p in carried]
        body["summary"] = service.summary(body["items"], service.parse_ts(body["server_time"]) or time.time())


@router.get("/summary")
def get_summary(rec: Annotated[session_auth.SessionRecord, Depends(require_session)]) -> dict:
    body = service.list_items(rec.tenant_id)
    _carried_only(rec.tenant_id, body)
    return {"server_time": body["server_time"], "summary": body["summary"]}


def _resolve_runs(tenant_id: str, runs: list[dict[str, Any]]) -> None:
    if not runs:
        return
    try:
        from .. import task_sources  # noqa: PLC0415

        recs = task_sources.collect(tenant_id)["records"]
    except Exception:  # noqa: BLE001
        recs = []
    by_key = {(r["type"], r["id"]): r for r in recs}
    for run in runs:
        r = by_key.get((run["run_type"], run["run_ref"]))
        run["found"] = r is not None
        run["title"] = r["title"] if r else None
        run["status"] = r["status"] if r else None
        run["type_label"] = r["type_label"] if r else run["run_type"]
        run["started_at"] = (r.get("started_at") or r.get("created_at")) if r else None
        run["ended_at"] = r.get("ended_at") if r else None


@router.get("/items/{item_id}")
def get_item(item_id: str, rec: Annotated[session_auth.SessionRecord, Depends(require_session)]) -> dict:
    try:
        body = service.detail(rec.tenant_id, item_id)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        raise
    _attach_evidence([body["item"]], _evidence_index(rec.tenant_id, time.time()))
    _resolve_runs(rec.tenant_id, body["runs"])
    return body


# ── Writes ───────────────────────────────────────────────────────────────────

def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        raise


def _kb_bound(tenant_id: str) -> bool:
    from .. import kb_projection  # noqa: PLC0415

    return kb_projection.kb_repo(tenant_id) is not None


def _board_project(tenant_id: str, scope: str | None) -> str | None:
    """The project a board request names (``?scope=project:<id>``), checked like ``get_items`` does: 404 for a project the board
    does not carry. ``None`` for the tenant's own board (no scope, ``bound``, ``project:corvin``)."""
    if scope is None or scope == "bound":
        return None
    if not scope.startswith("project:"):
        raise HTTPException(status_code=422, detail="scope must be 'bound' or 'project:<id>'")
    pid = scope.split(":", 1)[1]
    if pid == "corvin":                                     # the built-in Corvin project IS the tenant's own board
        return None
    from .. import kb_projection  # noqa: PLC0415

    if pid not in {p for p, _ in kb_projection.project_kbs(tenant_id, only=pid)}:
        raise HTTPException(status_code=404, detail="no such project")
    return pid


def _keep_local_fields(tenant_id: str, item: dict, body: ItemCreate, rec: session_auth.SessionRecord) -> dict:
    """A knowledge-base item is created by ``kb new``, which knows title, parent and text only. Priority, deadline and assignee
    are board fields of the card (editable on a KB card too): they are written onto the projected card right away, so what the
    dialog asked for is what the card shows (REVIEW-0084 N35)."""
    local = {k: getattr(body, k) for k in ("priority", "deadline", "assignee") if getattr(body, k, None) not in (None, "")}
    if not local or local == {"priority": item.get("priority")}:
        return item
    from core.task_tracking.models import ItemPatch  # noqa: PLC0415

    return _call(service.update, tenant_id, item["id"], ItemPatch(version=int(item.get("version") or 1), **local),
                 actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint)


@router.post("/items", status_code=201)
def post_item(body: ItemCreate, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
              scope: str | None = Query(default=None, max_length=40,
                                        description="the board the item is made on: project:<id> writes into that project's KB")) -> dict:
    """Create an item. While a knowledge base is bound to the tenant (ADR-2205) an initiative,
    epic or task is created IN the KB (``kb new``) and returned as its projection — the board
    has one writer. A KB parent makes a KB child too. Other kinds stay local board items.
    On a project's board (``?scope=project:<id>``) every item goes into THAT project's knowledge base, so only its kinds
    exist there - an item made on a project board used to land in the tenant's own knowledge base (REVIEW-0083 N32)."""
    project = _board_project(rec.tenant_id, scope)
    if project is not None:
        from .. import kb_projection  # noqa: PLC0415

        if body.kind not in ("initiative", "epic", "task"):
            raise HTTPException(status_code=409, detail={
                "message": "a project's board holds its knowledge base's items only: an initiative, an epic or a task",
                "kb_refused": True})
        if body.status != "open":
            raise HTTPException(status_code=409, detail={
                "message": "a knowledge-base item starts open — move it on the board (kb transition)", "kb_refused": True})
        made = _call(kb_projection.create_item, rec.tenant_id, kind=body.kind, title=body.title, parent_id=body.parent_id,
                     description=body.description or "", actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint, scope=project)
        return _keep_local_fields(rec.tenant_id, made, body, rec)
    if body.kind in ("initiative", "epic", "task") and _kb_bound(rec.tenant_id):
        from .. import kb_projection  # noqa: PLC0415

        parent_is_kb = False
        if body.parent_id:
            from core.task_tracking import store  # noqa: PLC0415

            with store.connect(rec.tenant_id) as conn:
                parent = _call(service._fetch, conn, rec.tenant_id, body.parent_id)
            parent_is_kb = str(parent.get("external_ref") or "").startswith(service.KB_REF_PREFIX)
        if body.kind == "initiative" or parent_is_kb:
            if body.status != "open":
                raise HTTPException(status_code=409, detail={
                    "message": "a knowledge-base item starts open — move it on the board (kb transition)",
                    "kb_refused": True})
            made = _call(kb_projection.create_item, rec.tenant_id, kind=body.kind, title=body.title,
                         parent_id=body.parent_id, description=body.description or "",
                         actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint)
            return _keep_local_fields(rec.tenant_id, made, body, rec)
    return _call(service.create, rec.tenant_id, body, actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint)


@router.patch("/items/{item_id}")
def patch_item(item_id: str, body: ItemPatch,
               rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    return _call(service.update, rec.tenant_id, item_id, body, actor=_ACTOR,
                 sid_fingerprint=rec.sid_fingerprint)


@router.post("/items/{item_id}/delete")
def post_delete(item_id: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    return _call(service.delete, rec.tenant_id, item_id, actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint)


@router.post("/items/{item_id}/restore")
def post_restore(item_id: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    return _call(service.restore, rec.tenant_id, item_id, actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint)


@router.post("/items/{item_id}/decision")
def post_decision(item_id: str, body: DecisionBody,
                  rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    return _call(service.decide, rec.tenant_id, item_id, body.decision, body.version, actor=_ACTOR,
                 sid_fingerprint=rec.sid_fingerprint)


@router.post("/items/{item_id}/dependencies", status_code=201)
def post_dependency(item_id: str, body: DependencyBody,
                    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    return _call(service.add_dependency, rec.tenant_id, item_id, body, actor=_ACTOR,
                 sid_fingerprint=rec.sid_fingerprint)


@router.delete("/items/{item_id}/dependencies/{depends_on_id}")
def delete_dependency(item_id: str, depends_on_id: str,
                      rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    return _call(service.remove_dependency, rec.tenant_id, item_id, depends_on_id, actor=_ACTOR,
                 sid_fingerprint=rec.sid_fingerprint)


def _linkable_types() -> set[str]:
    from .. import task_sources  # noqa: PLC0415

    return set(task_sources.TYPE_LABELS) - {"initiative"}


@router.post("/items/{item_id}/runs", status_code=201)
def post_run(item_id: str, body: RunLinkBody,
             rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    if body.run_type not in _linkable_types():
        raise HTTPException(status_code=400, detail=f"unknown run type {body.run_type!r}")
    return _call(service.link_run, rec.tenant_id, item_id, body, actor=_ACTOR,
                 sid_fingerprint=rec.sid_fingerprint)


@router.delete("/items/{item_id}/runs")
def delete_run(item_id: str, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
               run_type: str = Query(..., max_length=32),
               run_ref: str = Query(..., max_length=200)) -> dict:
    return _call(service.unlink_run, rec.tenant_id, item_id, run_type, run_ref, actor=_ACTOR,
                 sid_fingerprint=rec.sid_fingerprint)


# ── Knowledge-base projection (ADR-2205) ─────────────────────────────────────

class KbTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: Status
    reason: str = Field(default="", max_length=500)
    dod: str = Field(default="", max_length=1000)


@router.post("/items/{item_id}/kb-transition")
def post_kb_transition(item_id: str, body: KbTransitionBody,
                       rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    """A board move of a KB item: runs the KB's state machine, then re-projects."""
    from .. import kb_projection  # noqa: PLC0415

    return _call(kb_projection.transition, rec.tenant_id, item_id, body.to, reason=body.reason,
                 dod=body.dod, actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint)


@router.get("/kb/status")
def get_kb_status(rec: Annotated[session_auth.SessionRecord, Depends(require_session)]) -> dict:
    from .. import kb_projection  # noqa: PLC0415

    kb_projection.ensure_started(rec.tenant_id)   # a tenant created after boot gets its projector here
    st = dict(kb_projection.status(rec.tenant_id))
    st.pop("payload", None)
    st.pop("fingerprint", None)
    st["enabled"] = kb_projection.kb_repo(rec.tenant_id) is not None
    st["projects"] = kb_projection.project_status(rec.tenant_id)   # ADR-2316: one entry per project KB
    return st


@router.post("/kb/sync")
def post_kb_sync(rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
    from .. import kb_projection  # noqa: PLC0415

    # every tenant syncs ITS OWN binding (kb_repo(tenant)); without one the projector answers
    # "off" with the reason (hint: enable_plugin), never another tenant's KB
    try:
        st = dict(kb_projection.sync(rec.tenant_id, force=True))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        raise
    st.pop("payload", None)
    st.pop("fingerprint", None)
    return st


@router.post("/import")
def post_import(rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> JSONResponse:
    """Retired 2026-10-03 (ADR-2205): initiatives.json was imported into the knowledge base
    (Corvin-Knowledge migration/2026-10-03-initiatives-import.py); a second import would put
    a second writer back on the board."""
    return JSONResponse(status_code=410, content={
        "detail": "initiatives.json import is retired — work items live in the knowledge base "
                  "(Corvin-Knowledge: scripts/kb.py new task …)"})

