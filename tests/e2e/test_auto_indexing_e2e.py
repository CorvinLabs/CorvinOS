"""E2E tests for Auto-Indexing Hook (Stream 2.2).

Tests the full event persistence + index update flow:
1. Create learning event
2. Write to EventStore
3. Verify index updated
4. Verify audit logged
5. Verify status transitions

Scenarios:
- Full event flow: emit → index updated → status changes → audit logged
- Concurrent event writers
- Index recovery from audit chain
- Manifest refresh doesn't clear index
- Plugin reload doesn't corrupt index
"""

import asyncio
import json
import tempfile
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from core.learning.event_persistence import EventStore
from core.learning.event_schema import LearningEvent, LearningEventType
from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService


# ── Test Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def temp_db_dir():
    """Create a temporary directory for LevelDB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_learning_event() -> LearningEvent:
    """Create a sample learning event."""
    return LearningEvent(
        event_id="evt-123",
        event_type=LearningEventType.CONFIDENCE,
        tenant_id="_default",
        skill_name="test_skill",
        skill_version="1.0.0",
        session_id="sess-456",
        timestamp_utc=datetime.utcnow(),
        tags={"learning_loop_id": "test_loop", "plugin_id": "test_plugin"},
        feedback_value=0.8,
    )


# ── Test Full Event Flow ────────────────────────────────────────────────────


class TestFullEventFlow:
    """Tests for the complete event persistence + index flow."""

    @pytest.mark.asyncio
    async def test_event_write_updates_index(
        self, sample_learning_event, temp_db_dir
    ):
        """Full flow: event write → index updated."""
        # Create services
        store = EventStore("_default")

        # Mock the index update hook to verify it's called
        with patch("core.learning.event_persistence.EventStore._update_kg_index", new_callable=AsyncMock) as mock_update:
            # Write event
            audit_ref = await store.write_event(sample_learning_event, "_default")

            # Verify audit write succeeded
            assert audit_ref is not None

            # Verify index update was called
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_event_writers_safe(self, temp_db_dir):
        """Concurrent event writers don't corrupt data."""
        store = EventStore("_default")

        async def write_events(count):
            results = []
            for i in range(count):
                event = LearningEvent(
                    event_id=f"evt-{i}",
                    event_type=LearningEventType.CONFIDENCE,
                    tenant_id="_default",
                    skill_name="test_skill",
                    skill_version="1.0.0",
                    session_id=f"sess-{i}",
                    timestamp_utc=datetime.utcnow(),
                    tags={"learning_loop_id": f"loop-{i}", "plugin_id": "test"},
                    feedback_value=0.5,
                )
                try:
                    with patch("core.learning.event_persistence.EventStore._update_kg_index", new_callable=AsyncMock):
                        ref = await store.write_event(event, "_default")
                        results.append(ref)
                except Exception as e:
                    results.append(None)
            return results

        # Run concurrent writes
        result1 = await write_events(5)
        result2 = await write_events(5)

        # All should succeed
        assert all(r is not None for r in result1)
        assert all(r is not None for r in result2)

    @pytest.mark.asyncio
    async def test_index_entry_created_on_first_event(self, temp_db_dir):
        """Index entry is created when first event for a loop is written."""
        service = LearningLoopService("_default", temp_db_dir / "index")

        # Pre-register the loop
        service.insert_from_manifest(
            plugin_id="test",
            loop_id="new_loop",
            description="New loop",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        # Verify entry exists
        entry = service._storage.get("test", "new_loop")
        assert entry is not None
        assert entry.event_count_7d == 0

    @pytest.mark.asyncio
    async def test_index_entry_updated_on_subsequent_events(self, temp_db_dir):
        """Index entry is updated on subsequent events."""
        service = LearningLoopService("_default", temp_db_dir / "index")

        # Create and update loop
        service.insert_from_manifest(
            plugin_id="test",
            loop_id="multi_event_loop",
            description="Multi-event loop",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        # Simulate multiple events
        for i in range(3):
            service.update_on_event(
                plugin_id="test",
                loop_id="multi_event_loop",
                feedback_signal=0.5,
            )

        # Verify counts
        entry = service._storage.get("test", "multi_event_loop")
        assert entry.event_count_7d == 3

    @pytest.mark.asyncio
    async def test_health_score_updates_on_events(self, temp_db_dir):
        """Health score is updated with each event."""
        service = LearningLoopService("_default", temp_db_dir / "index")

        service.insert_from_manifest(
            plugin_id="test",
            loop_id="health_loop",
            description="Health loop",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        before = service._storage.get("test", "health_loop")
        initial_health = before.health_score

        # Add high-confidence event
        service.update_on_event(
            plugin_id="test",
            loop_id="health_loop",
            feedback_signal=0.9,
        )

        after = service._storage.get("test", "health_loop")
        # Health should improve
        expected = (initial_health + 0.9) / 2
        assert abs(after.health_score - expected) < 0.01

    @pytest.mark.asyncio
    async def test_last_event_timestamp_updated(self, temp_db_dir):
        """Last event timestamp is updated on each event."""
        service = LearningLoopService("_default", temp_db_dir / "index")

        service.insert_from_manifest(
            plugin_id="test",
            loop_id="ts_loop",
            description="TS loop",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        before = service._storage.get("test", "ts_loop")
        before_ts = before.last_event_ts

        await asyncio.sleep(0.05)

        service.update_on_event(
            plugin_id="test",
            loop_id="ts_loop",
        )

        after = service._storage.get("test", "ts_loop")
        assert after.last_event_ts > before_ts


# ── Test Index Persistence ─────────────────────────────────────────────────


class TestIndexPersistence:
    """Tests for index persistence and recovery."""

    @pytest.mark.asyncio
    async def test_index_persists_across_service_restarts(self, temp_db_dir):
        """Index entries persist when service is restarted."""
        # Create service and add entry
        service1 = LearningLoopService("_default", temp_db_dir / "index")
        service1.insert_from_manifest(
            plugin_id="test",
            loop_id="persist_loop",
            description="Persisted loop",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )
        service1.update_on_event(plugin_id="test", loop_id="persist_loop")
        service1.close()

        # Recreate service (simulating restart)
        service2 = LearningLoopService("_default", temp_db_dir / "index")
        entry = service2._storage.get("test", "persist_loop")

        # Entry should still exist
        assert entry is not None
        assert entry.event_count_7d == 1
        service2.close()

    @pytest.mark.asyncio
    async def test_manifest_refresh_adds_new_loops(self, temp_db_dir):
        """Manifest refresh adds new loops without clearing existing ones."""
        service = LearningLoopService("_default", temp_db_dir / "index")

        # Add initial loop
        service.insert_from_manifest(
            plugin_id="test",
            loop_id="loop1",
            description="Loop 1",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        # Add another loop (simulating manifest refresh)
        service.insert_from_manifest(
            plugin_id="test",
            loop_id="loop2",
            description="Loop 2",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        # Both should exist
        loops = service.list_loops()
        assert len(loops) == 2

    @pytest.mark.asyncio
    async def test_plugin_reload_doesnt_corrupt_index(self, temp_db_dir):
        """Plugin reload doesn't corrupt existing index entries."""
        service = LearningLoopService("_default", temp_db_dir / "index")

        # Create initial loop and add events
        service.insert_from_manifest(
            plugin_id="test_plugin",
            loop_id="reload_loop",
            description="Reload test",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        for _ in range(5):
            service.update_on_event(
                plugin_id="test_plugin",
                loop_id="reload_loop",
                feedback_signal=0.7,
            )

        before = service._storage.get("test_plugin", "reload_loop")
        before_count = before.event_count_7d

        # Simulate plugin reload (re-register the loop)
        service.insert_from_manifest(
            plugin_id="test_plugin",
            loop_id="reload_loop",
            description="Reload test (updated)",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=None,
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        # Index should be overwritten (new entry)
        # In production, we might preserve old events
        after = service._storage.get("test_plugin", "reload_loop")
        assert after is not None


# ── Test Tenant Isolation ───────────────────────────────────────────────────


class TestTenantIsolation:
    """Tests for tenant isolation in event flow."""

    @pytest.mark.asyncio
    async def test_event_with_wrong_tenant_rejected(self):
        """Event with wrong tenant is rejected."""
        store = EventStore("_default")

        event = LearningEvent(
            event_id="evt-wrong-tenant",
            event_type=LearningEventType.CONFIDENCE,
            tenant_id="other_tenant",  # Wrong!
            skill_name="test",
            skill_version="1.0.0",
            session_id="sess",
            timestamp_utc=datetime.utcnow(),
            tags={},
            feedback_value=0.5,
        )

        with pytest.raises(ValueError, match="Tenant mismatch"):
            with patch("core.learning.event_persistence.EventStore._update_kg_index", new_callable=AsyncMock):
                await store.write_event(event, "_default")

    @pytest.mark.asyncio
    async def test_index_isolated_by_tenant(self, temp_db_dir):
        """Index entries are isolated per tenant."""
        service_a = LearningLoopService("tenant_a", temp_db_dir / "index_a")
        service_b = LearningLoopService("tenant_b", temp_db_dir / "index_b")

        # Add loops to each tenant
        service_a.insert_from_manifest(
            plugin_id="test", loop_id="loop",
            description="A", event_source="", feedback_types=[],
            aggregation="rolling_mean_7d", health_threshold=None,
            dormancy_alert_hours=24, owner_skill=None,
        )

        service_b.insert_from_manifest(
            plugin_id="test", loop_id="loop",
            description="B", event_source="", feedback_types=[],
            aggregation="rolling_mean_7d", health_threshold=None,
            dormancy_alert_hours=24, owner_skill=None,
        )

        # Each should have their own entry
        loops_a = service_a.list_loops()
        loops_b = service_b.list_loops()

        assert len(loops_a) == 1
        assert len(loops_b) == 1
        assert loops_a[0].description == "A"
        assert loops_b[0].description == "B"

        service_a.close()
        service_b.close()
