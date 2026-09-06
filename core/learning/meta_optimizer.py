"""
Meta Loop Optimizer (ADR-0623/0624/0625)

Tier 3 self-tuning: learns optimal hyperparameters for Tier 1/2 loops
- α_core ∈ [0.001, 0.3]: learning rate for Tier 1
- α_infra ∈ [0.001, 0.3]: learning rate for Tier 2
- damping_core ∈ [0.8, 0.99]: exponential smoothing for Tier 1
- damping_infra ∈ [0.8, 0.99]: exponential smoothing for Tier 2

Updates every 100 batches (phase-locked).
"""

from typing import Dict
from core.learning.base import LearningLoop


class MetaOptimizer(LearningLoop):
    """Tier 3: self-tuning hyperparameters for Tier 1/2 loops."""

    def __init__(self, tenant_id: str = "_default"):
        super().__init__(loop_id="meta", tier=3)
        self.tenant_id = tenant_id

        # Tunable parameters (Tier 1/2 hyperparameters)
        self.α_core = 0.1
        self.α_infra = 0.01
        self.damping_core = 0.9
        self.damping_infra = 0.95
        self.convergence_threshold = 0.001
        self.variance_threshold = 0.01

        # Meta-level constants (never tuned)
        self.learning_rate_meta = 0.001  # fixed
        self.phase_lock_interval = 100   # update every 100 batches only

        # Tracking
        self.update_count = 0
        self.conservative_mode = False
        self.consecutive_worsening = 0

    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        loss_delta_core = feedback_signals.get('loss_delta_core', 0.0)
        loss_delta_infra = feedback_signals.get('loss_delta_infra', 0.0)
        meta_loss = min(1.0, max(0.0, (loss_delta_core + loss_delta_infra) / 0.1))
        self.record_loss(meta_loss)
        return float(meta_loss)

    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        delta_loss = loss - prev_loss
        grad_α = -delta_loss * 0.01
        variance = self.get_loss_variance(last_n=10) if len(self.loss_history) > 10 else 0.01
        grad_damping = max(0.0, (variance - 0.01)) * 0.1

        gradients = {
            'α_core': grad_α,
            'α_infra': grad_α,
            'damping_core': grad_damping,
            'damping_infra': grad_damping,
        }
        self.record_gradients(gradients)
        return gradients

    def apply_gradients(self, gradients: Dict[str, float], learning_rate: float = None, damping: float = None):
        if learning_rate is None:
            learning_rate = self.learning_rate_meta
        if damping is None:
            damping = 0.95

        # Stability gate
        avg_gradient = self.get_avg_gradient_magnitude(last_n=10) if len(self.loss_history) > 10 else 0.01
        if avg_gradient < 0.0001:
            return

        # Conservative mode
        effective_learning_rate = learning_rate * (0.5 if self.conservative_mode else 1.0)

        # Update α_core
        old_α_core = self.α_core
        raw_α_core = old_α_core - effective_learning_rate * gradients['α_core']
        new_α_core = damping * old_α_core + (1 - damping) * raw_α_core
        self.α_core = self.clip_parameter(new_α_core, 0.001, 0.3)

        # Update α_infra
        old_α_infra = self.α_infra
        raw_α_infra = old_α_infra - effective_learning_rate * gradients['α_infra']
        new_α_infra = damping * old_α_infra + (1 - damping) * raw_α_infra
        self.α_infra = self.clip_parameter(new_α_infra, 0.001, 0.3)

        # Update damping_core
        old_damping_core = self.damping_core
        raw_damping_core = old_damping_core + effective_learning_rate * gradients['damping_core']
        new_damping_core = damping * old_damping_core + (1 - damping) * raw_damping_core
        self.damping_core = self.clip_parameter(new_damping_core, 0.8, 0.99)

        # Update damping_infra
        old_damping_infra = self.damping_infra
        raw_damping_infra = old_damping_infra + effective_learning_rate * gradients['damping_infra']
        new_damping_infra = damping * old_damping_infra + (1 - damping) * raw_damping_infra
        self.damping_infra = self.clip_parameter(new_damping_infra, 0.8, 0.99)

        # Detect worsening (conservative mode)
        if len(self.loss_history) > 10:
            recent_losses = self.loss_history[-10:]
            avg_recent = sum(recent_losses) / len(recent_losses)
            if avg_recent > 0.5:
                self.consecutive_worsening += 1
                if self.consecutive_worsening > 5:
                    self.conservative_mode = True
            else:
                self.consecutive_worsening = 0
                self.conservative_mode = False

        self.record_parameters({
            'α_core': self.α_core,
            'α_infra': self.α_infra,
            'damping_core': self.damping_core,
            'damping_infra': self.damping_infra,
        })
        self.update_count += 1

    def check_convergence(self, gradient_history=None) -> bool:
        if len(self.loss_history) < 50:
            return False
        avg_gradient = self.get_avg_gradient_magnitude(last_n=50)
        param_stability = self.get_parameter_stability(last_n=50)
        return avg_gradient < 0.0001 and param_stability < 0.01

    def get_state(self) -> Dict:
        return {
            'α_core': float(self.α_core),
            'α_infra': float(self.α_infra),
            'damping_core': float(self.damping_core),
            'damping_infra': float(self.damping_infra),
            'update_count': self.update_count,
        }

    def set_state(self, state: Dict):
        self.α_core = state['α_core']
        self.α_infra = state['α_infra']
        self.damping_core = state['damping_core']
        self.damping_infra = state['damping_infra']
        self.update_count = state.get('update_count', 0)

    def process_feedback_signal(self, feedback_outcomes: list[Dict[str, float]]) -> bool:
        """Process user feedback outcomes and adjust tuning conservatively.

        Args:
            feedback_outcomes: List of feedback dicts with:
                - outcome_feedback: "yes"|"no"|"unknown"
                - quality_rating: 1-5 or None
                - confidence: 0-1
                - preference_feedback: "llm"|"deterministic"|"either"

        Returns:
            True if feedback was processed and parameters updated
        """
        if not feedback_outcomes or len(feedback_outcomes) < 10:
            return False  # Buffer until we have >= 10 samples

        # Compute weighted average of feedback
        total_confidence = 0.0
        yes_count = 0
        no_count = 0

        for fb in feedback_outcomes:
            confidence = fb.get('confidence', 0.5)
            outcome = fb.get('outcome_feedback', 'unknown')

            total_confidence += confidence
            if outcome == 'yes':
                yes_count += confidence
            elif outcome == 'no':
                no_count += confidence

        avg_confidence = total_confidence / len(feedback_outcomes) if feedback_outcomes else 0.0

        # Weighted consensus: positive if yes_count > no_count
        consensus_signal = yes_count - no_count

        # Detect contradictions (both yes and no present)
        has_contradiction = (yes_count > 0 and no_count > 0)
        if has_contradiction:
            self.conservative_mode = True

        # Apply signal as loss delta
        if avg_confidence >= 0.6:  # Only apply if confident
            feedback_loss_delta = -consensus_signal * 0.01  # Normalize signal
            feedback_gradients = {
                'α_core': -feedback_loss_delta * 0.5,
                'α_infra': -feedback_loss_delta * 0.3,
                'damping_core': 0.0,
                'damping_infra': 0.0,
            }
            self.apply_gradients(feedback_gradients, learning_rate=self.learning_rate_meta * 0.5)
            return True

        return False

    def detect_feedback_divergence(self, old_loss: float, new_loss: float) -> bool:
        """Detect if feedback-guided optimization made things worse.

        If loss increased after applying feedback, enable conservative mode
        and potentially rollback.

        Returns:
            True if loss worsened (divergence detected)
        """
        if new_loss > old_loss + 0.05:  # Significant worsening
            self.conservative_mode = True
            self.consecutive_worsening += 1
            return True

        self.consecutive_worsening = max(0, self.consecutive_worsening - 1)
        if self.consecutive_worsening == 0:
            self.conservative_mode = False

        return False

    def rollback_to_state(self, saved_state: Dict):
        """Rollback parameters to a previously saved state.

        Used when feedback-guided optimization diverges.
        """
        self.set_state(saved_state)
        self.conservative_mode = True

    def emit_event(self, collector_integration, **event_data):
        if collector_integration:
            collector_integration.on_meta_decision(
                α_core=float(self.α_core),
                α_infra=float(self.α_infra),
                damping_core=float(self.damping_core),
                damping_infra=float(self.damping_infra),
                feedback=event_data.get('feedback', {}),
                conservative_mode=self.conservative_mode,
            )
