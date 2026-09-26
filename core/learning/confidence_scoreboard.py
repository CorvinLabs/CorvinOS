"""ConfidenceScoreboard — Phase C k=3 Loop Closure (k=2 iteration).

Stores and manages confidence scores computed from aggregated audit windows.
- Score persistence (per task, model, pattern)
- Confidence trend tracking (moving average)
- Trigger thresholds for optimizer
- Query API for learning components

Invariants:
- Scores are immutable once written (append-only)
- Each score carries a window_id (traceability to audit window)
- Confidence range: [0.0, 1.0]
- Tenant-scoped (per tenant_id)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.paths import tenant_home


@dataclass(frozen=True)
class ConfidenceScore:
    """Immutable confidence score for a task/model/pattern."""

    score_id: str  # Unique identifier
    window_id: str  # Link to audit window
    tenant_id: str
    task_id: str
    model_id: str  # The model being evaluated
    pattern_key: str  # What was measured (e.g., "task_completion_rate")
    confidence: float  # [0.0, 1.0]
    sample_count: int  # Events aggregated into this score
    timestamp: str  # ISO-8601 UTC
    metadata: dict = field(default_factory=dict)  # Optional context

    def __post_init__(self):
        """Validate score after initialization."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be in [0.0, 1.0], got {self.confidence}")
        if self.sample_count < 1:
            raise ValueError(f"sample_count must be >= 1, got {self.sample_count}")


@dataclass
class ConfidenceTrend:
    """Moving average of confidence scores over a window."""

    task_id: str
    model_id: str
    pattern_key: str
    n_samples: int  # Number of scores in the trend
    mean_confidence: float  # Moving average
    std_dev: float  # Standard deviation
    trend_direction: str  # "improving", "degrading", "stable"
    last_updated: str  # ISO-8601

    def is_significant(self) -> bool:
        """Check if trend is statistically significant (n >= 10)."""
        return self.n_samples >= 10

    def meets_threshold(self, threshold: float = 0.75) -> bool:
        """Check if mean confidence meets optimizer threshold."""
        return self.mean_confidence >= threshold


class ConfidenceScoreboard:
    """Manage confidence scores and trends for learning loop."""

    def __init__(self, window_size: int = 10, trend_threshold: float = 0.75):
        """Initialize scoreboard.

        Args:
          window_size: number of scores to keep in moving average
          trend_threshold: confidence threshold for triggering optimization
        """
        self.window_size = max(3, min(window_size, 100))
        self.trend_threshold = max(0.0, min(trend_threshold, 1.0))
        self._scores: dict[str, ConfidenceScore] = {}  # In-memory cache
        self._trends: dict[tuple, ConfidenceTrend] = {}  # (task_id, model_id, pattern) → trend

    async def write_score(
        self,
        window_id: str,
        tenant_id: str,
        task_id: str,
        model_id: str,
        pattern_key: str,
        confidence: float,
        sample_count: int,
        metadata: Optional[dict] = None,
    ) -> str:
        """Write a confidence score (append-only).

        Args:
          window_id: link to audit window
          tenant_id: tenant context
          task_id: which task
          model_id: which model
          pattern_key: what pattern was measured
          confidence: score [0.0, 1.0]
          sample_count: size of aggregated batch
          metadata: optional context

        Returns:
          score_id of the written score

        Raises:
          ValueError: if score is invalid
        """
        import uuid

        score_id = f"score_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()

        try:
            score = ConfidenceScore(
                score_id=score_id,
                window_id=window_id,
                tenant_id=tenant_id,
                task_id=task_id,
                model_id=model_id,
                pattern_key=pattern_key,
                confidence=confidence,
                sample_count=sample_count,
                timestamp=now,
                metadata=metadata or {},
            )
        except ValueError as e:
            raise ValueError(f"Invalid confidence score: {e}") from e

        # Store in-memory cache
        self._scores[score_id] = score

        # Update trend
        await self._update_trend(task_id, model_id, pattern_key, confidence)

        # Persist to scoreboard log (append-only)
        await self._persist_score(tenant_id, score)

        return score_id

    async def _update_trend(
        self, task_id: str, model_id: str, pattern_key: str, confidence: float
    ) -> None:
        """Update moving average trend for this pattern."""
        trend_key = (task_id, model_id, pattern_key)

        # Retrieve or create trend
        if trend_key not in self._trends:
            self._trends[trend_key] = ConfidenceTrend(
                task_id=task_id,
                model_id=model_id,
                pattern_key=pattern_key,
                n_samples=0,
                mean_confidence=0.0,
                std_dev=0.0,
                trend_direction="stable",
                last_updated=datetime.now(timezone.utc).isoformat(),
            )

        trend = self._trends[trend_key]

        # Collect last N scores for this pattern
        recent_scores = [
            s
            for s in self._scores.values()
            if s.task_id == task_id
            and s.model_id == model_id
            and s.pattern_key == pattern_key
        ]

        # Keep only last window_size scores
        recent_scores = sorted(recent_scores, key=lambda s: s.timestamp)[-self.window_size :]

        if recent_scores:
            confidences = [s.confidence for s in recent_scores]
            mean = sum(confidences) / len(confidences)
            variance = sum((c - mean) ** 2 for c in confidences) / len(confidences)
            std_dev = variance ** 0.5

            # Determine trend direction
            if len(confidences) >= 2:
                first_half = sum(confidences[: len(confidences) // 2]) / max(1, len(confidences) // 2)
                second_half = sum(confidences[len(confidences) // 2 :]) / max(
                    1, len(confidences) - len(confidences) // 2
                )
                if second_half > first_half + 0.05:
                    direction = "improving"
                elif second_half < first_half - 0.05:
                    direction = "degrading"
                else:
                    direction = "stable"
            else:
                direction = "stable"

            # Update trend
            trend.n_samples = len(recent_scores)
            trend.mean_confidence = mean
            trend.std_dev = std_dev
            trend.trend_direction = direction
            trend.last_updated = datetime.now(timezone.utc).isoformat()

            self._trends[trend_key] = trend

    async def _persist_score(self, tenant_id: str, score: ConfidenceScore) -> None:
        """Persist score to scoreboard log (append-only).

        Appends to: <tenant_home>/<tenant_id>/global/learning/scoreboard.jsonl
        """
        try:
            log_path = Path(tenant_home(tenant_id)) / "global" / "learning" / "scoreboard.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            with open(log_path, "a") as f:
                json.dump(asdict(score), f)
                f.write("\n")
                f.flush()
        except Exception:
            # Non-blocking: persistence failure should not crash scoring
            pass

    async def get_trend(
        self, task_id: str, model_id: str, pattern_key: str
    ) -> Optional[ConfidenceTrend]:
        """Get the current trend for a pattern."""
        trend_key = (task_id, model_id, pattern_key)
        return self._trends.get(trend_key)

    async def get_scores_for_task(self, task_id: str) -> list[ConfidenceScore]:
        """Get all scores for a task."""
        return [s for s in self._scores.values() if s.task_id == task_id]

    async def list_triggerable_trends(self) -> list[ConfidenceTrend]:
        """List all trends that meet optimizer threshold.

        Returns:
          Trends with mean_confidence >= threshold (ready for optimization)
        """
        return [t for t in self._trends.values() if t.meets_threshold(self.trend_threshold)]

    async def clear_cache(self) -> None:
        """Clear in-memory cache (for testing or after large batch)."""
        self._scores.clear()
        self._trends.clear()
