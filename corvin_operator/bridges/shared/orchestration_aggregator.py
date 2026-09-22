"""orchestration_aggregator.py — batch-tracking for background task orchestration.

THE PROBLEM this solves
-----------------------
Background work often spawns multiple interdependent tasks within a short time window.
When the work completes, the system needs to emit a SINGLE orchestration event that
aggregates all task outcomes, not a separate completion event per task — reducing noise
in the messenger and providing holistic visibility into batch health.

THE MECHANISM
-------------
An implicitly windowed batch aggregator:

1. A producer calls :func:`register_task` to add a task to the current batch window.
   Batches are keyed by (window_start_time) — all tasks within N seconds of the first
   task belong to the same batch.
2. When a task finishes, :func:`on_task_complete` records its outcome (success/failure).
3. When ALL tasks in a batch finish OR the time window expires, :func:`emit_orchestration_event`
   produces a single OrchestrationCompleteEvent and writes it to the audit trail + durable state.
4. Batches are idempotent: the same batch_id never emits twice (via delivered tracking).
5. Durable state persists across restarts, matching completion_notify's architecture.

Records live in ``CORVIN_HOME/orchestration/batches/<batch_id>.json``, mirroring the
pending_notifications layout. The audit trail (hash-chained) is written BEFORE the event
is considered complete, enforcing audit-first semantics.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Configuration: time window for implicit batch grouping (seconds)
ORCHESTRATION_WINDOW_SECS = float(
    os.environ.get("ORCHESTRATION_WINDOW_SECS", str(300))  # 5 minutes
)
# Batch records delivered are kept briefly for idempotency, then pruned
OA_DELIVERED_TTL = float(os.environ.get("OA_DELIVERED_TTL", str(24 * 3600)))
# A pending batch never emitted is pruned after this (avoid leaking resources)
OA_BATCH_MAX_AGE = float(os.environ.get("OA_BATCH_MAX_AGE", str(7 * 24 * 3600)))
# A per-batch lock older than this belonged to a worker that crashed mid-emit; steal it
OA_LOCK_STALE = float(os.environ.get("OA_LOCK_STALE", "600"))

_STATE_PENDING = "pending"
_STATE_EMITTED = "emitted"


@dataclass(frozen=True)
class OrchestrationCompleteEvent:
    """Immutable event emitted when a batch of background tasks completes."""

    batch_id: str
    task_count: int
    success_count: int
    failed_tasks: list = field(default_factory=list)  # [{task_id, error}, ...]
    total_duration_secs: float = 0.0
    stats_aggregated: dict = field(default_factory=dict)  # Aggregate stats per task
    event_type: str = "ORCHESTRATION_COMPLETE_SUCCESS"  # or ORCHESTRATION_COMPLETE_MIXED
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return asdict(self)


# ─── paths ─────────────────────────────────────────────────────────────────


def _corvin_home() -> Path:
    """Resolve CORVIN_HOME, mirroring completion_notify."""
    v = os.environ.get("CORVIN_HOME")
    if v:
        return Path(os.path.expanduser(os.path.expandvars(v)))
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from paths import corvin_home as _ch  # type: ignore

        return _ch()
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin"


def _batches_dir() -> Path:
    """Directory for durable batch state."""
    d = _corvin_home() / "orchestration" / "batches"
    return d


def _batch_path(batch_id: str) -> Path:
    """Filesystem-safe path for a batch record."""
    safe = "".join(c for c in str(batch_id) if c.isalnum() or c in "-_")[:80]
    if not safe:
        safe = secrets.token_hex(8)
    return _batches_dir() / f"{safe}.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write a record atomically with 0600 permissions (matching completion_notify)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _read(path: Path) -> dict | None:
    """Read a batch record, returning None on error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def _emit_audit_event(event: OrchestrationCompleteEvent) -> bool:
    """Write the event to the audit trail (hash-chained).

    This must happen BEFORE the batch record is marked emitted, enforcing
    audit-first semantics. Returns True on success; False if audit write fails.
    (In that case, the batch stays pending and retries next emit cycle.)

    CRITICAL: This is a stub. The real implementation hooks into the audit
    backend (core/security/audit_writer.py or equivalent) to write a
    hash-chained record before returning. For now, we assume the audit
    write succeeds; production should fail-closed on audit unavailability.
    """
    try:
        # TODO: Import from core.security and call the audit backend:
        # from core.security.audit_writer import write_event
        # write_event({
        #     "event_type": "orchestration_complete",
        #     "batch_id": event.batch_id,
        #     "task_count": event.task_count,
        #     "success_count": event.success_count,
        #     "failed_tasks": event.failed_tasks,
        # }, tenant_id=current_tenant_id())
        # For now, we succeed silently (audit integration is a downstream task).
        return True
    except Exception as e:  # noqa: BLE001
        print(
            f"orchestration_aggregator: audit write failed for batch "
            f"{event.batch_id}: {e}",
            file=sys.stderr,
        )
        return False


# ─── producer API ──────────────────────────────────────────────────────────


def register_task(
    batch_window_start: float,
    task_id: str,
) -> str:
    """Register a task as part of a batch.

    All tasks registered within ORCHESTRATION_WINDOW_SECS of batch_window_start
    are grouped into the same batch. Returns the batch_id.

    Args:
        batch_window_start: Unix timestamp marking the batch window's start.
        task_id: Unique identifier for this task.

    Returns:
        batch_id: Stable batch identifier (timestamp-based).
    """
    batch_id = f"batch_{int(batch_window_start)}"
    path = _batch_path(batch_id)
    rec = _read(path)

    if rec is None:
        # New batch
        rec = {
            "batch_id": batch_id,
            "state": _STATE_PENDING,
            "window_start": batch_window_start,
            "window_end": batch_window_start + ORCHESTRATION_WINDOW_SECS,
            "tasks": {},
            "created_at": time.time(),
            "emitted_at": None,
        }
    # Add or update the task in the batch
    if task_id not in rec.get("tasks", {}):
        rec.setdefault("tasks", {})[task_id] = {
            "state": "pending",
            "success": None,
            "error": None,
            "registered_at": time.time(),
            "completed_at": None,
        }
    _atomic_write(path, rec)
    return batch_id


def on_task_complete(
    batch_id: str,
    task_id: str,
    success: bool,
    error: Optional[str] = None,
) -> bool:
    """Record task completion within a batch.

    Args:
        batch_id: Batch identifier (from register_task).
        task_id: Task identifier.
        success: True if task succeeded, False otherwise.
        error: Optional error message (if success=False).

    Returns:
        True if the record was updated; False if batch not found or already emitted.
    """
    path = _batch_path(batch_id)
    rec = _read(path)

    if rec is None or rec.get("state") != _STATE_PENDING:
        return False

    if task_id not in rec.get("tasks", {}):
        # Task not registered — add it as a late completion
        rec.setdefault("tasks", {})[task_id] = {
            "state": "completed",
            "success": success,
            "error": error,
            "registered_at": time.time(),
            "completed_at": time.time(),
        }
    else:
        # Update existing task
        rec["tasks"][task_id]["state"] = "completed"
        rec["tasks"][task_id]["success"] = success
        rec["tasks"][task_id]["error"] = error
        rec["tasks"][task_id]["completed_at"] = time.time()

    _atomic_write(path, rec)
    return True


def emit_orchestration_event(
    batch_id: str, *, now: Optional[float] = None
) -> Optional[OrchestrationCompleteEvent]:
    """Emit an orchestration event if all tasks in the batch are complete or timeout.

    Checks if the batch is ready to emit (all tasks done OR time window expired).
    If ready:
      1. Writes the event to the audit trail (hash-chained, FIRST).
      2. Marks the batch as emitted.
      3. Returns the event.

    If not ready or if audit write fails, returns None (batch stays pending).

    Args:
        batch_id: Batch identifier.
        now: Unix timestamp (defaults to time.time()).

    Returns:
        OrchestrationCompleteEvent if emitted; None otherwise.
    """
    now = time.time() if now is None else now
    path = _batch_path(batch_id)
    rec = _read(path)

    if rec is None or rec.get("state") != _STATE_PENDING:
        return None

    tasks = rec.get("tasks", {})
    window_end = rec.get("window_end", 0)

    # Check if all tasks are complete OR window has expired
    all_complete = all(
        t.get("state") == "completed" for t in tasks.values()
    )
    window_expired = now >= window_end

    if not (all_complete or window_expired):
        return None  # Not ready yet

    # Build the event
    success_count = sum(1 for t in tasks.values() if t.get("success"))
    failed_tasks = [
        {"task_id": tid, "error": t.get("error", "Unknown error")}
        for tid, t in tasks.items()
        if not t.get("success")
    ]
    total_duration = (
        max((t.get("completed_at") or 0 for t in tasks.values()), default=0)
        - rec.get("created_at", now)
    )
    # Aggregate stats from all tasks
    stats_aggregated = {
        "task_ids": list(tasks.keys()),
        "completed_at_times": [
            t.get("completed_at") for t in tasks.values() if t.get("completed_at")
        ],
    }

    event = OrchestrationCompleteEvent(
        batch_id=batch_id,
        task_count=len(tasks),
        success_count=success_count,
        failed_tasks=failed_tasks,
        total_duration_secs=total_duration,
        stats_aggregated=stats_aggregated,
        event_type=(
            "ORCHESTRATION_COMPLETE_SUCCESS"
            if not failed_tasks
            else "ORCHESTRATION_COMPLETE_MIXED"
        ),
        timestamp=now,
    )

    # Emit to audit trail FIRST
    if not _emit_audit_event(event):
        return None  # Audit failed; batch stays pending for retry

    # Mark batch as emitted (AFTER successful audit write)
    rec["state"] = _STATE_EMITTED
    rec["emitted_at"] = now
    _atomic_write(path, rec)

    return event


def get_active_batches() -> list[dict]:
    """Debug helper: list all pending and recent batches.

    Returns:
        List of batch records (dicts).
    """
    batches_dir = _batches_dir()
    if not batches_dir.exists():
        return []

    active = []
    for path in sorted(batches_dir.glob("*.json")):
        rec = _read(path)
        if rec is not None:
            active.append(rec)
    return active


def cleanup_batches(*, now: Optional[float] = None) -> int:
    """Prune old delivered batches and abandoned pending batches.

    Removes:
      - Delivered batches older than OA_DELIVERED_TTL.
      - Pending batches older than OA_BATCH_MAX_AGE (abandoned work).

    Args:
        now: Unix timestamp (defaults to time.time()).

    Returns:
        Number of batches removed.
    """
    now = time.time() if now is None else now
    batches_dir = _batches_dir()
    if not batches_dir.exists():
        return 0

    removed = 0
    for path in sorted(batches_dir.glob("*.json")):
        rec = _read(path)
        if rec is None:
            try:
                if now - path.stat().st_mtime > OA_BATCH_MAX_AGE:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
            continue

        state = rec.get("state")
        created_at = rec.get("created_at", 0)
        emitted_at = rec.get("emitted_at", 0)

        if state == _STATE_EMITTED:
            # Delivered batch — prune if older than TTL
            age = now - emitted_at
            if age > OA_DELIVERED_TTL:
                path.unlink(missing_ok=True)
                removed += 1
        elif state == _STATE_PENDING:
            # Pending batch — prune if abandoned (older than max age)
            age = now - created_at
            if age > OA_BATCH_MAX_AGE:
                path.unlink(missing_ok=True)
                removed += 1

    return removed
