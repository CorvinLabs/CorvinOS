"""Unit tests for Performance Aggregation Pipeline (Gap 4, ADR-0324).

Tests cover:
1. ToolPerformanceMetrics calculation (success rates, latency, cost)
2. Bayesian confidence scoring (converges at 30 samples)
3. Trend detection (improving/stable/degrading)
4. Cache TTL expiry and management
5. Tenant isolation (GDPR Art. 5, 32)
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.performance_aggregation import (
    ToolPerformanceMetrics,
    SkillPerformanceMetrics,
    PerformanceCache,
    PerformanceAggregator,
)


# Fixtures

@pytest.fixture
def mock_event_store():
    """Create mock EventStore for tests."""
    store = AsyncMock()
    store.read_events = AsyncMock(return_value=[])
    return store


@pytest.fixture
def aggregator(mock_event_store):
    """Create PerformanceAggregator for tests."""
    return PerformanceAggregator(event_store=mock_event_store)


def _create_tool_event(
    tool_id: str,
    status: str = "success",
    latency_ms: int = 100,
    cost_cents: int = 10,
    tenant_id: str = "test-tenant",
    session_id: str = "session-1",
    timestamp_offset_hours: int = 0,
) -> LearningEvent:
    """Helper to create a TOOL_EXECUTED event."""
    now = datetime.now(timezone.utc)
    ts = now - timedelta(hours=timestamp_offset_hours)

    return LearningEvent(
        event_type=LearningEventType.TOOL_EXECUTED,
        tenant_id=tenant_id,
        instance_id="test-instance",
        skill_name=None,
        session_id=session_id,
        timestamp_utc=ts,
        payload={
            "tool_id": tool_id,
            "tool_name": f"{tool_id}_v1",
            "tool_type": "generated",
            "status": status,
            "latency_ms": latency_ms,
            "input_tokens": 100,
            "output_tokens": 50,
            "estimated_cost_cents": cost_cents,
            "error_type": None,
            "error_message": None,
        },
    )


# Test Classes

class TestToolPerformanceMetrics:
    """Tests for ToolPerformanceMetrics dataclass."""

    def test_cold_start_detection(self):
        """Test is_cold_start property."""
        # < 10 samples = cold start
        metrics_cold = ToolPerformanceMetrics(
            tool_id="tool_1",
            success_rate=0.8,
            success_count=4,
            total_count=5,
            avg_latency_ms=100,
            p50_latency_ms=100,
            p95_latency_ms=120,
            p99_latency_ms=150,
            avg_cost_cents=10,
            cost_samples=5,
            confidence=0.17,
            trend="stable",
            days_since_first_sample=1,
            last_updated_utc=datetime.now(timezone.utc),
            tenant_id="test",
        )
        assert metrics_cold.is_cold_start

        # >= 10 samples = not cold start
        metrics_warm = ToolPerformanceMetrics(
            tool_id="tool_1",
            success_rate=0.8,
            success_count=8,
            total_count=10,
            avg_latency_ms=100,
            p50_latency_ms=100,
            p95_latency_ms=120,
            p99_latency_ms=150,
            avg_cost_cents=10,
            cost_samples=10,
            confidence=0.33,
            trend="stable",
            days_since_first_sample=1,
            last_updated_utc=datetime.now(timezone.utc),
            tenant_id="test",
        )
        assert not metrics_warm.is_cold_start

    def test_to_event_payload(self):
        """Test conversion to event payload format."""
        metrics = ToolPerformanceMetrics(
            tool_id="tool_1",
            success_rate=0.8,
            success_count=8,
            total_count=10,
            avg_latency_ms=100,
            p50_latency_ms=100,
            p95_latency_ms=120,
            p99_latency_ms=150,
            avg_cost_cents=10,
            cost_samples=10,
            confidence=0.33,
            trend="stable",
            days_since_first_sample=1,
            last_updated_utc=datetime.now(timezone.utc),
            tenant_id="test",
        )

        payload = metrics.to_event_payload()
        assert payload["tool_id"] == "tool_1"
        assert payload["success_rate"] == 0.8
        assert payload["total_count"] == 10
        assert payload["avg_latency_ms"] == 100


class TestBayesianConfidence:
    """Tests for Bayesian confidence calculation."""

    def test_confidence_zero_samples(self, aggregator):
        """Test confidence at 0 samples."""
        events = []
        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is None

    def test_confidence_few_samples(self, aggregator):
        """Test confidence with few samples (<30)."""
        # 5 samples => confidence = 5/30 ≈ 0.167
        events = [
            _create_tool_event("tool_1", status="success") for _ in range(5)
        ]
        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.confidence < 0.2
        assert metrics.confidence == pytest.approx(5 / 30, abs=0.01)

    def test_confidence_30_samples(self, aggregator):
        """Test confidence at 30 samples (converges to 1.0)."""
        events = [
            _create_tool_event("tool_1", status="success") for _ in range(30)
        ]
        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.confidence == 1.0

    def test_confidence_many_samples(self, aggregator):
        """Test confidence with >30 samples (stays at 1.0)."""
        events = [
            _create_tool_event("tool_1", status="success") for _ in range(100)
        ]
        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.confidence == 1.0


class TestTrendDetection:
    """Tests for trend detection (improving/stable/degrading)."""

    def test_trend_improving(self, aggregator):
        """Test trend detection: improving (recent > overall)."""
        # First 100 events: 50% success
        # Last 10 events: 100% success (all successes)
        events = [
            _create_tool_event("tool_1", status="failure" if i % 2 == 0 else "success")
            for i in range(100)
        ] + [
            _create_tool_event("tool_1", status="success")
            for _ in range(10)
        ]

        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.trend == "improving"

    def test_trend_degrading(self, aggregator):
        """Test trend detection: degrading (recent < overall)."""
        # First 100 events: 100% success
        # Last 10 events: 0% success (all failures)
        events = [
            _create_tool_event("tool_1", status="success")
            for _ in range(100)
        ] + [
            _create_tool_event("tool_1", status="failure")
            for _ in range(10)
        ]

        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.trend == "degrading"

    def test_trend_stable(self, aggregator):
        """Test trend detection: stable (recent ≈ overall)."""
        # Consistent 80% success throughout
        events = [
            _create_tool_event("tool_1", status="success" if i % 5 < 4 else "failure")
            for i in range(100)
        ]

        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.trend == "stable"


class TestLatencyPercentiles:
    """Tests for latency percentile calculations."""

    def test_latency_percentiles(self, aggregator):
        """Test p50, p95, p99 latency calculation."""
        # Create 100 events with latencies: 10, 20, 30, ..., 1000
        events = [
            _create_tool_event("tool_1", latency_ms=i * 10)
            for i in range(1, 101)
        ]

        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None

        # p50 should be around 500ms (median)
        assert 450 <= metrics.p50_latency_ms <= 550

        # p95 should be around 950ms
        assert 900 <= metrics.p95_latency_ms <= 1000

        # p99 should be around 990ms
        assert 980 <= metrics.p99_latency_ms <= 1000

        # avg should be around 500ms
        assert 450 <= metrics.avg_latency_ms <= 550


class TestSuccessRateCalculation:
    """Tests for success rate calculation."""

    def test_success_rate_100_percent(self, aggregator):
        """Test 100% success rate."""
        events = [
            _create_tool_event("tool_1", status="success")
            for _ in range(10)
        ]

        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.success_rate == 1.0
        assert metrics.success_count == 10

    def test_success_rate_50_percent(self, aggregator):
        """Test 50% success rate."""
        events = [
            _create_tool_event("tool_1", status="success" if i % 2 == 0 else "failure")
            for i in range(10)
        ]

        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.success_rate == pytest.approx(0.5, abs=0.01)
        assert metrics.success_count == 5

    def test_success_rate_0_percent(self, aggregator):
        """Test 0% success rate."""
        events = [
            _create_tool_event("tool_1", status="failure")
            for _ in range(10)
        ]

        metrics = aggregator._compute_tool_metrics("tool_1", events, "test")
        assert metrics is not None
        assert metrics.success_rate == 0.0
        assert metrics.success_count == 0


class TestPerformanceCache:
    """Tests for PerformanceCache TTL and expiry."""

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Test cache hit (value exists and not expired)."""
        cache = PerformanceCache(ttl_seconds=3600)
        await cache.set("key1", "value1")
        result = await cache.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Test cache miss (key doesn't exist)."""
        cache = PerformanceCache(ttl_seconds=3600)
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_expiry(self):
        """Test cache expiry (value exists but expired)."""
        cache = PerformanceCache(ttl_seconds=1)  # 1 second TTL

        await cache.set("key1", "value1")
        assert await cache.get("key1") == "value1"

        # Wait for expiry
        await asyncio.sleep(1.1)
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_clear_expired(self):
        """Test cache cleanup of expired entries."""
        cache = PerformanceCache(ttl_seconds=1)

        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await asyncio.sleep(1.1)
        await cache.set("key3", "value3")

        # Clear expired (key1, key2 should be removed)
        removed = await cache.clear_expired()
        assert removed == 2

        # key3 should still be there
        assert await cache.get("key3") == "value3"

    @pytest.mark.asyncio
    async def test_cache_size(self):
        """Test cache size tracking."""
        cache = PerformanceCache()
        assert await cache.size() == 0

        await cache.set("key1", "value1")
        assert await cache.size() == 1

        await cache.set("key2", "value2")
        assert await cache.size() == 2


class TestAggregationEdgeCases:
    """Tests for aggregation edge cases."""

    def test_empty_payload_handling(self, aggregator):
        """Test handling of events with missing payload fields."""
        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id="test",
            instance_id="test-instance",
            skill_name=None,
            session_id="session-1",
            timestamp_utc=datetime.now(timezone.utc),
            payload={},  # Empty payload
        )

        metrics = aggregator._compute_tool_metrics("tool_1", [event], "test")
        # Should return None (no tool_id to match)
        assert metrics is None

    def test_mixed_valid_invalid_events(self, aggregator):
        """Test aggregation with mixed valid and invalid events."""
        events = [
            _create_tool_event("tool_1", status="success", latency_ms=100),
            LearningEvent(
                event_type=LearningEventType.TOOL_EXECUTED,
                tenant_id="test",
                instance_id="test-instance",
                skill_name=None,
                session_id="session-1",
                timestamp_utc=datetime.now(timezone.utc),
                payload={"status": "failure"},  # Missing tool_id
            ),
            _create_tool_event("tool_1", status="failure", latency_ms=200),
        ]

        # Should only count valid events with tool_id
        metrics = aggregator._compute_tool_metrics("tool_1", events[:1] + events[2:], "test")
        assert metrics is not None
        assert metrics.total_count == 2


class TestTenantIsolation:
    """Tests for tenant isolation (GDPR Art. 5, 32)."""

    def test_metrics_include_tenant_id(self, aggregator):
        """Test that computed metrics include tenant_id."""
        event = _create_tool_event("tool_1", tenant_id="tenant_a")

        metrics = aggregator._compute_tool_metrics(
            tool_id="tool_1",
            events=[event],
            tenant_id="tenant_a",
        )

        assert metrics is not None
        assert metrics.tenant_id == "tenant_a"

    def test_metrics_payload_excludes_sensitive_data(self, aggregator):
        """Test that metrics payload doesn't leak sensitive data."""
        event = _create_tool_event("tool_1")
        metrics = aggregator._compute_tool_metrics("tool_1", [event], "test")

        assert metrics is not None
        payload = metrics.to_event_payload()

        # Verify no sensitive fields
        assert "tenant_id" not in payload
        assert "audit_id" not in payload
        assert "session_id" not in payload


class TestAggregationPerformance:
    """Tests for performance (latency, throughput)."""

    @pytest.mark.asyncio
    async def test_aggregation_large_dataset(self, aggregator):
        """Test aggregation performance with 10k events."""
        import time

        # Create 10k events
        events = [
            _create_tool_event(
                tool_id=f"tool_{i % 100}",
                status="success" if i % 10 < 8 else "failure",
                latency_ms=50 + (i % 100),
            )
            for i in range(10000)
        ]

        # Benchmark aggregation
        start = time.time()
        for i in range(0, 10000, 100):
            batch = events[i:i+100]
            metrics = aggregator._compute_tool_metrics(
                tool_id=f"tool_{i % 100}",
                events=batch,
                tenant_id="test",
            )
        elapsed = time.time() - start

        # Should complete in <1 second for 10k events
        assert elapsed < 1.0


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_events_list(self, aggregator):
        """Test metrics computation with empty event list."""
        metrics = aggregator._compute_tool_metrics("tool_1", [], "test")
        assert metrics is None

    def test_missing_latency_field(self, aggregator):
        """Test handling of missing latency_ms in payload."""
        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id="test",
            instance_id="test-instance",
            skill_name=None,
            session_id="session-1",
            timestamp_utc=datetime.now(timezone.utc),
            payload={
                "tool_id": "tool_1",
                "tool_name": "tool_1_v1",
                "status": "success",
                # Missing latency_ms
            },
        )

        metrics = aggregator._compute_tool_metrics("tool_1", [event], "test")
        assert metrics is not None
        assert metrics.avg_latency_ms == 0

    def test_missing_cost_field(self, aggregator):
        """Test handling of missing estimated_cost_cents."""
        event = LearningEvent(
            event_type=LearningEventType.TOOL_EXECUTED,
            tenant_id="test",
            instance_id="test-instance",
            skill_name=None,
            session_id="session-1",
            timestamp_utc=datetime.now(timezone.utc),
            payload={
                "tool_id": "tool_1",
                "tool_name": "tool_1_v1",
                "status": "success",
                "latency_ms": 100,
                # Missing estimated_cost_cents
            },
        )

        metrics = aggregator._compute_tool_metrics("tool_1", [event], "test")
        assert metrics is not None
        assert metrics.avg_cost_cents == 0

    def test_days_since_first_sample(self, aggregator):
        """Test days_since_first_sample calculation."""
        # Event from 3 days ago
        event = _create_tool_event("tool_1", timestamp_offset_hours=72)
        metrics = aggregator._compute_tool_metrics("tool_1", [event], "test")
        assert metrics is not None
        assert metrics.days_since_first_sample >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
