"""
MetaOptimizer (Tier 3 Meta Loop) — ADR-0623, ADR-0624, ADR-0625

Self-tuning optimizer for learning rate and damping factors across Tier 1 (core)
and Tier 2 (infrastructure) loops.

Learnable parameters:
  α_core: learning rate for core loops, ∈ [0.001, 0.3]
  α_infra: learning rate for infrastructure loops, ∈ [0.001, 0.3]
  damping_core: momentum for core loops, ∈ [0.8, 0.99]
  damping_infra: momentum for infra loops, ∈ [0.8, 0.99]
  convergence_threshold: gradient magnitude threshold, ∈ [0.0001, 0.01]
  variance_threshold: loss variance threshold, ∈ [0.001, 0.1]

Tuning law:
  ∂L_total/∂α = mean(loss_delta) over last 100 batches
  Phase-locking: update every 100 batches only
  Stability gate: only tune if avg_gradient > 0.001 for 100 consecutive batches
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from core.learning.base import LearningLoop
import json
from datetime import datetime


@dataclass
class MetaOptimizerState:
    """Immutable snapshot of meta optimizer state for checkpointing."""
    step_count: int
    alpha_core: float
    alpha_infra: float
    damping_core: float
    damping_infra: float
    convergence_threshold: float
    variance_threshold: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class MetaOptimizer(LearningLoop):
    """
    Tier 3 meta loop that tunes hyperparameters of Tier 1 and Tier 2 loops.

    Extends LearningLoop but operates at meta-level:
    - Loss is computed from drift in core/infra loss convergence
    - Gradients are w.r.t. learning rates and damping factors
    - Updates apply every 100 batches (phase-locking)
    """

    def __init__(self, tenant_id: str = "_default"):
        """
        Initialize MetaOptimizer with learnable hyperparameters.

        Args:
            tenant_id: for audit/data isolation
        """
        super().__init__(loop_id="meta", tier=3)
        self.tenant_id = tenant_id

        # ===== LEARNABLE PARAMETERS (with bounds) =====
        self.alpha_core = 0.1  # learning rate for core loops
        self.alpha_core_min, self.alpha_core_max = 0.001, 0.3

        self.alpha_infra = 0.01  # learning rate for infra loops
        self.alpha_infra_min, self.alpha_infra_max = 0.001, 0.3

        self.damping_core = 0.9  # momentum for core loops
        self.damping_core_min, self.damping_core_max = 0.8, 0.99

        self.damping_infra = 0.95  # momentum for infra loops
        self.damping_infra_min, self.damping_infra_max = 0.8, 0.99

        self.convergence_threshold = 0.001  # gradient threshold
        self.convergence_threshold_min, self.convergence_threshold_max = 0.0001, 0.01

        self.variance_threshold = 0.05  # loss variance threshold
        self.variance_threshold_min, self.variance_threshold_max = 0.001, 0.1

        # ===== PHASE-LOCKING =====
        self.phase_lock_interval = 100  # update every 100 batches
        self.last_update_step = 0

        # ===== STABILITY GATE =====
        self.min_gradient_signal = 0.001  # only tune if gradient signal strong
        self.stable_signal_count = 0  # consecutive steps with strong signal
        self.stable_signal_threshold = 100  # require 100 consecutive strong signals

        # ===== HISTORY TRACKING =====
        self.last_100_loss_deltas: List[float] = []  # for gradient computation
        self.last_100_gradients: Dict[str, List[float]] = {
            'alpha_core': [],
            'alpha_infra': [],
            'damping_core': [],
            'damping_infra': [],
            'convergence_threshold': [],
            'variance_threshold': [],
        }

        # ===== SAFETY & BOUNDS ENFORCEMENT =====
        self.divergence_detected = False
        self.conservative_mode = False

    # ===== REQUIRED ABSTRACT METHODS =====

    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        """
        Compute meta-level loss from downstream loop performance.

        Meta loss is the sum of:
          L_meta = 0.5 * L_drift + 0.3 * L_stability + 0.2 * L_convergence

        where:
          L_drift: large changes in core/infra loss (we want stability)
          L_stability: large swings in parameter updates (we want smooth learning)
          L_convergence: slow convergence rate (we want fast convergence)

        Args:
            feedback_signals: {
                'core_loss': current core loss,
                'infra_loss': current infra loss,
                'prev_core_loss': previous core loss,
                'prev_infra_loss': previous infra loss,
                'core_loss_variance': variance of core loop losses,
                'infra_loss_variance': variance of infra loop losses,
                'avg_gradient_magnitude': average gradient magnitude,
            }

        Returns:
            scalar loss in [0, 1]
        """
        core_loss = feedback_signals.get('core_loss', 0.0)
        infra_loss = feedback_signals.get('infra_loss', 0.0)
        prev_core_loss = feedback_signals.get('prev_core_loss', core_loss)
        prev_infra_loss = feedback_signals.get('prev_infra_loss', infra_loss)

        # L_drift: penalize large loss deltas (want smooth convergence)
        core_delta = abs(core_loss - prev_core_loss)
        infra_delta = abs(infra_loss - prev_infra_loss)
        L_drift = min(1.0, (core_delta + infra_delta) / 2.0)

        # L_stability: penalize parameter instability
        param_stability = self.get_parameter_stability(last_n=100)
        L_stability = min(1.0, param_stability)

        # L_convergence: penalize slow convergence (high variance = slow)
        core_var = feedback_signals.get('core_loss_variance', float('inf'))
        infra_var = feedback_signals.get('infra_loss_variance', float('inf'))
        avg_var = min(float('inf'), (core_var + infra_var) / 2.0)
        L_convergence = min(1.0, avg_var / 0.1)  # normalize to 0.1

        # Weighted sum
        components = {
            'drift': L_drift,
            'stability': L_stability,
            'convergence': L_convergence,
        }
        weights = {
            'drift': 0.5,
            'stability': 0.3,
            'convergence': 0.2,
        }

        loss = self.normalize_components(components, weights)
        self.record_loss(loss)

        return float(max(0.0, min(1.0, loss)))

    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        """
        Compute gradients w.r.t. learnable parameters.

        Tuning law:
          ∂L_total/∂α_core = mean(loss_delta) over last 100 batches
          ∂L_total/∂damping = -∂L_total/∂α (inverse relationship)

        Args:
            loss: current meta loss
            prev_loss: previous meta loss

        Returns:
            {parameter_name: gradient_value}
        """
        loss_delta = loss - prev_loss
        self.last_100_loss_deltas.append(loss_delta)
        if len(self.last_100_loss_deltas) > 100:
            self.last_100_loss_deltas.pop(0)

        # Average loss delta (positive = loss increasing = bad)
        avg_loss_delta = sum(self.last_100_loss_deltas) / len(self.last_100_loss_deltas)

        # Gradient tuning law: if loss is increasing, reduce learning rates
        # If loss is decreasing (neg avg_delta), we can increase learning rates
        grad_alpha_core = avg_loss_delta
        grad_alpha_infra = avg_loss_delta
        grad_damping_core = -avg_loss_delta * 0.5  # inverse relationship
        grad_damping_infra = -avg_loss_delta * 0.5

        # Convergence and variance thresholds: adjust based on current performance
        # If variance is high, increase threshold (be more lenient)
        core_var = self.get_loss_variance(last_n=100)
        grad_convergence_threshold = min(1.0, core_var / 0.1) * 0.001
        grad_variance_threshold = min(1.0, core_var / 0.1) * 0.01

        gradients = {
            'alpha_core': grad_alpha_core,
            'alpha_infra': grad_alpha_infra,
            'damping_core': grad_damping_core,
            'damping_infra': grad_damping_infra,
            'convergence_threshold': grad_convergence_threshold,
            'variance_threshold': grad_variance_threshold,
        }

        self.record_gradients(gradients)

        return gradients

    def apply_gradients(
        self,
        gradients: Dict[str, float],
        learning_rate: float = None,
        damping: float = None,
    ):
        """
        Apply gradient descent with damping (phase-locked at 100-batch intervals).

        Update rule:
          new_param = damping * old_param + (1 - damping) * (old_param - lr * gradient)

        Args:
            gradients: from compute_gradients()
            learning_rate: override tier-3 default (0.001)
            damping: override tier-3 default (0.99)
        """
        if learning_rate is None:
            learning_rate = self.learning_rate  # 0.001 for tier 3
        if damping is None:
            damping = self.damping_factor  # 0.99 for tier 3

        # Phase-locking: only apply every 100 batches
        if len(self.loss_history) % self.phase_lock_interval != 0:
            return

        # Stability gate: only tune if gradient signal is strong
        avg_grad_mag = self.get_avg_gradient_magnitude(last_n=100)
        if avg_grad_mag < self.min_gradient_signal:
            self.stable_signal_count = 0
            return
        else:
            self.stable_signal_count += 1

        # Only apply update if we have 100 consecutive steps with strong signal
        if self.stable_signal_count < self.stable_signal_threshold:
            return

        # ===== UPDATE EACH LEARNABLE PARAMETER WITH BOUNDS ENFORCEMENT =====

        # Update alpha_core
        old_alpha_core = self.alpha_core
        self.alpha_core = (
            damping * self.alpha_core
            + (1 - damping) * (self.alpha_core - learning_rate * gradients.get('alpha_core', 0.0))
        )
        self.alpha_core = self.clip_parameter(
            self.alpha_core, self.alpha_core_min, self.alpha_core_max
        )

        # Update alpha_infra
        old_alpha_infra = self.alpha_infra
        self.alpha_infra = (
            damping * self.alpha_infra
            + (1 - damping) * (self.alpha_infra - learning_rate * gradients.get('alpha_infra', 0.0))
        )
        self.alpha_infra = self.clip_parameter(
            self.alpha_infra, self.alpha_infra_min, self.alpha_infra_max
        )

        # Update damping_core
        old_damping_core = self.damping_core
        self.damping_core = (
            damping * self.damping_core
            + (1 - damping) * (self.damping_core - learning_rate * gradients.get('damping_core', 0.0))
        )
        self.damping_core = self.clip_parameter(
            self.damping_core, self.damping_core_min, self.damping_core_max
        )

        # Update damping_infra
        old_damping_infra = self.damping_infra
        self.damping_infra = (
            damping * self.damping_infra
            + (1 - damping) * (self.damping_infra - learning_rate * gradients.get('damping_infra', 0.0))
        )
        self.damping_infra = self.clip_parameter(
            self.damping_infra, self.damping_infra_min, self.damping_infra_max
        )

        # Update convergence_threshold
        old_convergence_threshold = self.convergence_threshold
        self.convergence_threshold = (
            damping * self.convergence_threshold
            + (1 - damping) * (self.convergence_threshold - learning_rate * gradients.get('convergence_threshold', 0.0))
        )
        self.convergence_threshold = self.clip_parameter(
            self.convergence_threshold,
            self.convergence_threshold_min,
            self.convergence_threshold_max,
        )

        # Update variance_threshold
        old_variance_threshold = self.variance_threshold
        self.variance_threshold = (
            damping * self.variance_threshold
            + (1 - damping) * (self.variance_threshold - learning_rate * gradients.get('variance_threshold', 0.0))
        )
        self.variance_threshold = self.clip_parameter(
            self.variance_threshold,
            self.variance_threshold_min,
            self.variance_threshold_max,
        )

        # Record updated parameters
        self.record_parameters({
            'alpha_core': self.alpha_core,
            'alpha_infra': self.alpha_infra,
            'damping_core': self.damping_core,
            'damping_infra': self.damping_infra,
            'convergence_threshold': self.convergence_threshold,
            'variance_threshold': self.variance_threshold,
        })

        self.last_update_step = len(self.loss_history)

    def check_convergence(self, gradient_history: List[float] = None) -> bool:
        """
        Check if meta loop has converged.

        Criteria:
          1. Average gradient magnitude < convergence_threshold
          2. Loss variance < variance_threshold over last 100 steps
          3. Parameter changes < 0.1% over last 100 steps

        Args:
            gradient_history: ignored (uses self.gradient_history)

        Returns:
            True if converged, False otherwise
        """
        if len(self.loss_history) < 100:
            return False

        # Criterion 1: gradient magnitude
        avg_grad_mag = self.get_avg_gradient_magnitude(last_n=100)
        if avg_grad_mag > self.convergence_threshold:
            return False

        # Criterion 2: loss variance
        loss_var = self.get_loss_variance(last_n=100)
        if loss_var > self.variance_threshold:
            return False

        # Criterion 3: parameter stability
        param_stability = self.get_parameter_stability(last_n=100)
        if param_stability > 0.001:  # < 0.1% change
            return False

        return True

    def emit_event(self, collector_integration, **event_data):
        """
        Emit a meta_tuning event to Live-Collector.

        Args:
            collector_integration: LiveCollectorIntegration instance
            **event_data: optional event data (loss, gradients, etc.)
        """
        if not collector_integration:
            return

        collector_integration.on_meta_tuning(
            step_count=len(self.loss_history),
            alpha_core=self.alpha_core,
            alpha_infra=self.alpha_infra,
            damping_core=self.damping_core,
            damping_infra=self.damping_infra,
            convergence_threshold=self.convergence_threshold,
            variance_threshold=self.variance_threshold,
            is_converged=self.check_convergence(),
        )

    # ===== ADDITIONAL METHODS FOR META OPTIMIZATION =====

    def get_tuned_hyperparameters(self) -> Dict[str, float]:
        """
        Get current tuned hyperparameters for use by Tier 1/2 loops.

        Returns:
            {
                'alpha_core': float,
                'alpha_infra': float,
                'damping_core': float,
                'damping_infra': float,
                'convergence_threshold': float,
                'variance_threshold': float,
            }
        """
        return {
            'alpha_core': self.alpha_core,
            'alpha_infra': self.alpha_infra,
            'damping_core': self.damping_core,
            'damping_infra': self.damping_infra,
            'convergence_threshold': self.convergence_threshold,
            'variance_threshold': self.variance_threshold,
        }

    def save_checkpoint(self, checkpoint_path: str) -> bool:
        """
        Save meta optimizer state to checkpoint file (every 100 batches).

        Args:
            checkpoint_path: file path to save to

        Returns:
            True if successful, False otherwise
        """
        try:
            state = MetaOptimizerState(
                step_count=len(self.loss_history),
                alpha_core=self.alpha_core,
                alpha_infra=self.alpha_infra,
                damping_core=self.damping_core,
                damping_infra=self.damping_infra,
                convergence_threshold=self.convergence_threshold,
                variance_threshold=self.variance_threshold,
            )
            with open(checkpoint_path, 'w') as f:
                json.dump({
                    'state': {
                        'step_count': state.step_count,
                        'alpha_core': state.alpha_core,
                        'alpha_infra': state.alpha_infra,
                        'damping_core': state.damping_core,
                        'damping_infra': state.damping_infra,
                        'convergence_threshold': state.convergence_threshold,
                        'variance_threshold': state.variance_threshold,
                        'timestamp': state.timestamp,
                    }
                }, f, indent=2)
            return True
        except Exception as e:
            print(f"Failed to save checkpoint: {e}")
            return False

    def restore_checkpoint(self, checkpoint_path: str) -> bool:
        """
        Restore meta optimizer state from checkpoint file.

        Args:
            checkpoint_path: file path to load from

        Returns:
            True if successful, False otherwise
        """
        try:
            with open(checkpoint_path, 'r') as f:
                data = json.load(f)

            state = data['state']
            self.alpha_core = state['alpha_core']
            self.alpha_infra = state['alpha_infra']
            self.damping_core = state['damping_core']
            self.damping_infra = state['damping_infra']
            self.convergence_threshold = state['convergence_threshold']
            self.variance_threshold = state['variance_threshold']

            return True
        except Exception as e:
            print(f"Failed to restore checkpoint: {e}")
            return False

    def get_state_snapshot(self) -> Dict[str, Any]:
        """
        Get complete state snapshot for debugging/visualization.

        Returns:
            Dict with all learnable parameters, history, and convergence status
        """
        return {
            'step_count': len(self.loss_history),
            'alpha_core': self.alpha_core,
            'alpha_infra': self.alpha_infra,
            'damping_core': self.damping_core,
            'damping_infra': self.damping_infra,
            'convergence_threshold': self.convergence_threshold,
            'variance_threshold': self.variance_threshold,
            'is_converged': self.check_convergence(),
            'loss_history': self.loss_history[-100:],
            'last_100_loss_deltas': self.last_100_loss_deltas,
            'stable_signal_count': self.stable_signal_count,
            'conservative_mode': self.conservative_mode,
            'divergence_detected': self.divergence_detected,
            'tuned_hyperparameters': self.get_tuned_hyperparameters(),
        }
