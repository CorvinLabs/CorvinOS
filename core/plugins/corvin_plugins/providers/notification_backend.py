"""Notification backend provider.

Singleton registry for notifications (alerts, webhooks, etc.).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, Callable
import threading
from enum import Enum

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_backend: Optional['NotificationBackend'] = None


class NotificationLevel(Enum):
    """Notification severity level."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Notification:
    """A notification event."""
    tenant_id: str
    user_id: str
    title: str
    message: str
    level: NotificationLevel
    timestamp: str
    metadata: dict = None


class NotificationBackend(Protocol):
    """Protocol for notification backends."""

    async def send_notification(self, notification: Notification) -> bool:
        """Send a notification."""
        ...

    async def register_webhook(self, tenant_id: str, url: str) -> bool:
        """Register a webhook for notifications."""
        ...

    async def get_notifications(self, tenant_id: str, user_id: str) -> list[Notification]:
        """Get notifications for a user."""
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultNotificationBackend:
    """Default in-process notification backend."""

    def __init__(self):
        """Initialize the notification backend."""
        self._notifications: list[Notification] = []
        self._webhooks: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    async def send_notification(self, notification: Notification) -> bool:
        """Send a notification."""
        try:
            with self._lock:
                self._notifications.append(notification)
                _logger.info(f"Notification sent: {notification.title}")
            return True
        except Exception as e:
            _logger.error(f"Failed to send notification: {e}")
            return False

    async def register_webhook(self, tenant_id: str, url: str) -> bool:
        """Register a webhook."""
        try:
            with self._lock:
                if tenant_id not in self._webhooks:
                    self._webhooks[tenant_id] = []
                if url not in self._webhooks[tenant_id]:
                    self._webhooks[tenant_id].append(url)
            return True
        except Exception as e:
            _logger.error(f"Failed to register webhook: {e}")
            return False

    async def get_notifications(self, tenant_id: str, user_id: str) -> list[Notification]:
        """Get notifications."""
        try:
            with self._lock:
                return [n for n in self._notifications
                        if n.tenant_id == tenant_id and n.user_id == user_id]
        except Exception:
            return []

    async def health_check(self) -> bool:
        """Check backend health."""
        return True


def get_active() -> NotificationBackend:
    """Get the currently active notification backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultNotificationBackend()
        return _active_backend


def set_active(backend: NotificationBackend) -> None:
    """Set the active notification backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend
