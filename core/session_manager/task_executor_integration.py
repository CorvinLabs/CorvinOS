"""TaskExecutor ↔ SessionAutoStarter Integration (ADR-0472 Phase 1.2).

Wires TaskExecutor's task loop to auto-session initialization:
  TaskExecutor.run() → call SessionAutoStarter.on_task_progress()
                    → auto-start new session on split trigger
"""

import logging
from typing import Optional, Any, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TaskExecutionState:
    """State passed between TaskExecutor and SessionAutoStarter."""
    task_id: str
    current_session_id: str
    context_usage_pct: float
    iterations: int
    goal: str
    audit_trail_hash: str
    split_metadata: dict = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "session_id": self.current_session_id,
            "context_pct": self.context_usage_pct,
            "iterations": self.iterations,
            "goal": self.goal,
            "audit_hash": self.audit_trail_hash,
            "splits": self.split_metadata or {},
        }


class TaskExecutorIntegration:
    """Wires TaskExecutor to SessionAutoStarter.

    Usage in TaskExecutor.run():
        integrator = TaskExecutorIntegration(auto_starter, tenant_id)
        for iteration in range(max_iterations):
            # Execute task step
            context_usage = monitor.get_context_usage()
            audit_hash = audit.get_current_hash()

            # Check for session split
            new_session = await integrator.on_iteration(
                task_id=task_id,
                current_session_id=current_session_id,
                context_usage_pct=context_usage,
                iterations=iteration,
                goal=task.goal,
                audit_trail_hash=audit_hash,
                context=execution_context,
            )

            if new_session:
                current_session_id = new_session
                logger.info(f"Task continued in new session: {new_session}")
    """

    def __init__(self, auto_starter: "SessionAutoStarter", tenant_id: str):
        """Initialize TaskExecutor integration.

        Args:
            auto_starter: SessionAutoStarter instance
            tenant_id: Tenant ID for isolation
        """
        self.auto_starter = auto_starter
        self.tenant_id = tenant_id

    async def on_task_start(
        self,
        task_id: str,
        goal: str,
    ) -> TaskExecutionState:
        """Called when task starts. Initialize first session.

        Args:
            task_id: Unique task identifier
            goal: Task goal/instruction

        Returns:
            Initial TaskExecutionState with first session_id
        """
        session_id = await self.auto_starter.on_task_start(
            task_id=task_id,
            goal=goal,
            tenant_id=self.tenant_id,
        )

        logger.info(f"[TaskIntegration] Task started: {task_id} in {session_id}")

        return TaskExecutionState(
            task_id=task_id,
            current_session_id=session_id,
            context_usage_pct=0.0,
            iterations=0,
            goal=goal,
            audit_trail_hash="genesis",
            split_metadata={},
        )

    async def on_iteration(
        self,
        task_id: str,
        current_session_id: str,
        context_usage_pct: float,
        iterations: int,
        goal: str,
        audit_trail_hash: str,
        context: dict,
    ) -> Optional[str]:
        """Called on each task iteration. Auto-split if needed.

        Args:
            task_id: Task ID
            current_session_id: Current session
            context_usage_pct: Context usage (0-100%)
            iterations: Iteration count
            goal: Task goal (for goal-drift check)
            audit_trail_hash: Current audit hash
            context: Execution context

        Returns:
            New session_id if split occurred, None otherwise
        """
        new_session_id = await self.auto_starter.on_task_progress(
            task_id=task_id,
            context_usage_pct=context_usage_pct,
            iterations=iterations,
            context=context,
            audit_trail_hash=audit_trail_hash,
        )

        if new_session_id:
            logger.info(
                f"[TaskIntegration] Session split: {task_id} "
                f"{current_session_id} → {new_session_id}"
            )

        return new_session_id

    async def on_task_complete(self, task_id: str) -> TaskExecutionState:
        """Called when task completes. Report final metadata.

        Args:
            task_id: Task ID

        Returns:
            Final TaskExecutionState with completion metadata
        """
        metadata = await self.auto_starter.on_task_complete(task_id)

        logger.info(
            f"[TaskIntegration] Task completed: {task_id} "
            f"splits={metadata.get('splits', 0)}"
        )

        return TaskExecutionState(
            task_id=task_id,
            current_session_id=metadata.get("final_session_id", "unknown"),
            context_usage_pct=100.0,
            iterations=metadata.get("iterations", 0),
            goal="completed",
            audit_trail_hash=metadata.get("audit_hash", ""),
            split_metadata=metadata,
        )
