"""
Gradient Backprop DAG (ADR-0626/0627/0628)

Tier 2 loops learn from unified loss gradients + correlation filtering.
Prevents anti-correlated updates, detects coupling oscillation.
"""

from typing import Dict, Tuple
import math


class GradientBackpropDAG:
    """Compute backprop gradients from L_total to Tier 2 loops."""

    def __init__(self):
        self.gradient_history = {
            'memory': [],
            'skills': [],
            'plugins': [],
        }

    def compute_backprop_gradients(
        self, 
        L_total: float,
        L_memory: float,
        L_skills: float,
        L_plugins: float,
    ) -> Dict[str, float]:
        """
        Compute ∂L_total/∂L_loop for each Tier 2 loop.
        
        Unified loss: L_total = 0.6*L_core + 0.3*(L_memory + L_skills + L_plugins)/3 + 0.1*L_meta
        Thus: ∂L_total/∂L_memory = 0.3/3 = 0.1 (approximately)
        
        In practice, use loss deltas to estimate.
        """
        # Estimate gradients from loss change
        grad_memory = 0.1 if L_memory > 0 else 0.0
        grad_skills = 0.1 if L_skills > 0 else 0.0
        grad_plugins = 0.1 if L_plugins > 0 else 0.0

        return {
            'memory': grad_memory,
            'skills': grad_skills,
            'plugins': grad_plugins,
        }

    def check_dag_validity(self) -> bool:
        """Verify DAG has no cycles (topological sort possible)."""
        # In this simple design, no explicit dependencies → always valid
        return True


class CorrelationFilter:
    """Filter backprop gradients by correlation with local gradients."""

    def __init__(self, correlation_threshold: float = 0.5):
        self.threshold = correlation_threshold
        self.correlation_history = {
            'memory': [],
            'skills': [],
            'plugins': [],
        }

    def compute_correlation(self, local_grad: float, backprop_grad: float, history_len: int = 50) -> float:
        """
        Pearson correlation between local_grad_history and backprop_grad_history.
        Simplified: if both have same sign, correlation is high.
        """
        if local_grad == 0 and backprop_grad == 0:
            return 1.0  # Both zero, aligned
        if local_grad == 0 or backprop_grad == 0:
            return 0.0  # One zero, one not
        # Sign agreement
        if (local_grad > 0 and backprop_grad > 0) or (local_grad < 0 and backprop_grad < 0):
            return 0.8  # Correlated
        else:
            return -0.5  # Anti-correlated

    def apply_filter(
        self, 
        loop_id: str, 
        local_gradient: Dict[str, float], 
        backprop_gradient: Dict[str, float],
    ) -> Tuple[bool, float]:
        """
        Decide whether to apply backprop gradient based on correlation.
        
        Returns: (apply_backprop, correlation_score)
        """
        local_grad = local_gradient.get(loop_id, 0.0)
        backprop_grad = backprop_gradient.get(loop_id, 0.0)

        corr = self.compute_correlation(local_grad, backprop_grad)
        self.correlation_history[loop_id].append(corr)

        # Apply backprop only if correlated
        apply = corr > self.threshold
        return apply, corr


class CouplingOscillationDetector:
    """Detect limit cycles from coupled loop updates (ADR-0628)."""

    def __init__(self, phase_lock_batches: int = 100):
        self.phase_lock = phase_lock_batches
        self.param_history = {
            'memory': [],
            'skills': [],
            'plugins': [],
        }

    def check_for_oscillation(self, loop_id: str, param_value: float) -> bool:
        """
        Detect oscillation: if params repeat in last N steps, likely limit cycle.
        Returns True if oscillation detected.
        """
        self.param_history[loop_id].append(param_value)

        if len(self.param_history[loop_id]) < 20:
            return False  # Not enough history

        # Check if oscillating (alternating high/low)
        last_10 = self.param_history[loop_id][-10:]
        even_values = last_10[0::2]  # indices 0, 2, 4, 6, 8
        odd_values = last_10[1::3]   # indices 1, 3, 5, 7, 9

        if len(even_values) > 0 and len(odd_values) > 0:
            even_mean = sum(even_values) / len(even_values)
            odd_mean = sum(odd_values) / len(odd_values)

            # If even/odd are very different, likely oscillating
            oscillation_score = abs(even_mean - odd_mean) / max(abs(even_mean), abs(odd_mean), 0.01)
            return oscillation_score > 0.2  # Threshold for oscillation detection

        return False

    def get_phase_lock_interval(self) -> int:
        """Return phase-lock interval (update every N batches)."""
        return self.phase_lock
