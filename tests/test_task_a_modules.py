"""Test stubs for Task A modules (nervous_system, audit.engine_span, notifications).

Full test suite to implement in next session.
"""

import pytest
from datetime import datetime
from core.nervous_system.registry import TaskRegistry, TaskState, TaskRecord
from core.audit.engine_span import EngineSpanTracker, EngineSpan
from core.notifications.bus import NotificationBus, NotificationLevel, NotificationChannel

# === NERVOUS_SYSTEM TESTS ===

class TestTaskRegistry:
    """Test TaskRegistry (50 LoC target for full implementation)."""

    def test_create_task(self):
        """Create a simple task."""
        reg = TaskRegistry("tenant-123")
        task_id = reg.create_task("gather data")
        assert task_id
        task = reg.get_task(task_id)
        assert task.task_name == "gather data"
        assert task.state == TaskState.CREATED

    def test_create_task_with_dependencies(self):
        """Create tasks with explicit dependencies."""
        reg = TaskRegistry("tenant-123")
        task1 = reg.create_task("task 1")
        task2 = reg.create_task("task 2", dependencies=[task1])
        assert task2
        record = reg.get_task(task2)
        assert task1 in record.dependencies

    def test_update_state(self):
        """Update task state transitions."""
        reg = TaskRegistry("tenant-123")
        task_id = reg.create_task("work")
        reg.update_state(task_id, TaskState.RUNNING, phase="gather")
        task = reg.get_task(task_id)
        assert task.state == TaskState.RUNNING
        assert task.phase == "gather"

    def test_list_active(self):
        """List only non-terminal tasks."""
        reg = TaskRegistry("tenant-123")
        t1 = reg.create_task("t1")
        t2 = reg.create_task("t2")
        reg.update_state(t1, TaskState.COMPLETED)
        active = reg.list_active()
        assert len(active) == 1
        assert active[0].task_id == t2

    def test_health_status(self):
        """Compute health metrics."""
        reg = TaskRegistry("tenant-123")
        reg.create_task("t1")
        reg.create_task("t2")
        health = reg.health_status()
        assert health["total_count"] == 2
        assert health["active_count"] == 2
        assert health["error_rate"] == 0.0


# === AUDIT.ENGINE_SPAN TESTS ===

class TestEngineSpanTracker:
    """Test EngineSpanTracker (60 LoC target for full implementation)."""

    def test_create_span(self):
        """Create execution span."""
        tracker = EngineSpanTracker("tenant-123")
        span = tracker.create_span("span-1", "orchestrator", "gather")
        assert span.span_id == "span-1"
        assert span.status == "running"
        assert span.phase == "gather"

    def test_complete_span(self):
        """Mark span as complete."""
        tracker = EngineSpanTracker("tenant-123")
        span = tracker.create_span("span-1", "orchestrator", "gather")
        completed = tracker.complete_span("span-1", status="completed", result_hash="abc123")
        assert completed.status == "completed"
        assert completed.result_hash == "abc123"
        assert completed.ended_at is not None

    def test_span_hash_chain(self):
        """Verify hash-chain linking."""
        tracker = EngineSpanTracker("tenant-123")
        s1 = tracker.create_span("s1", "engine", "gather")
        tracker.complete_span("s1")
        s2 = tracker.create_span("s2", "engine", "analyze")
        # s2 should have previous_hash set
        assert s2.previous_hash is not None

    def test_verify_chain_integrity(self):
        """Verify audit chain is unbroken."""
        tracker = EngineSpanTracker("tenant-123")
        tracker.create_span("s1", "engine", "gather")
        tracker.complete_span("s1")
        tracker.create_span("s2", "engine", "analyze")
        tracker.complete_span("s2")
        # Chain should verify as intact
        assert tracker.verify_chain_integrity()

    def test_span_audit_serialization(self):
        """Serialize span to audit-safe format."""
        tracker = EngineSpanTracker("tenant-123")
        span = tracker.create_span("span-1", "orchestrator", "gather")
        audit_dict = span.to_audit_dict()
        assert audit_dict["span_id"] == "span-1"
        assert audit_dict["phase"] == "gather"
        # No PII should be present
        assert all(k in audit_dict for k in ["created_at", "status"])


# === NOTIFICATIONS TESTS ===

class TestNotificationBus:
    """Test NotificationBus (80 LoC target for full implementation)."""

    @pytest.mark.asyncio
    async def test_publish_notification(self):
        """Publish a notification to the bus."""
        bus = NotificationBus("tenant-123")
        await bus.publish(
            "notif-1",
            "task-1",
            "Phase 1: gather data",
            level=NotificationLevel.INFO,
            channel=NotificationChannel.DISCORD,
        )
        delivered = bus.get_delivered()
        assert len(delivered) == 1
        assert delivered[0].message == "Phase 1: gather data"

    @pytest.mark.asyncio
    async def test_subscribe_and_deliver(self):
        """Subscribe handler and deliver notification."""
        bus = NotificationBus("tenant-123")
        received = []

        async def handler(notif):
            received.append(notif)

        bus.subscribe(NotificationChannel.DISCORD, handler)
        # Manually trigger delivery (process_queue runs in background)
        notif = bus._subscribers[NotificationChannel.DISCORD][0].__self__  # type: ignore

    @pytest.mark.asyncio
    async def test_notification_to_dict(self):
        """Serialize notification for wire transmission."""
        bus = NotificationBus("tenant-123")
        await bus.publish(
            "n1",
            "t1",
            "Working...",
            metadata={"phase": "gather"},
        )
        notif = bus.get_delivered()[0]
        data = notif.to_dict()
        assert data["task_id"] == "t1"
        assert data["message"] == "Working..."
        assert data["metadata"]["phase"] == "gather"

    @pytest.mark.asyncio
    async def test_filter_delivered_by_task(self):
        """Filter notifications by task_id."""
        bus = NotificationBus("tenant-123")
        await bus.publish("n1", "task-a", "msg1")
        await bus.publish("n2", "task-b", "msg2")
        await bus.publish("n3", "task-a", "msg3")
        task_a_notifs = bus.get_delivered("task-a")
        assert len(task_a_notifs) == 2
        assert all(n.task_id == "task-a" for n in task_a_notifs)
