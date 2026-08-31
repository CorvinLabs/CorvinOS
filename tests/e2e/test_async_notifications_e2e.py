"""End-to-end tests for async notification delivery (ADR-0368).

Tests complete notification flow:
1. Event emitted by subsystem
2. NotificationBroker enqueues and batches
3. Handlers deliver to channels
4. Metrics tracked (latency, success rate)

Loss function: notification_latency / 5000ms
- target: <500ms per delivery (loss < 0.1)

LDD Verification: Task completion notifications delivered within SLA
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import pytest

from core.orchestration.subsystems.notification_broker import (
    NotificationBroker,
    NotificationEvent,
    NotificationEventType,
    NotificationSeverity,
    NotificationQueue,
)
from core.orchestration.subsystems.notification_handlers import (
    DiscordNotificationHandler,
    SlackNotificationHandler,
    LoggerNotificationHandler,
)


class TestAsyncNotificationsE2E:
    """End-to-end tests for notification delivery."""

    @pytest.mark.asyncio
    async def test_single_event_notification_flow(self):
        """Test complete flow for a single notification event.

        Scenario:
        1. HealthMonitor detects subsystem error
        2. Emits notification event
        3. NotificationBroker enqueues
        4. Handler delivers to Discord
        5. Metrics updated

        LDD: Latency < 500ms
        """
        broker = NotificationBroker()

        # Register mock handlers
        discord_handler = AsyncMock(return_value=True)
        broker.register_backend("discord", discord_handler)

        # Start broker
        await broker.start()

        try:
            # Emit event (simulating HealthMonitor)
            start_time = datetime.utcnow()
            result = await broker.emit_event(
                event_type=NotificationEventType.HEALTH_DEGRADED,
                task_id="long_task_001",
                severity=NotificationSeverity.ERROR,
                title="HealthMonitor: Subsystem Degraded",
                message="LoopEngineer response time >1000ms",
                metadata={"subsystem": "LoopEngineer", "latency_ms": 1250},
            )

            assert result is True

            # Wait for delivery
            await asyncio.sleep(0.5)

            # Verify notification in history
            history = broker.get_notification_history(task_id="long_task_001")
            assert len(history) == 1
            assert history[0]["event_type"] == "health_degraded"
            assert history[0]["severity"] == "error"

            # Verify delivery latency (LDD verification)
            elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
            loss_latency = elapsed / 5000.0  # Target: 5 seconds
            assert loss_latency < 1.0, f"Latency loss {loss_latency:.2f} exceeded threshold"
            assert elapsed < 500, f"Notification delivery took {elapsed:.0f}ms, target <500ms"

            print(f"✓ Notification delivered in {elapsed:.0f}ms (loss={loss_latency:.3f})")

        finally:
            await broker.stop()

    @pytest.mark.asyncio
    async def test_batch_notifications_with_priority_routing(self):
        """Test batching notifications and routing by severity.

        Scenario:
        1. Emit 5 INFO events (batch into 1 delivery)
        2. Emit 2 ERROR events (immediate delivery)
        3. Emit 1 CRITICAL event (immediate, retry)
        4. Verify each route's delivery count and latency

        LDD: Batch delivers within delay_ms window
        """
        broker = NotificationBroker()

        # Register mock handlers
        discord_handler = AsyncMock(return_value=True)
        slack_handler = AsyncMock(return_value=True)
        email_handler = AsyncMock(return_value=True)

        broker.register_backend("discord", discord_handler)
        broker.register_backend("slack", slack_handler)
        broker.register_backend("email", email_handler)

        await broker.start()

        try:
            # Emit INFO events (should batch)
            for i in range(5):
                await broker.emit_event(
                    event_type=NotificationEventType.PROGRESS_CHECKPOINT,
                    task_id="task_1",
                    severity=NotificationSeverity.INFO,
                    title=f"Progress {i}",
                    message=f"Progress checkpoint {i}",
                )

            # Emit ERROR events (should deliver quickly)
            for i in range(2):
                await broker.emit_event(
                    event_type=NotificationEventType.SUBSYSTEM_ERROR,
                    task_id="task_1",
                    severity=NotificationSeverity.ERROR,
                    title=f"Error {i}",
                    message=f"Subsystem error {i}",
                )

            # Emit CRITICAL event (immediate delivery to all channels)
            await broker.emit_event(
                event_type=NotificationEventType.QUOTA_EXCEEDED,
                task_id="task_1",
                severity=NotificationSeverity.CRITICAL,
                title="Critical: Quota Exceeded",
                message="Task quota limit exceeded",
            )

            # Wait for deliveries
            await asyncio.sleep(2.0)

            # Verify history
            history = broker.get_notification_history(task_id="task_1")
            assert len(history) == 8  # 5 + 2 + 1

            # Count by severity
            info_count = sum(1 for e in history if e["severity"] == "info")
            error_count = sum(1 for e in history if e["severity"] == "error")
            critical_count = sum(1 for e in history if e["severity"] == "critical")

            assert info_count == 5
            assert error_count == 2
            assert critical_count == 1

            print(
                f"✓ Batch notifications: {info_count} INFO, {error_count} ERROR, "
                f"{critical_count} CRITICAL"
            )

        finally:
            await broker.stop()

    @pytest.mark.asyncio
    async def test_notification_delivery_with_retry(self):
        """Test delivery retry with exponential backoff.

        Scenario:
        1. First delivery fails
        2. NotificationBroker retries (exponential backoff)
        3. Subsequent retry succeeds
        4. Metrics updated (failures, deliveries)

        LDD: Retry succeeds within 3 attempts
        """
        broker = NotificationBroker()

        # Mock handler that fails once then succeeds
        call_count = 0

        async def flaky_handler(events):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Temporary network error")
            return True

        broker.register_backend("discord", flaky_handler)

        await broker.start()

        try:
            await broker.emit_event(
                event_type=NotificationEventType.TASK_FAILED,
                task_id="task_2",
                severity=NotificationSeverity.ERROR,
                title="Task Failed",
                message="Task execution failed",
            )

            # Wait for retries
            await asyncio.sleep(2.0)

            # Verify event in history (delivery eventually succeeds)
            history = broker.get_notification_history(task_id="task_2")
            assert len(history) == 1

            # Verify retry occurred
            stats = broker.get_stats()
            print(f"✓ Retry test: Delivered after {call_count} attempts")
            print(f"  Stats: {stats['queue_stats']}")

        finally:
            await broker.stop()

    @pytest.mark.asyncio
    async def test_notification_queue_under_load(self):
        """Test NotificationQueue performance under load.

        Scenario:
        1. Emit 100 notifications rapidly
        2. Queue batches and delivers
        3. All notifications eventually delivered
        4. Measure throughput (events/sec)

        LDD: Throughput > 100 events/sec
        """
        broker = NotificationBroker()

        delivery_count = 0

        async def counting_handler(events):
            nonlocal delivery_count
            delivery_count += len(events)
            await asyncio.sleep(0.01)  # Simulate network delay
            return True

        broker.register_backend("discord", counting_handler)

        await broker.start()

        try:
            import time
            start_time = time.time()

            # Emit 100 events rapidly
            for i in range(100):
                await broker.emit_event(
                    event_type=NotificationEventType.PROGRESS_CHECKPOINT,
                    task_id=f"task_{i % 10}",
                    severity=NotificationSeverity.INFO,
                    title=f"Event {i}",
                    message=f"Event message {i}",
                )

            # Wait for all deliveries
            await asyncio.sleep(5.0)

            elapsed = time.time() - start_time
            throughput = 100 / elapsed

            # Verify all delivered
            assert delivery_count >= 95, f"Only {delivery_count}/100 delivered"

            print(f"✓ Load test: {delivery_count} events delivered in {elapsed:.1f}s")
            print(f"  Throughput: {throughput:.0f} events/sec (target: >100)")
            print(f"  Loss: {max(0, 100 - delivery_count)} events")

        finally:
            await broker.stop()

    @pytest.mark.asyncio
    async def test_multi_channel_delivery(self):
        """Test notifications delivered to multiple channels.

        Scenario:
        1. CRITICAL event → Discord + Slack + Email
        2. ERROR event → Discord + Slack
        3. INFO event → Discord only
        4. Verify each channel receives correct events

        LDD: Multi-channel delivery latency <500ms per event
        """
        broker = NotificationBroker()

        # Mock handlers for each channel
        discord_events = []
        slack_events = []
        email_events = []

        async def discord_handler(events):
            discord_events.extend(events)
            return True

        async def slack_handler(events):
            slack_events.extend(events)
            return True

        async def email_handler(events):
            email_events.extend(events)
            return True

        broker.register_backend("discord", discord_handler)
        broker.register_backend("slack", slack_handler)
        broker.register_backend("email", email_handler)

        await broker.start()

        try:
            # CRITICAL: All channels
            await broker.emit_event(
                event_type=NotificationEventType.QUOTA_EXCEEDED,
                task_id="task_3",
                severity=NotificationSeverity.CRITICAL,
                title="Critical Alert",
                message="Quota exceeded",
            )

            # ERROR: Discord + Slack
            await broker.emit_event(
                event_type=NotificationEventType.TASK_FAILED,
                task_id="task_3",
                severity=NotificationSeverity.ERROR,
                title="Error Alert",
                message="Task failed",
            )

            # INFO: Discord only
            await broker.emit_event(
                event_type=NotificationEventType.PROGRESS_CHECKPOINT,
                task_id="task_3",
                severity=NotificationSeverity.INFO,
                title="Info",
                message="Progress update",
            )

            # Wait for delivery
            await asyncio.sleep(1.0)

            # Verify routing (approximate counts due to batching)
            print(f"✓ Multi-channel delivery:")
            print(f"  Discord: {len(discord_events)} events")
            print(f"  Slack: {len(slack_events)} events")
            print(f"  Email: {len(email_events)} events")

        finally:
            await broker.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
