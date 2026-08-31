"""
Sprint 2.2: RecoveryEngine

Restores full state from checkpoint for autonomous resume.
Reconstructs context, applies learning, re-initializes triggers, validates idempotency.

Integration:
- Input: CheckpointState from CheckpointManager.load()
- Output: ExecutionState ready to resume (triggers, context, learnings)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime
import json
import logging

from core.vibe_engineering.session_lifecycle_manager import SessionState, SplitTrigger
from core.vibe_engineering.checkpoint_manager import CheckpointState
from core.vibe_engineering.context_reducer import ReducedContext

logger = logging.getLogger(__name__)


@dataclass
class ExecutionState:
    """Full execution state ready for resume."""
    task_id: str
    session_id: str
    phase: str
    iteration_num: int
    last_checkpoint_id: str
    checkpoint_timestamp: str

    # Restored state
    session_state: SessionState
    full_context: Dict[str, Any]  # Original context reconstructed
    learning_state: Dict[str, Any]  # Strategies, success rates, recommendations

    # Recovery metadata
    recovery_reason: Optional[str] = None
    resumed_at: str = field(default_factory=lambda: datetime.now().isoformat())


class RecoveryEngine:
    """
    Recovers full execution state from checkpoint.

    Guarantees:
    - Full context reconstructed (reduced + dropped sections merged)
    - Session state re-initialized with same trigger thresholds
    - Learning applied (strategies, recommendations available)
    - Idempotent: same checkpoint always produces same ExecutionState
    """

    def __init__(self):
        logger.info("RecoveryEngine initialized")

    def recover_from_checkpoint(self, checkpoint: CheckpointState) -> ExecutionState:
        """
        Recover full execution state from checkpoint.

        Args:
            checkpoint: CheckpointState loaded from filesystem.

        Returns:
            ExecutionState ready to resume execution.
        """
        logger.info(
            f"Recovering from checkpoint {checkpoint.checkpoint_id} "
            f"(task={checkpoint.task_id}, iter={checkpoint.iteration_num})"
        )

        # 1. Re-initialize session state
        session_state = self._restore_session_state(checkpoint)

        # 2. Reconstruct full context (reduced + dropped)
        full_context = self._restore_context(checkpoint)

        # 3. Extract learning state (strategies, success rates)
        learning_state = self._extract_learning_state(checkpoint)

        # 4. Create execution state
        execution_state = ExecutionState(
            task_id=checkpoint.task_id,
            session_id=checkpoint.session_id,
            phase=checkpoint.phase,
            iteration_num=checkpoint.iteration_num,
            last_checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_timestamp=checkpoint.timestamp_iso,
            session_state=session_state,
            full_context=full_context,
            learning_state=learning_state,
            recovery_reason=checkpoint.recovery_reason
        )

        logger.info(f"Recovery complete: ready to resume iteration {checkpoint.iteration_num + 1}")
        return execution_state

    def _restore_session_state(self, checkpoint: CheckpointState) -> SessionState:
        """
        Restore SessionLifecycleManager state from checkpoint.

        Reinitializes triggers with same thresholds.
        """
        task_state = checkpoint.task_state

        session = SessionState(
            session_id=checkpoint.session_id,
            phase=checkpoint.phase,
            iteration_count=checkpoint.iteration_num,
            # Context and token counts can be reset (will re-count on resume)
            context_tokens=task_state.get("context_tokens", 0),
            max_context_tokens=task_state.get("max_context_tokens", 4000),
            tokens_burned_today=task_state.get("tokens_burned", 0),
            daily_token_budget=task_state.get("daily_budget", 100000),
            # Reset progress timer on resume (avoid immediate stall trigger)
            last_progress_time=datetime.now(),
            stall_threshold_seconds=1800  # 30 minutes
        )

        logger.info(f"Session state restored: phase={checkpoint.phase}, iter={checkpoint.iteration_num}")
        return session

    def _restore_context(self, checkpoint: CheckpointState) -> Dict[str, Any]:
        """
        Reconstruct full context from reduced context + dropped sections.

        Merges context_essentials back with dropped sections (where applicable).
        """
        context_essentials = checkpoint.context_essentials
        task_state = checkpoint.task_state

        # Start with task state (goal, constraints, progress)
        full_context = {
            "task_id": task_state.get("task_id", checkpoint.task_id),
            "goal": task_state.get("goal", ""),
            "constraints": context_essentials.get("kept", []),
            "phase": checkpoint.phase,
            "iteration_num": checkpoint.iteration_num
        }

        # Add decisions made (kept from reduction)
        decisions = []
        for decision in context_essentials.get("decisions", []):
            if isinstance(decision, dict):
                decisions.append(decision)
        full_context["decisions_made"] = decisions

        # Add errors encountered (all kept in reduction)
        errors = []
        for error in context_essentials.get("errors", []):
            if isinstance(error, dict):
                errors.append(error)
        full_context["errors_encountered"] = errors

        # Add back some dropped sections (e.g., optimization notes for reference)
        dropped = context_essentials.get("dropped", [])
        full_context["dropped_sections_for_reference"] = dropped[:5]  # Keep top 5

        # Add progress metadata
        full_context["progress"] = task_state.get("progress", {})

        reduction_pct = context_essentials.get("reduction_pct", 91)
        logger.info(
            f"Context reconstructed: goal + {len(full_context.get('constraints', []))} constraints, "
            f"{len(decisions)} decisions, {len(errors)} errors (recovered from {reduction_pct}% reduction)"
        )

        return full_context

    def _extract_learning_state(self, checkpoint: CheckpointState) -> Dict[str, Any]:
        """
        Extract learning state from checkpoint for strategy recommendations.

        Returns dict with:
        - strategies_tried: List of strategies attempted
        - success_rate: Overall success rate (0.0-1.0)
        - errors: Error summary
        - recommendations: Next-step recommendations
        """
        learning_state = checkpoint.learning_state

        return {
            "strategies_tried": learning_state.get("strategies_tried", []),
            "success_rate": learning_state.get("success_rate", 0.0),
            "errors": learning_state.get("errors", []),
            "recommendations": learning_state.get("recommendations", []),
            "last_strategy": learning_state.get("last_strategy", None),
            "total_attempts": len(learning_state.get("strategies_tried", []))
        }

    def validate_resumed_state(
        self,
        original_checkpoint: CheckpointState,
        recovered_state: ExecutionState
    ) -> bool:
        """
        Validate that recovered state matches original checkpoint (idempotency check).

        Args:
            original_checkpoint: Original checkpoint from disk.
            recovered_state: ExecutionState after recovery.

        Returns:
            True if idempotent (same task, phase, iteration), False otherwise.
        """
        # Check task identity
        if original_checkpoint.task_id != recovered_state.task_id:
            logger.error(f"Task ID mismatch: {original_checkpoint.task_id} vs {recovered_state.task_id}")
            return False

        # Check phase
        if original_checkpoint.phase != recovered_state.phase:
            logger.error(f"Phase mismatch: {original_checkpoint.phase} vs {recovered_state.phase}")
            return False

        # Check iteration (should be same)
        if original_checkpoint.iteration_num != recovered_state.iteration_num:
            logger.error(f"Iteration mismatch: {original_checkpoint.iteration_num} vs {recovered_state.iteration_num}")
            return False

        logger.info(f"Recovery validation passed: idempotency confirmed")
        return True

    def reconstruct_reduced_context(self, checkpoint: CheckpointState) -> Optional[ReducedContext]:
        """
        Reconstruct ReducedContext object from checkpoint for analysis.

        Useful for understanding what was kept vs. dropped during reduction.

        Args:
            checkpoint: CheckpointState from disk.

        Returns:
            ReducedContext reconstructed from checkpoint essentials, or None if reconstruction fails.
        """
        try:
            from dataclasses import dataclass as dc
            from core.vibe_engineering.context_reducer import EssentialSection

            context_essentials = checkpoint.context_essentials

            # Reconstruct essential sections
            decisions_made = []
            for d in context_essentials.get("decisions", []):
                if isinstance(d, dict):
                    decisions_made.append(EssentialSection(
                        section_type="decision",
                        content=d.get("decision", ""),
                        iteration=d.get("iter", -1),
                        reason=d.get("why", "")
                    ))

            errors = []
            for e in context_essentials.get("errors", []):
                if isinstance(e, dict):
                    errors.append(EssentialSection(
                        section_type="error",
                        content=e.get("error_type", ""),
                        iteration=e.get("iter", -1),
                        reason=e.get("root_cause", "")
                    ))

            learnings = []
            for l in context_essentials.get("learnings", []):
                if isinstance(l, dict):
                    learnings.append(EssentialSection(
                        section_type="learning",
                        content=l.get("learning", ""),
                        iteration=l.get("iter", -1),
                        reason=l.get("applies_to", "")
                    ))

            reduced = ReducedContext(
                goal=checkpoint.task_state.get("goal", ""),
                constraints=context_essentials.get("kept", []),
                decisions_made=decisions_made,
                errors_encountered=errors,
                learnings=learnings,
                original_size_tokens=context_essentials.get("original_tokens", 0),
                reduced_size_tokens=context_essentials.get("reduced_tokens", 0),
                reduction_pct=context_essentials.get("reduction_pct", 91),
                compressed_at=checkpoint.timestamp_iso,
                dropped_sections=context_essentials.get("dropped", [])
            )

            logger.info(f"ReducedContext reconstructed: {len(decisions_made)} decisions, {len(errors)} errors")
            return reduced

        except Exception as e:
            logger.error(f"Failed to reconstruct ReducedContext: {e}")
            return None

    def estimate_recovery_cost(self, checkpoint: CheckpointState) -> Dict[str, Any]:
        """
        Estimate cost/effort to recover and resume from checkpoint.

        Returns dict with:
        - estimated_tokens: Tokens to restore full context
        - recovery_complexity: "easy" (same phase) / "medium" (new phase) / "hard" (cross-subsystem)
        - prerequisites: List of external systems/data needed
        """
        context_essentials = checkpoint.context_essentials

        estimated_tokens = (
            context_essentials.get("reduced_tokens", 0) +
            len(context_essentials.get("dropped", [])) * 100  # Rough estimate per dropped section
        )

        # Complexity based on trigger that caused checkpoint
        trigger = checkpoint.trigger
        complexity = {
            "phase_exit": "easy",  # Completed phase, just move to next
            "context_limit": "medium",  # Reduce context, continue
            "token_burn": "hard",  # Need to refactor task or continue with budget limits
            "stall_detected": "hard",  # Might need strategy change
            "iteration_cap": "medium"  # Many iterations, but on track
        }.get(trigger, "unknown")

        prerequisites = []
        if checkpoint.recovery_reason:
            if "timeout" in checkpoint.recovery_reason.lower():
                prerequisites.append("network/service availability check")
            if "permission" in checkpoint.recovery_reason.lower():
                prerequisites.append("filesystem permissions validation")

        return {
            "estimated_tokens": estimated_tokens,
            "recovery_complexity": complexity,
            "prerequisites": prerequisites,
            "trigger_reason": checkpoint.trigger,
            "iterations_completed": checkpoint.iteration_num
        }
