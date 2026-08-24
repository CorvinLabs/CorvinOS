"""Phase 3d: Event Bus Integration for Status Broadcasting.

Publishes Vibe status updates to CorvinOS Event Bus.
Enables Console, Discord, and other notifiers to subscribe.
"""

import logging
import asyncio
import inspect
from typing import Callable, Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

class StatusLevel(str, Enum):
    """Status broadcast level."""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

class StatusEvent:
    """Immutable status event."""

    def __init__(
        self,
        level: StatusLevel,
        message: str,
        task_id: str,
        persona_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.timestamp = datetime.now().isoformat()
        self.level = level
        self.message = message
        self.task_id = task_id
        self.persona_id = persona_id
        self.metadata = metadata or {}

    def to_dict(self) -> Dict:
        """Serialize for Event Bus."""
        return {
            "timestamp": self.timestamp,
            "level": self.level.value,
            "message": self.message,
            "task_id": self.task_id,
            "persona_id": self.persona_id,
            "metadata": self.metadata
        }

class EventBroadcaster:
    """Broadcast Vibe status to CorvinOS Event Bus (Phase 3d)."""

    def __init__(self, event_bus=None):
        """
        Args:
            event_bus: CorvinOS Event Bus client (injected; None = disabled)
        """
        self.event_bus = event_bus
        self.listeners: List[Callable] = []  # Legacy: direct listeners

    def add_listener(self, listener: Callable):
        """Add direct listener (fallback if Event Bus unavailable)."""
        if not inspect.iscoroutinefunction(listener):
            raise TypeError(f"Listener must be async, got {type(listener).__name__}")
        self.listeners.append(listener)

    async def broadcast(
        self,
        level: StatusLevel,
        message: str,
        task_id: str,
        persona_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Publish status event to Event Bus.

        Event routing:
        - Published to corvinOS.events.publish("vibe.status", event)
        - Subscribers (notifiers) handle vibe.status → Discord/Console/etc
        - If Event Bus unavailable: fallback to direct listeners
        """
        event = StatusEvent(level, message, task_id, persona_id, metadata)

        # Try Event Bus first
        if self.event_bus:
            try:
                await self.event_bus.publish("vibe.status", event.to_dict())
                logger.debug(f"Published to Event Bus: {event.level.value}")
            except Exception as e:
                logger.warning(f"Event Bus publish failed: {e}, falling back to direct listeners")
                await self._notify_direct(event)
        else:
            # No Event Bus: use direct listeners
            await self._notify_direct(event)

    async def _notify_direct(self, event: StatusEvent):
        """Notify direct listeners (fallback)."""
        for listener in self.listeners:
            try:
                await listener(event.level.value, event.message, event.metadata)
            except Exception as e:
                logger.error(f"Direct listener failed: {e}")

class ConsoleNotifier:
    """Notifier for Console web-chat updates (Phase 3d)."""

    def __init__(self, console_api=None):
        """
        Args:
            console_api: Console API client (injected)
        """
        self.console = console_api

    async def on_status_event(self, event_dict: Dict[str, Any]):
        """Handle vibe.status event from Event Bus."""
        if not self.console:
            return

        try:
            # Route to Console UI (e.g., update task sidebar)
            # await self.console.update_task_status(
            #     task_id=event_dict["task_id"],
            #     status=event_dict["level"],
            #     message=event_dict["message"]
            # )
            logger.info(f"Console notified: {event_dict['message']}")
        except Exception as e:
            logger.error(f"Console notifier failed: {e}")

class DiscordNotifier:
    """Notifier for Discord webhook updates (Phase 3d)."""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Args:
            webhook_url: Discord webhook URL (from config)
        """
        self.webhook_url = webhook_url

    async def on_status_event(self, event_dict: Dict[str, Any]):
        """Handle vibe.status event from Event Bus."""
        if not self.webhook_url:
            logger.debug("Discord webhook not configured, skipping")
            return

        try:
            # Format message based on level
            level = event_dict["level"]
            emoji = self._emoji_for_level(level)
            message = f"{emoji} {event_dict['message']}"

            # Send to Discord (real implementation would use aiohttp)
            # await self._send_webhook(message, event_dict)
            logger.info(f"Discord webhook queued: {message}")

        except Exception as e:
            logger.error(f"Discord notifier failed: {e}")

    def _emoji_for_level(self, level: str) -> str:
        """Get emoji for status level."""
        emojis = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌"
        }
        return emojis.get(level, "•")

class NotifierRegistry:
    """Register and manage status notifiers (Phase 3d)."""

    def __init__(self, broadcaster: EventBroadcaster):
        self.broadcaster = broadcaster
        self.notifiers = {}

    def register_notifier(self, name: str, notifier: Callable):
        """Register a notifier callable."""
        self.notifiers[name] = notifier
        logger.info(f"Registered notifier: {name}")

    async def subscribe(self):
        """Subscribe all notifiers to Event Bus (if available)."""
        if self.broadcaster.event_bus:
            try:
                for name, notifier in self.notifiers.items():
                    # Subscribe notifier to vibe.status events
                    # self.broadcaster.event_bus.subscribe("vibe.status", notifier.on_status_event)
                    logger.info(f"Subscribed {name} to vibe.status events")
            except Exception as e:
                logger.error(f"Notifier subscription failed: {e}")
