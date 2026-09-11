"""FeedbackCollector — collects user ratings and flags."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import uuid


@dataclass(frozen=True)
class FeedbackEvent:
    """Immutable event: user gave feedback on a skill."""
    feedback_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    skill_id: str = ""  # Which skill
    execution_id: str = ""  # Which execution (optional)
    rating: int = 3  # 1–5 (1=bad, 5=excellent)
    signal_type: str = "neutral"  # "positive" (5) | "negative" (1) | "neutral" (2-4)
    comment: Optional[str] = None  # User's optional comment (NOT stored for audit)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict = field(default_factory=dict)

    def validate(self) -> bool:
        """Validate feedback."""
        if not self.skill_id:
            return False
        if not (1 <= self.rating <= 5):
            return False
        if self.signal_type not in ["positive", "negative", "neutral"]:
            return False
        return True

    def to_audit_safe_dict(self) -> Dict:
        """Convert to audit-safe dict (no comment, no PII)."""
        return {
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "execution_id": self.execution_id,
            "rating": self.rating,
            "signal_type": self.signal_type,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class FeedbackCollector:
    """
    Collects user feedback on generated skills.

    Feedback types:
    - Rating (1–5): How good was the skill?
    - Signal: Positive/Negative/Neutral (derived from rating)
    - Comment: Optional user notes (NOT audited for GDPR)

    Stratified sampling: tracks feedback source, applies inverse-prevalence weighting.
    """

    def __init__(self):
        """Initialize collector."""
        self.events: List[FeedbackEvent] = []
        self.skill_feedback: Dict[str, List[FeedbackEvent]] = {}  # {skill_id: [events]}
        self.signal_distribution: Dict[str, int] = {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
        }

    def record_feedback(self, event: FeedbackEvent) -> None:
        """Record user feedback."""
        if not event.validate():
            raise ValueError(f"Invalid feedback event: {event}")

        self.events.append(event)

        # Index by skill
        if event.skill_id not in self.skill_feedback:
            self.skill_feedback[event.skill_id] = []
        self.skill_feedback[event.skill_id].append(event)

        # Track signal distribution
        self.signal_distribution[event.signal_type] += 1

    def create_feedback_event(
        self,
        skill_id: str,
        rating: int,
        execution_id: str = "",
        comment: Optional[str] = None,
    ) -> FeedbackEvent:
        """Create and record a feedback event (convenience method)."""
        # Determine signal type from rating
        if rating >= 5:
            signal_type = "positive"
        elif rating <= 1:
            signal_type = "negative"
        else:
            signal_type = "neutral"

        event = FeedbackEvent(
            skill_id=skill_id,
            rating=rating,
            signal_type=signal_type,
            execution_id=execution_id,
            comment=None,  # Never store comments (GDPR)
        )
        self.record_feedback(event)
        return event

    def get_all_events(self) -> List[FeedbackEvent]:
        """Get all feedback events."""
        return list(self.events)

    def get_feedback_for_skill(self, skill_id: str) -> List[FeedbackEvent]:
        """Get all feedback for a specific skill."""
        return list(self.skill_feedback.get(skill_id, []))

    def get_signal_distribution(self) -> Dict[str, float]:
        """Get % distribution of signal types (for importance weighting)."""
        total = sum(self.signal_distribution.values())
        if total == 0:
            return {"positive": 0, "negative": 0, "neutral": 0}
        return {
            signal: count / total
            for signal, count in self.signal_distribution.items()
        }

    def compute_inverse_prevalence_weights(self) -> Dict[str, float]:
        """
        Compute inverse-prevalence weights for rare signals.

        Rationale: If negative feedback is rare (10%), it matters more than
        neutral feedback (70%). Weight inversely by prevalence.

        Returns:
            {signal_type: weight} where weights sum to 1
        """
        dist = self.get_signal_distribution()
        if all(v == 0 for v in dist.values()):
            # No feedback yet, use uniform
            return {"positive": 1/3, "negative": 1/3, "neutral": 1/3}

        # Inverse prevalence (1 / p(signal))
        inverse_weights = {
            signal: (1 / (p + 1e-6))
            for signal, p in dist.items()
        }

        # Normalize to sum to 1
        total = sum(inverse_weights.values())
        return {
            signal: w / total
            for signal, w in inverse_weights.items()
        }

    def get_average_rating(self, skill_id: str) -> Optional[float]:
        """Get average rating for a skill (1–5, higher is better)."""
        feedback = self.get_feedback_for_skill(skill_id)
        if not feedback:
            return None
        return sum(e.rating for e in feedback) / len(feedback)

    def get_skill_summary(self, skill_id: str) -> Optional[Dict]:
        """Get feedback summary for a skill."""
        feedback = self.get_feedback_for_skill(skill_id)
        if not feedback:
            return None

        signal_counts = {"positive": 0, "negative": 0, "neutral": 0}
        for event in feedback:
            signal_counts[event.signal_type] += 1

        return {
            "skill_id": skill_id,
            "feedback_count": len(feedback),
            "average_rating": sum(e.rating for e in feedback) / len(feedback),
            "signal_counts": signal_counts,
        }

    def reset(self) -> None:
        """Clear all state (for testing)."""
        self.events = []
        self.skill_feedback = {}
        self.signal_distribution = {"positive": 0, "negative": 0, "neutral": 0}
