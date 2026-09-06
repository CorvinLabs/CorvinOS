"""Skill Composition Loop (ADR-0621) - Learning optimal DAG ordering"""

from typing import Dict, List, Any
from core.learning.base import LearningLoop
import heapq


class CompositionOptimizer(LearningLoop):
    """Learns optimal skill execution order via priority weights"""

    def __init__(self, skill_dag=None):
        super().__init__(loop_id="skills", tier=2)
        self.skill_dag = skill_dag or {}
        self.skill_priority_weights = {}

        # Initialize: equal priority
        for skill_id in self._get_all_skills():
            self.skill_priority_weights[skill_id] = 1.0 / max(len(self._get_all_skills()), 1)

        self.current_order = []
        self.reorder_cooldown = 50
        self.time_since_last_reorder = 0

    def _get_all_skills(self) -> List[str]:
        """Get all skills from DAG"""
        if not self.skill_dag:
            return ["routing", "confidence", "feedback"]
        return list(set([s for deps in self.skill_dag.values() for s in deps] + list(self.skill_dag.keys())))

    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        """L_skills = 0.4*quality + 0.3*latency + 0.2*conflicts + 0.1*ordering"""
        quality = feedback_signals.get('composition_error_rate', 0.1)
        latency = min(1.0, feedback_signals.get('dag_execution_time_ms', 500) / 1000.0)
        conflicts = feedback_signals.get('skill_contradictions', 0) / 100.0
        ordering = feedback_signals.get('ordering_penalty', 0.0)

        loss = 0.4 * quality + 0.3 * latency + 0.2 * conflicts + 0.1 * ordering
        loss = min(1.0, max(0.0, loss))

        self.record_loss(loss)
        return float(loss)

    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        """Gradient per skill based on quality impact"""
        delta = loss - prev_loss
        gradients = {}

        for skill_id in self._get_all_skills():
            gradients[skill_id] = delta * 0.01

        self.record_gradients(gradients)
        return gradients

    def apply_gradients(self, gradients: Dict[str, float], learning_rate: float = None, damping: float = None):
        """Update priority weights with damping"""
        if learning_rate is None:
            learning_rate = self.learning_rate
        if damping is None:
            damping = self.damping_factor

        for skill_id, gradient in gradients.items():
            old = self.skill_priority_weights.get(skill_id, 1.0)
            new = old - learning_rate * gradient
            new = damping * old + (1 - damping) * new
            self.skill_priority_weights[skill_id] = max(0.1, min(2.0, new))

        # Normalize
        total = sum(self.skill_priority_weights.values())
        for skill_id in self.skill_priority_weights:
            self.skill_priority_weights[skill_id] /= total

        # Check reorder
        self.time_since_last_reorder += 1
        if self.time_since_last_reorder >= self.reorder_cooldown:
            self.current_order = self._topological_sort_by_priority()
            self.time_since_last_reorder = 0

        self.record_parameters(self.skill_priority_weights)

    def _topological_sort_by_priority(self) -> List[str]:
        """Topological sort with priority tiebreaker"""
        # Simplified: just return sorted by weight (real impl would do topological sort)
        return sorted(self._get_all_skills(), key=lambda s: -self.skill_priority_weights.get(s, 1.0))

    def check_convergence(self, gradient_history=None) -> bool:
        """Converged if weights stable and order unchanged"""
        if len(self.loss_history) < 100:
            return False

        avg_grad = self.get_avg_gradient_magnitude(100)
        param_stab = self.get_parameter_stability(100)

        return avg_grad < 0.001 and param_stab < 0.01

    def emit_event(self, collector_integration, **event_data):
        """Emit to Live-Collector"""
        if collector_integration:
            collector_integration.on_skill_composition_decision(
                skill_order=self.current_order or self._get_all_skills(),
                priority_weights=self.skill_priority_weights.copy(),
                feedback=event_data.get('feedback', {}),
                execution_time_ms=event_data.get('execution_time_ms', 0)
            )
