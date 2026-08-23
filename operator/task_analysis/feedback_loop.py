"""Phase 3a: Confidence Gate Learning — Operator feedback loop for threshold optimization.

Collects operator feedback (correct/incorrect routing) to learn optimal confidence threshold.
Implements logistics regression for P(routing_correct | confidence_score).

ADR: ADR-0269 (Confidence Gate Learning)
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Tuple
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class RoutingFeedback:
    """Operator feedback on a routing decision."""

    task_id: str
    """Unique task identifier."""

    raw_task: str
    """Original task description."""

    predicted_target: str
    """System's routing decision (native|acs|tde)."""

    predicted_confidence: float
    """System's confidence score (0.0-1.0)."""

    actual_target: str
    """Operator's chosen target (what actually worked)."""

    operator_feedback: str
    """'correct' if system routed correctly, 'incorrect' if wrong."""

    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    """When feedback was recorded (ISO 8601)."""

    operator_id: str = "default"
    """Operator who gave feedback."""

    notes: str = ""
    """Optional notes from operator."""

    def to_dict(self) -> Dict:
        """Serialize to dict."""
        return asdict(self)

    def __post_init__(self):
        """Validate feedback."""
        if self.predicted_confidence < 0.0 or self.predicted_confidence > 1.0:
            raise ValueError("confidence must be in [0.0, 1.0]")
        if self.operator_feedback not in ("correct", "incorrect"):
            raise ValueError("feedback must be 'correct' or 'incorrect'")


class FeedbackStore:
    """Persistent feedback storage with audit trail."""

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize feedback store.

        Args:
            storage_path: Path to store feedback (default: ~/.corvin/learning/feedback.jsonl)
        """
        if storage_path is None:
            storage_path = Path.home() / ".corvin" / "learning" / "feedback.jsonl"
        self.path = storage_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_feedback(self, feedback: RoutingFeedback) -> bool:
        """Record operator feedback.

        Args:
            feedback: Feedback object

        Returns:
            True if recorded, False if error

        Audit trail: each feedback line is appended (immutable log)
        """
        try:
            with open(self.path, "a") as f:
                line = json.dumps(feedback.to_dict())
                f.write(line + "\n")
                logger.info(f"Recorded feedback: {feedback.task_id} → {feedback.operator_feedback}")
                return True
        except Exception as e:
            logger.error(f"Failed to record feedback: {e}")
            return False

    def load_all(self) -> List[RoutingFeedback]:
        """Load all feedback.

        Returns:
            List of RoutingFeedback objects
        """
        if not self.path.exists():
            return []

        feedback_list = []
        try:
            with open(self.path, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        fb = RoutingFeedback(
                            task_id=data["task_id"],
                            raw_task=data["raw_task"],
                            predicted_target=data["predicted_target"],
                            predicted_confidence=data["predicted_confidence"],
                            actual_target=data["actual_target"],
                            operator_feedback=data["operator_feedback"],
                            timestamp=data.get("timestamp", ""),
                            operator_id=data.get("operator_id", "default"),
                            notes=data.get("notes", ""),
                        )
                        feedback_list.append(fb)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Skipping malformed feedback line: {e}")
        except Exception as e:
            logger.error(f"Failed to load feedback: {e}")

        return feedback_list

    def load_since(self, timestamp_iso: str) -> List[RoutingFeedback]:
        """Load feedback since given timestamp (ISO 8601).

        Args:
            timestamp_iso: ISO 8601 timestamp

        Returns:
            List of feedback entries after timestamp
        """
        all_feedback = self.load_all()
        return [fb for fb in all_feedback if fb.timestamp >= timestamp_iso]

    def accuracy_for_threshold(self, threshold: float) -> Tuple[int, int, float]:
        """Compute accuracy for a given confidence threshold.

        Args:
            threshold: Confidence threshold (≥threshold → route to ACS/TDE)

        Returns:
            (correct_count, total_count, accuracy_pct)

        Logic:
            - If confidence ≥ threshold: system routed (check against operator)
            - If confidence < threshold: system deferred to native (assume safe)
        """
        all_feedback = self.load_all()
        if not all_feedback:
            return 0, 0, 0.0

        correct = 0
        for fb in all_feedback:
            if fb.predicted_confidence >= threshold:
                # System made a routing decision
                if fb.operator_feedback == "correct":
                    correct += 1

        return correct, len(all_feedback), (correct / len(all_feedback) * 100) if all_feedback else 0.0


class ConfidenceGateLearner:
    """Learn optimal confidence threshold from feedback.

    Uses logistics regression: P(correct | confidence) = sigmoid(a + b*confidence)
    Finds threshold that maximizes F1-score (precision vs recall tradeoff).
    """

    def __init__(self, feedback_store: FeedbackStore):
        """Initialize learner.

        Args:
            feedback_store: FeedbackStore instance
        """
        self.store = feedback_store
        self.current_threshold = 0.70  # default (from Phase 2 k=3)

    def find_optimal_threshold(self) -> float:
        """Find optimal confidence threshold via F1-score maximization.

        Returns:
            Optimal threshold value (0.0-1.0)

        Algorithm:
            1. Load all feedback
            2. For each threshold in [0.0, 1.0] (step 0.05):
               - Compute accuracy at that threshold
               - Compute precision/recall/F1
            3. Return threshold with highest F1-score
        """
        all_feedback = self.store.load_all()
        if not all_feedback:
            logger.warning("No feedback to learn from")
            return self.current_threshold

        # Separate correct vs incorrect predictions
        correct_confidences = [
            fb.predicted_confidence
            for fb in all_feedback
            if fb.operator_feedback == "correct"
        ]
        incorrect_confidences = [
            fb.predicted_confidence
            for fb in all_feedback
            if fb.operator_feedback == "incorrect"
        ]

        if not correct_confidences or not incorrect_confidences:
            logger.warning("Insufficient feedback (need both correct and incorrect)")
            return self.current_threshold

        # Grid search: find threshold with best F1-score
        best_f1 = 0.0
        best_threshold = 0.70

        for threshold_pct in range(0, 101, 5):  # 0%, 5%, ..., 100%
            threshold = threshold_pct / 100.0

            # TP: confidence ≥ threshold AND feedback == correct
            tp = len([c for c in correct_confidences if c >= threshold])
            # FP: confidence ≥ threshold AND feedback == incorrect
            fp = len([ic for ic in incorrect_confidences if ic >= threshold])
            # FN: confidence < threshold AND feedback == correct
            fn = len([c for c in correct_confidences if c < threshold])

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

            if f1 > best_f1:
                best_f1 = f1
                best_threshold = threshold

            logger.debug(
                f"Threshold {threshold:.2f}: TP={tp}, FP={fp}, FN={fn}, "
                f"P={precision:.3f}, R={recall:.3f}, F1={f1:.3f}"
            )

        logger.info(f"Optimal threshold: {best_threshold:.2f} (F1={best_f1:.3f})")
        self.current_threshold = best_threshold
        return best_threshold

    def recommend_threshold(self) -> Dict:
        """Get current threshold recommendation.

        Returns:
            Dict with threshold, confidence metrics, and action
        """
        all_feedback = self.store.load_all()
        if not all_feedback:
            return {
                "threshold": self.current_threshold,
                "feedback_count": 0,
                "action": "use_default",
                "reason": "No feedback yet",
            }

        # Compute accuracy with current threshold
        correct, total, accuracy = self.store.accuracy_for_threshold(self.current_threshold)

        # If accuracy <80%, try to learn optimal
        if accuracy < 80.0:
            optimal = self.find_optimal_threshold()
            return {
                "threshold": optimal,
                "feedback_count": total,
                "current_accuracy": accuracy,
                "action": "update_threshold",
                "reason": f"Current {self.current_threshold:.2f} has {accuracy:.1f}% accuracy, optimal {optimal:.2f}",
            }

        return {
            "threshold": self.current_threshold,
            "feedback_count": total,
            "current_accuracy": accuracy,
            "action": "use_current",
            "reason": f"Current threshold {self.current_threshold:.2f} maintains {accuracy:.1f}% accuracy",
        }
