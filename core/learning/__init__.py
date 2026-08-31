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
- anomaly_detector: AnomalyDetector, AnomalyAlert (Phase 8: Anomaly Detection & Auto-Recovery)
"""

from .models import TreeNode, LearningEvent, ConfidenceEvent, CompositionType
from .storage import LearningEventStore
from .confidence import update_confidence, apply_decay
from .decorators import e2e_for
from .reachability import ReachabilityMonitor
from .metrics import MetricsCollector, MetricRecord, MetricType, AggregatedMetrics
from .attention_budget import AttentionBudget, AttentionTracker, AttentionUsage, BudgetStatus, BudgetStats
from .active_loop import ActiveLearningLoop
from .integration import LearningIntegration
from .audit import AuditTrail
from .migration import MigrationPlanner
from .anomaly_detector import AnomalyDetector, AnomalyAlert
# Phase 4: Learned Classification for Context Engineering (ADR-0393)
from .task_features import TaskFeatureExtractor, FeatureVector
from .classifier_model import LearnedClassifier, PredictionResult, ClassifierMetrics
from .classifier_trainer import ClassifierTrainer, TrainingDataset, TrainingDataPoint
from .active_feedback import ActiveFeedbackCollector, FeedbackRecord, FeedbackMetrics
from .classifier_serving import ClassifierService

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
    # ADR-0319: Attention Budget
    "AttentionBudget",
    "AttentionTracker",
    "AttentionUsage",
    "BudgetStatus",
    "BudgetStats",
    # ADR-0320: Metrics Collection
    "MetricsCollector",
    "MetricRecord",
    "MetricType",
    "AggregatedMetrics",
    "ActiveLearningLoop",
    "LearningIntegration",
    "AuditTrail",
    "MigrationPlanner",
    "AnomalyDetector",
    "AnomalyAlert",
    # Phase 4: Learned Classifier (ADR-0393)
    "TaskFeatureExtractor",
    "FeatureVector",
    "LearnedClassifier",
    "PredictionResult",
    "ClassifierMetrics",
    "ClassifierTrainer",
    "TrainingDataset",
    "TrainingDataPoint",
    "ActiveFeedbackCollector",
    "FeedbackRecord",
    "FeedbackMetrics",
    "ClassifierService",
]
