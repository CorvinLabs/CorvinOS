"""TreeOfThoughts: Unified Learning System.

3-level hierarchy (Pattern → Method → Framework) with Bayesian confidence and an
active learning loop.

Core modules:
- models: TreeNode, LearningEvent, ConfidenceEvent
- storage: EventStore (append-only, date-partitioned)
- confidence: Bayesian update algorithm
- metrics: ExecutionMetrics, MetricsCollector
- active_loop: ActiveLearningLoop (exec → event → confidence)
- anomaly_detector: AnomalyDetector, AnomalyAlert (Phase 8: Anomaly Detection & Auto-Recovery)
"""

from .models import TreeNode, LearningEvent, ConfidenceEvent, CompositionType
from .storage import LearningEventStore
from .confidence import update_confidence, apply_decay
from .metrics import (
    MetricsCollector, MetricRecord, MetricType, AggregatedMetrics,
    ExecutionMetrics, ExecutionMetricsRecorder,
)
from .active_loop import ActiveLearningLoop
from .integration import LearningIntegration
from .anomaly_detector import AnomalyDetector, AnomalyAlert

__all__ = [
    "TreeNode",
    "LearningEvent",
    "ConfidenceEvent",
    "CompositionType",
    "LearningEventStore",
    "update_confidence",
    "apply_decay",
    # ADR-0320: Metrics Collection
    "MetricsCollector",
    "MetricRecord",
    "MetricType",
    "AggregatedMetrics",
    "ExecutionMetrics",
    "ExecutionMetricsRecorder",
    "ActiveLearningLoop",
    "LearningIntegration",
    "AnomalyDetector",
    "AnomalyAlert",
]
