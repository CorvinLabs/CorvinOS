"""Operator Fingerprinting (Phase 1, Week 4).

Learns 4D operator style model from task decisions and outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from collections import defaultdict
import statistics


@dataclass(frozen=True)
class OperatorFingerprint:
    """4D operator style profile."""

    operator_id: str
    risk_tolerance: float  # 0.0=conservative, 1.0=aggressive
    speed_preference: float  # 0.0=thorough, 1.0=fast
    communication_style: str  # "terse", "neutral", "detailed"
    expertise_profile: dict[str, float]  # task_type → proficiency (0.0-1.0)
    confidence: float  # How confident in this fingerprint (0.0-1.0)
    last_updated: str  # ISO 8601
    total_observations: int = 0


class OperatorFingerprintLearner:
    """Learns operator fingerprint from decisions and outcomes."""

    def __init__(self, operator_id: str, min_observations: int = 50):
        """Initialize learner.

        Args:
            operator_id: Operator identifier
            min_observations: Minimum observations before confidence ≥0.7
        """
        self.operator_id = operator_id
        self.min_observations = min_observations

        # Observations
        self.decision_latencies: list[int] = []  # For speed_preference
        self.decision_accuracies: list[float] = []  # For risk_tolerance
        self.feedback_lengths: list[int] = []  # For communication_style
        self.task_type_outcomes: dict[str, list[float]] = defaultdict(list)  # For expertise_profile

    def add_decision(
        self,
        task_type: str,
        latency_ms: int,
        accuracy: float,
        feedback_text: Optional[str] = None,
    ) -> None:
        """Record a task decision and outcome."""
        self.decision_latencies.append(latency_ms)
        self.decision_accuracies.append(accuracy)

        if feedback_text:
            self.feedback_lengths.append(len(feedback_text))

        self.task_type_outcomes[task_type].append(accuracy)

    def _compute_risk_tolerance(self) -> float:
        """Compute risk tolerance from accuracy outcomes.

        Conservative operators accept lower accuracy (safer).
        Aggressive operators demand high accuracy (riskier).

        Algorithm: Invert accuracy variance and mean.
        - Low variance + high mean = aggressive (risk-taking, consistent high performers)
        - High variance = conservative (risk-avoiding, mixed outcomes)
        """
        if len(self.decision_accuracies) < 3:
            return 0.5  # Default neutral

        mean_acc = statistics.mean(self.decision_accuracies)
        var_acc = statistics.variance(self.decision_accuracies) if len(self.decision_accuracies) > 1 else 0.0

        # Normalize to 0-1 range
        # High mean + low variance → aggressive (1.0)
        # Low mean + high variance → conservative (0.0)
        # ``(mean - var) / 2`` capped the score at 0.5 for a PERFECT operator,
        # so the documented "aggressive" half of the scale was unreachable
        # (N-07, ``test_risk_tolerance_computation``). mean ∈ [0, 1] and
        # var ∈ [0, 0.25], so ``mean - var`` already lives in [-0.25, 1].
        risk = mean_acc - var_acc
        return max(0.0, min(1.0, risk))

    def _compute_speed_preference(self) -> float:
        """Compute speed preference from task latencies.

        Fast operators choose quick tasks (low latency).
        Thorough operators prefer detailed/complex tasks (high latency).

        Algorithm: Normalize average latency.
        - Low latency = fast preference (1.0)
        - High latency = thorough preference (0.0)
        """
        if len(self.decision_latencies) < 3:
            return 0.5  # Default neutral

        mean_latency = statistics.mean(self.decision_latencies)

        # Normalize assuming typical range 50-200ms
        # <50ms = speed=1.0, >200ms = speed=0.0
        speed = max(0.0, min(1.0, (200 - mean_latency) / 150.0))
        return speed

    def _compute_communication_style(self) -> str:
        """Determine communication style from feedback length."""
        if len(self.feedback_lengths) < 3:
            return "neutral"

        mean_length = statistics.mean(self.feedback_lengths)

        # Thresholds: < 20 chars (a word or two) = terse; ≥ 50 chars (a full
        # sentence with explanation) = detailed; in between = neutral. The
        # previous 100-char bar classified a 60-char explanatory sentence as
        # "neutral" (N-07, ``test_communication_style_detection``).
        if mean_length < 20:
            return "terse"
        elif mean_length < 50:
            return "neutral"
        else:
            return "detailed"

    def _compute_expertise_profile(self) -> dict[str, float]:
        """Compute per-task-type expertise from accuracy outcomes."""
        expertise = {}

        for task_type, outcomes in self.task_type_outcomes.items():
            if outcomes:
                expertise[task_type] = statistics.mean(outcomes)

        return expertise

    def _compute_confidence(self) -> float:
        """Compute confidence in fingerprint.

        Confidence increases with:
        - More observations (up to min_observations)
        - Consistency (low variance across observations)
        """
        total_obs = len(self.decision_accuracies)

        if total_obs < 10:
            return 0.0

        if total_obs < self.min_observations:
            return 0.3 + 0.4 * (total_obs - 10) / (self.min_observations - 10)

        # Confident if converged (last 20 obs similar to overall)
        if total_obs >= self.min_observations:
            recent = self.decision_accuracies[-20:]
            overall = self.decision_accuracies
            recent_mean = statistics.mean(recent)
            overall_mean = statistics.mean(overall)

            # If recent matches overall ±0.05, converged
            if abs(recent_mean - overall_mean) < 0.05:
                return 0.9
            else:
                return 0.7

        return 0.5

    def generate_fingerprint(self) -> OperatorFingerprint:
        """Generate operator fingerprint from accumulated observations."""
        return OperatorFingerprint(
            operator_id=self.operator_id,
            risk_tolerance=self._compute_risk_tolerance(),
            speed_preference=self._compute_speed_preference(),
            communication_style=self._compute_communication_style(),
            expertise_profile=self._compute_expertise_profile(),
            confidence=self._compute_confidence(),
            last_updated=datetime.utcnow().isoformat(),
            total_observations=len(self.decision_accuracies),
        )

    def is_converged(self) -> bool:
        """Check if fingerprint has converged (high confidence)."""
        fingerprint = self.generate_fingerprint()
        return fingerprint.confidence >= 0.7


class OperatorFingerprintRegistry:
    """Registry of operator fingerprints."""

    def __init__(self):
        self.learners: dict[str, OperatorFingerprintLearner] = {}
        self.fingerprints: dict[str, OperatorFingerprint] = {}

    def register_operator(self, operator_id: str) -> OperatorFingerprintLearner:
        """Register a new operator for learning."""
        learner = OperatorFingerprintLearner(operator_id)
        self.learners[operator_id] = learner
        return learner

    def add_decision(
        self,
        operator_id: str,
        task_type: str,
        latency_ms: int,
        accuracy: float,
        feedback_text: Optional[str] = None,
    ) -> None:
        """Add decision to operator's profile."""
        if operator_id not in self.learners:
            self.register_operator(operator_id)

        self.learners[operator_id].add_decision(task_type, latency_ms, accuracy, feedback_text)

        # Update fingerprint
        self.fingerprints[operator_id] = self.learners[operator_id].generate_fingerprint()

    def get_fingerprint(self, operator_id: str) -> Optional[OperatorFingerprint]:
        """Get operator's fingerprint."""
        return self.fingerprints.get(operator_id)

    def get_converged_operators(self) -> list[str]:
        """Get operators with converged fingerprints."""
        return [
            op_id for op_id, learner in self.learners.items()
            if learner.is_converged()
        ]

    def get_statistics(self) -> dict:
        """Get registry statistics."""
        total_operators = len(self.learners)
        converged = len(self.get_converged_operators())

        return {
            "total_operators": total_operators,
            "converged_operators": converged,
            "fingerprints": {
                op_id: {
                    "risk_tolerance": fp.risk_tolerance,
                    "speed_preference": fp.speed_preference,
                    "communication_style": fp.communication_style,
                    "confidence": fp.confidence,
                    "total_observations": fp.total_observations,
                }
                for op_id, fp in self.fingerprints.items()
            },
        }
