"""Tests for ADR-0324 — Performance Aggregation Pipeline and Confidence Intervals.

Coverage:
- Confidence interval calculation (Bayesian Beta-Binomial)
- Performance aggregation (tool/skill metrics)
- Caching and cache invalidation
- Trending and time-windowed analysis
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.confidence_intervals import (
    ConfidenceInterval,
    ConfidenceIntervalCalculator,
    WindowedConfidenceCalculator,
)
from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.event_store import EventStore
from core.learning.performance_aggregator import (
    PerformanceAggregator,
    ToolPerformanceMetrics,
    SkillPerformanceMetrics,
    AggregationScheduler,
)


# ============================================================================
# Confidence Interval Tests
# ============================================================================


class TestConfidenceIntervalCalculator:
    """Test Bayesian confidence interval calculations."""

    def test_compute_interval_high_success_rate(self):
        """Test confidence interval for high success rate (8 successes, 2 failures)."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=8, failures=2)

        # Posterior: Beta(8+2, 2+2) = Beta(10, 4)
        assert 0.5 < ci.mean < 0.9, "Mean should be between 50-90%"
        assert ci.lower < ci.mean < ci.upper, "Lower < Mean < Upper"
        assert ci.samples == 10
        # At 80% success rate, 95% CI should be roughly [55%, 95%]
        assert 0.4 < ci.lower < 0.7
        assert 0.8 < ci.upper < 1.0

    def test_compute_interval_low_success_rate(self):
        """Test confidence interval for low success rate (2 successes, 8 failures)."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=2, failures=8)

        # Posterior: Beta(2+2, 8+2) = Beta(4, 10)
        assert 0.1 < ci.mean < 0.5, "Mean should be between 10-50%"
        assert ci.lower < ci.mean < ci.upper
        assert ci.samples == 10

    def test_compute_interval_zero_samples(self):
        """Test confidence interval with zero samples."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=0, failures=0)

        # Posterior: Beta(0+2, 0+2) = Beta(2, 2) (uniform)
        assert 0.4 < ci.mean < 0.6, "Mean of Beta(2,2) should be 0.5"
        assert ci.samples == 0

    def test_compute_interval_cold_start(self):
        """Test that cold-start (1 success) is regularized, not overconfident."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=1, failures=0)

        # Posterior: Beta(1+2, 0+2) = Beta(3, 2)
        # Should NOT be [1.0, 1.0] but something like [0.2, 0.9]
        assert 0.2 < ci.lower < 0.5, "Cold-start should have lower bound > 0.2"
        assert 0.7 < ci.upper < 0.95, "Cold-start should have upper bound < 0.95"
        assert ci.lower < ci.mean < ci.upper

    def test_compute_interval_width(self):
        """Test that confidence interval width decreases with sample size."""
        ci_10 = ConfidenceIntervalCalculator.compute_interval(successes=5, failures=5)
        ci_100 = ConfidenceIntervalCalculator.compute_interval(successes=50, failures=50)

        width_10 = ci_10.width()
        width_100 = ci_100.width()

        # More samples → narrower interval
        assert width_100 < width_10, "Larger sample should have narrower interval"

    def test_compute_interval_credible_level(self):
        """Test different credible levels."""
        ci_90 = ConfidenceIntervalCalculator.compute_interval(
            successes=8, failures=2, credible_level=0.90
        )
        ci_99 = ConfidenceIntervalCalculator.compute_interval(
            successes=8, failures=2, credible_level=0.99
        )

        # Higher credible level → wider interval
        assert ci_99.width() > ci_90.width()

    def test_confidence_interval_str(self):
        """Test string representation."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=8, failures=2)
        s = str(ci)
        assert "%" in s and "[" in s and "]" in s and "n=" in s


class TestWindowedConfidenceCalculator:
    """Test windowed confidence calculations."""

    def test_compute_for_window(self):
        """Test confidence intervals across multiple time windows."""
        successes = {"7d": 7, "30d": 20, "all": 25}
        failures = {"7d": 3, "30d": 10, "all": 15}

        intervals = WindowedConfidenceCalculator.compute_for_window(successes, failures)

        assert len(intervals) == 3
        assert "7d" in intervals and "30d" in intervals and "all" in intervals
        assert intervals["7d"].samples == 10
        assert intervals["30d"].samples == 30
        assert intervals["all"].samples == 40

    def test_trend_improving(self):
        """Test trend detection: improving."""
        intervals = {
            "7d": ConfidenceIntervalCalculator.compute_interval(5, 15),  # 25%
            "30d": ConfidenceIntervalCalculator.compute_interval(15, 35),  # 30%
            "all": ConfidenceIntervalCalculator.compute_interval(20, 30),  # 40%
        }

        trend = WindowedConfidenceCalculator.trend(
            intervals, window_order=["7d", "30d", "all"]
        )
        assert trend == "improving"

    def test_trend_declining(self):
        """Test trend detection: declining."""
        intervals = {
            "7d": ConfidenceIntervalCalculator.compute_interval(20, 30),  # 40%
            "30d": ConfidenceIntervalCalculator.compute_interval(15, 35),  # 30%
            "all": ConfidenceIntervalCalculator.compute_interval(5, 15),  # 25%
        }

        trend = WindowedConfidenceCalculator.trend(
            intervals, window_order=["7d", "30d", "all"]
        )
        assert trend == "declining"

    def test_trend_stable(self):
        """Test trend detection: stable."""
        intervals = {
            "7d": ConfidenceIntervalCalculator.compute_interval(50, 50),  # 50%
            "30d": ConfidenceIntervalCalculator.compute_interval(100, 100),  # 50%
            "all": ConfidenceIntervalCalculator.compute_interval(150, 150),  # 50%
        }

        trend = WindowedConfidenceCalculator.trend(
            intervals, window_order=["7d", "30d", "all"]
        )
        assert trend == "stable"


# ============================================================================
# Performance Aggregator Tests
# ============================================================================


class TestPerformanceAggregator:
    """Test performance aggregation pipeline."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store for testing."""
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "events.db"
            yield EventStore(db_path)

    @pytest.fixture
    def aggregator(self, event_store):
        """Create aggregator with test event store."""
        return PerformanceAggregator(event_store, cache_ttl_minutes=1)

    def test_aggregate_tool_metrics_empty(self, aggregator):
        """Test aggregation on empty event store."""
        metrics = asyncio.run(aggregator.aggregate_tool_metrics())
        assert isinstance(metrics, dict)
        assert len(metrics) == 0

    def test_aggregate_tool_metrics_single_tool(self, event_store, aggregator):
        """Test aggregation for a single tool."""
        # Write a few TOOL_EXECUTED events
        for i in range(5):
            is_success = i < 3  # 3 success, 2 failure
            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id="_default",
                instance_id="test",
                skill_name=None,
                session_id="session1",
                timestamp_utc=datetime.utcnow(),
                payload={
                    "tool_id": "bash",
                    "tool_name": "Bash Tool",
                    "status": "success" if is_success else "error",
                    "latency_ms": 100 + i * 10,
                    "estimated_cost_cents": 1 + i,
                },
            )
            event_store.write_event(event)

        # Aggregate
        metrics = asyncio.run(aggregator.aggregate_tool_metrics(time_window_days=999))

        assert "bash" in metrics
        m = metrics["bash"]
        assert m.success_count == 3
        assert m.failure_count == 2
        assert m.confidence_samples == 5
        assert 0.4 < m.confidence_mean < 0.7

    def test_cache_hits(self, aggregator):
        """Test that cache reduces recomputation."""
        metrics1 = asyncio.run(aggregator.aggregate_tool_metrics(use_cache=False))
        metrics2 = asyncio.run(aggregator.aggregate_tool_metrics(use_cache=True))

        # Same result (even if empty)
        assert metrics1 == metrics2

        # Cache should now have an entry
        stats = aggregator.get_stats()
        assert stats["cache_entries"] > 0

    def test_cache_invalidation(self, aggregator):
        """Test that old cache entries are invalidated."""
        # Fill cache
        asyncio.run(aggregator.aggregate_tool_metrics(use_cache=False))
        stats1 = aggregator.get_stats()
        assert stats1["valid_entries"] == 1

        # Clear cache
        cleared = aggregator.clear_cache()
        assert cleared == 1

        stats2 = aggregator.get_stats()
        assert stats2["cache_entries"] == 0

    def test_multiple_tools(self, event_store, aggregator):
        """Test aggregation across multiple tools."""
        tool_ids = ["bash", "python", "git"]

        for tool_id in tool_ids:
            for i in range(3):
                event = LearningEvent(
                    event_type=LearningEventType.TOOL_EXECUTED,
                    tenant_id="_default",
                    instance_id="test",
                    skill_name=None,
                    session_id="session1",
                    timestamp_utc=datetime.utcnow(),
                    payload={
                        "tool_id": tool_id,
                        "tool_name": tool_id.title(),
                        "status": "success",
                        "latency_ms": 50,
                        "estimated_cost_cents": 1,
                    },
                )
                event_store.write_event(event)

        metrics = asyncio.run(aggregator.aggregate_tool_metrics(time_window_days=999))

        assert len(metrics) == 3
        for tool_id in tool_ids:
            assert tool_id in metrics
            assert metrics[tool_id].success_count == 3

    def test_time_window_filtering(self, event_store, aggregator):
        """Test that time window filtering works."""
        now = datetime.utcnow()

        # Write event from 10 days ago
        old_event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id="_default",
            instance_id="test",
            skill_name=None,
            session_id="session1",
            timestamp_utc=now - timedelta(days=10),
            payload={
                "tool_id": "bash",
                "tool_name": "Bash",
                "status": "success",
                "latency_ms": 50,
                "estimated_cost_cents": 1,
            },
        )
        event_store.write_event(old_event)

        # Write event from today
        new_event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id="_default",
            instance_id="test",
            skill_name=None,
            session_id="session1",
            timestamp_utc=now,
            payload={
                "tool_id": "bash",
                "tool_name": "Bash",
                "status": "success",
                "latency_ms": 50,
                "estimated_cost_cents": 1,
            },
        )
        event_store.write_event(new_event)

        # Query 7-day window (should include only new_event)
        metrics_7d = asyncio.run(aggregator.aggregate_tool_metrics(time_window_days=7))
        assert metrics_7d["bash"].success_count == 1

        # Query 30-day window (should include both)
        metrics_30d = asyncio.run(aggregator.aggregate_tool_metrics(time_window_days=30))
        assert metrics_30d["bash"].success_count == 2

    def test_percentile_calculation(self, aggregator):
        """Test percentile calculation."""
        # Create aggregator with sample data
        data = [10, 20, 30, 40, 50]

        p50 = aggregator._percentile(data, 50)
        p95 = aggregator._percentile(data, 95)
        p99 = aggregator._percentile(data, 99)

        assert p50 == 30  # Median
        assert p95 >= 40
        assert p99 >= 40
        assert p99 >= p95 >= p50

    def test_tool_metrics_structure(self, event_store, aggregator):
        """Test that tool metrics have correct structure."""
        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id="_default",
            instance_id="test",
            skill_name=None,
            session_id="session1",
            timestamp_utc=datetime.utcnow(),
            payload={
                "tool_id": "bash",
                "tool_name": "Bash Tool",
                "status": "success",
                "latency_ms": 100,
                "estimated_cost_cents": 5,
            },
        )
        event_store.write_event(event)

        metrics = asyncio.run(aggregator.aggregate_tool_metrics(time_window_days=999))
        m = metrics["bash"]

        # Check required fields
        assert m.tool_id == "bash"
        assert m.tool_name == "Bash Tool"
        assert m.success_count >= 0
        assert m.failure_count >= 0
        assert 0.0 <= m.confidence_mean <= 1.0
        assert m.confidence_lower <= m.confidence_mean <= m.confidence_upper
        assert m.median_latency_ms >= 0
        assert m.median_cost_cents >= 0

    def test_tool_success_rate_property(self):
        """Test success_rate property calculation."""
        m = ToolPerformanceMetrics(
            tool_id="test",
            tool_name="Test",
            success_count=8,
            failure_count=2,
            confidence_lower=0.5,
            confidence_mean=0.8,
            confidence_upper=0.95,
            confidence_samples=10,
            median_latency_ms=100,
            p95_latency_ms=200,
            p99_latency_ms=300,
            median_cost_cents=5,
        )

        assert m.success_rate == 0.8
        assert m.failure_rate == 0.2


class TestAggregationScheduler:
    """Test aggregation scheduler."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store."""
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "events.db"
            yield EventStore(db_path)

    @pytest.fixture
    def scheduler(self, event_store):
        """Create scheduler."""
        aggregator = PerformanceAggregator(event_store)
        return AggregationScheduler(aggregator, interval_minutes=1)

    @pytest.mark.asyncio
    async def test_scheduler_run_aggregation(self, scheduler):
        """Test that scheduler runs aggregation."""
        # Should not raise
        await scheduler.run_aggregation(tenant_id="_default")

    def test_scheduler_stop(self, scheduler):
        """Test scheduler stop."""
        scheduler._running = True
        scheduler.stop()
        assert scheduler._running is False


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests combining multiple components."""

    @pytest.fixture
    def event_store(self):
        """Create temporary event store."""
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "events.db"
            yield EventStore(db_path)

    def test_end_to_end_tool_aggregation(self, event_store):
        """End-to-end: write events, aggregate, verify metrics."""
        aggregator = PerformanceAggregator(event_store)

        # Simulate tool usage
        tool_results = [
            ("success", 50),
            ("success", 60),
            ("success", 55),
            ("error", 100),
            ("success", 52),
        ]

        for status, latency in tool_results:
            event = LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id="_default",
                instance_id="instance1",
                skill_name=None,
                session_id="session1",
                timestamp_utc=datetime.utcnow(),
                payload={
                    "tool_id": "bash",
                    "tool_name": "Bash",
                    "status": status,
                    "latency_ms": latency,
                    "estimated_cost_cents": 2,
                },
            )
            event_store.write_event(event)

        # Aggregate and verify
        metrics = asyncio.run(aggregator.aggregate_tool_metrics(time_window_days=999))

        assert "bash" in metrics
        m = metrics["bash"]
        assert m.success_count == 4
        assert m.failure_count == 1
        assert 0.7 < m.confidence_mean < 0.9
        assert 40 <= m.median_latency_ms <= 100  # Some latency
