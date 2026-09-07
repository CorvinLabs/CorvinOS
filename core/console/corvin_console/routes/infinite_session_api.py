"""Infinite Session Dashboard API (Phase D) — ADR-0545.

Mounted under ``/v1/console`` (``app.py``), prefix ``/api/infinite-session``:

  GET  /tasks                              — tasks with snapshot history
  GET  /task/{task_id}/history             — full snapshot chain + drift
  GET  /task/{task_id}/context-diff        — diff between two snapshots
  POST /task/{task_id}/revert              — revert = NEW chained snapshot
  GET  /health                             — store / chain / rollback health

The ONLY persistence read or written here is :class:`EventStore` (ADR-0540);
there is no parallel "checkpoints" layout. A *checkpoint id* in this API IS a
snapshot id.

Security:
- every id (path, query, body) is validated against ``ID_PATTERN``
  (``^[A-Za-z0-9_.-]{1,128}$``, no ``..``) at the Pydantic/Query layer AND
  again — with ``resolve().is_relative_to`` — inside the tenant-bound store;
- the tenant is ``rec.tenant_id`` from the authenticated session, never a
  query parameter; the store refuses any other tenant;
- ``revert`` sits behind ``require_csrf`` and is audited through the core
  console audit helper (``console.action_performed`` / ``action_failed``,
  content-free: action, target, ``sid_fingerprint``, tenant). The snapshot
  write itself commits ``infinite_session.snapshot_created`` to the core
  chain audit-first. The free-text ``reason`` is NEVER persisted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from core.infinite_session.drift_detector import DriftDetector
from core.infinite_session.event_store import EventStore
from core.infinite_session.paths import ID_PATTERN
from core.infinite_session.rollback_manager import RollbackManager, state_hash
from core.infinite_session.snapshot_schema import Snapshot, SnapshotType

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/infinite-session", tags=["infinite-session"])

_TASK_ID = PathParam(..., pattern=ID_PATTERN, max_length=128)
_CHECKPOINT_Q = Query(..., pattern=ID_PATTERN, max_length=128)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─ Models ────────────────────────────────────────────────────────────────


class CheckpointRecord(BaseModel):
    """One snapshot of a task chain."""
    checkpoint_id: str
    phase_id: str
    snapshot_type: str
    timestamp: str
    seq: int
    content_hash: str
    prev_snapshot_hash: Optional[str] = None
    config_state: Dict[str, Any]
    drift_level: str
    drift_alert: Optional[str] = None


class TaskHistory(BaseModel):
    task_id: str
    tenant_id: str
    started_at: str
    last_updated: str
    checkpoint_count: int
    chain_valid: bool
    chain_error: Optional[str] = None
    checkpoints: List[CheckpointRecord]
    current_config: Dict[str, Any]
    total_drift: float


class ConfigDiff(BaseModel):
    from_checkpoint_id: str
    to_checkpoint_id: str
    timestamp: str
    additions: Dict[str, Any] = Field(default_factory=dict)
    removals: Dict[str, Any] = Field(default_factory=dict)
    modifications: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class RevertRequest(BaseModel):
    task_id: str = Field(..., pattern=ID_PATTERN, max_length=128)
    target_checkpoint_id: str = Field(..., pattern=ID_PATTERN, max_length=128)
    # Operator confirmation only — never persisted (no free text in audit/learning records).
    reason: str = Field(..., min_length=1, max_length=500)


class RevertResult(BaseModel):
    success: bool
    task_id: str
    reverted_to_checkpoint: str
    new_checkpoint_id: str = ""
    transaction_id: str = ""
    timestamp: str
    error: Optional[str] = None


class TaskSummary(BaseModel):
    task_id: str
    phase_id: str
    started_at: str
    last_updated: str
    checkpoint_count: int
    current_drift_level: str
    last_drift_alert: Optional[str] = None
    status: str  # "active" | "reverted"


class TaskListResponse(BaseModel):
    tasks: List[TaskSummary]
    total_count: int
    tenant_id: str
    timestamp: str


class HealthStatus(BaseModel):
    status: str  # healthy | degraded | unhealthy
    event_store: str
    rollback_manager: str
    drift_detector: str
    total_tasks: int
    total_checkpoints: int
    last_check: str
    errors: List[str] = Field(default_factory=list)


# ─ Per-request, tenant-bound stores ──────────────────────────────────────


def get_event_store(tenant_id: str) -> EventStore:
    return EventStore(tenant_id)


def get_rollback_manager(tenant_id: str) -> RollbackManager:
    return RollbackManager(tenant_id)


def get_drift_detector(tenant_id: str) -> DriftDetector:
    return DriftDetector(tenant_id)


def _load_chain(store: EventStore, tenant_id: str, task_id: str) -> List[Snapshot]:
    index, error = store.list_snapshots(tenant_id, task_id)
    if error:
        raise HTTPException(status_code=400, detail=error)
    snapshots: List[Snapshot] = []
    for meta in index:
        snapshot, read_error = store.read_snapshot(tenant_id, task_id, meta.snapshot_id)
        if read_error or snapshot is None:
            raise HTTPException(status_code=500, detail=f"Unreadable snapshot {meta.snapshot_id}")
        snapshots.append(snapshot)
    return snapshots


def _drift_records(
    detector: DriftDetector, snapshots: List[Snapshot]
) -> List[CheckpointRecord]:
    records: List[CheckpointRecord] = []
    history: List[tuple[str, Dict[str, Any]]] = []
    for seq, snapshot in enumerate(snapshots, start=1):
        history.append((snapshot.timestamp, snapshot.state_dict))
        assessment = detector.assess_states(history)
        records.append(CheckpointRecord(
            checkpoint_id=snapshot.snapshot_id,
            phase_id=snapshot.phase_id,
            snapshot_type=snapshot.snapshot_type.value,
            timestamp=snapshot.timestamp,
            seq=seq,
            content_hash=snapshot.content_hash,
            prev_snapshot_hash=snapshot.prev_snapshot_hash,
            config_state=snapshot.state_dict,
            drift_level=assessment.level.name,
            drift_alert=assessment.message if assessment.level.name != "NORMAL" else None,
        ))
    return records


# ─ Endpoints ─────────────────────────────────────────────────────────────


@router.get("/tasks", response_model=TaskListResponse, summary="List tasks with session history")
async def list_tasks(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> TaskListResponse:
    tenant_id = rec.tenant_id
    store = get_event_store(tenant_id)
    detector = get_drift_detector(tenant_id)

    summaries: List[TaskSummary] = []
    for task_id in store.list_tasks():
        index, error = store.list_snapshots(tenant_id, task_id)
        if error or not index:
            continue
        latest, read_error = store.read_snapshot(tenant_id, task_id, index[-1].snapshot_id)
        if read_error or latest is None:
            continue
        snapshots = _load_chain(store, tenant_id, task_id)
        assessment = detector.assess_states([(s.timestamp, s.state_dict) for s in snapshots])
        summaries.append(TaskSummary(
            task_id=task_id,
            phase_id=latest.phase_id,
            started_at=index[0].timestamp,
            last_updated=index[-1].timestamp,
            checkpoint_count=len(index),
            current_drift_level=assessment.level.name,
            last_drift_alert=assessment.message if assessment.level.name != "NORMAL" else None,
            status="reverted" if latest.snapshot_type == SnapshotType.ROLLBACK_RECOVERY else "active",
        ))

    return TaskListResponse(
        tasks=summaries[offset: offset + limit],
        total_count=len(summaries),
        tenant_id=tenant_id,
        timestamp=_now(),
    )


@router.get("/task/{task_id}/history", response_model=TaskHistory, summary="Get full task history")
async def get_task_history(
    task_id: Annotated[str, _TASK_ID],
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> TaskHistory:
    tenant_id = rec.tenant_id
    store = get_event_store(tenant_id)
    snapshots = _load_chain(store, tenant_id, task_id)
    if not snapshots:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    chain_valid, chain_error = store.verify_snapshot_chain(tenant_id, task_id)
    records = _drift_records(get_drift_detector(tenant_id), snapshots)
    return TaskHistory(
        task_id=task_id,
        tenant_id=tenant_id,
        started_at=snapshots[0].timestamp,
        last_updated=snapshots[-1].timestamp,
        checkpoint_count=len(snapshots),
        chain_valid=chain_valid,
        chain_error=chain_error or None,
        checkpoints=records,
        current_config=snapshots[-1].state_dict,
        total_drift=sum(1.0 for r in records if r.drift_level != "NORMAL"),
    )


@router.get("/task/{task_id}/context-diff", response_model=ConfigDiff, summary="Get config diff")
async def get_context_diff(
    task_id: Annotated[str, _TASK_ID],
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    from_checkpoint: Annotated[str, _CHECKPOINT_Q],
    to_checkpoint: Annotated[str, _CHECKPOINT_Q],
) -> ConfigDiff:
    tenant_id = rec.tenant_id
    store = get_event_store(tenant_id)

    from_snapshot, error = store.read_snapshot(tenant_id, task_id, from_checkpoint)
    if error or from_snapshot is None:
        raise HTTPException(status_code=404, detail=f"Checkpoint {from_checkpoint} not found")
    to_snapshot, error = store.read_snapshot(tenant_id, task_id, to_checkpoint)
    if error or to_snapshot is None:
        raise HTTPException(status_code=404, detail=f"Checkpoint {to_checkpoint} not found")

    from_state, to_state = from_snapshot.state_dict, to_snapshot.state_dict
    return ConfigDiff(
        from_checkpoint_id=from_checkpoint,
        to_checkpoint_id=to_checkpoint,
        timestamp=to_snapshot.timestamp,
        additions={k: v for k, v in to_state.items() if k not in from_state},
        removals={k: v for k, v in from_state.items() if k not in to_state},
        modifications={
            k: {"before": from_state[k], "after": to_state[k]}
            for k in from_state
            if k in to_state and from_state[k] != to_state[k]
        },
    )


@router.post("/task/{task_id}/revert", response_model=RevertResult, summary="Revert to checkpoint")
async def revert_to_checkpoint(
    task_id: Annotated[str, _TASK_ID],
    revert_req: RevertRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> RevertResult:
    """Revert = append a ``rollback_recovery`` snapshot carrying the target's
    state, chained onto the current head. Nothing is rewritten or deleted.
    The transaction is WAL-logged in :class:`RollbackManager` and audited."""
    tenant_id = rec.tenant_id
    if revert_req.task_id != task_id:
        raise HTTPException(status_code=400, detail="task_id in body does not match path")

    def _denied(reason: str, status: int) -> None:
        console_audit.action_failed(
            tenant_id=tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="infinite_session.revert", target_kind="task", target_id=task_id,
            reason=reason,
        )
        raise HTTPException(status_code=status, detail=reason)

    store = get_event_store(tenant_id)
    target, error = store.read_snapshot(tenant_id, task_id, revert_req.target_checkpoint_id)
    if error or target is None:
        _denied("target_checkpoint_not_found", 404)
    head, error = store.get_latest_snapshot(tenant_id, task_id)
    if error or head is None:
        _denied("task_has_no_snapshots", 404)
    if head.snapshot_id == target.snapshot_id:
        _denied("already_at_target", 409)
    chain_ok, chain_error = store.verify_snapshot_chain(tenant_id, task_id)
    if not chain_ok:
        _denied("chain_invalid", 409)

    config_path = f"task.{task_id}.state"
    rollback = get_rollback_manager(tenant_id)
    tx_id, error = rollback.begin_transaction(
        tenant_id=tenant_id, config_path=config_path,
        old_state=head.state_dict, new_state=target.state_dict, operation="revert",
    )
    if error:
        _denied("transaction_begin_failed", 500)

    try:
        recovery = Snapshot.create(
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id=target.phase_id,
            state_dict=target.state_dict,
            snapshot_type=SnapshotType.ROLLBACK_RECOVERY,
            prev_snapshot_hash=head.content_hash,
            base_commit=target.base_commit,
            worktree_path=target.worktree_path,
        )
    except ValueError as exc:
        _denied(f"snapshot_invalid:{type(exc).__name__}", 400)

    ok, error = store.write_snapshot(recovery)
    if not ok:
        logger.warning("infinite-session revert write refused for %s: %s", task_id, error)
        _denied("snapshot_write_refused", 500)

    committed, error = rollback.commit_transaction(
        transaction_id=tx_id, tenant_id=tenant_id, config_path=config_path,
        old_state=head.state_dict, new_state=target.state_dict, operation="revert",
    )
    if not committed:
        # The snapshot is on the chain (audited); the transaction log records the failure.
        logger.error("infinite-session revert commit failed for %s: %s", task_id, error)
        _denied("transaction_commit_failed", 500)

    console_audit.action_performed(
        tenant_id=tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="infinite_session.revert", target_kind="task", target_id=task_id,
    )
    logger.info(
        "infinite-session revert task=%s target=%s new=%s tx=%s old_hash=%s new_hash=%s",
        task_id, target.snapshot_id, recovery.snapshot_id, tx_id,
        state_hash(head.state_dict)[:12], state_hash(target.state_dict)[:12],
    )
    return RevertResult(
        success=True,
        task_id=task_id,
        reverted_to_checkpoint=target.snapshot_id,
        new_checkpoint_id=recovery.snapshot_id,
        transaction_id=tx_id,
        timestamp=recovery.timestamp,
    )


@router.get("/health", response_model=HealthStatus, summary="Get system health")
async def health_check(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> HealthStatus:
    tenant_id = rec.tenant_id
    errors: List[str] = []
    total_tasks = total_checkpoints = 0

    try:
        store = get_event_store(tenant_id)
        es_status = "healthy"
        for task_id in store.list_tasks():
            total_tasks += 1
            index, _ = store.list_snapshots(tenant_id, task_id)
            total_checkpoints += len(index)
            ok, chain_error = store.verify_snapshot_chain(tenant_id, task_id)
            if not ok:
                es_status = "degraded"
                errors.append(f"chain:{task_id}:{chain_error}")
    except Exception as exc:  # noqa: BLE001 — health must report, not raise
        es_status = "unhealthy"
        errors.append(f"event_store:{type(exc).__name__}")

    try:
        rollback = get_rollback_manager(tenant_id)
        ok, chain_error = rollback.verify_chain_integrity(tenant_id)
        rm_status = "healthy" if ok else "degraded"
        if not ok:
            errors.append(f"rollback_chain:{chain_error}")
    except Exception as exc:  # noqa: BLE001
        rm_status = "unhealthy"
        errors.append(f"rollback_manager:{type(exc).__name__}")

    try:
        get_drift_detector(tenant_id)
        dd_status = "healthy"
    except Exception as exc:  # noqa: BLE001
        dd_status = "unhealthy"
        errors.append(f"drift_detector:{type(exc).__name__}")

    statuses = {es_status, rm_status, dd_status}
    if "unhealthy" in statuses:
        overall = "unhealthy"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return HealthStatus(
        status=overall,
        event_store=es_status,
        rollback_manager=rm_status,
        drift_detector=dd_status,
        total_tasks=total_tasks,
        total_checkpoints=total_checkpoints,
        last_check=_now(),
        errors=errors,
    )
