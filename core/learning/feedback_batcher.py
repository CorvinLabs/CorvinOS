"""Feedback Batcher — Gate 3 Implementation (ADR-0676).

Buffers feedback until threshold is met, then triggers optimization.

Trigger conditions:
- At least 10 new feedback samples collected, OR
- At least 1 hour since last optimization trigger

Architecture (event-driven):
1. FeedbackCollector emits FeedbackReceivedEvent
2. FeedbackBatcher listens, buffers feedback
3. When threshold met, emits OptimizationTriggeredEvent
4. Optimizer listens for OptimizationTriggeredEvent
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Callable
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class BatcherState:
    """State of a feedback batch."""
    skill_id: str
    feedback_count: int = 0
    last_triggered_at: Optional[datetime] = None
    buffered_feedback_ids: List[str] = field(default_factory=list)


class FeedbackBatcher:
    """Buffers feedback, triggers optimization on threshold."""

    def __init__(
        self,
        threshold_count: int = 10,
        threshold_time_seconds: int = 3600,  # 1 hour
    ):
        """
        Initialize feedback batcher.

        Args:
            threshold_count: Trigger optimization after N feedback samples
            threshold_time_seconds: Trigger optimization after N seconds (1h default)
        """
        self.threshold_count = threshold_count
        self.threshold_time_seconds = threshold_time_seconds

        # Per-skill state: skill_id → BatcherState
        self.state: Dict[str, BatcherState] = {}

        # Callbacks for optimization trigger
        self.on_trigger_callbacks: List[Callable[[str], None]] = []

    def add_feedback(self, skill_id: str, feedback_id: str) -> bool:
        """
        Add feedback to buffer.

        Returns: True if threshold reached and optimization triggered
        """
        if skill_id not in self.state:
            self.state[skill_id] = BatcherState(skill_id=skill_id)

        state = self.state[skill_id]
        state.feedback_count += 1
        state.buffered_feedback_ids.append(feedback_id)

        logger.debug(
            f"Feedback buffered: skill={skill_id}, count={state.feedback_count}, "
            f"threshold={self.threshold_count}"
        )

        # Check if threshold met
        if self._should_trigger(skill_id):
            self._trigger_optimization(skill_id)
            return True

        return False

    def _should_trigger(self, skill_id: str) -> bool:
        """Check if optimization should be triggered."""
        state = self.state[skill_id]

        # Check count threshold
        if state.feedback_count >= self.threshold_count:
            return True

        # Check time threshold (if at least one was triggered before)
        if state.last_triggered_at:
            elapsed = datetime.now(timezone.utc) - state.last_triggered_at
            if elapsed.total_seconds() >= self.threshold_time_seconds:
                return True

        return False

    def _trigger_optimization(self, skill_id: str) -> None:
        """Trigger optimization for skill."""
        state = self.state[skill_id]

        logger.info(
            f"Optimization triggered: skill={skill_id}, "
            f"feedback_count={state.feedback_count}, buffered={len(state.buffered_feedback_ids)}"
        )

        # Update state
        state.last_triggered_at = datetime.now(timezone.utc)
        state.feedback_count = 0
        state.buffered_feedback_ids = []

        # Call registered callbacks
        for callback in self.on_trigger_callbacks:
            try:
                callback(skill_id)
            except Exception as e:
                logger.error(f"Callback failed for skill {skill_id}: {e}")

    def register_trigger_callback(self, callback: Callable[[str], None]) -> None:
        """Register callback to be called when optimization is triggered."""
        self.on_trigger_callbacks.append(callback)

    def get_state(self, skill_id: str) -> Optional[BatcherState]:
        """Get current batch state for skill."""
        return self.state.get(skill_id)

    def get_all_states(self) -> Dict[str, BatcherState]:
        """Get all batch states."""
        return dict(self.state)

    def reset_state(self, skill_id: Optional[str] = None) -> None:
        """Reset batcher state (useful for testing)."""
        if skill_id:
            if skill_id in self.state:
                del self.state[skill_id]
                logger.debug(f"Reset batcher state for skill: {skill_id}")
        else:
            self.state.clear()
            logger.debug("Reset all batcher states")


# Global singleton
_feedback_batcher = None


def get_feedback_batcher() -> FeedbackBatcher:
    """Get or create feedback batcher singleton."""
    global _feedback_batcher
    if _feedback_batcher is None:
        _feedback_batcher = FeedbackBatcher(threshold_count=10, threshold_time_seconds=3600)
    return _feedback_batcher
