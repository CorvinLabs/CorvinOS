"""SkillExecutionListener — tracks real skill usage and outcomes."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import uuid


@dataclass(frozen=True)
class SkillExecutedEvent:
    """Immutable event: a generated skill was executed."""
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    skill_id: str = ""  # Which skill ran
    source_id: str = ""  # Which data source(s) it used
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    duration_ms: float = 0.0  # How long it took
    success: bool = False  # Did it succeed?
    error: Optional[str] = None  # Error message, if any
    outcome_quality: Optional[float] = None  # 0–1, if measurable
    metadata: Dict = field(default_factory=dict)

    def validate(self) -> bool:
        """Validate event correctness."""
        if not self.skill_id or not self.source_id:
            return False
        if self.duration_ms < 0:
            return False
        if self.outcome_quality is not None and not (0 <= self.outcome_quality <= 1):
            return False
        return True


class SkillExecutionListener:
    """
    Subscribes to skill execution events.

    Tracks:
    - When each generated skill runs
    - How long it takes (latency)
    - Whether it succeeds or fails
    - Quality of output (if measurable)
    - Which source(s) it used

    Used to detect which skills are actually useful.
    """

    def __init__(self):
        """Initialize listener."""
        self.events: List[SkillExecutedEvent] = []
        self.skill_stats: Dict[str, Dict] = {}  # {skill_id: {exec_count, error_count, avg_latency}}

    def record_execution(self, event: SkillExecutedEvent) -> None:
        """Record a skill execution event."""
        if not event.validate():
            raise ValueError(f"Invalid execution event: {event}")

        self.events.append(event)

        # Update stats
        if event.skill_id not in self.skill_stats:
            self.skill_stats[event.skill_id] = {
                "exec_count": 0,
                "error_count": 0,
                "total_latency_ms": 0.0,
                "quality_scores": [],
            }

        stats = self.skill_stats[event.skill_id]
        stats["exec_count"] += 1
        if not event.success:
            stats["error_count"] += 1
        stats["total_latency_ms"] += event.duration_ms
        if event.outcome_quality is not None:
            stats["quality_scores"].append(event.outcome_quality)

    def get_all_events(self) -> List[SkillExecutedEvent]:
        """Get all execution events."""
        return list(self.events)

    def get_skill_stats(self, skill_id: str) -> Optional[Dict]:
        """Get stats for a specific skill."""
        if skill_id not in self.skill_stats:
            return None

        stats = self.skill_stats[skill_id]
        avg_latency = (
            stats["total_latency_ms"] / stats["exec_count"]
            if stats["exec_count"] > 0 else 0.0
        )
        error_rate = (
            stats["error_count"] / stats["exec_count"]
            if stats["exec_count"] > 0 else 0.0
        )
        avg_quality = (
            sum(stats["quality_scores"]) / len(stats["quality_scores"])
            if stats["quality_scores"] else None
        )

        return {
            "skill_id": skill_id,
            "exec_count": stats["exec_count"],
            "error_count": stats["error_count"],
            "error_rate": error_rate,
            "avg_latency_ms": avg_latency,
            "avg_quality_score": avg_quality,
        }

    def get_all_skills_stats(self) -> Dict[str, Dict]:
        """Get stats for all skills."""
        return {
            skill_id: self.get_skill_stats(skill_id)
            for skill_id in self.skill_stats
        }

    def get_events_for_skill(self, skill_id: str) -> List[SkillExecutedEvent]:
        """Get all execution events for a specific skill."""
        return [e for e in self.events if e.skill_id == skill_id]

    def reset(self) -> None:
        """Clear all state (for testing)."""
        self.events = []
        self.skill_stats = {}
