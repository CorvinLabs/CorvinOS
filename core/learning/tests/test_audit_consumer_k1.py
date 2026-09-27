"""k=1 Tests: AuditEventConsumer — Batch-Aggregation (Tier-1/2)."""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta

from core.learning.audit_consumer import (
    AuditEventConsumer,
    AuditEventSummary,
    AggregatedAuditWindow,
)


class TestAuditEventConsumerBasics:
    """Tier-1: Unit tests for AuditEventConsumer."""

    def test_consumer_init(self):
        """AuditEventConsumer initializes with sensible defaults."""
        consumer = AuditEventConsumer()
        assert consumer.batch_size == 100
        assert consumer.window_seconds == 300
        assert isinstance(consumer._processed_hashes, set)

    def test_consumer_batch_size_clamped(self):
        """Batch size is clamped to [10, 10000]."""
        consumer_small = AuditEventConsumer(batch_size=1)
        assert consumer_small.batch_size == 10

        consumer_large = AuditEventConsumer(batch_size=50000)
        assert consumer_large.batch_size == 10000

    def test_consumer_window_seconds_clamped(self):
        """Window seconds is clamped to [30, 3600]."""
        consumer_small = AuditEventConsumer(window_seconds=1)
        assert consumer_small.window_seconds == 30

        consumer_large = AuditEventConsumer(window_seconds=10000)
        assert consumer_large.window_seconds == 3600

    def test_audit_event_summary_creation(self):
        """AuditEventSummary creates correctly."""
        event = AuditEventSummary(
            event_id="evt-123",
            event_type="task_created",
            task_id="task-456",
            tenant_id="_default",
            actor="user-1",
            action="create",
            timestamp="2026-09-27T12:00:00Z",
            chain_hash="abc123",
        )

        assert event.event_id == "evt-123"
        assert event.event_type == "task_created"
        assert event.task_id == "task-456"

    def test_aggregated_audit_window_creation(self):
        """AggregatedAuditWindow creates with statistics."""
        window = AggregatedAuditWindow(
            window_id="win_test_001",
            tenant_id="_default",
            event_count=100,
            event_types={"task_created": 50, "task_updated": 50},
            action_counts={"create": 50, "update": 50},
            actor_counts={"user-1": 100},
            latest_chain_hash="xyz789",
            window_start_ts="2026-09-27T12:00:00Z",
            window_end_ts="2026-09-27T12:05:00Z",
            duration_seconds=300.0,
            throughput_events_per_sec=0.333,
        )

        assert window.event_count == 100
        assert window.is_statistically_valid() is True

    def test_aggregated_audit_window_invalid_if_too_small(self):
        """Window with n < 10 is not statistically valid."""
        window = AggregatedAuditWindow(
            window_id="win_small",
            tenant_id="_default",
            event_count=5,
        )

        assert window.is_statistically_valid() is False

    def test_aggregated_audit_window_valid_at_threshold(self):
        """Window with n=10 is statistically valid."""
        window = AggregatedAuditWindow(
            window_id="win_threshold",
            tenant_id="_default",
            event_count=10,
        )

        assert window.is_statistically_valid() is True


class TestAuditEventAggregation:
    """Tier-1: Unit tests for event aggregation logic."""

    @pytest.mark.asyncio
    async def test_aggregate_into_window_valid(self):
        """Aggregating n=50 events produces valid window."""
        consumer = AuditEventConsumer()

        events = [
            AuditEventSummary(
                event_id=f"evt-{i}",
                event_type="task_created" if i % 2 == 0 else "task_updated",
                task_id=f"task-{i}",
                tenant_id="_default",
                actor="user-1",
                action="create" if i % 2 == 0 else "update",
                timestamp=f"2026-09-27T12:{i:02d}:00Z",
                chain_hash=f"hash_{i}",
            )
            for i in range(50)
        ]

        window = await consumer.aggregate_into_window(events)

        assert window is not None
        assert window.event_count == 50
        assert window.event_types["task_created"] == 25
        assert window.event_types["task_updated"] == 25
        assert window.action_counts["create"] == 25
        assert window.is_statistically_valid() is True

    @pytest.mark.asyncio
    async def test_aggregate_into_window_too_small(self):
        """Aggregating n < 10 returns None (not statistically valid)."""
        consumer = AuditEventConsumer()

        events = [
            AuditEventSummary(
                event_id=f"evt-{i}",
                event_type="task_created",
                task_id=f"task-{i}",
                tenant_id="_default",
                actor="user-1",
                action="create",
                timestamp=f"2026-09-27T12:{i:02d}:00Z",
                chain_hash=f"hash_{i}",
            )
            for i in range(5)  # Only 5 events
        ]

        window = await consumer.aggregate_into_window(events)

        assert window is None

    @pytest.mark.asyncio
    async def test_aggregate_into_window_empty(self):
        """Aggregating empty list returns None."""
        consumer = AuditEventConsumer()
        window = await consumer.aggregate_into_window([])
        assert window is None

    @pytest.mark.asyncio
    async def test_aggregate_calculates_throughput(self):
        """Window calculates throughput events/sec correctly."""
        consumer = AuditEventConsumer()

        # 100 events over 10 seconds = 10 events/sec
        base_time = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
        events = [
            AuditEventSummary(
                event_id=f"evt-{i}",
                event_type="task_created",
                task_id=f"task-{i}",
                tenant_id="_default",
                actor="user-1",
                action="create",
                timestamp=(base_time + timedelta(seconds=i / 10)).isoformat(),
                chain_hash=f"hash_{i}",
            )
            for i in range(100)
        ]

        window = await consumer.aggregate_into_window(events)

        assert window is not None
        assert window.throughput_events_per_sec == pytest.approx(10.0, rel=0.1)

    @pytest.mark.asyncio
    async def test_processed_events_are_not_reread(self, monkeypatch):
        """A second read returns nothing already consumed — also for rows
        without a chain hash (they used to collide on "" and be dropped)."""
        from core.task_tracking import service
        from core.task_tracking.models import ItemCreate

        monkeypatch.setattr(service, "chain_writer", lambda tid, et, d: None)  # no hash
        for i in range(3):
            service.create("_default", ItemCreate(title=f"t{i}", kind="task"), actor="user:alice")

        consumer = AuditEventConsumer()
        first = await consumer.read_unprocessed_events("_default")
        assert len(first) == 3
        assert {e.actor for e in first} == {"user"}  # actor kind, never the id
        assert await consumer.read_unprocessed_events("_default") == []


class TestAuditEventConsumerIntegration:
    """Tier-2: Integration tests (would need DB mock or fixture)."""

    @pytest.mark.asyncio
    async def test_read_unprocessed_events_empty_db(self):
        """Reading from empty DB returns empty list."""
        consumer = AuditEventConsumer()

        # Without a real DB fixture, this will return empty
        # (the function catches exceptions and returns [])
        events = await consumer.read_unprocessed_events("_nonexistent_tenant")

        assert events == []

    @pytest.mark.asyncio
    async def test_window_seconds_is_clamped(self):
        assert AuditEventConsumer(window_seconds=1).window_seconds == 30
        assert AuditEventConsumer(window_seconds=99999).window_seconds == 3600

    @pytest.mark.asyncio
    async def test_process_until_window_complete_timeout(self):
        """process_until_window_complete returns None if the window expires with no events."""
        consumer = AuditEventConsumer()
        consumer.window_seconds = 1  # below the constructor clamp, to keep the test fast

        window = await asyncio.wait_for(
            consumer.process_until_window_complete("_nonexistent_tenant"),
            timeout=5.0,
        )

        assert window is None


# Benchmark / Load test (optional, Tier-2+)
@pytest.mark.asyncio
async def test_aggregate_large_batch():
    """Stress test: aggregate 1000 events."""
    consumer = AuditEventConsumer(batch_size=1000)

    events = [
        AuditEventSummary(
            event_id=f"evt-{i}",
            event_type=f"task_{i % 5}",
            task_id=f"task-{i}",
            tenant_id="_default",
            actor=f"user-{i % 10}",
            action=f"action_{i % 3}",
            timestamp=f"2026-09-27T12:{i % 60:02d}:00Z",
            chain_hash=f"hash_{i}",
        )
        for i in range(1000)
    ]

    window = await consumer.aggregate_into_window(events)

    assert window is not None
    assert window.event_count == 1000
    assert window.is_statistically_valid() is True
