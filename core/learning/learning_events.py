"""Phase 2: Learning Infrastructure — Event Schema (ADR-0314).

Defines 8 immutable learning event types for self-optimizing Skills.
All events are frozen dataclasses (immutable) and tenant-scoped (GDPR Art. 32).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class EventType(str, Enum):
    """8 learning event types (ADR-0314)."""

    CONFIDENCE = "confidence"  # Skill confidence score changed
    FEEDBACK = "feedback"  # User gave feedback on Skill decision
    OUTCOME = "outcome"  # Outcome signal (was decision correct?)
    PREFERENCE = "preference"  # User expressed preference (LLM vs deterministic)
    ATTENTION = "attention"  # Attention/token budget update
    METRIC = "metric"  # Metric observed (latency, cost, error rate)
    CONFIG_UPDATED = "config_updated"  # Skill config changed by optimizer
    SKILL_EXECUTED = "skill_executed"  # Skill was executed (from audit chain)
    DECISION = "decision"  # Skill selection decision recorded (ADR-0316, Phase 4 hooks)


@dataclass(frozen=True)
class LearningEvent:
    """Immutable learning event (ADR-0314, GDPR Art. 30, 32).

    Guarantees:
    - Frozen (immutable after creation)
    - Tenant-scoped (no cross-tenant leakage)
    - Timestamped (audit trail)
    - Hash-chainable (future: ADR-0319 retention)
    """

    event_id: str  # UUID4
    event_type: EventType  # From enum above
    skill_id: str  # "os.feature_flags_system", "os.delegation_router", etc.
    tenant_id: str  # Tenant scope (GDPR requirement)
    timestamp: str  # ISO 8601 UTC
    version: str = "1.0"  # Schema version

    # Event-type-specific data
    signal: Optional[dict[str, Any]] = None  # feedback, outcome, metric data
    skill_config_delta: Optional[dict[str, Any]] = None  # For config_updated events
    skill_version: Optional[str] = None  # Skill version at execution time

    # Metadata
    lom: Optional[str] = None  # Line of Moral Responsibility (code location)
    prev_hash: Optional[str] = None  # Previous event hash (for chaining)

    def __post_init__(self):
        """Validate event on creation (frozen dataclass)."""
        if not self.tenant_id:
            raise ValueError("tenant_id is required (GDPR Art. 32)")
        if not self.skill_id:
            raise ValueError("skill_id is required")
        if not self.event_id:
            raise ValueError("event_id is required")

    @classmethod
    def create(
        cls,
        event_type: EventType,
        skill_id: str,
        tenant_id: str,
        signal: Optional[dict[str, Any]] = None,
        skill_version: Optional[str] = None,
        lom: Optional[str] = None,
    ) -> LearningEvent:
        """Factory for creating new events."""
        return cls(
            event_id=str(uuid4()),
            event_type=event_type,
            skill_id=skill_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            signal=signal,
            skill_version=skill_version,
            lom=lom,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (for storage/JSON)."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "version": self.version,
            "signal": self.signal,
            "skill_config_delta": self.skill_config_delta,
            "skill_version": self.skill_version,
            "lom": self.lom,
            "prev_hash": self.prev_hash,
        }
