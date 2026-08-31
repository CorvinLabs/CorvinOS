"""
TaskRegistry: Persistent registry of in-flight tasks and phases.

Implements ADR-0402: Task-Orchestration Engine.
- Registry: persistent JSONL store (~/.corvin/tasks/registry.jsonl)
- Atomic writes via content-hash checksums (detect/fail on conflicts)
- Immutable phase snapshots (write-once append)
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Literal
from datetime import datetime
import json
import hashlib
import os
from pathlib import Path
import asyncio
from enum import Enum


class PhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class PhaseMetadata:
    """Immutable snapshot of a phase's state."""
    phase_id: str
    status: PhaseStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    result: Optional[Dict] = None  # Phase result data
    error: Optional[str] = None  # Error message on failure


@dataclass(frozen=True)
class TaskMetadata:
    """Immutable snapshot of a task's state at registration."""
    task_id: str
    title: str
    status: TaskStatus
    phases: Dict[str, PhaseMetadata] = field(default_factory=dict)

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    tenant_id: str = "_default"
    parent_task_id: Optional[str] = None

    def content_hash(self) -> str:
        """Deterministic content hash for conflict detection."""
        content = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()


class TaskRegistryPersistence:
    """Handles persistence of TaskMetadata to JSONL."""

    def __init__(self, registry_path: Optional[str] = None):
        """Initialize with optional custom registry path."""
        if registry_path is None:
            corvin_home = os.environ.get("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            registry_path = os.path.join(corvin_home, "tasks", "registry.jsonl")

        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()  # Serialize writes

        # Incremental index over the append-only log: (tenant_id, task_id) →
        # latest record, plus the byte offset up to which the file has been
        # parsed. Every read used to slurp and json-parse the ENTIRE file, and
        # a phase update happens several times per phase — so a long run's cost
        # was quadratic in its own length and a multi-hundred-phase task spent
        # most of its wall clock re-reading its own history. Parsing only the
        # bytes appended since the last read makes it linear. Correct under
        # concurrent appenders because the log is strictly append-only: bytes
        # already parsed never change.
        self._index: Dict[tuple, Dict] = {}
        self._offset = 0
        self._corrupt_lines = 0

    def _refresh_index(self) -> None:
        """Parse the bytes appended since the last refresh. Never raises."""
        try:
            size = self.registry_path.stat().st_size
        except OSError:
            self._index, self._offset = {}, 0
            return
        if size < self._offset:
            # The file shrank — it was compacted, rotated or replaced. The
            # index describes a file that no longer exists; rebuild from zero.
            self._index, self._offset = {}, 0
        if size == self._offset:
            return
        try:
            # BINARY mode, deliberately. `self._offset` is a BYTE offset, and
            # `TextIOWrapper.seek()` is documented to accept only an opaque
            # cookie from `tell()`. Passing a byte offset happens to work for
            # UTF-8 at a character boundary in CPython — it packs start_pos into
            # the low bits of the cookie — but that is an implementation detail,
            # not a contract. Reading bytes and decoding explicitly is exact and
            # also makes the `consumed` accounting below byte-precise by
            # construction.
            with open(self.registry_path, "rb") as f:
                f.seek(self._offset)
                raw = f.read()
        except OSError as e:
            raise RuntimeError(f"Failed to read registry: {e}") from e

        consumed = 0
        for bline in raw.splitlines(keepends=True):
            if not bline.endswith(b"\n"):
                # A partially written final line: another process is mid-append.
                # Stop here and pick it up on the next refresh rather than
                # treating a torn write as corruption.
                break
            consumed += len(bline)
            stripped = bline.strip()
            if not stripped:
                continue
            try:
                # json.loads accepts bytes and handles the UTF-8 decode; a line
                # with invalid UTF-8 raises here and is skipped as corrupt,
                # which is the right outcome for a torn write.
                record = json.loads(stripped)
                key = (record.get("tenant_id", "_default"), record.get("task_id"))
            except (json.JSONDecodeError, ValueError, AttributeError,
                    UnicodeDecodeError):
                # SKIP a corrupt line instead of failing the whole read. The
                # original code raised RuntimeError here, so a single torn or
                # truncated line (a crash mid-append, a full disk) made EVERY
                # task in the registry permanently unreadable — losing every
                # in-flight long run at once.
                self._corrupt_lines += 1
                continue
            if key[1]:
                self._index[key] = record
        self._offset += consumed

    @property
    def corrupt_line_count(self) -> int:
        """Corrupt lines skipped so far — surfaced so silent data loss is
        observable rather than invisible."""
        return self._corrupt_lines

    async def append_task(self, task: TaskMetadata) -> None:
        """Append task metadata to registry (write-once)."""
        async with self._lock:
            record = {
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status.value,
                "phases": {
                    phase_id: {
                        "phase_id": p.phase_id,
                        "status": p.status.value,
                        "started_at": p.started_at.isoformat() if p.started_at else None,
                        "completed_at": p.completed_at.isoformat() if p.completed_at else None,
                        "retry_count": p.retry_count,
                        "result": p.result,
                        "error": p.error,
                    }
                    for phase_id, p in task.phases.items()
                },
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
                "tenant_id": task.tenant_id,
                "parent_task_id": task.parent_task_id,
                "content_hash": task.content_hash(),
            }

            try:
                # One write() of one complete line, then fsync. Without the
                # flush+fsync a crash can leave the record only in the page
                # cache — the phase looks done in memory and is gone on disk,
                # which is precisely the state a resume cannot recover from.
                with open(self.registry_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except IOError as e:
                raise RuntimeError(f"Failed to append to registry: {e}")

    async def get_task(self, task_id: str, tenant_id: str = "_default") -> Optional[TaskMetadata]:
        """Retrieve latest version of a task from registry."""
        async with self._lock:
            if not self.registry_path.exists():
                return None
            self._refresh_index()
            record = self._index.get((tenant_id, task_id))
            if record is None:
                return None
            try:
                return self._deserialize_task(record)
            except (KeyError, ValueError, TypeError) as e:
                # A structurally valid JSON line with an unexpected shape (an
                # older schema, a hand-edited file). Treat it as absent rather
                # than raising into the orchestrator's hot loop.
                self._corrupt_lines += 1
                print(f"task_registry: unreadable record for {task_id}: {e}")
                return None

    async def list_tasks(self, tenant_id: str = "_default") -> List[TaskMetadata]:
        """Retrieve all latest versions of tasks for a tenant."""
        async with self._lock:
            if not self.registry_path.exists():
                return []
            self._refresh_index()
            out: List[TaskMetadata] = []
            for (tid, _task_id), record in self._index.items():
                if tid != tenant_id:
                    continue
                try:
                    out.append(self._deserialize_task(record))
                except (KeyError, ValueError, TypeError):
                    self._corrupt_lines += 1
            return out

    async def compact(self) -> int:
        """Rewrite the log keeping only the latest record per task.

        The log is append-only, so a long run's file grows with every phase
        update and never shrinks. This collapses it.

        PRECONDITION: no other process may be appending while this runs. The
        rewrite is atomic (tmp + rename) so the file is never observed
        half-written, but a record appended by ANOTHER process between the read
        and the rename would be lost — which is why this is an explicit
        maintenance call and is deliberately NOT invoked automatically from the
        hot path. Returns the number of records in the compacted file.
        """
        async with self._lock:
            if not self.registry_path.exists():
                return 0
            self._refresh_index()
            records = list(self._index.values())
            tmp = self.registry_path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.registry_path)
            # The index still describes the OLD byte layout; force a rebuild.
            self._offset = 0
            self._index = {}
            return len(records)

    def _deserialize_task(self, record: Dict) -> TaskMetadata:
        """Deserialize a task from a registry record."""
        phases = {
            phase_id: PhaseMetadata(
                phase_id=p["phase_id"],
                status=PhaseStatus(p["status"]),
                started_at=datetime.fromisoformat(p["started_at"]) if p["started_at"] else None,
                completed_at=datetime.fromisoformat(p["completed_at"]) if p["completed_at"] else None,
                retry_count=p["retry_count"],
                result=p["result"],
                error=p["error"],
            )
            for phase_id, p in record.get("phases", {}).items()
        }

        return TaskMetadata(
            task_id=record["task_id"],
            title=record["title"],
            status=TaskStatus(record["status"]),
            phases=phases,
            created_at=datetime.fromisoformat(record["created_at"]),
            updated_at=datetime.fromisoformat(record["updated_at"]),
            tenant_id=record["tenant_id"],
            parent_task_id=record.get("parent_task_id"),
        )


# Singleton instance for default registry
_default_registry = None


def get_default_registry() -> TaskRegistryPersistence:
    """Get or create default registry instance."""
    global _default_registry
    if _default_registry is None:
        _default_registry = TaskRegistryPersistence()
    return _default_registry
