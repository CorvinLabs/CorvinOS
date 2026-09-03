"""Brain Startup — Initialization and context setup for TaskBrain (ADR-0358).

Handles:
- Compliance tripwire verification (ADR-0232, CLAUDE.md)
- MemoryCoordinator initialization
- ExecutionContext creation from templates
- ContextBus initialization
- Initial context broadcasting
"""

import asyncio
import logging
from typing import Optional, Dict, Any

from core.context_engineering.memory_coordinator import MemoryCoordinator
from core.context_engineering.context_bus import ContextBus
from core.context_engineering.context_api import ContextAPI
from core.context_engineering.execution_context import ExecutionContext, ContextStack
from core.context_engineering.session_checkpoint import SessionContinuationManager, CheckpointNotFoundError
from core.orchestration.context_coherence_manager import ContextCoherenceManager, CoherenceNotFoundError

logger = logging.getLogger(__name__)


class BrainStartupError(Exception):
    """Raised when brain startup fails."""

    pass


class ContextInitializer:
    """Handles ExecutionContext initialization for a task.

    Responsibilities:
    - Load task template from MemoryCoordinator
    - Create ExecutionContext from template
    - Initialize ContextBus
    - Broadcast context_initialized event
    """

    def __init__(self, corvin_home: Optional[str] = None):
        """Initialize ContextInitializer.

        Per CLAUDE.md and ADR-0232, verifies compliance tripwire on startup.
        Tripwire is non-overridable and fail-closed: if core audit mechanisms
        are unreachable, the platform shuts down.

        Args:
            corvin_home: Path to CORVIN_HOME (falls back to env var if None).

        Raises:
            RuntimeError: If compliance tripwire check fails (non-recoverable).
            ValueError: If corvin_home not provided and CORVIN_HOME env var not set.
        """
        # Per CLAUDE.md: Boot tripwire (fail-closed; asserts the CORE audit writer is reachable)
        # ADR-0232: Tripwire assertions at boot: Missing mechanism → platform SHUTS DOWN
        try:
            # The tripwire lives at core/compliance/corvin_compliance_reports/
            # tripwire.py (CLAUDE.md names this path). The import used to read
            # `core.compliance.tripwire`, which does not exist — so it raised
            # ModuleNotFoundError, was caught by the handler below, and reported
            # as "compliance tripwire failure". Fail-CLOSED, so nothing unsafe
            # got through; but the tripwire ASSERTED NOTHING. It never checked
            # that the core audit writer was reachable or that its chain
            # verified — it only ever crashed on its own import, and this whole
            # startup path was dead as a result.
            from corvin_compliance_reports.tripwire import assert_all
            assert_all()
            logger.info("Compliance tripwire passed — core audit mechanisms reachable")
        except Exception as e:
            logger.critical(f"COMPLIANCE TRIPWIRE FAILED: {e}")
            raise RuntimeError(f"Platform shutdown: compliance tripwire failure: {e}") from e

        self.memory_coordinator = MemoryCoordinator(corvin_home)
        self.session_continuation_manager = SessionContinuationManager(corvin_home)
        self.context_coherence_manager = ContextCoherenceManager(corvin_home)
        self.context_bus: Optional[ContextBus] = None
        self.execution_context: Optional[ExecutionContext] = None
        self.context_api: Optional[ContextAPI] = None
        self._corvin_home = corvin_home

    async def initialize_context(
        self,
        task_id: str,
        tenant_id: str,
        task_type: str,
        budget_remaining: float = 1000.0,
        time_remaining: int = 3600,
        model: str = "claude-3-sonnet",
        checkpoint_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initialize ExecutionContext for a task.

        If checkpoint_id is provided, resumes from a prior checkpoint.
        Otherwise, loads task template from MemoryCoordinator (PROJECT > GLOBAL hierarchy),
        creates ExecutionContext, initializes ContextBus, and broadcasts
        context_initialized event.

        Args:
            task_id: Unique task identifier
            tenant_id: Tenant identifier (usually '_default')
            task_type: Task type for template lookup (e.g., 'code_fix')
            budget_remaining: Initial budget (tokens or cost)
            time_remaining: Time available for task (seconds)
            model: LLM model identifier
            checkpoint_id: Optional checkpoint ID to resume from (ADR-0367)

        Returns:
            Dict with initialization result: {
                'task_id': str,
                'tenant_id': str,
                'context_initialized': bool,
                'template_source': str,  # 'project', 'global', or 'checkpoint'
                'context_stack_depth': int,
                'resumed_from_checkpoint': bool,  # New field
            }

        Raises:
            BrainStartupError: If initialization fails.
        """
        try:
            resumed_from_checkpoint = False

            # Step 1: Try to resume from checkpoint if provided (ADR-0367)
            if checkpoint_id:
                try:
                    checkpoint = self.session_continuation_manager.load_checkpoint(
                        task_id, checkpoint_id
                    )
                    self.execution_context = self.session_continuation_manager.resume_from_checkpoint(
                        checkpoint, ExecutionContext
                    )
                    template_source = "checkpoint"
                    resumed_from_checkpoint = True
                    logger.info(
                        f"Resumed task '{task_id}' from checkpoint '{checkpoint_id}' "
                        f"at turn {checkpoint.turn_number}"
                    )

                    # Step 1b: Load parent coherence if available (ADR-0369)
                    try:
                        parent_coherence = self.context_coherence_manager.load_coherence(
                            task_id
                        )
                        logger.info(
                            f"Loaded parent coherence for task '{task_id}': "
                            f"{len(parent_coherence.tools_known_good)} good tools"
                        )
                        # Store in metadata for subsystems to use
                        if not hasattr(self, "_coherence_data"):
                            self._coherence_data = {}
                        self._coherence_data[task_id] = parent_coherence
                    except CoherenceNotFoundError:
                        logger.info(f"No prior coherence for task '{task_id}'")

                except CheckpointNotFoundError as e:
                    logger.warning(f"Checkpoint not found: {e}. Using fresh task template.")
                    # Fall through to template-based initialization

            # If no checkpoint or checkpoint failed, load task template
            if not resumed_from_checkpoint:
                # Load task template from MemoryCoordinator
                task_template = self.memory_coordinator.load_task_template(task_type)
                template_source = task_template.get("_source", "unknown")

                logger.info(
                    f"Loaded task template '{task_type}' from {template_source} memory layer"
                )

                # Create ContextStack (initially at root)
                context_stack = ContextStack()
                context_stack.push("task", task_id)

                # Create ExecutionContext
                self.execution_context = ExecutionContext(
                    task_id=task_id,
                    tenant_id=tenant_id,
                    task_template=task_template,
                    context_stack=context_stack,
                    budget_remaining=budget_remaining,
                    time_remaining=time_remaining,
                    model=model,
                    strategy="",
                    strategy_confidence=0.5,
                )

            logger.info(f"Created ExecutionContext for task '{task_id}'")

            # Step 4: Initialize ContextBus
            self.context_bus = ContextBus()
            await self.context_bus.start()
            ContextBus.set_context(self.execution_context)

            logger.info("ContextBus started and ExecutionContext registered")

            # Step 5: Create ContextAPI for root subsystem access
            self.context_api = ContextAPI("TaskBrain", self.context_bus)

            # Step 6: Broadcast context_initialized event
            await self.context_bus.publish(
                "context_initialized",
                {
                    "task_id": task_id,
                    "tenant_id": tenant_id,
                    "template_source": template_source,
                    "context_stack": str(self.execution_context.context_stack),
                    "budget_remaining": budget_remaining,
                    "time_remaining": time_remaining,
                    "model": model,
                    "timestamp": self.execution_context.decision_history.__class__.now_iso()
                    if hasattr(self.execution_context.decision_history.__class__, "now_iso")
                    else "",
                },
            )

            logger.info(f"Broadcasting context_initialized for task '{task_id}'")

            return {
                "task_id": task_id,
                "tenant_id": tenant_id,
                "context_initialized": True,
                "template_source": template_source,
                "context_stack_depth": self.execution_context.context_stack.depth,
                "resumed_from_checkpoint": resumed_from_checkpoint,
            }

        except Exception as e:
            logger.error(f"Failed to initialize context: {str(e)}")
            raise BrainStartupError(f"Context initialization failed: {str(e)}") from e

    async def shutdown(self) -> None:
        """Gracefully shutdown context bus and cleanup."""
        if self.context_bus:
            await self.context_bus.stop()
            logger.info("ContextBus stopped")

    def get_execution_context(self) -> Optional[ExecutionContext]:
        """Get the initialized ExecutionContext."""
        return self.execution_context

    def get_context_api(self) -> Optional[ContextAPI]:
        """Get the ContextAPI for subsystem use."""
        return self.context_api

    def get_context_bus(self) -> Optional[ContextBus]:
        """Get the ContextBus for event pub/sub."""
        return self.context_bus
