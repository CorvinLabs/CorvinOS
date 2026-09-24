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

from core.task_tracking import service
from core.task_tracking.models import (
    DecisionBody,
    DependencyBody,
    ItemCreate,
    ItemPatch,
    RunLinkBody,
)

from .. import auth as session_auth
from ..deps import require_csrf, require_session

router = APIRouter(prefix="/task-tracking")

_ACTOR = "operator"
_EVIDENCE_PREFIX = "initiatives.json#"


def _fail(exc: Exception) -> None:
    if isinstance(exc, service.Conflict):
        raise HTTPException(status_code=409, detail={"message": str(exc), "current": exc.current})
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

@router.get("/items")
def get_items(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    include_deleted: bool = False,
) -> dict:
    try:
        body = service.list_items(rec.tenant_id, include_deleted=include_deleted)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        raise
    _attach_evidence(body["items"], _evidence_index(rec.tenant_id, time.time()))
    _attach_live_runs(rec.tenant_id, body["items"])
    # Offer the import only on a store that never held anything: once items
    # exist (even deleted ones) the import would skip them all.
    body["import_available"] = (not body["items"]) and not service.has_any_rows(rec.tenant_id) \
        and _import_pending(rec.tenant_id)
    return body


def _import_pending(tenant_id: str) -> bool:
    try:
        from .. import task_tracking_import as imp  # noqa: PLC0415

        return bool(imp.plan(tenant_id))
    except Exception:  # noqa: BLE001
        return False


@router.get("/summary")
def get_summary(rec: Annotated[session_auth.SessionRecord, Depends(require_session)]) -> dict:
    body = service.list_items(rec.tenant_id)
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


@router.post("/items", status_code=201)
def post_item(body: ItemCreate, rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> dict:
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


@router.post("/import")
def post_import(rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)]) -> JSONResponse:
    from .. import task_tracking_import as imp  # noqa: PLC0415

    try:
        res = imp.run(rec.tenant_id, actor=_ACTOR, sid_fingerprint=rec.sid_fingerprint)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        raise
    return JSONResponse(res)

