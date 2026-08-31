"""Intelligent Async Notification Broker — Event-Driven Task Notifications (ADR-0368).

Enables asynchronous notifications for task completion, errors, budget warnings,
and progress updates via configurable channels (Discord, Slack, email, etc).

Key Mechanisms:
- NotificationQueue: Async queue for event batching
- NotificationRoute: Channel + delivery policy (latency, batch size)
- NotificationBackendRegistry: Pluggable delivery backends
- Exponential backoff retry for failed deliveries
- Loss function: notification_latency / 5000ms (target <500ms)
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class NotificationSeverity(str, Enum):
    """Severity levels for notifications."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationEventType(str, Enum):
    """Types of events that trigger notifications."""
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    HEALTH_DEGRADED = "health_degraded"
    SUBSYSTEM_ERROR = "subsystem_error"
    BUDGET_WARNING = "budget_warning"
    QUOTA_EXCEEDED = "quota_exceeded"
    STRATEGY_CHANGE = "strategy_change"
    PROGRESS_CHECKPOINT = "progress_checkpoint"


@dataclass
class NotificationEvent:
    """Immutable notification event."""
    event_id: str
    event_type: NotificationEventType
    task_id: str
    severity: NotificationSeverity
    title: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
        }


@dataclass(frozen=True)
class NotificationRoute:
    """Routing policy for notifications (frozen for use as dict key)."""
    channels: tuple = field(default_factory=tuple)  # ("discord", "slack", "email")
    delay_ms: int = 0  # Delivery delay (for batching)
    batch_size: int = 1  # Batch N events before sending
    retry_count: int = 3
    timeout_ms: int = 5000


class NotificationBackendRegistry:
    """Registry for notification delivery backends."""

    def __init__(self):
        self._backends: Dict[str, Callable] = {}

    def register(self, channel: str, handler: Callable) -> None:
        """Register a delivery handler for a channel.

        Args:
            channel: Channel name (e.g., 'discord', 'slack')
            handler: Async callable(events: List[NotificationEvent]) -> bool
                    Returns True if delivery succeeded
        """
        self._backends[channel] = handler
        logger.info(f"Registered notification backend: {channel}")

    def get_handler(self, channel: str) -> Optional[Callable]:
        """Get handler for channel."""
        return self._backends.get(channel)

    def available_channels(self) -> List[str]:
        """Get list of available notification channels."""
        return list(self._backends.keys())


class NotificationQueue:
    """Async queue for batching and delivering notifications."""

    def __init__(
        self,
        registry: NotificationBackendRegistry,
        batch_timeout_ms: int = 5000,
        max_queue_size: int = 10000,
    ):
        self.registry = registry
        self.batch_timeout_ms = batch_timeout_ms
        self.max_queue_size = max_queue_size
        self._queue: asyncio.Queue[NotificationEvent] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._delivered_count = 0
        self._failed_count = 0

    async def start(self) -> None:
        """Start the notification worker."""
        if self._running:
            logger.warning("NotificationQueue already running")
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("NotificationQueue started")

    async def stop(self) -> None:
        """Stop the notification worker."""
        if not self._running:
            return

        self._running = False
        if self._worker_task:
            await self._worker_task
        logger.info(
            f"NotificationQueue stopped (delivered={self._delivered_count}, "
            f"failed={self._failed_count})"
        )

    async def enqueue(self, event: NotificationEvent) -> bool:
        """Enqueue a notification event.

        Args:
            event: NotificationEvent to deliver

        Returns:
            True if enqueued, False if queue full
        """
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.warning(f"Notification queue full, dropping event: {event.event_id}")
            return False

    async def _worker_loop(self) -> None:
        """Background worker that batches and delivers notifications."""
        while self._running:
            try:
                # Collect events for batch
                batch: List[NotificationEvent] = []
                deadline = asyncio.get_event_loop().time() + (
                    self.batch_timeout_ms / 1000
                )

                # Collect up to 100 events or timeout
                while len(batch) < 100:
                    timeout = max(0, deadline - asyncio.get_event_loop().time())
                    try:
                        event = await asyncio.wait_for(
                            self._queue.get(),
                            timeout=timeout,
                        )
                        batch.append(event)
                    except asyncio.TimeoutError:
                        break

                if batch:
                    await self._deliver_batch(batch)

            except Exception as e:
                logger.error(f"Error in notification worker: {e}")
                await asyncio.sleep(1)

    async def _deliver_batch(self, events: List[NotificationEvent]) -> None:
        """Deliver a batch of events to configured channels."""
        # Group events by route
        routes_by_severity = self._route_by_severity(events)

        for route, batch in routes_by_severity.items():
            await self._deliver_to_route(route, batch)

    async def _deliver_to_route(
        self,
        route: NotificationRoute,
        events: List[NotificationEvent],
    ) -> None:
        """Deliver batch to a specific route (set of channels)."""
        for channel in route.channels:
            handler = self.registry.get_handler(channel)
            if not handler:
                logger.warning(f"No handler for channel: {channel}")
                continue

            # Retry with exponential backoff
            retry_delay = 0.5  # Start at 500ms
            for attempt in range(route.retry_count):
                try:
                    success = await asyncio.wait_for(
                        handler(events),
                        timeout=route.timeout_ms / 1000,
                    )

                    if success:
                        self._delivered_count += len(events)
                        logger.info(
                            f"Delivered {len(events)} events to {channel} "
                            f"(attempt {attempt + 1})"
                        )
                        break
                    else:
                        logger.warning(
                            f"Delivery to {channel} returned False (attempt {attempt + 1})"
                        )

                except asyncio.TimeoutError:
                    logger.warning(f"Delivery to {channel} timed out (attempt {attempt + 1})")

                except Exception as e:
                    logger.error(f"Delivery to {channel} failed: {e} (attempt {attempt + 1})")

                # Exponential backoff
                if attempt < route.retry_count - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10)  # Cap at 10s
                else:
                    self._failed_count += len(events)

    @staticmethod
    def _route_by_severity(
        events: List[NotificationEvent],
    ) -> Dict[NotificationRoute, List[NotificationEvent]]:
        """Route events by severity to appropriate channels."""
        routes: Dict[NotificationRoute, List[NotificationEvent]] = {}

        for event in events:
            # Route by severity
            if event.severity == NotificationSeverity.CRITICAL:
                route = NotificationRoute(
                    channels=["discord", "slack", "email"],
                    delay_ms=0,
                    batch_size=1,
                )
            elif event.severity == NotificationSeverity.ERROR:
                route = NotificationRoute(
                    channels=["discord", "slack"],
                    delay_ms=500,
                    batch_size=5,
                )
            elif event.severity == NotificationSeverity.WARNING:
                route = NotificationRoute(
                    channels=["discord"],
                    delay_ms=1000,
                    batch_size=10,
                )
            else:  # INFO
                route = NotificationRoute(
                    channels=["discord"],
                    delay_ms=5000,
                    batch_size=100,
                )

            if route not in routes:
                routes[route] = []
            routes[route].append(event)

        return routes

    def get_stats(self) -> Dict[str, int]:
        """Get delivery statistics."""
        return {
            "queue_size": self._queue.qsize(),
            "delivered": self._delivered_count,
            "failed": self._failed_count,
            "running": self._running,
        }


class NotificationBroker:
    """Main notification subsystem for Brain.

    Responsibilities:
    - Listen to subsystem events (health_degraded, budget_warning, task_completed)
    - Route notifications to configured channels (Discord, Slack, email)
    - Handle batching and delivery with exponential backoff
    - Provide metrics (delivery latency, success rate)
    """

    VERSION = "1.0.0"

    def __init__(self, corvin_home: Optional[str] = None):
        """Initialize NotificationBroker.

        Args:
            corvin_home: Path to CORVIN_HOME (for config)
        """
        self.registry = NotificationBackendRegistry()
        self.queue = NotificationQueue(self.registry)
        self._event_handlers: Dict[NotificationEventType, Callable] = {}
        self._notification_history: List[NotificationEvent] = []
        self._max_history = 1000

    async def start(self) -> None:
        """Start the notification broker."""
        await self.queue.start()
        logger.info("NotificationBroker started")

    async def stop(self) -> None:
        """Stop the notification broker."""
        await self.queue.stop()
        logger.info("NotificationBroker stopped")

    def register_backend(self, channel: str, handler: Callable) -> None:
        """Register a notification delivery backend.

        Args:
            channel: Channel name (e.g., 'discord', 'slack')
            handler: Async callable(events: List[NotificationEvent]) -> bool
        """
        self.registry.register(channel, handler)

    async def emit_event(
        self,
        event_type: NotificationEventType,
        task_id: str,
        severity: NotificationSeverity,
        title: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Emit a notification event.

        Args:
            event_type: Type of event
            task_id: Related task ID
            severity: Severity level
            title: Short title for notification
            message: Detailed message
            metadata: Optional additional data

        Returns:
            True if event enqueued successfully
        """
        event = NotificationEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            task_id=task_id,
            severity=severity,
            title=title,
            message=message,
            metadata=metadata or {},
        )

        # Store in history
        self._notification_history.append(event)
        if len(self._notification_history) > self._max_history:
            self._notification_history.pop(0)

        logger.info(
            f"Emitting {event_type.value} for task {task_id}: {title}"
        )
        return await self.queue.enqueue(event)

    def get_notification_history(
        self,
        task_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get notification history.

        Args:
            task_id: Optional filter by task ID
            limit: Maximum number of notifications to return

        Returns:
            List of notification events (dicts)
        """
        events = self._notification_history
        if task_id:
            events = [e for e in events if e.task_id == task_id]

        # Return most recent first
        return [e.to_dict() for e in reversed(events[-limit:])]

    def get_stats(self) -> Dict[str, Any]:
        """Get broker statistics."""
        return {
            "queue_stats": self.queue.get_stats(),
            "history_size": len(self._notification_history),
            "available_channels": self.registry.available_channels(),
        }
