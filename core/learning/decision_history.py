"""Decision History — tracking user choices (ADR-0316)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import uuid4


@dataclass(frozen=True)
class DecisionRecord:
    """Immutable record of a user choice."""

    decision_id: str
    choice_type: str
    candidates: list[str]
    chosen: str
    timestamp_utc: datetime
    session_id: str
    confidence_score: Optional[float] = None
    user_input: Optional[str] = None
    reasoning: Optional[str] = None

    def to_payload(self) -> dict:
        """Convert to learning event payload."""
        return {
            "decision_id": self.decision_id,
            "choice_type": self.choice_type,
            "candidates": self.candidates,
            "chosen": self.chosen,
            "confidence_score": self.confidence_score,
            "user_input": self.user_input,
            "reasoning": self.reasoning,
        }


class DecisionRecorder:
    """Record user choices for learning."""

    def __init__(self, tenant_id: str):
        """Initialize recorder.

        Args:
            tenant_id: Tenant ID (for isolation)
        """
        self.tenant_id = tenant_id

    def create_decision(
        self,
        choice_type: str,
        candidates: list[str],
        chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        user_input: Optional[str] = None,
        reasoning: Optional[str] = None,
    ) -> DecisionRecord:
        """Create a decision record.

        Args:
            choice_type: Type of choice ("skill_selection", "model_choice", etc.)
            candidates: Available options
            chosen: Which option was selected
            session_id: Session ID
            confidence_score: Confidence in this choice (from ADR-0315)
            user_input: User's original query/request
            reasoning: Why this choice was made

        Returns:
            DecisionRecord (ready to emit as event)

        Raises:
            ValueError: If candidates > 100 or invalid state
        """
        if len(candidates) > 100:
            raise ValueError(f"Too many candidates: {len(candidates)} > 100")

        if chosen not in candidates:
            raise ValueError(f"Chosen '{chosen}' not in candidates {candidates}")

        if confidence_score is not None and not (0.0 <= confidence_score <= 1.0):
            raise ValueError(f"Invalid confidence_score: {confidence_score}")

        # Validate no secrets in reasoning
        if reasoning and self._contains_potential_secret(reasoning):
            reasoning = "[redacted]"

        return DecisionRecord(
            decision_id=str(uuid4()),
            choice_type=choice_type,
            candidates=candidates,
            chosen=chosen,
            timestamp_utc=datetime.utcnow(),
            session_id=session_id,
            confidence_score=confidence_score,
            user_input=user_input,
            reasoning=reasoning,
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
