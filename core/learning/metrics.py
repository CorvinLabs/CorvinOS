"""Metrics collection: latency, cost, success_rate (Phase 2)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from .storage import LearningEventStore
from .models import LearningEvent


@dataclass
class ExecutionMetrics:
    """Metrics from a single pattern/method execution."""
    subject_id: str  # pattern_id or method_id
    latency_ms: float
    cost_tokens: int = 0
    success: bool = True
    error_type: str | None = None
    context: dict = field(default_factory=dict)  # {task_id, user_id, stage}


class MetricsCollector:
    """Collect and emit metrics as LearningEvents."""
    
    def __init__(self, store: LearningEventStore):
        self.store = store
    
    def record(self, metrics: ExecutionMetrics) -> None:
        """Record execution metrics and emit LearningEvent."""
        if metrics.success:
            event_type = "used"
            confidence_delta = +0.05
            reason = "succeeded in production"
        else:
            event_type = "failed"
            confidence_delta = -0.15
            reason = f"failed with {metrics.error_type}"
        
        event = LearningEvent(
            subject_id=metrics.subject_id,
            event_type=event_type,
            confidence_delta=confidence_delta,
            reason=reason,
            context={
                **metrics.context,
                "latency_ms": metrics.latency_ms,
                "cost_tokens": metrics.cost_tokens,
                "error_type": metrics.error_type,
            }
        )
        
        self.store.append_event(metrics.subject_id, event)
        
        # Update calls_in_production counter
        node = self.store.get_node(metrics.subject_id)
        if node:
            node.calls_in_production += 1
