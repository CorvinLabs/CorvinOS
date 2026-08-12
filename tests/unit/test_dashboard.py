"""Tests for Reporting Dashboard (ADR-0321)."""

import pytest
from datetime import datetime
from core.learning.dashboard import (
    MetricSummary,
    SkillPerformance,
    DashboardMetrics,
    MetricsAggregator,
)


class TestMetricSummary:
    """Test metric summary model."""

    def test_create_summary(self):
        """Create metric summary."""
        summary = MetricSummary(
            metric_type="accuracy",
            count=10,
            mean=0.85,
            min=0.70,
            max=0.95,
            stddev=0.08,
        )

        assert summary.metric_type == "accuracy"
        assert summary.count == 10
        assert summary.mean == 0.85

    def test_summary_to_dict(self):
        """Convert summary to dict."""
        summary = MetricSummary(
            metric_type="latency",
            count=5,
            mean=150.5,
            min=100.0,
            max=200.0,
            stddev=35.2,
        )

        data = summary.to_dict()

        assert data["metric_type"] == "latency"
        assert data["mean"] == 150.5


class TestSkillPerformance:
    """Test skill performance model."""

    def test_create_performance(self):
        """Create skill performance."""
        perf = SkillPerformance(
            skill_name="ranking",
            accuracy=0.92,
            latency_ms=250.0,
            confidence=0.85,
            user_satisfaction=4.5,
            usage_count=42,
        )

        assert perf.skill_name == "ranking"
        assert perf.accuracy == 0.92
        assert perf.usage_count == 42

    def test_performance_to_dict(self):
        """Convert performance to dict."""
        perf = SkillPerformance(
            skill_name="summarizer",
            accuracy=0.88,
            latency_ms=320.0,
        )

        data = perf.to_dict()

        assert data["skill_name"] == "summarizer"
        assert data["accuracy"] == 0.88
        assert data["last_updated"] is None


class TestDashboardMetrics:
    """Test dashboard metrics model."""

    def test_create_dashboard(self):
        """Create dashboard snapshot."""
        acc_summary = MetricSummary("accuracy", 10, 0.85, 0.70, 0.95, 0.08)
        lat_summary = MetricSummary("latency", 10, 250.0, 100.0, 400.0, 80.0)

        dashboard = DashboardMetrics(
            timestamp=datetime.utcnow(),
            accuracy_summary=acc_summary,
            latency_summary=lat_summary,
            total_events=20,
        )

        assert dashboard.accuracy_summary.mean == 0.85
        assert dashboard.total_events == 20

    def test_dashboard_to_dict(self):
        """Convert dashboard to dict."""
        summary = MetricSummary("confidence", 5, 0.75, 0.60, 0.90, 0.10)

        dashboard = DashboardMetrics(
            timestamp=datetime.utcnow(),
            confidence_summary=summary,
            total_events=5,
        )

        data = dashboard.to_dict()

        assert data["confidence_summary"]["mean"] == 0.75
        assert data["total_events"] == 5


class TestMetricsAggregator:
    """Test metrics aggregator."""

    @pytest.fixture
    def aggregator(self):
        """Create aggregator."""
        return MetricsAggregator("_default")

    def test_aggregate_empty(self, aggregator):
        """Handle empty metric list."""
        summary = aggregator.aggregate_metrics([])
        assert summary is None

    def test_aggregate_accuracy(self, aggregator):
        """Aggregate accuracy metrics."""
        metrics = [
            {"value": 0.90},
            {"value": 0.85},
            {"value": 0.95},
        ]

        summary = aggregator.aggregate_metrics(metrics, "accuracy")

        assert summary.count == 3
        assert summary.mean == pytest.approx(0.9, rel=0.01)
        assert summary.min == 0.85
        assert summary.max == 0.95

    def test_aggregate_latency(self, aggregator):
        """Aggregate latency metrics."""
        metrics = [
            {"value": 100.0},
            {"value": 200.0},
            {"value": 300.0},
        ]

        summary = aggregator.aggregate_metrics(metrics, "latency")

        assert summary.count == 3
        assert summary.mean == 200.0

    def test_aggregate_stddev(self, aggregator):
        """Calculate standard deviation."""
        metrics = [
            {"value": 1.0},
            {"value": 2.0},
            {"value": 3.0},
        ]

        summary = aggregator.aggregate_metrics(metrics, "test")

        # mean=2.0, variance=2/3, stddev=sqrt(2/3)≈0.8165
        assert summary.stddev == pytest.approx(0.8165, rel=0.01)

    def test_aggregate_skill_performance(self, aggregator):
        """Aggregate performance for a skill."""
        accuracy = [{"value": 0.90}, {"value": 0.88}]
        latency = [{"value": 250.0}, {"value": 240.0}]
        confidence = [{"value": 0.85}]
        satisfaction = [{"value": 4.5}, {"value": 5.0}]

        perf = aggregator.aggregate_skill_performance(
            accuracy,
            latency,
            confidence,
            satisfaction,
            "ranking",
        )

        assert perf.skill_name == "ranking"
        assert perf.accuracy == pytest.approx(0.89, rel=0.01)
        assert perf.latency_ms == 245.0
        assert perf.confidence == 0.85
        assert perf.user_satisfaction == pytest.approx(4.75, rel=0.01)
        assert perf.usage_count == 7  # 2 + 2 + 1 + 2

    def test_skill_performance_no_metrics(self, aggregator):
        """Handle skill with no metrics."""
        perf = aggregator.aggregate_skill_performance([], [], [], [], "empty_skill")

        assert perf.skill_name == "empty_skill"
        assert perf.accuracy is None
        assert perf.latency_ms is None
        assert perf.usage_count == 0

    def test_build_dashboard_empty(self, aggregator):
        """Build empty dashboard."""
        dashboard = aggregator.build_dashboard([])

        assert dashboard.total_events == 0
        assert dashboard.accuracy_summary is None

    def test_build_dashboard_with_metrics(self, aggregator):
        """Build complete dashboard."""
        metrics = [
            {"metric_type": "accuracy", "value": 0.90},
            {"metric_type": "accuracy", "value": 0.85},
            {"metric_type": "latency", "value": 250.0},
            {"metric_type": "latency", "value": 300.0},
        ]

        dashboard = aggregator.build_dashboard(metrics)

        assert dashboard.total_events == 4
        assert dashboard.accuracy_summary.count == 2
        assert dashboard.latency_summary.count == 2

    def test_build_dashboard_with_skills(self, aggregator):
        """Build dashboard with skill aggregations."""
        metrics = [
            {"metric_type": "accuracy", "value": 0.92},
        ]

        skills_by_name = {
            "ranking": {
                "accuracy": [{"value": 0.92}],
                "latency": [{"value": 240.0}],
                "confidence": [],
                "satisfaction": [],
            }
        }

        dashboard = aggregator.build_dashboard(metrics, skills_by_name)

        assert "ranking" in dashboard.skills
        assert dashboard.skills["ranking"].accuracy == 0.92
        assert dashboard.skills["ranking"].latency_ms == 240.0
