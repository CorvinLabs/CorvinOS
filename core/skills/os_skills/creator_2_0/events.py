"""Learning events for Creator 2.0 — Phase completion with loss signals."""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime
import json


@dataclass(frozen=True)
class LossComponents:
    """Loss decomposition for a phase (all 0–1)."""
    relevance: float  # Does the output match the user's intent?
    completeness: float  # Are all requirements covered?
    performance: float  # Is the code efficient and clean?
    maintainability: float  # Is it well-structured and documented?

    def overall(self, weights: Optional[Dict[str, float]] = None) -> float:
        """Compute weighted overall loss (lower is better)."""
        if weights is None:
            weights = {
                "relevance": 0.40,
                "completeness": 0.30,
                "performance": 0.20,
                "maintainability": 0.10,
            }
        return (
            weights["relevance"] * self.relevance
            + weights["completeness"] * self.completeness
            + weights["performance"] * self.performance
            + weights["maintainability"] * self.maintainability
        )

    def validate(self) -> bool:
        """All components must be in [0, 1]."""
        return all(0 <= v <= 1 for v in [self.relevance, self.completeness, self.performance, self.maintainability])


@dataclass(frozen=True)
class PhaseCompletedEvent:
    """Immutable event emitted when a phase completes."""
    phase_num: int  # 0–10
    skill_id: str  # Unique identifier for the skill being created
    duration_ms: float  # Time to execute the phase
    errors: List[str] = field(default_factory=list)  # Any errors encountered
    loss_components: Optional[LossComponents] = None  # Loss decomposition (if applicable)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict = field(default_factory=dict)  # Arbitrary metadata (e.g., user clarifications)

    def overall_loss(self) -> Optional[float]:
        """Return overall loss for this phase (None if no loss_components)."""
        if self.loss_components is None:
            return None
        return self.loss_components.overall()

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict."""
        d = asdict(self)
        if self.loss_components:
            d["loss_components"] = asdict(self.loss_components)
        return d

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class LossEmitter:
    """Emits learning events to the learning system."""

    def __init__(self):
        self.events: List[PhaseCompletedEvent] = []

    def emit(self, event: PhaseCompletedEvent) -> None:
        """Record a phase completion event."""
        if not event.loss_components or event.loss_components.validate():
            self.events.append(event)
        else:
            raise ValueError(f"Invalid loss components: {event.loss_components}")

    def get_events(self) -> List[PhaseCompletedEvent]:
        """Retrieve all emitted events."""
        return list(self.events)

    def overall_skill_loss(self) -> Optional[float]:
        """Compute overall loss for the skill (average across all phases)."""
        losses = [e.overall_loss() for e in self.events if e.overall_loss() is not None]
        if not losses:
            return None
        return sum(losses) / len(losses)

    def validate_skill_loss(self) -> bool:
        """Check that final loss is well-formed (no NaN, no inf)."""
        loss = self.overall_skill_loss()
        if loss is None:
            return True  # No loss to validate
        return not (loss != loss or loss == float("inf") or loss == float("-inf"))  # not NaN and not inf
