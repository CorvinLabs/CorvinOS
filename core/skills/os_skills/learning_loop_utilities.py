"""Shared learning loop utilities for Phase 3 (Streams 1+3).

Provides baseline measurement, accuracy calculation, and feedback simulation
for both Workflow Optimizer (Stream 1) and Flow Guard (Stream 3).

**Compliance:**
- GDPR Art. 30/32: All operations audit-logged
- ADR-0314: No PII in measurements or simulations
- Fail-closed: invalid inputs rejected immediately
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Tuple, Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class TaskComplexity(str, Enum):
    """Task complexity tiers (Stream 1 / Workflow Optimizer)."""
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class DataClass(str, Enum):
    """Data classification tiers (Stream 3 / Flow Guard)."""
    PUBLIC = "public"
    INTERNAL = "internal"
    FINANCIAL = "financial"
    HEALTH = "health"
    PII = "pii"


@dataclass(frozen=True)
class SyntheticTask:
    """Synthetic task for Stream 1 learning loop testing."""
    task_id: str
    complexity: TaskComplexity
    features: Dict[str, float]  # Feature vector for classification
    correct_model: str  # Ground truth: haiku/sonnet/opus
    tenant_id: str

    @staticmethod
    def generate(
        count: int,
        complexity_dist: Optional[Dict[TaskComplexity, float]] = None,
        tenant_id: str = "_default",
    ) -> List[SyntheticTask]:
        """Generate synthetic tasks with realistic complexity distribution.

        Args:
            count: Number of tasks to generate
            complexity_dist: Distribution of complexities (default: uniform)
            tenant_id: Tenant scope

        Returns:
            List of SyntheticTask with ground-truth model assignments
        """
        if complexity_dist is None:
            complexity_dist = {
                TaskComplexity.SIMPLE: 0.4,
                TaskComplexity.MEDIUM: 0.35,
                TaskComplexity.COMPLEX: 0.25,
            }

        tasks = []
        for i in range(count):
            # Sample complexity per distribution
            complexity = random.choices(
                list(complexity_dist.keys()),
                weights=list(complexity_dist.values()),
            )[0]

            # Generate feature vector (4–6 normalized features)
            features = {
                "avg_token_length": random.uniform(50, 500),
                "decision_branching": random.uniform(0, 1),
                "reasoning_depth": random.uniform(0, 1),
                "code_generation": random.random(),
                "multi_step": random.random(),
            }

            # Assign ground-truth model based on complexity
            # Simple: prefer haiku (85% correct with haiku, 55% with opus)
            # Medium: prefer sonnet (60% correct)
            # Complex: prefer opus (70% correct)
            if complexity == TaskComplexity.SIMPLE:
                correct_model = random.choices(
                    ["haiku", "sonnet", "opus"],
                    weights=[0.85, 0.10, 0.05],
                )[0]
            elif complexity == TaskComplexity.MEDIUM:
                correct_model = random.choices(
                    ["haiku", "sonnet", "opus"],
                    weights=[0.20, 0.60, 0.20],
                )[0]
            else:  # COMPLEX
                correct_model = random.choices(
                    ["haiku", "sonnet", "opus"],
                    weights=[0.05, 0.25, 0.70],
                )[0]

            tasks.append(
                SyntheticTask(
                    task_id=f"task_{i:04d}_{uuid4().hex[:8]}",
                    complexity=complexity,
                    features=features,
                    correct_model=correct_model,
                    tenant_id=tenant_id,
                )
            )

        return tasks


@dataclass(frozen=True)
class SyntheticDataFlow:
    """Synthetic data flow for Stream 3 learning loop testing."""
    flow_id: str
    data_class: DataClass
    engine: str  # claude-haiku, claude-sonnet, claude-opus
    destination: str  # console, webhook, file
    features: Dict[str, float]  # Feature vector for classification
    correct_decision: str  # Ground truth: allow/deny
    tenant_id: str

    @staticmethod
    def generate(
        count: int,
        class_dist: Optional[Dict[DataClass, float]] = None,
        tenant_id: str = "_default",
    ) -> List[SyntheticDataFlow]:
        """Generate synthetic data flows with realistic classification.

        Args:
            count: Number of flows to generate
            class_dist: Distribution of data classes (default: realistic)
            tenant_id: Tenant scope

        Returns:
            List of SyntheticDataFlow with ground-truth decisions
        """
        if class_dist is None:
            class_dist = {
                DataClass.PUBLIC: 0.40,
                DataClass.INTERNAL: 0.30,
                DataClass.FINANCIAL: 0.15,
                DataClass.HEALTH: 0.10,
                DataClass.PII: 0.05,
            }

        engines = ["claude-haiku", "claude-sonnet", "claude-opus"]
        destinations = ["console", "webhook", "file"]

        flows = []
        for i in range(count):
            # Sample data class per distribution
            data_class = random.choices(
                list(class_dist.keys()),
                weights=list(class_dist.values()),
            )[0]

            # Sample engine and destination
            engine = random.choice(engines)
            destination = random.choice(destinations)

            # Generate feature vector (entropy, patterns, schema signals)
            features = {
                "entropy": random.uniform(0, 8),
                "schema_match_score": random.uniform(0, 1),
                "regex_detections": random.randint(0, 10),
                "size_bytes": random.uniform(100, 1_000_000),
            }

            # Assign ground-truth decision
            # PUBLIC → 90% allow, 10% deny
            # INTERNAL → 75% allow, 25% deny
            # FINANCIAL → 20% allow, 80% deny
            # HEALTH → 15% allow, 85% deny
            # PII → 5% allow, 95% deny
            allow_probs = {
                DataClass.PUBLIC: 0.90,
                DataClass.INTERNAL: 0.75,
                DataClass.FINANCIAL: 0.20,
                DataClass.HEALTH: 0.15,
                DataClass.PII: 0.05,
            }
            allow_prob = allow_probs[data_class]
            correct_decision = "allow" if random.random() < allow_prob else "deny"

            flows.append(
                SyntheticDataFlow(
                    flow_id=f"flow_{i:04d}_{uuid4().hex[:8]}",
                    data_class=data_class,
                    engine=engine,
                    destination=destination,
                    features=features,
                    correct_decision=correct_decision,
                    tenant_id=tenant_id,
                )
            )

        return flows


@dataclass(frozen=True)
class AccuracyMetrics:
    """Accuracy and improvement measurement."""
    baseline_accuracy: float  # P(correct | baseline weights)
    learned_accuracy: float  # P(correct | learned weights)
    improvement: float  # (learned - baseline) / baseline * 100 (%)
    confidence_interval: Tuple[float, float]  # 90% CI for improvement
    sample_size: int  # Number of measured tasks/flows


def calculate_accuracy(
    predictions: List[str],
    ground_truths: List[str],
) -> float:
    """Calculate accuracy P(predicted == ground_truth).

    Args:
        predictions: List of predicted values
        ground_truths: List of ground-truth values

    Returns:
        Accuracy as float [0.0, 1.0]

    Raises:
        ValueError: Mismatched list lengths
    """
    if len(predictions) != len(ground_truths):
        raise ValueError(
            f"Predictions ({len(predictions)}) and ground truths ({len(ground_truths)}) "
            f"have different lengths"
        )

    if len(predictions) == 0:
        raise ValueError("Cannot calculate accuracy for empty predictions")

    correct = sum(1 for p, g in zip(predictions, ground_truths) if p == g)
    return correct / len(predictions)


def estimate_confidence_interval(
    accuracy: float,
    sample_size: int,
    confidence_level: float = 0.90,
) -> Tuple[float, float]:
    """Estimate 90% confidence interval for accuracy (Wilson score).

    Uses Wilson score interval (better for small samples than normal approximation).

    Args:
        accuracy: Measured accuracy
        sample_size: Number of samples
        confidence_level: Confidence level (default 0.90)

    Returns:
        Tuple of (lower, upper) bounds
    """
    import math

    if sample_size == 0 or not (0 <= accuracy <= 1):
        return (0.0, 1.0)

    # Wilson score interval z-critical value (90% confidence ≈ 1.645)
    z = {
        0.90: 1.645,
        0.95: 1.96,
        0.99: 2.576,
    }.get(confidence_level, 1.645)

    denominator = 1 + (z**2) / sample_size
    center = (accuracy + (z**2) / (2 * sample_size)) / denominator
    margin = z * math.sqrt((accuracy * (1 - accuracy) + (z**2) / (4 * sample_size)) / sample_size) / denominator

    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)

    return (lower, upper)


def measure_baseline_accuracy(
    tasks_or_flows: List[Any],
    routing_or_classification_func,
    tenant_id: str = "_default",
) -> float:
    """Measure baseline accuracy with default (Phase 2) weights.

    Args:
        tasks_or_flows: List of SyntheticTask or SyntheticDataFlow
        routing_or_classification_func: Function(item) → predicted value
        tenant_id: Tenant scope

    Returns:
        Baseline accuracy P(correct | default weights)
    """
    predictions = []
    ground_truths = []

    for item in tasks_or_flows:
        predicted = routing_or_classification_func(item)
        ground_truth = (
            item.correct_model
            if hasattr(item, "correct_model")
            else item.correct_decision
        )
        predictions.append(predicted)
        ground_truths.append(ground_truth)

    return calculate_accuracy(predictions, ground_truths)


def measure_learned_accuracy(
    tasks_or_flows: List[Any],
    routing_or_classification_func,
    learned_weights: Dict[str, float],
    tenant_id: str = "_default",
) -> float:
    """Measure learned accuracy with Phase 3 updated weights.

    Args:
        tasks_or_flows: List of SyntheticTask or SyntheticDataFlow
        routing_or_classification_func: Function(item, weights) → predicted value
        learned_weights: Updated weights/thresholds from Phase 3
        tenant_id: Tenant scope

    Returns:
        Learned accuracy P(correct | learned weights)
    """
    predictions = []
    ground_truths = []

    for item in tasks_or_flows:
        predicted = routing_or_classification_func(item, learned_weights)
        ground_truth = (
            item.correct_model
            if hasattr(item, "correct_model")
            else item.correct_decision
        )
        predictions.append(predicted)
        ground_truths.append(ground_truth)

    return calculate_accuracy(predictions, ground_truths)


def simulate_feedback(
    tasks_or_flows: List[Any],
    count: int,
    feedback_quality: float = 1.0,
) -> List[Tuple[Any, str]]:
    """Simulate operator feedback on routing/classification decisions.

    Simulates 'count' operator feedback events. Higher feedback_quality
    correlates feedback closer to ground truth.

    Args:
        tasks_or_flows: List of SyntheticTask or SyntheticDataFlow
        count: Number of feedback events to simulate
        feedback_quality: How often feedback matches ground truth (0.0-1.0)

    Returns:
        List of (item, feedback_type) pairs
        - feedback_type for Stream 1: "correct" or "incorrect"
        - feedback_type for Stream 3: "allow_correct", "deny_correct", etc.
    """
    if count > len(tasks_or_flows):
        raise ValueError(
            f"Cannot simulate {count} feedback on {len(tasks_or_flows)} items"
        )

    # Sample items without replacement
    sampled = random.sample(tasks_or_flows, count)
    feedback_list = []

    for item in sampled:
        # Decide if feedback matches ground truth
        matches_ground_truth = random.random() < feedback_quality

        if hasattr(item, "correct_model"):  # Stream 1 (Task)
            feedback_type = "correct" if matches_ground_truth else "incorrect"
        else:  # Stream 3 (DataFlow)
            # For Stream 3, simulate policy feedback
            if matches_ground_truth:
                feedback_type = (
                    "allow_correct"
                    if item.correct_decision == "allow"
                    else "deny_correct"
                )
            else:
                feedback_type = (
                    "allow_wrong"
                    if item.correct_decision == "deny"
                    else "deny_wrong"
                )

        feedback_list.append((item, feedback_type))

    return feedback_list
