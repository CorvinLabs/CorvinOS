"""
Unified Learning Loss Vector — Phase 1 Implementation with Phase 2 Tier Damping.

Coordinates 6 independent learning loops via a single objective:
  L_total = Σ wᵢ · Lᵢ

Tier damping (Phase 2) prevents cascading divergence by scaling gradients per tier:
  - Tier 1 (Meta-Skills): α=0.01 (slow response, fail-closed mechanisms)
  - Tier 2 (Infrastructure): α=0.05 (medium response, memory/audit)
  - Tier 3 (Learning): α=0.1 (fast response, confidence/routing)

Audit-first design: every loss computation is logged to core audit chain.
Fail-closed: if audit fails, RuntimeError is raised; loss is not returned.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any, Protocol, Tuple
import math
import numpy as np


@dataclass(frozen=True)
class UnifiedLossSnapshot:
    """Immutable loss state for one batch of tasks."""
    timestamp: datetime
    batch_id: str
    tenant_id: str

    # Per-loop losses
    L_routing: float         # Routing accuracy error
    L_confidence: float      # Confidence calibration (Brier score)
    L_feedback: float        # Feedback arrival rate + staleness
    L_attention: float       # Attention budget overrun + underutilization
    L_latency: float         # P99 latency vs SLA + variance
    L_diversity: float       # Task-type coverage + engine entropy

    # Aggregated
    L_total: float           # Weighted sum
    weights: Dict[str, float] = field(default_factory=dict)

    # Audit chain
    hash: Optional[str] = None
    prev_hash: Optional[str] = None


@dataclass(frozen=True)
class UnifiedLossComputedEvent:
    """Audit event: one loss computation completed."""
    timestamp: datetime
    tenant_id: str
    batch_id: str

    L_routing: float
    L_confidence: float
    L_feedback: float
    L_attention: float
    L_latency: float
    L_diversity: float
    L_total: float

    weights: Dict[str, float]
    hash: str
    prev_hash: str


class AuditBackend(Protocol):
    """What the optimizer needs from an audit chain.

    The in-memory ``MockAuditBackend`` that used to live HERE (and was therefore
    importable — and instantiable — from production code, F-L10) now lives in
    ``tests/learning/mock_audit_backend.py``. Production wiring must hand in a
    backend whose ``write_event`` commits to the core hash chain.
    """

    def write_event(self, event: Dict[str, Any]) -> Optional[str]: ...

    def last_hash(self) -> str: ...


LOSS_COMPONENTS = ('routing', 'confidence', 'feedback', 'attention', 'latency', 'diversity')

# Tier mapping for gradient damping (Phase 2 mitigation for cascading divergence)
# Maps loss component → tier (1, 2, or 3) for damped gradient application
COMPONENT_TIER_MAP = {
    'routing': 3,        # Tier 3 (Learning): fast response ~α=0.1
    'confidence': 3,     # Tier 3 (Learning): fast response ~α=0.1
    'feedback': 2,       # Tier 2 (Infrastructure): medium response ~α=0.05
    'attention': 2,      # Tier 2 (Infrastructure): medium response ~α=0.05
    'latency': 2,        # Tier 2 (Infrastructure): medium response ~α=0.05
    'diversity': 1,      # Tier 1 (Meta-Skills): slow response ~α=0.01
}

# Damping factors per tier (prevents oscillation by slowing gradient application)
TIER_DAMPING_FACTORS = {
    1: 0.01,   # Meta-Skills tier: very conservative (fail-closed mechanisms)
    2: 0.05,   # Infrastructure tier: moderate (memory, audit, skills)
    3: 0.1,    # Learning tier: responsive (confidence, routing)
}


def validate_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Return a validated copy of ``weights`` or raise ``ValueError``.

    Exactly the six loss components, every weight a finite non-negative
    number, and the weights sum to 1 (±1e-6). Before this check a caller could
    hand in ``NaN``/negative/missing weights and the "unified" loss silently
    became garbage (F-L10).
    """
    if not isinstance(weights, dict):
        raise ValueError("weights must be a dict")
    if set(weights) != set(LOSS_COMPONENTS):
        raise ValueError(
            f"weights must cover exactly {sorted(LOSS_COMPONENTS)}, got {sorted(weights)}"
        )
    clean: Dict[str, float] = {}
    for name, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"weight {name!r} is not a number: {value!r}")
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"weight {name!r} must be finite and >= 0, got {value!r}")
        clean[name] = value
    total = sum(clean.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"weights must sum to 1.0, got {total!r}")
    return clean


class UnifiedLossOptimizer:
    """
    Main API: compute unified loss for a batch of tasks with per-tier gradient damping.

    Fail-closed: audit write FIRST, then compute loss.
    If audit fails, RuntimeError is raised; loss is NOT returned.

    Phase 2 Mitigation (ADR-0617): Per-Tier Gradient Damping prevents cascading divergence
    by scaling gradients independently per tier. Each tier has a damping factor (α_tier)
    that controls the learning rate for that tier's loss components.
    """

    def __init__(
        self,
        tenant_id: str,
        audit_backend: AuditBackend,
        tier_damping: Optional[Dict[int, float]] = None,
    ):
        if audit_backend is None or not callable(getattr(audit_backend, "write_event", None)):
            raise ValueError("audit_backend with write_event() is required (audit-first, fail-closed)")
        self.tenant_id = tenant_id
        self.audit = audit_backend
        self.weights = {
            'routing': 1/6,
            'confidence': 1/6,
            'feedback': 1/6,
            'attention': 1/6,
            'latency': 1/6,
            'diversity': 1/6,
        }
        # Phase 2: Tier damping configuration (per-tier learning rates)
        if tier_damping is None:
            self.tier_damping = dict(TIER_DAMPING_FACTORS)
        else:
            self.tier_damping = self._validate_tier_damping(tier_damping)

    def _validate_tier_damping(self, tier_damping: Dict[int, float]) -> Dict[int, float]:
        """Validate tier damping factors (all tiers must be 0 < α <= 1.0)."""
        clean = {}
        for tier, alpha in tier_damping.items():
            if not isinstance(tier, int) or tier not in (1, 2, 3):
                raise ValueError(f"tier must be 1, 2, or 3, got {tier}")
            if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
                raise ValueError(f"damping factor for tier {tier} is not a number: {alpha}")
            alpha = float(alpha)
            if not (0.0 < alpha <= 1.0):
                raise ValueError(f"damping factor for tier {tier} must be in (0, 1.0], got {alpha}")
            clean[tier] = alpha
        return clean

    def compute_batch_loss(
        self,
        task_batch: List[Dict[str, Any]],
        outcomes: List[Dict[str, Any]],
        feedback_signals: List[Optional[Dict[str, Any]]],
    ) -> UnifiedLossSnapshot:
        """
        Compute L_total for one batch.

        Fail-closed: audit write FIRST, then compute loss.
        If audit fails, RuntimeError is raised.
        """
        timestamp = datetime.now()
        batch_id = f"batch_{timestamp.timestamp()}"

        # Compute each loss component
        L_routing = self._compute_L_routing(task_batch, outcomes)
        L_confidence = self._compute_L_confidence(task_batch, outcomes)
        L_feedback = self._compute_L_feedback(feedback_signals)
        L_attention = self._compute_L_attention(task_batch)
        L_latency = self._compute_L_latency(task_batch)
        L_diversity = self._compute_L_diversity(task_batch)

        # Weighted sum
        L_total = (
            self.weights['routing'] * L_routing +
            self.weights['confidence'] * L_confidence +
            self.weights['feedback'] * L_feedback +
            self.weights['attention'] * L_attention +
            self.weights['latency'] * L_latency +
            self.weights['diversity'] * L_diversity
        )

        # Audit FIRST (fail-closed)
        event_dict = {
            'event_type': 'unified_loss_computed',
            'timestamp': timestamp.isoformat(),
            'tenant_id': self.tenant_id,
            'batch_id': batch_id,
            'L_routing': float(L_routing),
            'L_confidence': float(L_confidence),
            'L_feedback': float(L_feedback),
            'L_attention': float(L_attention),
            'L_latency': float(L_latency),
            'L_diversity': float(L_diversity),
            'L_total': float(L_total),
            'weights': {k: float(v) for k, v in self.weights.items()},
        }

        event_hash = self.audit.write_event(event_dict)

        if event_hash is None:
            raise RuntimeError("Audit write failed; loss computation aborted")

        # Create snapshot
        snapshot = UnifiedLossSnapshot(
            timestamp=timestamp,
            batch_id=batch_id,
            tenant_id=self.tenant_id,
            L_routing=L_routing,
            L_confidence=L_confidence,
            L_feedback=L_feedback,
            L_attention=L_attention,
            L_latency=L_latency,
            L_diversity=L_diversity,
            L_total=L_total,
            weights=dict(self.weights),
            hash=event_hash,
            prev_hash=self.audit.last_hash(),
        )

        return snapshot

    def _compute_L_routing(self, task_batch: List[Dict], outcomes: List[Dict]) -> float:
        """
        L_routing = 1 - P(engine_correct | task)

        If task was routed to correct engine and outcome is correct, no penalty.
        If routed to wrong engine or outcome is wrong, penalty.
        """
        if not task_batch or not outcomes:
            return 0.0

        correct_count = 0
        for task, outcome in zip(task_batch, outcomes):
            if outcome.get('engine_correct', False) and outcome.get('correct', False):
                correct_count += 1

        return 1.0 - (correct_count / len(task_batch))

    def _compute_L_confidence(self, task_batch: List[Dict], outcomes: List[Dict]) -> float:
        """
        L_confidence = Brier score = E[(score - actual)²]

        Measures calibration: predicted confidence vs actual correctness.
        """
        if not task_batch or not outcomes:
            return 0.0

        errors = []
        for task, outcome in zip(task_batch, outcomes):
            predicted_confidence = task.get('confidence_score', 0.5)
            actual_correctness = 1.0 if outcome.get('correct', False) else 0.0
            error = (predicted_confidence - actual_correctness) ** 2
            errors.append(error)

        return np.mean(errors) if errors else 0.0

    def _compute_L_feedback(self, feedback_signals: List[Optional[Dict]]) -> float:
        """
        L_feedback = (1 - arrival_rate) + 0.1 * stale_signal_age

        Penalizes: missing feedback and stale signals.
        """
        if not feedback_signals:
            return 1.0

        # Arrival rate
        arrived = sum(1 for f in feedback_signals if f is not None)
        arrival_rate = arrived / len(feedback_signals)

        # Staleness (age in seconds; convert to hours)
        staleness_scores = []
        now = datetime.now()
        for f in feedback_signals:
            if f is not None and 'timestamp' in f:
                age_seconds = (now - datetime.fromisoformat(f['timestamp'])).total_seconds()
                age_hours = age_seconds / 3600.0
                staleness_scores.append(min(age_hours, 24.0))  # Cap at 24h

        mean_staleness = np.mean(staleness_scores) if staleness_scores else 0.0

        return (1.0 - arrival_rate) + 0.1 * (mean_staleness / 24.0)

    def _compute_L_attention(self, task_batch: List[Dict]) -> float:
        """
        L_attention = cost_overrun_ratio + (1 - utilization)

        Penalizes: budget overruns and underutilization.
        """
        if not task_batch:
            return 0.0

        budget_target = 1000.0  # tokens per task
        costs = [float(task.get('tokens_used', 0) or 0) for task in task_batch]
        # A task with no or a zero/negative budget contributes no utilization
        # signal instead of dividing by zero (F-L10).
        budgets = [float(task.get('budget_allocated', budget_target) or 0) for task in task_batch]

        cost_ratio = np.mean(costs) / budget_target if costs else 0.0
        overrun = max(0.0, cost_ratio - 1.0)

        ratios = [
            min(costs[i], budgets[i]) / budgets[i]
            for i in range(len(costs))
            if budgets[i] > 0
        ]
        utilization = float(np.mean(ratios)) if ratios else 0.0

        return overrun + (1.0 - utilization)

    def _compute_L_latency(self, task_batch: List[Dict]) -> float:
        """
        L_latency = p99_latency / sla_target + 0.3 * variance_normalized

        Penalizes: SLA breaches and high variance.
        """
        if not task_batch:
            return 0.0

        latencies = [task.get('latency_seconds', 0.0) for task in task_batch]
        if not latencies:
            return 0.0

        sla_target = 5.0  # seconds
        p99_latency = np.percentile(latencies, 99)

        variance = np.var(latencies)
        variance_normalized = variance / (sla_target ** 2) if sla_target > 0 else 0.0

        return (p99_latency / sla_target) + 0.3 * variance_normalized

    def _compute_L_diversity(self, task_batch: List[Dict]) -> float:
        """
        L_diversity = (1 - task_type_coverage) + (1 - engine_entropy)

        Penalizes: missing task types and unbalanced engine distribution.
        """
        if not task_batch:
            return 0.0

        # Task type coverage
        task_types = [task.get('task_type', 'unknown') for task in task_batch]
        unique_types = len(set(task_types))
        total_types = 15  # Hardcoded; adjust as needed
        coverage = min(1.0, unique_types / total_types)

        # Engine entropy
        engines = [task.get('routed_engine', 'unknown') for task in task_batch]
        engine_counts = {}
        for engine in engines:
            engine_counts[engine] = engine_counts.get(engine, 0) + 1

        engine_probs = np.array(list(engine_counts.values())) / len(engines)
        engine_entropy = -np.sum(engine_probs * np.log(engine_probs + 1e-9))
        max_entropy = np.log(4)  # 4 possible engines
        entropy_normalized = engine_entropy / max_entropy if max_entropy > 0 else 0.0

        return (1.0 - coverage) + (1.0 - entropy_normalized)

    def compute_gradients(
        self,
        loss_deltas: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Compute gradients for loss components (negative gradient direction for descent).

        Args:
            loss_deltas: Dict mapping component name → change in loss (dL/dw)

        Returns:
            Dict mapping component name → gradient value
        """
        gradients = {}
        for component, delta in loss_deltas.items():
            if component not in LOSS_COMPONENTS:
                raise ValueError(f"unknown loss component: {component}")
            # Gradient descent: move opposite to loss increase
            gradients[component] = -delta
        return gradients

    def apply_damped_gradients(
        self,
        gradients: Dict[str, float],
        learning_rate: float = 1.0,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """
        Apply gradients to weights with per-tier damping (Phase 2 mitigation).

        Damping prevents cascading divergence by scaling each tier's gradient
        independently. This decouples fast-changing tiers (Learning/Tier 3) from
        slow-changing tiers (Meta-Skills/Tier 1).

        Args:
            gradients: Dict mapping component name → gradient value
            learning_rate: Global learning rate (scaled by tier damping per component)

        Returns:
            Tuple[updated_weights, audit_event_dict]
            Raises RuntimeError if audit write fails (fail-closed).
        """
        if not isinstance(learning_rate, (int, float)) or not (0 < learning_rate < 10):
            raise ValueError(f"learning_rate must be in (0, 10), got {learning_rate}")

        old_weights = dict(self.weights)
        new_weights = dict(self.weights)

        # Apply damped gradients component-by-component
        damped_gradients = {}
        for component, gradient in gradients.items():
            if component not in LOSS_COMPONENTS:
                raise ValueError(f"unknown loss component: {component}")

            tier = COMPONENT_TIER_MAP[component]
            alpha_tier = self.tier_damping[tier]
            damped_gradient = alpha_tier * gradient

            # Update weight: w_new = w_old + learning_rate * damped_gradient
            weight_delta = learning_rate * damped_gradient
            new_weights[component] = max(0.0, min(1.0, new_weights[component] + weight_delta))

            damped_gradients[component] = {
                'tier': tier,
                'original_gradient': float(gradient),
                'damping_factor': float(alpha_tier),
                'damped_gradient': float(damped_gradient),
                'weight_delta': float(weight_delta),
            }

        # Validate new weights (must sum to 1)
        total = sum(new_weights.values())
        if abs(total - 1.0) > 0.1:  # Allow some tolerance during updates
            # Renormalize to maintain weight sum = 1.0
            for component in new_weights:
                new_weights[component] = new_weights[component] / total

        # Audit FIRST (fail-closed)
        audit_event = {
            'event_type': 'gradient_damped_by_tier',
            'tenant_id': self.tenant_id,
            'timestamp': datetime.now().isoformat(),
            'old_weights': {k: float(v) for k, v in old_weights.items()},
            'new_weights': {k: float(v) for k, v in new_weights.items()},
            'damped_gradients': damped_gradients,
            'learning_rate': float(learning_rate),
            'tier_damping_factors': {k: float(v) for k, v in self.tier_damping.items()},
        }

        event_hash = self.audit.write_event(audit_event)
        if event_hash is None:
            raise RuntimeError("Audit write failed; weights NOT updated (fail-closed)")

        # Apply weights
        self.weights = new_weights
        return new_weights, audit_event

    def update_weights(self, new_weights: Dict[str, float]) -> Dict[str, float]:
        """Update loss weights: validate, audit FIRST, then apply (fail-closed)."""
        clean = validate_weights(new_weights)
        old_weights = dict(self.weights)
        delta = {k: clean[k] - old_weights[k] for k in clean}

        event_hash = self.audit.write_event({
            'event_type': 'weights_updated',
            'tenant_id': self.tenant_id,
            'timestamp': datetime.now().isoformat(),
            'old_weights': old_weights,
            'new_weights': dict(clean),
            'delta': delta,
            'reason': 'manual_update',
        })
        if event_hash is None:
            raise RuntimeError("Audit write failed; weights NOT updated")

        self.weights = clean
        return dict(clean)
