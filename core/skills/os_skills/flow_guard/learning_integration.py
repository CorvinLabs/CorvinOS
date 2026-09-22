"""
Flow Guard Learning Integration (ADR-0314)

Integrates Flow Guard with the learning infrastructure:
- Feedback → policy updates
- Confidence scoring
- Outcome tracking
- Audit trail emission

This module bridges FlowGuard with the core learning loop.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List
from enum import Enum
import json
import logging
from uuid import uuid4

from .flow_guard import FlowGuard, FlowEvaluation
from .flow_policy import FlowDecision, FlowOutcome

logger = logging.getLogger(__name__)


class FeedbackType(str, Enum):
    """Types of feedback for flow decisions."""

    OUTCOME_SUCCESS = "outcome_success"  # Flow succeeded, no leak
    OUTCOME_LEAK_DETECTED = "outcome_leak_detected"  # PII leaked
    OUTCOME_ERROR = "outcome_error"  # API error
    OPERATOR_APPROVAL = "operator_approval"  # Operator approved flow
    OPERATOR_REJECTION = "operator_rejection"  # Operator rejected flow


@dataclass
class LearningEvent:
    """
    Learning event for skill feedback.

    Immutable and audit-trail compatible (ADR-0314).
    """

    tenant_id: str
    skill_id: str = "os.flow_guard"
    feedback_type: FeedbackType = field(default=FeedbackType.OUTCOME_SUCCESS)
    data_class: str = ""
    destination_engine: str = ""
    result: str = ""  # "success" | "pii_leak_detected" | "error"
    confidence_before: float = 0.0
    confidence_after: float = 0.0
    reasoning: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str = field(default_factory=lambda: str(uuid4()))
    lom: str = "learning_integration.LearningEvent"  # Line of Moral Responsibility

    def to_audit_dict(self) -> dict:
        """Convert to audit event format."""
        return {
            "event_type": "skill_feedback",
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "feedback_type": self.feedback_type.value,
            "data_class": self.data_class,
            "destination_engine": self.destination_engine,
            "result": self.result,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "lom": self.lom,
        }


class LearningIntegration:
    """
    Integrates Flow Guard with ADR-0314 learning infrastructure.

    Responsibilities:
      1. Consume flow outcomes (success/leak/error)
      2. Update policy confidence based on feedback
      3. Track confidence history
      4. Emit audit events (ADR-0232)
      5. Compute confidence scores for routing decisions
    """

    def __init__(
        self,
        tenant_id: str,
        flow_guard: FlowGuard,
        audit_backend=None,
    ):
        """
        Initialize learning integration.

        Args:
            tenant_id: Tenant ID (required)
            flow_guard: FlowGuard instance to update
            audit_backend: Optional audit backend for event logging
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("tenant_id is required and must be a non-empty string")

        self.tenant_id = tenant_id
        self.flow_guard = flow_guard
        self.audit_backend = audit_backend
        self.feedback_history: List[LearningEvent] = []

    def process_flow_outcome(
        self,
        evaluation: FlowEvaluation,
        outcome_result: str,
        reasoning: str = "",
    ) -> LearningEvent:
        """
        Process a flow outcome and update policy.

        Args:
            evaluation: Original FlowEvaluation
            outcome_result: "success" | "pii_leak_detected" | "error"
            reasoning: Optional explanation

        Returns:
            LearningEvent (immutable, audit-trail compatible)
        """
        if not evaluation or not evaluation.data_class:
            raise ValueError("evaluation must have valid data_class")

        # Get current confidence before update
        confidence_before = evaluation.policy_confidence

        # Update policy based on outcome
        self.flow_guard.record_outcome(
            data_class=evaluation.data_class,
            destination_engine=evaluation.destination_engine,
            result=outcome_result,
            reasoning=reasoning,
        )

        # Get new confidence after update
        new_eval = self.flow_guard.evaluate_flow(
            data="",  # Dummy; we care about policy, not classification
            destination_engine=evaluation.destination_engine,
            context={"data_class": evaluation.data_class},
        )
        confidence_after = new_eval.policy_confidence

        # Create immutable learning event
        event = LearningEvent(
            tenant_id=self.tenant_id,
            feedback_type=self._map_outcome_to_feedback_type(outcome_result),
            data_class=evaluation.data_class,
            destination_engine=evaluation.destination_engine,
            result=outcome_result,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            reasoning=reasoning or f"Flow {outcome_result}",
            lom="learning_integration.LearningIntegration.process_flow_outcome:L120",
        )

        # Record in history
        self.feedback_history.append(event)

        # Emit audit event
        if self.audit_backend:
            self.audit_backend.write_event(event.to_audit_dict())

        # Log structured event
        logger.info(
            f"Flow outcome processed: {evaluation.data_class} → "
            f"{evaluation.destination_engine}, result={outcome_result}, "
            f"confidence {confidence_before:.2f} → {confidence_after:.2f}",
            extra=event.to_audit_dict(),
        )

        return event

    def process_operator_feedback(
        self,
        data_class: str,
        destination_engine: str,
        approval: bool,
        reasoning: str = "",
    ) -> LearningEvent:
        """
        Process operator feedback (approval/rejection).

        Args:
            data_class: Data classification
            destination_engine: Destination engine
            approval: True=approved, False=rejected
            reasoning: Operator's reasoning

        Returns:
            LearningEvent (immutable)
        """
        if not data_class or not destination_engine:
            raise ValueError("data_class and destination_engine are required")

        # Get current policy confidence
        policy = self.flow_guard.get_policy()
        confidence_before = 0.5  # Default if no existing rule

        for rule in policy.rules:
            if (rule.data_class == data_class and
                rule.destination_engine == destination_engine):
                confidence_before = rule.confidence
                break

        # Update policy based on operator decision
        if approval:
            # Operator approved: increase allow confidence
            self.flow_guard.record_outcome(
                data_class=data_class,
                destination_engine=destination_engine,
                result="success",
                reasoning=f"Operator approved: {reasoning}",
            )
            feedback_type = FeedbackType.OPERATOR_APPROVAL
        else:
            # Operator rejected: increase deny confidence
            self.flow_guard.record_outcome(
                data_class=data_class,
                destination_engine=destination_engine,
                result="pii_leak_detected",
                reasoning=f"Operator rejected: {reasoning}",
            )
            feedback_type = FeedbackType.OPERATOR_REJECTION

        # Get new confidence
        new_policy = self.flow_guard.get_policy()
        confidence_after = confidence_before

        for rule in new_policy.rules:
            if (rule.data_class == data_class and
                rule.destination_engine == destination_engine):
                confidence_after = rule.confidence
                break

        # Create learning event
        event = LearningEvent(
            tenant_id=self.tenant_id,
            feedback_type=feedback_type,
            data_class=data_class,
            destination_engine=destination_engine,
            result="approved" if approval else "rejected",
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            reasoning=reasoning,
            lom="learning_integration.LearningIntegration.process_operator_feedback:L195",
        )

        # Record in history
        self.feedback_history.append(event)

        # Emit audit event
        if self.audit_backend:
            self.audit_backend.write_event(event.to_audit_dict())

        logger.info(
            f"Operator feedback processed: {data_class} → {destination_engine}, "
            f"decision={approval}, confidence {confidence_before:.2f} → {confidence_after:.2f}",
            extra=event.to_audit_dict(),
        )

        return event

    def compute_confidence_score(self) -> Dict[str, float]:
        """
        Compute overall confidence score for skill routing.

        Returns:
            Dict with:
              - "overall": aggregate confidence (0.0-1.0)
              - "allow": avg allow confidence
              - "deny": avg deny confidence
              - "feedback_count": num feedback events
        """
        if not self.feedback_history:
            return {
                "overall": 0.5,  # Neutral
                "allow": 0.5,
                "deny": 0.5,
                "feedback_count": 0,
            }

        # Compute averages
        allow_confidences = [
            e.confidence_after for e in self.feedback_history
            if e.result == "success"
        ]
        deny_confidences = [
            e.confidence_after for e in self.feedback_history
            if e.result in ["pii_leak_detected", "rejected"]
        ]

        allow_avg = sum(allow_confidences) / len(allow_confidences) if allow_confidences else 0.5
        deny_avg = sum(deny_confidences) / len(deny_confidences) if deny_confidences else 0.5

        # Overall: bias toward deny (safer)
        overall = (allow_avg * 0.4 + deny_avg * 0.6) if deny_confidences else allow_avg

        return {
            "overall": overall,
            "allow": allow_avg,
            "deny": deny_avg,
            "feedback_count": len(self.feedback_history),
        }

    def get_feedback_history(self) -> List[LearningEvent]:
        """Get all feedback events (immutable)."""
        return list(self.feedback_history)

    def export_feedback_as_json(self) -> str:
        """Export feedback history as JSON."""
        events = [
            {
                **e.to_audit_dict(),
                "event_id": e.event_id,
            }
            for e in self.feedback_history
        ]
        return json.dumps(events, indent=2)

    @staticmethod
    def _map_outcome_to_feedback_type(outcome_result: str) -> FeedbackType:
        """Map outcome string to FeedbackType."""
        mapping = {
            "success": FeedbackType.OUTCOME_SUCCESS,
            "pii_leak_detected": FeedbackType.OUTCOME_LEAK_DETECTED,
            "error": FeedbackType.OUTCOME_ERROR,
        }
        return mapping.get(outcome_result, FeedbackType.OUTCOME_SUCCESS)
