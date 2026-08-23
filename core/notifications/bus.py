"""Notification bus for orchestration updates (Discord, Slack, etc.).

Decoupled from orchestration logic; subscribed by bridge adapters
(Discord, Slack, email) to push real-time phase updates.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

class NotificationLevel(str, Enum):
    """Severity levels for notifications."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class NotificationChannel(str, Enum):
    """Delivery channels."""
    DISCORD = "discord"
    SLACK = "slack"
    EMAIL = "email"
    LOG = "log"

@dataclass
class Notification:
    """Immutable notification record."""
    notification_id: str
    task_id: str
    message: str
    level: NotificationLevel
    channel: NotificationChannel
    created_at: datetime
    metadata: Dict = None

    def to_dict(self) -> Dict:
        """Serialize for delivery."""
        return {
            "notification_id": self.notification_id,
            "task_id": self.task_id,
            "message": self.message,
            "level": self.level.value,
            "channel": self.channel.value,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata or {},
        }

class NotificationBus:
    """Central bus for orchestration notifications.

    Responsibilities:
    - Queue notifications from orchestrator
    - Route to subscribed channels (Discord, Slack, etc.)
    - Tenant-isolated (each bus bound to a tenant_id)
    - Async delivery (fire-and-forget, non-blocking)
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._subscribers: Dict[NotificationChannel, List[Callable]] = {
            channel: [] for channel in NotificationChannel
        }
        self._delivered: List[Notification] = []

    def subscribe(
        self,
        channel: NotificationChannel,
        handler: Callable,
    ) -> None:
        """Subscribe a handler to a channel.

        Args:
            channel: NotificationChannel to subscribe to
            handler: async callable(notification: Notification) -> None
        """
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(handler)

    def unsubscribe(
        self,
        channel: NotificationChannel,
        handler: Callable,
    ) -> None:
        """Unsubscribe a handler."""
        if channel in self._subscribers:
            self._subscribers[channel] = [
                h for h in self._subscribers[channel] if h != handler
            ]

    async def publish(
        self,
        notification_id: str,
        task_id: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        channel: NotificationChannel = NotificationChannel.DISCORD,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Publish a notification (non-blocking).

        Args:
            notification_id: Unique ID
            task_id: Associated task
            message: Notification text (PII-scrubbed)
            level: Severity
            channel: Target channel
            metadata: Additional context (tenant-scoped)
        """
        notif = Notification(
            notification_id=notification_id,
            task_id=task_id,
            message=message,
            level=level,
            channel=channel,
            created_at=datetime.utcnow(),
            metadata=metadata or {},
        )
        await self._queue.put(notif)
        self._delivered.append(notif)

    async def process_queue(self) -> None:
        """Process queued notifications (run in background).

        Delivers to all subscribed handlers for the notification's channel.
        Best-effort: exceptions in handlers don't block queue processing.
        """
        while True:
            try:
                notif = await self._queue.get()
                await self._deliver(notif)
                self._queue.task_done()
            except asyncio.CancelledError:
                logger.info("NotificationBus.process_queue cancelled")
                break
            except Exception as e:
                logger.error(f"Error in process_queue: {e}")

    async def _deliver(self, notif: Notification) -> None:
        """Deliver a single notification to subscribed handlers."""
        handlers = self._subscribers.get(notif.channel, [])
        for handler in handlers:
            try:
                await handler(notif)
            except Exception as e:
                logger.warning(
                    f"Handler failed for {notif.channel}: {e}",
                    exc_info=True,
                )

    def get_delivered(self, task_id: Optional[str] = None) -> List[Notification]:
        """Get delivery history.

        Args:
            task_id: Filter by task (optional)

        Returns:
            List of delivered notifications
        """
        if task_id:
            return [n for n in self._delivered if n.task_id == task_id]
        return list(self._delivered)

    async def flush(self) -> None:
        """Wait for all queued notifications to be delivered."""
        await self._queue.join()
