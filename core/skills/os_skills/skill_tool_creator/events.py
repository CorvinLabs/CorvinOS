"""Phase completion events + loss tracking."""

from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime
import hashlib


@dataclass(frozen=True)
class PhaseCompletedEvent:
    """Immutable event emitted when a phase completes."""
    phase_id: int  # 0–10
    skill_id: str
    success: bool
    duration_ms: int
    loss_component: float  # 0–1, where 0 = perfect
    timestamp: str  # ISO8601

    # For backprop
    data_sources_used: List[str]
    decisions_made: Dict[str, Any]
    errors_if_any: List[str] = field(default_factory=list)

    # Audit
    event_hash: str = ""
    prev_event_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "skill_id": self.skill_id,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "loss_component": self.loss_component,
            "timestamp": self.timestamp,
            "data_sources_used": self.data_sources_used,
            "decisions_made": self.decisions_made,
            "errors_if_any": self.errors_if_any,
            "event_hash": self.event_hash,
            "prev_event_hash": self.prev_event_hash,
        }


@dataclass
class PhaseGate:
    """Gate condition for phase completion."""
    phase_id: int
    required_conditions: List[str]  # ["skill_functional", "clarity", "completeness"]
    success: bool = False
    errors: List[str] = field(default_factory=list)

    def evaluate(self) -> bool:
        """Check if gate is satisfied."""
        return self.success and len(self.errors) == 0


@dataclass
class CreatorRequest:
    """Request to Creator 2.0."""
    goal: str  # one-liner description
    data_manifest_id: str
    mode: str  # "skill" | "tool"
    quality_target: float = 0.90  # optimization target


@dataclass
class CreatorOutput:
    """Output from Creator 2.0."""
    skill_id: str
    zip_path: str
    validation_report: Dict[str, Any]
    quality_score: float
    all_phase_events: List[PhaseCompletedEvent] = field(default_factory=list)
    learnings: Dict[str, Any] = field(default_factory=dict)  # for daemon
