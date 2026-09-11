"""WeightLearner — gradient descent learning with adaptive rates."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from enum import Enum
import hashlib


class ConvergenceStatus(Enum):
    """Status of weight convergence."""
    LEARNING = "learning"  # Still updating
    CONVERGED = "converged"  # Stabilized
    OSCILLATING = "oscillating"  # Amplitude too high
    DISABLED = "disabled"  # Learning disabled (too many attempts)


@dataclass(frozen=True)
class WeightUpdateEvent:
    """Immutable event: a weight was updated via learning."""
    weight_id: str
    weight_name: str  # e.g., "source:memory_relevance"
    old_value: float
    new_value: float
    gradient: float
    learning_rate: float
    delta: float  # weight_delta = old - new
    convergence_status: str = "learning"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict = field(default_factory=dict)

    def validate(self) -> bool:
        """Validate event."""
        if not self.weight_name:
            return False
        if self.convergence_status not in [
            "learning",
            "converged",
            "oscillating",
            "disabled",
        ]:
            return False
        return True


class WeightLearner:
    """
    Gradient descent learning with adaptive learning rates.

    Algorithm:
    1. Initialize weights from prior (or 0.5)
    2. On feedback, compute gradient = (loss_after - loss_before) / weight_delta
    3. Update: weight_new = weight_old - lr * gradient
    4. Adaptive LR: reduce if oscillation (amplitude > 0.10) detected
    5. Per-weight convergence: |delta| < ε for 10 iterations + amplitude < 0.10
    6. Inverse-prevalence weighting: rare signals matter more

    Deterministic: no randomness, fully reproducible.
    """

    def __init__(self, base_learning_rate: float = 0.01, epsilon: float = 0.002):
        """
        Initialize learner.

        Args:
            base_learning_rate: Starting LR (capped at 0.01)
            epsilon: Convergence threshold (default 0.002 for realistic convergence)
        """
        self.base_lr = min(base_learning_rate, 0.01)  # Cap at 0.01
        self.epsilon = epsilon

        self.weights: Dict[str, float] = {}  # {weight_name: value}
        self.learning_rates: Dict[str, float] = {}  # {weight_name: current_lr}
        self.convergence_status: Dict[str, ConvergenceStatus] = {}
        self.delta_history: Dict[str, List[float]] = {}  # {weight_name: [deltas]}
        self.update_count: Dict[str, int] = {}  # {weight_name: count}
        self.events: List[WeightUpdateEvent] = []
        self.momentum: Dict[str, float] = {}  # {weight_name: prev_delta}

    def initialize_weight(self, weight_name: str, initial_value: float = 0.5) -> None:
        """Initialize a weight (before learning)."""
        if not (0 <= initial_value <= 1):
            raise ValueError(f"Weight must be in [0, 1], got {initial_value}")

        self.weights[weight_name] = initial_value
        self.learning_rates[weight_name] = self.base_lr
        self.convergence_status[weight_name] = ConvergenceStatus.LEARNING
        self.delta_history[weight_name] = []
        self.update_count[weight_name] = 0
        self.momentum[weight_name] = 0.0

    def update_weight(
        self,
        weight_name: str,
        loss_before: float,
        loss_after: float,
        weight_delta: float,
        signal_importance: float = 1.0,  # From inverse-prevalence weighting
    ) -> WeightUpdateEvent:
        """
        Update a weight based on loss change and gradient.

        Args:
            weight_name: Name of the weight
            loss_before: Loss before weight change
            loss_after: Loss after weight change
            weight_delta: Change in weight during this update
            signal_importance: Importance weight (>1 for rare signals)

        Returns:
            WeightUpdateEvent (immutable)
        """
        if weight_name not in self.weights:
            self.initialize_weight(weight_name)

        # Check if learning is disabled
        if self.convergence_status[weight_name] == ConvergenceStatus.DISABLED:
            return self._create_no_update_event(weight_name, "Learning disabled")

        # Compute gradient
        loss_change = loss_after - loss_before
        gradient = loss_change / (weight_delta + 1e-6)  # Avoid division by zero

        # Apply importance weighting
        gradient *= signal_importance

        # Get current learning rate
        lr = self.learning_rates[weight_name]

        # Momentum term (smooth oscillations)
        prev_delta = self.momentum[weight_name]
        momentum_term = 0.9 * prev_delta + 0.1 * gradient

        # Update weight
        old_value = self.weights[weight_name]
        delta = lr * momentum_term
        new_value = old_value - delta

        # Clamp to [0, 1]
        new_value = max(0.0, min(1.0, new_value))
        delta = old_value - new_value

        # Update state
        self.weights[weight_name] = new_value
        self.momentum[weight_name] = delta
        self.delta_history[weight_name].append(delta)
        self.update_count[weight_name] += 1

        # Check convergence
        convergence = self._check_convergence(weight_name)

        # Adaptive learning rate
        if convergence == ConvergenceStatus.OSCILLATING:
            new_lr = lr * 0.1  # Reduce by 10x
            self.learning_rates[weight_name] = new_lr

        self.convergence_status[weight_name] = convergence

        # Create event
        event = WeightUpdateEvent(
            weight_id=self._hash_weight_name(weight_name),
            weight_name=weight_name,
            old_value=old_value,
            new_value=new_value,
            gradient=gradient,
            learning_rate=lr,
            delta=delta,
            convergence_status=convergence.value,
            metadata={
                "loss_change": loss_change,
                "weight_delta_input": weight_delta,
                "signal_importance": signal_importance,
                "momentum_applied": momentum_term,
            }
        )

        if event.validate():
            self.events.append(event)

        return event

    def _check_convergence(self, weight_name: str) -> ConvergenceStatus:
        """Check if weight has converged."""
        if self.update_count[weight_name] < 10:
            return ConvergenceStatus.LEARNING

        # Get last 10 deltas
        recent = self.delta_history[weight_name][-10:]

        # Check magnitude
        avg_magnitude = sum(abs(d) for d in recent) / len(recent)
        if avg_magnitude < self.epsilon:
            # Check amplitude (no oscillation)
            amplitude = max(recent) - min(recent)
            if amplitude < 0.10:
                return ConvergenceStatus.CONVERGED
            else:
                return ConvergenceStatus.OSCILLATING

        return ConvergenceStatus.LEARNING

    def disable_learning(self, weight_name: str) -> None:
        """Disable learning for a weight (after many failed attempts)."""
        self.convergence_status[weight_name] = ConvergenceStatus.DISABLED

    def get_weight(self, weight_name: str) -> Optional[float]:
        """Get current weight value."""
        return self.weights.get(weight_name)

    def get_all_weights(self) -> Dict[str, float]:
        """Get all weight values."""
        return dict(self.weights)

    def get_convergence_status(self, weight_name: str) -> Optional[ConvergenceStatus]:
        """Get convergence status for a weight."""
        return self.convergence_status.get(weight_name)

    def get_all_convergence_statuses(self) -> Dict[str, ConvergenceStatus]:
        """Get convergence status for all weights."""
        return dict(self.convergence_status)

    def get_learning_rate(self, weight_name: str) -> Optional[float]:
        """Get current learning rate for a weight."""
        return self.learning_rates.get(weight_name)

    def get_all_events(self) -> List[WeightUpdateEvent]:
        """Get all weight update events."""
        return list(self.events)

    def get_events_for_weight(self, weight_name: str) -> List[WeightUpdateEvent]:
        """Get all events for a specific weight."""
        return [e for e in self.events if e.weight_name == weight_name]

    def _create_no_update_event(
        self, weight_name: str, reason: str
    ) -> WeightUpdateEvent:
        """Create a no-op event (learning disabled)."""
        return WeightUpdateEvent(
            weight_id=self._hash_weight_name(weight_name),
            weight_name=weight_name,
            old_value=self.weights.get(weight_name, 0.5),
            new_value=self.weights.get(weight_name, 0.5),
            gradient=0.0,
            learning_rate=0.0,
            delta=0.0,
            convergence_status=ConvergenceStatus.DISABLED.value,
            metadata={"reason": reason},
        )

    @staticmethod
    def _hash_weight_name(name: str) -> str:
        """Hash weight name for audit trail."""
        return hashlib.sha256(name.encode()).hexdigest()[:16]

    def reset(self) -> None:
        """Clear all state (for testing)."""
        self.weights = {}
        self.learning_rates = {}
        self.convergence_status = {}
        self.delta_history = {}
        self.update_count = {}
        self.events = []
        self.momentum = {}
