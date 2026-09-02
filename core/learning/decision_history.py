"""Phase 3.2: Decision History (ADR-0316).

Track user accept/reject/modify actions on Skill decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


class UserAction(str, Enum):
    """User action on Skill decision (ADR-0316)."""

    ACCEPT = "accept"  # User approved decision
    REJECT = "reject"  # User rejected decision
    MODIFY = "modify"  # User modified decision
    IGNORE = "ignore"  # User ignored (didn't act)


@dataclass(frozen=True)
class DecisionHistoryEvent:
    """Immutable decision history event (ADR-0316, GDPR Art. 30, 32).

    Tracks user actions on Skill decisions for pattern learning.
    """

    event_id: str  # UUID4
    decision_id: str  # Links to ConfidenceEvent
    skill_id: str  # Which Skill decided?
    tenant_id: str  # Tenant scope (GDPR Art. 32)
    timestamp: str  # ISO8601 UTC
    user_action: UserAction  # accept | reject | modify | ignore
    version: str = "1.0"

    # Context
    context_snapshot: dict = None  # What was the task context?
    rationale: Optional[str] = None  # Why this action?
    
    # Metadata
    lom: Optional[str] = None
    prev_hash: Optional[str] = None

    def __post_init__(self):
        if not self.tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32)")
        if not self.decision_id:
            raise ValueError("decision_id required")

    @classmethod
    def create(
        cls,
        decision_id: str,
        skill_id: str,
        tenant_id: str,
        user_action: UserAction,
        context_snapshot: Optional[dict] = None,
        rationale: Optional[str] = None,
        lom: Optional[str] = None,
    ) -> DecisionHistoryEvent:
        return cls(
            event_id=str(uuid4()),
            decision_id=decision_id,
            skill_id=skill_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            user_action=user_action,
            context_snapshot=context_snapshot or {},
            rationale=rationale,
            lom=lom,
        )

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "decision_id": self.decision_id,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "user_action": self.user_action.value,
            "context_snapshot": self.context_snapshot,
            "rationale": self.rationale,
            "lom": self.lom,
        }
