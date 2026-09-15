"""
SessionManager Orchestrator (S3.1)
Wires S1.1 (Lifecycle), S1.2 (Checkpoint), S2.1 (ContextReducer), S2.2 (RecoveryEngine)
"""
from typing import Optional, Dict, Any
from dataclasses import dataclass
import logging

from .session_lifecycle_manager import SessionLifecycleManager, SplitTrigger
from .checkpoint_manager import CheckpointManager, Checkpoint
from .context_reducer import ContextReducer
from .recovery_engine import RecoveryEngine

logger = logging.getLogger(__name__)


@dataclass
class SessionExecutionPlan:
    """Orchestration plan for task execution with auto-recovery"""
    task_id: str
    max_iterations: int = 200
    split_threshold_stalls: int = 5
    auto_split_enabled: bool = True


class SessionManager:
    """Main orchestrator: detects splits → checkpoints → reduces → recovers"""

    def __init__(self):
        self.lifecycle_mgr = SessionLifecycleManager()
        self.checkpoint_mgr = CheckpointManager()
        self.context_reducer = ContextReducer()
        self.recovery_engine = RecoveryEngine()
        self.event_bus = None  # Wired in S3.2

    def execute_task(self, task_id: str, initial_context: Dict[str, Any],
                     plan: Optional[SessionExecutionPlan] = None) -> Dict[str, Any]:
        """
        Execute task with auto-split, checkpoint, and recovery.

        Flow:
        1. Detect split trigger → checkpoint
        2. Reduce context 91%
        3. On resume: recover full state + apply learnings
        """
        if not plan:
            plan = SessionExecutionPlan(task_id=task_id)

        # Wir sind hier: Execute with monitoring
        iteration = 0
        current_context = initial_context
        split_count = 0

        try:
            while iteration < plan.max_iterations:
                iteration += 1

                # S1.1: Detect split triggers
                split_trigger = self.lifecycle_mgr.detect_split_trigger(
                    iteration, current_context
                )

                if split_trigger and plan.auto_split_enabled:
                    logger.info(f"Split triggered: {split_trigger}")
                    split_count += 1

                    # S1.2: Create checkpoint
                    checkpoint = self.checkpoint_mgr.create_checkpoint(
                        task_id=task_id,
                        iteration=iteration,
                        context=current_context,
                        split_trigger=split_trigger
                    )

                    # S2.1: Reduce context 91%
                    reduced = self.context_reducer.reduce(current_context)
                    logger.info(f"Context reduced: {len(str(current_context))} → {len(str(reduced))} tokens")

                    # S3.2: Publish event
                    if self.event_bus:
                        self.event_bus.publish("session_split_triggered", {
                            "task_id": task_id,
                            "iteration": iteration,
                            "trigger": str(split_trigger),
                            "split_count": split_count
                        })

                    # Return checkpoint for next session
                    return {
                        "status": "split_executed",
                        "task_id": task_id,
                        "iteration": iteration,
                        "split_count": split_count,
                        "checkpoint": checkpoint.to_dict() if hasattr(checkpoint, 'to_dict') else None
                    }

                # Normal execution: advance iteration
                # (In real system, this runs LLM turns, task logic, etc.)

            # All iterations complete
            return {
                "status": "completed",
                "task_id": task_id,
                "total_iterations": iteration,
                "total_splits": split_count,
                "final_context": current_context
            }

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            raise

    def resume_from_checkpoint(self, checkpoint: Checkpoint,
                               new_context: Dict[str, Any]) -> Dict[str, Any]:
        """Resume from checkpoint after split (S2.2 integration)"""
        logger.info(f"Resuming from checkpoint: {checkpoint.task_id}")

        # S2.2: Recover full state
        recovered = self.recovery_engine.recover_from_checkpoint(checkpoint)

        # Merge recovered context with new input
        merged_context = {**recovered, **new_context}

        # Continue execution with recovered state
        plan = SessionExecutionPlan(task_id=checkpoint.task_id)
        return self.execute_task(
            task_id=checkpoint.task_id,
            initial_context=merged_context,
            plan=plan
        )

    def set_event_bus(self, event_bus):
        """Inject event bus for S3.2 integration"""
        self.event_bus = event_bus
