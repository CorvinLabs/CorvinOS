"""
Memory Loop Optimizer (ADR-0620)

Learns to optimize:
- context_window_size [4KB–16KB]
- layer_importance_weights (original:0.50 frozen, preserved/injected learnable)
- recall_threshold [0.5–0.9]

Based on feedback signals:
- missing_context_ratio: % of context that was needed but not retrieved
- irrelevance_score: % of retrieved context not used
- retrieval_latency_ms: time to lookup context
- token_waste_ratio: % of context tokens not contributing to outcome
"""

import numpy as np
from typing import Dict, Any
from core.learning.base import LearningLoop


class MemoryOptimizer(LearningLoop):
    """Learns to optimize context management."""

    def __init__(self, tenant_id: str = "_default", min_audit_requirement_bytes: int = 1000):
        """
        Args:
            tenant_id: for audit trail
            min_audit_requirement_bytes: minimum context bytes needed for compliance
        """
        super().__init__(loop_id="memory", tier=2)
        self.tenant_id = tenant_id

        # Learnable parameters
        self.context_window_size = 8000  # bytes
        self.layer_importance = {
            'original': 0.50,      # frozen (immutable base)
            'preserved': 0.30,     # learnable
            'injected': 0.20,      # learnable
        }
        self.recall_threshold = 0.70  # learnable

        # Compliance floor
        self.min_context_window = max(4000, min_audit_requirement_bytes)

        # Feedback smoothing (exponential averaging)
        self.smoothing_alpha = 0.95  # 0=old data only, 1=new data only
        self.smoothed_feedback = {}

        # Thresholds
        self.latency_threshold_ms = 100.0
        self.convergence_gradient_threshold = 0.001
        self.convergence_variance_threshold = 0.01

    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        """
        L_memory = 0.4 * L_recall
                 + 0.3 * L_relevance
                 + 0.2 * L_latency
                 + 0.1 * L_efficiency

        All components normalized to [0, 1].
        """
        # Smooth feedback signals (exponential averaging for delayed feedback)
        for key, value in feedback_signals.items():
            if key not in self.smoothed_feedback:
                self.smoothed_feedback[key] = value
            else:
                self.smoothed_feedback[key] = (self.smoothing_alpha * self.smoothed_feedback[key]
                                               + (1 - self.smoothing_alpha) * value)

        # Extract components (assume already [0, 1])
        missing_ratio = feedback_signals.get('missing_context_ratio', 0.0)  # 0=good, 1=all missing
        irrelevance = feedback_signals.get('irrelevance_score', 0.0)        # 0=good, 1=all irrelevant
        latency_ms = feedback_signals.get('retrieval_latency_ms', 0.0)
        token_waste = feedback_signals.get('token_waste_ratio', 0.0)        # 0=good, 1=all wasted

        # Normalize latency to [0, 1] (threshold at 100ms)
        L_latency = min(1.0, latency_ms / 100.0)

        # Compute loss
        components = {
            'recall': missing_ratio,
            'relevance': irrelevance,
            'latency': L_latency,
            'efficiency': token_waste,
        }
        weights = {
            'recall': 0.4,
            'relevance': 0.3,
            'latency': 0.2,
            'efficiency': 0.1,
        }

        loss = self.normalize_components(components, weights)

        # Add latency penalty (if exceeds threshold)
        if latency_ms > self.latency_threshold_ms:
            latency_penalty = (latency_ms - self.latency_threshold_ms) * 0.001
            loss += latency_penalty

        loss = min(1.0, max(0.0, loss))  # clip to [0, 1]

        # Record for convergence tracking
        self.record_loss(loss)

        return float(loss)

    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        """
        Compute gradients for:
        - context_window_size
        - layer_importance.preserved (injected = 1 - preserved, since original frozen)
        - recall_threshold
        """
        delta_loss = loss - prev_loss

        gradients = {
            'window_size': -delta_loss * 0.01,  # negative = good (loss decreased), reduce window if helped
            'layer_preserved': delta_loss * 0.02,  # positive gradient = increase preserved (stable)
            'layer_injected': -delta_loss * 0.02,  # inverse of preserved
            'recall_threshold': delta_loss * 0.01,
        }

        self.record_gradients(gradients)

        return gradients

    def apply_gradients(self, gradients: Dict[str, float], learning_rate: float = None, damping: float = None):
        """
        Apply gradient descent with damping.

        Mitigations:
        1. Hard bounds (window, layers, threshold)
        2. Layer weight normalization
        3. Compliance floor enforcement
        """
        if learning_rate is None:
            learning_rate = self.learning_rate
        if damping is None:
            damping = self.damping_factor

        # Update 1: Context window size
        old_window = self.context_window_size
        raw_update = old_window - learning_rate * gradients['window_size']
        new_window = damping * old_window + (1 - damping) * raw_update

        # Hard bounds + compliance floor
        new_window = self.clip_parameter(new_window, self.min_context_window, 16000)
        self.context_window_size = new_window

        # Update 2: Layer weights (with normalization)
        old_preserved = self.layer_importance['preserved']
        old_injected = self.layer_importance['injected']

        # Apply gradients
        raw_preserved = old_preserved - learning_rate * gradients['layer_preserved']
        raw_injected = old_injected - learning_rate * gradients['layer_injected']

        # Damping
        new_preserved = damping * old_preserved + (1 - damping) * raw_preserved
        new_injected = damping * old_injected + (1 - damping) * raw_injected

        # Normalize (preserve + injected must sum to 0.5, since original is frozen at 0.50)
        total_flex = new_preserved + new_injected
        if total_flex > 0:
            new_preserved = (new_preserved / total_flex) * 0.5
            new_injected = (new_injected / total_flex) * 0.5
        else:
            new_preserved = 0.25
            new_injected = 0.25

        # Clip
        new_preserved = self.clip_parameter(new_preserved, 0.1, 0.6)
        new_injected = self.clip_parameter(new_injected, 0.1, 0.6)

        # Renormalize
        total = new_preserved + new_injected
        if total > 0:
            new_preserved /= total
            new_injected /= total
            new_preserved *= 0.5
            new_injected *= 0.5

        self.layer_importance['preserved'] = new_preserved
        self.layer_importance['injected'] = new_injected

        # Update 3: Recall threshold
        old_threshold = self.recall_threshold
        raw_threshold = old_threshold - learning_rate * gradients['recall_threshold']
        new_threshold = damping * old_threshold + (1 - damping) * raw_threshold
        new_threshold = self.clip_parameter(new_threshold, 0.5, 0.9)
        self.recall_threshold = new_threshold

        # Record for convergence tracking
        self.record_parameters({
            'context_window_size': self.context_window_size,
            'layer_preserved': self.layer_importance['preserved'],
            'recall_threshold': self.recall_threshold,
        })

    def check_convergence(self, gradient_history: Dict[str, list] = None) -> bool:
        """
        Converged if:
        1. Average gradient magnitude < threshold
        2. Loss variance < threshold
        3. Parameter changes < 1% per step
        """
        if len(self.loss_history) < 100:
            return False

        # Check 1: gradient magnitude
        avg_gradient = self.get_avg_gradient_magnitude(last_n=100)
        if avg_gradient > self.convergence_gradient_threshold:
            return False

        # Check 2: loss variance
        loss_variance = self.get_loss_variance(last_n=100)
        if loss_variance > self.convergence_variance_threshold:
            return False

        # Check 3: parameter stability
        param_stability = self.get_parameter_stability(last_n=100)
        if param_stability > 0.01:  # >1% change per step = not stable
            return False

        return True

    def emit_event(self, collector_integration, **event_data):
        """Emit to Live-Collector."""
        if collector_integration is None:
            return

        collector_integration.on_memory_decision(
            context_window_size=int(self.context_window_size),
            layer_importance=self.layer_importance.copy(),
            recall_threshold=float(self.recall_threshold),
            feedback=event_data.get('feedback', {})
        )
