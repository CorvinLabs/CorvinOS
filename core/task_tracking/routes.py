"""REST API Routes for Task Tracking Console (Phase C k=4).

Endpoints:
- GET /v1/console/tasks — list all tasks
- GET /v1/console/tasks/{task_id} — task detail + approval state
- POST /v1/console/tasks/{task_id}/approve — approve task
- POST /v1/console/tasks/{task_id}/reject — reject task
- GET /v1/console/tasks/{task_id}/audit-trail — audit events
- GET /v1/console/tasks/{task_id}/versions — version history
- POST /v1/console/tasks/{task_id}/rollback — rollback to version

Integrates with:
- core/task_tracking/service.py (SSOT)
- core/task_tracking/governance.py (validators)
- core/task_tracking/snapshots.py (rollback)
"""
from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import service, governance, snapshots

router = APIRouter(prefix="/v1/console/tasks", tags=["task-tracking"])


# ── Request/Response Models ──────────────────────────────────────────────────


class TaskResponse(BaseModel):
    """Task response model for API."""
    id: str
    title: str
    description: Optional[str]
    kind: str
    status: str
    approval_state: str
    owner: Optional[str]
    assignee: Optional[str]
    priority: str
    version: int
    created_at: str
    updated_at: str
    completed_at: Optional[str]


class ApprovalDecisionRequest(BaseModel):
    """Request to approve/reject a task."""
    actor: str
    rationale: str
    version: int  # Optimistic concurrency token


class RollbackRequest(BaseModel):
    """Request to rollback task to a previous version."""
    target_version: int
    actor: str


# ── List Tasks ─────────────────────────────────────────────────────────────


@router.get("/")
def list_tasks(
    tenant_id: str = Query("_default"),
    include_deleted: bool = Query(False),
) -> dict[str, Any]:
    """
    List all tasks for a tenant.

    Query params:
        tenant_id: tenant to list tasks for (default: _default)
        include_deleted: include soft-deleted tasks

    Returns:
        {tasks: [...], summary: {...}}
    """
    try:
        rows = service.list_items(tenant_id, include_deleted=include_deleted)
        import time
        now_ts = time.time()
        with_rollups = service.with_rollups(rows, now_ts)
        summary = service.summary(with_rollups, now_ts)

        return {
            "tasks": with_rollups,
            "summary": summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list tasks: {e}")


# ── Task Detail ──────────────────────────────────────────────────────────────


@router.get("/{task_id}")
def get_task(
    task_id: str,
    tenant_id: str = Query("_default"),
) -> dict[str, Any]:
    """
    Get task detail with approval state and audit trail.

    Returns:
        {task: {...}, approval_state, audit_trail: [...]}
    """
    try:
        task = service.detail(tenant_id, task_id)
        return {
            "task": task,
            "approval_state": task.get("approval_state"),
        }
    except service.NotFound:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Approval Decision ────────────────────────────────────────────────────────


@router.post("/{task_id}/approve")
async def approve_task(
    task_id: str,
    request: ApprovalDecisionRequest,
    tenant_id: str = Query("_default"),
) -> dict[str, Any]:
    """
    Approve a task (pending → approved).

    Request body:
        {
            "actor": "reviewer_id",
            "rationale": "LGTM, looks good",
            "version": 42
        }

    Steps:
    1. Load task
    2. Request approval_state must be "pending"
    3. Run validators (fail-closed, 5s timeout per validator)
    4. Emit approval decision to audit chain (EU AI Act Art. 50, GDPR Art. 30)
    5. Call service.decide(decision="approved")
    6. Return updated task

    Returns:
        {task: {...}, approval_state, decision_result: {...}}

    Compliance:
        - Timeout guards: validators fail-closed on 5s timeout
        - Audit logging: approval decision recorded immutably in chain
        - Validator orchestration: concurrent timeouts don't corrupt state
    """
    try:
        # Step 1: Validate request
        task = service.detail(tenant_id, task_id)

        if task["approval_state"] != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve: approval_state is '{task['approval_state']}', not 'pending'",
            )

        # Step 2: Run validators (with timeout guards)
        registry = governance.get_registry()
        policy = await registry.check_approval_allowed(task_id, request.actor, "approve")

        if not policy.approved:
            # Blocked by validator(s) — emit rejection to audit trail
            from core.task_tracking.audit import emit_approval_decision_event  # noqa: PLC0415
            try:
                await emit_approval_decision_event(
                    task_id=task_id,
                    actor=request.actor,
                    decision="rejected",
                    tenant_id=tenant_id,
                    rationale=f"Validator blocked: {policy.reason}",
                    validator_ids_applied=policy.validators_run,
                    validation_results={
                        v_id: result.passed
                        for v_id, result in policy.validation_results.items()
                    },
                )
            except Exception as e:  # noqa: BLE001
                # Audit failure is critical (fail-closed) but don't block rejection response
                import logging
                logging.exception(f"Failed to audit rejection for {task_id}: {e}")

            return {
                "task": task,
                "decision_result": {
                    "approved": False,
                    "reason": policy.reason,
                    "blocked_by": policy.blocked_by,
                    "validators_run": policy.validators_run,
                },
            }

        # Step 3: All validators passed — emit approval to audit chain
        from core.task_tracking.audit import emit_approval_decision_event  # noqa: PLC0415
        try:
            await emit_approval_decision_event(
                task_id=task_id,
                actor=request.actor,
                decision="approve",
                tenant_id=tenant_id,
                rationale=request.rationale,
                validator_ids_applied=policy.validators_run,
                validation_results={
                    v_id: result.passed
                    for v_id, result in policy.validation_results.items()
                },
            )
        except OSError as e:
            # Audit-first: if chain write fails, approval cannot proceed (fail-closed)
            raise HTTPException(
                status_code=503,
                detail=f"Approval decision cannot be recorded (audit unavailable): {e}",
            ) from e

        # Step 4: Execute approval
        approved_task = service.decide(
            tenant_id, task_id, "approved", version=request.version, actor=request.actor
        )

        return {
            "task": approved_task,
            "decision_result": {
                "approved": True,
                "reason": "Task approved",
                "validators_run": policy.validators_run,
            },
        }

    except service.NotFound:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    except service.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except service.TaskTrackingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise  # Pass through HTTP exceptions
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/reject")
async def reject_task(
    task_id: str,
    request: ApprovalDecisionRequest,
    tenant_id: str = Query("_default"),
) -> dict[str, Any]:
    """
    Reject a task (pending → rejected).

    Same as approve but with decision="rejected".
    """
    try:
        task = service.detail(tenant_id, task_id)

        if task["approval_state"] != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject: approval_state is '{task['approval_state']}', not 'pending'",
            )

        # Execute rejection (no validators for rejection)
        rejected_task = service.decide(
            tenant_id, task_id, "rejected", version=request.version, actor=request.actor
        )

        return {
            "task": rejected_task,
            "decision_result": {
                "approved": False,
                "reason": request.rationale,
            },
        }

    except service.NotFound:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    except service.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except service.TaskTrackingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Audit Trail ──────────────────────────────────────────────────────────────


@router.get("/{task_id}/audit-trail")
def get_audit_trail(
    task_id: str,
    tenant_id: str = Query("_default"),
    limit: int = Query(100),
) -> dict[str, Any]:
    """
    Get audit trail for a task (approval decisions + all events).

    Returns:
        {task_id, events: [...], count: int}
    """
    try:
        from . import store  # noqa: PLC0415

        with store.connect(tenant_id) as conn:
            events = conn.execute(
                "SELECT * FROM events WHERE tenant_id=? AND item_id=? ORDER BY ts DESC LIMIT ?",
                (tenant_id, task_id, limit),
            ).fetchall()

            return {
                "task_id": task_id,
                "events": [dict(row) for row in events] if events else [],
                "count": len(events) if events else 0,
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Version History & Rollback ───────────────────────────────────────────────


@router.get("/{task_id}/versions")
async def list_versions(
    task_id: str,
    tenant_id: str = Query("_default"),
    limit: int = Query(50),
) -> dict[str, Any]:
    """
    List version snapshots for a task (for rollback).

    Returns:
        {task_id, versions: [...]}
    """
    try:
        versions = await snapshots.list_versions(tenant_id, task_id, limit=limit)

        return {
            "task_id": task_id,
            "versions": versions,
            "count": len(versions),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/rollback")
async def rollback_task(
    task_id: str,
    request: RollbackRequest,
    tenant_id: str = Query("_default"),
) -> dict[str, Any]:
    """
    Rollback task to a previous version.

    Request body:
        {
            "target_version": 5,
            "actor": "admin_id"
        }

    Returns:
        {rollback_result: {...}, task: {...}}
    """
    try:
        # Execute rollback
        result = await snapshots.rollback_to_version(
            tenant_id, task_id, request.target_version, actor=request.actor
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.reason)

        # Fetch updated task
        task = service.detail(tenant_id, task_id)

        return {
            "rollback_result": {
                "success": result.success,
                "from_version": result.from_version,
                "to_version": result.to_version,
                "reason": result.reason,
            },
            "task": task,
        }

    except service.NotFound:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
