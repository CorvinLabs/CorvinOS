"""Decision Record — Immutable audit trail of subsystem decisions.

Persists decisions made during task execution for learning and audit purposes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DecisionRecord:
    """Immutable audit trail of subsystem decisions."""

    timestamp: str  # ISO 8601 with Z suffix
    subsystem: str
    decision_type: str
    value: str
    reasoning: str
    context_stack: str  # str(stack) for hierarchical context
    confidence: float  # 0.0–1.0, reflects decision certainty
    guidance_applied: bool  # True if guidance influenced this decision

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {
            "timestamp": self.timestamp,
            "subsystem": self.subsystem,
            "decision_type": self.decision_type,
            "value": self.value,
            "reasoning": self.reasoning,
            "context_stack": self.context_stack,
            "confidence": self.confidence,
            "guidance_applied": self.guidance_applied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DecisionRecord":
        """Deserialize from dict."""
        return cls(**data)

    @staticmethod
    def now_iso() -> str:
        """Get current time in ISO 8601 format with Z suffix."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
