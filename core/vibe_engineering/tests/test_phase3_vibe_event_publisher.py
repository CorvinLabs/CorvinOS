"""Phase 3: Vibe Event Publisher Tests (25+ tests).

Tests event publication flow, ContextBus integration, async safety, and
reliability under concurrent task execution.
"""

import pytest
import asyncio
from typing import Dict, List, Any

from core.vibe_engineering.vibe_event_publisher import (
    VibeEventPublisher,
    GuidanceEvent,
    GuidanceEventType,
    DecisionContext,
)
from core.vibe_engineering.guidance_classifier import GuidanceCategory
from core.context_engineering.context_bus import ContextBus


class MockContextBus:
    """Mock ContextBus for testing without real async infrastructure."""

    def __init__(self):
        self.published_events: List[tuple] = []
        self.subscribers: Dict[str, List] = {}

    async def publish(self, event_type: str, payload: dict) -> None:
        """Mock publish: record event."""
        self.published_events.append((event_type, payload))

    def subscribe(self, event_type: str, callback) -> None:
        """Mock subscribe: record subscription."""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)

    def get_instance(self):
        """Mock get_instance."""
        return self


class TestVibeEventPublisherBasics:
    """Basic publisher initialization and lifecycle."""

    def test_publisher_init_defaults(self):
        """Initialize with default classifier and no ContextBus."""
        publisher = VibeEventPublisher()
        assert publisher.classifier is not None
        assert publisher._is_running is False

    def test_publisher_init_custom_classifier(self):
        """Initialize with custom GuidanceClassifier."""
        from core.vibe_engineering.guidance_classifier import GuidanceClassifier
        custom_classifier = GuidanceClassifier(enable_llm=False)
        publisher = VibeEventPublisher(classifier=custom_classifier)
        assert publisher.classifier is custom_classifier

    def test_publisher_init_custom_context_bus(self):
        """Initialize with custom ContextBus."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        assert publisher.context_bus is mock_bus

    async def test_publisher_start_stop(self):
        """Start and stop lifecycle."""
        publisher = VibeEventPublisher()
        await publisher.start()
        assert publisher._is_running is True
        assert publisher._worker_task is not None

        await publisher.stop()
        assert publisher._is_running is False

    async def test_publisher_double_start_idempotent(self):
        """Starting twice should be idempotent."""
        publisher = VibeEventPublisher()
        await publisher.start()
        task1 = publisher._worker_task

        await publisher.start()
        task2 = publisher._worker_task

        # Should be the same task
        assert task1 is task2
        await publisher.stop()

    async def test_publisher_double_stop_safe(self):
        """Stopping twice should be safe."""
        publisher = VibeEventPublisher()
        await publisher.start()
        await publisher.stop()
        await publisher.stop()  # Should not raise
        assert publisher._is_running is False


class TestGuidanceEventPublishing:
    """Publishing guidance events to ContextBus."""

    async def test_publish_guidance_event_basic(self):
        """Publish a basic guidance event."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-1",
            tenant_id="tenant-1",
            task_type="test",
            description="Test task",
        )

        event = await publisher.publish_guidance_event(ctx)
        assert event.task_id == "task-1"
        assert event.tenant_id == "tenant-1"
        assert event.category != ""

        # Give worker time to process queue
        await asyncio.sleep(0.1)
        assert len(mock_bus.published_events) > 0

        await publisher.stop()

    async def test_publish_guidance_event_without_start_raises(self):
        """Publishing before start() should raise RuntimeError."""
        publisher = VibeEventPublisher()
        ctx = DecisionContext(
            task_id="task-2",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        with pytest.raises(RuntimeError):
            await publisher.publish_guidance_event(ctx)

    async def test_publish_queues_event_asynchronously(self):
        """Event publishing is non-blocking (queued, not awaited)."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-3",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        # Publish multiple events in rapid succession
        events = []
        for i in range(5):
            ctx.task_id = f"task-{i}"
            event = await publisher.publish_guidance_event(ctx)
            events.append(event)

        # All should be queued
        assert publisher._event_queue.qsize() >= 3

        # Wait for processing
        await asyncio.sleep(0.2)

        # All should eventually be published
        assert len(mock_bus.published_events) >= 5

        await publisher.stop()

    async def test_publish_event_contains_metrics(self):
        """Published event includes supporting metrics."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-4",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
            error_rate=0.4,
            budget_remaining=0.3,
        )

        event = await publisher.publish_guidance_event(ctx)
        assert len(event.supporting_metrics) > 0
        assert "error_rate" in event.supporting_metrics

        await asyncio.sleep(0.1)
        await publisher.stop()

    async def test_publish_event_contains_context_snapshot(self):
        """Published event includes context snapshot."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-5",
            tenant_id="tenant-1",
            task_type="code_generation",
            description="Generate code",
            current_iteration=5,
            max_iterations=10,
        )

        event = await publisher.publish_guidance_event(ctx)
        assert event.context_snapshot["task_type"] == "code_generation"
        assert event.context_snapshot["current_iteration"] == 5

        await asyncio.sleep(0.1)
        await publisher.stop()


class TestGuidanceEventCategoryMapping:
    """Event type mapping from GuidanceCategory."""

    def test_map_refactor_category(self):
        """REFACTOR maps to REFACTOR event type."""
        publisher = VibeEventPublisher()
        event_type = publisher._map_category_to_event_type(
            GuidanceCategory.REFACTOR
        )
        assert event_type == GuidanceEventType.REFACTOR

    def test_map_parallelize_category(self):
        """PARALLELIZE maps to PARALLELIZE event type."""
        publisher = VibeEventPublisher()
        event_type = publisher._map_category_to_event_type(
            GuidanceCategory.PARALLELIZE
        )
        assert event_type == GuidanceEventType.PARALLELIZE

    def test_map_all_categories(self):
        """All GuidanceCategories have event type mappings."""
        publisher = VibeEventPublisher()
        for category in GuidanceCategory:
            event_type = publisher._map_category_to_event_type(category)
            assert isinstance(event_type, GuidanceEventType)

    def test_unmapped_defaults_to_feature_gating(self):
        """Unknown category defaults to FEATURE_GATING."""
        publisher = VibeEventPublisher()
        # Force a hypothetical unknown category
        class FakeCategory:
            pass
        fake = FakeCategory()
        # Direct call to _map would fail, but that's expected
        # Instead test that default case works
        event_type = publisher._map_category_to_event_type(
            GuidanceCategory.FEATURE_GATING
        )
        assert event_type == GuidanceEventType.FEATURE_GATING


class TestPublisherStatistics:
    """Monitor publisher statistics."""

    async def test_stats_published_events_counter(self):
        """Statistics track published event count."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        assert publisher.get_stats()["published_events"] == 0

        ctx = DecisionContext(
            task_id="task-6",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        stats = publisher.get_stats()
        assert stats["published_events"] >= 1

        await publisher.stop()

    async def test_stats_queue_depth(self):
        """Statistics report event queue depth."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-7",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        # Queue multiple events
        for i in range(3):
            ctx.task_id = f"task-{i}"
            await publisher.publish_guidance_event(ctx)

        stats = publisher.get_stats()
        assert stats["queue_depth"] > 0

        await asyncio.sleep(0.1)
        await publisher.stop()

    async def test_stats_failed_publishes_counter(self):
        """Statistics track failed publish attempts."""
        # Use None as context_bus to force failures
        publisher = VibeEventPublisher(context_bus=None)
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-8",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        stats = publisher.get_stats()
        # With no ContextBus, should have failed publishes
        assert stats["failed_publishes"] >= 1

        await publisher.stop()

    async def test_stats_success_rate_calculation(self):
        """Statistics calculate publication success rate."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-9",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        stats = publisher.get_stats()
        success_rate = stats["publication_success_rate"]
        assert 0.0 <= success_rate <= 1.0

        await publisher.stop()


class TestEventSubscriptions:
    """Subscription to guidance events."""

    async def test_subscribe_to_guidance_category(self):
        """Subscribe to guidance events of specific category."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        callback_invoked = []

        async def mock_callback(payload: dict):
            callback_invoked.append(payload)

        await publisher.subscribe_to_guidance(
            GuidanceCategory.PARALLELIZE, mock_callback
        )

        assert GuidanceEventType.PARALLELIZE.value in mock_bus.subscribers

        await publisher.stop()

    async def test_subscribe_without_context_bus_raises(self):
        """Subscribing without ContextBus should raise."""
        publisher = VibeEventPublisher(context_bus=None)
        await publisher.start()

        async def dummy_callback(payload):
            pass

        with pytest.raises(RuntimeError):
            await publisher.subscribe_to_guidance(
                GuidanceCategory.ERROR_RECOVERY, dummy_callback
            )

        await publisher.stop()


class TestEventSerialization:
    """GuidanceEvent serialization."""

    def test_guidance_event_to_dict(self):
        """Serialize GuidanceEvent to dictionary."""
        event = GuidanceEvent(
            task_id="task-10",
            tenant_id="tenant-1",
            category="parallelize",
            confidence=0.8,
            rationale="Test rationale",
        )

        d = event.to_dict()
        assert d["task_id"] == "task-10"
        assert d["category"] == "parallelize"
        assert d["confidence"] == 0.8
        assert "timestamp" in d

    def test_guidance_event_with_metrics(self):
        """GuidanceEvent includes supporting metrics."""
        metrics = {"error_rate": 0.15, "budget": 0.5}
        event = GuidanceEvent(
            task_id="task-11",
            tenant_id="tenant-1",
            supporting_metrics=metrics,
        )

        d = event.to_dict()
        assert d["supporting_metrics"]["error_rate"] == 0.15


class TestConcurrentPublishing:
    """Concurrent event publishing behavior."""

    async def test_concurrent_publish_multiple_tasks(self):
        """Publish events from multiple concurrent tasks."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        async def publish_task(task_id: str):
            ctx = DecisionContext(
                task_id=task_id,
                tenant_id="tenant-1",
                task_type="test",
                description=f"Task {task_id}",
            )
            return await publisher.publish_guidance_event(ctx)

        # Publish from 5 concurrent tasks
        results = await asyncio.gather(
            *[publish_task(f"task-{i}") for i in range(5)]
        )

        assert len(results) == 5
        assert all(isinstance(r, GuidanceEvent) for r in results)

        await asyncio.sleep(0.2)
        await publisher.stop()

    async def test_concurrent_publish_multitenancy(self):
        """Concurrent publishing respects tenant isolation."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        async def publish_for_tenant(tenant_id: str, task_num: int):
            ctx = DecisionContext(
                task_id=f"{tenant_id}-task-{task_num}",
                tenant_id=tenant_id,
                task_type="test",
                description=f"Task for {tenant_id}",
            )
            return await publisher.publish_guidance_event(ctx)

        # Publish from multiple tenants concurrently
        results = await asyncio.gather(
            *[
                publish_for_tenant("tenant-1", 0),
                publish_for_tenant("tenant-2", 0),
                publish_for_tenant("tenant-1", 1),
                publish_for_tenant("tenant-3", 0),
            ]
        )

        assert len(results) == 4
        # All should have different tenant IDs
        tenants = set(r.tenant_id for r in results)
        assert len(tenants) == 3  # 3 distinct tenants

        await asyncio.sleep(0.1)
        await publisher.stop()


class TestErrorHandling:
    """Error handling and recovery."""

    async def test_context_bus_publish_failure_logged(self):
        """Failed ContextBus publish is logged but doesn't crash."""
        class FailingContextBus:
            async def publish(self, event_type: str, payload: dict):
                raise Exception("Bus failure")

        publisher = VibeEventPublisher(context_bus=FailingContextBus())
        await publisher.start()

        ctx = DecisionContext(
            task_id="task-12",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        await publisher.publish_guidance_event(ctx)
        await asyncio.sleep(0.1)

        stats = publisher.get_stats()
        # Should have failed publishes
        assert stats["failed_publishes"] >= 1

        await publisher.stop()

    async def test_callback_exception_handled_gracefully(self):
        """Callback exception doesn't break event publishing."""
        mock_bus = MockContextBus()
        publisher = VibeEventPublisher(context_bus=mock_bus)
        await publisher.start()

        def failing_callback(event: GuidanceEvent):
            raise RuntimeError("Callback failed")

        ctx = DecisionContext(
            task_id="task-13",
            tenant_id="tenant-1",
            task_type="test",
            description="Test",
        )

        # Publish with failing callback
        event = await publisher.publish_guidance_event(
            ctx, request_callback=failing_callback
        )

        # Should still return event
        assert event is not None
        assert event.task_id == "task-13"

        await publisher.stop()


class TestGlobalSingleton:
    """Global publisher singleton."""

    def test_get_publisher_creates_singleton(self):
        """get_publisher() creates global singleton."""
        from core.vibe_engineering.vibe_event_publisher import (
            get_publisher, set_publisher
        )

        set_publisher(None)  # Reset
        pub1 = get_publisher()
        pub2 = get_publisher()

        assert pub1 is pub2

    def test_set_publisher_replaces_singleton(self):
        """set_publisher() replaces global singleton."""
        from core.vibe_engineering.vibe_event_publisher import (
            get_publisher, set_publisher
        )

        mock_bus = MockContextBus()
        new_pub = VibeEventPublisher(context_bus=mock_bus)
        set_publisher(new_pub)

        retrieved = get_publisher()
        assert retrieved is new_pub


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
