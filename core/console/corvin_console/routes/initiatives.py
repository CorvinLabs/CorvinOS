"""Initiatives board routes — live status of running and finished initiative tasks.

Endpoints (all under /v1/console):
  GET   /initiatives                                  → derived board (session)
  PATCH /initiatives/{iid}/tasks/{tid}                → set status/progress (CSRF, audited)
  PUT   /initiatives/{iid}/gates/{gid}                → set gate decision (CSRF, audited)
  POST  /initiatives/verify                           → start an evidence verification run in
                                                        the background (CSRF, audited; 202)
  PUT   /initiatives/{iid}/close                      → close run (completed/cancelled) or
                                                        reopen it (outcome=null) (CSRF, audited)

The tenant comes from the authenticated session only. Data model and
derivation rules: ``corvin_console/initiatives.py``.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from .. import initiatives as board_mod
from ..deps import require_csrf, require_session

router = APIRouter(prefix="/initiatives", tags=["initiatives"])


def _raise(exc: board_mod.InitiativeError) -> None:
    code = 404 if isinstance(exc, board_mod.NotFound) else 400
    raise HTTPException(status_code=code, detail=str(exc))


@router.get("")
async def get_board(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    try:
        return board_mod.board(rec.tenant_id)
    except board_mod.InitiativeError as exc:
        _raise(exc)
        raise  # unreachable


class TaskPatch(BaseModel):
    status: Literal["pending", "running", "done", "blocked"] | None = None
    progress: int | None = Field(default=None, ge=0, le=100)


@router.patch("/{iid}/tasks/{tid}")
async def patch_task(
    iid: str,
    tid: str,
    body: TaskPatch,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    try:
        result = board_mod.update_task(rec.tenant_id, iid, tid,
                                       status=body.status, progress=body.progress)
    except board_mod.InitiativeError as exc:
        _raise(exc)
        raise
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="initiative.task.update",
        target_kind="initiative_task",
        target_id=f"{iid}/{tid}",
    )
    return result


class GateBody(BaseModel):
    decision: Literal["pending", "go", "no_go"]


@router.put("/{iid}/gates/{gid}")
async def put_gate(
    iid: str,
    gid: str,
    body: GateBody,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    try:
        result = board_mod.set_gate_decision(rec.tenant_id, iid, gid, body.decision)
    except board_mod.InitiativeError as exc:
        _raise(exc)
        raise
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=f"initiative.gate.{body.decision}",
        target_kind="initiative_gate",
        target_id=f"{iid}/{gid}",
    )
    return result


class CloseBody(BaseModel):
    outcome: Literal["completed", "cancelled"] | None


@router.put("/{iid}/close")
async def put_close(
    iid: str,
    body: CloseBody,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    try:
        result = board_mod.close_run(rec.tenant_id, iid, body.outcome)
    except board_mod.InitiativeError as exc:
        _raise(exc)
        raise
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=f"initiative.close.{body.outcome}" if body.outcome else "initiative.reopen",
        target_kind="initiative",
        target_id=iid,
    )
    return result


@router.post("/verify", status_code=202)
async def post_verify(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    """Re-check every task's evidence now instead of waiting for the timer.

    Runs detached (tests can take minutes); the board shows
    ``verification.running`` until it finishes and picks the results up on
    the next poll.
    """
    from .. import initiatives_verify  # noqa: PLC0415

    started = initiatives_verify.start_background(rec.tenant_id)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="initiative.verify.start" if started else "initiative.verify.already_running",
        target_kind="initiatives",
        target_id="evidence",
    )
    return {"started": started, "running": True}
