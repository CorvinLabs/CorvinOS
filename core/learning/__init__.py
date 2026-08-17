"""TreeOfThoughts: Unified Learning System.

3-level hierarchy (Pattern → Method → Framework) with Bayesian confidence,
reachability proof via E2E tests + production usage, and active learning loop.

Core modules:
- models: TreeNode, LearningEvent, ConfidenceEvent
- storage: EventStore (append-only, date-partitioned)
- confidence: Bayesian update algorithm
- decorators: @e2e_for(pattern_id) for E2E proof
- reachability: ReachabilityMonitor (verify coverage)
- metrics: ExecutionMetrics, MetricsCollector
- active_loop: ActiveLearningLoop (exec → event → confidence)
"""

from .models import TreeNode, LearningEvent, ConfidenceEvent, CompositionType
from .storage import LearningEventStore
from .confidence import update_confidence, apply_decay
from .decorators import e2e_for
from .reachability import ReachabilityMonitor
from .metrics import ExecutionMetrics, MetricsCollector
from .active_loop import ActiveLearningLoop
from .integration import LearningIntegration
from .audit import AuditTrail
from .migration import MigrationPlanner

__all__ = [
    "TreeNode",
    "LearningEvent",
    "ConfidenceEvent",
    "CompositionType",
    "LearningEventStore",
    "update_confidence",
    "apply_decay",
    "e2e_for",
    "ReachabilityMonitor",
    "ExecutionMetrics",
    "MetricsCollector",
    "ActiveLearningLoop",
    "LearningIntegration",
    "AuditTrail",
    "MigrationPlanner",
]
