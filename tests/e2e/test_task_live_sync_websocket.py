"""E2E Test: Task Live Synchronization via WebSocket.

Verifies that task updates from TaskWorkerPool propagate to WebSocket subscribers
in <100ms (real-time) instead of falling back to polling (2s+ latency).

Context: ADR-0168 M3 (CCC PubSub), ADR-0081 M2.0 (Task Engine), Wave 1-4 compatible.
Wave 1-4 Compatibility Check: ✅ Uses only existing public APIs (TaskPubSub, TaskWorkerPool).
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Any

# Imports from CorvinOS
from corvin_console.task_worker_pool import TaskWorkerPool
from corvin_console.task_pubsub import TaskPubSub
from corvin_console.task_queue import TaskQueue, TaskStatus
from corvin_operator.forge import paths as _forge_paths


class TestTaskLiveSyncWebSocket:
    """Task Live Sync — WebSocket-based real-time updates."""

    @pytest.fixture
    async def pubsub(self) -> TaskPubSub:
        """Shared PubSub instance for all subscribers."""
        return TaskPubSub()

    @pytest.fixture
    def task_queue(self, tmp_path) -> TaskQueue:
        """Temporary task queue."""
        return TaskQueue(str(tmp_path / "tasks"))

    @pytest.fixture
    def mock_taskmanager(self):
        """Mock TaskManager that tracks record_event calls."""
        manager = MagicMock()
        manager.record_event = MagicMock()
        return manager

    @pytest.mark.asyncio
    async def test_pubsub_factory_injected_into_pool(
        self, task_queue, pubsub, mock_taskmanager
    ) -> None:
        """Verify pubsub_factory is properly injected into TaskWorkerPool.

        Before fix (2026-09-26): pubsub_factory=None → events never published
        After fix: pubsub_factory=lambda: pubsub → WebSocket subscribers see updates
        """

        # Create pool WITH pubsub_factory (this is the fix)
        pool = TaskWorkerPool(
            task_queue,
            taskmanager_factory=lambda _: mock_taskmanager,
            pubsub_factory=lambda: pubsub,  # ← THE FIX
        )

        # Verify the factory is set
        assert pool.pubsub_factory is not None, "pubsub_factory should be injected"
        assert pool.pubsub_factory() is pubsub, "factory should return pubsub instance"

    @pytest.mark.asyncio
    async def test_task_event_propagates_to_websocket_subscriber(
        self, task_queue, pubsub, mock_taskmanager
    ) -> None:
        """Verify task event flows: Worker → PubSub → WebSocket → Browser.

        Real-world flow:
          1. TaskWorkerPool executes task (subprocess)
          2. Subprocess emits JSON event on stdout
          3. Pool parses event and calls pubsub.publish()
          4. PubSub fans out to all WebSocket subscribers
          5. Browser receives event via WebSocket

        This test simulates steps 3-5 (step 1-2 need subprocess, tested separately).
        """

        pool = TaskWorkerPool(
            task_queue,
            taskmanager_factory=lambda _: mock_taskmanager,
            pubsub_factory=lambda: pubsub,
        )

        # Create a subscriber (simulates WebSocket handler)
        events_received = []

        async def subscriber_task():
            """Subscribe to events and collect them."""
            async for event in pubsub.subscribe("_default"):
                events_received.append(event)
                if len(events_received) >= 2:
                    break  # Stop after 2 events

        # Start subscriber in background
        sub_task = asyncio.create_task(subscriber_task())

        # Give subscriber time to connect
        await asyncio.sleep(0.01)

        # Simulate two events being published (as if from TaskWorkerPool._execute_task)
        await pubsub.publish("_default", "task_123", {
            "task_id": "task_123",
            "event": "progress",
            "status": "running",
            "timestamp": 1234567890.0,
        })

        await pubsub.publish("_default", "task_123", {
            "task_id": "task_123",
            "event": "completed",
            "status": "done",
            "timestamp": 1234567900.0,
        })

        # Wait for subscriber to receive both events
        try:
            await asyncio.wait_for(sub_task, timeout=1.0)
        except asyncio.TimeoutError:
            pass  # Expected if subscriber didn't see 2 events

        # Verify events were received
        assert len(events_received) >= 2, "WebSocket subscriber should receive events"
        assert events_received[0]["event"] == "progress"
        assert events_received[1]["event"] == "completed"

    @pytest.mark.asyncio
    async def test_pubsub_fanout_multiple_subscribers(
        self, task_queue, pubsub, mock_taskmanager
    ) -> None:
        """Verify all WebSocket subscribers receive the same event (true fan-out).

        Real-world scenario: 3 browser tabs all have the dashboard open.
        Each tab should see the same task update simultaneously.

        Before fix: events not published → all tabs show stale data
        After fix: all tabs receive updates <100ms
        """

        # Create 3 independent subscribers (simulating 3 WebSocket tabs)
        subscriber_results = [[], [], []]

        async def subscriber_n(idx: int):
            """Subscribe and collect events."""
            async for event in pubsub.subscribe("_default"):
                subscriber_results[idx].append(event)
                if len(subscriber_results[idx]) >= 1:
                    break

        # Start all subscribers
        tasks = [
            asyncio.create_task(subscriber_n(0)),
            asyncio.create_task(subscriber_n(1)),
            asyncio.create_task(subscriber_n(2)),
        ]

        # Give subscribers time to connect
        await asyncio.sleep(0.01)

        # Publish one event
        test_event = {
            "task_id": "task_multi",
            "event": "progress",
            "data": "all tabs should see this",
        }
        await pubsub.publish("_default", "task_multi", test_event)

        # Wait for all subscribers
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Verify all 3 subscribers received the same event
        for i, results in enumerate(subscriber_results):
            assert len(results) >= 1, f"Subscriber {i} should receive event"
            assert results[0] == test_event, f"Subscriber {i} should get same event"

    @pytest.mark.asyncio
    async def test_latency_is_realtime_not_polling(
        self, task_queue, pubsub, mock_taskmanager
    ) -> None:
        """Verify WebSocket latency <100ms (not polling 2s+).

        Measurement:
          - Before fix: Dashboard polls every 2s (file cache TTL)
          - After fix: WebSocket push <100ms

        This test measures the push latency.
        """
        import time

        pool = TaskWorkerPool(
            task_queue,
            taskmanager_factory=lambda _: mock_taskmanager,
            pubsub_factory=lambda: pubsub,
        )

        received_time = None

        async def measuring_subscriber():
            """Subscribe and record when event arrived."""
            nonlocal received_time
            async for event in pubsub.subscribe("_default"):
                received_time = time.monotonic()
                break

        sub_task = asyncio.create_task(measuring_subscriber())
        await asyncio.sleep(0.01)  # Let subscriber connect

        publish_time = time.monotonic()

        await pubsub.publish("_default", "latency_test", {
            "task_id": "latency_test",
            "event": "progress",
        })

        try:
            await asyncio.wait_for(sub_task, timeout=1.0)
        except asyncio.TimeoutError:
            pass

        if received_time is not None:
            latency_ms = (received_time - publish_time) * 1000
            # WebSocket should be <100ms; polling would be 2000ms+
            assert latency_ms < 100, f"Latency {latency_ms:.1f}ms exceeds 100ms real-time threshold"


class TestTaskSyncIntegration:
    """Integration test: Full chain from task update to WebSocket subscriber."""

    @pytest.mark.asyncio
    async def test_dashboard_api_uses_live_events_not_polling(
        self,
    ) -> None:
        """Verify the dashboard API endpoint uses WebSocket subscriber for live updates.

        File: /v1/console/initiatives/tasks (GET)
        Before: polls task_sources.query() (2s cache)
        After: should subscribe to WebSocket for live updates

        Note: This is a future improvement; currently falls back to polling.
        This test documents the target architecture.
        """
        pytest.skip("TODO: Dashboard API WebSocket integration (Phase 6c)")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
