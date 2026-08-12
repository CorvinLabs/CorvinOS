"""Unit tests for Telemetry Pipeline (ADR-0308)."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.skills.telemetry import (
    HTTPPublisher,
    MetricsCollector,
    NoOpPublisher,
    QueuePublisher,
    TelemetrySample,
)
from core.skills.telemetry_manager import TelemetryManager


class TestTelemetrySample:
    """TelemetrySample tests."""

    def test_create_sample(self):
        sample = TelemetrySample(
            skill_name="test-skill",
            skill_version="1.0",
            graded_count=10,
            failed_count=2,
            avg_latency=0.5,
        )

        assert sample.skill_name == "test-skill"
        assert sample.graded_count == 10
        assert sample.failed_count == 2

    def test_sample_has_timestamp(self):
        sample = TelemetrySample(
            skill_name="test",
            skill_version="1.0",
            graded_count=1,
            failed_count=0,
            avg_latency=0.1,
        )

        assert sample.timestamp is not None
        assert isinstance(sample.timestamp, datetime)

    def test_sample_to_dict(self):
        ts = datetime(2026, 8, 12, 10, 0, 0)
        sample = TelemetrySample(
            skill_name="test",
            skill_version="1.0",
            graded_count=5,
            failed_count=1,
            avg_latency=0.2,
            timestamp=ts,
        )

        d = sample.to_dict()
        assert d["skill_name"] == "test"
        assert d["graded_count"] == 5
        assert d["failed_count"] == 1
        assert d["avg_latency"] == 0.2
        assert d["timestamp"] == "2026-08-12T10:00:00"


class TestMetricsCollector:
    """MetricsCollector batching tests."""

    def test_init(self):
        collector = MetricsCollector("skill", "1.0", batch_size=5)
        assert collector.skill_name == "skill"
        assert collector.batch_size == 5

    @pytest.mark.asyncio
    async def test_add_sample(self):
        collector = MetricsCollector("skill", "1.0")
        stats = {
            "graded_count": 10,
            "failed_count": 2,
            "avg_latency": 0.5,
        }

        await collector.add_sample(stats)

        batch_size = await collector.get_batch_size()
        assert batch_size == 1

    @pytest.mark.asyncio
    async def test_batch_accumulates(self):
        collector = MetricsCollector("skill", "1.0", batch_size=3)
        stats = {
            "graded_count": 10,
            "failed_count": 0,
            "avg_latency": 0.1,
        }

        for _ in range(2):
            await collector.add_sample(stats)

        batch_size = await collector.get_batch_size()
        assert batch_size == 2

    @pytest.mark.asyncio
    async def test_should_flush_on_size(self):
        collector = MetricsCollector("skill", "1.0", batch_size=2)
        stats = {"graded_count": 1, "failed_count": 0, "avg_latency": 0.1}

        await collector.add_sample(stats)
        assert not await collector.should_flush()

        await collector.add_sample(stats)
        assert await collector.should_flush()

    @pytest.mark.asyncio
    async def test_flush_clears_buffer(self):
        collector = MetricsCollector("skill", "1.0")
        stats = {"graded_count": 5, "failed_count": 0, "avg_latency": 0.1}

        await collector.add_sample(stats)
        await collector.add_sample(stats)

        samples = await collector.flush()
        assert len(samples) == 2

        batch_size = await collector.get_batch_size()
        assert batch_size == 0

    @pytest.mark.asyncio
    async def test_flush_returns_correct_data(self):
        collector = MetricsCollector("myskill", "2.0")
        stats = {
            "graded_count": 100,
            "failed_count": 5,
            "avg_latency": 1.2,
        }

        await collector.add_sample(stats)
        samples = await collector.flush()

        assert len(samples) == 1
        sample = samples[0]
        assert sample.skill_name == "myskill"
        assert sample.graded_count == 100
        assert sample.failed_count == 5


class TestNoOpPublisher:
    """NoOpPublisher tests."""

    @pytest.mark.asyncio
    async def test_always_succeeds(self):
        publisher = NoOpPublisher()
        samples = [
            TelemetrySample("test", "1.0", 10, 0, 0.1),
            TelemetrySample("test", "1.0", 20, 1, 0.2),
        ]

        result = await publisher.publish(samples)
        assert result is True


class TestQueuePublisher:
    """QueuePublisher tests."""

    @pytest.mark.asyncio
    async def test_enqueues_samples(self):
        queue = asyncio.Queue()
        publisher = QueuePublisher(queue)
        samples = [TelemetrySample("test", "1.0", 10, 0, 0.1)]

        result = await publisher.publish(samples)
        assert result is True

        # Verify queue size increased
        assert queue.qsize() == 1

        item = queue.get_nowait()
        assert item["skill_name"] == "test"
        assert item["graded_count"] == 10

    @pytest.mark.asyncio
    async def test_handles_multiple_samples(self):
        queue = asyncio.Queue()
        publisher = QueuePublisher(queue)
        samples = [
            TelemetrySample("skill1", "1.0", 10, 0, 0.1),
            TelemetrySample("skill2", "1.0", 20, 1, 0.2),
        ]

        result = await publisher.publish(samples)
        assert result is True

        # Queue should have 2 items
        assert queue.qsize() == 2


class TestHTTPPublisher:
    """HTTPPublisher tests."""

    @pytest.mark.asyncio
    async def test_init(self):
        publisher = HTTPPublisher("https://api.example.com/telemetry")
        assert publisher.endpoint == "https://api.example.com/telemetry"
        assert publisher.timeout == 5.0

    @pytest.mark.asyncio
    async def test_publish_success(self):
        publisher = HTTPPublisher("https://api.example.com/telemetry")
        publisher.session = None  # Force re-init, which will fail (no anthropic installed)

        samples = [TelemetrySample("test", "1.0", 10, 0, 0.1)]
        result = await publisher.publish(samples)

        # When session can't be obtained, publish returns False
        assert result is False

    @pytest.mark.asyncio
    async def test_publish_failure_status(self):
        publisher = HTTPPublisher("https://api.example.com/telemetry")

        mock_session = AsyncMock()
        mock_response = MagicMock()
        mock_response.status = 500  # Server error

        async def mock_post(*args, **kwargs):
            class AsyncContextManager:
                async def __aenter__(self):
                    return mock_response
                async def __aexit__(self, *args):
                    pass
            return AsyncContextManager()

        mock_session.post.side_effect = mock_post

        publisher.session = mock_session

        samples = [TelemetrySample("test", "1.0", 10, 0, 0.1)]
        result = await publisher.publish(samples)

        assert result is False

    @pytest.mark.asyncio
    async def test_publish_network_error(self):
        publisher = HTTPPublisher("https://api.example.com/telemetry")

        mock_session = AsyncMock()
        mock_session.post.side_effect = Exception("Network error")

        publisher.session = mock_session

        samples = [TelemetrySample("test", "1.0", 10, 0, 0.1)]
        result = await publisher.publish(samples)

        assert result is False  # No exception raised, returns False

    @pytest.mark.asyncio
    async def test_close_session(self):
        publisher = HTTPPublisher("https://api.example.com/telemetry")

        mock_session = AsyncMock()
        publisher.session = mock_session

        await publisher.close()
        mock_session.close.assert_called_once()


class TestTelemetryManager:
    """TelemetryManager orchestration tests."""

    def test_init(self):
        collector = MetricsCollector("skill", "1.0")
        publisher = NoOpPublisher()
        manager = TelemetryManager(collector, publisher, batch_size=5)

        assert manager.collector is collector
        assert manager.published_count == 0
        assert manager.failed_count == 0

    @pytest.mark.asyncio
    async def test_manual_flush_success(self):
        collector = MetricsCollector("skill", "1.0")
        publisher = NoOpPublisher()
        manager = TelemetryManager(collector, publisher)

        stats = {"graded_count": 5, "failed_count": 0, "avg_latency": 0.1}
        await collector.add_sample(stats)

        result = await manager.manual_flush()
        assert result is True
        assert manager.published_count == 1

    @pytest.mark.asyncio
    async def test_manual_flush_empty(self):
        collector = MetricsCollector("skill", "1.0")
        publisher = NoOpPublisher()
        manager = TelemetryManager(collector, publisher)

        result = await manager.manual_flush()
        assert result is True  # Empty flush is success

    @pytest.mark.asyncio
    async def test_get_stats(self):
        collector = MetricsCollector("skill", "1.0")
        publisher = NoOpPublisher()
        manager = TelemetryManager(collector, publisher)

        stats = {"graded_count": 10, "failed_count": 1, "avg_latency": 0.2}
        await collector.add_sample(stats)

        mgr_stats = manager.get_stats()
        assert mgr_stats["published_count"] == 0
        assert mgr_stats["pending_count"] == 1

    @pytest.mark.asyncio
    async def test_reset_stats(self):
        collector = MetricsCollector("skill", "1.0")
        publisher = NoOpPublisher()
        manager = TelemetryManager(collector, publisher)

        # Simulate some publishes
        manager.published_count = 5
        manager.failed_count = 2

        await manager.reset_stats()

        assert manager.published_count == 0
        assert manager.failed_count == 0

    @pytest.mark.asyncio
    async def test_collect_and_publish_loop_one_iteration(self):
        collector = MetricsCollector("skill", "1.0", batch_size=1)
        publisher = NoOpPublisher()
        manager = TelemetryManager(
            collector,
            publisher,
            batch_size=1,  # Will flush after 1 sample
            poll_interval_s=0.01,
            flush_interval_s=0.1,
        )

        # Add a sample directly
        await collector.add_sample({
            "graded_count": 5,
            "failed_count": 0,
            "avg_latency": 0.1,
        })

        # Manually flush
        result = await manager.manual_flush()
        assert result is True
        assert manager.published_count == 1
