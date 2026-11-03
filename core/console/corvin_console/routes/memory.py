"""Memory panel — the caller's OWN tenant memory (ADR A-10).

Until 2026-10 this module served ``~/.claude/projects/<slug>/memory`` — the memory of
whoever runs Claude Code on the host — to EVERY console tenant, and let any session write
and delete in it (S-1a). No route here touches ``~/.claude`` or any other host store: every
read and write goes through :mod:`corvin_console.memory_store`, which is confined to
``<CORVIN_HOME>/tenants/<tenant>/`` of the session's tenant. Never sample data (ADR-0763).

Routes (owner-only; mutations need the CSRF token and are audited):

* ``GET    /memory``               index: real entries + per-source counts
* ``GET    /memory/search?q=``     recall FTS over the tenant's turns + note/file text
* ``GET    /memory/{name}``        one entry with its content
* ``POST   /memory``               add a note ``{title, content, type}``
* ``DELETE /memory/{name}``        delete a note, topic, harvest entry or recall turn
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field

from .. import audit
from .. import auth as session_auth
from .. import memory_store
from ..deps import require_csrf, require_session, require_session_csrf_on_mutation

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

_TARGET_KIND = "memory_entry"


def _require_owner(rec: session_auth.SessionRecord) -> session_auth.SessionRecord:
    """Memory is the owner's view of their tenant — fail closed for any other tier."""
    if getattr(rec, "tier", None) != "owner":
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="owner required")
    return rec


def _owner_read(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> session_auth.SessionRecord:
    return _require_owner(rec)


def _owner_mutation(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> session_auth.SessionRecord:
    return _require_owner(rec)


def _audit_ok(rec: session_auth.SessionRecord, action: str, target_id: str) -> None:
    try:
        audit.action_performed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action=action, target_kind=_TARGET_KIND, target_id=target_id,
        )
    except Exception:  # noqa: BLE001 — informational record, never blocks the action
        pass


def _audit_failed(rec: session_auth.SessionRecord, action: str, target_id: str, reason: str) -> None:
    try:
        audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action=action, target_kind=_TARGET_KIND, target_id=target_id, reason=reason,
        )
    except Exception:  # noqa: BLE001
        pass


class MemoryNoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=memory_store.MAX_TITLE_CHARS)
    content: str = Field(min_length=1, max_length=memory_store.MAX_CONTENT_CHARS)
    type: Literal["user", "feedback", "project", "reference"] = "project"


@router.get("/memory")
def memory_index(
    rec: Annotated[session_auth.SessionRecord, Depends(_owner_read)],
) -> dict[str, Any]:
    """The caller's tenant memory: notes, topic/harvest files, recent recall turns."""
    return memory_store.index(rec.tenant_id)


@router.get("/memory/search")
def memory_search(
    rec: Annotated[session_auth.SessionRecord, Depends(_owner_read)],
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return memory_store.search(rec.tenant_id, q, limit=limit)


@router.get("/memory/{name}")
def memory_entry(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(_owner_read)],
) -> dict[str, Any]:
    try:
        return memory_store.get_entry(rec.tenant_id, name)
    except memory_store.MemoryNotFound:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="no such memory entry") from None


@router.post("/memory")
def memory_add(
    body: MemoryNoteIn,
    rec: Annotated[session_auth.SessionRecord, Depends(_owner_mutation)],
) -> dict[str, Any]:
    try:
        entry = memory_store.add_note(
            rec.tenant_id, title=body.title, content=body.content, type=body.type,
            created_by=rec.token_fingerprint or "",
            session_fingerprint=rec.sid_fingerprint,
        )
    except memory_store.MemoryInvalid as exc:
        _audit_failed(rec, "memory.note_add", "-", "invalid")
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from None
    except memory_store.MemoryFull as exc:
        _audit_failed(rec, "memory.note_add", "-", "full")
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from None
    _audit_ok(rec, "memory.note_add", entry["name"])
    return entry


@router.delete("/memory/{name}")
def memory_delete(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(_owner_mutation)],
) -> dict[str, Any]:
    try:
        source = memory_store.delete_entry(rec.tenant_id, name)
    except memory_store.MemoryNotFound:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="no such memory entry") from None
    except memory_store.MemoryReadOnly:
        _audit_failed(rec, "memory.entry_delete", name[:80], "read_only")
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN,
                            detail="this memory entry is read-only") from None
    _audit_ok(rec, "memory.entry_delete", name)
    return {"deleted": name, "source": source}
