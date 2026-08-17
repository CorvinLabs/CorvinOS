"""TaskBrain: Main orchestration engine for autonomous task management.

ADR-0347: Brain Subsystem Hub Architecture
ADR-0358: Context Engineering v2 (ExecutionContext + ContextAPI)
CONCEPT-0009: Autonomous Task Orchestration

Task 2.3: ExecutionContext v1/v2 Coexistence Layer
  - Supports v1 (routing metadata) → v2 (task execution) conversion
  - Preserves backward compatibility with existing v1 callers
  - Bridges between old engine/delegation routing and new Brain subsystems
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Union

from .hub import SubsystemHub
from .brain_startup import ContextInitializer, BrainStartupError
from .context_bridge import ContextBridge
from core.console.corvin_core.execution_context import ExecutionContext as ExecutionContextV1

logger = logging.getLogger(__name__)


class TaskBrain:
    """Central orchestration engine for long-running tasks.

    Manages:
    - Subsystem registration and lifecycle
    - Event flow between subsystems
    - Task scheduling and execution
    - ExecutionContext initialization via MemoryCoordinator
    """

    def __init__(
        self,
        poll_interval_s: float = 5.0,
        max_event_queue_size: int = 10000,
        corvin_home: Optional[str] = None,
    ):
        self.poll_interval_s = poll_interval_s
        self.hub = SubsystemHub(max_event_queue_size=max_event_queue_size)
        self._tasks: Dict[str, Any] = {}
        self._context_initializer = ContextInitializer(corvin_home)

    def register_subsystem(self, subsystem):
        """Register a subsystem with the brain."""
        self.hub.register_subsystem(subsystem)

    async def run_task(
        self,
        task_id: str,
        tenant_id: str,
        task_type: str,
        budget_remaining: float = 1000.0,
        time_remaining: int = 3600,
        model: str = "claude-3-sonnet",
        ctx_v1: Optional[ExecutionContextV1] = None,
    ) -> Dict[str, Any]:
        """Run a task with ExecutionContext initialized from MemoryCoordinator.

        Supports both v1 (routing metadata) and v2 (task execution) contexts.
        If ctx_v1 provided (legacy caller), converts to v2 via ContextBridge.
        Otherwise, creates v2 context from scratch.

        Initializes:
        1. MemoryCoordinator to load task template
        2. ExecutionContext v2 (converted from v1 if provided)
        3. ContextBus for event pub/sub
        4. Broadcasts context_initialized event to subsystems

        Args:
            task_id: Unique task identifier
            tenant_id: Tenant identifier (usually '_default')
            task_type: Task type for template lookup (e.g., 'code_fix')
            budget_remaining: Initial budget (tokens or cost)
            time_remaining: Time available for task (seconds)
            model: LLM model identifier
            ctx_v1: Optional ExecutionContext v1 (backward-compat)

        Returns:
            Dict with task initialization result: {
                'task_id': str,
                'tenant_id': str,
                'context_initialized': bool,
                'template_source': str,
                'context_stack_depth': int,
                'version': 'v1+v2' if ctx_v1 else 'v2',  # new field
            }

        Raises:
            BrainStartupError: If context initialization fails.
        """
        logger.info(
            f"Starting task: {task_id} (type={task_type}, "
            f"context={'v1+v2' if ctx_v1 else 'v2'})"
        )

        try:
            # Override model from ctx_v1 if provided (v1 has priority)
            if ctx_v1 and ctx_v1.model_name:
                effective_model = ctx_v1.model_name
            else:
                effective_model = model

            # Initialize ExecutionContext via MemoryCoordinator
            result = await self._context_initializer.initialize_context(
                task_id=task_id,
                tenant_id=tenant_id,
                task_type=task_type,
                budget_remaining=budget_remaining,
                time_remaining=time_remaining,
                model=effective_model,
            )

            # Store both v1 (if provided) and v2 in task metadata
            self._tasks[task_id] = {
                "task_id": task_id,
                "tenant_id": tenant_id,
                "task_type": task_type,
                "status": "initialized",
                "version": "v1+v2" if ctx_v1 else "v2",
                "context_v1": ctx_v1,  # ← v1 for routing callbacks (may be None)
                "context_v1_metadata": (
                    ContextBridge.preserve_v1_fields(
                        self._context_initializer.get_execution_context(),
                        engine=ctx_v1.engine_id.value if ctx_v1 else "unknown",
                        model_source=ctx_v1.model_source if ctx_v1 else "unknown",
                        delegation_mode=ctx_v1.delegation_mode if ctx_v1 else "native",
                    )
                    if ctx_v1
                    else {}
                ),
                "context_bus": self._context_initializer.get_context_bus(),
                "context_api": self._context_initializer.get_context_api(),
                "execution_context": self._context_initializer.get_execution_context(),
            }

            # Add version to result
            result["version"] = self._tasks[task_id]["version"]

            logger.info(f"Task '{task_id}' context initialized: {result}")
            return result

        except BrainStartupError as e:
            logger.error(f"Failed to initialize task context: {str(e)}")
            raise

    async def run_forever(self):
        """Main orchestration loop."""
        logger.info("TaskBrain starting")
        await self.hub.run_forever(poll_interval_s=self.poll_interval_s)

    def stop(self):
        """Stop the brain."""
        logger.info("TaskBrain stopping")
        self.hub.stop()

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("TaskBrain shutting down")
        for name in list(self.hub.subsystems.keys()):
            self.hub.unregister_subsystem(name)
        await self._context_initializer.shutdown()
        self.stop()
