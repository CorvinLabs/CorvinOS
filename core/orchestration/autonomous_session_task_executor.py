"""Production wrapper: AutonomousTaskEngine with autonomous sessions (Phase 1.2).

Extends AutonomousTaskEngine.execute_task() with SessionAutoStarter hooks
for automatic session initialization and splitting on context pressure.
"""

import logging
from typing import Optional, Any, Dict
from datetime import datetime

from .autonomous_task_engine import AutonomousTaskEngine, TaskDefinition

logger = logging.getLogger(__name__)


class AutonomousSessionTaskExecutor(AutonomousTaskEngine):
    """AutonomousTaskEngine with autonomous session management (Phase 1.2).

    Wraps execute_task() to auto-start sessions and split on context pressure.
    """

    def __init__(
        self,
        name: str = "CorvinOS-TaskBrain-SessionAware",
        session_auto_starter: Optional[Any] = None,
    ):
        """Initialize executor with session auto-starter.

        Args:
            name: Engine identifier
            session_auto_starter: SessionAutoStarter instance (optional)
        """
        super().__init__(name=name)
        self.session_auto_starter = session_auto_starter
        self.task_sessions: Dict[str, str] = {}  # task_id → session_id

    async def execute_task_with_sessions(
        self,
        task_id: str,
        context_monitor: Optional[Any] = None,
        audit_logger: Optional[Any] = None,
    ) -> Any:
        """Execute task with autonomous session splitting (Phase 1.2).

        Args:
            task_id: Task to execute
            context_monitor: Context usage monitor (e.g., token counter)
            audit_logger: Audit trail logger

        Returns:
            Task result or None if failed
        """
        if task_id not in self.tasks:
            raise ValueError(f"Task {task_id} not registered")

        task = self.tasks[task_id]
        context = self.contexts[task_id]

        # Initialize first session (if using auto-starter)
        if self.session_auto_starter:
            try:
                session_state = await self.session_auto_starter.on_task_start(
                    task_id=task_id,
                    goal=task.description or task.name,
                )
                self.task_sessions[task_id] = session_state.current_session_id
                logger.info(
                    f"[SessionTaskExecutor] Task {task_id} started "
                    f"in session {session_state.current_session_id}"
                )
            except Exception as e:
                logger.warning(
                    f"[SessionTaskExecutor] Failed to initialize session: {e}, "
                    f"continuing without session management"
                )

        # Wrap handler to monitor context and trigger splits
        original_handler = task.handler
        iteration_count = [0]

        async def session_aware_handler(ctx):
            """Wrapped handler that checks for session splits."""
            iteration_count[0] += 1

            # Get context metrics (if monitor provided)
            context_usage_pct = 0.0
            if context_monitor:
                try:
                    context_usage_pct = context_monitor.get_usage_percent()
                except Exception:
                    pass

            # Get audit hash (if logger provided)
            audit_hash = "unknown"
            if audit_logger:
                try:
                    audit_hash = audit_logger.get_current_hash()
                except Exception:
                    pass

            # Check for session split (if using auto-starter)
            if self.session_auto_starter and iteration_count[0] > 1:
                try:
                    new_session = await self.session_auto_starter.on_task_progress(
                        task_id=task_id,
                        context_usage_pct=context_usage_pct,
                        iterations=iteration_count[0],
                        context={"tokens_used": int(context_usage_pct * 100000), "tokens_available": 100000},
                        audit_trail_hash=audit_hash,
                    )

                    if new_session:
                        old_session = self.task_sessions[task_id]
                        self.task_sessions[task_id] = new_session
                        logger.info(
                            f"[SessionTaskExecutor] Task {task_id} split: "
                            f"{old_session} → {new_session} (iteration {iteration_count[0]})"
                        )
                except Exception as e:
                    logger.warning(f"[SessionTaskExecutor] Split check failed: {e}")

            # Execute original handler
            return await original_handler(ctx)

        # Replace handler temporarily
        task.handler = session_aware_handler

        try:
            # Execute with retry logic (inherited from AutonomousTaskEngine)
            result = await super().execute_task(task_id)

            # Report completion (if using auto-starter)
            if self.session_auto_starter:
                try:
                    completion_meta = await self.session_auto_starter.on_task_complete(task_id)
                    logger.info(
                        f"[SessionTaskExecutor] Task {task_id} completed "
                        f"in {completion_meta.get('splits', 0)} splits"
                    )
                except Exception as e:
                    logger.warning(f"[SessionTaskExecutor] Completion reporting failed: {e}")

            return result

        finally:
            # Restore original handler
            task.handler = original_handler

    def get_task_sessions(self, task_id: str) -> Optional[str]:
        """Get current session for a task (for monitoring).

        Args:
            task_id: Task ID

        Returns:
            Current session_id or None
        """
        return self.task_sessions.get(task_id)
