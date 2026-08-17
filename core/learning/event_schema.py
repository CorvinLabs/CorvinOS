"""Learning Event Schema — typed, immutable events for learning signals (ADR-0314)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class LearningEventType(str, Enum):
    """Canonical learning event types."""

    CONFIDENCE_SCORE = "confidence.score"
    DECISION_RECORD = "decision.record"
    USER_FEEDBACK = "feedback.user_provided"
    OUTCOME_OBSERVED = "outcome.observed"
    PREFERENCE_SET = "preference.set"
    ATTENTION_CONSUMED = "attention.consumed"
    ATTENTION_REFUNDED = "attention.refunded"
    METRIC_AGGREGATED = "metric.aggregated"
    TOKEN_METRICS = "token.metrics"  # Phase 1: Token measurement


@dataclass(frozen=True)
class LearningEvent:
    """Immutable learning event with audit trail."""

    event_type: LearningEventType
    tenant_id: str
    instance_id: str
    skill_name: Optional[str]
    session_id: str
    timestamp_utc: datetime
    event_id: str = field(default_factory=lambda: str(uuid4()))
    user_id: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    audit_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def to_audit_event(self) -> dict[str, Any]:
        """Convert to audit.jsonl format."""
        return {
            "event_type": f"learning.{self.event_type.value}",
            "tenant_id": self.tenant_id,
            "instance_id": self.instance_id,
            "user_id": self.user_id,
            "skill_name": self.skill_name,
            "session_id": self.session_id,
            "timestamp": self.timestamp_utc.isoformat() + "Z",
            "event_id": self.event_id,
            "payload": self.payload,
            "tags": self.tags,
        }


# Event payload dataclasses


@dataclass(frozen=True)
class ConfidenceScorePayload:
    """Confidence assessment (ADR-0315)."""

    relevance_score: float
    reliability_score: float
    model: str
    reasoning: Optional[str] = None


@dataclass(frozen=True)
class DecisionRecordPayload:
    """Choice recorded (ADR-0316)."""

    decision_id: str
    choice_type: str
    candidates: list[str]
    chosen: str
    context: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class UserFeedbackPayload:
    """Operator feedback (ADR-0317)."""

    feedback_type: str
    feedback_value: Any
    decision_id: Optional[str] = None
    context: Optional[str] = None


@dataclass(frozen=True)
class OutcomeObservedPayload:
    """Post-decision measurement (ADR-0317)."""

    decision_id: str
    outcome_type: str
    outcome_value: Any
    window_seconds: int


@dataclass(frozen=True)
class PreferenceSetPayload:
    """Style preference (ADR-0318)."""

    preference_key: str
    preference_value: str
    confidence: float = 0.5


@dataclass(frozen=True)
class AttentionConsumedPayload:
    """Budget deduction (ADR-0319)."""

    budget_type: str
    amount: int
    remaining: int
    reason: Optional[str] = None


@dataclass(frozen=True)
class AttentionRefundedPayload:
    """Budget restoration (ADR-0319)."""

    budget_type: str
    amount: int
    remaining: int
    reason: Optional[str] = None


@dataclass(frozen=True)
class MetricAggregatedPayload:
    """Aggregated metric (ADR-0320)."""

    metric_name: str
    window_seconds: int
    value: float
    sample_count: int


@dataclass(frozen=True)
class TokenMetricsPayload:
    """Token consumption measurement (Phase 1: TMF)."""

    turn_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int

    # Engine & inference info
    engine: str                           # "claude-opus-5" | "hermes" | etc
    engine_tier: str                      # "cloud" | "local" | "tiered"
    model_id: Optional[str] = None

    # Baseline comparison
    baseline_tokens: Optional[int] = None  # Native engine cost
    savings_tokens: Optional[int] = None   # baseline - total
    savings_percent: Optional[float] = None

    # Task metadata (for slicing)
    task_type: Optional[str] = None       # "code" | "research" | "analysis" | "chat"
    task_domain: Optional[str] = None     # "backend" | "frontend" | "data" | "general"
    task_complexity: Optional[str] = None # "trivial" | "simple" | "moderate" | "complex"

    # Subsystem breakdown (which subsystems consumed tokens?)
    subsystem_tokens: dict[str, int] = field(default_factory=dict)  # {"confidence": 200, "cache": 150, ...}

    # Inference details
    iterations_count: int = 1
    latency_ms: Optional[int] = None
    error_rate: Optional[float] = None

    # Outcome & quality
    outcome_quality: Optional[str] = None  # "excellent" | "good" | "acceptable" | "poor"
    required_followup: bool = False        # did user need to ask again?
    user_satisfaction: Optional[int] = None  # 1-5 rating
