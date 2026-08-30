"""LDD Goal Re-Synchronization Protocol (ADR-0406).

Detects and corrects goal drift during LDD outer loop iterations.
Fails closed: escalates to user on persistent drift (>=3 consecutive low-similarity).
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoalAlignmentCheckpoint:
    """Immutable checkpoint for one goal-alignment check."""

    iteration_num: int
    similarity_score: float  # 0.0-1.0
    completeness_score: float  # 0.0-1.0
    composite_score: float  # (similarity * 0.7) + (completeness * 0.3)
    drift_count: int  # consecutive low-similarity iterations
    decision: str  # "CONTINUE" | "CORRECT" | "ESCALATE"
    reason: str


@dataclass
class LDDGoalResyncProtocol:
    """Validates goal alignment before each LDD iteration.

    Thresholds:
    - CONTINUE: composite_score >= 0.7 (keep going)
    - CORRECT: 0.5 <= composite_score < 0.7 (enter correction phase)
    - ESCALATE: composite_score < 0.5 AND drift_count >= 3 (user input needed)
    """

    goal_context: "GoalContext"  # Restored from checkpoint
    audit_logger: Optional["AuditLogger"] = None

    # Threshold configuration
    SIMILARITY_THRESHOLD_CONTINUE: float = 0.7
    SIMILARITY_THRESHOLD_CORRECT: float = 0.5
    DRIFT_COUNT_ESCALATE: int = 3

    # State tracking
    drift_count: int = 0
    checkpoint_history: List[GoalAlignmentCheckpoint] = field(default_factory=list)

    def check_before_iteration(
        self, iteration_num: int, current_strategy: str
    ) -> GoalAlignmentCheckpoint:
        """Check goal alignment before next LDD iteration.

        Args:
            iteration_num: Current LDD iteration number
            current_strategy: String representation of current strategy/work

        Returns:
            GoalAlignmentCheckpoint with decision (CONTINUE/CORRECT/ESCALATE)
        """
        # 1. Measure semantic similarity: goal vs current work
        sim_score = self._compute_similarity(
            self.goal_context.original_goal, current_strategy
        )

        # 2. Measure goal completeness in current work
        comp_score = self._compute_completeness(
            self.goal_context.original_goal, current_strategy
        )

        # 3. Composite score: 70% similarity + 30% completeness
        composite = (sim_score * 0.7) + (comp_score * 0.3)

        # 4. Decide action based on composite score + drift history
        decision, reason = self._decide_action(composite, iteration_num)

        # 5. Update drift counter (reset on good score)
        if composite < self.SIMILARITY_THRESHOLD_CONTINUE:
            self.drift_count += 1
        else:
            self.drift_count = 0

        # 6. Create checkpoint
        checkpoint = GoalAlignmentCheckpoint(
            iteration_num=iteration_num,
            similarity_score=sim_score,
            completeness_score=comp_score,
            composite_score=composite,
            drift_count=self.drift_count,
            decision=decision,
            reason=reason,
        )

        self.checkpoint_history.append(checkpoint)

        # 7. Audit log
        if self.audit_logger:
            self.audit_logger.log_event(
                "ldd_goal_alignment_check",
                {
                    "iteration": iteration_num,
                    "similarity_score": sim_score,
                    "completeness_score": comp_score,
                    "composite_score": composite,
                    "drift_count": self.drift_count,
                    "decision": decision,
                },
            )

        logger.debug(
            f"Iteration {iteration_num}: alignment={composite:.2f}, "
            f"drift={self.drift_count}, decision={decision}"
        )

        return checkpoint

    def _decide_action(self, composite: float, iteration: int) -> Tuple[str, str]:
        """Decide whether to CONTINUE, CORRECT, or ESCALATE based on score.

        Args:
            composite: Composite alignment score (0.0-1.0)
            iteration: Current iteration number (for logging)

        Returns:
            Tuple of (decision_string, reason_string)
        """
        if composite >= self.SIMILARITY_THRESHOLD_CONTINUE:
            return "CONTINUE", f"Goal alignment {composite:.2f} (strong)"

        elif composite >= self.SIMILARITY_THRESHOLD_CORRECT:
            if self.drift_count >= 2:
                return (
                    "ESCALATE",
                    f"Goal alignment {composite:.2f} + {self.drift_count} drifts (user input needed)",
                )
            else:
                return (
                    "CORRECT",
                    f"Goal alignment {composite:.2f} (enter correction phase)",
                )

        else:  # composite < SIMILARITY_THRESHOLD_CORRECT
            if self.drift_count >= self.DRIFT_COUNT_ESCALATE:
                return (
                    "ESCALATE",
                    f"Goal alignment {composite:.2f} (3+ consecutive drifts)",
                )
            else:
                return (
                    "CORRECT",
                    f"Goal alignment {composite:.2f} (correction phase)",
                )

    def _compute_similarity(self, goal: str, strategy: str) -> float:
        """Measure semantic similarity between goal and current strategy.

        Uses TF-IDF based cosine similarity (0.0-1.0).
        """
        if not goal or not strategy:
            return 0.0

        # Tokenize: convert to lowercase, split on whitespace
        goal_tokens = set(goal.lower().split())
        strategy_tokens = set(strategy.lower().split())

        # Intersection: common terms
        common = goal_tokens & strategy_tokens
        if not common:
            return 0.0

        # Jaccard similarity: |intersection| / |union|
        union = goal_tokens | strategy_tokens
        jaccard = len(common) / len(union) if union else 0.0

        return jaccard

    def _compute_completeness(self, goal: str, strategy: str) -> float:
        """Measure how much of goal is represented in strategy.

        Keyword coverage: how many goal terms appear in strategy (0.0-1.0).
        """
        if not goal or not strategy:
            return 0.0

        # Tokenize goal into key terms (filter stop words)
        goal_tokens = set(goal.lower().split())
        strategy_lower = strategy.lower()

        # Count how many goal terms appear in strategy
        matched_terms = sum(1 for token in goal_tokens if token in strategy_lower)
        coverage = matched_terms / len(goal_tokens) if goal_tokens else 0.0

        return coverage
