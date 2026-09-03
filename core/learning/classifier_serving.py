"""Model Serving for Learned Classifier (ADR-0393).

Handles model loading, hot-reloading on new versions, and inference
with automatic fallback to keyword classifier on failures.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from core.learning.classifier_model import LearnedClassifier, PredictionResult
from core.learning.task_features import TaskFeatureExtractor

logger = logging.getLogger(__name__)


class ClassifierService:
    """Production-grade classifier service with hot-reload and fallback."""

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        feature_extractor: Optional[TaskFeatureExtractor] = None,
        enable_fallback: bool = True,
    ):
        """Initialize classifier service."""
        self.model_dir = Path(model_dir or Path.home() / ".corvin" / "models")
        self.feature_extractor = feature_extractor or TaskFeatureExtractor()
        self.enable_fallback = enable_fallback

        # Current active classifier
        self.classifier: Optional[LearnedClassifier] = None
        self.current_version: Optional[str] = None

        # Metrics
        self.total_predictions = 0
        self.fallback_count = 0
        self.inference_times = []

    def load_model(self, version: str) -> bool:
        """Load a specific model version."""
        try:
            if self.classifier is None:
                self.classifier = LearnedClassifier(
                    self.feature_extractor,
                    model_dir=self.model_dir,
                    enable_fallback=self.enable_fallback,
                )

            self.classifier.load(version)
            self.current_version = version
            logger.info(f"Loaded model version {version}")
            return True

        except Exception as e:
            logger.error(f"Failed to load model {version}: {e}")
            return False

    def load_latest_model(self) -> bool:
        """Load the latest available model version."""
        if not self.model_dir.exists():
            logger.warning(f"Model directory not found: {self.model_dir}")
            return False

        # Find all .pkl files
        model_files = sorted(self.model_dir.glob("classifier_v*.pkl"))
        if not model_files:
            logger.warning("No models found in model directory")
            return False

        # Extract version from filename
        versions = []
        for f in model_files:
            try:
                version_str = f.stem.replace("classifier_v", "")
                versions.append((version_str, f))
            except (ValueError, IndexError):
                continue

        if not versions:
            logger.warning("No valid model versions found")
            return False

        # Sort by version string
        latest_version, _ = sorted(versions, key=lambda x: self._parse_version(x[0]))[-1]

        return self.load_model(latest_version)

    @staticmethod
    def _parse_version(version_str: str) -> tuple[int, ...]:
        """Parse version string for sorting."""
        try:
            parts = version_str.split(".")
            return tuple(int(p) for p in parts)
        except (ValueError, AttributeError):
            return (0, 0, 0)

    def predict(
        self,
        task_text: str,
        task_type: Optional[str] = None,
        prior_complexity: Optional[str] = None,
        domain_tags: Optional[list[str]] = None,
        user_id: Optional[str] = None,
    ) -> PredictionResult:
        """Predict task complexity."""
        import time
        start_time = time.time()

        try:
            # Extract features
            feature_vector = self.feature_extractor.extract_all_features(
                task_text,
                task_type=task_type,
                prior_complexity=prior_complexity,
                domain_tags=domain_tags,
                user_id=user_id,
            )

            # Make prediction
            if self.classifier is None and not self.load_latest_model():
                # Fallback: use keyword classifier
                if self.enable_fallback:
                    logger.warning("No model available, using keyword fallback")
                    result = self._keyword_fallback(task_text)
                    self.fallback_count += 1
                else:
                    raise RuntimeError("No model available and fallback disabled")
            else:
                result = self.classifier.predict(feature_vector)
                if result.used_fallback:
                    self.fallback_count += 1

            self.total_predictions += 1
            elapsed = time.time() - start_time
            self.inference_times.append(elapsed)

            return result

        except Exception as e:
            logger.error(f"Prediction failed: {e}, falling back to keyword classifier")
            if self.enable_fallback:
                result = self._keyword_fallback(task_text)
                self.fallback_count += 1
                self.total_predictions += 1
                return result
            else:
                raise

    def _keyword_fallback(self, task_text: str) -> PredictionResult:
        """Fallback to keyword classifier."""
        from .classifier_model import import_context_engineering
        import_context_engineering()
        from context_engineering.task_classifier import classify
        result = classify(task_text)
        return PredictionResult(
            complexity=result.complexity.value,
            confidence=result.confidence,
            model_version="keyword-fallback",
            used_fallback=True,
        )

    def get_inference_metrics(self) -> dict:
        """Get inference performance metrics."""
        import statistics

        metrics = {
            "total_predictions": self.total_predictions,
            "fallback_count": self.fallback_count,
            "fallback_rate": (
                self.fallback_count / self.total_predictions
                if self.total_predictions > 0
                else 0.0
            ),
            "current_model_version": self.current_version or "none",
        }

        if self.inference_times:
            metrics.update({
                "inference_time_ms_mean": statistics.mean(self.inference_times) * 1000,
                "inference_time_ms_median": statistics.median(self.inference_times) * 1000,
                "inference_time_ms_max": max(self.inference_times) * 1000,
                "inference_time_ms_min": min(self.inference_times) * 1000,
            })

        return metrics

    def get_model_status(self) -> str:
        """Get human-readable model status."""
        if self.classifier is None:
            return "No model loaded"

        lines = [
            f"Model version: {self.current_version or 'unknown'}",
            f"Total predictions: {self.total_predictions}",
            f"Fallback uses: {self.fallback_count}",
        ]

        if self.classifier.training_metrics:
            m = self.classifier.training_metrics
            lines.extend([
                f"Training F1: {m.f1_score:.3f}",
                f"Training accuracy: {m.accuracy:.3f}",
                f"Training samples: {m.test_set_size}",
            ])

        if self.inference_times:
            import statistics
            mean_ms = statistics.mean(self.inference_times) * 1000
            lines.append(f"Mean inference: {mean_ms:.2f}ms")

        return "\n".join(lines)
