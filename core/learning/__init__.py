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

Phase 6: Learning Loop
- feedback_pipeline: CI/CD feedback ingestion, pattern analysis, prompt refinement
- ab_testing: A/B testing framework, statistical analysis, auto-rollout/rollback
- learning_dashboard: Metrics collection, aggregation, alerting, visualization
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

# Optional imports — numpy/sklearn may not be available in all environments
try:
    from .classifier_model import LearnedClassifier, PredictionResult, ClassifierMetrics
    from .classifier_trainer import ClassifierTrainer, TrainingDataset, TrainingDataPoint
    from .active_feedback import ActiveFeedbackCollector, FeedbackRecord, FeedbackMetrics
    from .classifier_serving import ClassifierService
    _CLASSIFIER_AVAILABLE = True
except ImportError:
    # Gracefully handle missing numpy/sklearn
    LearnedClassifier = None  # type: ignore
    PredictionResult = None  # type: ignore
    ClassifierMetrics = None  # type: ignore
    ClassifierTrainer = None  # type: ignore
    TrainingDataset = None  # type: ignore
    TrainingDataPoint = None  # type: ignore
    ActiveFeedbackCollector = None  # type: ignore
    FeedbackRecord = None  # type: ignore
    FeedbackMetrics = None  # type: ignore
    ClassifierService = None  # type: ignore
    _CLASSIFIER_AVAILABLE = False

# Phase 6: Learning Loop (ADR-0428)
try:
    from .feedback_pipeline import (
        FeedbackPipeline,
        TestResult,
        TestResultType,
        FailureCategory,
        PatternAnalysis,
        PromptRefinement,
    )
    from .ab_testing import (
        ABTestingFramework,
        ExperimentMetric,
        ExperimentGroup,
        ExperimentStatus,
        Experiment,
        RolloutPlan,
        RolloutPhase,
    )
    from .learning_dashboard import (
        LearningDashboard,
        MetricType as DashboardMetricType,
        MetricPoint,
        AggregatedMetric,
        MetricAlert,
        AggregationWindow,
    )
    _LEARNING_LOOP_AVAILABLE = True
except ImportError:
    # Gracefully handle missing dependencies
    FeedbackPipeline = None  # type: ignore
    TestResult = None  # type: ignore
    TestResultType = None  # type: ignore
    FailureCategory = None  # type: ignore
    PatternAnalysis = None  # type: ignore
    PromptRefinement = None  # type: ignore
    ABTestingFramework = None  # type: ignore
    ExperimentMetric = None  # type: ignore
    ExperimentGroup = None  # type: ignore
    ExperimentStatus = None  # type: ignore
    Experiment = None  # type: ignore
    RolloutPlan = None  # type: ignore
    RolloutPhase = None  # type: ignore
    LearningDashboard = None  # type: ignore
    DashboardMetricType = None  # type: ignore
    MetricPoint = None  # type: ignore
    AggregatedMetric = None  # type: ignore
    MetricAlert = None  # type: ignore
    AggregationWindow = None  # type: ignore
    _LEARNING_LOOP_AVAILABLE = False

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
]

# Conditionally add classifier modules
if _CLASSIFIER_AVAILABLE:
    __all__.extend([
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
    ])

# Conditionally add learning loop modules
if _LEARNING_LOOP_AVAILABLE:
    __all__.extend([
        "FeedbackPipeline",
        "TestResult",
        "TestResultType",
        "FailureCategory",
        "PatternAnalysis",
        "PromptRefinement",
        "ABTestingFramework",
        "ExperimentMetric",
        "ExperimentGroup",
        "ExperimentStatus",
        "Experiment",
        "RolloutPlan",
        "RolloutPhase",
        "LearningDashboard",
        "MetricPoint",
        "AggregatedMetric",
        "MetricAlert",
        "AggregationWindow",
    ])
