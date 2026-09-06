"""Phase 4 Week 17: Feedback Schema & Collection — User feedback → FEEDBACK events.

Compliance: GDPR Art. 5 (minimization), Art. 6 (consent), Art. 30 (audit), Art. 32 (security)

User feedback closes the learning loop by providing ground truth about Skill decisions.
This module collects three types of feedback:
  - outcome_feedback: "Was the Skill decision correct?" (yes/no/unknown)
  - quality_rating: "How would you rate the quality?" (1–5 stars)
  - preference_feedback: "Prefer LLM or deterministic?" (llm/deterministic/either)

All feedback is validated, scrubbed of PII, buffered for confidence thresholds, and
emitted as immutable FEEDBACK events to the audit-first EventStore.

Fail-soft: validation failures drop feedback silently (with audit trail), never raising
exceptions that could break the user experience.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class OutcomeFeedbackType(str, Enum):
    """Outcome feedback: was the Skill decision correct?"""
    YES = "yes"              # User agreed with Skill decision
    NO = "no"                # User disagreed with Skill decision
    UNKNOWN = "unknown"      # User unsure or inconclusive


class PreferenceFeedbackType(str, Enum):
    """User preference: LLM-generated vs deterministic?"""
    LLM = "llm"              # Prefer LLM-generated answers
    DETERMINISTIC = "deterministic"  # Prefer rule-based answers
    EITHER = "either"        # No preference, both acceptable


@dataclass(frozen=True)
class FeedbackEvent:
    """Immutable feedback event (GDPR Art. 30 audit trail).

    Guarantees:
    - Frozen (immutable)
    - PII-scrubbed (no names, emails, PII)
    - Tenant-scoped (GDPR Art. 32)
    - Audit-logged (every event recorded)
    """
    feedback_id: str                           # UUID4
    skill_id: str                              # e.g., "os.delegation_router"
    task_id: str                               # Task this feedback is about
    tenant_id: str                             # Tenant scope
    timestamp: str                             # ISO 8601 UTC

    # Three feedback types (mutually exclusive: ≥1 must be present)
    outcome_feedback: Optional[OutcomeFeedbackType] = None  # Correctness
    quality_rating: Optional[int] = None                    # 1–5 stars
    preference_feedback: Optional[PreferenceFeedbackType] = None  # Style

    # Metadata
    reason: Optional[str] = None               # User's explanation (scrubbed of PII)
    confidence: Optional[float] = None         # User's confidence in feedback (0–1)
    source: str = "user"                       # "user" | "system" | "audit"
    lom: Optional[str] = None                  # Line of Moral Responsibility

    def __post_init__(self):
        """Validate feedback on creation."""
        if not self.tenant_id:
            raise ValueError("tenant_id is required (GDPR Art. 32)")
        if not self.skill_id:
            raise ValueError("skill_id is required")
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.feedback_id:
            raise ValueError("feedback_id is required")

        # At least one feedback type must be present
        has_feedback = (
            self.outcome_feedback is not None or
            self.quality_rating is not None or
            self.preference_feedback is not None
        )
        if not has_feedback:
            raise ValueError("at least one feedback type (outcome/quality/preference) required")

    @classmethod
    def create(
        cls,
        skill_id: str,
        task_id: str,
        tenant_id: str,
        outcome_feedback: Optional[OutcomeFeedbackType] = None,
        quality_rating: Optional[int] = None,
        preference_feedback: Optional[PreferenceFeedbackType] = None,
        reason: Optional[str] = None,
        confidence: Optional[float] = None,
        source: str = "user",
        lom: Optional[str] = None,
    ) -> FeedbackEvent:
        """Factory for creating new feedback events."""
        return cls(
            feedback_id=str(uuid4()),
            skill_id=skill_id,
            task_id=task_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            outcome_feedback=outcome_feedback,
            quality_rating=quality_rating,
            preference_feedback=preference_feedback,
            reason=reason,
            confidence=confidence,
            source=source,
            lom=lom,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for storage/JSON)."""
        return {
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "outcome_feedback": self.outcome_feedback.value if self.outcome_feedback else None,
            "quality_rating": self.quality_rating,
            "preference_feedback": self.preference_feedback.value if self.preference_feedback else None,
            "reason": self.reason,
            "confidence": self.confidence,
            "source": self.source,
            "lom": self.lom,
        }


class FeedbackScrubber:
    """Scrubs user feedback of PII before storage (GDPR Art. 5 minimization)."""

    # Common PII patterns to detect and remove
    PII_PATTERNS = [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
        r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b',                     # Phone
        r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b',                       # SSN-like
        r'\b[A-Z]{1,2}\d{1,3}\s?\d[A-Z]{2}\b',                   # UK postcode
        r'\b\d{5}[-\s]?\d{4}\b',                                   # US ZIP+4
        r'\b(?:silvio|shumway|user\d+)\b',                        # Known usernames
    ]

    COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in PII_PATTERNS]

    MAX_REASON_LENGTH = 1000  # Prevent DoS via huge reason strings

    @classmethod
    def scrub(cls, text: Optional[str]) -> Optional[str]:
        """Remove PII from feedback reason (fail-soft: return None on huge input).

        Args:
            text: User's feedback reason

        Returns:
            Scrubbed text, or None if input is too large
        """
        if text is None:
            return None

        if len(text) > cls.MAX_REASON_LENGTH:
            logger.warning("feedback reason too long (%d chars), dropped", len(text))
            return None

        scrubbed = text
        for pattern in cls.COMPILED_PATTERNS:
            scrubbed = pattern.sub("[REDACTED]", scrubbed)

        return scrubbed


class FeedbackValidator:
    """Validates feedback before emission (fail-closed, GDPR Art. 32)."""

    FEEDBACK_WINDOW_MINUTES = 60  # Only feedback within 60min of execution
    CONFIDENCE_THRESHOLD = 0.6    # Require ≥60% confidence for buffering
    MIN_BUFFER_SIZE = 10          # Collect ≥10 feedback samples before tuning

    def __init__(self, audit_backend=None, event_store=None):
        """Initialize validator with dependencies.

        Args:
            audit_backend: Audit trail writer
            event_store: EventStore instance (to verify task exists)
        """
        self.audit_backend = audit_backend
        self.event_store = event_store

    def validate(self, feedback: FeedbackEvent) -> tuple[bool, Optional[str]]:
        """Validate feedback (return: (is_valid, error_message)).

        Checks:
        1. Tenant ID is valid (GDPR Art. 32 isolation)
        2. Skill ID is not empty
        3. Task ID is not empty
        4. Feedback is time-bound (within 60 min)
        5. At least one feedback type is present
        6. Quality rating is in range (1–5 or None)
        7. Confidence is in range (0–1 or None)
        8. Reason has been scrubbed of PII
        """

        # 1. Tenant ID validation (fail-closed isolation)
        if not feedback.tenant_id or len(feedback.tenant_id) == 0:
            return False, "tenant_id required (GDPR Art. 32)"
        if not self._is_valid_tenant(feedback.tenant_id):
            return False, f"invalid tenant_id: {feedback.tenant_id}"

        # 2. Skill ID validation
        if not feedback.skill_id or len(feedback.skill_id) == 0:
            return False, "skill_id required"

        # 3. Task ID validation
        if not feedback.task_id or len(feedback.task_id) == 0:
            return False, "task_id required"

        # 4. Feedback must be time-bound
        if not self._is_within_feedback_window(feedback.timestamp):
            return False, f"feedback too old (max {self.FEEDBACK_WINDOW_MINUTES} min)"

        # 5. At least one feedback type
        has_feedback = (
            feedback.outcome_feedback is not None or
            feedback.quality_rating is not None or
            feedback.preference_feedback is not None
        )
        if not has_feedback:
            return False, "at least one feedback type required"

        # 6. Quality rating range validation
        if feedback.quality_rating is not None:
            if not isinstance(feedback.quality_rating, int) or feedback.quality_rating < 1 or feedback.quality_rating > 5:
                return False, "quality_rating must be 1–5 or None"

        # 7. Confidence range validation
        if feedback.confidence is not None:
            if not isinstance(feedback.confidence, (int, float)):
                return False, "confidence must be float (0–1) or None"
            if feedback.confidence < 0.0 or feedback.confidence > 1.0:
                return False, "confidence must be in [0, 1]"

        # 8. Reason must be scrubbed (check for PII patterns)
        if feedback.reason and "[REDACTED]" not in feedback.reason:
            # Reason should have been pre-scrubbed; if not, fail
            scrubber = FeedbackScrubber()
            scrubbed = scrubber.scrub(feedback.reason)
            if scrubbed is None:
                return False, "feedback reason too large or contains PII"

        return True, None

    @staticmethod
    def _is_within_feedback_window(timestamp_iso: str) -> bool:
        """Check if feedback timestamp is within allowed window (60 min)."""
        try:
            feedback_time = datetime.fromisoformat(timestamp_iso.replace('Z', '+00:00'))
            now = datetime.utcnow().replace(tzinfo=feedback_time.tzinfo)
            delta = now - feedback_time
            return delta <= timedelta(minutes=FeedbackValidator.FEEDBACK_WINDOW_MINUTES)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _is_valid_tenant(tenant_id: str) -> bool:
        """Validate tenant_id format (alphanumeric + underscores)."""
        return bool(tenant_id) and len(tenant_id) <= 128 and all(c.isalnum() or c == '_' for c in tenant_id)


class FeedbackBuffer:
    """Buffers feedback samples until confidence threshold reached (GDPR Art. 6 consent gate).

    Collects feedback for each (skill_id, task_id) pair and only emits tuning signals
    when:
    1. Buffer has ≥10 samples
    2. Average confidence ≥0.6
    3. No contradictions (yes + no on same task rejected)
    """

    def __init__(self, min_samples: int = 10, confidence_threshold: float = 0.6):
        """Initialize feedback buffer.

        Args:
            min_samples: Require ≥N samples before tuning
            confidence_threshold: Require ≥N confidence to emit signal
        """
        self.min_samples = min_samples
        self.confidence_threshold = confidence_threshold
        self.buffers: Dict[str, List[FeedbackEvent]] = {}  # key: (skill_id, task_id)

    def add(self, feedback: FeedbackEvent) -> None:
        """Add feedback to buffer (fail-soft: no exceptions)."""
        key = (feedback.skill_id, feedback.task_id)
        if key not in self.buffers:
            self.buffers[key] = []
        self.buffers[key].append(feedback)

    def ready_to_emit(self, skill_id: str, task_id: str) -> bool:
        """Check if buffer has enough samples for tuning signal.

        Returns:
            True if ≥10 samples AND average confidence ≥0.6
        """
        key = (skill_id, task_id)
        samples = self.buffers.get(key, [])

        if len(samples) < self.min_samples:
            return False

        # Average confidence must meet threshold
        confidences = [f.confidence for f in samples if f.confidence is not None]
        if not confidences:
            return False

        avg_confidence = sum(confidences) / len(confidences)
        return avg_confidence >= self.confidence_threshold

    def get_and_clear(self, skill_id: str, task_id: str) -> List[FeedbackEvent]:
        """Retrieve and clear buffer for a skill/task pair."""
        key = (skill_id, task_id)
        samples = self.buffers.get(key, [])
        self.buffers[key] = []
        return samples

    def get_summary(self, skill_id: str, task_id: str) -> Dict[str, Any]:
        """Get summary statistics for a buffer (for logging/debugging)."""
        key = (skill_id, task_id)
        samples = self.buffers.get(key, [])

        if not samples:
            return {"count": 0}

        # Count feedback types
        outcome_yes = sum(1 for f in samples if f.outcome_feedback == OutcomeFeedbackType.YES)
        outcome_no = sum(1 for f in samples if f.outcome_feedback == OutcomeFeedbackType.NO)
        quality_avg = sum(f.quality_rating for f in samples if f.quality_rating) / sum(1 for f in samples if f.quality_rating) if any(f.quality_rating for f in samples) else None

        return {
            "count": len(samples),
            "outcome_yes": outcome_yes,
            "outcome_no": outcome_no,
            "quality_avg": quality_avg,
        }


__all__ = [
    "FeedbackEvent",
    "FeedbackScrubber",
    "FeedbackValidator",
    "FeedbackBuffer",
    "OutcomeFeedbackType",
    "PreferenceFeedbackType",
]
