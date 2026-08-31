"""Persistent task queue (M2): append-only log at tenants/<tid>/global/tasks.jsonl.

ADR-0081 M2.0: Tenant-global task queue decoupled from sessions.
Tasks survive session death and are processed by independent worker pool.
ADR-0080 M4: Quota gates (max_concurrent, max_per_day), event log rotation.
ADR-0101 M3: Instruction text split into per-task payload files (mode 0600),
keeping the queue JSONL metadata-only (no user content in the log).
Phase 2: completion_notify routing registration for Discord/Telegram notifications.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raised when task creation would exceed quota limits."""
    pass


class TaskStatus(str):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str):
    pass


# Re-define properly as a proper class
from enum import Enum


class TaskStatus(str, Enum):
    """Task lifecycle states."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskQueueEntry:
    """Queue entry for tenant-global persistent task.

    NOTE: ``instruction`` is NOT stored in the queue JSONL log (ADR-0101 M3).
    It is written to a separate payload file (<global>/task_payloads/<task_id>.bin,
    mode 0600) and loaded on demand by the worker. The field is populated
    after dequeue via TaskQueue.load_instruction().
    """
    task_id: str
    tenant_id: str
    chat_key: str
    instruction: str  # populated from payload file, empty string in replay
    status: TaskStatus
    created_at: float
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    ttl_seconds: int = 3600
    context_checkpoint: Optional[dict[str, Any]] = None  # ADR-0087 M1

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "chat_key": self.chat_key,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "ttl_seconds": self.ttl_seconds,
            "context_checkpoint": self.context_checkpoint,
        }

    @property
    def is_terminal(self) -> bool:
        """Task in final state."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    @property
    def is_expired(self) -> bool:
        """Task exceeded TTL."""
        return (time.time() - self.created_at) > self.ttl_seconds


class TaskQueue:
    """Persistent append-only task queue.

    Storage layout (ADR-0101 M3):
      tasks_<tenant_id>.jsonl  — metadata only (no instruction text), mode 0600
      task_payloads/<task_id>.bin — instruction payload, mode 0600
      task_payloads/ directory   — mode 0700

    Queue events (JSONL):
      {"timestamp": ..., "event": "task.enqueued", "task_id": ..., "chat_key": ..., ...}
      {"timestamp": ..., "event": "task.status_changed", "task_id": ..., "status": ..., ...}

    The instruction field is intentionally absent from all JSONL events.
    """

    def __init__(self, tenant_global_dir: Path):
        """Initialize queue.

        Args:
            tenant_global_dir: Path to tenants/<tid>/global/ directory.
        """
        self.tenant_global_dir = Path(tenant_global_dir)
        self.tenant_global_dir.mkdir(parents=True, exist_ok=True)
        # Payload dir: mode 0700
        payload_dir = self.tenant_global_dir / "task_payloads"
        payload_dir.mkdir(exist_ok=True)
        try:
            payload_dir.chmod(0o700)
        except OSError:
            pass

    def _log_path(self, tenant_id: str) -> Path:
        """Path to tenant's task queue log."""
        return self.tenant_global_dir / f"tasks_{tenant_id}.jsonl"

    def _payload_dir(self) -> Path:
        return self.tenant_global_dir / "task_payloads"

    def _payload_path(self, task_id: str) -> Path:
        """Path to instruction payload file for a task."""
        return self._payload_dir() / f"{task_id}.bin"

    def _write_payload(self, task_id: str, instruction: str) -> None:
        """Write instruction to payload file atomically (mode 0600)."""
        path = self._payload_path(task_id)
        body = instruction.encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=self._payload_dir(), prefix=".pl.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load_instruction(self, task_id: str) -> str | None:
        """Load instruction from payload file.

        Returns None if the payload file has been cleaned up (terminal task).
        """
        path = self._payload_path(task_id)
        if not path.exists():
            return None
        try:
            return path.read_bytes().decode("utf-8")
        except OSError:
            return None

    def _cleanup_payload(self, task_id: str) -> None:
        """Delete payload file when task reaches a terminal state."""
        path = self._payload_path(task_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _write_event(self, tenant_id: str, event_type: str, **details) -> None:
        """Append event to append-only log (metadata only — no instruction text)."""
        log_path = self._log_path(tenant_id)
        event = {
            "timestamp": time.time(),
            "event": event_type,
            "tenant_id": tenant_id,
            **details,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())
        # Ensure log file is 0600
        try:
            log_path.chmod(0o600)
        except OSError:
            pass

    def _count_active_tasks(self, tenant_id: str) -> int:
        tasks = self._replay_log(tenant_id)
        return sum(1 for t in tasks if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING))

    def _count_tasks_created_today(self, tenant_id: str) -> int:
        tasks = self._replay_log(tenant_id)
        cutoff = time.time() - 86400
        return sum(1 for t in tasks if t.created_at >= cutoff)

    def check_quota(
        self,
        tenant_id: str,
        max_concurrent: int = 10,
        max_per_day: int = 1000,
    ) -> None:
        """Check quota limits before creating a new task (M4).

        Raises:
            QuotaExceededError if limits exceeded.
        """
        active = self._count_active_tasks(tenant_id)
        if active >= max_concurrent:
            raise QuotaExceededError(
                f"Quota exceeded: {active} concurrent tasks (max {max_concurrent})"
            )
        today = self._count_tasks_created_today(tenant_id)
        if today >= max_per_day:
            raise QuotaExceededError(
                f"Quota exceeded: {today} tasks created today (max {max_per_day})"
            )

    def enqueue(
        self,
        tenant_id: str,
        chat_key: str,
        instruction: str,
        ttl_seconds: int = 3600,
        check_quota: bool = True,
        quota_limits: dict | None = None,
        channel: str | None = None,
        chat_id: str | int | None = None,
        sender: str | None = None,
    ) -> str:
        """Enqueue a new task with optional messenger routing.

        The instruction is stored in a separate payload file (0600); the queue
        JSONL log contains only metadata — no user text (ADR-0101 M3).

        Phase 2: If channel + chat_id provided, registers routing with
        completion_notify so task completion notifications can be routed to the
        originating messenger (Discord, Telegram, etc.). Sender field is
        PII-gated per ADR-0297.

        Returns:
            task_id (UUID4 string).

        Raises:
            QuotaExceededError if quota check enabled and limits exceeded.
        """
        if check_quota:
            limits = quota_limits or {"max_concurrent": 10, "max_per_day": 1000}
            self.check_quota(
                tenant_id,
                max_concurrent=limits.get("max_concurrent", 10),
                max_per_day=limits.get("max_per_day", 1000),
            )

        task_id = str(uuid.uuid4())
        now = time.time()

        # Write instruction to payload file BEFORE the queue event,
        # so the worker always finds a payload when it dequeues.
        self._write_payload(task_id, instruction)

        # Phase 2: Register routing for completion notifications (ADR-0297 PII gate)
        if channel and chat_id:
            try:
                root = Path(__file__).resolve().parents[2]
                shared = root / "operator" / "bridges" / "shared"
                if str(shared) not in sys.path:
                    sys.path.insert(0, str(shared))
                import completion_notify as _cn

                # PII GATE (ADR-0297): scan sender for PII before registration
                sanitized_sender = sender or "api"
                if sender:
                    try:
                        from core.pii import has_sensitive
                        if has_sensitive(sender):
                            logger.warning(
                                f"[task_queue] Task {task_id}: sender contains PII, "
                                f"dropping sender field for completion_notify"
                            )
                            sanitized_sender = "api"
                    except Exception as e:
                        logger.warning(f"[task_queue] PII check failed for sender: {e}")
                        # Fail-closed: drop sender on PII check error
                        sanitized_sender = "api"

                _cn.register(
                    task_id=task_id,
                    channel=channel,
                    chat_id=str(chat_id),
                    tenant_id=tenant_id,
                    sender=sanitized_sender,
                    label="background task",
                )
                logger.info(
                    f"[task_queue] Registered routing for task {task_id}: "
                    f"channel={channel}, chat_id={chat_id}"
                )
            except Exception as e:
                logger.warning(
                    f"[task_queue] Failed to register completion_notify for {task_id}: {e}. "
                    f"Task will proceed but notifications will not be routed."
                )

        self._write_event(
            tenant_id,
            "task.enqueued",
            task_id=task_id,
            chat_key=chat_key,
            # instruction intentionally omitted — stored in payload file
            created_at=now,
            ttl_seconds=ttl_seconds,
        )

        return task_id

    def dequeue(self, tenant_id: str) -> Optional[TaskQueueEntry]:
        """Dequeue next PENDING task (FIFO).

        Returns a TaskQueueEntry with instruction='' (the worker must call
        load_instruction() to get the actual text).
        """
        tasks = self._replay_log(tenant_id)

        for task in tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if task.is_expired:
                self.update_status(task.task_id, TaskStatus.FAILED, exit_code=124)
                continue
            self.update_status(task.task_id, TaskStatus.RUNNING)
            return self.get_task(task.task_id, tenant_id=tenant_id)

        return None

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        exit_code: Optional[int] = None,
    ) -> None:
        """Update task status (idempotent).

        Cleans up the payload file when the task reaches a terminal state.
        """
        task = self.get_task(task_id)
        if task is None:
            return

        now = time.time()

        started_at = task.started_at
        if status == TaskStatus.RUNNING and task.started_at is None:
            started_at = now

        ended_at = task.ended_at
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            ended_at = now

        details: dict[str, Any] = {
            "task_id": task_id,
            "status": status.value,
        }
        if started_at is not None:
            details["started_at"] = started_at
        if ended_at is not None:
            details["ended_at"] = ended_at
        if exit_code is not None:
            details["exit_code"] = exit_code

        self._write_event(task.tenant_id, "task.status_changed", **details)

        # Clean up payload file on terminal state (instruction no longer needed)
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._cleanup_payload(task_id)

    def get_task(self, task_id: str, tenant_id: Optional[str] = None) -> Optional[TaskQueueEntry]:
        """Retrieve task by ID (replays log, returns latest state).

        The returned entry has instruction='' — call load_instruction() to
        load the payload file if needed.
        """
        if tenant_id:
            tasks = self._replay_log(tenant_id)
            for task in tasks:
                if task.task_id == task_id:
                    return task
        else:
            for log_path in self.tenant_global_dir.glob("tasks_*.jsonl"):
                tenant = log_path.stem.replace("tasks_", "", 1)
                tasks = self._replay_log(tenant)
                for task in tasks:
                    if task.task_id == task_id:
                        return task
        return None

    def list_tenant_tasks(
        self,
        tenant_id: str,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> list[TaskQueueEntry]:
        """List all tasks for a tenant, optionally filtered by status."""
        tasks = self._replay_log(tenant_id)
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def cleanup_expired_tasks(self, tenant_id: str) -> int:
        """Mark all expired PENDING tasks as FAILED."""
        count = 0
        tasks = self._replay_log(tenant_id)
        for task in tasks:
            if task.status == TaskStatus.PENDING and task.is_expired:
                self.update_status(task.task_id, TaskStatus.FAILED, exit_code=124)
                count += 1
        return count

    def _replay_log(self, tenant_id: str) -> list[TaskQueueEntry]:
        """Replay append-only log to reconstruct current state.

        Instruction is not stored in the log; entries have instruction=''.
        """
        log_path = self._log_path(tenant_id)
        if not log_path.exists():
            return []

        state: dict[str, TaskQueueEntry] = {}

        try:
            with open(log_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    event_type = event.get("event")

                    if event_type == "task.enqueued":
                        task_id = event["task_id"]
                        state[task_id] = TaskQueueEntry(
                            task_id=task_id,
                            tenant_id=event.get("tenant_id", tenant_id),
                            chat_key=event.get("chat_key", ""),
                            instruction="",  # loaded from payload file on demand
                            status=TaskStatus.PENDING,
                            created_at=event["created_at"],
                            ttl_seconds=event.get("ttl_seconds", 3600),
                        )

                    elif event_type == "task.status_changed":
                        task_id = event["task_id"]
                        if task_id in state:
                            state[task_id].status = TaskStatus(event["status"])
                            state[task_id].started_at = event.get("started_at")
                            state[task_id].ended_at = event.get("ended_at")
                            state[task_id].exit_code = event.get("exit_code")

        except Exception:
            # Log corruption: treat as recoverable (partial state is better than none)
            pass

        return list(state.values())
