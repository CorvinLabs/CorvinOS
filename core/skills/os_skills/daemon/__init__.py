"""Phase 3: Background Learning Daemon for Creator 2.0.

Event-driven daemon that:
1. Detects data source changes (ChangeDetector)
2. Tracks real skill usage (ExecutionListener)
3. Collects user feedback (FeedbackCollector)
4. Builds causal graph (CausalGraph: Source → Skill → Outcome)
5. Learns weight via gradient descent (WeightLearner)
6. Schedules skill regeneration (RegenerationScheduler)
7. Coordinates lifecycle (DaemonIntegration)

Architecture: Event-driven, immutable events, audit-first design.
Learning: Gradient descent with adaptive learning rate, per-weight convergence.
Recovery: Persistent checkpoints every 60s, watchdog restart.
"""

from .change_detector import DataSourceChangeDetector, SourceChangedEvent
from .execution_listener import SkillExecutionListener, SkillExecutedEvent
from .feedback_collector import FeedbackCollector, FeedbackEvent
from .causal_graph import CausalGraph, CausalNode, CausalEdge
from .weight_learner import WeightLearner, WeightUpdateEvent, ConvergenceStatus
from .regeneration_scheduler import RegenerationScheduler, RegenerationQueueItem
from .integration import DaemonIntegration, DaemonState

__all__ = [
    "DataSourceChangeDetector",
    "SourceChangedEvent",
    "SkillExecutionListener",
    "SkillExecutedEvent",
    "FeedbackCollector",
    "FeedbackEvent",
    "CausalGraph",
    "CausalNode",
    "CausalEdge",
    "WeightLearner",
    "WeightUpdateEvent",
    "ConvergenceStatus",
    "RegenerationScheduler",
    "RegenerationQueueItem",
    "DaemonIntegration",
    "DaemonState",
]
