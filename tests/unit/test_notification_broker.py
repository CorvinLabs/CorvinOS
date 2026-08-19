"""Unit tests for notification_broker module (ADR-0368).

Tests NotificationBroker, NotificationQueue, NotificationBackendRegistry,
and handler integration.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.orchestration.subsystems.notification_broker import (
    NotificationBroker,
    NotificationEvent,
    NotificationEventType,
    NotificationQueue,
    NotificationRoute,
    NotificationSeverity,
    NotificationBackendRegistry,
)
from core.orchestration.subsystems.notification_handlers import (
    DiscordNotificationHandler,
    SlackNotificationHandler,
    EmailNotificationHandler,
    LoggerNotificationHandler,
)


@pytest.fixture
def broker():
    """Create a NotificationBroker instance."""
    return NotificationBroker()


class TestNotificationEvent:
    """Tests for NotificationEvent dataclass."""

    def test_create_event(self):
        """Test creating a notification event."""
        event = NotificationEvent(
            event_id="evt_123",
            event_type=NotificationEventType.TASK_COMPLETED,
            task_id="task_001",
            severity=NotificationSeverity.INFO,
            title="Task Completed",
            message="Task successfully completed",
        )

        assert event.event_id == "evt_123"
        assert event.event_type == NotificationEventType.TASK_COMPLETED
        assert event.severity == NotificationSeverity.INFO

    def test_event_to_dict(self):
        """Test serializing event to dict."""
        event = NotificationEvent(
            event_id="evt_123",
            event_type=NotificationEventType.TASK_FAILED,
            task_id="task_001",
            severity=NotificationSeverity.ERROR,
            title="Task Failed",
            message="Task execution failed",
            metadata={"error_type": "TimeoutError"},
        )

        event_dict = event.to_dict()
        assert event_dict["event_id"] == "evt_123"
        assert event_dict["event_type"] == "task_failed"
        assert event_dict["severity"] == "error"
        assert event_dict["metadata"]["error_type"] == "TimeoutError"


class TestNotificationBackendRegistry:
    """Tests for NotificationBackendRegistry."""

    def test_register_backend(self):
        """Test registering a notification backend."""
        registry = NotificationBackendRegistry()
        handler = AsyncMock()

        registry.register("discord", handler)
        assert registry.get_handler("discord") is handler

    def test_available_channels(self):
        """Test getting available channels."""
        registry = NotificationBackendRegistry()

        registry.register("discord", AsyncMock())
        registry.register("slack", AsyncMock())
        registry.register("email", AsyncMock())

        channels = registry.available_channels()
        assert len(channels) == 3
        assert "discord" in channels
        assert "slack" in channels
        assert "email" in channels


class TestNotificationQueue:
    """Tests for NotificationQueue."""

    @pytest.mark.asyncio
    async def test_queue_enqueue(self):
        """Test enqueueing an event."""
        registry = NotificationBackendRegistry()
        queue = NotificationQueue(registry)

        event = NotificationEvent(
            event_id="evt_1",
            event_type=NotificationEventType.TASK_COMPLETED,
            task_id="task_1",
            severity=NotificationSeverity.INFO,
            title="Done",
            message="Task done",
        )

        result = await queue.enqueue(event)
        assert result is True
        assert queue._queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_queue_full(self):
        """Test that queue properly handles full condition."""
        registry = NotificationBackendRegistry()
        queue = NotificationQueue(registry, max_queue_size=2)

        # Fill queue
        for i in range(2):
            event = NotificationEvent(
                event_id=f"evt_{i}",
                event_type=NotificationEventType.TASK_COMPLETED,
                task_id=f"task_{i}",
                severity=NotificationSeverity.INFO,
                title="Test",
                message="Test event",
            )
            await queue.enqueue(event)

        # Try to add one more (should fail)
        event = NotificationEvent(
            event_id="evt_overflow",
            event_type=NotificationEventType.TASK_COMPLETED,
            task_id="task_overflow",
            severity=NotificationSeverity.INFO,
            title="Overflow",
            message="This should fail",
        )

        result = await queue.enqueue(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_route_by_severity(self):
        """Test that events are routed correctly by severity."""
        events = [
            NotificationEvent(
                event_id="evt_1",
                event_type=NotificationEventType.TASK_COMPLETED,
                task_id="task_1",
                severity=NotificationSeverity.INFO,
                title="Info",
                message="Info event",
            ),
            NotificationEvent(
                event_id="evt_2",
                event_type=NotificationEventType.BUDGET_WARNING,
                task_id="task_2",
                severity=NotificationSeverity.WARNING,
                title="Warning",
                message="Warning event",
            ),
            NotificationEvent(
                event_id="evt_3",
                event_type=NotificationEventType.SUBSYSTEM_ERROR,
                task_id="task_3",
                severity=NotificationSeverity.ERROR,
                title="Error",
                message="Error event",
            ),
            NotificationEvent(
                event_id="evt_4",
                event_type=NotificationEventType.QUOTA_EXCEEDED,
                task_id="task_4",
                severity=NotificationSeverity.CRITICAL,
                title="Critical",
                message="Critical event",
            ),
        ]

        routes = NotificationQueue._route_by_severity(events)

        # Verify routing
        assert len(routes) >= 2  # At least 2 different routes

        # Verify each severity is routed
        severities = []
        for route, route_events in routes.items():
            for event in route_events:
                severities.append(event.severity.value)

        assert "info" in severities
        assert "warning" in severities
        assert "error" in severities
        assert "critical" in severities


class TestNotificationBroker:
    """Tests for NotificationBroker."""

    @pytest.mark.asyncio
    async def test_broker_emit_event(self, broker):
        """Test emitting a notification event."""
        result = await broker.emit_event(
            event_type=NotificationEventType.TASK_COMPLETED,
            task_id="task_1",
            severity=NotificationSeverity.INFO,
            title="Task Done",
            message="Task completed successfully",
        )

        assert result is True
        assert len(broker._notification_history) == 1

    @pytest.mark.asyncio
    async def test_broker_register_backend(self, broker):
        """Test registering a backend with broker."""
        handler = AsyncMock(return_value=True)
        broker.register_backend("discord", handler)

        assert "discord" in broker.registry.available_channels()

    @pytest.mark.asyncio
    async def test_broker_get_history(self, broker):
        """Test retrieving notification history."""
        for i in range(5):
            await broker.emit_event(
                event_type=NotificationEventType.TASK_COMPLETED,
                task_id=f"task_{i}",
                severity=NotificationSeverity.INFO,
                title=f"Event {i}",
                message=f"Event message {i}",
            )

        history = broker.get_notification_history(limit=3)
        assert len(history) == 3
        # Most recent first
        assert history[0]["title"] == "Event 4"

    @pytest.mark.asyncio
    async def test_broker_filter_history_by_task(self, broker):
        """Test filtering history by task ID."""
        for i in range(5):
            await broker.emit_event(
                event_type=NotificationEventType.TASK_COMPLETED,
                task_id="task_1" if i < 3 else "task_2",
                severity=NotificationSeverity.INFO,
                title=f"Event {i}",
                message=f"Message {i}",
            )

        history = broker.get_notification_history(task_id="task_1")
        assert len(history) == 3
        assert all(e["task_id"] == "task_1" for e in history)

    @pytest.mark.asyncio
    async def test_broker_get_stats(self, broker):
        """Test getting broker statistics."""
        stats = broker.get_stats()

        assert "queue_stats" in stats
        assert "history_size" in stats
        assert "available_channels" in stats
        assert stats["history_size"] == 0


class TestNotificationHandlers:
    """Tests for notification handlers."""

    @pytest.mark.asyncio
    async def test_discord_handler(self):
        """Test Discord handler."""
        handler = DiscordNotificationHandler("https://discord.com/api/webhooks/123/abc")

        event = NotificationEvent(
            event_id="evt_1",
            event_type=NotificationEventType.TASK_COMPLETED,
            task_id="task_1",
            severity=NotificationSeverity.WARNING,
            title="Task Warning",
            message="Task completed with warnings",
            metadata={"warnings": 3},
        )

        result = await handler([event])
        assert result is True

    @pytest.mark.asyncio
    async def test_discord_handler_severity_color(self):
        """Test Discord handler color mapping."""
        handler = DiscordNotificationHandler("https://discord.com/api/webhooks/123/abc")

        assert handler._severity_color("info") == 0x0099FF
        assert handler._severity_color("warning") == 0xFFA500
        assert handler._severity_color("error") == 0xFF0000
        assert handler._severity_color("critical") == 0x8B0000

    @pytest.mark.asyncio
    async def test_slack_handler(self):
        """Test Slack handler."""
        handler = SlackNotificationHandler("https://hooks.slack.com/services/T123/B456/xyz")

        event = NotificationEvent(
            event_id="evt_1",
            event_type=NotificationEventType.BUDGET_WARNING,
            task_id="task_1",
            severity=NotificationSeverity.WARNING,
            title="Budget Low",
            message="Task budget is running low",
            metadata={"remaining": "100 cents"},
        )

        result = await handler([event])
        assert result is True

    @pytest.mark.asyncio
    async def test_email_handler(self):
        """Test Email handler."""
        smtp_config = {
            "host": "smtp.example.com",
            "port": 587,
            "from_addr": "alerts@example.com",
            "to_addr": "ops@example.com",
        }
        handler = EmailNotificationHandler(smtp_config)

        event = NotificationEvent(
            event_id="evt_1",
            event_type=NotificationEventType.TASK_FAILED,
            task_id="task_1",
            severity=NotificationSeverity.ERROR,
            title="Task Failed",
            message="Task execution failed",
        )

        result = await handler([event])
        assert result is True

    @pytest.mark.asyncio
    async def test_logger_handler(self):
        """Test Logger handler (always succeeds)."""
        handler = LoggerNotificationHandler()

        events = [
            NotificationEvent(
                event_id="evt_1",
                event_type=NotificationEventType.TASK_COMPLETED,
                task_id="task_1",
                severity=NotificationSeverity.INFO,
                title="Done",
                message="Task done",
            ),
            NotificationEvent(
                event_id="evt_2",
                event_type=NotificationEventType.TASK_FAILED,
                task_id="task_2",
                severity=NotificationSeverity.ERROR,
                title="Failed",
                message="Task failed",
            ),
        ]

        result = await handler(events)
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
