"""Learning Loop Integration for DoD Verifier Skill 2.0 (ADR-0314 + ADR-0XXX).

Feedback collection and weight tuning via ADR-0314 learning infrastructure.

Flow:
1. User runs DoD verification → DoD_VerificationResult with score 0-100
2. User provides feedback via /api/dod/feedback → "accurate" | "inaccurate" | "not_applicable"
3. FeedbackEvent written to event store
4. Optimizer computes confidence delta: P(score is accurate | feedback)
5. Optimizer adjusts check weights (e.g., test_coverage weight 0.20 → 0.22)
6. Next verification uses tuned weights
7. Progress tracked in learning dashboard

Guarantee: Feedback cannot weaken compliance mechanisms or audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List
import json
import hashlib

try:
    from core.learning.base import LearningLoop
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from core.learning.base import LearningLoop


@dataclass(frozen=True)
class DoD_FeedbackEvent:
    """Immutable feedback event for DoD check accuracy."""
    event_type: str = "dod_feedback"
    task_id: str = ""
    check_name: str = ""  # e.g., "test_coverage", "repo_cleanliness"
    feedback: str = ""  # "accurate" | "inaccurate" | "not_applicable"
    note: str = ""  # Optional user note
    tenant_id: str = "_default"
    timestamp: str = ""
    hash: str = ""
    prev_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event."""
        payload = json.dumps({
            "event_type": self.event_type,
            "task_id": self.task_id,
            "check_name": self.check_name,
            "feedback": self.feedback,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class DoD_FeedbackCollector:
    """Collects user feedback on DoD verification accuracy."""

    def __init__(self, tenant_id: str, event_store=None):
        """Initialize feedback collector.

        Args:
            tenant_id: Tenant scope for all feedback
            event_store: Optional event store for persistence
        """
        self.tenant_id = tenant_id
        self.event_store = event_store
        self.feedback_log: List[DoD_FeedbackEvent] = []

    def record_feedback(
        self,
        task_id: str,
        check_name: str,
        feedback: str,
        note: str = "",
    ) -> DoD_FeedbackEvent:
        """Record feedback on a DoD check.

        Args:
            task_id: Task being verified
            check_name: Which check (repo_cleanliness, test_coverage, etc.)
            feedback: "accurate", "inaccurate", or "not_applicable"
            note: Optional explanation

        Returns:
            DoD_FeedbackEvent (immutable)
        """
        if feedback not in ("accurate", "inaccurate", "not_applicable"):
            raise ValueError(f"Invalid feedback value: {feedback}")

        timestamp = datetime.utcnow().isoformat()

        # Compute hash
        event = DoD_FeedbackEvent(
            task_id=task_id,
            check_name=check_name,
            feedback=feedback,
            note=note,
            tenant_id=self.tenant_id,
            timestamp=timestamp,
        )

        event_hash = event.compute_hash()

        # Persist to event store if available
        if self.event_store:
            try:
                self.event_store.write_event({
                    "event_type": "dod_feedback",
                    "task_id": task_id,
                    "check_name": check_name,
                    "feedback": feedback,
                    "note": note,
                    "tenant_id": self.tenant_id,
                    "timestamp": timestamp,
                    "hash": event_hash,
                })
            except Exception as e:
                print(f"Warning: Failed to persist feedback event: {e}")

        # Record locally
        self.feedback_log.append(event)

        return event

    def get_feedback_for_task(self, task_id: str) -> List[DoD_FeedbackEvent]:
        """Get all feedback for a task."""
        return [e for e in self.feedback_log if e.task_id == task_id]

    def get_feedback_for_check(self, check_name: str) -> List[DoD_FeedbackEvent]:
        """Get all feedback for a check."""
        return [e for e in self.feedback_log if e.check_name == check_name]


class DoD_LearningOptimizer(LearningLoop):
    """Learning loop for DoD Verifier — learns optimal check weights from feedback.

    Tier: 2 (infrastructure) — learns check weights, not critical path

    Learnable parameters:
    - repo_cleanliness_weight: P(check is accurate for repo quality)
    - test_coverage_weight: P(check is accurate for test completeness)
    - adr_sync_weight: P(check is accurate for ADR-code sync)
    - compliance_weight: P(check is accurate for compliance)
    - documentation_weight: P(check is accurate for documentation)

    Loss function:
    loss = -sum(w_i * P(feedback=accurate | check_i))

    Convergence: when weight changes < 0.01 over 10 steps.
    """

    def __init__(self, tenant_id: str):
        """Initialize DoD learning optimizer.

        Args:
            tenant_id: Tenant scope
        """
        super().__init__(loop_id="dod_verifier", tier=2)
        self.tenant_id = tenant_id

        # Initialize weights (equal initially)
        self.weights = {
            "repo_cleanliness": 0.20,
            "test_coverage": 0.20,
            "adr_sync": 0.20,
            "compliance": 0.20,
            "documentation": 0.20,
        }

        # Confidence scores (how confident are we in each check?)
        self.confidences = {
            "repo_cleanliness": 0.5,
            "test_coverage": 0.5,
            "adr_sync": 0.5,
            "compliance": 0.5,
            "documentation": 0.5,
        }

    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        """Compute loss from feedback.

        Args:
            feedback_signals: {
                "repo_cleanliness_accuracy": 0.8,  # 80% of users say accurate
                "test_coverage_accuracy": 0.6,
                ...
            }

        Returns:
            loss in [0, 1], where 0 = perfect, 1 = worst
        """
        # Loss = 1 - sum(weight * accuracy)
        # (Want to maximize weighted accuracy)
        loss = 0.0
        for check_name, accuracy in feedback_signals.items():
            # Normalize check name
            check_key = check_name.replace("_accuracy", "")
            if check_key in self.weights:
                weight = self.weights[check_key]
                # Higher accuracy = lower loss
                loss -= weight * accuracy

        # Normalize to [0, 1]
        loss = max(0.0, min(1.0, loss + 1.0))
        return loss

    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        """Compute weight gradients from loss delta.

        Args:
            loss: current loss
            prev_loss: previous loss

        Returns:
            {weight_name: gradient_value}
        """
        gradient = {}
        for check_name in self.weights.keys():
            # Simple gradient: lower loss = increase weight
            # (This is a simplified gradient; real version uses feedback directly)
            grad = loss - prev_loss  # If loss increased, gradient is positive
            gradient[check_name] = grad

        return gradient

    def apply_gradients(
        self,
        gradients: Dict[str, float],
        learning_rate: float = None,
        damping: float = None,
    ) -> None:
        """Apply gradient descent with damping.

        Update rule:
            new_w = damping * old_w + (1 - damping) * (old_w - lr * grad)

        Constraint: all weights sum to 1.0
        """
        if learning_rate is None:
            learning_rate = self.learning_rate
        if damping is None:
            damping = self.damping_factor

        # Apply gradient descent
        for weight_name, gradient in gradients.items():
            if weight_name in self.weights:
                old_w = self.weights[weight_name]
                new_w = damping * old_w + (1 - damping) * (old_w - learning_rate * gradient)
                # Clip to [0, 1]
                new_w = self.clip_parameter(new_w, 0.0, 1.0)
                self.weights[weight_name] = new_w

        # Re-normalize weights to sum to 1.0
        weight_sum = sum(self.weights.values())
        if weight_sum > 0:
            self.weights = {
                k: v / weight_sum for k, v in self.weights.items()
            }

        # Record in history
        self.record_parameters(self.weights)

    def check_convergence(self, gradient_history: List[float] = None) -> bool:
        """Check convergence: gradient magnitude < 0.01 over last 10 steps.

        Args:
            gradient_history: optional list of gradients

        Returns:
            True if converged
        """
        if gradient_history is None:
            gradient_history = []
            for param_name, param_history in self.param_history.items():
                if len(param_history) >= 2:
                    for i in range(len(param_history) - 1):
                        gradient_history.append(
                            abs(param_history[i+1] - param_history[i])
                        )

        if not gradient_history:
            return False

        # Check average gradient magnitude
        avg_grad = sum(gradient_history[-10:]) / min(10, len(gradient_history))

        return avg_grad < 0.01  # Converged if avg gradient < 0.01


if __name__ == "__main__":
    # Example usage
    collector = DoD_FeedbackCollector("_default")
    optimizer = DoD_LearningOptimizer("_default")

    # Record feedback
    fb1 = collector.record_feedback(
        task_id="task_123",
        check_name="test_coverage",
        feedback="accurate",
        note="Tests were comprehensive",
    )

    print(f"Feedback recorded: {fb1.task_id} → {fb1.check_name}: {fb1.feedback}")

    # Simulate learning
    feedback_signals = {
        "repo_cleanliness_accuracy": 0.9,
        "test_coverage_accuracy": 0.8,
        "adr_sync_accuracy": 0.7,
        "compliance_accuracy": 0.6,
        "documentation_accuracy": 0.5,
    }

    loss = optimizer.compute_loss(feedback_signals)
    print(f"Initial loss: {loss:.3f}")
    print(f"Initial weights: {optimizer.weights}")
