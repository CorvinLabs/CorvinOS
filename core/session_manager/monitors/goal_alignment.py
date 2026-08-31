"""GoalAlignmentMonitor: Detect semantic drift from original goal.

Monitors:
- Original goal (provided at session start)
- Current work transcript (updated each iteration)
- Semantic similarity score [0.0-1.0]

Alert: If similarity <0.6 for 3+ consecutive iterations → "goal_drift_detected"

Implementation:
- Simple cosine similarity on lemmatized text (Python stdlib, no models)
- Configurable threshold (default 0.6)
- Configurable consecutive-low-score count (default 3)

ADR-0407: Session Manager Phase 2.2
Depends on: base.MonitorBase
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from .base import MonitorBase, MonitorAlert, AlertType, MonitorConfig, MonitorState

logger = logging.getLogger(__name__)


@dataclass
class GoalAlignmentState(MonitorState):
    """Extended state for GoalAlignmentMonitor."""

    original_goal: str = ""
    consecutive_low_scores: int = 0
    similarity_scores: list[float] = field(default_factory=list)


class GoalAlignmentMonitor(MonitorBase):
    """Detect semantic drift from original goal.

    Measures: Cosine similarity between original goal and current work.
    Alert: If similarity <0.6 for 3+ consecutive iterations.

    Configuration:
    - similarity_threshold: Similarity score threshold [0.0-1.0], default 0.6
    - consecutive_low_count: Number of consecutive low scores to trigger, default 3
    """

    def __init__(self, config: Optional[MonitorConfig] = None):
        """Initialize GoalAlignmentMonitor.

        Args:
            config: Optional configuration
        """
        super().__init__("goal_alignment_monitor", config)
        self.similarity_threshold = 0.6
        self.consecutive_low_count = 3

    def set_goal(self, session_id: str, task_id: str, tenant_id: str, goal: str) -> None:
        """Set original goal for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID
            goal: Original goal text
        """
        state = self.create_or_get_state(session_id, task_id, tenant_id)
        if isinstance(state, GoalAlignmentState):
            state.original_goal = goal
            logger.debug(f"Set original goal for {session_id}: {goal[:100]}...")
        else:
            # Replace with extended state
            extended_state = GoalAlignmentState(
                session_id=session_id,
                task_id=task_id,
                tenant_id=tenant_id,
                original_goal=goal,
            )
            self.session_states[session_id] = extended_state

    def check(self, state: MonitorState) -> Optional[MonitorAlert]:
        """Check for goal drift.

        Args:
            state: MonitorState for the session

        Returns:
            MonitorAlert if goal drift detected, None otherwise
        """
        if not isinstance(state, GoalAlignmentState) or not state.original_goal:
            return None

        current_work = state.metadata.get("current_work", "")
        if not current_work:
            return None

        # Calculate similarity
        similarity = self._calculate_similarity(state.original_goal, current_work)
        state.similarity_scores.append(similarity)

        # Check if similarity is below threshold
        if similarity < self.similarity_threshold:
            state.consecutive_low_scores += 1
            logger.debug(
                f"{self.name}: {state.session_id} similarity={similarity:.2f} "
                f"({state.consecutive_low_scores} consecutive low)"
            )

            # Alert if consecutive low scores >= threshold
            if state.consecutive_low_scores >= self.consecutive_low_count:
                alert = MonitorAlert(
                    alert_type=AlertType.GOAL_DRIFT_DETECTED,
                    session_id=state.session_id,
                    task_id=state.task_id,
                    tenant_id=state.tenant_id,
                    severity="warning",
                    reason=f"Goal drift detected: similarity={similarity:.2f} "
                    f"<{self.similarity_threshold} for {state.consecutive_low_scores} iterations",
                    metadata={
                        "similarity_score": similarity,
                        "original_goal": state.original_goal[:200],
                        "current_work": current_work[:200],
                        "consecutive_low_count": state.consecutive_low_scores,
                        "threshold": self.similarity_threshold,
                    },
                )
                # Reset counter after alert
                state.consecutive_low_scores = 0
                return alert
        else:
            # Reset counter on high score
            state.consecutive_low_scores = 0

        return None

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate cosine similarity between two texts.

        Simple implementation: tokenize, lowercase, split into words,
        calculate Jaccard similarity (intersection / union).

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score [0.0-1.0]
        """
        # Simple word-based Jaccard similarity
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        if union == 0:
            return 0.0

        return intersection / union

    def create_or_get_state(
        self, session_id: str, task_id: str, tenant_id: str
    ) -> GoalAlignmentState:
        """Create or get GoalAlignmentState for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID

        Returns:
            GoalAlignmentState for the session
        """
        if session_id not in self.session_states:
            self.session_states[session_id] = GoalAlignmentState(
                session_id=session_id,
                task_id=task_id,
                tenant_id=tenant_id,
            )
        return self.session_states[session_id]
