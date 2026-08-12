"""Outcome Feedback — closed-loop learning (ADR-0317)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


class OutcomeType(str, Enum):
    """Outcome classifications."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


@dataclass(frozen=True)
class OutcomeRecord:
    """Immutable record of a choice outcome."""

    outcome_id: str
    decision_id: str
    session_id: str
    outcome: OutcomeType
    timestamp_utc: datetime
    feedback_text: Optional[str] = None
    rating: Optional[int] = None

    def to_payload(self) -> dict:
        """Convert to learning event payload."""
        return {
            "outcome_id": self.outcome_id,
            "decision_id": self.decision_id,
            "outcome": self.outcome.value,
            "feedback_text": self.feedback_text,
            "rating": self.rating,
        }


class OutcomeRecorder:
    """Record outcomes of decisions for closed-loop learning."""

    def __init__(self, tenant_id: str):
        """Initialize recorder.

        Args:
            tenant_id: Tenant ID (for isolation)
        """
        self.tenant_id = tenant_id

    def record_outcome(
        self,
        decision_id: str,
        session_id: str,
        outcome: OutcomeType,
        feedback_text: Optional[str] = None,
        rating: Optional[int] = None,
    ) -> OutcomeRecord:
        """Record an outcome for a decision.

        Args:
            decision_id: ID of decision being evaluated
            session_id: Session ID
            outcome: "success", "partial", or "failure"
            feedback_text: User's feedback on the outcome
            rating: Optional numeric rating (1-5)

        Returns:
            OutcomeRecord (ready to emit as event)

        Raises:
            ValueError: If invalid rating
        """
        if rating is not None and not (1 <= rating <= 5):
            raise ValueError(f"Invalid rating: {rating}, must be 1-5")

        if feedback_text and self._contains_potential_secret(feedback_text):
            feedback_text = "[redacted]"

        return OutcomeRecord(
            outcome_id=str(uuid4()),
            decision_id=decision_id,
            session_id=session_id,
            outcome=outcome,
            timestamp_utc=datetime.utcnow(),
            feedback_text=feedback_text,
            rating=rating,
        )

    def _contains_potential_secret(self, text: str) -> bool:
        """Check if text might contain secrets using regex patterns."""
        import re

        patterns = [
            r'\b(api_key|api_secret|password|token|credential|secret|auth)\b\s*[=:]',
            r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*',
            r'[a-f0-9]{32,}',  # Hex blobs (MD5+ length)
        ]

        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False
