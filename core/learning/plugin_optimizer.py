"""Plugin Orchestration Loop (ADR-0622) - Learning plugin selection & prioritization"""

from typing import Dict, List, Any
from core.learning.base import LearningLoop


class PluginOrchestrator(LearningLoop):
    """Learns optimal plugin selection per task type"""

    def __init__(self):
        super().__init__(loop_id="plugins", tier=2)
        self.plugin_priority_weights = {}  # global weights
        self.task_type_weights = {}  # per-task-type weights
        self.task_budget_ms = 100  # resource budget

    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        """L_plugins = 0.4*quality + 0.3*latency + 0.2*reliability + 0.1*compatibility"""
        quality = feedback_signals.get('quality_gain', 0.0)  # higher = better
        latency = min(1.0, feedback_signals.get('execution_time_ms', 100) / 200.0)
        reliability = feedback_signals.get('error_rate', 0.0)
        compatibility = feedback_signals.get('conflict_score', 0.0)

        # Loss: lower quality, higher latency/errors/conflicts = worse
        loss = (0.4 * (1 - quality)  # invert: higher quality = lower loss
              + 0.3 * latency
              + 0.2 * reliability
              + 0.1 * compatibility)

        loss = min(1.0, max(0.0, loss))
        self.record_loss(loss)
        return float(loss)

    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        """Gradient based on quality contribution"""
        delta = loss - prev_loss
        return {'plugin_priority': delta * 0.01}

    def apply_gradients(self, gradients: Dict[str, float], learning_rate: float = None, damping: float = None):
        """Update plugin weights"""
        if learning_rate is None:
            learning_rate = self.learning_rate
        if damping is None:
            damping = self.damping_factor

        grad = gradients.get('plugin_priority', 0.0)

        # Update all plugins (simplified)
        for plugin_id in ['plugin_a', 'plugin_b', 'plugin_c']:
            old = self.plugin_priority_weights.get(plugin_id, 1.0)
            new = old - learning_rate * grad
            new = damping * old + (1 - damping) * new
            self.plugin_priority_weights[plugin_id] = max(0.01, min(2.0, new))

        # Normalize
        total = sum(self.plugin_priority_weights.values())
        for pid in self.plugin_priority_weights:
            self.plugin_priority_weights[pid] /= total

        self.record_parameters(self.plugin_priority_weights)

    def check_convergence(self, gradient_history=None) -> bool:
        """Converged if weights stable"""
        if len(self.loss_history) < 100:
            return False

        avg_grad = self.get_avg_gradient_magnitude(100)
        return avg_grad < 0.001

    def emit_event(self, collector_integration, **event_data):
        """Emit to Live-Collector"""
        if collector_integration:
            collector_integration.on_plugin_decision(
                task_type=event_data.get('task_type', 'generic'),
                plugins_loaded=event_data.get('plugins_loaded', list(self.plugin_priority_weights.keys())),
                plugin_priorities=self.plugin_priority_weights.copy(),
                feedback=event_data.get('feedback', {})
            )
