"""
ADR-0528 Phase 3: Learning Loop Integration with ADR-0314

Wires context filter decisions into the learning infrastructure.
User feedback shapes future scoring decisions (learned scores override static).
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import json


@dataclass(frozen=True)
class FilterFeedback:
    """User feedback on filter decision (immutable, audit-ready)."""
    block_id: str
    was_useful: bool  # User found this block helpful
    feedback_weight: float  # 0.0–1.0 (confidence in feedback)
    timestamp: datetime
    user_id: Optional[str] = None
    session_id: Optional[str] = None


class ContextFilterLearner:
    """
    Learning adapter for ADR-0314 integration.

    Takes user feedback on filtered context and updates scoring weights.
    """

    def __init__(self):
        self.feedback_history: List[FilterFeedback] = []
        self.learned_scores: Dict[str, float] = {}  # category → learned_score
        self.convergence_rate = 0.0

    def record_feedback(self, feedback: FilterFeedback) -> None:
        """Record user feedback on filter decision."""
        self.feedback_history.append(feedback)

    def compute_learned_scores(
        self,
        feedback_list: List[FilterFeedback],
        decay_factor: float = 0.95,  # Exponential decay for old feedback
    ) -> Dict[str, float]:
        """
        Compute learned scores from feedback history.

        Algorithm (simplified):
          1. Group feedback by block_id
          2. Weight recent feedback higher (decay old feedback)
          3. Compute average usefulness per block category
          4. Adjust static scores: learned_score = static + delta
        """
        if not feedback_list:
            return {}

        # Group by category (inferred from block_id pattern)
        category_scores = {}

        for i, feedback in enumerate(feedback_list):
            # Weight recent feedback higher
            age_weight = decay_factor ** (len(feedback_list) - i - 1)
            actual_weight = feedback.feedback_weight * age_weight

            # Extract category from block_id (heuristic)
            category = self._infer_category(feedback.block_id)
            if category not in category_scores:
                category_scores[category] = []

            # Score: +1 if useful, -1 if not useful
            usefulness_signal = 1.0 if feedback.was_useful else -1.0
            category_scores[category].append(usefulness_signal * actual_weight)

        # Compute average usefulness per category
        learned_scores = {}
        for category, signals in category_scores.items():
            avg_signal = sum(signals) / len(signals)  # -1.0 to +1.0
            # Adjust static score by learned delta
            # Example: session_state (0.5) + high feedback → 0.65
            delta = avg_signal * 0.15  # Max adjustment: ±0.15
            learned_scores[category] = min(1.0, max(0.0, 0.5 + delta))

        self.learned_scores = learned_scores
        self._compute_convergence_rate(feedback_list)
        return learned_scores

    def get_adjusted_score(self, category: str, static_score: float) -> float:
        """
        Get score for category, using learned score if available.

        Priority:
          1. Learned score (from feedback)
          2. Static score (fallback)
        """
        if category in self.learned_scores:
            return self.learned_scores[category]
        return static_score

    def _infer_category(self, block_id: str) -> str:
        """Infer category from block_id (heuristic)."""
        if "task" in block_id.lower():
            return "task_history"
        elif "session" in block_id.lower():
            return "session_state"
        elif "user" in block_id.lower() or "profile" in block_id.lower():
            return "user_profile"
        elif "system" in block_id.lower():
            return "system_messages"
        elif "conversation" in block_id.lower() or "recall" in block_id.lower():
            return "conversation_recall"
        return "unknown"

    def _compute_convergence_rate(self, feedback_list: List[FilterFeedback]) -> None:
        """Compute convergence rate (how stable are the learned scores?)."""
        if len(feedback_list) < 10:
            self.convergence_rate = 0.0
            return

        # Sample convergence: compare first half vs second half of feedback
        mid = len(feedback_list) // 2
        first_half_avg = sum(
            1.0 if f.was_useful else -1.0
            for f in feedback_list[:mid]
        ) / max(mid, 1)

        second_half_avg = sum(
            1.0 if f.was_useful else -1.0
            for f in feedback_list[mid:]
        ) / max(len(feedback_list) - mid, 1)

        # Convergence: how close are the halves?
        # 0.0 = completely different, 1.0 = identical
        self.convergence_rate = 1.0 - abs(first_half_avg - second_half_avg)


class LearningAuditEvent:
    """Audit event for learning decisions (ADR-0314)."""

    @staticmethod
    def feedback_recorded(
        feedback: FilterFeedback,
        tenant_id: str = "_default",
    ) -> Dict:
        """Create audit event for feedback."""
        return {
            "event_type": "context_filter_feedback_received",
            "tenant_id": tenant_id,
            "timestamp": feedback.timestamp.isoformat(),
            "block_id": feedback.block_id,
            "was_useful": feedback.was_useful,
            "feedback_weight": feedback.feedback_weight,
            "user_id": feedback.user_id,
            "session_id": feedback.session_id,
        }

    @staticmethod
    def score_updated(
        category: str,
        old_score: float,
        new_score: float,
        reason: str,
        tenant_id: str = "_default",
    ) -> Dict:
        """Create audit event for score update (learning)."""
        return {
            "event_type": "context_filter_score_learned",
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat(),
            "category": category,
            "old_score": old_score,
            "new_score": new_score,
            "delta": new_score - old_score,
            "reason": reason,
        }


# Integration hook: called after each routing decision to log feedback
def on_routing_feedback(
    blocks_used: List[str],  # block_ids that were included
    blocks_filtered: List[str],  # block_ids that were filtered
    routing_confidence: float,  # Claude's output confidence
    learner: ContextFilterLearner,
) -> List[FilterFeedback]:
    """
    Post-routing feedback: capture what was useful.

    Called after routing decision to assess filter effectiveness.
    Blocks that led to high-confidence routing → marked useful.
    Blocks that were filtered but high-confidence routing → filtering was correct.
    """
    feedbacks = []

    # Heuristic: if routing confidence high, used blocks were useful
    if routing_confidence > 0.8:
        for block_id in blocks_used:
            feedbacks.append(FilterFeedback(
                block_id=block_id,
                was_useful=True,
                feedback_weight=routing_confidence,  # High confidence = strong signal
                timestamp=datetime.utcnow(),
            ))

    # Filtered blocks: if routing still confident, filtering was correct
    if routing_confidence > 0.7 and blocks_filtered:
        for block_id in blocks_filtered:
            feedbacks.append(FilterFeedback(
                block_id=block_id,
                was_useful=False,  # Filtering was correct
                feedback_weight=routing_confidence * 0.5,  # Weaker signal
                timestamp=datetime.utcnow(),
            ))

    return feedbacks
