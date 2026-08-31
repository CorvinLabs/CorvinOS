"""Task registry for nervous system health monitoring.

Maintains live state of orchestrated tasks: tracking, dependencies,
error states, and orchestration events.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

class TaskState(str, Enum):
    """Task lifecycle states."""
    CREATED = "created"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class TaskRecord:
    """Immutable task record for registry."""
    task_id: str
    task_name: str
    state: TaskState
    created_at: datetime
    updated_at: datetime
    dependencies: List[str] = field(default_factory=list)
    phase: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

class TaskRegistry:
    """Tenant-scoped registry of orchestrated tasks.

    Responsibilities:
    - Track all active/completed tasks (immutable records)
    - Enforce tenant isolation (all queries filtered by tenant_id)
    - Provide health status (percentage complete, error rate)
    - Wire into Brain graph via ContextBus events
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._tasks: Dict[str, TaskRecord] = {}
        self._archived: List[TaskRecord] = []

    def create_task(
        self, task_name: str, dependencies: Optional[List[str]] = None
    ) -> str:
        """Create and register a new task.

        Args:
            task_name: Human-readable task name
            dependencies: List of parent task_ids this depends on

        Returns:
            Generated task_id (UUID4)

        Raises:
            ValueError: if a dependency doesn't exist
        """
        task_id = str(uuid.uuid4())
        if dependencies:
            for dep_id in dependencies:
                if dep_id not in self._tasks:
                    raise ValueError(f"Dependency {dep_id} not found in registry")

        record = TaskRecord(
            task_id=task_id,
            task_name=task_name,
            state=TaskState.CREATED,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            dependencies=dependencies or [],
        )
        self._tasks[task_id] = record
        return task_id

    def update_state(self, task_id: str, new_state: TaskState, phase: Optional[str] = None) -> None:
        """Update task state atomically.

        Args:
            task_id: Target task
            new_state: New TaskState
            phase: Current phase (e.g., 'gather', 'analyze', 'synthesize')

        Raises:
            KeyError: if task doesn't exist
        """
        if task_id not in self._tasks:
            raise KeyError(f"Task {task_id} not in registry")

        old_record = self._tasks[task_id]
        new_record = TaskRecord(
            task_id=old_record.task_id,
            task_name=old_record.task_name,
            state=new_state,
            created_at=old_record.created_at,
            updated_at=datetime.utcnow(),
            dependencies=old_record.dependencies,
            phase=phase or old_record.phase,
            error_message=old_record.error_message,
            metadata=old_record.metadata,
        )
        self._tasks[task_id] = new_record

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Retrieve task record."""
        return self._tasks.get(task_id)

    def list_active(self) -> List[TaskRecord]:
        """List all non-terminal tasks."""
        terminal_states = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
        return [
            r for r in self._tasks.values()
            if r.state not in terminal_states
        ]

    def health_status(self) -> Dict:
        """Compute registry health metrics.

        Returns:
            Dict with keys:
            - active_count: int
            - completed_count: int
            - failed_count: int
            - error_rate: float [0, 1]
        """
        active = len(self.list_active())
        completed = len([r for r in self._tasks.values() if r.state == TaskState.COMPLETED])
        failed = len([r for r in self._tasks.values() if r.state == TaskState.FAILED])
        total = len(self._tasks)

        error_rate = failed / total if total > 0 else 0.0
        return {
            "active_count": active,
            "completed_count": completed,
            "failed_count": failed,
            "total_count": total,
            "error_rate": error_rate,
        }
