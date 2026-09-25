"""
ML Feedback Loop Pipeline (Phase 3c)
Captures user corrections as labeled training data for model retraining

Workflow:
1. User provides feedback on summary quality (0-5 rating)
2. Feedback aggregated by summary strategy + content type
3. Retraining triggered when threshold reached
4. New model versioned + A/B tested
5. Best model promoted to production

@date 2026-09-25
@phase Phase 3c: ML Feedback Loop (MVP)
"""

from typing import Optional, Dict, List, Any
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ModelStatus(str, Enum):
    """Model lifecycle status"""
    TRAINING = "training"
    VALIDATION = "validation"
    STAGING = "staging"  # A/B test environment
    PRODUCTION = "production"
    DEPRECATED = "deprecated"


class ModelVersion:
    """Represents a trained model version"""

    def __init__(
        self,
        version_id: str,
        strategy: str,  # "syntax_aware", "visual_aware", etc.
        status: ModelStatus = ModelStatus.TRAINING,
        training_samples: int = 0,
        validation_accuracy: Optional[float] = None,
        ab_test_config: Optional[Dict[str, Any]] = None,
    ):
        self.version_id = version_id
        self.strategy = strategy
        self.status = status
        self.training_samples = training_samples
        self.validation_accuracy = validation_accuracy
        self.ab_test_config = ab_test_config or {}
        self.created_at = datetime.utcnow()
        self.promoted_at: Optional[datetime] = None

    def promote_to_production(self):
        """Promote model to production after A/B test success"""
        self.status = ModelStatus.PRODUCTION
        self.promoted_at = datetime.utcnow()
        logger.info(f"Model {self.version_id} promoted to PRODUCTION")


class FeedbackCollector:
    """Collects feedback for model retraining"""

    def __init__(self, retraining_threshold: int = 100):
        self._feedback_buffer: Dict[str, List[Dict[str, Any]]] = {}
        self._retraining_threshold = retraining_threshold

    def add_feedback(
        self,
        session_id: str,
        strategy: str,
        summary: str,
        user_correction: Optional[str],
        quality_score: float,  # 0-5
    ):
        """Add user feedback for model retraining"""
        if strategy not in self._feedback_buffer:
            self._feedback_buffer[strategy] = []

        feedback_record = {
            "session_id": session_id,
            "summary": summary,
            "user_correction": user_correction,
            "quality_score": quality_score,
            "timestamp": datetime.utcnow().isoformat(),
        }

        self._feedback_buffer[strategy].append(feedback_record)

        # Check if retraining threshold reached
        if len(self._feedback_buffer[strategy]) >= self._retraining_threshold:
            logger.info(f"Retraining threshold reached for strategy '{strategy}'")
            return self._feedback_buffer[strategy].copy()

        return None

    def get_feedback_stats(self, strategy: str) -> Dict[str, Any]:
        """Get feedback statistics for a strategy"""
        if strategy not in self._feedback_buffer:
            return {"count": 0, "avg_score": 0.0}

        feedback = self._feedback_buffer[strategy]
        scores = [f["quality_score"] for f in feedback]

        return {
            "count": len(feedback),
            "avg_score": sum(scores) / len(scores) if scores else 0.0,
            "min_score": min(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
        }


class ModelRegistry:
    """Registry of trained model versions"""

    def __init__(self):
        self._models: Dict[str, ModelVersion] = {}
        self._production_models: Dict[str, str] = {}  # strategy -> version_id

    def register_model(self, model: ModelVersion):
        """Register a new trained model version"""
        self._models[model.version_id] = model
        logger.info(f"Model {model.version_id} registered ({model.strategy})")

    def promote_model(self, model_id: str):
        """Promote model to production"""
        if model_id not in self._models:
            raise ValueError(f"Model {model_id} not found")

        model = self._models[model_id]
        model.promote_to_production()
        self._production_models[model.strategy] = model_id

    def get_production_model(self, strategy: str) -> Optional[ModelVersion]:
        """Get production model for strategy"""
        model_id = self._production_models.get(strategy)
        if model_id:
            return self._models.get(model_id)
        return None

    def get_model_versions(self, strategy: str) -> List[ModelVersion]:
        """Get all versions for a strategy"""
        return [
            m for m in self._models.values()
            if m.strategy == strategy
        ]


class ABTestFramework:
    """A/B testing framework for model promotion"""

    def __init__(self, test_split: float = 0.1):  # 10% canary
        self._test_split = test_split
        self._active_tests: Dict[str, Dict[str, Any]] = {}

    def start_test(
        self,
        model_id: str,
        strategy: str,
        baseline_model_id: Optional[str] = None,
        success_metric_threshold: float = 0.95,
    ):
        """Start A/B test for a model"""
        test_config = {
            "model_id": model_id,
            "strategy": strategy,
            "baseline_model_id": baseline_model_id,
            "success_metric_threshold": success_metric_threshold,
            "started_at": datetime.utcnow().isoformat(),
            "test_results": {
                "candidate_quality": 0.0,
                "baseline_quality": 0.0,
                "sample_size": 0,
            },
        }

        self._active_tests[model_id] = test_config
        logger.info(f"A/B test started for model {model_id}")
        return test_config

    def record_test_result(self, model_id: str, quality_score: float, is_candidate: bool = True):
        """Record a test result"""
        if model_id not in self._active_tests:
            return

        test = self._active_tests[model_id]
        if is_candidate:
            # Update candidate score (exponential moving average)
            alpha = 0.1
            test["test_results"]["candidate_quality"] = (
                alpha * quality_score +
                (1 - alpha) * test["test_results"]["candidate_quality"]
            )
        else:
            # Update baseline score
            alpha = 0.1
            test["test_results"]["baseline_quality"] = (
                alpha * quality_score +
                (1 - alpha) * test["test_results"]["baseline_quality"]
            )

        test["test_results"]["sample_size"] += 1

    def check_test_complete(self, model_id: str, min_samples: int = 50) -> bool:
        """Check if test has enough data"""
        if model_id not in self._active_tests:
            return False

        test = self._active_tests[model_id]
        return test["test_results"]["sample_size"] >= min_samples

    def get_test_winner(self, model_id: str) -> Optional[str]:
        """Determine A/B test winner"""
        if model_id not in self._active_tests:
            return None

        test = self._active_tests[model_id]
        if test["test_results"]["candidate_quality"] > test["test_results"]["baseline_quality"]:
            return model_id
        elif test["baseline_model_id"]:
            return test["baseline_model_id"]
        return None


# Singleton instances
_feedback_collector: Optional[FeedbackCollector] = None
_model_registry: Optional[ModelRegistry] = None
_ab_framework: Optional[ABTestFramework] = None


def get_feedback_collector() -> FeedbackCollector:
    """Get feedback collector"""
    global _feedback_collector
    if _feedback_collector is None:
        _feedback_collector = FeedbackCollector(retraining_threshold=100)
    return _feedback_collector


def get_model_registry() -> ModelRegistry:
    """Get model registry"""
    global _model_registry
    if _model_registry is None:
        _model_registry = ModelRegistry()
    return _model_registry


def get_ab_framework() -> ABTestFramework:
    """Get A/B testing framework"""
    global _ab_framework
    if _ab_framework is None:
        _ab_framework = ABTestFramework(test_split=0.1)
    return _ab_framework
