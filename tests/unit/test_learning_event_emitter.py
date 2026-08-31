"""Tests for Event Emitter (ADR-0314)."""

import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import LearningEvent, LearningEventType


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home


class TestEventEmitter:
    """Test EventEmitter (non-blocking event emission)."""

    @pytest.mark.asyncio
    async def test_create_emitter(self, temp_tenant_home):
        """Create EventEmitter instance."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        assert emitter.tenant_id == "_default"
        assert emitter.event_queue is not None

    @pytest.mark.asyncio
    async def test_emit_event_non_blocking(self, temp_tenant_home):
        """Emit event (non-blocking, returns immediately)."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
            payload={"relevance_score": 0.95},
        )

        # Should not block
        await emitter.emit(event)

        # Event should be queued
        assert emitter.event_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_emit_event_tenant_isolation(self, temp_tenant_home):
        """Emit event with wrong tenant raises error."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="other-tenant",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
            payload={},
        )

        with pytest.raises(ValueError, match="Tenant mismatch"):
            await emitter.emit(event)

    @pytest.mark.asyncio
    async def test_start_and_stop_emitter(self, temp_tenant_home):
        """Start/stop the event processing worker."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()
        assert emitter._worker_task is not None

        await emitter.stop()
        assert emitter._worker_task is None

    @pytest.mark.asyncio
    async def test_emit_and_persist(self, temp_tenant_home):
        """Emit event and verify it persists via worker."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
            payload={"relevance_score": 0.95},
        )

        await emitter.emit(event)
        await emitter.flush()

        await emitter.stop()

        # Verify event was persisted
        persisted = await emitter.read_events()
        assert len(persisted) == 1
        assert persisted[0].event_type == LearningEventType.CONFIDENCE_SCORE

    @pytest.mark.asyncio
    async def test_queue_full_drops_events(self, temp_tenant_home):
        """Queue full → event dropped (fire-and-forget)."""
        emitter = EventEmitter(temp_tenant_home, "_default", max_queue_size=1)

        # First event goes in
        event1 = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-1",
            timestamp_utc=datetime.utcnow(),
            payload={},
        )
        await emitter.emit(event1)
        assert emitter.event_queue.qsize() == 1

        # Second event dropped (queue full)
        event2 = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-2",
            timestamp_utc=datetime.utcnow(),
            payload={},
        )
        await emitter.emit(event2)
        assert emitter.event_queue.qsize() == 1  # Still 1 (dropped)

    @pytest.mark.asyncio
    async def test_get_event_count(self, temp_tenant_home):
        """Get persisted event count."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        for i in range(3):
            event = LearningEvent(
                event_type=LearningEventType.CONFIDENCE_SCORE,
                tenant_id="_default",
                instance_id="console-1",
                skill_name="ranking",
                session_id=f"session-{i}",
                timestamp_utc=datetime.utcnow(),
                payload={},
            )
            await emitter.emit(event)

        await emitter.flush()
        await emitter.stop()

        count = await emitter.get_event_count()
        assert count == 3

    @pytest.mark.asyncio
    async def test_read_events_filtered(self, temp_tenant_home):
        """Read persisted events with filtering."""
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
            payload={},
        )

        await emitter.emit(event)
        await emitter.flush()
        await emitter.stop()

        # Read with filter
        events = await emitter.read_events(skill_name="ranking")
        assert len(events) == 1
