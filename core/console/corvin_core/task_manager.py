"""Task lifecycle management for console chat.

Each chat message spawn creates a Task — a durable, replayable execution context
with task_id, state (PENDING → RUNNING → COMPLETED | FAILED | CANCELLED),
and append-only event log.

Storage:
  <corvin_home>/sessions/web:<sid>/tasks/<task_id>.json        — task metadata
  <corvin_home>/sessions/web:<sid>/tasks/<task_id>.events.jsonl — event log (append-only)

Event log format (one JSON per line):
  {seq: 0, timestamp: <iso8601>, event: "task.created", ...}
  {seq: 1, timestamp: <iso8601>, event: "task.started", ...}
  {seq: 2, timestamp: <iso8601>, event: "stream_token", chunk: "..."}
  ...
  {seq: N, timestamp: <iso8601>, event: "task.completed", exit_code: 0}

ADR-0080 M4: Quota gates (max_concurrent, max_per_day), event log rotation,
session cleanup, L16 audit integration.

Boot recovery: a process killed mid-turn (SIGKILL / bridge restart) leaves
tasks stuck on RUNNING (no terminal event written), which also blocks the
max_concurrent quota. ``reap_stale_running()`` finalizes such orphans as
failed; the adapter calls it once per boot across all chats (see
adapter.py ``main()`` "task-reaper").
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Optional


class QuotaExceededError(Exception):
    """Raised when task creation would exceed quota limits."""
    pass


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """Task metadata (derived from event log replay)."""
    task_id: str
    chat_key: str
    status: TaskStatus
    created_at: float
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    input: dict[str, Any] = field(default_factory=dict)
    output_events: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: Optional[int] = None
    result_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "chat_key": self.chat_key,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "input": self.input,
            "duration_ms": self.duration_ms,
            "result_summary": self.result_summary,
        }


class TaskManager:
    """Manage task lifecycle: creation, event logging, queries."""

    def __init__(self, tasks_dir: Path):
        """Initialize with tasks directory.

        Args:
            tasks_dir: Base directory for all task metadata + event logs.
                       E.g. ~/.corvin/sessions/web:sid/tasks/
        """
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, task_id: str) -> Path:
        """Path to task metadata JSON."""
        return self.tasks_dir / f"{task_id}.json"

    def _events_path(self, task_id: str) -> Path:
        """Path to task event log JSONL."""
        return self.tasks_dir / f"{task_id}.events.jsonl"

    def _rotate_events_if_needed(self, task_id: str) -> None:
        """Rotate event log if it exceeds 10 MB or is older than 7 days (M4).

        Rotation creates a backup: task_id.events.jsonl.1, .2, etc.
        """
        events_path = self._events_path(task_id)
        if not events_path.exists():
            return

        # Check size (10 MB = 10 * 1024 * 1024 bytes)
        size_mb = events_path.stat().st_size / (1024 * 1024)
        if size_mb > 10:
            self._do_rotate(events_path)
            return

        # Check age (7 days = 7 * 86400 seconds)
        age_sec = time.time() - events_path.stat().st_mtime
        if age_sec > (7 * 86400):
            self._do_rotate(events_path)

    def _do_rotate(self, events_path: Path) -> None:
        """Perform log rotation: rename .jsonl to .jsonl.1, .jsonl.1 to .jsonl.2, etc."""
        try:
            # Find next rotation number
            n = 1
            while (events_path.parent / f"{events_path.name}.{n}").exists():
                n += 1

            # Rename current to .1, .1 to .2, etc. (reverse order to avoid collision)
            for i in range(n - 1, 0, -1):
                old = events_path.parent / f"{events_path.name}.{i}"
                new = events_path.parent / f"{events_path.name}.{i + 1}"
                if old.exists():
                    old.replace(new)

            # Rename current to .1
            events_path.replace(events_path.parent / f"{events_path.name}.1")
        except OSError:
            # If rotation fails, log and continue (don't block task execution)
            pass

    def _write_event(self, task_id: str, event: dict[str, Any]) -> int:
        """Append event to event log, return sequence number (0-indexed).

        The event dict should NOT include 'seq' or 'timestamp' — those are
        added here. Event must include at least 'event' field.
        Rotates log if needed (M4).
        """
        events_path = self._events_path(task_id)
        events_path.parent.mkdir(parents=True, exist_ok=True)

        # M4: Rotate if needed
        self._rotate_events_if_needed(task_id)

        # Count existing events to get next seq
        seq = 0
        if events_path.exists():
            with open(events_path, "r", encoding="utf-8") as f:
                for _ in f:
                    seq += 1

        # Prepare event with seq + timestamp
        full_event = {
            "seq": seq,
            "timestamp": time.time(),
            **event,
        }

        # Append to event log (atomic-ish: one line at a time)
        line = json.dumps(full_event, separators=(',', ':'))
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        return seq

    def _read_events(self, task_id: str) -> list[dict[str, Any]]:
        """Read all events from task's event log."""
        events_path = self._events_path(task_id)
        if not events_path.exists():
            return []

        events = []
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events

    def _replay_task(self, task_id: str) -> Task | None:
        """Reconstruct task state by replaying event log."""
        meta_path = self._meta_path(task_id)
        if not meta_path.exists():
            return None

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        task = Task(
            task_id=task_id,
            chat_key=meta["chat_key"],
            status=TaskStatus(meta["status"]),
            created_at=meta["created_at"],
            started_at=meta.get("started_at"),
            ended_at=meta.get("ended_at"),
            exit_code=meta.get("exit_code"),
            input=meta.get("input", {}),
            duration_ms=meta.get("duration_ms"),
            result_summary=meta.get("result_summary", ""),
        )

        # Attach event log for streaming
        task.output_events = self._read_events(task_id)

        return task

    def _write_meta(self, task_id: str, task: Task) -> None:
        """Write task metadata snapshot (for recovery)."""
        meta_path = self._meta_path(task_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)

        payload = task.to_dict()
        tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")

        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

        tmp.replace(meta_path)

    def _count_running_tasks(self, chat_key: str) -> int:
        """Count currently running tasks for a chat (M4 quota)."""
        count = 0
        for meta_file in self.tasks_dir.glob("*.json"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    if meta.get("chat_key") == chat_key and meta.get("status") == "running":
                        count += 1
            except (OSError, json.JSONDecodeError):
                pass
        return count

    def _count_tasks_today(self, chat_key: str) -> int:
        """Count tasks created today for a chat (M4 quota)."""
        now = time.time()
        today_start = (now - (now % 86400))  # Unix timestamp of today 00:00 UTC

        count = 0
        for meta_file in self.tasks_dir.glob("*.json"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    if meta.get("chat_key") == chat_key and meta.get("created_at", 0) >= today_start:
                        count += 1
            except (OSError, json.JSONDecodeError):
                pass
        return count

    def check_quota(
        self,
        chat_key: str,
        max_concurrent: int = 5,  # Default: business tier
        max_per_day: int = 500,
    ) -> None:
        """Check if task creation would exceed quota limits (M4).

        Args:
            chat_key: Chat identifier (e.g., "web:sid")
            max_concurrent: Max concurrent tasks for this tier
            max_per_day: Max tasks per calendar day for this tier

        Raises:
            QuotaExceededError: If quota would be exceeded
        """
        running = self._count_running_tasks(chat_key)
        if running >= max_concurrent:
            raise QuotaExceededError(
                f"Quota exceeded: {running} tasks running, max {max_concurrent}"
            )

        today_count = self._count_tasks_today(chat_key)
        if today_count >= max_per_day:
            raise QuotaExceededError(
                f"Daily quota exceeded: {today_count} tasks today, max {max_per_day}"
            )

    def create_task(
        self,
        chat_key: str,
        instruction: str,
        persona: str = "assistant",
        check_quota: bool = True,
        quota_limits: dict[str, int] | None = None,
        **metadata: Any,
    ) -> str:
        """Create a new task, record task.created event.

        Args:
            chat_key: Chat identifier
            instruction: Task instruction text
            persona: Persona name
            check_quota: If True, verify quota before creation (M4)
            quota_limits: {"max_concurrent": N, "max_per_day": N} or use defaults
            **metadata: Additional input metadata

        Returns: task_id (UUID)
        Raises: QuotaExceededError if quota check enabled and limits exceeded
        """
        # M4: Check quota before creation
        if check_quota:
            limits = quota_limits or {"max_concurrent": 5, "max_per_day": 500}
            self.check_quota(
                chat_key,
                max_concurrent=limits.get("max_concurrent", 5),
                max_per_day=limits.get("max_per_day", 500),
            )

        task_id = str(uuid.uuid4())

        # Record task.created event
        self._write_event(task_id, {
            "event": "task.created",
            "chat_key": chat_key,
            "instruction_len": len(instruction),
            "persona": persona,
        })

        # Write metadata snapshot
        task = Task(
            task_id=task_id,
            chat_key=chat_key,
            status=TaskStatus.PENDING,
            created_at=time.time(),
            input={"instruction": instruction, "persona": persona, **metadata},
        )
        self._write_meta(task_id, task)

        return task_id

    def record_event(self, task_id: str, event: dict[str, Any]) -> int:
        """Record an event in the task's log, update metadata if needed.

        Returns: sequence number of the event
        """
        seq = self._write_event(task_id, event)

        # Update metadata if this is a state-change event
        if event["event"] in ("task.started", "task.completed", "task.failed", "task.cancelled"):
            task = self._replay_task(task_id)
            if task:
                if event["event"] == "task.started":
                    task.status = TaskStatus.RUNNING
                    task.started_at = time.time()
                elif event["event"] == "task.completed":
                    task.status = TaskStatus.COMPLETED
                    task.ended_at = time.time()
                    task.exit_code = event.get("exit_code", 0)
                    if task.started_at and task.ended_at:
                        task.duration_ms = int((task.ended_at - task.started_at) * 1000)
                    task.result_summary = event.get("summary", "")
                elif event["event"] == "task.failed":
                    task.status = TaskStatus.FAILED
                    task.ended_at = time.time()
                    task.exit_code = event.get("exit_code", 1)
                    if task.started_at and task.ended_at:
                        task.duration_ms = int((task.ended_at - task.started_at) * 1000)
                elif event["event"] == "task.cancelled":
                    task.status = TaskStatus.CANCELLED
                    task.ended_at = time.time()

                self._write_meta(task_id, task)

        return seq

    def get_task(self, task_id: str) -> Task | None:
        """Get task by ID (replay from event log)."""
        return self._replay_task(task_id)

    def list_tasks(self, chat_key: str) -> list[Task]:
        """List all tasks for a chat_key."""
        tasks = []
        for meta_file in sorted(self.tasks_dir.glob("*.json")):
            if meta_file.name.endswith(".tmp"):
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("chat_key") == chat_key:
                    task_id = meta_file.stem
                    task = self._replay_task(task_id)
                    if task:
                        tasks.append(task)
            except (OSError, json.JSONDecodeError):
                pass

        # Sort by created_at descending (newest first)
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def cleanup_tasks(self, chat_key: str) -> int:
        """Delete all task files for a chat (M4 session reset cleanup).

        Called by L8 session.reset. Removes metadata + event logs.

        Returns: count of deleted task files
        """
        deleted = 0
        for meta_file in self.tasks_dir.glob("*.json"):
            if meta_file.name.endswith(".tmp"):
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("chat_key") != chat_key:
                    continue

                task_id = meta_file.stem

                # Delete metadata file
                try:
                    meta_file.unlink()
                    deleted += 1
                except OSError:
                    pass

                # Delete all event log files (including rotated .1, .2, etc.)
                events_base = self._events_path(task_id)
                for events_file in self.tasks_dir.glob(f"{task_id}.events.jsonl*"):
                    try:
                        events_file.unlink()
                    except OSError:
                        pass
            except json.JSONDecodeError:
                pass

        return deleted

    def cancel_task(self, task_id: str) -> bool:
        """Mark task as cancelled. Returns True if successful."""
        task = self._replay_task(task_id)
        if not task:
            return False

        if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return False

        self.record_event(task_id, {"event": "task.cancelled"})
        return True

    def _last_started_pid(self, task_id: str) -> int | None:
        """Return the pid from the task's most recent ``task.started`` event,
        or None if none was recorded."""
        events_path = self._events_path(task_id)
        if not events_path.exists():
            return None
        pid: int | None = None
        try:
            with events_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if not s or "task.started" not in s:
                        continue
                    try:
                        ev = json.loads(s)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event") == "task.started" and isinstance(ev.get("pid"), int):
                        pid = ev["pid"]
        except OSError:
            return None
        return pid

    def _task_pid_alive(self, task_id: str) -> bool:
        """True iff the task's recorded engine process is still alive.

        Crash-recovery correctness hinges on this: the boot reaper must reap
        ONLY genuine orphans (process gone) and NEVER a task whose process is
        still running. The "at boot nothing runs" assumption is false in
        practice — an engine subprocess can outlive an adapter restart
        (reparented, still streaming), and a task can itself spawn adapter
        boots (the E2E security suite boots ``adapter.main()``), whose reaper
        would otherwise finalize the live parent task. (Incident 2026-06-17.)

        Returns False when no pid was recorded (pending task that never
        started, or a missing event log) so true orphans are still reaped.
        ``os.kill(pid, 0)`` is the cross-platform liveness probe; on Linux we
        additionally confirm the pid is a ``claude`` engine to dodge pid-reuse
        (a dead task whose pid got recycled by an unrelated process).
        """
        pid = self._last_started_pid(task_id)
        if pid is None or pid <= 0:
            return False
        try:
            os.kill(pid, 0)  # signal 0: probe only, never delivered
        except ProcessLookupError:
            return False  # process gone — genuine orphan
        except PermissionError:
            return True   # alive but owned by another uid — still alive
        except OSError:
            return False
        # Linux hardening against pid-reuse — best-effort; non-Linux / races
        # fall back to trusting the os.kill liveness result above.
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            if b"claude" not in cmdline:
                return False  # pid recycled by an unrelated process → orphan
        except OSError:
            pass
        return True

    def reap_stale_running(self, reason: str = "orphaned_on_restart") -> list[str]:
        """Finalize tasks stuck in RUNNING/PENDING whose process is gone.

        A process killed (SIGKILL / bridge restart) while a task was RUNNING
        never writes a terminal event, so the task is stuck on ``running``
        forever. That also counts against the per-chat ``max_concurrent`` quota
        (see ``_count_running_tasks``), eventually starving the chat of new
        tasks. This finalizes such orphans.

        Liveness gate (load-bearing): a RUNNING/PENDING task is reaped ONLY when
        its recorded engine pid is no longer alive (see ``_task_pid_alive``).
        Reaping every RUNNING task unconditionally — the original "at boot
        nothing runs" assumption — false-positived a live multi-hour task on
        2026-06-17 when an E2E test booted ``adapter.main()`` while that task's
        process was still streaming. Never reap a task whose process is alive.

        Records an append-only ``task.failed`` event with ``error=reason`` so
        the event log stays the single source of truth and the quota counter is
        freed. ``task.failed`` is the canonical terminal transition (handled by
        ``record_event``); a new event name would not update the meta status and
        would leave the quota blocked.

        Returns: list of reaped task_ids (empty if none were stale).
        """
        reaped: list[str] = []
        for meta_file in self.tasks_dir.glob("*.json"):
            if meta_file.name.endswith(".tmp"):
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if meta.get("status") not in ("running", "pending"):
                continue
            task_id = meta_file.stem
            # Never reap a task whose engine process is still alive.
            if self._task_pid_alive(task_id):
                continue
            try:
                self.record_event(task_id, {
                    "event": "task.failed",
                    "exit_code": -1,
                    "error": reason,
                    "reaped": True,
                })
                reaped.append(task_id)
            except Exception:  # noqa: BLE001
                # Best-effort: one unreadable/locked task must not abort the
                # rest of the sweep.
                continue
        return reaped

    def events_since(
        self,
        task_id: str,
        start_seq: Optional[int] = None,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """Iterator of events AFTER start_seq (resume semantics).

        Yields: (seq, event_dict) for each event with seq > start_seq.
        start_seq is the last sequence number the consumer has already
        seen (SSE Last-Event-ID); None replays from the beginning.
        """
        events_path = self._events_path(task_id)
        if not events_path.exists():
            return

        start_seq = start_seq if start_seq is not None else -1

        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if event.get("seq", -1) > start_seq:
                        yield event["seq"], event
                except json.JSONDecodeError:
                    pass
