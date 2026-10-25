"""
Flow Guard Learning Integration (ADR-0314, ADR-2283)

Integrates Flow Guard with the learning infrastructure:
- Feedback → policy updates (strengthen-only)
- Confidence tracking
- Audit trail emission (audit-FIRST when a backend is set)

ADR-2283 (2026-10-25) — the console route ``/v1/console/flow/feedback`` is the only
production caller. Its rules, enforced HERE so no caller can route around them:

* **Strengthen-only (ADR-2049 "never weakens").** Feedback may add or raise a DENY rule
  (``rejected``, ``pii_leak_detected``) or record a neutral ``error``. Anything that would
  add or raise an ALLOW rule (``success``, ``approved``) raises
  :class:`FeedbackWouldWeaken`; an unknown result raises :class:`UnknownFeedbackResult`.
* **Audit-first.** With an ``audit_backend`` (the route passes the strict core-chain
  ``CoreAuditBackend``), the record is written BEFORE the policy changes; a write that
  raises leaves the policy and the history untouched.
* **No free text.** Operator reasoning is not kept, not returned and never audited.
* **Real values only.** ``confidence_before`` / ``confidence_after`` are the DENY rule's
  actual confidence, or ``None`` when no such rule exists — never a placeholder.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional, Dict, List
from enum import Enum
import json
import logging
from uuid import uuid4

from .flow_guard import FlowGuard, FlowEvaluation
from .flow_policy import FlowDecision

logger = logging.getLogger(__name__)


class FeedbackType(str, Enum):
    """Types of feedback for flow decisions."""

    OUTCOME_SUCCESS = "outcome_success"  # Flow succeeded, no leak (never applied — would weaken)
    OUTCOME_LEAK_DETECTED = "outcome_leak_detected"  # PII leaked
    OUTCOME_ERROR = "outcome_error"  # API error
    OPERATOR_APPROVAL = "operator_approval"  # Operator approved flow (never applied — would weaken)
    OPERATOR_REJECTION = "operator_rejection"  # Operator rejected flow


#: Results that only ever add/raise a DENY rule or change nothing.
STRENGTHENING_RESULTS: Dict[str, FeedbackType] = {
    "rejected": FeedbackType.OPERATOR_REJECTION,
    "pii_leak_detected": FeedbackType.OUTCOME_LEAK_DETECTED,
    "error": FeedbackType.OUTCOME_ERROR,
}
#: Results that would add or raise an ALLOW rule — refused (ADR-2049, ADR-2283).
WEAKENING_RESULTS = frozenset({"success", "approved"})


class FeedbackWouldWeaken(ValueError):
    """The feedback would add or raise an ALLOW rule (ADR-2283: refused)."""


class UnknownFeedbackResult(ValueError):
    """The feedback result is not one of :data:`STRENGTHENING_RESULTS`."""


@dataclass(frozen=True)
class LearningEvent:
    """
    Learning event for skill feedback.

    Immutable and audit-trail compatible (ADR-0314). Carries no free text.
    """

    tenant_id: str
    skill_id: str = "os.flow_guard"
    feedback_type: FeedbackType = field(default=FeedbackType.OUTCOME_ERROR)
    data_class: str = ""
    destination_engine: str = ""
    result: str = ""  # "rejected" | "pii_leak_detected" | "error"
    rule_effect: str = "none"  # "deny_strengthened" | "none"
    confidence_before: Optional[float] = None  # DENY rule confidence before; None = no rule
    confidence_after: Optional[float] = None  # DENY rule confidence after; None = no rule
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str = field(default_factory=lambda: str(uuid4()))
    audit_ref: str = ""  # the chain record's audit_ref (strict backend), "" otherwise
    lom: str = "learning_integration.LearningEvent"  # Line of Moral Responsibility

    def to_audit_dict(self) -> dict:
        """The audit record — metadata only (ids, closed codes, real numbers).

        ``confidence_after`` is not part of it: the record is written BEFORE the
        policy changes (audit-first), when that value does not exist yet.
        """
        record = {
            "event_type": "skill_feedback",
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "feedback_type": self.feedback_type.value,
            "data_class": self.data_class,
            "destination_engine": self.destination_engine,
            "result": self.result,
            "rule_effect": self.rule_effect,
            "event_id": self.event_id,
            "lom": self.lom,
        }
        if self.confidence_before is not None:
            record["confidence_before"] = self.confidence_before
        return record


class LearningIntegration:
    """
    Integrates Flow Guard with ADR-0314 learning infrastructure.

    Responsibilities:
      1. Consume strengthening feedback (rejected / leak / error)
      2. Update the policy — DENY only, never ALLOW
      3. Track feedback history (metadata only)
      4. Emit audit events, audit-first (ADR-0232, ADR-2283)
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
            audit_backend: Object with ``write_event(dict)``. When set, every feedback
                is written to it BEFORE the policy changes; if it raises, nothing changes.
                The console route passes ``CoreAuditBackend(tenant_id, strict=True)``.
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("tenant_id is required and must be a non-empty string")

        self.tenant_id = tenant_id
        self.flow_guard = flow_guard
        self.audit_backend = audit_backend
        self.feedback_history: List[LearningEvent] = []

    def _deny_confidence(self, data_class: str, destination_engine: str) -> Optional[float]:
        for rule in self.flow_guard.get_policy().rules:
            if (rule.data_class == data_class
                    and rule.destination_engine == destination_engine
                    and rule.decision == FlowDecision.DENY):
                return rule.confidence
        return None

    def record_feedback(
        self,
        data_class: str,
        destination_engine: str,
        result: str,
    ) -> LearningEvent:
        """Apply one strengthen-only feedback, audit-first.

        Raises:
            ValueError: empty data_class / destination_engine
            FeedbackWouldWeaken: ``result`` would add or raise an ALLOW rule
            UnknownFeedbackResult: ``result`` is not a known strengthening result
            Exception from the audit backend: the record did not commit — the policy
                and the history are unchanged
        """
        if not data_class or not destination_engine:
            raise ValueError("data_class and destination_engine are required")
        if result in WEAKENING_RESULTS:
            raise FeedbackWouldWeaken(
                f"feedback {result!r} would add or raise an ALLOW rule; feedback may only strengthen")
        if result not in STRENGTHENING_RESULTS:
            raise UnknownFeedbackResult(f"unknown feedback result {result!r}")

        rule_effect = "none" if result == "error" else "deny_strengthened"
        event = LearningEvent(
            tenant_id=self.tenant_id,
            feedback_type=STRENGTHENING_RESULTS[result],
            data_class=data_class,
            destination_engine=destination_engine,
            result=result,
            rule_effect=rule_effect,
            confidence_before=self._deny_confidence(data_class, destination_engine),
            lom="learning_integration.LearningIntegration.record_feedback",
        )

        # AUDIT-FIRST: no policy change without its record.
        if self.audit_backend is not None:
            ref = self.audit_backend.write_event(event.to_audit_dict())
            event = replace(event, audit_ref=str(ref) if ref else "")

        # The policy learns from outcomes; "rejected" is a DENY signal like a leak.
        self.flow_guard.record_outcome(
            data_class=data_class,
            destination_engine=destination_engine,
            result="error" if result == "error" else "pii_leak_detected",
        )
        event = replace(event, confidence_after=self._deny_confidence(data_class, destination_engine))
        self.feedback_history.append(event)

        logger.info(
            "Flow feedback applied: %s -> %s, result=%s, effect=%s",
            data_class, destination_engine, result, rule_effect,
        )
        return event

    def process_flow_outcome(
        self,
        evaluation: FlowEvaluation,
        outcome_result: str,
        reasoning: str = "",
    ) -> LearningEvent:
        """Strengthen-only outcome feedback for ``evaluation``'s (class, destination).

        ``reasoning`` is accepted for compatibility and DISCARDED (ADR-2283: no free text).
        """
        if not evaluation or not evaluation.data_class:
            raise ValueError("evaluation must have valid data_class")
        return self.record_feedback(evaluation.data_class, evaluation.destination_engine, outcome_result)

    def process_operator_feedback(
        self,
        data_class: str,
        destination_engine: str,
        approval: bool,
        reasoning: str = "",
    ) -> LearningEvent:
        """Operator feedback. A rejection strengthens DENY; an approval would weaken the
        policy and raises :class:`FeedbackWouldWeaken`. ``reasoning`` is discarded."""
        return self.record_feedback(data_class, destination_engine, "approved" if approval else "rejected")

    def compute_confidence_score(self) -> Dict[str, float]:
        """
        Compute overall confidence score for skill routing.

        Returns:
            Dict with:
              - "overall": aggregate confidence (0.0-1.0)
              - "allow": avg allow confidence (always neutral: feedback never raises ALLOW)
              - "deny": avg deny confidence
              - "feedback_count": num feedback events
        """
        deny_confidences = [
            e.confidence_after for e in self.feedback_history
            if e.rule_effect == "deny_strengthened" and e.confidence_after is not None
        ]
        deny_avg = sum(deny_confidences) / len(deny_confidences) if deny_confidences else 0.5
        allow_avg = 0.5
        overall = (allow_avg * 0.4 + deny_avg * 0.6) if deny_confidences else 0.5
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
        """Export feedback history as JSON (metadata only)."""
        events = [
            {**e.to_audit_dict(), "confidence_after": e.confidence_after, "timestamp": e.timestamp}
            for e in self.feedback_history
        ]
        return json.dumps(events, indent=2)
