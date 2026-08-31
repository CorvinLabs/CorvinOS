"""Autonomous Task Orchestration Engine (CONCEPT-0009 Implementation).

Orchestrate multi-option tasks with health monitoring, auto-healing, parallelism.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import List, Dict, Optional, Callable, Any
from uuid import uuid4


class TaskState(str, Enum):
    """Task lifecycle states."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    ESCALATED = "escalated"


class TaskPriority(int, Enum):
    """Task priority levels (higher = execute first)."""
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class TaskDefinition:
    """Autonomous task specification."""
    task_id: str
    name: str
    description: str
    priority: TaskPriority
    handler: Callable  # async def handler(task_context) -> result
    dependencies: List[str] = field(default_factory=list)
    max_retries: int = 3
    timeout_seconds: int = 3600
    metadata: Dict[str, Any] = field(default_factory=dict)

    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TaskContext:
    """Runtime context for autonomous task execution."""
    task_id: str
    state: TaskState
    attempts: int = 0
    last_error: Optional[str] = None
    result: Optional[Any] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AutonomousTaskEngine:
    """Orchestrate multiple autonomous tasks with health monitoring."""

    def __init__(self, name: str = "CorvinOS-TaskBrain"):
        """Initialize task engine.

        Args:
            name: Engine identifier
        """
        self.name = name
        self.tasks: Dict[str, TaskDefinition] = {}
        self.contexts: Dict[str, TaskContext] = {}
        self.event_log: List[Dict] = []

    def register_task(self, task: TaskDefinition) -> None:
        """Register an autonomous task.

        Args:
            task: Task definition
        """
        self.tasks[task.task_id] = task
        self.contexts[task.task_id] = TaskContext(
            task_id=task.task_id,
            state=TaskState.PENDING,
        )
        self._log_event("task_registered", {"task_id": task.task_id, "name": task.name})

    async def execute_task(self, task_id: str) -> Any:
        """Execute a single task with retry + healing logic.

        Args:
            task_id: Task to execute

        Returns:
            Task result or None if failed after retries
        """
        if task_id not in self.tasks:
            raise ValueError(f"Task {task_id} not registered")

        task = self.tasks[task_id]
        context = self.contexts[task_id]

        self._log_event("task_started", {"task_id": task_id, "priority": task.priority.name})

        for attempt in range(task.max_retries):
            context.attempts = attempt + 1
            context.state = TaskState.RUNNING
            context.started_at = datetime.utcnow()

            try:
                # Execute task with timeout
                result = await asyncio.wait_for(
                    task.handler(context),
                    timeout=task.timeout_seconds,
                )
                context.result = result
                context.state = TaskState.COMPLETE
                context.completed_at = datetime.utcnow()
                self._log_event("task_complete", {"task_id": task_id, "attempt": attempt + 1})
                return result

            except asyncio.TimeoutError as e:
                context.last_error = f"Timeout after {task.timeout_seconds}s"
                self._log_event("task_timeout", {"task_id": task_id, "attempt": attempt + 1})

            except Exception as e:
                context.last_error = str(e)
                self._log_event("task_error", {"task_id": task_id, "error": str(e), "attempt": attempt + 1})

            # Auto-heal: exponential backoff
            if attempt < task.max_retries - 1:
                backoff = 2 ** attempt
                await asyncio.sleep(backoff)

        # All retries exhausted
        context.state = TaskState.FAILED
        self._log_event("task_failed", {"task_id": task_id, "max_retries": task.max_retries})
        return None

    async def execute_parallel(self, task_ids: List[str]) -> Dict[str, Any]:
        """Execute multiple tasks in parallel.

        Args:
            task_ids: Tasks to execute

        Returns:
            Dict of {task_id: result}
        """
        results = {}
        tasks = [self.execute_task(tid) for tid in task_ids]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for task_id, outcome in zip(task_ids, outcomes):
            results[task_id] = outcome if not isinstance(outcome, Exception) else None

        return results

    def get_status(self) -> Dict[str, Any]:
        """Get engine status (for health monitoring)."""
        complete = sum(1 for c in self.contexts.values() if c.state == TaskState.COMPLETE)
        failed = sum(1 for c in self.contexts.values() if c.state == TaskState.FAILED)
        pending = sum(1 for c in self.contexts.values() if c.state == TaskState.PENDING)

        return {
            "engine": self.name,
            "total_tasks": len(self.tasks),
            "complete": complete,
            "failed": failed,
            "pending": pending,
            "running": sum(1 for c in self.contexts.values() if c.state == TaskState.RUNNING),
            "success_rate": complete / len(self.tasks) if self.tasks else 0.0,
        }

    def _log_event(self, event_type: str, data: Dict) -> None:
        """Log orchestration event."""
        self.event_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "data": data,
        })
