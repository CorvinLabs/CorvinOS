"""Training Data Pipeline for Learned Classifier (ADR-0393).

Collects training data from metrics.jsonl and feedback_log.jsonl,
creates balanced training sets, and manages dataset versioning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrainingDataPoint:
    """Single training example."""
    task_text: str
    complexity_label: str  # "simple", "moderate", "complex"
    confidence_score: float
    task_id: str
    task_type: Optional[str] = None
    user_id: Optional[str] = None
    feedback_timestamp: Optional[str] = None
    source: str = "feedback"  # "feedback", "measurement", "bootstrap"


@dataclass
class TrainingDataset:
    """Collected training dataset with metadata."""
    training_points: list[TrainingDataPoint] = field(default_factory=list)
    collected_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    version: str = "0.1.0"
    class_distribution: dict[str, int] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)

    def add_point(self, point: TrainingDataPoint) -> None:
        """Add training point and update distribution."""
        self.training_points.append(point)
        label = point.complexity_label
        self.class_distribution[label] = self.class_distribution.get(label, 0) + 1

    def is_balanced(self, min_ratio: float = 0.3) -> bool:
        """Check if class distribution is balanced (no class < min_ratio)."""
        if not self.class_distribution:
            return False

        total = sum(self.class_distribution.values())
        for count in self.class_distribution.values():
            if count / total < min_ratio:
                return False
        return True

    def balance_report(self) -> str:
        """Get human-readable balance report."""
        total = sum(self.class_distribution.values())
        lines = ["Class Distribution:"]
        for label, count in sorted(self.class_distribution.items()):
            pct = 100 * count / total if total > 0 else 0
            lines.append(f"  {label}: {count} ({pct:.1f}%)")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "collected_at": self.collected_at,
            "version": self.version,
            "total_points": len(self.training_points),
            "class_distribution": self.class_distribution,
            "source_files": self.source_files,
            "training_points": [
                {
                    "task_text": p.task_text,
                    "complexity_label": p.complexity_label,
                    "confidence_score": p.confidence_score,
                    "task_id": p.task_id,
                    "task_type": p.task_type,
                    "user_id": p.user_id,
                    "feedback_timestamp": p.feedback_timestamp,
                    "source": p.source,
                }
                for p in self.training_points
            ],
        }

    def save(self, path: Path) -> None:
        """Save dataset to JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Dataset saved to {path} ({len(self.training_points)} points)")


class ClassifierTrainer:
    """Collect and manage training data for classifier."""

    def __init__(self, data_dir: Optional[Path] = None):
        """Initialize trainer.

        Args:
            data_dir: Directory for training data (default: ~/.corvin/training_data)
        """
        self.data_dir = Path(data_dir or Path.home() / ".corvin" / "training_data")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def collect_training_data(
        self,
        metrics_path: Optional[Path] = None,
        feedback_log_path: Optional[Path] = None,
        min_confidence: float = 0.5,
        min_samples: int = 100,
    ) -> TrainingDataset:
        """Collect training data from metrics and feedback logs.

        Args:
            metrics_path: Path to metrics.jsonl
            feedback_log_path: Path to feedback_log.jsonl
            min_confidence: Minimum confidence score for inclusion
            min_samples: Minimum samples required per class

        Returns:
            TrainingDataset with collected and validated data
        """
        dataset = TrainingDataset()

        # Collect from metrics file
        if metrics_path and Path(metrics_path).exists():
            self._load_from_metrics(dataset, Path(metrics_path), min_confidence)
            dataset.source_files.append(str(metrics_path))

        # Collect from feedback log
        if feedback_log_path and Path(feedback_log_path).exists():
            self._load_from_feedback(dataset, Path(feedback_log_path), min_confidence)
            dataset.source_files.append(str(feedback_log_path))

        # Validate dataset
        if len(dataset.training_points) < min_samples:
            logger.warning(
                f"Only {len(dataset.training_points)} samples collected, "
                f"need at least {min_samples}"
            )

        # Check balance
        logger.info(dataset.balance_report())

        return dataset

    def _load_from_metrics(
        self,
        dataset: TrainingDataset,
        path: Path,
        min_confidence: float,
    ) -> None:
        """Load training data from metrics.jsonl."""
        logger.info(f"Loading metrics from {path}")
        count = 0

        try:
            with open(path) as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        record = json.loads(line)

                        # Extract fields (format may vary)
                        task_text = record.get("task_text") or record.get("task") or ""
                        if not task_text:
                            continue

                        # Look for complexity label
                        complexity = (
                            record.get("complexity") or
                            record.get("predicted_complexity") or
                            record.get("complexity_label")
                        )
                        if not complexity or complexity not in ["simple", "moderate", "complex"]:
                            continue

                        # Look for confidence
                        confidence = float(
                            record.get("confidence") or
                            record.get("confidence_score") or
                            min_confidence
                        )
                        if confidence < min_confidence:
                            continue

                        point = TrainingDataPoint(
                            task_text=task_text,
                            complexity_label=complexity,
                            confidence_score=confidence,
                            task_id=record.get("task_id", f"metrics_{count}"),
                            task_type=record.get("task_type"),
                            user_id=record.get("user_id"),
                            feedback_timestamp=record.get("timestamp"),
                            source="measurement",
                        )
                        dataset.add_point(point)
                        count += 1

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except IOError as e:
            logger.error(f"Error reading metrics file: {e}")

        logger.info(f"Loaded {count} points from metrics")

    def _load_from_feedback(
        self,
        dataset: TrainingDataset,
        path: Path,
        min_confidence: float,
    ) -> None:
        """Load training data from feedback_log.jsonl."""
        logger.info(f"Loading feedback from {path}")
        count = 0

        try:
            with open(path) as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        record = json.loads(line)

                        # Extract fields
                        task_text = record.get("task_text") or record.get("task") or ""
                        if not task_text:
                            continue

                        # Operator feedback on actual complexity
                        actual_complexity = (
                            record.get("actual_complexity") or
                            record.get("operator_rating") or
                            record.get("true_label")
                        )
                        if not actual_complexity or actual_complexity not in ["simple", "moderate", "complex"]:
                            continue

                        # Confidence is typically higher for operator feedback
                        confidence = float(
                            record.get("feedback_confidence") or 0.9
                        )
                        if confidence < min_confidence:
                            continue

                        point = TrainingDataPoint(
                            task_text=task_text,
                            complexity_label=actual_complexity,
                            confidence_score=confidence,
                            task_id=record.get("task_id", f"feedback_{count}"),
                            task_type=record.get("task_type"),
                            user_id=record.get("user_id"),
                            feedback_timestamp=record.get("feedback_timestamp"),
                            source="feedback",
                        )
                        dataset.add_point(point)
                        count += 1

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except IOError as e:
            logger.error(f"Error reading feedback file: {e}")

        logger.info(f"Loaded {count} points from feedback")

    def bootstrap_from_keyword_classifier(
        self,
        task_samples: list[str],
    ) -> TrainingDataset:
        """Bootstrap training data using keyword classifier.

        Used to initialize model when no feedback data exists.

        Args:
            task_samples: List of task descriptions to classify

        Returns:
            TrainingDataset with keyword-classifier labels
        """
        # Use late import to avoid namespace collision
        from operator.context_engineering.task_classifier import classify

        dataset = TrainingDataset(version="0.0.0-bootstrap")
        dataset.source_files.append("keyword_classifier_bootstrap")

        for i, task_text in enumerate(task_samples):
            result = classify(task_text)
            point = TrainingDataPoint(
                task_text=task_text,
                complexity_label=result.complexity.value,
                confidence_score=result.confidence,
                task_id=f"bootstrap_{i}",
                source="bootstrap",
            )
            dataset.add_point(point)

        logger.info(f"Bootstrapped {len(dataset.training_points)} samples")
        return dataset

    def create_training_split(
        self,
        dataset: TrainingDataset,
        test_ratio: float = 0.2,
        random_seed: int = 42,
    ) -> tuple[TrainingDataset, TrainingDataset]:
        """Split dataset into training and test sets.

        Args:
            dataset: Full dataset
            test_ratio: Ratio for test set (default 0.2)
            random_seed: Random seed for reproducibility

        Returns:
            (train_dataset, test_dataset)
        """
        import random
        random.seed(random_seed)

        # Stratified split by class
        by_class = {}
        for point in dataset.training_points:
            label = point.complexity_label
            if label not in by_class:
                by_class[label] = []
            by_class[label].append(point)

        train_dataset = TrainingDataset(version=dataset.version)
        test_dataset = TrainingDataset(version=dataset.version)

        for label, points in by_class.items():
            random.shuffle(points)
            split_idx = int(len(points) * (1 - test_ratio))

            for point in points[:split_idx]:
                train_dataset.add_point(point)

            for point in points[split_idx:]:
                test_dataset.add_point(point)

        logger.info(
            f"Split: {len(train_dataset.training_points)} train, "
            f"{len(test_dataset.training_points)} test"
        )

        return train_dataset, test_dataset

    def save_dataset(self, dataset: TrainingDataset, suffix: str = "") -> Path:
        """Save dataset to file.

        Args:
            dataset: TrainingDataset to save
            suffix: Optional suffix for filename

        Returns:
            Path to saved file
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"training_data_{timestamp}{suffix}.json"
        path = self.data_dir / filename

        dataset.save(path)
        return path
