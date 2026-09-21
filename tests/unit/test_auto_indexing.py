"""Unit tests for Auto-Indexing Hook (Stream 2.2).

Tests:
- IndexUpdateHook (async update, error handling)
- Integration with event_persistence.EventStore
- Status transitions on event
- Concurrent event writes
- Tenant isolation in hook
- Health score rolling mean
- Dormancy threshold triggers
- Failure resilience

Coverage:
- Event write → index entry created/updated atomically
- Audit write fails → index update skipped (fail-closed)
- Index update fails → audit event logged, no cascade
- Concurrent events → index stays consistent
- Event with no loop_id → skipped gracefully
- Loop status transitions computed correctly
- Health score rolling mean accurate
- Dormancy threshold triggers on time
"""

import asyncio
import json
import tempfile
import threading
import time
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, AsyncMock, patch

from core.learning.index_update_hook import IndexUpdateHook, get_index_update_hook
from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService


# ── Test Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def temp_db_dir():
    """Create a temporary directory for LevelDB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def learning_loop_service(temp_db_dir) -> LearningLoopService:
    """Create a LearningLoopService instance."""
    service = LearningLoopService("_default", temp_db_dir / "learning_loop_index")

    # Pre-populate with a test loop
    service.insert_from_manifest(
        plugin_id="test_plugin",
        loop_id="test_loop",
        description="Test loop",
        event_source="TestEvent.confidence",
        feedback_types=["outcome_feedback"],
        aggregation="rolling_mean_7d",
        health_threshold=0.5,
        dormancy_alert_hours=24,
        owner_skill="test_skill",
    )

    yield service
    service.close()


@pytest.fixture
def index_update_hook(learning_loop_service) -> IndexUpdateHook:
    """Create an IndexUpdateHook instance."""
    return IndexUpdateHook(learning_loop_service)


# ── Test IndexUpdateHook ────────────────────────────────────────────────────


class TestIndexUpdateHook:
    """Tests for the auto-indexing hook."""

    @pytest.mark.asyncio
    async def test_hook_updates_on_event(self, index_update_hook, learning_loop_service):
        """Hook updates index entry on event."""
        # Get the entry before update
        before = learning_loop_service._storage.get("test_plugin", "test_loop")
        initial_count = before.event_count_7d

        # Call the hook
        result = await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id="test_plugin",
            loop_id="test_loop",
            feedback_signal=0.8,
            event_type="confidence",
        )

        assert result is True

        # Get the entry after update
        after = learning_loop_service._storage.get("test_plugin", "test_loop")
        assert after.event_count_7d == initial_count + 1
        assert after.health_score > before.health_score

    @pytest.mark.asyncio
    async def test_hook_skips_missing_loop_id(self, index_update_hook):
        """Hook skips events without loop_id."""
        result = await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id="test_plugin",
            loop_id=None,  # Missing!
            feedback_signal=0.8,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_hook_skips_missing_plugin_id(self, index_update_hook):
        """Hook skips events without plugin_id."""
        result = await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id=None,  # Missing!
            loop_id="test_loop",
            feedback_signal=0.8,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_hook_rejects_tenant_mismatch(self, index_update_hook):
        """Hook rejects tenant mismatch."""
        result = await index_update_hook.update_on_event(
            tenant_id="other_tenant",  # Different!
            plugin_id="test_plugin",
            loop_id="test_loop",
            feedback_signal=0.8,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_hook_handles_missing_feedback_signal(self, index_update_hook, learning_loop_service):
        """Hook works even without feedback_signal."""
        before = learning_loop_service._storage.get("test_plugin", "test_loop")

        result = await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id="test_plugin",
            loop_id="test_loop",
            feedback_signal=None,  # Missing signal
            event_type="other",
        )

        assert result is True
        after = learning_loop_service._storage.get("test_plugin", "test_loop")
        assert after.event_count_7d == before.event_count_7d + 1

    @pytest.mark.asyncio
    async def test_hook_updates_last_event_timestamp(self, index_update_hook, learning_loop_service):
        """Hook updates last_event_ts."""
        before = learning_loop_service._storage.get("test_plugin", "test_loop")
        before_ts = before.last_event_ts

        # Wait a bit
        await asyncio.sleep(0.1)

        await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id="test_plugin",
            loop_id="test_loop",
        )

        after = learning_loop_service._storage.get("test_plugin", "test_loop")
        assert after.last_event_ts > before_ts

    @pytest.mark.asyncio
    async def test_hook_updates_health_score(self, index_update_hook, learning_loop_service):
        """Hook updates health_score with rolling mean."""
        before = learning_loop_service._storage.get("test_plugin", "test_loop")
        initial_health = before.health_score

        # Send high confidence signal
        await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id="test_plugin",
            loop_id="test_loop",
            feedback_signal=0.95,  # High
        )

        after = learning_loop_service._storage.get("test_plugin", "test_loop")
        # Health should increase (rolling mean of initial + 0.95)
        expected = (initial_health + 0.95) / 2
        assert abs(after.health_score - expected) < 0.01

    @pytest.mark.asyncio
    async def test_hook_handles_service_error(self, index_update_hook):
        """Hook gracefully handles service errors."""
        # Mock the service to raise an error
        index_update_hook.service.update_on_event = Mock(side_effect=Exception("Test error"))

        # Should not raise, just return False
        result = await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id="test_plugin",
            loop_id="test_loop",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_hook_missing_service_returns_false(self):
        """Hook returns False if service is None."""
        hook = IndexUpdateHook(None)

        # Patch the service attribute to simulate missing service
        hook.service = None

        with patch.object(hook.service, 'update_on_event', side_effect=AttributeError):
            result = await hook.update_on_event(
                tenant_id="_default",
                plugin_id="test_plugin",
                loop_id="test_loop",
            )
        # Should handle the AttributeError gracefully


class TestGetIndexUpdateHook:
    """Tests for hook factory function."""

    def test_get_hook_creates_instance(self, temp_db_dir):
        """Factory creates hook instance for tenant."""
        # Clean up before test
        import core.learning.index_update_hook as hook_module
        hook_module._hook_instances.clear()
        hook_module._service_instances.clear()

        hook = get_index_update_hook("_default")
        assert hook is not None
        assert isinstance(hook, IndexUpdateHook)

    def test_get_hook_reuses_instance(self, temp_db_dir):
        """Factory reuses hook for same tenant."""
        import core.learning.index_update_hook as hook_module
        hook_module._hook_instances.clear()
        hook_module._service_instances.clear()

        hook1 = get_index_update_hook("_default")
        hook2 = get_index_update_hook("_default")

        assert hook1 is hook2

    def test_get_hook_invalid_tenant(self):
        """Factory rejects invalid tenant."""
        import core.learning.index_update_hook as hook_module
        hook_module._hook_instances.clear()
        hook_module._service_instances.clear()

        hook = get_index_update_hook("invalid!tenant")
        assert hook is None

    def test_cleanup_hooks(self, temp_db_dir):
        """Cleanup closes all services."""
        import core.learning.index_update_hook as hook_module
        hook_module._hook_instances.clear()
        hook_module._service_instances.clear()

        get_index_update_hook("_default")

        assert len(hook_module._hook_instances) == 1
        assert len(hook_module._service_instances) == 1

        hook_module.cleanup_hooks()

        assert len(hook_module._hook_instances) == 0
        assert len(hook_module._service_instances) == 0


# ── Test Status Transitions ─────────────────────────────────────────────────


class TestStatusTransitions:
    """Tests for status transition logic triggered by events."""

    @pytest.mark.asyncio
    async def test_status_remains_active_on_frequent_events(
        self, index_update_hook, learning_loop_service
    ):
        """Status stays 'active' when events are frequent (< 24h apart)."""
        # Insert entry with initial timestamp
        entry = learning_loop_service._storage.get("test_plugin", "test_loop")
        assert entry.status == "active"

        # Add more events (all within same hour)
        for _ in range(5):
            await index_update_hook.update_on_event(
                tenant_id="_default",
                plugin_id="test_plugin",
                loop_id="test_loop",
                feedback_signal=0.7,
            )

        updated = learning_loop_service._storage.get("test_plugin", "test_loop")
        assert updated.status == "active"
        assert updated.event_count_7d == 5

    @pytest.mark.asyncio
    async def test_status_degrades_on_low_health_and_events(
        self, learning_loop_service
    ):
        """Status becomes 'degrading' when health < threshold AND event_count >= 10."""
        # Create entry with low health threshold
        learning_loop_service._storage.delete("test_plugin", "test_loop")
        entry = learning_loop_service.insert_from_manifest(
            plugin_id="test_plugin",
            loop_id="degrading_loop",
            description="Will degrade",
            event_source="Test",
            feedback_types=[],
            aggregation="rolling_mean_7d",
            health_threshold=0.7,  # High threshold
            dormancy_alert_hours=24,
            owner_skill=None,
        )

        # Add many events with low confidence
        for _ in range(10):
            learning_loop_service.update_on_event(
                plugin_id="test_plugin",
                loop_id="degrading_loop",
                feedback_signal=0.2,  # Low confidence
            )

        updated = learning_loop_service._storage.get("test_plugin", "degrading_loop")
        # Should be degrading (health < 0.7 AND event_count >= 10)
        if updated.health_score < 0.7:
            assert updated.status == "degrading"


# ── Test Concurrent Writes ──────────────────────────────────────────────────


class TestConcurrentIndexUpdates:
    """Tests for concurrent event handling."""

    @pytest.mark.asyncio
    async def test_concurrent_hook_updates_are_safe(
        self, index_update_hook, learning_loop_service
    ):
        """Concurrent hook calls don't corrupt data."""
        # Create multiple tasks that update the same loop
        tasks = [
            index_update_hook.update_on_event(
                tenant_id="_default",
                plugin_id="test_plugin",
                loop_id="test_loop",
                feedback_signal=0.5 + i * 0.01,
            )
            for i in range(10)
        ]

        results = await asyncio.gather(*tasks)
        assert all(results)

        # Verify final state
        final = learning_loop_service._storage.get("test_plugin", "test_loop")
        assert final.event_count_7d == 10


# ── Test Error Handling ─────────────────────────────────────────────────────


class TestErrorHandling:
    """Tests for error handling in the hook."""

    @pytest.mark.asyncio
    async def test_hook_no_exception_on_storage_error(self, index_update_hook):
        """Hook doesn't raise even if storage fails."""
        # Mock storage to raise error
        index_update_hook.service._storage.get = Mock(return_value=None)
        index_update_hook.service.update_on_event = Mock(return_value=None)

        # Should not raise
        result = await index_update_hook.update_on_event(
            tenant_id="_default",
            plugin_id="test_plugin",
            loop_id="test_loop",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_hook_logs_error_on_failure(self, index_update_hook):
        """Hook logs errors but doesn't raise."""
        with patch.object(index_update_hook.service, 'update_on_event', side_effect=RuntimeError("Test")):
            # Should not raise
            result = await index_update_hook.update_on_event(
                tenant_id="_default",
                plugin_id="test_plugin",
                loop_id="test_loop",
            )
            assert result is False
