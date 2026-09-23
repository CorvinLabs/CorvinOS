"""Task Audit Trail API — Read-only access to task state transitions (ADR-0XXX).

Endpoints:
---------
  GET    /v1/console/tasks/{task_id}/audit-trail           → get task audit trail
  GET    /v1/console/tasks/{task_id}/audit-trail/status    → get chain integrity status
  POST   /v1/console/tasks/{task_id}/audit-trail/verify    → verify chain integrity
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from .. import auth as session_auth
from ..deps import require_session
from core.console.corvin_core.task_audit_trail import TaskAuditTrail

router = APIRouter()


@router.get("/tasks/{task_id}/audit-trail")
async def get_task_audit_trail(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Get audit trail for a task (read-only, tenant-scoped).

    Returns: {
        ok: bool,
        task_id: str,
        events: [
            {
                event_id: str,
                task_id: str,
                event_type: str,  # task.created | task.started | task.completed | task.failed | task.cancelled
                old_state: str | None,
                new_state: str,
                executor_id: str,
                reason: str,
                timestamp: str,
                hash: str,
                prev_hash: str,
            },
            ...
        ],
        chain_status: {
            height: int,
            last_hash: str,
            integrity_verified: bool,
        }
    }
    """
    try:
        audit = TaskAuditTrail(task_id=task_id, tenant_id=rec.tenant_id)
        events = audit.read_events()
        status = audit.get_chain_status()

        return {
            "ok": True,
            "task_id": task_id,
            "events": events,
            "chain_status": status,
        }
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read audit trail: {str(e)}",
        ) from e


@router.get("/tasks/{task_id}/audit-trail/status")
async def get_audit_trail_status(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Get audit chain status (height, hash, integrity).

    Returns: {
        ok: bool,
        task_id: str,
        height: int,           # number of events
        last_hash: str,        # hash of last event
        integrity_verified: bool,
        last_verified: str,    # ISO8601 timestamp
    }
    """
    try:
        audit = TaskAuditTrail(task_id=task_id, tenant_id=rec.tenant_id)
        status = audit.get_chain_status()

        return {
            "ok": True,
            **status,
        }
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get audit status: {str(e)}",
        ) from e


@router.post("/tasks/{task_id}/audit-trail/verify")
async def verify_audit_chain(
    task_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Verify audit chain integrity (on-demand verification).

    Returns: {
        ok: bool,
        task_id: str,
        chain_valid: bool,
        verification_timestamp: str,  # ISO8601
    }
    """
    try:
        audit = TaskAuditTrail(task_id=task_id, tenant_id=rec.tenant_id)
        chain_valid = audit.verify_chain()

        return {
            "ok": True,
            "task_id": task_id,
            "chain_valid": chain_valid,
            "verification_timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Verification failed: {str(e)}",
        ) from e
