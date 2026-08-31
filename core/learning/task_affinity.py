"""Task Affinity Learning (Phase 3, Week 13).

Learns which tasks operator excels at - per-task-type performance tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from statistics import mean, stdev


@dataclass(frozen=True)
class TaskAffinity:
    """Operator's affinity/strength in a task type."""

    task_type: str
    success_rate: float  # 0-1, proportion of successful tasks
    avg_latency_ms: int
    avg_quality_score: float  # 0-1
    confidence: float  # 0-1, how confident in this measurement
    sample_count: int  # How many tasks observed


class TaskAffinityLearner:
    """Learns operator task affinities from execution history."""

    def __init__(self, operator_id: str, min_samples: int = 10):
        self.operator_id = operator_id
        self.min_samples = min_samples

        # Per-task-type tracking
        self.task_outcomes: Dict[str, list] = {}  # task_type → [outcomes]
        self.task_latencies: Dict[str, list] = {}  # task_type → [latencies]
        self.task_qualities: Dict[str, list] = {}  # task_type → [qualities]

    def record_task(
        self,
        task_type: str,
        success: bool,
        latency_ms: int,
        quality_score: float,
    ) -> None:
        """Record a task outcome."""
        if task_type not in self.task_outcomes:
            self.task_outcomes[task_type] = []
            self.task_latencies[task_type] = []
            self.task_qualities[task_type] = []

        self.task_outcomes[task_type].append(success)
        self.task_latencies[task_type].append(latency_ms)
        self.task_qualities[task_type].append(quality_score)

    def get_affinity(self, task_type: str) -> Optional[TaskAffinity]:
        """Get affinity for task type."""
        if task_type not in self.task_outcomes:
            return None

        outcomes = self.task_outcomes[task_type]
        if len(outcomes) == 0:
            return None

        # Compute metrics
        success_rate = sum(outcomes) / len(outcomes)
        avg_latency = int(mean(self.task_latencies[task_type]))
        avg_quality = mean(self.task_qualities[task_type])

        # Compute confidence (based on sample size and consistency)
        if len(outcomes) < self.min_samples:
            confidence = 0.3 + 0.4 * (len(outcomes) / self.min_samples)
        else:
            # Confidence = low if high variance, high if consistent
            quality_std = stdev(self.task_qualities[task_type]) if len(self.task_qualities[task_type]) > 1 else 0
            consistency = max(0.0, 1.0 - (quality_std * 2))  # High std = low consistency
            confidence = 0.7 + 0.3 * consistency

        return TaskAffinity(
            task_type=task_type,
            success_rate=success_rate,
            avg_latency_ms=avg_latency,
            avg_quality_score=avg_quality,
            confidence=min(1.0, confidence),
            sample_count=len(outcomes),
        )

    def get_all_affinities(self) -> Dict[str, TaskAffinity]:
        """Get all affinities."""
        affinities = {}
        for task_type in self.task_outcomes:
            affinity = self.get_affinity(task_type)
            if affinity:
                affinities[task_type] = affinity
        return affinities

    def get_strong_tasks(self, threshold: float = 0.75) -> list[str]:
        """Get task types where operator is strong (>threshold success rate)."""
        strong = []
        for task_type, affinity in self.get_all_affinities().items():
            if affinity.confidence >= 0.7 and affinity.success_rate >= threshold:
                strong.append(task_type)
        return strong

    def get_weak_tasks(self, threshold: float = 0.60) -> list[str]:
        """Get task types where operator struggles (<threshold success rate)."""
        weak = []
        for task_type, affinity in self.get_all_affinities().items():
            if affinity.confidence >= 0.7 and affinity.success_rate <= threshold:
                weak.append(task_type)
        return weak

    def is_converged(self, task_type: str) -> bool:
        """Check if affinity for task type has converged."""
        affinity = self.get_affinity(task_type)
        if not affinity:
            return False
        return (
            affinity.sample_count >= self.min_samples and
            affinity.confidence >= 0.7
        )


class TaskAffinityRegistry:
    """Registry of task affinities per operator."""

    def __init__(self):
        self.learners: Dict[str, TaskAffinityLearner] = {}

    def register_operator(self, operator_id: str) -> TaskAffinityLearner:
        """Register operator for affinity learning."""
        learner = TaskAffinityLearner(operator_id)
        self.learners[operator_id] = learner
        return learner

    def record_task(
        self,
        operator_id: str,
        task_type: str,
        success: bool,
        latency_ms: int,
        quality_score: float,
    ) -> None:
        """Record task for operator."""
        if operator_id not in self.learners:
            self.register_operator(operator_id)
        self.learners[operator_id].record_task(task_type, success, latency_ms, quality_score)

    def get_affinity(self, operator_id: str, task_type: str) -> Optional[TaskAffinity]:
        """Get operator's affinity for task type."""
        if operator_id not in self.learners:
            return None
        return self.learners[operator_id].get_affinity(task_type)

    def get_personalized_routing(
        self,
        operator_id: str,
        task_type: str,
    ) -> Optional[str]:
        """Get routing suggestion based on operator affinity.

        Returns recommended engine:
        - Strong task → Haiku (cheap, fast)
        - Medium task → Hermes (balanced)
        - Weak task → Claude (high quality)
        """
        affinity = self.get_affinity(operator_id, task_type)
        if not affinity or affinity.confidence < 0.7:
            return None  # Not enough data

        if affinity.success_rate >= 0.75:
            return "haiku"  # Operator is strong, use cheap
        elif affinity.success_rate >= 0.60:
            return "hermes"  # Medium, balanced approach
        else:
            return "claude"  # Weak, use premium quality
