"""Reporting Dashboard — observability UI (ADR-0321)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class MetricSummary:
    """Summary statistics for a metric."""

    metric_type: str
    count: int
    mean: float
    min: float
    max: float
    stddev: float

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "metric_type": self.metric_type,
            "count": self.count,
            "mean": self.mean,
            "min": self.min,
            "max": self.max,
            "stddev": self.stddev,
        }


@dataclass(frozen=True)
class SkillPerformance:
    """Aggregated performance metrics for a skill."""

    skill_name: str
    accuracy: Optional[float] = None
    latency_ms: Optional[float] = None
    confidence: Optional[float] = None
    user_satisfaction: Optional[float] = None
    usage_count: int = 0
    last_updated: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "skill_name": self.skill_name,
            "accuracy": self.accuracy,
            "latency_ms": self.latency_ms,
            "confidence": self.confidence,
            "user_satisfaction": self.user_satisfaction,
            "usage_count": self.usage_count,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


@dataclass(frozen=True)
class DashboardMetrics:
    """Complete dashboard metrics snapshot."""

    timestamp: datetime
    accuracy_summary: Optional[MetricSummary] = None
    latency_summary: Optional[MetricSummary] = None
    confidence_summary: Optional[MetricSummary] = None
    satisfaction_summary: Optional[MetricSummary] = None
    skills: dict = field(default_factory=dict)
    total_events: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "accuracy_summary": self.accuracy_summary.to_dict() if self.accuracy_summary else None,
            "latency_summary": self.latency_summary.to_dict() if self.latency_summary else None,
            "confidence_summary": self.confidence_summary.to_dict() if self.confidence_summary else None,
            "satisfaction_summary": self.satisfaction_summary.to_dict() if self.satisfaction_summary else None,
            "skills": {name: perf.to_dict() for name, perf in self.skills.items()},
            "total_events": self.total_events,
        }


class MetricsAggregator:
    """Aggregate metrics for observability dashboard."""

    def __init__(self, tenant_id: str):
        """Initialize aggregator.

        Args:
            tenant_id: Tenant ID (for isolation)
        """
        self.tenant_id = tenant_id

    def aggregate_metrics(
        self,
        metrics: list[dict],
        metric_type: Optional[str] = None,
    ) -> Optional[MetricSummary]:
        """Aggregate metrics to summary statistics.

        Args:
            metrics: List of metric dicts from EventStore
            metric_type: Type of metric to aggregate (for label)

        Returns:
            MetricSummary or None if no metrics
        """
        if not metrics:
            return None

        values = [float(m.get("value", 0)) for m in metrics]
        if not values:
            return None

        mean = sum(values) / len(values)
        min_val = min(values)
        max_val = max(values)

        # Calculate stddev
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        stddev = variance ** 0.5

        return MetricSummary(
            metric_type=metric_type or "unknown",
            count=len(metrics),
            mean=round(mean, 4),
            min=round(min_val, 4),
            max=round(max_val, 4),
            stddev=round(stddev, 4),
        )

    def aggregate_skill_performance(
        self,
        accuracy_metrics: list[dict],
        latency_metrics: list[dict],
        confidence_metrics: list[dict],
        satisfaction_metrics: list[dict],
        skill_name: str,
    ) -> SkillPerformance:
        """Aggregate performance metrics for a skill.

        Args:
            accuracy_metrics: List of accuracy metric dicts
            latency_metrics: List of latency metric dicts
            confidence_metrics: List of confidence metric dicts
            satisfaction_metrics: List of satisfaction metric dicts
            skill_name: Name of the skill

        Returns:
            SkillPerformance aggregated stats
        """
        accuracy = None
        if accuracy_metrics:
            accuracy_values = [float(m.get("value", 0)) for m in accuracy_metrics]
            accuracy = round(sum(accuracy_values) / len(accuracy_values), 4)

        latency = None
        if latency_metrics:
            latency_values = [float(m.get("value", 0)) for m in latency_metrics]
            latency = round(sum(latency_values) / len(latency_values), 2)

        confidence = None
        if confidence_metrics:
            confidence_values = [float(m.get("value", 0)) for m in confidence_metrics]
            confidence = round(sum(confidence_values) / len(confidence_values), 4)

        satisfaction = None
        if satisfaction_metrics:
            satisfaction_values = [float(m.get("value", 0)) for m in satisfaction_metrics]
            satisfaction = round(sum(satisfaction_values) / len(satisfaction_values), 2)

        # Usage count is total of all metric types
        usage_count = (
            len(accuracy_metrics)
            + len(latency_metrics)
            + len(confidence_metrics)
            + len(satisfaction_metrics)
        )

        return SkillPerformance(
            skill_name=skill_name,
            accuracy=accuracy,
            latency_ms=latency,
            confidence=confidence,
            user_satisfaction=satisfaction,
            usage_count=usage_count,
            last_updated=datetime.utcnow(),
        )

    def build_dashboard(
        self,
        all_metrics: list[dict],
        skills_by_name: dict = None,
    ) -> DashboardMetrics:
        """Build complete dashboard from metrics.

        Args:
            all_metrics: All metric records
            skills_by_name: Pre-aggregated skill metrics {skill_name: {metric_type: [metrics]}}

        Returns:
            DashboardMetrics dashboard snapshot
        """
        if not all_metrics:
            return DashboardMetrics(timestamp=datetime.utcnow(), total_events=0)

        # Separate by metric type
        accuracy = [m for m in all_metrics if m.get("metric_type") == "accuracy"]
        latency = [m for m in all_metrics if m.get("metric_type") == "latency"]
        confidence = [m for m in all_metrics if m.get("metric_type") == "confidence"]
        satisfaction = [m for m in all_metrics if m.get("metric_type") == "satisfaction"]

        # Aggregate each type
        acc_summary = self.aggregate_metrics(accuracy, "accuracy")
        lat_summary = self.aggregate_metrics(latency, "latency")
        conf_summary = self.aggregate_metrics(confidence, "confidence")
        sat_summary = self.aggregate_metrics(satisfaction, "satisfaction")

        # Build skill performance
        skills_dict = {}
        if skills_by_name:
            for skill_name, metrics_by_type in skills_by_name.items():
                perf = self.aggregate_skill_performance(
                    metrics_by_type.get("accuracy", []),
                    metrics_by_type.get("latency", []),
                    metrics_by_type.get("confidence", []),
                    metrics_by_type.get("satisfaction", []),
                    skill_name,
                )
                skills_dict[skill_name] = perf

        return DashboardMetrics(
            timestamp=datetime.utcnow(),
            accuracy_summary=acc_summary,
            latency_summary=lat_summary,
            confidence_summary=conf_summary,
            satisfaction_summary=sat_summary,
            skills=skills_dict,
            total_events=len(all_metrics),
        )
