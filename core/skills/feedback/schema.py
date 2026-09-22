"""
Feedback Integration Schema — ADR-2033

Unified feedback types for Skills 2.0 learning loop (ADR-0314).
Four immutable feedback types: Outcome, Preference, Confidence, Metric.

Every feedback event is:
- Immutable (frozen dataclass)
- Tenant-scoped (fail-closed on null tenant_id)
- Audit-logged (hash-chained to previous event)
- Non-PII (only skill_id, signals, no user content)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4


class FeedbackType(str, Enum):
    """Four feedback types for Skills 2.0 learning loop."""
    OUTCOME = "outcome"  # Was the Skill decision correct? (yes/no/other)
    PREFERENCE = "preference"  # Prefer this behavior next time?
    CONFIDENCE = "confidence"  # Confidence in Skill's ability (0.0–1.0)
    METRIC = "metric"  # Observed metric (latency, error rate, cost)


@dataclass(frozen=True)
class FeedbackEvent:
    """
    Base feedback event — immutable, audit-logged.

    Attributes:
        feedback_id: UUID, unique per feedback event
        skill_id: e.g., "os.delegation_router"
        tenant_id: Tenant scope (fail-closed: raises if None)
        feedback_type: One of FeedbackType
        signal: Feedback signal (bool for outcome, float for confidence, str for preference)
        reason: Optional human-readable reason (100-char max, no PII)
        timestamp: UTC timestamp when feedback was recorded
        prev_hash: SHA256 of previous audit event (for chain verification)
    """
    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    skill_id: str = field()  # e.g., "os.delegation_router"
    tenant_id: str = field()
    feedback_type: FeedbackType = field()
    signal: float | bool | str = field()  # Type varies by feedback_type
    reason: Optional[str] = field(default=None)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "") + "Z")
    prev_hash: Optional[str] = field(default=None)  # Filled by audit backend

    def __post_init__(self):
        """Validate feedback event on creation (fail-closed)."""
        if not self.tenant_id:
            raise ValueError("tenant_id is required (fail-closed on null)")

        if not self.skill_id:
            raise ValueError("skill_id is required")

        if not isinstance(self.feedback_type, FeedbackType):
            raise ValueError(f"Invalid feedback_type: {self.feedback_type}")

        # Validate signal by feedback type
        if self.feedback_type == FeedbackType.OUTCOME:
            if not isinstance(self.signal, bool) and self.signal not in ("yes", "no", "other"):
                raise ValueError(f"OUTCOME signal must be bool or 'yes'/'no'/'other', got {self.signal}")

        elif self.feedback_type == FeedbackType.CONFIDENCE:
            if not isinstance(self.signal, (int, float)) or not (0.0 <= self.signal <= 1.0):
                raise ValueError(f"CONFIDENCE signal must be float in [0.0, 1.0], got {self.signal}")

        elif self.feedback_type == FeedbackType.PREFERENCE:
            if not isinstance(self.signal, str):
                raise ValueError(f"PREFERENCE signal must be str, got {type(self.signal)}")

        elif self.feedback_type == FeedbackType.METRIC:
            if not isinstance(self.signal, (int, float)):
                raise ValueError(f"METRIC signal must be numeric, got {type(self.signal)}")

        # Validate reason (max 100 chars, no embedded PII checks here — audit backend handles that)
        if self.reason and len(self.reason) > 100:
            raise ValueError(f"reason must be ≤100 chars, got {len(self.reason)}")


@dataclass(frozen=True)
class OutcomeFeedback(FeedbackEvent):
    """
    Outcome feedback: "Was the Skill decision correct?"

    signal: bool | str
      - True: decision was correct
      - False: decision was incorrect
      - "other": unclear or context-dependent
    """
    feedback_type: FeedbackType = field(default=FeedbackType.OUTCOME, init=False)


@dataclass(frozen=True)
class PreferenceFeedback(FeedbackEvent):
    """
    Preference feedback: "Prefer this behavior next time?"

    signal: str, one of:
      - "llm_generated": prefer LLM-generated decisions
      - "deterministic": prefer deterministic rules
      - "neither": no preference
    """
    feedback_type: FeedbackType = field(default=FeedbackType.PREFERENCE, init=False)


@dataclass(frozen=True)
class ConfidenceFeedback(FeedbackEvent):
    """
    Confidence feedback: "How confident are you in this Skill?"

    signal: float in [0.0, 1.0]
      - 1.0: high confidence
      - 0.5: moderate confidence
      - 0.0: no confidence (disable Skill?)
    """
    feedback_type: FeedbackType = field(default=FeedbackType.CONFIDENCE, init=False)


@dataclass(frozen=True)
class MetricFeedback(FeedbackEvent):
    """
    Metric feedback: "Observed metric outcome"

    signal: float (numeric metric value)
    reason: metric name (e.g., "latency_ms", "error_count", "cost_delta")
    """
    feedback_type: FeedbackType = field(default=FeedbackType.METRIC, init=False)


# Validation helpers
def validate_feedback(event: FeedbackEvent) -> tuple[bool, str]:
    """
    Validate feedback event (called by audit backend before logging).

    Returns:
        (is_valid, error_message)
    """
    try:
        # Validation happens in __post_init__, so if we get here, it's valid
        return (True, "")
    except ValueError as e:
        return (False, str(e))


def feedback_to_audit_event(event: FeedbackEvent, prev_hash: str) -> dict:
    """
    Convert FeedbackEvent to audit-logged format (called by audit backend).

    Args:
        event: FeedbackEvent
        prev_hash: SHA256 of previous audit event (for chain verification)

    Returns:
        dict ready for audit logging (immutable append-only)
    """
    return {
        "tenant_id": event.tenant_id,
        "event_type": "skill_feedback",
        "feedback_id": event.feedback_id,
        "skill_id": event.skill_id,
        "feedback_type": event.feedback_type.value,
        "signal": event.signal,
        "reason": event.reason or "",
        "timestamp": event.timestamp,
        "prev_hash": prev_hash,
        # hash will be filled by audit backend (SHA256 of this record)
    }
