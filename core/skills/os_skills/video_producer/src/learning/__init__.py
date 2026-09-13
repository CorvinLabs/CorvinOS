"""Video Producer Learning Infrastructure (Phase 4b).

Components:
- feedback_collector: Operator feedback collection (1-5 scale)
- confidence_scorer: Worker performance tracking via exponential smoothing
- model_selector: LLM model selection using epsilon-greedy bandit
- loop_integration: Closes the feedback loop with audit trail
"""

from .feedback_collector import FeedbackCollector, FeedbackRecord
from .confidence_scorer import ConfidenceScorer, ConfidenceMetric, WorkerConfidenceProfile
from .model_selector import ModelSelector, ModelPerformance, categorize_duration
from .loop_integration import LearningLoopIntegration, LearningLoopEvent

__all__ = [
    "FeedbackCollector",
    "FeedbackRecord",
    "ConfidenceScorer",
    "ConfidenceMetric",
    "WorkerConfidenceProfile",
    "ModelSelector",
    "ModelPerformance",
    "categorize_duration",
    "LearningLoopIntegration",
    "LearningLoopEvent",
]
