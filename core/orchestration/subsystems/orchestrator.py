"""Orchestrator subsystem: Task scheduling and parallelism management."""

import logging
from typing import Any, Dict, List, Optional

from .base import Subsystem

logger = logging.getLogger(__name__)


class Orchestrator(Subsystem):
    """Orchestrate task execution with parallelism and dependency tracking."""

    def __init__(
        self,
        max_parallel_sessions: int = 3,
        dependency_aware: bool = True,
    ):
        self.max_parallel_sessions = max_parallel_sessions
        self.dependency_aware = dependency_aware
        self.active_tasks: Dict[str, Dict[str, Any]] = {}
        self.dependencies: Dict[str, List[str]] = {}

    @property
    def name(self) -> str:
        return "orchestrator"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to task events."""
        self.hub = hub
        hub.subscribe("task_started", self.on_task_started)
        hub.subscribe("task_completed", self.on_task_completed)
        logger.info("Orchestrator started")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "task_started":
            task_id = event_data.get("task_id")
            if task_id:
                self.active_tasks[task_id] = event_data

        elif event_name == "task_completed":
            task_id = event_data.get("task_id")
            if task_id and task_id in self.active_tasks:
                del self.active_tasks[task_id]

    async def on_task_started(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Task started."""
        task_id = event_data.get("task_id")
        if task_id:
            self.active_tasks[task_id] = event_data

    async def on_task_completed(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Task completed."""
        task_id = event_data.get("task_id")
        if task_id and task_id in self.active_tasks:
            del self.active_tasks[task_id]

    def _add_dependency(self, task_id: str, depends_on: str) -> None:
        """Add dependency relationship."""
        if task_id not in self.dependencies:
            self.dependencies[task_id] = []
        if depends_on not in self.dependencies[task_id]:
            self.dependencies[task_id].append(depends_on)

    def _can_run(self, task_id: str) -> bool:
        """Check if task can run (dependencies satisfied)."""
        if not self.dependency_aware:
            return True

        if task_id not in self.dependencies:
            return True

        # All dependencies must be completed
        for dep_id in self.dependencies[task_id]:
            if dep_id in self.active_tasks:
                return False

        return True

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle orchestration queries."""
        if request_type == "spawn_task":
            task_id = kwargs.get("task_id")
            depends_on = kwargs.get("depends_on")

            if len(self.active_tasks) >= self.max_parallel_sessions:
                return {"success": False, "reason": "max parallel sessions reached"}

            if depends_on:
                self._add_dependency(task_id, depends_on)

            if not self._can_run(task_id):
                return {"success": False, "reason": "dependencies not satisfied"}

            self.active_tasks[task_id] = {"status": "spawned"}
            self.publish_event(
                "task_spawned",
                {
                    "task_id": task_id,
                    "dependencies": self.dependencies.get(task_id, []),
                },
            )
            return {"success": True, "task_id": task_id}

        elif request_type == "get_active_tasks":
            return {
                "active_count": len(self.active_tasks),
                "max_parallel": self.max_parallel_sessions,
                "tasks": list(self.active_tasks.keys()),
            }

        elif request_type == "can_parallelize":
            task_id = kwargs.get("task_id")
            return {
                "can_run": self._can_run(task_id),
                "parallel_slots_available": max(
                    0, self.max_parallel_sessions - len(self.active_tasks)
                ),
            }

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("Orchestrator shutdown")
