"""Performance metrics collection for Phase 3 adaptive routing (ADR-0391).

Tracks per-stage performance metrics (utilization, confidence, quality, latency)
and provides rolling averages for decision making on budget rebalancing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .adaptive_budget import PerformanceMetric


@dataclass
class StageMetrics:
    """Aggregated metrics for a single stage over a window."""
    stage_id: str
    window_size: int
    metrics: deque[PerformanceMetric] = field(default_factory=deque)

    def add_metric(self, metric: PerformanceMetric) -> None:
        """Record a new metric, maintaining window size."""
        self.metrics.append(metric)
        while len(self.metrics) > self.window_size:
            self.metrics.popleft()

    def get_rolling_average(self) -> PerformanceMetric:
        """Calculate rolling average across all metrics in window."""
        if not self.metrics:
            return PerformanceMetric(
                utilization=0.0,
                confidence=0.0,
                quality=0.0,
                latency_ms=0.0
            )

        count = len(self.metrics)
        avg = PerformanceMetric(
            utilization=sum(m.utilization for m in self.metrics) / count,
            confidence=sum(m.confidence for m in self.metrics) / count,
            quality=sum(m.quality for m in self.metrics) / count,
            latency_ms=sum(m.latency_ms for m in self.metrics) / count,
        )
        return avg

    def clear(self) -> None:
        """Clear all recorded metrics."""
        self.metrics.clear()


class PerformanceTracker:
    """Tracks per-stage performance metrics for adaptive budgeting.

    Maintains rolling windows of metrics per stage and detects when
    rebalancing should occur based on performance changes.
    """

    def __init__(self, *, window_size: int = 10, rebalance_delta_threshold: float = 0.15):
        """Initialize the tracker.

        Args:
            window_size: Number of recent measurements to keep per stage (default 10)
            rebalance_delta_threshold: Utilization change to trigger rebalancing (default 15%)
        """
        self.window_size = window_size
        self.rebalance_delta_threshold = rebalance_delta_threshold
        self.stages: dict[str, StageMetrics] = {}
        self._baseline_utilization: dict[str, float] = {}

    def record_stage_execution(self, stage_id: str, metric: PerformanceMetric) -> None:
        """Record execution metrics for a stage.

        Args:
            stage_id: Identifier of the stage (e.g., "memory", "graph", "skills", "synthesis")
            metric: PerformanceMetric with utilization, confidence, quality, latency
        """
        if stage_id not in self.stages:
            self.stages[stage_id] = StageMetrics(stage_id, self.window_size)
            self._baseline_utilization[stage_id] = metric.utilization

        self.stages[stage_id].add_metric(metric)

    def get_rolling_average(self, stage_id: str,
                          window_size: "int | None" = None) -> PerformanceMetric | None:
        """Get rolling average metrics for a stage.

        Args:
            stage_id: Stage identifier
            window_size: Override window size for this query (optional)

        Returns:
            PerformanceMetric with rolling averages, or None if stage not tracked
        """
        if stage_id not in self.stages:
            return None

        stage = self.stages[stage_id]
        if window_size is not None and window_size > 0:
            # Temporarily limit the window for this calculation
            metrics_to_avg = list(stage.metrics)[:-window_size] if len(stage.metrics) > window_size else stage.metrics
            if not metrics_to_avg:
                return stage.get_rolling_average()

            count = len(metrics_to_avg)
            return PerformanceMetric(
                utilization=sum(m.utilization for m in metrics_to_avg) / count,
                confidence=sum(m.confidence for m in metrics_to_avg) / count,
                quality=sum(m.quality for m in metrics_to_avg) / count,
                latency_ms=sum(m.latency_ms for m in metrics_to_avg) / count,
            )

        return stage.get_rolling_average()

    def should_rebalance(self) -> bool:
        """Determine if budget rebalancing should occur.

        Returns True if any stage's utilization has drifted significantly
        (by rebalance_delta_threshold) from its baseline.

        Returns:
            bool indicating if rebalancing should happen
        """
        for stage_id, baseline in self._baseline_utilization.items():
            avg = self.get_rolling_average(stage_id)
            if avg is None:
                continue

            delta = abs(avg.utilization - baseline)
            if delta >= self.rebalance_delta_threshold:
                return True

        return False

    def get_all_metrics(self) -> dict[str, PerformanceMetric]:
        """Get rolling averages for all tracked stages.

        Returns:
            Dict mapping stage_id to its rolling average PerformanceMetric
        """
        return {
            stage_id: stage.get_rolling_average()
            for stage_id, stage in self.stages.items()
        }

    def reset_baseline(self) -> None:
        """Reset utilization baseline to current rolling average.

        Called after a rebalance to establish new baseline for drift detection.
        """
        for stage_id in self.stages:
            avg = self.get_rolling_average(stage_id)
            if avg is not None:
                self._baseline_utilization[stage_id] = avg.utilization

    def clear_stage(self, stage_id: str) -> None:
        """Clear metrics for a specific stage."""
        if stage_id in self.stages:
            self.stages[stage_id].clear()

    def clear_all(self) -> None:
        """Clear all recorded metrics."""
        for stage in self.stages.values():
            stage.clear()
        self._baseline_utilization.clear()

    def summary(self) -> dict[str, Any]:
        """Export a summary of current tracking state."""
        return {
            "window_size": self.window_size,
            "rebalance_delta_threshold": self.rebalance_delta_threshold,
            "stages_tracked": len(self.stages),
            "metrics_per_stage": {
                stage_id: len(stage.metrics)
                for stage_id, stage in self.stages.items()
            },
            "rolling_averages": {
                stage_id: {
                    "utilization": metric.utilization,
                    "confidence": metric.confidence,
                    "quality": metric.quality,
                    "latency_ms": metric.latency_ms,
                }
                for stage_id, metric in self.get_all_metrics().items()
            },
            "should_rebalance": self.should_rebalance(),
        }
