"""
Base class for all learnable loops in the 9D Learning Vector system.

Every loop (core 6D + infrastructure + meta) implements this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any
import json
from datetime import datetime


class LearningLoop(ABC):
    """
    Abstract base class for all learnable loops.

    Every loop:
    1. Computes a scalar loss value from feedback
    2. Computes gradients w.r.t. learnable parameters
    3. Applies gradient descent updates
    4. Detects convergence
    5. Emits Live-Collector events
    """

    def __init__(self, loop_id: str, tier: int = 2):
        """
        Args:
            loop_id: unique identifier (e.g., "memory", "skills", "plugins")
            tier: 1 (core), 2 (infrastructure), or 3 (meta)
        """
        self.loop_id = loop_id
        self.tier = tier

        # Tier-specific defaults (can be overridden by Meta Loop)
        if tier == 1:
            self.learning_rate = 0.1
            self.damping_factor = 0.9
        elif tier == 2:
            self.learning_rate = 0.01
            self.damping_factor = 0.95
        else:  # tier 3
            self.learning_rate = 0.001
            self.damping_factor = 0.99

        # History tracking
        self.loss_history: List[float] = []
        self.gradient_history: Dict[str, List[float]] = {}
        self.param_history: Dict[str, List[float]] = {}

    @abstractmethod
    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        """
        Compute loss for this loop.

        Args:
            feedback_signals: Dict of measured metrics from downstream
                Example: {'quality': 0.8, 'latency': 45, 'cost': 0.05}

        Returns:
            scalar loss value (0.0 = perfect, 1.0 = worst)

        Implementations must:
        - Normalize components to [0, 1]
        - Use weighted sum (e.g., 0.4*quality + 0.3*latency + 0.2*cost + 0.1*efficiency)
        - Return float
        """
        pass

    @abstractmethod
    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        """
        Compute gradients w.r.t. learnable parameters.

        Args:
            loss: current loss
            prev_loss: previous loss

        Returns:
            {parameter_name: gradient_value}

        Implementations must:
        - Return one gradient per learnable parameter
        - Use numerical differentiation if needed (for validation)
        - Store gradient history for convergence detection
        """
        pass

    @abstractmethod
    def apply_gradients(self, gradients: Dict[str, float], learning_rate: float = None, damping: float = None):
        """
        Apply gradient descent update with damping.

        Update rule:
            new_value = damping * old_value + (1 - damping) * (old_value - learning_rate * gradient)

        Args:
            gradients: from compute_gradients()
            learning_rate: override default (if None, use self.learning_rate)
            damping: override default (if None, use self.damping_factor)

        Implementations must:
        - Apply damping to reduce oscillation
        - Clip parameters to valid ranges
        - Maintain constraints (e.g., weights sum to 1.0)
        - Store parameter history for convergence detection
        """
        pass

    @abstractmethod
    def check_convergence(self, gradient_history: List[float] = None) -> bool:
        """
        Has this loop converged?

        Args:
            gradient_history: gradient magnitudes over last N steps
                             (if None, use self.gradient_history)

        Returns:
            True if converged, False otherwise

        Implementations must:
        - Define convergence criteria (e.g., gradient < threshold, variance < threshold)
        - Be testable with synthetic data (100-batch runs)
        """
        pass

    def emit_event(self, collector_integration, **event_data):
        """
        Emit a Live-Collector event for this loop.

        Override in subclasses to define what events are emitted.

        Args:
            collector_integration: LiveCollectorIntegration instance
            **event_data: loop-specific data to emit
        """
        pass

    # Utility methods (shared by all loops)

    def normalize_components(self, components: Dict[str, float], weights: Dict[str, float]) -> float:
        """
        Compute weighted sum of normalized components.

        Args:
            components: {name: value} where each value is in [0, 1]
            weights: {name: weight} where sum(weights.values()) ≈ 1.0

        Returns:
            scalar loss in [0, 1]
        """
        assert all(0 <= v <= 1 for v in components.values()), f"Components not normalized: {components}"
        assert abs(sum(weights.values()) - 1.0) < 0.01, f"Weights don't sum to 1.0: {weights}"

        loss = sum(weights[name] * components[name] for name in components)
        return loss

    def clip_parameter(self, value: float, min_val: float, max_val: float) -> float:
        """Clip parameter to valid range."""
        return max(min_val, min(max_val, value))

    def record_loss(self, loss: float):
        """Record loss for convergence tracking."""
        self.loss_history.append(loss)

    def record_gradients(self, gradients: Dict[str, float]):
        """Record gradients for convergence tracking."""
        for param_name, gradient in gradients.items():
            if param_name not in self.gradient_history:
                self.gradient_history[param_name] = []
            self.gradient_history[param_name].append(gradient)

    def record_parameters(self, parameters: Dict[str, float]):
        """Record parameter values for convergence tracking."""
        for param_name, value in parameters.items():
            if param_name not in self.param_history:
                self.param_history[param_name] = []
            self.param_history[param_name].append(value)

    def get_avg_gradient_magnitude(self, last_n: int = 100) -> float:
        """Get average gradient magnitude over last N steps."""
        all_recent_gradients = []
        for param_gradients in self.gradient_history.values():
            all_recent_gradients.extend([abs(g) for g in param_gradients[-last_n:]])

        if not all_recent_gradients:
            return float('inf')

        return sum(all_recent_gradients) / len(all_recent_gradients)

    def get_loss_variance(self, last_n: int = 100) -> float:
        """Get loss variance over last N steps."""
        recent_losses = self.loss_history[-last_n:]

        if len(recent_losses) < 2:
            return float('inf')

        mean = sum(recent_losses) / len(recent_losses)
        variance = sum((x - mean) ** 2 for x in recent_losses) / len(recent_losses)

        return variance

    def get_parameter_stability(self, last_n: int = 100) -> float:
        """Get parameter change rate (lower = more stable)."""
        max_change = 0.0

        for param_history in self.param_history.values():
            recent = param_history[-last_n:]
            if len(recent) < 2:
                continue

            changes = [abs(recent[i+1] - recent[i]) / max(abs(recent[i]), 0.01)
                      for i in range(len(recent)-1)]
            max_change = max(max_change, sum(changes) / len(changes))

        return max_change
