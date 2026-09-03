"""Phase 3: Confidence Scorer (ADR-0315).

Computes relevance + reliability scores for Skill decisions.
"""

from typing import Optional

from core.learning.confidence_events import ConfidenceEvent


class ConfidenceScorer:
    """Computes confidence scores for Skill decisions.

    Two components:
    1. Relevance (0.0-1.0): How relevant was this decision to the user's task?
    2. Reliability (0.0-1.0): How confident are we in this decision?

    Combined score: 0.4 * relevance + 0.6 * reliability (reliability weighted higher)
    """

    @staticmethod
    def compute_relevance(
        skill_id: str,
        feedback_count: int = 0,
        user_feedback_positive: bool = True,
    ) -> float:
        """Compute relevance score (0.0-1.0)."""
        relevance = 0.5  # Neutral base

        if feedback_count > 0:
            relevance += 0.3 if user_feedback_positive else -0.3
            if feedback_count > 1:
                relevance += 0.1

        return max(0.0, min(1.0, relevance))

    @staticmethod
    def compute_reliability(
        execution_latency_ms: float,
        error_count: int = 0,
        consistency_factor: float = 1.0,
    ) -> float:
        """Compute reliability score (0.0-1.0)."""
        reliability = consistency_factor * 0.7

        if execution_latency_ms < 100:
            reliability += 0.2
        elif execution_latency_ms < 500:
            reliability += 0.1

        reliability -= error_count * 0.1

        return max(0.0, min(1.0, reliability))

    @staticmethod
    def score_decision(
        skill_id: str,
        tenant_id: str,
        decision_id: str = "",
        execution_latency_ms: float = 100.0,
        user_feedback_positive: Optional[bool] = None,
        feedback_count: int = 0,
        error_count: int = 0,
        consistency_factor: float = 0.8,
        lom: Optional[str] = None,
    ) -> ConfidenceEvent:
        """Compute full confidence event for a Skill decision."""
        relevance = ConfidenceScorer.compute_relevance(
            skill_id=skill_id,
            feedback_count=feedback_count,
            user_feedback_positive=user_feedback_positive or True,
        )

        reliability = ConfidenceScorer.compute_reliability(
            execution_latency_ms=execution_latency_ms,
            error_count=error_count,
            consistency_factor=consistency_factor,
        )

        return ConfidenceEvent.create(
            skill_id=skill_id,
            tenant_id=tenant_id,
            relevance_score=relevance,
            reliability_score=reliability,
            decision_id=decision_id,
            feedback_count=feedback_count,
            reasoning=f"Relevance: {relevance:.2f}, Reliability: {reliability:.2f}",
            lom=lom,
        )
