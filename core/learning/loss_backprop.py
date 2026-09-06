"""
Loss Backpropagation — Phase 1 Implementation.

Computes gradients through the DAG of loop interdependencies.

DAG structure:
  L_routing ← [L_confidence, L_latency]
  L_confidence ← [L_feedback, L_latency]
  L_feedback ← [L_attention]
  L_attention ← []
  L_latency ← [L_confidence]
  L_diversity ← []
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any, Optional
import numpy as np


@dataclass(frozen=True)
class LossGradientComputedEvent:
    """Audit event: gradients computed for backprop."""
    timestamp: datetime
    tenant_id: str
    batch_id: str

    grad_routing: float
    grad_confidence: float
    grad_feedback: float
    grad_attention: float
    grad_latency: float
    grad_diversity: float

    dag_edges: Dict[str, List[str]] = field(default_factory=dict)
    hash: Optional[str] = None
    prev_hash: Optional[str] = None


class LossBackpropagator:
    """
    Computes gradients following the DAG of loop interdependencies.

    Gradient flow rules:
      - If outcome was correct AND confidence was low → penalize confidence
      - If outcome was correct AND routing was wrong → penalize routing
      - If feedback was sparse AND attention budget was tight → penalize attention
      - etc.
    """

    def __init__(self, audit_backend):
        self.audit = audit_backend

    def compute_gradients(
        self,
        snapshot,  # UnifiedLossSnapshot
        task_batch: List[Dict[str, Any]],
        outcomes: List[Dict[str, Any]],
        feedback_signals: List[Optional[Dict[str, Any]]],
    ) -> Dict[str, float]:
        """
        Compute ∂L_total / ∂wᵢ via chain rule through DAG.

        Returns: {'routing': 0.01, 'confidence': -0.03, ...}
        """
        gradients = {}

        # --- Level 1: Direct Losses (no incoming edges) ---

        # L_attention: no incoming edges
        gradients['attention'] = self._gradient_attention(task_batch)

        # L_diversity: no incoming edges (but no backprop)
        gradients['diversity'] = 0.0  # Monitored, not optimized

        # --- Level 2: Feedback depends on Attention ---

        # If attention budget was tight, feedback arrival should be higher (penalty)
        budget_tightness = self._measure_attention_tightness(task_batch)
        feedback_arrival_rate = (
            sum(1 for f in feedback_signals if f is not None) / len(feedback_signals)
            if feedback_signals else 0.0
        )

        grad_feedback_direct = -(feedback_arrival_rate)  # More arrival → lower loss
        grad_feedback_from_attention = budget_tightness * 0.1

        gradients['feedback'] = grad_feedback_direct + grad_feedback_from_attention

        # --- Level 3: Confidence training depends on Feedback + Latency ---

        # Calibration error
        calibration_error = self._compute_calibration_error(task_batch, outcomes)
        grad_confidence_direct = -2.0 * calibration_error

        # Feedback quality affects training
        labels_available = sum(1 for f in feedback_signals if f is not None) / len(feedback_signals) if feedback_signals else 0.0
        grad_confidence_from_feedback = (1.0 - labels_available) * 0.2

        # Latency SLA affects training
        sla_breach = self._compute_sla_breach_severity(task_batch)
        grad_confidence_from_latency = sla_breach * 0.1

        gradients['confidence'] = (
            grad_confidence_direct +
            grad_confidence_from_feedback +
            grad_confidence_from_latency
        )

        # --- Level 4: Routing depends on Confidence + Skill Config ---

        # If routing was wrong AND confidence was high, confidence params are to blame
        outcome_mismatch = np.array([
            (1.0 if not o.get('engine_correct', False) else 0.0) * task.get('confidence_score', 0.5)
            for task, o in zip(task_batch, outcomes)
        ])
        grad_routing_direct = np.mean(outcome_mismatch)

        # Confidence quality affects routing
        confidence_calibration_quality = max(0.0, 1.0 - np.abs(calibration_error))
        grad_routing_from_confidence = (1.0 - confidence_calibration_quality) * 0.15

        # Skill config affects routing
        skill_failure_rate = sum(
            1.0 for o in outcomes if not o.get('correct', False)
        ) / len(outcomes) if outcomes else 0.0
        grad_routing_from_skill = skill_failure_rate * 0.1

        gradients['routing'] = (
            grad_routing_direct +
            grad_routing_from_confidence +
            grad_routing_from_skill
        )

        # --- Level 5: Latency depends on Confidence training overhead ---

        p99_latency = self._compute_p99_latency(task_batch)
        sla_target = 5.0
        grad_latency_direct = (p99_latency / sla_target) - 1.0

        # Confidence training overhead
        confidence_training_time = self._estimate_confidence_training_overhead()
        grad_latency_from_confidence = confidence_training_time * 0.05

        gradients['latency'] = grad_latency_direct + grad_latency_from_confidence

        # --- Clip gradients ---
        for key in gradients:
            gradients[key] = np.clip(gradients[key], -1.0, 1.0)

        # --- Audit: Record DAG edges ---
        dag_edges = {
            'routing': ['confidence', 'latency'],
            'confidence': ['feedback', 'latency'],
            'feedback': ['attention'],
            'attention': [],
            'latency': ['confidence'],
            'diversity': [],
        }

        self.audit.write_event({
            'event_type': 'loss_gradient_computed',
            'tenant_id': snapshot.tenant_id,
            'batch_id': snapshot.batch_id,
            'timestamp': datetime.now().isoformat(),
            'gradients': {k: float(v) for k, v in gradients.items()},
            'dag_edges': dag_edges,
        })

        # Divergence detection
        total_grad_magnitude = sum(abs(v) for v in gradients.values())
        if total_grad_magnitude > 1.0:
            severity = 'warning' if total_grad_magnitude < 2.0 else 'error'
            self.audit.write_event({
                'event_type': 'backprop_divergence_detected',
                'severity': severity,
                'total_grad_magnitude': float(total_grad_magnitude),
                'tenant_id': snapshot.tenant_id,
                'timestamp': datetime.now().isoformat(),
            })

        return gradients

    def _gradient_attention(self, task_batch: List[Dict]) -> float:
        """Gradient for Attention loss."""
        if not task_batch:
            return 0.0

        budget_target = 1000.0
        costs = [task.get('tokens_used', 0) for task in task_batch]
        budgets = [task.get('budget_allocated', budget_target) for task in task_batch]

        cost_ratio = np.mean(costs) / budget_target if costs else 0.0
        overrun = max(0.0, cost_ratio - 1.0)

        return overrun * 0.5  # Penalize overruns

    def _measure_attention_tightness(self, task_batch: List[Dict]) -> float:
        """How tight is the attention budget? [0, 1]."""
        if not task_batch:
            return 0.0

        budget_target = 1000.0
        costs = [task.get('tokens_used', 0) for task in task_batch]
        budgets = [task.get('budget_allocated', budget_target) for task in task_batch]

        utilization = np.mean([
            min(costs[i], budgets[i]) / budgets[i]
            for i in range(len(costs))
        ]) if budgets else 0.0

        return 1.0 - utilization  # High utilization → tight budget

    def _compute_calibration_error(self, task_batch: List[Dict], outcomes: List[Dict]) -> float:
        """Brier score: E[(score - actual)²]."""
        if not task_batch or not outcomes:
            return 0.0

        errors = []
        for task, outcome in zip(task_batch, outcomes):
            predicted = task.get('confidence_score', 0.5)
            actual = 1.0 if outcome.get('correct', False) else 0.0
            errors.append((predicted - actual) ** 2)

        return np.mean(errors) if errors else 0.0

    def _compute_sla_breach_severity(self, task_batch: List[Dict]) -> float:
        """How much does latency breach the SLA? [0, 1]."""
        if not task_batch:
            return 0.0

        latencies = [task.get('latency_seconds', 0.0) for task in task_batch]
        sla_target = 5.0

        p99_latency = np.percentile(latencies, 99) if latencies else 0.0
        breach = max(0.0, p99_latency - sla_target)

        return min(1.0, breach / sla_target)

    def _compute_p99_latency(self, task_batch: List[Dict]) -> float:
        """Compute p99 latency."""
        if not task_batch:
            return 0.0

        latencies = [task.get('latency_seconds', 0.0) for task in task_batch]
        return np.percentile(latencies, 99) if latencies else 0.0

    def _estimate_confidence_training_overhead(self) -> float:
        """Estimate training time overhead (simplified; 0-1 scale)."""
        # In real system, this would query actual training stats
        return 0.1  # Placeholder

    def detect_oscillation(self, gradient_history: List[float], window: int = 10) -> bool:
        """Detect if gradients alternate sign (oscillation)."""
        if len(gradient_history) < window:
            return False

        recent = gradient_history[-window:]
        sign_changes = sum(
            1 for i in range(1, len(recent))
            if recent[i] * recent[i - 1] < 0
        )

        return sign_changes > window * 0.6

    def recover_from_divergence(self, optimizer, last_stable_weights: Dict[str, float]):
        """Recover from oscillation."""
        optimizer.weights = last_stable_weights

        self.audit.write_event({
            'event_type': 'backprop_divergence_recovery',
            'action': 'learning_disabled',
            'reason': 'gradient_oscillation_detected',
            'timestamp': datetime.now().isoformat(),
        })
