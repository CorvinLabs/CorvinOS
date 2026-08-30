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
from core.context_engineering.session_checkpoint import SessionContinuationManager

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
        self._session_continuation_manager = SessionContinuationManager(corvin_home)
        self._corvin_home = corvin_home
        self._subsystems_initialized = False

    def register_subsystem(self, subsystem):
        """Register a subsystem with the brain."""
        self.hub.register_subsystem(subsystem)

    async def _register_skill_forge_subsystem(self, execution_context: Any) -> None:
        """Register SkillForgeSubsystem after ExecutionContext initialization.

        Called once per task during run_task() after ExecutionContext is available.

        Args:
            execution_context: ExecutionContext with tenant_id for subsystem

        Raises:
            Exception: Logged as warning; non-critical for task execution
        """
        try:
            from core.orchestration.subsystems.skill_forge_subsystem import SkillForgeSubsystem

            skill_forge = SkillForgeSubsystem(context=execution_context)
            self.register_subsystem(skill_forge)
            logger.info(f"✓ SkillForgeSubsystem registered for task (tenant={execution_context.tenant_id})")

        except Exception as e:
            logger.error(f"Failed to register SkillForgeSubsystem: {e}")
            raise

    async def initialize_subsystems(self) -> None:
        """Initialize and register all Brain subsystems (ADR-0360, ADR-0359, etc.).

        Called once during Brain startup to wire all standard subsystems:
        - SkillForgeSubsystem (ADR-0360) — NOW initialized in run_task() after ExecutionContext
        - ToolForgeSubsystem (ADR-0359)
        - LoopEngineer, LearningEngine, etc.

        Raises:
            RuntimeError: If subsystem initialization fails
        """
        if self._subsystems_initialized:
            logger.warning("Subsystems already initialized; skipping re-initialization")
            return

        try:
            logger.info("Initializing Brain subsystems...")

            # Note: SkillForgeSubsystem is now registered in run_task() after ExecutionContext
            # This method can be used for subsystems that don't need ExecutionContext

            # 2. TODO: ToolForgeSubsystem (ADR-0359) — add when available
            # TODO: Add other subsystems (LoopEngineer, LearningEngine, etc.)

            self._subsystems_initialized = True
            logger.info("✓ Brain subsystems initialization complete")

        except Exception as e:
            logger.error(f"Subsystem initialization failed: {e}")
            raise RuntimeError(f"Brain subsystem initialization failed: {e}") from e

    def save_task_checkpoint(
        self,
        task_id: str,
        tenant_id: str,
        turn_number: int = 0,
        tokens_consumed: int = 0,
        cost_consumed_cents: float = 0.0,
        error_recovery_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Save a checkpoint for task continuation (ADR-0367).

        Called by subsystems (e.g., HealthMonitor) after each turn to enable
        resuming the task in a new session if needed (e.g., on token overflow).

        Feature flag: FEATURE_SESSION_CHECKPOINTS (default: True)

        Args:
            task_id: Unique task identifier
            tenant_id: Tenant identifier
            turn_number: Which turn created this checkpoint
            tokens_consumed: Total tokens consumed so far
            cost_consumed_cents: Total cost incurred so far
            error_recovery_state: Optional error recovery metadata

        Returns:
            checkpoint_id (string) if successful, None if feature disabled or error
        """
        # Check if task is registered
        task_meta = self._tasks.get(task_id)
        if not task_meta:
            logger.warning(f"Task '{task_id}' not found in _tasks registry")
            return None

        try:
            execution_context = self._context_initializer.get_execution_context()
            # CRITICAL: Check for None before accessing attributes
            if execution_context is None:
                logger.warning(f"ExecutionContext is None for task '{task_id}'; checkpoint save skipped")
                return None

            session_id = getattr(execution_context, "session_id", task_id)
            checkpoint_id = self._session_continuation_manager.save_checkpoint(
                task_id=task_id,
                tenant_id=tenant_id,
                execution_context=execution_context,
                session_id=session_id,
                turn_number=turn_number,
                tokens_consumed=tokens_consumed,
                cost_consumed_cents=cost_consumed_cents,
                error_recovery_state=error_recovery_state,
            )
            logger.info(f"Saved checkpoint '{checkpoint_id}' for task '{task_id}'")
            return checkpoint_id

        except Exception as e:
            logger.error(f"Failed to save checkpoint for task '{task_id}': {e}")
            return None

    def get_checkpoint_metadata(self, task_id: str) -> list[Dict[str, Any]]:
        """Get metadata for all checkpoints of a task (for UI display).

        Args:
            task_id: Unique task identifier

        Returns:
            List of checkpoint metadata dicts
        """
        try:
            return self._session_continuation_manager.get_checkpoint_metadata(task_id)
        except Exception as e:
            logger.error(f"Failed to get checkpoint metadata for '{task_id}': {e}")
            return []

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
            LicenseLimitError: If daily brain_tasks quota exceeded.
        """
        logger.info(
            f"Starting task: {task_id} (type={task_type}, "
            f"context={'v1+v2' if ctx_v1 else 'v2'})"
        )

        # ADR-0365: Enforce brain_tasks_per_day quota
        try:
            from pathlib import Path
            from core.orchestration.quota_gate import increment_and_check
            # Configured root wins; the gate resolves CORVIN_HOME (then
            # ~/.corvin) when the initializer has none.
            _configured = self._context_initializer._corvin_home
            increment_and_check(
                Path(_configured) if _configured else None,
                "brain_tasks_per_day",
                tenant_id,
            )
        except Exception as e:
            logger.error(f"Brain task quota check failed: {e}")
            raise

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

            # ADR-0360: Register SkillForgeSubsystem once ExecutionContext is available
            # Feature flag: skill_forge_v2_enabled (default OFF, ship-dark per CLAUDE.md)
            if not self._subsystems_initialized:
                try:
                    execution_context = self._context_initializer.get_execution_context()
                    if execution_context:
                        # Check feature flag (default: OFF)
                        # Note: console uses "skill_forge_enabled", not "skill_forge_v2_enabled"
                        skill_forge_enabled = getattr(
                            execution_context, "_feature_flags", {}
                        ).get("skill_forge_enabled", False)

                        if skill_forge_enabled:
                            await self._register_skill_forge_subsystem(execution_context)
                            logger.info("SkillForgeSubsystem enabled (feature flag: skill_forge_v2_enabled)")
                        else:
                            logger.debug("SkillForgeSubsystem disabled (feature flag not set; default OFF)")

                        self._subsystems_initialized = True
                except Exception as e:
                    logger.warning(f"Failed to register SkillForgeSubsystem: {e}")
                    # Non-critical; continue without subsystems

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
