"""Phase 3: Confidence Intervals (ADR-0315).

Skill decision confidence scoring: relevance + reliability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


class ConfidenceMetric(str, Enum):
    """Confidence metric types (ADR-0315)."""

    RELEVANCE = "relevance"  # How relevant was this decision to the task?
    RELIABILITY = "reliability"  # How confident are we in the decision?
    COMBINED = "combined"  # Weighted average (relevance * 0.4 + reliability * 0.6)


@dataclass(frozen=True)
class ConfidenceEvent:
    """Immutable confidence scoring event (ADR-0315, GDPR Art. 30, 32).

    Guarantees:
    - Frozen (immutable after creation)
    - Tenant-scoped (no cross-tenant leakage)
    - Timestamped (audit trail)
    - Hash-chainable (future retention policy)
    """

    event_id: str  # UUID4
    skill_id: str  # "os.delegation_router", "os.context_adapter", etc.
    tenant_id: str  # Tenant scope (GDPR requirement)
    timestamp: str  # ISO 8601 UTC
    version: str = "1.0"  # Schema version

    # Confidence metrics
    relevance_score: float  # 0.0-1.0 (how relevant to task)
    reliability_score: float  # 0.0-1.0 (how confident in decision)
    combined_score: float  # 0.0-1.0 (weighted: 0.4*relevance + 0.6*reliability)

    # Context
    decision_id: str = ""  # Links to LearningEvent that was scored
    feedback_count: int = 0  # How many feedback signals contributed?
    reasoning: Optional[str] = None  # Why this confidence level

    # Metadata
    lom: Optional[str] = None  # Line of Moral Responsibility
    prev_hash: Optional[str] = None  # Previous event hash (for chaining)

    def __post_init__(self):
        """Validate event on creation (frozen dataclass)."""
        if not self.tenant_id:
            raise ValueError("tenant_id is required (GDPR Art. 32)")
        if not self.skill_id:
            raise ValueError("skill_id is required")
        if not self.event_id:
            raise ValueError("event_id is required")

        # Validate score ranges
        for score, name in [
            (self.relevance_score, "relevance_score"),
            (self.reliability_score, "reliability_score"),
            (self.combined_score, "combined_score"),
        ]:
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"{name} must be in range [0.0, 1.0], got {score}")

    @classmethod
    def create(
        cls,
        skill_id: str,
        tenant_id: str,
        relevance_score: float,
        reliability_score: float,
        decision_id: str = "",
        feedback_count: int = 0,
        reasoning: Optional[str] = None,
        lom: Optional[str] = None,
    ) -> ConfidenceEvent:
        """Factory for creating new confidence events."""
        # Compute combined score (weighted average)
        combined = relevance_score * 0.4 + reliability_score * 0.6

        return cls(
            event_id=str(uuid4()),
            skill_id=skill_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            relevance_score=relevance_score,
            reliability_score=reliability_score,
            combined_score=combined,
            decision_id=decision_id,
            feedback_count=feedback_count,
            reasoning=reasoning,
            lom=lom,
        )

    def to_dict(self):
        """Serialize to dict (for storage/JSON)."""
        return {
            "event_id": self.event_id,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "version": self.version,
            "relevance_score": self.relevance_score,
            "reliability_score": self.reliability_score,
            "combined_score": self.combined_score,
            "decision_id": self.decision_id,
            "feedback_count": self.feedback_count,
            "reasoning": self.reasoning,
            "lom": self.lom,
            "prev_hash": self.prev_hash,
        }
