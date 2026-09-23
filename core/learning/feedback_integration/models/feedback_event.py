"""Stream 4: FeedbackEvent Schema — Unified feedback for all Streams (ADR-2050).

Defines 4 immutable feedback types:
  1. outcome_feedback: Yes/No/Other (was routing/detection correct?)
  2. preference_feedback: LLM/Deterministic/Neither (user preference)
  3. confidence_score: 0–100% (operator confidence in decision)
  4. metric_observed: numeric (latency, cost, accuracy observations)

All events are frozen dataclasses, tenant-scoped (GDPR Art. 32), audit-trail bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class FeedbackType(str, Enum):
    """Four feedback types for Stream 1–3 optimization loops."""
    OUTCOME = "outcome_feedback"  # was decision correct?
    PREFERENCE = "preference_feedback"  # LLM vs deterministic preference
    CONFIDENCE = "confidence_score"  # 0–1 confidence estimate
    METRIC = "metric_observed"  # numeric observations (latency, cost, accuracy)


class OutcomeChoice(str, Enum):
    """Outcome feedback: yes/no/other."""
    YES = "yes"
    NO = "no"
    OTHER = "other"


class PreferenceChoice(str, Enum):
    """Preference feedback: LLM vs deterministic."""
    LLM = "llm"
    DETERMINISTIC = "deterministic"
    NEITHER = "neither"


@dataclass(frozen=True)
class FeedbackEvent:
    """Immutable feedback event (ADR-2050, GDPR Art. 32).

    Fired via POST /v1/console/learning/<skill>/feedback.
    Routed to EventStore (audit-first, tenant-scoped).

    Guarantees:
    - Frozen (immutable after creation)
    - Tenant-scoped (GDPR requirement)
    - Timestamped (audit trail)
    - Audit-trail bound (audit_ref set by EventStore)
    """

    # Metadata
    feedback_id: str  # UUID4
    feedback_type: FeedbackType
    skill_id: str  # "os.workflow_optimizer", "os.security_orchestrator", etc.
    tenant_id: str  # Tenant scope (GDPR requirement)
    timestamp: str  # ISO 8601 UTC
    task_id: Optional[str] = None  # Task this feedback relates to (if applicable)

    # Feedback payload (type-specific)
    outcome: Optional[OutcomeChoice] = None  # For OUTCOME type
    preference: Optional[PreferenceChoice] = None  # For PREFERENCE type
    confidence_score: Optional[float] = None  # For CONFIDENCE type (0–1)
    metric_name: Optional[str] = None  # For METRIC type (e.g., "latency_ms")
    metric_value: Optional[float] = None  # For METRIC type (numeric)

    # Optional metadata
    reason: Optional[str] = None  # Human-readable reason (max 500 chars, never persisted)
    lom: Optional[str] = None  # Line of Moral Responsibility
    audit_ref: Optional[str] = None  # Set by EventStore after audit-chain write

    def __post_init__(self):
        """Validate feedback on creation."""
        if not self.tenant_id:
            raise ValueError("tenant_id is required (GDPR Art. 32)")
        if not self.skill_id:
            raise ValueError("skill_id is required")
        if not self.feedback_id:
            raise ValueError("feedback_id is required")

        # Type-specific validation
        if self.feedback_type == FeedbackType.OUTCOME and not self.outcome:
            raise ValueError("outcome_feedback requires 'outcome' field")
        if self.feedback_type == FeedbackType.PREFERENCE and not self.preference:
            raise ValueError("preference_feedback requires 'preference' field")
        if self.feedback_type == FeedbackType.CONFIDENCE:
            if self.confidence_score is None:
                raise ValueError("confidence_score requires 'confidence_score' field")
            if not (0.0 <= self.confidence_score <= 1.0):
                raise ValueError(f"confidence_score must be 0–1, got {self.confidence_score}")
        if self.feedback_type == FeedbackType.METRIC:
            if not self.metric_name or self.metric_value is None:
                raise ValueError("metric_observed requires 'metric_name' and 'metric_value'")

    @classmethod
    def create(
        cls,
        feedback_type: FeedbackType,
        skill_id: str,
        tenant_id: str,
        outcome: Optional[OutcomeChoice] = None,
        preference: Optional[PreferenceChoice] = None,
        confidence_score: Optional[float] = None,
        metric_name: Optional[str] = None,
        metric_value: Optional[float] = None,
        task_id: Optional[str] = None,
        reason: Optional[str] = None,
        lom: Optional[str] = None,
    ) -> FeedbackEvent:
        """Factory for creating new feedback events."""
        return cls(
            feedback_id=str(uuid4()),
            feedback_type=feedback_type,
            skill_id=skill_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            task_id=task_id,
            outcome=outcome,
            preference=preference,
            confidence_score=confidence_score,
            metric_name=metric_name,
            metric_value=metric_value,
            reason=reason,
            lom=lom,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (for storage/JSON)."""
        return {
            "feedback_id": self.feedback_id,
            "feedback_type": self.feedback_type.value,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "outcome": self.outcome.value if self.outcome else None,
            "preference": self.preference.value if self.preference else None,
            "confidence_score": self.confidence_score,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "reason": self.reason[:500] if self.reason else None,  # Cap at 500 chars
            "lom": self.lom,
            "audit_ref": self.audit_ref,
        }
