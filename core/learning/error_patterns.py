"""Error Pattern Learning (Phase 1, Week 3).

Identifies common error patterns from task outcomes and predicts failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from collections import defaultdict, Counter
import math


@dataclass(frozen=True)
class ErrorPattern:
    """A learned error pattern."""

    pattern_id: str
    description: str
    task_types: list[str]  # Task types affected
    error_types: list[str]  # Error types seen
    frequency: int  # Number of times observed
    severity: str  # "low", "medium", "high"
    confidence: float  # How confident are we in this pattern (0.0-1.0)
    last_seen: str  # ISO 8601 timestamp


@dataclass
class ErrorObservation:
    """Single error observation for learning."""

    task_id: str
    task_type: str
    error_type: str
    error_message: Optional[str]
    operator_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict = field(default_factory=dict)


class PatternDetector:
    """Detects patterns from error observations."""

    def __init__(self, min_observations: int = 3):
        """Initialize detector.

        Args:
            min_observations: Minimum observations to form a pattern
        """
        self.min_observations = min_observations
        self.observations: list[ErrorObservation] = []
        self.patterns: dict[str, ErrorPattern] = {}

    def add_observation(self, obs: ErrorObservation) -> None:
        """Add error observation for analysis."""
        self.observations.append(obs)
        self._update_patterns()

    def _update_patterns(self) -> None:
        """Update patterns based on current observations."""
        if len(self.observations) < self.min_observations:
            return

        # Group by task_type + error_type combination
        pattern_groups = defaultdict(list)

        for obs in self.observations:
            key = (obs.task_type, obs.error_type)
            pattern_groups[key].append(obs)

        # Create/update patterns
        for (task_type, error_type), group in pattern_groups.items():
            if len(group) >= self.min_observations:
                pattern_id = f"pattern-{task_type}-{error_type}"

                # Gather all task types in this group
                task_types = list(set(obs.task_type for obs in group))
                error_types = list(set(obs.error_type for obs in group))

                # Calculate severity (frequency-based)
                frequency = len(group)
                if frequency >= 10:
                    severity = "high"
                elif frequency >= 5:
                    severity = "medium"
                else:
                    severity = "low"

                # Confidence based on consistency
                confidence = min(1.0, frequency / 20.0)

                # Get most recent
                last_seen = max(obs.timestamp for obs in group)

                pattern = ErrorPattern(
                    pattern_id=pattern_id,
                    description=f"{error_type} in {task_type} tasks",
                    task_types=task_types,
                    error_types=error_types,
                    frequency=frequency,
                    severity=severity,
                    confidence=confidence,
                    last_seen=last_seen,
                )

                self.patterns[pattern_id] = pattern

    def get_patterns(self, task_type: Optional[str] = None) -> list[ErrorPattern]:
        """Get patterns, optionally filtered by task type."""
        patterns = list(self.patterns.values())

        if task_type:
            patterns = [p for p in patterns if task_type in p.task_types]

        return sorted(patterns, key=lambda p: p.frequency, reverse=True)

    def get_pattern(self, pattern_id: str) -> Optional[ErrorPattern]:
        """Get specific pattern."""
        return self.patterns.get(pattern_id)


class ErrorPredictor:
    """Predicts task failure probability."""

    def __init__(self):
        """Initialize predictor."""
        self.detector = PatternDetector()
        self.operator_error_counts: dict[str, int] = defaultdict(int)
        self.operator_task_counts: dict[str, int] = defaultdict(int)

    def add_observation(self, obs: ErrorObservation) -> None:
        """Add observation for learning."""
        self.detector.add_observation(obs)
        self.operator_error_counts[obs.operator_id] += 1
        self.operator_task_counts[obs.operator_id] += 1

    def add_success(self, operator_id: str) -> None:
        """Record successful task (for error rate calculation)."""
        self.operator_task_counts[operator_id] += 1

    def predict_failure(
        self,
        task_type: str,
        operator_id: str,
        metadata: Optional[dict] = None,
    ) -> float:
        """Predict probability of failure (0.0-1.0).

        Algorithm:
        1. Get error patterns for task_type
        2. Estimate base failure rate from patterns
        3. Adjust by operator's historical error rate
        4. Return probability
        """
        # Base rate from patterns
        patterns = self.detector.get_patterns(task_type)

        if not patterns:
            # No patterns for this task type, use operator's historical rate
            if self.operator_task_counts[operator_id] == 0:
                return 0.1  # Default low probability
            return self.operator_error_counts[operator_id] / self.operator_task_counts[operator_id]

        # Weight patterns by confidence and frequency
        weighted_failures = sum(p.confidence * p.frequency for p in patterns)
        total_weight = sum(p.frequency for p in patterns)

        if total_weight == 0:
            base_rate = 0.1
        else:
            base_rate = weighted_failures / total_weight

        # Adjust by operator's error rate
        if self.operator_task_counts[operator_id] > 0:
            operator_error_rate = (
                self.operator_error_counts[operator_id] / self.operator_task_counts[operator_id]
            )
            # Blend: 70% pattern base rate, 30% operator history
            combined_rate = 0.7 * base_rate + 0.3 * operator_error_rate
        else:
            combined_rate = base_rate

        return min(1.0, combined_rate)

    def get_precision_and_recall(self) -> tuple[float, float]:
        """Estimate precision and recall (for validation).

        Returns (precision, recall) - accuracy depends on data quality.
        For now, return estimates based on pattern confidence.
        """
        if not self.detector.patterns:
            return (0.0, 0.0)

        # Precision: weight by confidence
        avg_confidence = sum(p.confidence for p in self.detector.patterns.values()) / len(
            self.detector.patterns
        )

        # Recall: proportion of high-severity patterns
        high_severity = sum(
            1 for p in self.detector.patterns.values() if p.severity == "high"
        )
        total = len(self.detector.patterns)
        recall = high_severity / total if total > 0 else 0.0

        return (avg_confidence, recall)


class RootCauseAnalyzer:
    """Analyzes root causes of errors."""

    def __init__(self):
        self.observations: list[ErrorObservation] = []

    def add_observation(self, obs: ErrorObservation) -> None:
        """Record observation."""
        self.observations.append(obs)

    def analyze_task_type_failures(self, task_type: str) -> dict:
        """Analyze which task types fail most often."""
        task_failures = [o for o in self.observations if o.task_type == task_type]

        if not task_failures:
            return {
                "task_type": task_type,
                "total_failures": 0,
                "error_breakdown": {},
            }

        error_counter = Counter(o.error_type for o in task_failures)

        return {
            "task_type": task_type,
            "total_failures": len(task_failures),
            "error_breakdown": dict(error_counter),
            "most_common_error": error_counter.most_common(1)[0][0],
        }

    def analyze_operator_failures(self, operator_id: str) -> dict:
        """Analyze operator's failure patterns."""
        operator_failures = [o for o in self.observations if o.operator_id == operator_id]

        if not operator_failures:
            return {
                "operator_id": operator_id,
                "total_failures": 0,
                "task_type_breakdown": {},
            }

        task_type_counter = Counter(o.task_type for o in operator_failures)

        return {
            "operator_id": operator_id,
            "total_failures": len(operator_failures),
            "task_type_breakdown": dict(task_type_counter),
            "most_problematic_task_type": task_type_counter.most_common(1)[0][0],
        }

    def get_correlation_matrix(self) -> dict[str, list[float]]:
        """Get task_type × error_type correlation matrix.

        Shows which error types are most common for each task type.
        """
        task_types = set(o.task_type for o in self.observations)
        error_types = set(o.error_type for o in self.observations)

        # Create matrix
        matrix = {}
        for task_type in task_types:
            task_obs = [o for o in self.observations if o.task_type == task_type]
            total = len(task_obs)

            error_counts = {}
            for error_type in error_types:
                count = sum(1 for o in task_obs if o.error_type == error_type)
                error_counts[error_type] = count / total if total > 0 else 0.0

            matrix[task_type] = error_counts

        return matrix
