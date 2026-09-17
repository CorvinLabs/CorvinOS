"""Context-Drift Feedback Loop Integration.

Learning loop: collect feedback → tune thresholds → improve detection accuracy
Implements ADR-0314 (Learning Infrastructure) for Context-Drift.

Every goal alignment check emits a learning event:
- User provides feedback (was drift detection correct?)
- System learns from feedback
- Thresholds auto-tune via feedback optimizer
- Audit trail logs all tuning decisions (GDPR compliance)

ADR-0407: Session Context Drift Prevention
ADR-0314: Learning Infrastructure - Event Schema
ADR-0362: Production Deployment Framework
"""

import logging
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from pathlib import Path
import hashlib

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackEvent:
    """Immutable user feedback on drift detection accuracy."""
    goal_id: str
    alignment_score: float
    user_rating: int  # 1-5 stars
    was_drift_correct: bool  # Did we correctly identify drift?
    timestamp: str  # ISO 8601
    tenant_id: str = "default"

    def to_dict(self) -> dict:
        """Convert to dict (audit trail compatible)."""
        return asdict(self)


@dataclass
class ThresholdTuningResult:
    """Result of threshold optimization."""
    old_threshold: float
    new_threshold: float
    accuracy_before: float
    accuracy_after: float
    accuracy_improvement: float
    samples_used: int
    timestamp: str


class ContextDriftFeedbackStore:
    """Store for feedback events (append-only, audit-safe)."""

    def __init__(self, store_path: str = "~/.corvin/learning/context-drift-feedback.jsonl"):
        self.store_path = Path(store_path).expanduser()
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def record_feedback(self, feedback: FeedbackEvent) -> None:
        """Record feedback event (append-only)."""
        with open(self.store_path, "a") as f:
            f.write(json.dumps(feedback.to_dict()) + "\n")
        logger.info(f"✅ Feedback recorded: goal={feedback.goal_id}, correct={feedback.was_drift_correct}")

    def get_recent_feedback(self, days: int = 7, limit: int = 10000) -> List[FeedbackEvent]:
        """Get recent feedback events (within N days)."""
        if not self.store_path.exists():
            return []

        cutoff = datetime.utcnow() - timedelta(days=days)
        feedback_list = []

        with open(self.store_path, "r") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    timestamp = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
                    if timestamp >= cutoff:
                        feedback = FeedbackEvent(**data)
                        feedback_list.append(feedback)
                except Exception as e:
                    logger.warning(f"Failed to parse feedback line: {e}")

        return feedback_list[:limit]

    def count_feedback(self, goal_id: Optional[str] = None) -> int:
        """Count total feedback events."""
        if not self.store_path.exists():
            return 0

        count = 0
        with open(self.store_path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line.strip())
                    if goal_id is None or data.get("goal_id") == goal_id:
                        count += 1
                except Exception:
                    pass
        return count


class ContextDriftFeedbackLoop:
    """Learning loop: collect feedback → tune thresholds → improve detection."""

    def __init__(self, store: Optional[ContextDriftFeedbackStore] = None):
        self.store = store or ContextDriftFeedbackStore()
        self.current_threshold = 0.35  # Default from ADR-0407
        self.tuning_history: List[ThresholdTuningResult] = []

    def collect_feedback(
        self,
        goal_id: str,
        alignment_score: float,
        user_feedback: Dict,
        tenant_id: str = "default"
    ) -> FeedbackEvent:
        """Collect user feedback on drift detection accuracy.

        Args:
            goal_id: ID of the goal being checked
            alignment_score: The computed alignment score (0.0-1.0)
            user_feedback: {
                'rating': 1-5,  # How helpful was the drift detection?
                'was_correct': bool,  # Did we correctly identify drift?
            }
            tenant_id: Tenant ID for multi-tenant isolation

        Returns:
            FeedbackEvent recorded to store
        """
        feedback = FeedbackEvent(
            goal_id=goal_id,
            alignment_score=alignment_score,
            user_rating=user_feedback["rating"],
            was_drift_correct=user_feedback["was_correct"],
            timestamp=datetime.utcnow().isoformat() + "Z",
            tenant_id=tenant_id
        )

        # Store feedback
        self.store.record_feedback(feedback)

        # Emit audit event (GDPR compliance)
        logger.info(f"📊 Feedback: goal={goal_id}, score={alignment_score:.2f}, correct={feedback.was_drift_correct}")

        return feedback

    def tune_thresholds(self, target_accuracy: float = 0.85) -> Optional[ThresholdTuningResult]:
        """Optimize drift detection thresholds based on feedback.

        Uses binary search to find the threshold that maximizes accuracy.
        Only updates if we have sufficient feedback data (≥10 samples).

        Args:
            target_accuracy: Target accuracy to aim for (0.0-1.0)

        Returns:
            ThresholdTuningResult if tuning occurred, None otherwise
        """
        # Collect recent feedback
        feedback_list = self.store.get_recent_feedback(days=30)

        if len(feedback_list) < 10:
            logger.info(f"⏳ Insufficient feedback for tuning: {len(feedback_list)}/10 samples")
            return None

        logger.info(f"🎯 Optimizing threshold with {len(feedback_list)} feedback samples...")

        # Extract scores and correctness labels
        scores = [f.alignment_score for f in feedback_list]
        correctness = [f.was_drift_correct for f in feedback_list]

        # Binary search for optimal threshold
        old_threshold = self.current_threshold
        new_threshold = self._find_optimal_threshold(scores, correctness)

        # Compute accuracy improvement
        old_accuracy = self._compute_accuracy(scores, correctness, old_threshold)
        new_accuracy = self._compute_accuracy(scores, correctness, new_threshold)
        improvement = new_accuracy - old_accuracy

        # Update threshold
        self.current_threshold = new_threshold

        result = ThresholdTuningResult(
            old_threshold=old_threshold,
            new_threshold=new_threshold,
            accuracy_before=old_accuracy,
            accuracy_after=new_accuracy,
            accuracy_improvement=improvement,
            samples_used=len(feedback_list),
            timestamp=datetime.utcnow().isoformat() + "Z"
        )

        self.tuning_history.append(result)

        logger.info(f"✅ Threshold tuned: {old_threshold:.3f} → {new_threshold:.3f}")
        logger.info(f"   Accuracy: {old_accuracy:.2%} → {new_accuracy:.2%} (+{improvement:.2%})")

        return result

    def _find_optimal_threshold(self, scores: List[float], correctness: List[bool]) -> float:
        """Find threshold that maximizes accuracy (binary search).

        The threshold determines when we declare drift:
        - If score < threshold: we say "DRIFT DETECTED"
        - If score >= threshold: we say "NO DRIFT"

        We want to maximize accuracy on user feedback.
        """
        best_accuracy = 0.0
        best_threshold = 0.35  # default

        # Search over 0.0 to 1.0 in steps of 0.01
        for candidate_threshold in [i * 0.01 for i in range(0, 101)]:
            # Predict drift/no-drift for each sample
            predictions = [score < candidate_threshold for score in scores]

            # Compute accuracy
            accuracy = sum(p == c for p, c in zip(predictions, correctness)) / len(correctness)

            if accuracy > best_accuracy:
                best_accuracy = accuracy
                best_threshold = candidate_threshold

        return best_threshold

    def _compute_accuracy(
        self,
        scores: List[float],
        correctness: List[bool],
        threshold: float
    ) -> float:
        """Compute accuracy for a given threshold."""
        if not scores:
            return 0.0

        predictions = [score < threshold for score in scores]
        accuracy = sum(p == c for p, c in zip(predictions, correctness)) / len(correctness)
        return accuracy

    def get_feedback_quality_score(self) -> float:
        """Get overall feedback quality/accuracy score (0.0-1.0).

        This measures how consistent user feedback is (higher = more consistent).
        Used for confidence scoring in deployment monitoring.
        """
        feedback_list = self.store.get_recent_feedback(days=7)
        if not feedback_list:
            return 0.5  # Default confidence

        scores = [f.alignment_score for f in feedback_list]
        correctness = [f.was_drift_correct for f in feedback_list]

        accuracy = self._compute_accuracy(scores, correctness, self.current_threshold)
        return accuracy

    def get_convergence_status(self) -> Dict:
        """Get convergence status of the learning loop.

        Returns:
            {
                'is_converged': bool,  # Has accuracy plateaued?
                'accuracy_trend': float,  # Recent improvement trend
                'samples_since_last_tuning': int,
                'recommendations': List[str],  # Suggestions for operator
            }
        """
        if len(self.tuning_history) < 2:
            return {
                "is_converged": False,
                "accuracy_trend": 0.0,
                "samples_since_last_tuning": self.store.count_feedback(),
                "recommendations": ["Collect more feedback to enable learning"]
            }

        # Check recent trend
        recent_results = self.tuning_history[-3:]
        improvements = [r.accuracy_improvement for r in recent_results]
        avg_improvement = sum(improvements) / len(improvements)

        # Converged if recent improvements are <1%
        is_converged = avg_improvement < 0.01

        recommendations = []
        if is_converged:
            recommendations.append("Threshold converged — collection continues for robustness")
        if avg_improvement < 0:
            recommendations.append("⚠️  Recent tuning degrading accuracy — monitor feedback quality")

        return {
            "is_converged": is_converged,
            "accuracy_trend": avg_improvement,
            "samples_since_last_tuning": self.store.count_feedback() - (self.tuning_history[-1].samples_used if self.tuning_history else 0),
            "recommendations": recommendations
        }

    def get_metrics(self) -> Dict:
        """Get learning loop metrics for monitoring dashboard."""
        feedback_list = self.store.get_recent_feedback(days=7)

        if not feedback_list:
            return {
                "total_feedback_samples": 0,
                "feedback_quality": 0.5,
                "current_threshold": self.current_threshold,
                "tuning_iterations": len(self.tuning_history),
                "last_tuning": None,
            }

        scores = [f.alignment_score for f in feedback_list]
        correctness = [f.was_drift_correct for f in feedback_list]
        accuracy = self._compute_accuracy(scores, correctness, self.current_threshold)

        last_tuning = self.tuning_history[-1] if self.tuning_history else None

        return {
            "total_feedback_samples": len(feedback_list),
            "feedback_quality": accuracy,
            "current_threshold": self.current_threshold,
            "tuning_iterations": len(self.tuning_history),
            "last_tuning": {
                "timestamp": last_tuning.timestamp,
                "old_threshold": last_tuning.old_threshold,
                "new_threshold": last_tuning.new_threshold,
                "improvement": last_tuning.accuracy_improvement
            } if last_tuning else None,
            "convergence": self.get_convergence_status()
        }
