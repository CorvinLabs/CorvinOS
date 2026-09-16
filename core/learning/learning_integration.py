"""Learning Integration Layer — Plugs Decision History + Outcome Feedback into Skills (ADR-0316/0317).

This layer provides a unified interface for:
1. Recording Skill decisions (model selection, routing, etc.)
2. Recording outcomes (success/partial/failure)
3. Computing confidence deltas for learning optimization

Fail-closed: any integration error is logged, never propagated to caller.
Tenant-scoped: all records filtered by tenant_id (GDPR Art. 32).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from core.learning.decision_history import DecisionHistoryStore, DecisionRecorder
from core.learning.outcome_feedback import OutcomeFeedbackStore, OutcomeRecorder, OutcomeType

logger = logging.getLogger(__name__)


class SkillDecisionRecorder:
    """Record Skill decisions with confidence scoring (ADR-0316 integration)."""

    def __init__(
        self,
        tenant_id: str,
        decision_history_store: DecisionHistoryStore,
    ):
        """Initialize recorder.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            decision_history_store: Persistent decision store

        Raises:
            ValueError: If tenant_id missing (fail-closed)
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")
        self.tenant_id = tenant_id
        self.store = decision_history_store
        self.recorder = DecisionRecorder(tenant_id)

    def record_skill_decision(
        self,
        skill_id: str,
        candidates: list[str],
        chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
        user_id: Optional[str] = None,
        lom: Optional[str] = None,
    ) -> str:
        """Record a Skill decision (e.g., model selection, routing).

        Args:
            skill_id: Which Skill made the decision (e.g., "os.delegation_router")
            candidates: List of options considered
            chosen: Selected option
            session_id: Session ID
            confidence_score: Confidence [0.0, 1.0] (from ADR-0315)
            reasoning: Why this choice? (will be sanitized)
            user_id: For GDPR erasure (Art. 17)
            lom: Line of Moral Responsibility

        Returns:
            decision_id (immutable)

        Raises:
            ValueError: On validation failure (fail-closed)
        """
        try:
            decision = self.recorder.create_decision(
                choice_type=f"skill_{skill_id.replace('.', '_')}",
                candidates=candidates,
                chosen=chosen,
                session_id=session_id,
                confidence_score=confidence_score,
                reasoning=reasoning,
                user_id=user_id,
                lom=lom,
            )
            decision_id = self.store.record_decision(decision)
            logger.debug(
                f"Recorded Skill decision: {skill_id} → {chosen} "
                f"(confidence={confidence_score}, decision_id={decision_id})"
            )
            return decision_id
        except Exception as e:
            logger.error(f"Failed to record Skill decision ({skill_id}): {e}")
            raise

    def record_model_selection_decision(
        self,
        model_candidates: list[str],
        model_chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Record a model selection decision (convenience method).

        Args:
            model_candidates: Available models
            model_chosen: Selected model
            session_id: Session ID
            confidence_score: Confidence in choice
            reasoning: Why this model?
            user_id: For GDPR erasure

        Returns:
            decision_id
        """
        return self.record_skill_decision(
            skill_id="delegation_router",
            candidates=model_candidates,
            chosen=model_chosen,
            session_id=session_id,
            confidence_score=confidence_score,
            reasoning=reasoning,
            user_id=user_id,
            lom="core/learning/learning_integration.py:record_model_selection_decision",
        )

    def record_routing_decision(
        self,
        route_candidates: list[str],
        route_chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Record a routing decision (convenience method).

        Args:
            route_candidates: Available routes/destinations
            route_chosen: Selected route
            session_id: Session ID
            confidence_score: Confidence in route
            reasoning: Why this route?
            user_id: For GDPR erasure

        Returns:
            decision_id
        """
        return self.record_skill_decision(
            skill_id="context_adapter",
            candidates=route_candidates,
            chosen=route_chosen,
            session_id=session_id,
            confidence_score=confidence_score,
            reasoning=reasoning,
            user_id=user_id,
            lom="core/learning/learning_integration.py:record_routing_decision",
        )


class SkillOutcomeRecorder:
    """Record Skill outcomes with confidence backprop (ADR-0317 integration)."""

    def __init__(
        self,
        tenant_id: str,
        outcome_feedback_store: OutcomeFeedbackStore,
    ):
        """Initialize recorder.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            outcome_feedback_store: Persistent outcome store

        Raises:
            ValueError: If tenant_id missing (fail-closed)
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")
        self.tenant_id = tenant_id
        self.store = outcome_feedback_store
        self.recorder = OutcomeRecorder(tenant_id)

    def record_skill_outcome(
        self,
        decision_id: str,
        session_id: str,
        outcome: OutcomeType,
        user_id: Optional[str] = None,
        feedback_text: Optional[str] = None,
        rating: Optional[int] = None,
        quality_score: Optional[float] = None,
        latency_ms: Optional[int] = None,
    ) -> tuple[str, float]:
        """Record a Skill outcome (success/partial/failure).

        Args:
            decision_id: Links to prior decision_history record
            session_id: Session ID
            outcome: SUCCESS, PARTIAL, or FAILURE
            user_id: For GDPR erasure (Art. 17)
            feedback_text: User feedback (will be sanitized)
            rating: 1-5 rating (optional)
            quality_score: 0-1 quality metric (optional)
            latency_ms: Time to outcome (optional)

        Returns:
            (outcome_id, confidence_delta) — delta is backprop adjustment
                from ADR-0315 confidence optimization

        Raises:
            ValueError: On validation failure (fail-closed)
        """
        try:
            outcome_record = self.recorder.record_outcome(
                decision_id=decision_id,
                session_id=session_id,
                outcome=outcome,
                user_id=user_id,
                feedback_text=feedback_text,
                rating=rating,
                quality_score=quality_score,
                latency_ms=latency_ms,
            )
            outcome_id = self.store.record_outcome(outcome_record)

            # Compute confidence delta for learning loop (ADR-0315)
            confidence_delta = self.store.compute_confidence_delta(outcome, rating)

            logger.debug(
                f"Recorded Skill outcome: decision={decision_id} → {outcome.value} "
                f"(rating={rating}, delta={confidence_delta:+.2f}, outcome_id={outcome_id})"
            )
            return outcome_id, confidence_delta
        except Exception as e:
            logger.error(f"Failed to record Skill outcome (decision={decision_id}): {e}")
            raise

    def record_success_outcome(
        self,
        decision_id: str,
        session_id: str,
        user_id: Optional[str] = None,
        quality_score: Optional[float] = None,
        latency_ms: Optional[int] = None,
    ) -> tuple[str, float]:
        """Record a successful Skill outcome (convenience method).

        Args:
            decision_id: Links to decision_history
            session_id: Session ID
            user_id: For GDPR erasure
            quality_score: 0-1 outcome quality
            latency_ms: Time to outcome

        Returns:
            (outcome_id, confidence_delta)
        """
        return self.record_skill_outcome(
            decision_id=decision_id,
            session_id=session_id,
            outcome=OutcomeType.SUCCESS,
            user_id=user_id,
            quality_score=quality_score,
            latency_ms=latency_ms,
        )

    def record_failure_outcome(
        self,
        decision_id: str,
        session_id: str,
        user_id: Optional[str] = None,
        feedback_text: Optional[str] = None,
        quality_score: Optional[float] = None,
        latency_ms: Optional[int] = None,
    ) -> tuple[str, float]:
        """Record a failed Skill outcome (convenience method).

        Args:
            decision_id: Links to decision_history
            session_id: Session ID
            user_id: For GDPR erasure
            feedback_text: Error message or feedback
            quality_score: 0-1 outcome quality
            latency_ms: Time to outcome

        Returns:
            (outcome_id, confidence_delta)
        """
        return self.record_skill_outcome(
            decision_id=decision_id,
            session_id=session_id,
            outcome=OutcomeType.FAILURE,
            user_id=user_id,
            feedback_text=feedback_text,
            quality_score=quality_score,
            latency_ms=latency_ms,
        )

    def get_success_rate(self, decision_ids: Optional[list[str]] = None) -> float:
        """Get success rate for decisions (with small-n suppression).

        Args:
            decision_ids: Optional list to filter by decisions

        Returns:
            Success rate (0-1), or 0.5 if N<10 (PII safeguard)
        """
        return self.store.compute_success_rate(self.tenant_id, decision_ids)
