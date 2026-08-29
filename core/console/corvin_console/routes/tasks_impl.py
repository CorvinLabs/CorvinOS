"""Task Engine M2 route handlers (ADR-0081, ADR-0101). M4 quota gates (ADR-0080)."""
from __future__ import annotations

from typing import AsyncIterator

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

from fastapi import HTTPException, WebSocket, WebSocketDisconnect, status as http_status
from pydantic import BaseModel

from corvin_console.task_queue import TaskQueue, TaskStatus, QuotaExceededError
from corvin_console.task_pubsub import get_pubsub

# M6: real abort via subprocess signal
try:
    from corvin_console.task_worker_pool import signal_abort as _signal_abort
    _ABORT_SIGNAL_AVAILABLE = True
except ImportError:
    _ABORT_SIGNAL_AVAILABLE = False
    _signal_abort = None


class TaskCreateRequest(BaseModel):
    chat_key: str | None = None
    instruction: str
    ttl_seconds: int = 3600
    # Optional routing info for Discord/Telegram/etc (Phase 2: routing-wiring)
    channel: str | None = None          # "discord", "telegram", etc.
    chat_id: str | int | None = None    # Channel/conversation ID
    sender: str | None = None            # User ID/identifier (for audit)


class TaskCreateResponse(BaseModel):
    ok: bool
    task_id: str


class TaskGetResponse(BaseModel):
    task_id: str
    chat_key: str
    status: str
    created_at: float
    started_at: float | None = None
    ended_at: float | None = None
    exit_code: int | None = None


async def create_task_handler(
    tenant_id: str,
    chat_key: str | None,
    instruction: str,
    ttl_seconds: int,
    channel: str | None = None,
    chat_id: str | int | None = None,
    sender: str | None = None,
) -> TaskCreateResponse:
    """Create a tenant-global task with optional messenger routing.

    Args:
        tenant_id: Tenant identifier.
        chat_key: Chat identifier (e.g., 'web:sid'). If None, uses current session.
        instruction: User instruction.
        ttl_seconds: Task timeout.
        channel: (Optional) Messenger channel ("discord", "telegram", etc.)
        chat_id: (Optional) Channel/conversation ID for notifications
        sender: (Optional) User ID/identifier (for audit)

    Returns:
        TaskCreateResponse with task_id.
    """
    if not chat_key:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "chat_key required")

    # Get task queue for tenant
    tenant_global = _forge_paths.tenant_global_dir(tenant_id)
    queue = TaskQueue(tenant_global)

    try:
        task_id = queue.enqueue(
            tenant_id=tenant_id,
            chat_key=chat_key,
            instruction=instruction,
            ttl_seconds=ttl_seconds,
            check_quota=True,
            # Phase 2: Pass routing info for completion_notify registration
            channel=channel,
            chat_id=chat_id,
            sender=sender,
        )
    except QuotaExceededError:
        raise HTTPException(
            http_status.HTTP_429_TOO_MANY_REQUESTS,
            "task quota exceeded",
        )

    return TaskCreateResponse(ok=True, task_id=task_id)


async def get_task_handler(tenant_id: str, task_id: str) -> dict:
    """Get task by ID.

    Args:
        tenant_id: Tenant identifier.
        task_id: Task identifier.

    Returns:
        Task metadata dict.

    Raises:
        HTTPException(404) if not found.
        HTTPException(403) if different tenant owns it.
    """
    tenant_global = _forge_paths.tenant_global_dir(tenant_id)
    queue = TaskQueue(tenant_global)

    task = queue.get_task(task_id, tenant_id=tenant_id)
    if task is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "task not found")

    # Verify tenant ownership
    if task.tenant_id != tenant_id:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "task not owned by this tenant")

    return {
        "task_id": task.task_id,
        "chat_key": task.chat_key,
        "status": task.status.value,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "ended_at": task.ended_at,
        "exit_code": task.exit_code,
    }


async def abort_task_handler(tenant_id: str, task_id: str) -> dict:
    """Cancel a task (idempotent).

    Args:
        tenant_id: Tenant identifier.
        task_id: Task identifier.

    Returns:
        {ok: True}

    Raises:
        HTTPException(404) if not found.
        HTTPException(403) if different tenant owns it.
        HTTPException(409) if not in cancellable state.
    """
    tenant_global = _forge_paths.tenant_global_dir(tenant_id)
    queue = TaskQueue(tenant_global)

    task = queue.get_task(task_id, tenant_id=tenant_id)
    if task is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "task not found")

    if task.tenant_id != tenant_id:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "task not owned by this tenant")

    if task.is_terminal:
        # Already terminal: idempotent success
        return {"ok": True}

    # Mark as CANCELLED in queue log first
    queue.update_status(task_id, TaskStatus.CANCELLED)

    # M6: Signal the running subprocess to terminate (best-effort)
    if _ABORT_SIGNAL_AVAILABLE and _signal_abort is not None:
        try:
            _signal_abort(task_id)
        except Exception:
            pass  # abort signal is best-effort; status is already CANCELLED

    return {"ok": True}


async def progress_handler(websocket, tenant_id: str) -> None:
    """WebSocket handler for task progress pub/sub.

    Accepts WebSocket connection and streams all task events for a tenant.
    """
    await websocket.accept()
    pubsub = get_pubsub()

    try:
        async for event in pubsub.subscribe(tenant_id):
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        await websocket.close(code=http_status.WS_1011_SERVER_ERROR)
