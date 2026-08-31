"""User-Defined Learning Objectives (ULO) REST API — ADR-0163 M4.

Thin REST surface over the shared ``ulo.py`` registry.

Endpoints
---------
  GET    /v1/console/ulo/objectives
      ?channel=<chan>&chat=<chat_key>
      → list all objectives for the chat
  POST   /v1/console/ulo/objectives
      body: {channel, chat_key, text, priority?, scope?, check_trigger?}
      → add objective
  PUT    /v1/console/ulo/objectives/{id}
      body: {action: "pause"|"resume", channel, chat_key}
           |{action: "update", channel, chat_key, text}
      → pause/resume/update
  DELETE /v1/console/ulo/objectives/{id}
      ?channel=<chan>&chat=<chat>
      → delete (channel/chat as query params — body on DELETE is non-standard)

All mutations require CSRF.  Read endpoints require session only.
Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

_SHARED = Path(__file__).resolve().parents[4] / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import ulo as _ulo  # noqa: E402

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────

class ObjectiveOut(BaseModel):
    id:                      str
    text:                    str
    priority:                str
    scope:                   str
    active:                  bool
    created_at:              float
    updated_at:              float
    compliance_window:       int
    compliance_rate:         float | None
    reinforcement_threshold: float
    turns_checked:           int
    consecutive_failures:    int
    check_trigger:           str


class AddBody(BaseModel):
    channel:       str
    chat_key:      str
    text:          str = Field(..., max_length=200)
    priority:      Literal["low", "medium", "high"] = "medium"
    scope:         Literal["session", "chat", "all"] = "chat"
    check_trigger: Literal["always", "code", "review", "commit"] = "always"


class ActionBody(BaseModel):
    action:   Literal["pause", "resume", "update"]
    channel:  str
    chat_key: str
    text:     str | None = None       # required when action="update"


# ── Helpers ───────────────────────────────────────────────────────────────

def _to_out(obj) -> dict:
    return obj.to_dict()


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/ulo/objectives")
def list_objectives(
    channel: str,
    chat: str,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    objs = _ulo.load(channel, chat, tenant_id=_rec.tenant_id)
    return {
        "objectives":   [_to_out(o) for o in objs],
        "count":        len(objs),
        "active_count": sum(1 for o in objs if o.active),
    }


@router.post("/ulo/objectives", status_code=http_status.HTTP_201_CREATED)
def add_objective(
    body: AddBody,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    try:
        obj = _ulo.add(
            body.channel,
            body.chat_key,
            body.text,
            priority=body.priority,
            scope=body.scope,
            check_trigger=body.check_trigger,
            tenant_id=_rec.tenant_id,
        )
    except ValueError as e:
        console_audit.action_failed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="ulo.add",
            target_kind="objective",
            target_id="",
            reason="validation_error",
        )
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    console_audit.action_performed(
        tenant_id=_rec.tenant_id,
        sid_fingerprint=_rec.sid_fingerprint,
        action="ulo.add",
        target_kind="objective",
        target_id=obj.id,
    )
    return {"objective": _to_out(obj)}


@router.put("/ulo/objectives/{ulo_id}")
def update_objective(
    ulo_id: str,
    body: ActionBody,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    if body.action in ("pause", "resume"):
        active = body.action == "resume"
        found = _ulo.set_active(body.channel, body.chat_key, ulo_id, active, _rec.tenant_id)
        if not found:
            console_audit.action_failed(
                tenant_id=_rec.tenant_id,
                sid_fingerprint=_rec.sid_fingerprint,
                action=f"ulo.{body.action}",
                target_kind="objective",
                target_id=ulo_id,
                reason="not_found",
            )
            raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                                detail=f"{ulo_id} not found")
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action=f"ulo.{body.action}",
            target_kind="objective",
            target_id=ulo_id,
        )
        return {"id": ulo_id, "active": active}

    if body.action == "update":
        if not body.text:
            console_audit.action_failed(
                tenant_id=_rec.tenant_id,
                sid_fingerprint=_rec.sid_fingerprint,
                action="ulo.update",
                target_kind="objective",
                target_id=ulo_id,
                reason="text_required",
            )
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                                detail="text required for update")
        try:
            found = _ulo.update_text(body.channel, body.chat_key, ulo_id, body.text, _rec.tenant_id)
        except ValueError as e:
            console_audit.action_failed(
                tenant_id=_rec.tenant_id,
                sid_fingerprint=_rec.sid_fingerprint,
                action="ulo.update",
                target_kind="objective",
                target_id=ulo_id,
                reason="validation_error",
            )
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        if not found:
            console_audit.action_failed(
                tenant_id=_rec.tenant_id,
                sid_fingerprint=_rec.sid_fingerprint,
                action="ulo.update",
                target_kind="objective",
                target_id=ulo_id,
                reason="not_found",
            )
            raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                                detail=f"{ulo_id} not found")
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="ulo.update",
            target_kind="objective",
            target_id=ulo_id,
        )
        return {"id": ulo_id, "text": body.text}

    console_audit.action_failed(
        tenant_id=_rec.tenant_id,
        sid_fingerprint=_rec.sid_fingerprint,
        action="ulo.update",
        target_kind="objective",
        target_id=ulo_id,
        reason="unknown_action",
    )
    raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                        detail=f"unknown action {body.action!r}")


@router.delete("/ulo/objectives/{ulo_id}", status_code=http_status.HTTP_200_OK)
def delete_objective(
    ulo_id: str,
    channel: Annotated[str, Query()],
    chat: Annotated[str, Query()],
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    found = _ulo.delete(channel, chat, ulo_id, _rec.tenant_id)
    if not found:
        console_audit.action_failed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="ulo.delete",
            target_kind="objective",
            target_id=ulo_id,
            reason="not_found",
        )
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            detail=f"{ulo_id} not found")
    console_audit.action_performed(
        tenant_id=_rec.tenant_id,
        sid_fingerprint=_rec.sid_fingerprint,
        action="ulo.delete",
        target_kind="objective",
        target_id=ulo_id,
    )
    return {"id": ulo_id, "deleted": True}
