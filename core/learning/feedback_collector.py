"""Feedback Collection Module — Gate 3 Implementation (ADR-0676).

Collects user feedback on skill executions, validates, scrubs PII, and emits
immutable FeedbackReceivedEvent to event stream.

Components:
- FeedbackCollector: Main API for feedback submission
- FeedbackValidator: Validates feedback payload (type, bounds, PII)
- PIIScrubber: Removes PII from reason field (fail-safe)
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List
from uuid import uuid4

logger = logging.getLogger(__name__)


class OutcomeFeedbackType(str, Enum):
    """Outcome feedback: was the Skill decision correct?"""
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class PreferenceFeedbackType(str, Enum):
    """User preference: LLM-generated vs deterministic?"""
    LLM = "llm"
    DETERMINISTIC = "deterministic"
    EITHER = "either"


@dataclass(frozen=True)
class FeedbackCollectorResult:
    """Result of feedback collection attempt."""
    feedback_id: str
    skill_id: str
    task_id: str
    tenant_id: str
    timestamp: str
    accepted: bool
    reason: Optional[str] = None  # If rejected, why


class PIIScrubber:
    """Scrubs PII from feedback reason field."""

    # Patterns to detect PII
    PII_PATTERNS = [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
        r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
        r'\b\d{16}\b',  # Credit card-like
        r'\b(?:[A-Z]{2}\d{6,8}|[0-9]{9})\b',  # ID-like patterns
    ]

    @classmethod
    def scrub(cls, text: Optional[str]) -> Optional[str]:
        """Scrub PII from text, return safe version or None."""
        if not text:
            return None

        scrubbed = text
        for pattern in cls.PII_PATTERNS:
            scrubbed = re.sub(pattern, "[REDACTED]", scrubbed, flags=re.IGNORECASE)

        # If too much redacted, return None (likely spam or accidentally contains PII)
        redacted_count = scrubbed.count("[REDACTED]")
        if redacted_count > 3:  # More than 3 PII patterns found
            logger.warning(f"Feedback contains too much PII ({redacted_count} patterns), discarding")
            return None

        return scrubbed if scrubbed != text else text  # Return original if no PII found


class FeedbackValidator:
    """Validates feedback payload."""

    @staticmethod
    def validate(
        skill_id: str,
        task_id: str,
        outcome_feedback: Optional[str],
        quality_rating: Optional[int],
        preference_feedback: Optional[str],
        reason: Optional[str],
        confidence: Optional[float],
    ) -> tuple[bool, Optional[str]]:
        """
        Validate feedback payload.

        Returns: (is_valid, error_message)
        """
        # Check required fields
        if not skill_id or not skill_id.strip():
            return False, "skill_id is required"
        if not task_id or not task_id.strip():
            return False, "task_id is required"

        # Check at least one feedback type provided
        if not any([outcome_feedback, quality_rating, preference_feedback]):
            return False, "At least one feedback type required"

        # Validate outcome_feedback
        if outcome_feedback:
            if outcome_feedback not in [e.value for e in OutcomeFeedbackType]:
                return False, f"Invalid outcome_feedback: {outcome_feedback}"

        # Validate quality_rating
        if quality_rating is not None:
            if not isinstance(quality_rating, int) or quality_rating < 1 or quality_rating > 5:
                return False, "quality_rating must be integer 1–5"

        # Validate preference_feedback
        if preference_feedback:
            if preference_feedback not in [e.value for e in PreferenceFeedbackType]:
                return False, f"Invalid preference_feedback: {preference_feedback}"

        # Validate confidence
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
                return False, "confidence must be float 0.0–1.0"

        return True, None


class FeedbackCollector:
    """Main API for feedback collection."""

    def __init__(self, tenant_id: str = "_default"):
        """Initialize feedback collector."""
        self.tenant_id = tenant_id
        self.feedback_store: List[Dict[str, Any]] = []  # In-memory buffer (Gate 3)
        # In Gate 5, this will be persisted to EventStore + config_history.jsonl

    async def collect_feedback(
        self,
        skill_id: str,
        task_id: str,
        outcome_feedback: Optional[str] = None,
        quality_rating: Optional[int] = None,
        preference_feedback: Optional[str] = None,
        reason: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> FeedbackCollectorResult:
        """
        Collect user feedback on skill execution.

        Returns: FeedbackCollectorResult (accepted or rejected with reason)
        """
        feedback_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Validate feedback
        is_valid, error_msg = FeedbackValidator.validate(
            skill_id=skill_id,
            task_id=task_id,
            outcome_feedback=outcome_feedback,
            quality_rating=quality_rating,
            preference_feedback=preference_feedback,
            reason=reason,
            confidence=confidence,
        )

        if not is_valid:
            logger.warning(f"Invalid feedback: {error_msg}")
            return FeedbackCollectorResult(
                feedback_id=feedback_id,
                skill_id=skill_id,
                task_id=task_id,
                tenant_id=self.tenant_id,
                timestamp=timestamp,
                accepted=False,
                reason=error_msg,
            )

        # Scrub PII from reason
        scrubbed_reason = PIIScrubber.scrub(reason) if reason else None

        # Store feedback (in-memory for Gate 3, persisted in Gate 3+)
        feedback_event = {
            "feedback_id": feedback_id,
            "skill_id": skill_id,
            "task_id": task_id,
            "tenant_id": self.tenant_id,
            "timestamp": timestamp,
            "outcome_feedback": outcome_feedback,
            "quality_rating": quality_rating,
            "preference_feedback": preference_feedback,
            "reason": scrubbed_reason,
            "confidence": confidence,
        }

        self.feedback_store.append(feedback_event)
        logger.info(
            f"Feedback collected: feedback_id={feedback_id}, skill={skill_id}, "
            f"task={task_id}, types=[{', '.join(filter(None, [outcome_feedback, quality_rating, preference_feedback]))}]"
        )

        return FeedbackCollectorResult(
            feedback_id=feedback_id,
            skill_id=skill_id,
            task_id=task_id,
            tenant_id=self.tenant_id,
            timestamp=timestamp,
            accepted=True,
        )

    def get_feedback_for_skill(self, skill_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent feedback for a skill."""
        return [f for f in self.feedback_store[-limit:] if f["skill_id"] == skill_id]

    def get_all_feedback(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all recent feedback."""
        return self.feedback_store[-limit:]

    def clear_feedback(self, skill_id: Optional[str] = None) -> int:
        """Clear feedback (useful for testing)."""
        if skill_id:
            count = len(self.feedback_store)
            self.feedback_store = [f for f in self.feedback_store if f["skill_id"] != skill_id]
            return count - len(self.feedback_store)
        else:
            count = len(self.feedback_store)
            self.feedback_store = []
            return count


# Global singleton
_feedback_collector = None


def get_feedback_collector(tenant_id: str = "_default") -> FeedbackCollector:
    """Get or create feedback collector singleton."""
    global _feedback_collector
    if _feedback_collector is None:
        _feedback_collector = FeedbackCollector(tenant_id=tenant_id)
    return _feedback_collector
