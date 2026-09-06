"""
Unified Learning Loss Vector — Simplified Version (no numpy).
For Phase 1 proof-of-concept + testing.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any
import hashlib
import json
import math


@dataclass(frozen=True)
class UnifiedLossSnapshot:
    """Immutable loss state for one batch of tasks."""
    timestamp: datetime
    batch_id: str
    tenant_id: str

    L_routing: float
    L_confidence: float
    L_feedback: float
    L_attention: float
    L_latency: float
    L_diversity: float

    L_total: float
    weights: Dict[str, float] = field(default_factory=dict)

    hash: Optional[str] = None
    prev_hash: Optional[str] = None


class MockAuditBackend:
    """Mock audit backend."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self.last_hash_value = "genesis"

    def write_event(self, event: Dict[str, Any]) -> Optional[str]:
        """Write event to audit chain; return hash."""
        if event is None:
            return None

        event_str = json.dumps(event, sort_keys=True, default=str)
        event_hash = hashlib.sha256(
            f"{self.last_hash_value}{event_str}".encode()
        ).hexdigest()

        event['hash'] = event_hash
        event['prev_hash'] = self.last_hash_value
        self.events.append(event)
        self.last_hash_value = event_hash

        return event_hash

    def last_hash(self) -> str:
        return self.last_hash_value

    def verify_chain(self) -> bool:
        """Verify hash chain is intact."""
        current_hash = "genesis"
        for event in self.events:
            if event['prev_hash'] != current_hash:
                return False
            current_hash = event['hash']
        return True

    def read_events(self, tenant_id: str) -> List[Dict]:
        """Read all events for tenant."""
        return [e for e in self.events if e.get('tenant_id') == tenant_id]


class UnifiedLossOptimizer:
    """Main API: compute unified loss for a batch of tasks."""

    def __init__(self, tenant_id: str, audit_backend: MockAuditBackend):
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

    def compute_batch_loss(
        self,
        task_batch: List[Dict[str, Any]],
        outcomes: List[Dict[str, Any]],
        feedback_signals: List[Optional[Dict[str, Any]]],
    ) -> UnifiedLossSnapshot:
        """
        Compute L_total for one batch.
        Fail-closed: audit write FIRST, then compute loss.
        """
        timestamp = datetime.now()
        batch_id = f"batch_{int(timestamp.timestamp() * 1000)}"

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
        if not task_batch or not outcomes:
            return 0.0

        correct_count = 0
        for i, outcome in enumerate(outcomes):
            if i < len(task_batch):
                if outcome.get('engine_correct', False) and outcome.get('correct', False):
                    correct_count += 1

        return 1.0 - (correct_count / len(task_batch))

    def _compute_L_confidence(self, task_batch: List[Dict], outcomes: List[Dict]) -> float:
        if not task_batch or not outcomes:
            return 0.0

        errors = []
        for i, outcome in enumerate(outcomes):
            if i < len(task_batch):
                predicted_confidence = task_batch[i].get('confidence_score', 0.5)
                actual_correctness = 1.0 if outcome.get('correct', False) else 0.0
                error = (predicted_confidence - actual_correctness) ** 2
                errors.append(error)

        return sum(errors) / len(errors) if errors else 0.0

    def _compute_L_feedback(self, feedback_signals: List[Optional[Dict]]) -> float:
        if not feedback_signals:
            return 1.0

        arrived = sum(1 for f in feedback_signals if f is not None)
        arrival_rate = arrived / len(feedback_signals)

        staleness_scores = []
        now = datetime.now()
        for f in feedback_signals:
            if f is not None and 'timestamp' in f:
                try:
                    ts = datetime.fromisoformat(f['timestamp'])
                    age_seconds = (now - ts).total_seconds()
                    age_hours = age_seconds / 3600.0
                    staleness_scores.append(min(age_hours, 24.0))
                except:
                    pass

        mean_staleness = sum(staleness_scores) / len(staleness_scores) if staleness_scores else 0.0

        return (1.0 - arrival_rate) + 0.1 * (mean_staleness / 24.0)

    def _compute_L_attention(self, task_batch: List[Dict]) -> float:
        if not task_batch:
            return 0.0

        budget_target = 1000.0
        costs = [task.get('tokens_used', 0) for task in task_batch]
        budgets = [task.get('budget_allocated', budget_target) for task in task_batch]

        cost_ratio = (sum(costs) / len(costs)) / budget_target if costs else 0.0
        overrun = max(0.0, cost_ratio - 1.0)

        utilization = sum([
            min(costs[i], budgets[i]) / max(budgets[i], 1)
            for i in range(len(costs))
        ]) / len(costs) if budgets else 0.0

        return overrun + (1.0 - utilization)

    def _compute_L_latency(self, task_batch: List[Dict]) -> float:
        if not task_batch:
            return 0.0

        latencies = [task.get('latency_seconds', 0.0) for task in task_batch]
        if not latencies:
            return 0.0

        sla_target = 5.0
        latencies_sorted = sorted(latencies)
        p99_idx = max(0, int(len(latencies_sorted) * 0.99) - 1)
        p99_latency = latencies_sorted[p99_idx] if p99_idx < len(latencies_sorted) else latencies_sorted[-1]

        mean_latency = sum(latencies) / len(latencies)
        variance = sum((x - mean_latency) ** 2 for x in latencies) / len(latencies)
        variance_normalized = variance / (sla_target ** 2) if sla_target > 0 else 0.0

        return (p99_latency / sla_target) + 0.3 * variance_normalized

    def _compute_L_diversity(self, task_batch: List[Dict]) -> float:
        if not task_batch:
            return 0.0

        task_types = [task.get('task_type', 'unknown') for task in task_batch]
        unique_types = len(set(task_types))
        total_types = 15
        coverage = min(1.0, unique_types / total_types)

        engines = [task.get('routed_engine', 'unknown') for task in task_batch]
        engine_counts = {}
        for engine in engines:
            engine_counts[engine] = engine_counts.get(engine, 0) + 1

        engine_probs = [count / len(engines) for count in engine_counts.values()]
        engine_entropy = -sum(p * math.log(p + 1e-9) for p in engine_probs)
        max_entropy = math.log(4)
        entropy_normalized = engine_entropy / max_entropy if max_entropy > 0 else 0.0

        return (1.0 - coverage) + (1.0 - entropy_normalized)

    def update_weights(self, new_weights: Dict[str, float]) -> Dict[str, float]:
        """Update loss weights and record in audit."""
        old_weights = dict(self.weights)
        self.weights = new_weights

        delta = {k: new_weights[k] - old_weights[k] for k in self.weights}

        self.audit.write_event({
            'event_type': 'weights_updated',
            'tenant_id': self.tenant_id,
            'timestamp': datetime.now().isoformat(),
            'old_weights': old_weights,
            'new_weights': new_weights,
            'delta': delta,
            'reason': 'manual_update',
        })

        return new_weights
