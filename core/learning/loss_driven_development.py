"""LDD Outer Loop: Loss-Driven Development with Goal Re-Synchronization (ADR-0406).

Orchestrates the outer LDD loop, embedding goal-alignment checks before each iteration.
Decides action (CONTINUE/CORRECT/ESCALATE) based on drift detection.
"""

import logging
from typing import Any, Dict, Optional

from core.session_manager.ldd_goal_resync import LDDGoalResyncProtocol
from core.session_manager.goal_context import GoalContext

logger = logging.getLogger(__name__)


class LDDOuterLoop:
    """Orchestrates LDD with goal re-sync checks.

    Coordinates with LoopEngineer subsystem to apply goal-alignment gates
    before each LDD iteration.
    """

    def __init__(
        self,
        goal_context: GoalContext,
        max_iterations: int = 100,
        audit_logger: Optional[Any] = None,
    ):
        """Initialize LDD outer loop with goal context.

        Args:
            goal_context: GoalContext restored from checkpoint or initialized at start
            max_iterations: Maximum iterations before stopping
            audit_logger: Optional audit logger for compliance (GDPR Art. 30)
        """
        self.goal_context = goal_context
        self.max_iterations = max_iterations
        self.audit_logger = audit_logger

        # Initialize goal re-sync protocol
        self.goal_resync = LDDGoalResyncProtocol(
            goal_context=goal_context, audit_logger=audit_logger
        )

        self.iteration_num = 0
        self.current_strategy = None
        self.is_running = False

    async def run_outer_loop(
        self, initial_strategy: str, on_iterate, on_correct, on_escalate
    ):
        """Run LDD outer loop with goal re-sync checks.

        Args:
            initial_strategy: Initial strategy/approach from k=0
            on_iterate: Async callable(iteration_num, strategy) -> updated_strategy
            on_correct: Async callable(checkpoint) -> corrected_strategy
            on_escalate: Async callable(checkpoint) -> escalation_result

        Flow:
            For each iteration:
            1. Check goal alignment (NEW)
            2. Decide action (CONTINUE/CORRECT/ESCALATE)
            3. If ESCALATE: stop and escalate
            4. If CORRECT: run goal-correction phase
            5. If CONTINUE: normal iteration
        """
        self.is_running = True
        self.current_strategy = initial_strategy

        try:
            for self.iteration_num in range(self.max_iterations):
                # === PHASE 1: Goal Alignment Check (NEW, ADR-0406) ===
                checkpoint = self.goal_resync.check_before_iteration(
                    iteration_num=self.iteration_num,
                    current_strategy=self.current_strategy,
                )

                logger.debug(
                    f"Iteration {self.iteration_num}: "
                    f"alignment={checkpoint.composite_score:.2f}, "
                    f"decision={checkpoint.decision}"
                )

                # === PHASE 2: Decision Branch ===
                if checkpoint.decision == "ESCALATE":
                    logger.warning(
                        f"Goal drift detected at iteration {self.iteration_num}: "
                        f"{checkpoint.reason}"
                    )
                    await on_escalate(checkpoint)
                    break

                elif checkpoint.decision == "CORRECT":
                    logger.info(
                        f"Entering goal-correction phase at iteration {self.iteration_num}"
                    )
                    self.current_strategy = await on_correct(checkpoint)

                # === PHASE 3: Normal Iteration ===
                # (CONTINUE or after correction)
                self.current_strategy = await on_iterate(
                    self.iteration_num, self.current_strategy
                )

        finally:
            self.is_running = False
            logger.info(
                f"LDD outer loop completed at iteration {self.iteration_num} "
                f"(total checkpoints: {len(self.goal_resync.checkpoint_history)})"
            )

    def get_goal_drift_report(self) -> Dict[str, Any]:
        """Generate a report of goal drift throughout the run.

        Returns:
            Dict with keys: total_iterations, drift_detected_at, escalations, timeline
        """
        checkpoints = self.goal_resync.checkpoint_history
        escalations = [cp for cp in checkpoints if cp.decision == "ESCALATE"]
        corrections = [cp for cp in checkpoints if cp.decision == "CORRECT"]

        return {
            "total_iterations": len(checkpoints),
            "total_drift_events": sum(1 for cp in checkpoints if cp.drift_count > 0),
            "escalation_count": len(escalations),
            "correction_count": len(corrections),
            "first_escalation": escalations[0].iteration_num if escalations else None,
            "max_drift_count": max((cp.drift_count for cp in checkpoints), default=0),
            "checkpoints": [
                {
                    "iteration": cp.iteration_num,
                    "score": cp.composite_score,
                    "decision": cp.decision,
                    "drift_count": cp.drift_count,
                }
                for cp in checkpoints
            ],
        }
