"""Notification Handlers — Delivery implementations for Discord, Slack, email (ADR-0368).

Each handler implements the NotificationEvent → Channel delivery protocol.
Handlers are registered with NotificationBroker and called by the queue worker.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DiscordNotificationHandler:
    """Discord webhook notification handler."""

    def __init__(self, webhook_url: str):
        """Initialize Discord handler.

        Args:
            webhook_url: Discord webhook URL (from Server Settings → Webhooks)
        """
        self.webhook_url = webhook_url

    async def __call__(self, events: List[Any]) -> bool:
        """Send notifications to Discord.

        Args:
            events: List of NotificationEvent objects

        Returns:
            True if delivery succeeded
        """
        if not events:
            return True

        try:
            # Build Discord embed message
            embeds = []
            for event in events:
                embed = {
                    "title": event.title,
                    "description": event.message,
                    "color": self._severity_color(event.severity),
                    "fields": [
                        {"name": "Task ID", "value": event.task_id, "inline": True},
                        {"name": "Type", "value": event.event_type.value, "inline": True},
                        {"name": "Severity", "value": event.severity.value, "inline": True},
                    ],
                    "timestamp": event.timestamp,
                }

                # Add metadata fields
                for key, value in event.metadata.items():
                    embed["fields"].append({
                        "name": key,
                        "value": str(value)[:100],  # Truncate long values
                        "inline": True,
                    })

                embeds.append(embed)

            payload = {
                "embeds": embeds,
                "username": "CorvinOS Brain",
                "avatar_url": "https://avatars.githubusercontent.com/u/1234567",
            }

            # Send to webhook (simulated — would use aiohttp)
            logger.info(
                f"Discord: Would send {len(events)} events to webhook "
                f"(first: {events[0].title})"
            )

            # Simulate network delay
            await asyncio.sleep(0.1)
            return True

        except Exception as e:
            logger.error(f"Discord handler error: {e}")
            return False

    @staticmethod
    def _severity_color(severity: str) -> int:
        """Map severity to Discord embed color."""
        colors = {
            "info": 0x0099FF,      # Blue
            "warning": 0xFFA500,   # Orange
            "error": 0xFF0000,     # Red
            "critical": 0x8B0000,  # Dark Red
        }
        return colors.get(severity, 0x808080)


class SlackNotificationHandler:
    """Slack webhook notification handler."""

    def __init__(self, webhook_url: str):
        """Initialize Slack handler.

        Args:
            webhook_url: Slack webhook URL (from Incoming Webhooks)
        """
        self.webhook_url = webhook_url

    async def __call__(self, events: List[Any]) -> bool:
        """Send notifications to Slack.

        Args:
            events: List of NotificationEvent objects

        Returns:
            True if delivery succeeded
        """
        if not events:
            return True

        try:
            # Build Slack blocks message
            blocks = []

            for event in events:
                blocks.extend([
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"🔔 {event.title}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Task:* {event.task_id}\n*Type:* {event.event_type.value}\n*Severity:* {event.severity.value}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": event.message,
                        },
                    },
                ])

                # Add metadata
                if event.metadata:
                    metadata_text = "\n".join(
                        f"*{k}:* {v}" for k, v in event.metadata.items()
                    )
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": metadata_text,
                        },
                    })

                blocks.append({"type": "divider"})

            payload = {
                "blocks": blocks,
                "username": "CorvinOS Brain",
            }

            # Send to webhook (simulated — would use aiohttp)
            logger.info(
                f"Slack: Would send {len(events)} events to webhook "
                f"(first: {events[0].title})"
            )

            # Simulate network delay
            await asyncio.sleep(0.1)
            return True

        except Exception as e:
            logger.error(f"Slack handler error: {e}")
            return False


class EmailNotificationHandler:
    """Email notification handler."""

    def __init__(self, smtp_config: Dict[str, Any]):
        """Initialize email handler.

        Args:
            smtp_config: SMTP configuration (host, port, from_addr, credentials)
        """
        self.smtp_config = smtp_config
        self.recipient_addr = smtp_config.get("to_addr", "alerts@example.com")

    async def __call__(self, events: List[Any]) -> bool:
        """Send notifications via email.

        Args:
            events: List of NotificationEvent objects

        Returns:
            True if delivery succeeded
        """
        if not events:
            return True

        try:
            # Build email body
            subject_parts = []
            body_parts = []

            for event in events:
                subject_parts.append(f"{event.event_type.value}: {event.title}")

                body_parts.extend([
                    f"Event: {event.event_type.value}",
                    f"Severity: {event.severity.value}",
                    f"Task ID: {event.task_id}",
                    f"Title: {event.title}",
                    f"Message: {event.message}",
                    "",
                ])

            subject = " | ".join(subject_parts[:5])  # Truncate
            body = "\n".join(body_parts)

            # Send email (simulated — would use aiosmtplib)
            logger.info(
                f"Email: Would send {len(events)} events to {self.recipient_addr} "
                f"(subject: {subject})"
            )

            # Simulate network delay
            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            logger.error(f"Email handler error: {e}")
            return False


class LoggerNotificationHandler:
    """Logging-only handler (default, always available)."""

    async def __call__(self, events: List[Any]) -> bool:
        """Log notifications.

        Args:
            events: List of NotificationEvent objects

        Returns:
            Always True (logging never fails)
        """
        for event in events:
            level = {
                "info": logging.INFO,
                "warning": logging.WARNING,
                "error": logging.ERROR,
                "critical": logging.CRITICAL,
            }.get(event.severity.value, logging.INFO)

            logger.log(
                level,
                f"[{event.event_type.value}] {event.title}: {event.message} "
                f"(task={event.task_id})"
            )

        return True
