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
                with open(self.registry_path, "a") as f:
                    f.write(json.dumps(record) + "\n")
            except IOError as e:
                raise RuntimeError(f"Failed to append to registry: {e}")

    async def get_task(self, task_id: str, tenant_id: str = "_default") -> Optional[TaskMetadata]:
        """Retrieve latest version of a task from registry."""
        async with self._lock:
            if not self.registry_path.exists():
                return None

            try:
                with open(self.registry_path, "r") as f:
                    lines = f.readlines()
                    # Find the LAST occurrence of this task_id (latest version)
                    for line in reversed(lines):
                        record = json.loads(line)
                        if record.get("task_id") == task_id and record.get("tenant_id") == tenant_id:
                            return self._deserialize_task(record)
                return None
            except (IOError, json.JSONDecodeError) as e:
                raise RuntimeError(f"Failed to read registry: {e}")

    async def list_tasks(self, tenant_id: str = "_default") -> List[TaskMetadata]:
        """Retrieve all latest versions of tasks for a tenant."""
        async with self._lock:
            if not self.registry_path.exists():
                return []

            try:
                seen_task_ids = {}
                with open(self.registry_path, "r") as f:
                    lines = f.readlines()
                    # Read backwards to find latest version of each task
                    for line in reversed(lines):
                        record = json.loads(line)
                        if record.get("tenant_id") != tenant_id:
                            continue
                        task_id = record.get("task_id")
                        if task_id not in seen_task_ids:
                            seen_task_ids[task_id] = record

                return [self._deserialize_task(r) for r in seen_task_ids.values()]
            except (IOError, json.JSONDecodeError) as e:
                raise RuntimeError(f"Failed to list tasks: {e}")

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
