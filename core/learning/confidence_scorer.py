"""Confidence Scoring — relevance + reliability (ADR-0315)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ConfidenceBand(str, Enum):
    """Confidence level bands."""

    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"


@dataclass(frozen=True)
class ConfidenceScore:
    """A skill's confidence profile."""

    relevance: float
    reliability: float
    combined: float
    band: ConfidenceBand
    reasoning: Optional[str] = None


class ConfidenceScorer:
    """Score skills by relevance + reliability."""

    # Skill-task pairing heuristics (Phase 3.1)
    RELEVANCE_MAP = {
        ("ranking", "summarize"): 0.9,
        ("ranking", "code_review"): 0.3,
        ("ranking", "research"): 0.85,
        ("code_review", "code_review"): 0.95,
        ("code_review", "summarize"): 0.2,
        ("code_review", "research"): 0.4,
        ("summarizer", "summarize"): 0.95,
        ("summarizer", "code_review"): 0.3,
        ("summarizer", "research"): 0.8,
    }

    def score_skill(
        self,
        skill_name: str,
        task_type: str,
        invocation_count: int,
        error_rate: float,
        avg_latency_ms: float,
        latency_stddev_ms: float = 0.0,
        user_feedback_score: Optional[float] = None,
    ) -> ConfidenceScore:
        """Calculate full confidence profile for a skill.

        Args:
            skill_name: Which skill was used
            task_type: What was the task
            invocation_count: How many times invoked
            error_rate: Fraction of invocations that failed (0.0–1.0)
            avg_latency_ms: Mean response time
            latency_stddev_ms: Standard deviation of response times
            user_feedback_score: User rating if available (0.0–1.0)

        Returns:
            ConfidenceScore with all components
        """
        # Calculate components
        relevance = self._calculate_relevance(skill_name, task_type, user_feedback_score)
        reliability = self._calculate_reliability(
            invocation_count, error_rate, avg_latency_ms, latency_stddev_ms
        )

        # Combine: 60% relevance, 40% reliability
        combined = 0.6 * relevance + 0.4 * reliability

        # Map to band
        band = self._score_to_band(combined)

        reasoning = (
            f"relevance={relevance:.2f} (task={task_type}), "
            f"reliability={reliability:.2f} (n={invocation_count}, error_rate={error_rate:.2f})"
        )

        return ConfidenceScore(
            relevance=relevance,
            reliability=reliability,
            combined=combined,
            band=band,
            reasoning=reasoning,
        )

    def _calculate_relevance(
        self,
        skill_name: str,
        task_type: str,
        user_feedback_score: Optional[float] = None,
    ) -> float:
        """Calculate relevance score (0.0–1.0).

        Args:
            skill_name: Which skill
            task_type: What task
            user_feedback_score: User rating (0.0–1.0) if available

        Returns:
            Relevance score 0.0–1.0
        """
        # Heuristic: skill-task pairing
        base_score = self.RELEVANCE_MAP.get((skill_name, task_type), 0.5)

        # Phase 4+: User feedback overrides heuristic
        if user_feedback_score is not None:
            base_score = 0.7 * base_score + 0.3 * user_feedback_score

        return max(0.0, min(1.0, base_score))

    def _calculate_reliability(
        self,
        invocation_count: int,
        error_rate: float,
        avg_latency_ms: float,
        latency_stddev_ms: float,
    ) -> float:
        """Calculate reliability score (0.0–1.0).

        Args:
            invocation_count: How many times used
            error_rate: Fraction of errors (0.0–1.0)
            avg_latency_ms: Mean response time
            latency_stddev_ms: Standard deviation

        Returns:
            Reliability score 0.0–1.0
        """
        # Minimum sample size: <5 invocations = neutral
        if invocation_count < 5:
            return 0.5

        # Error rate component: low error = high reliability
        error_reliability = 1.0 - error_rate

        # Latency consistency: low variance = high reliability
        if latency_stddev_ms == 0 or avg_latency_ms == 0:
            latency_reliability = 1.0
        else:
            # Coefficient of variation (normalized standard deviation)
            cv = latency_stddev_ms / avg_latency_ms
            # High CV = low reliability; cap at 1.0
            latency_reliability = max(0.0, 1.0 - min(1.0, cv))

        # Combine: error rate (70%) more important than latency variance (30%)
        reliability = 0.7 * error_reliability + 0.3 * latency_reliability

        return max(0.0, min(1.0, reliability))

    def _score_to_band(self, score: float) -> ConfidenceBand:
        """Convert numeric score to band.

        Args:
            score: 0.0–1.0

        Returns:
            ConfidenceBand
        """
        if score >= 0.85:
            return ConfidenceBand.VERY_HIGH
        elif score >= 0.70:
            return ConfidenceBand.HIGH
        elif score >= 0.50:
            return ConfidenceBand.MEDIUM
        elif score >= 0.25:
            return ConfidenceBand.LOW
        else:
            return ConfidenceBand.VERY_LOW
