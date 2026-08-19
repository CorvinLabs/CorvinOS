"""Active Learning Feedback Loop (ADR-0393).

Collects operator feedback on classifier predictions, manages retraining,
and tracks model performance over time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FeedbackRecord:
    """Single feedback record from operator."""
    turn_id: str
    task_text: str
    predicted_complexity: str
    actual_complexity: str
    confidence_score: float
    feedback_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    correct: bool = field(init=False)
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    def __post_init__(self):
        """Calculate correctness."""
        self.correct = self.predicted_complexity == self.actual_complexity


@dataclass
class FeedbackMetrics:
    """Feedback collection and model performance metrics."""
    total_feedback_records: int = 0
    correct_predictions: int = 0
    incorrect_predictions: int = 0
    accuracy: float = 0.0
    complexity_by_class: dict[str, int] = field(default_factory=dict)
    feedback_start_date: Optional[str] = None
    feedback_end_date: Optional[str] = None

    @property
    def accuracy_pct(self) -> float:
        """Accuracy as percentage."""
        return self.accuracy * 100 if self.accuracy > 0 else 0.0


class ActiveFeedbackCollector:
    """Collect and manage operator feedback for continuous learning."""

    def __init__(self, feedback_dir: Optional[Path] = None):
        """Initialize feedback collector."""
        self.feedback_dir = Path(feedback_dir or Path.home() / ".corvin" / "feedback")
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.feedback_log_path = self.feedback_dir / "feedback.jsonl"

    def record_feedback(self, record: FeedbackRecord) -> None:
        """Record operator feedback on a prediction."""
        with open(self.feedback_log_path, "a") as f:
            f.write(json.dumps({
                "turn_id": record.turn_id,
                "task_text": record.task_text,
                "predicted_complexity": record.predicted_complexity,
                "actual_complexity": record.actual_complexity,
                "confidence_score": record.confidence_score,
                "correct": record.correct,
                "feedback_timestamp": record.feedback_timestamp,
                "user_id": record.user_id,
                "session_id": record.session_id,
            }) + "\n")

        logger.info(
            f"Feedback recorded: {record.turn_id} "
            f"({record.predicted_complexity} → {record.actual_complexity})"
        )

    def collect_feedback_since(self, since: datetime) -> list[FeedbackRecord]:
        """Collect all feedback since a timestamp."""
        records = []

        if not self.feedback_log_path.exists():
            return records

        try:
            with open(self.feedback_log_path) as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                        ts = datetime.fromisoformat(data["feedback_timestamp"].replace("Z", "+00:00"))

                        if ts >= since:
                            record = FeedbackRecord(
                                turn_id=data["turn_id"],
                                task_text=data["task_text"],
                                predicted_complexity=data["predicted_complexity"],
                                actual_complexity=data["actual_complexity"],
                                confidence_score=data["confidence_score"],
                                feedback_timestamp=data["feedback_timestamp"],
                                user_id=data.get("user_id"),
                                session_id=data.get("session_id"),
                            )
                            records.append(record)

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except IOError as e:
            logger.error(f"Error reading feedback log: {e}")

        return records

    def get_feedback_metrics(self) -> FeedbackMetrics:
        """Calculate feedback collection and accuracy metrics."""
        metrics = FeedbackMetrics()

        if not self.feedback_log_path.exists():
            return metrics

        try:
            with open(self.feedback_log_path) as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                        metrics.total_feedback_records += 1

                        is_correct = data.get("correct", False)
                        if is_correct:
                            metrics.correct_predictions += 1
                        else:
                            metrics.incorrect_predictions += 1

                        # Track by class
                        actual = data.get("actual_complexity", "unknown")
                        metrics.complexity_by_class[actual] = (
                            metrics.complexity_by_class.get(actual, 0) + 1
                        )

                        # Update date range
                        ts_str = data.get("feedback_timestamp", "")
                        if metrics.feedback_start_date is None:
                            metrics.feedback_start_date = ts_str
                        metrics.feedback_end_date = ts_str

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except IOError as e:
            logger.error(f"Error calculating metrics: {e}")

        # Calculate accuracy
        if metrics.total_feedback_records > 0:
            metrics.accuracy = metrics.correct_predictions / metrics.total_feedback_records

        return metrics

    def should_retrain(
        self,
        since_last_retrain: timedelta = timedelta(days=7),
        min_new_feedback: int = 100,
        accuracy_threshold: float = 0.85,
    ) -> tuple[bool, str]:
        """Determine if retraining is needed."""
        metrics = self.get_feedback_metrics()

        if metrics.total_feedback_records < min_new_feedback:
            return False, f"Only {metrics.total_feedback_records} feedback records (need {min_new_feedback})"

        if metrics.accuracy < accuracy_threshold:
            return True, f"Accuracy {metrics.accuracy_pct:.1f}% < threshold {accuracy_threshold*100:.1f}%"

        # Check time since last retrain
        last_retrain_marker = self.feedback_dir / ".last_retrain"
        if last_retrain_marker.exists():
            last_retrain = datetime.fromisoformat(last_retrain_marker.read_text())
            elapsed = datetime.utcnow() - last_retrain
            if elapsed > since_last_retrain:
                return True, f"Time since last retrain: {elapsed.days}d > {since_last_retrain.days}d"

        return False, "No retraining needed"

    def mark_retrain_complete(self) -> None:
        """Mark current time as last successful retrain."""
        last_retrain_marker = self.feedback_dir / ".last_retrain"
        last_retrain_marker.write_text(datetime.utcnow().isoformat())

    def get_confusion_matrix_report(self) -> str:
        """Generate human-readable confusion matrix from feedback."""
        confusion = {}
        labels = ["simple", "moderate", "complex"]

        for pred in labels:
            confusion[pred] = {}
            for actual in labels:
                confusion[pred][actual] = 0

        if not self.feedback_log_path.exists():
            return "No feedback recorded"

        try:
            with open(self.feedback_log_path) as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                        pred = data.get("predicted_complexity", "unknown")
                        actual = data.get("actual_complexity", "unknown")

                        if pred in confusion and actual in confusion[pred]:
                            confusion[pred][actual] += 1

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except IOError:
            return "Error reading feedback log"

        # Format as table
        lines = ["Confusion Matrix (predicted → actual):"]
        lines.append(f"{'':12} {' '.join(f'{l:10}' for l in labels)}")
        for pred in labels:
            values = [str(confusion[pred][actual]) for actual in labels]
            lines.append(f"{pred:12} {' '.join(f'{v:10}' for v in values)}")

        return "\n".join(lines)

    def export_feedback_for_retraining(
        self,
        since: Optional[datetime] = None,
        output_path: Optional[Path] = None,
    ) -> Path:
        """Export feedback records for model retraining."""
        if since is None:
            since = datetime.utcnow() - timedelta(days=30)

        if output_path is None:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_path = self.feedback_dir / f"export_{timestamp}.jsonl"

        records = self.collect_feedback_since(since)

        with open(output_path, "w") as f:
            for record in records:
                f.write(json.dumps({
                    "task_text": record.task_text,
                    "actual_complexity": record.actual_complexity,
                    "predicted_complexity": record.predicted_complexity,
                    "confidence_score": record.confidence_score,
                    "feedback_timestamp": record.feedback_timestamp,
                    "correct": record.correct,
                    "user_id": record.user_id,
                    "session_id": record.session_id,
                }) + "\n")

        logger.info(f"Exported {len(records)} feedback records to {output_path}")
        return output_path
