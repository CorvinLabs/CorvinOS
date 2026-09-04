"""
Phase 7: L5 Feedback Integration — Closed-Loop Learning

Operator feedback → Learning optimizer → Parameter tuning

Components:
- FeedbackCollector: Structured feedback after each decision
- FeedbackProcessor: Aggregate + analyze feedback
- LearningOptimizer: Adjust L5 parameters based on feedback

ADR-0590: L5 Operator Feedback Loop
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
import statistics


@dataclass
class OperatorFeedback:
    """Single operator feedback."""
    approval_id: str
    decision_was_correct: bool  # True/False/Unsure
    should_have_been_auto_approved: bool  # True/False/Unsure
    feedback_type: str  # "positive", "negative", "neutral"
    notes: str
    timestamp: str = ""
    operator_id: str = "_default"


@dataclass
class FeedbackAggregation:
    """Aggregated feedback statistics."""
    total_feedback: int = 0
    correct_rate: float = 0.0  # % of decisions operator agreed with
    should_auto_approve_rate: float = 0.0  # % that should have been auto-approved
    average_sentiment: float = 0.0  # 1.0 (very positive) to -1.0 (very negative)
    operator_accuracy: float = 0.0  # Operator decision accuracy vs. outcome


class FeedbackCollector:
    """Collects operator feedback post-decision."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self._lock = RLock()
        self._feedback_records: List[OperatorFeedback] = []

    def collect_feedback(
        self,
        approval_id: str,
        decision_was_correct: bool,
        should_auto_approved: bool,
        feedback_type: str,
        notes: str,
        operator_id: str = "_default",
    ) -> OperatorFeedback:
        """Collect feedback on an operator decision."""
        with self._lock:
            feedback = OperatorFeedback(
                approval_id=approval_id,
                decision_was_correct=decision_was_correct,
                should_have_been_auto_approved=should_auto_approved,
                feedback_type=feedback_type,
                notes=notes,
                timestamp=datetime.utcnow().isoformat(),
                operator_id=operator_id,
            )
            self._feedback_records.append(feedback)
            return feedback

    def get_recent_feedback(self, limit: int = 100) -> List[OperatorFeedback]:
        """Get recent feedback records."""
        with self._lock:
            return self._feedback_records[-limit:]


class FeedbackProcessor:
    """Process operator feedback into learning signals."""

    def __init__(self, feedback_collector: FeedbackCollector):
        self.collector = feedback_collector
        self._lock = RLock()

    def aggregate_feedback(self) -> FeedbackAggregation:
        """Aggregate all feedback into statistics."""
        with self._lock:
            feedback_list = self.collector.get_recent_feedback(limit=1000)

            if not feedback_list:
                return FeedbackAggregation()

            correct_count = sum(1 for f in feedback_list if f.decision_was_correct)
            auto_approve_count = sum(
                1 for f in feedback_list if f.should_have_been_auto_approved
            )

            # Calculate sentiment (-1 to +1)
            sentiment_scores = {
                "positive": 1.0,
                "neutral": 0.0,
                "negative": -1.0,
            }
            sentiments = [
                sentiment_scores.get(f.feedback_type, 0.0) for f in feedback_list
            ]

            return FeedbackAggregation(
                total_feedback=len(feedback_list),
                correct_rate=(correct_count / len(feedback_list)) * 100,
                should_auto_approve_rate=(auto_approve_count / len(feedback_list)) * 100,
                average_sentiment=statistics.mean(sentiments) if sentiments else 0.0,
                operator_accuracy=(correct_count / len(feedback_list)) * 100,
            )

    def operator_performance_by_id(self) -> Dict[str, Dict]:
        """Calculate performance metrics per operator."""
        with self._lock:
            feedback_list = self.collector.get_recent_feedback(limit=1000)
            by_operator: Dict[str, List[OperatorFeedback]] = {}

            for f in feedback_list:
                if f.operator_id not in by_operator:
                    by_operator[f.operator_id] = []
                by_operator[f.operator_id].append(f)

            results = {}
            for op_id, feedback_records in by_operator.items():
                correct = sum(1 for f in feedback_records if f.decision_was_correct)
                results[op_id] = {
                    "total_decisions": len(feedback_records),
                    "correct_rate": (correct / len(feedback_records)) * 100,
                    "accuracy_trend": "improving" if correct > len(feedback_records) * 0.8 else "needs_work",
                }

            return results


class LearningOptimizer:
    """Adjust L5 parameters based on operator feedback."""

    def __init__(self, processor: FeedbackProcessor):
        self.processor = processor
        self._lock = RLock()

    def optimize_smooth_threshold(self, current_threshold: float) -> float:
        """Adjust Smooth gate confidence threshold based on feedback."""
        with self._lock:
            agg = self.processor.aggregate_feedback()

            if agg.total_feedback < 50:
                return current_threshold  # Need more data

            # If auto-approve rate too low, lower threshold
            if agg.should_auto_approve_rate > 70 and current_threshold > 0.90:
                return current_threshold - 0.02

            # If auto-approve rate too high, raise threshold
            if agg.should_auto_approve_rate < 40 and current_threshold < 0.98:
                return current_threshold + 0.02

            return current_threshold

    def optimize_operator_workload(self) -> Dict:
        """Recommend operator workload adjustments."""
        with self._lock:
            agg = self.processor.aggregate_feedback()
            perf_by_op = self.processor.operator_performance_by_id()

            recommendations = {
                "smooth_threshold_change": 0.0,  # ±0.01-0.03
                "operator_count_change": 0,  # +/-N
                "priority_routing": "default",
            }

            # If average correctness too low, maybe need better training
            if agg.correct_rate < 75:
                recommendations["operator_count_change"] = -1  # Remove underperformer
                recommendations["priority_routing"] = "experienced_only"

            # If queue backing up, suggest more operators
            if agg.total_feedback > 200:  # Proxy for high load
                recommendations["operator_count_change"] = 1

            return recommendations

    def get_optimization_signals(self) -> Dict:
        """Generate optimization signals for learning."""
        with self._lock:
            agg = self.processor.aggregate_feedback()

            return {
                "feedback_count": agg.total_feedback,
                "operator_accuracy": agg.operator_accuracy,
                "sentiment": agg.average_sentiment,
                "auto_approve_rate": agg.should_auto_approve_rate,
                "needs_optimization": agg.operator_accuracy < 80 or agg.should_auto_approve_rate < 50,
            }
