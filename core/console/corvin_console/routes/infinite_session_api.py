"""Infinite Session Dashboard API (Phase D) — ADR-0545.

Exposes REST endpoints for the infinite session dashboard:
  - GET /v1/console/infinite-session/tasks — List all tasks with session history
  - GET /v1/console/infinite-session/task/<task_id>/history — Full task checkpoint history
  - GET /v1/console/infinite-session/task/<task_id>/context-diff — Config diff viewer
  - POST /v1/console/infinite-session/task/<task_id>/revert — Rollback to checkpoint
  - GET /v1/console/infinite-session/health — System health

Tenant isolation enforced (users only see their own data).
All operations audit-logged and fail-closed.

Compliance:
  - GDPR Art. 30/32: Audit trail, chain integrity, tenant scoping
  - EU AI Act: Transparency (user can inspect all session decisions)
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field

try:
    from core.infinite_session.event_store import EventStore
    from core.infinite_session.rollback_manager import RollbackManager
    from core.infinite_session.snapshot_schema import Snapshot, scrub_pii_from_text
    from core.infinite_session.drift_detector import DriftDetector, DriftAlert
    from core.infinite_session.ema_smoother import EMASmoother
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.infinite_session.event_store import EventStore
    from core.infinite_session.rollback_manager import RollbackManager
    from core.infinite_session.snapshot_schema import Snapshot, scrub_pii_from_text
    from core.infinite_session.drift_detector import DriftDetector, DriftAlert
    from core.infinite_session.ema_smoother import EMASmoother

from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/infinite-session", tags=["infinite-session"])


# ─ Pydantic models for request/response ─────────────────────────────────────


class CheckpointRecord(BaseModel):
    """Single checkpoint in task history."""
    checkpoint_id: str
    phase_id: str
    timestamp: str
    config_state: Dict[str, Any]
    drift_level: str
    drift_alert: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskHistory(BaseModel):
    """Full history of a task across checkpoints."""
    task_id: str
    tenant_id: str
    started_at: str
    last_updated: str
    checkpoint_count: int
    checkpoints: List[CheckpointRecord]
    current_config: Dict[str, Any]
    total_drift: float


class ConfigDiff(BaseModel):
    """Diff between two config states."""
    from_checkpoint_id: str
    to_checkpoint_id: str
    timestamp: str
    additions: Dict[str, Any] = Field(default_factory=dict)
    removals: Dict[str, Any] = Field(default_factory=dict)
    modifications: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class RevertRequest(BaseModel):
    """Request to revert to a previous checkpoint."""
    task_id: str
    target_checkpoint_id: str
    reason: str = Field(..., min_length=1, max_length=500)


class RevertResult(BaseModel):
    """Result of revert operation."""
    success: bool
    task_id: str
    reverted_to_checkpoint: str
    timestamp: str
    affected_tasks: int
    error: Optional[str] = None


class TaskSummary(BaseModel):
    """Summary of a task for list view."""
    task_id: str
    phase_id: str
    started_at: str
    checkpoint_count: int
    current_drift_level: str
    last_drift_alert: Optional[str] = None
    status: str  # active, completed, reverted


class TaskListResponse(BaseModel):
    """List of all tasks with session history."""
    tasks: List[TaskSummary]
    total_count: int
    tenant_id: str
    timestamp: str


class HealthStatus(BaseModel):
    """System health check response."""
    status: str  # healthy, degraded, unhealthy
    event_store: str
    rollback_manager: str
    drift_detector: str
    total_tasks: int
    total_checkpoints: int
    last_check: str
    errors: List[str] = Field(default_factory=list)


# ─ Global managers (per-tenant) ─────────────────────────────────────────────


def _tenant_home(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` — honours CORVIN_HOME."""
    from forge.tenants import tenant_home  # type: ignore[import-not-found]
    return Path(tenant_home(tenant_id))


_event_stores: Dict[str, EventStore] = {}
_rollback_managers: Dict[str, RollbackManager] = {}
_drift_detectors: Dict[str, DriftDetector] = {}


def get_event_store(tenant_id: str) -> EventStore:
    """Get or initialize event store for tenant."""
    if tenant_id not in _event_stores:
        _event_stores[tenant_id] = EventStore(tenant_home=str(_tenant_home(tenant_id)))
    return _event_stores[tenant_id]


def get_rollback_manager(tenant_id: str) -> RollbackManager:
    """Get or initialize rollback manager for tenant."""
    if tenant_id not in _rollback_managers:
        _rollback_managers[tenant_id] = RollbackManager(
            corvin_home=str(_tenant_home(tenant_id).parent.parent)
        )
    return _rollback_managers[tenant_id]


def get_drift_detector(tenant_id: str) -> DriftDetector:
    """Get or initialize drift detector for tenant."""
    if tenant_id not in _drift_detectors:
        _drift_detectors[tenant_id] = DriftDetector(
            tenant_home=_tenant_home(tenant_id),
            smoother=EMASmoother(alpha=0.3),
        )
    return _drift_detectors[tenant_id]


# ─ API Endpoints ────────────────────────────────────────────────────────────


@router.get("/tasks", response_model=TaskListResponse, summary="List tasks with session history")
async def list_tasks(
    rec: session_auth.SessionRecord = Depends(require_session),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> TaskListResponse:
    """List all tasks for the tenant with session history.

    Returns:
      - Task ID, phase, started time
      - Checkpoint count (session depth)
      - Current drift level + last alert
      - Status (active/completed/reverted)

    Tenant isolation enforced (returns only user's tenant data).
    """
    tenant_id = rec.tenant_id
    event_store = get_event_store(tenant_id)

    try:
        # Load tasks from event store
        tasks_dir = event_store.root_dir / "tasks"
        if not tasks_dir.exists():
            return TaskListResponse(
                tasks=[],
                total_count=0,
                tenant_id=tenant_id,
                timestamp=datetime.utcnow().isoformat(),
            )

        # Enumerate all task directories
        all_tasks = []
        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue

            task_id = task_dir.name
            checkpoints_dir = task_dir / "checkpoints"
            if not checkpoints_dir.exists():
                continue

            checkpoint_count = len(list(checkpoints_dir.glob("*.json")))
            if checkpoint_count == 0:
                continue

            # Load metadata from latest checkpoint
            latest_checkpoint = max(checkpoints_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
            with open(latest_checkpoint, "r") as f:
                cp_data = json.load(f)

            # Detect drift level
            drift_detector = get_drift_detector(tenant_id)
            drift_level = drift_detector.detect_drift(cp_data.get("config_state", {}))

            all_tasks.append(TaskSummary(
                task_id=task_id,
                phase_id=cp_data.get("phase_id", "unknown"),
                started_at=cp_data.get("created_at", datetime.utcnow().isoformat()),
                checkpoint_count=checkpoint_count,
                current_drift_level=drift_level.level.name,
                last_drift_alert=drift_level.alert.message if drift_level.alert else None,
                status="active",  # TODO: load from task state
            ))

        # Apply pagination
        total_count = len(all_tasks)
        tasks_page = all_tasks[offset : offset + limit]

        return TaskListResponse(
            tasks=tasks_page,
            total_count=total_count,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat(),
        )

    except Exception as e:
        logger.error(f"Error listing tasks for tenant {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list tasks: {str(e)}")


@router.get("/task/{task_id}/history", response_model=TaskHistory, summary="Get full task history")
async def get_task_history(
    task_id: str,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> TaskHistory:
    """Get complete checkpoint history for a task.

    Returns full chain of checkpoints, current config state, and aggregated drift.

    Tenant isolation enforced.
    """
    tenant_id = rec.tenant_id
    event_store = get_event_store(tenant_id)

    try:
        task_dir = event_store.root_dir / "tasks" / task_id
        if not task_dir.exists():
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        checkpoints_dir = task_dir / "checkpoints"
        if not checkpoints_dir.exists():
            raise HTTPException(status_code=404, detail=f"No checkpoints found for task {task_id}")

        # Load all checkpoints
        checkpoints = []
        drift_detector = get_drift_detector(tenant_id)
        total_drift = 0.0

        for cp_file in sorted(checkpoints_dir.glob("*.json")):
            with open(cp_file, "r") as f:
                cp_data = json.load(f)

            drift_level = drift_detector.detect_drift(cp_data.get("config_state", {}))
            total_drift += drift_level.drift_score

            checkpoints.append(CheckpointRecord(
                checkpoint_id=cp_file.stem,
                phase_id=cp_data.get("phase_id", "unknown"),
                timestamp=cp_data.get("created_at", datetime.utcnow().isoformat()),
                config_state=cp_data.get("config_state", {}),
                drift_level=drift_level.level.name,
                drift_alert=drift_level.alert.message if drift_level.alert else None,
                metadata=cp_data.get("metadata", {}),
            ))

        # Get current config (latest checkpoint)
        if checkpoints:
            current_config = checkpoints[-1].config_state
            started_at = checkpoints[0].timestamp
            last_updated = checkpoints[-1].timestamp
        else:
            current_config = {}
            started_at = datetime.utcnow().isoformat()
            last_updated = started_at

        return TaskHistory(
            task_id=task_id,
            tenant_id=tenant_id,
            started_at=started_at,
            last_updated=last_updated,
            checkpoint_count=len(checkpoints),
            checkpoints=checkpoints,
            current_config=current_config,
            total_drift=total_drift,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading task history for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load task history: {str(e)}")


@router.get("/task/{task_id}/context-diff", response_model=ConfigDiff, summary="Get config diff")
async def get_context_diff(
    task_id: str,
    from_checkpoint: str = Query(..., min_length=1),
    to_checkpoint: str = Query(..., min_length=1),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> ConfigDiff:
    """Get diff between two checkpoints.

    Shows additions, removals, and modifications to config state.
    """
    tenant_id = rec.tenant_id
    event_store = get_event_store(tenant_id)

    try:
        task_dir = event_store.root_dir / "tasks" / task_id
        cp_dir = task_dir / "checkpoints"

        from_file = cp_dir / f"{from_checkpoint}.json"
        to_file = cp_dir / f"{to_checkpoint}.json"

        if not from_file.exists():
            raise HTTPException(status_code=404, detail=f"Checkpoint {from_checkpoint} not found")
        if not to_file.exists():
            raise HTTPException(status_code=404, detail=f"Checkpoint {to_checkpoint} not found")

        with open(from_file, "r") as f:
            from_state = json.load(f).get("config_state", {})
        with open(to_file, "r") as f:
            to_state = json.load(f).get("config_state", {})
            to_timestamp = json.load(f).get("created_at", datetime.utcnow().isoformat())

        # Compute diff
        additions = {k: v for k, v in to_state.items() if k not in from_state}
        removals = {k: v for k, v in from_state.items() if k not in to_state}
        modifications = {}

        for k in from_state:
            if k in to_state and from_state[k] != to_state[k]:
                modifications[k] = {"before": from_state[k], "after": to_state[k]}

        return ConfigDiff(
            from_checkpoint_id=from_checkpoint,
            to_checkpoint_id=to_checkpoint,
            timestamp=to_timestamp,
            additions=additions,
            removals=removals,
            modifications=modifications,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing diff for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to compute diff: {str(e)}")


@router.post("/task/{task_id}/revert", response_model=RevertResult, summary="Revert to checkpoint")
async def revert_to_checkpoint(
    task_id: str,
    revert_req: RevertRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> RevertResult:
    """Revert task to a previous checkpoint.

    Atomically rolls back the task's config state to match the target checkpoint.
    All operations are audit-logged.

    Fail-closed: any error → entire revert rejected.
    """
    tenant_id = rec.tenant_id
    rollback_mgr = get_rollback_manager(tenant_id)
    event_store = get_event_store(tenant_id)

    try:
        # Load target checkpoint
        task_dir = event_store.root_dir / "tasks" / task_id
        cp_file = task_dir / "checkpoints" / f"{revert_req.target_checkpoint_id}.json"

        if not cp_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Checkpoint {revert_req.target_checkpoint_id} not found",
            )

        with open(cp_file, "r") as f:
            target_state = json.load(f).get("config_state", {})

        # Get current state
        latest_cp = max((task_dir / "checkpoints").glob("*.json"), key=lambda p: p.stat().st_mtime)
        with open(latest_cp, "r") as f:
            current_state = json.load(f).get("config_state", {})

        # Begin transaction
        tx_id, error = rollback_mgr.begin_transaction(
            tenant_id=tenant_id,
            config_path=f"task.{task_id}.config",
            old_state=current_state,
            new_state=target_state,
        )

        if error:
            return RevertResult(
                success=False,
                task_id=task_id,
                reverted_to_checkpoint="",
                timestamp=datetime.utcnow().isoformat(),
                affected_tasks=0,
                error=error,
            )

        # Commit transaction (atomic)
        def audit_callback(**kwargs):
            logger.info(f"Revert audit: {kwargs}")
            return True

        success, error = rollback_mgr.commit_transaction(
            transaction_id=tx_id,
            tenant_id=tenant_id,
            config_path=f"task.{task_id}.config",
            old_state=current_state,
            new_state=target_state,
            audit_callback=audit_callback,
        )

        if not success:
            return RevertResult(
                success=False,
                task_id=task_id,
                reverted_to_checkpoint="",
                timestamp=datetime.utcnow().isoformat(),
                affected_tasks=0,
                error=error or "Revert failed",
            )

        # Emit audit event (scrub PII from reason field)
        scrubbed_reason = scrub_pii_from_text(revert_req.reason)
        logger.info(
            f"Task {task_id} reverted to checkpoint {revert_req.target_checkpoint_id} "
            f"by {rec.user_id}: {scrubbed_reason}"
        )

        return RevertResult(
            success=True,
            task_id=task_id,
            reverted_to_checkpoint=revert_req.target_checkpoint_id,
            timestamp=datetime.utcnow().isoformat(),
            affected_tasks=1,  # TODO: compute based on dependency graph
            error=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reverting task {task_id}: {e}")
        return RevertResult(
            success=False,
            task_id=task_id,
            reverted_to_checkpoint="",
            timestamp=datetime.utcnow().isoformat(),
            affected_tasks=0,
            error=str(e),
        )


@router.get("/health", response_model=HealthStatus, summary="Get system health")
async def health_check(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> HealthStatus:
    """Health check for infinite session infrastructure.

    Verifies:
      - Event store is reachable
      - Rollback manager is functional
      - Drift detector is operational
      - Total task/checkpoint counts
    """
    tenant_id = rec.tenant_id
    errors = []

    try:
        event_store = get_event_store(tenant_id)
        es_status = "healthy" if event_store.root_dir.exists() else "degraded"
    except Exception as e:
        es_status = "unhealthy"
        errors.append(f"Event store error: {str(e)}")

    try:
        rollback_mgr = get_rollback_manager(tenant_id)
        rm_status = "healthy" if rollback_mgr.log_dir.exists() else "degraded"
    except Exception as e:
        rm_status = "unhealthy"
        errors.append(f"Rollback manager error: {str(e)}")

    try:
        drift_detector = get_drift_detector(tenant_id)
        dd_status = "healthy"
    except Exception as e:
        dd_status = "unhealthy"
        errors.append(f"Drift detector error: {str(e)}")

    # Count tasks and checkpoints
    total_tasks = 0
    total_checkpoints = 0
    try:
        tasks_dir = event_store.root_dir / "tasks"
        if tasks_dir.exists():
            for task_dir in tasks_dir.iterdir():
                if task_dir.is_dir():
                    total_tasks += 1
                    cp_dir = task_dir / "checkpoints"
                    if cp_dir.exists():
                        total_checkpoints += len(list(cp_dir.glob("*.json")))
    except Exception as e:
        errors.append(f"Error counting tasks: {str(e)}")

    overall_status = "healthy"
    if es_status != "healthy" or rm_status != "healthy" or dd_status != "healthy":
        overall_status = "degraded" if errors else "unhealthy"

    return HealthStatus(
        status=overall_status,
        event_store=es_status,
        rollback_manager=rm_status,
        drift_detector=dd_status,
        total_tasks=total_tasks,
        total_checkpoints=total_checkpoints,
        last_check=datetime.utcnow().isoformat(),
        errors=errors,
    )
