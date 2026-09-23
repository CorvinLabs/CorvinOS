"""Task Engine M2 routes — tenant-global task management (ADR-0081).

Endpoints
---------
  POST   /v1/console/tasks                      → create task (tenant-global)
  GET    /v1/console/tasks/{task_id}            → get task (cross-session)
  POST   /v1/console/tasks/{task_id}/abort      → cancel task
  WS     /v1/console/tasks/progress             → pub/sub task progress
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from .. import chat_runtime
from ..deps import require_csrf, require_session
from .tasks_impl import (
    TaskGetResponse,
    create_task_handler,
    get_task_handler,
    abort_task_handler,
    progress_handler,
)


router = APIRouter()


class TaskCreateRequest(BaseModel):
    """Request to create a task."""
    chat_key: str | None = None
    instruction: str = Field(..., max_length=32_000)
    ttl_seconds: int = Field(default=3600, ge=1, le=86_400)


class TaskCreateResponse(BaseModel):
    """Response from task creation."""
    ok: bool
    task_id: str


@router.post("/tasks")
async def create_task(
    body: TaskCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> TaskCreateResponse:
    """Create a tenant-global task.

    If chat_key is omitted, infers from session context.
    Returns immediately with task_id (execution happens in background).
    """
    result = await create_task_handler(
        tenant_id=rec.tenant_id,
        chat_key=body.chat_key,
        instruction=body.instruction,
        ttl_seconds=body.ttl_seconds,
    )
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="task.create",
        target_kind="task",
        target_id=result.task_id,
    )
    return result


@router.get("/tasks")
async def list_tasks(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    ids: str = "",
) -> dict:
    """Batch-fetch tasks by id (tenant-scoped). Used by the frontend's
    IndexedDB task-recovery reconciliation (`task-recovery.ts`).

    ``ids`` is a comma-separated list. Each id is resolved through the same
    tenant-ownership check as the single-task route; ids that are missing or
    owned by another tenant are silently skipped (so recovery is best-effort
    and never leaks another tenant's task). Returns ``{tasks: [...]}``.
    """
    wanted = [i.strip() for i in ids.split(",") if i.strip()][:200]
    tasks: list[dict] = []
    for task_id in wanted:
        try:
            tasks.append(await get_task_handler(tenant_id=rec.tenant_id, task_id=task_id))
        except HTTPException:
            continue  # not found / not owned → skip
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Get task by ID (cross-session, tenant-scoped).

    Authorization: caller's tenant must own the task.
    Returns: {task: TaskQueueEntry, status: str, ...}
    """
    return await get_task_handler(tenant_id=rec.tenant_id, task_id=task_id)


@router.post("/tasks/{task_id}/abort")
async def abort_task(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    """Cancel a task (idempotent).

    Returns: {ok: True}
    """
    result = await abort_task_handler(tenant_id=rec.tenant_id, task_id=task_id)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="task.abort",
        target_kind="task",
        target_id=task_id,
    )
    return result


@router.websocket("/tasks/progress")
async def task_progress_ws(
    websocket: WebSocket,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> None:
    """WebSocket endpoint for task progress pub/sub.

    Streams: {task_id: str, event: str, ...}
    """
    await progress_handler(websocket=websocket, tenant_id=rec.tenant_id)
