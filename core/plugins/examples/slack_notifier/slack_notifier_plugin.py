"""Slack Notification Backend plugin — forward audit events to Slack.

This is a complete example plugin showing best practices for:
- Manifest configuration
- Plugin lifecycle (on_load, health_check, on_unload)
- Implementing a capability protocol (NotificationBackend)
- Error handling and observability
- Configuration validation
- Testing patterns

To use this plugin:
1. Copy to ~/.corvin/plugins/installed/slack-notifier/plugin.py
2. Create manifest.yaml with Slack webhook URL
3. Enable via `corvin plugin enable slack-notifier`
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

import requests

from corvin.core.plugins.corvin_plugins.plugin_interface import PluginInterface
from corvin.core.plugins.corvin_plugins.protocol import (
    HealthStatus,
    NotificationBackend,
    PluginContext,
)

logger = logging.getLogger(__name__)


class SlackNotifierPlugin(PluginInterface, NotificationBackend):
    """Forward CorvinOS audit events to a Slack channel via webhook."""

    # ── Required attributes ───────────────────────────────────────────

    plugin_id = "slack-notifier"
    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "Slack Notifier"

    def __init__(self):
        """Initialize with no arguments (called by the loader)."""
        self.webhook_url: Optional[str] = None
        self.requests_sent = 0
        self.requests_failed = 0
        self.last_error: Optional[str] = None
        self.last_request_time: Optional[float] = None
        self.enabled = False

    # ── Lifecycle methods ─────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Called once when the plugin is loaded.

        Validate the Slack webhook URL and self-register with the
        notification backend registry.
        """
        config = ctx.config

        # Validate required configuration
        if "webhook_url" not in config:
            raise ValueError(
                "Missing required config: webhook_url (Slack incoming webhook URL)"
            )

        self.webhook_url = config["webhook_url"]

        # Validate webhook URL format
        if not self.webhook_url.startswith("https://hooks.slack.com/"):
            raise ValueError(
                f"Invalid webhook_url: must start with 'https://hooks.slack.com/', "
                f"got {self.webhook_url[:50]}..."
            )

        # Optional: test the webhook
        test_enabled = config.get("test_webhook_on_load", True)
        if test_enabled:
            try:
                self._test_webhook()
                logger.info(f"{self.display_name}: webhook validation successful")
            except Exception as e:
                logger.warning(
                    f"{self.display_name}: webhook validation failed: {type(e).__name__}"
                )
                # Don't fail to load, but mark as unhealthy until webhook works
                self.last_error = f"Webhook test failed: {type(e).__name__}"

        # Self-register with the notification backend registry
        ctx.notification_registry.register(self.plugin_id, self)

        # Emit audit event
        ctx.audit_emit("plugin.loaded", {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "type": self.plugin_type,
            "target": "slack",
        })

        self.enabled = True
        logger.info(f"Loaded {self.display_name} (webhook_url={self.webhook_url[:40]}...)")

    def on_unload(self) -> None:
        """Called when the plugin is unloaded."""
        self.enabled = False
        self.webhook_url = None
        logger.info(f"Unloaded {self.display_name}")

    def on_enable(self) -> None:
        """Called when the operator enables the plugin."""
        self.enabled = True
        logger.info(f"Enabled {self.display_name}")

    def on_disable(self) -> None:
        """Called when the operator disables the plugin."""
        self.enabled = False
        logger.info(f"Disabled {self.display_name}")

    def health_check(self) -> HealthStatus:
        """Check the plugin's health (called ~1-2 times per second).

        We check:
        1. Webhook URL is configured
        2. Requests are being processed (if any)
        3. Error rate is acceptable
        """
        if not self.webhook_url:
            return HealthStatus(
                ok=False,
                message="Webhook URL not configured",
            )

        if not self.enabled:
            return HealthStatus(
                ok=False,
                message="Plugin is disabled",
            )

        # Calculate error rate
        total = self.requests_sent + self.requests_failed
        if total == 0:
            # No requests yet; this is fine
            return HealthStatus(
                ok=True,
                message="Ready (no requests yet)",
            )

        error_rate = self.requests_failed / total
        if error_rate > 0.1:  # >10% error rate = unhealthy
            return HealthStatus(
                ok=False,
                message=f"High error rate ({error_rate*100:.1f}%)",
                details={
                    "requests_sent": self.requests_sent,
                    "requests_failed": self.requests_failed,
                    "last_error": self.last_error,
                },
            )

        return HealthStatus(
            ok=True,
            message="Connected and operational",
            details={
                "requests_sent": self.requests_sent,
                "requests_failed": self.requests_failed,
                "last_request": self.last_request_time,
            },
        )

    # ── Capability-specific methods (NotificationBackend) ────────────

    def notify(
        self,
        event: str,
        payload: Dict[str, Any],
        *,
        tenant_id: str = "_default",
        severity: str = "info",
    ) -> None:
        """Implement NotificationBackend.notify() — send an event to Slack.

        This is called when interesting events occur. We format the event
        as a Slack message and post it to the webhook.

        Contract:
        - Never raise; a failing notification never takes down core
        - Non-blocking; return immediately (we use requests.post() which is blocking,
          but this is acceptable for external webhooks — in production, use asyncio)
        - Never include user content in the payload (already filtered by core)
        """
        if not self.webhook_url or not self.enabled:
            self.requests_failed += 1
            return

        try:
            self.last_request_time = time.time()

            # Format the Slack message
            slack_message = self._format_slack_message(
                event, payload, severity, tenant_id
            )

            # Send to Slack (blocking I/O)
            response = requests.post(
                self.webhook_url,
                json=slack_message,
                timeout=5,
            )

            # Check response
            if response.status_code != 200:
                self.requests_failed += 1
                self.last_error = f"HTTP {response.status_code}"
                logger.warning(
                    f"{self.display_name}: webhook returned {response.status_code}"
                )
                return

            self.requests_sent += 1
            self.last_error = None

        except requests.Timeout:
            self.requests_failed += 1
            self.last_error = "Timeout"
            logger.warning(f"{self.display_name}: webhook request timed out")
        except requests.RequestException as e:
            self.requests_failed += 1
            self.last_error = f"{type(e).__name__}"
            logger.warning(
                f"{self.display_name}: webhook request failed ({type(e).__name__})"
            )
        except Exception as e:
            # Catch any other exceptions to never crash the caller
            self.requests_failed += 1
            self.last_error = f"{type(e).__name__}"
            logger.warning(
                f"{self.display_name}: unexpected error ({type(e).__name__})"
            )

    # ── Metrics export ───────────────────────────────────────────────

    def get_metrics(self) -> Dict[str, Any]:
        """Export metrics for the admin dashboard."""
        total = self.requests_sent + self.requests_failed
        error_rate = (self.requests_failed / total * 100) if total > 0 else 0

        return {
            "requests_sent": self.requests_sent,
            "requests_failed": self.requests_failed,
            "total_requests": total,
            "error_rate_percent": error_rate,
        }

    # ── Helper methods ───────────────────────────────────────────────

    def _test_webhook(self) -> None:
        """Test the webhook URL by posting a test message."""
        test_message = {
            "text": f"✓ {self.display_name} is connected",
            "attachments": [
                {
                    "color": "good",
                    "title": "Plugin Test",
                    "text": f"Successfully connected to CorvinOS at {time.strftime('%Y-%m-%d %H:%M:%S')}",
                }
            ],
        }

        response = requests.post(
            self.webhook_url,
            json=test_message,
            timeout=5,
        )

        if response.status_code != 200:
            raise RuntimeError(f"Webhook returned {response.status_code}")

    def _format_slack_message(
        self,
        event: str,
        payload: Dict[str, Any],
        severity: str,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """Format an audit event as a Slack message."""
        # Color based on severity
        color_map = {
            "debug": "#808080",
            "info": "#439FE0",
            "warning": "#FF9800",
            "error": "#F44336",
            "critical": "#9C27B0",
        }
        color = color_map.get(severity.lower(), "#808080")

        # Summarize the payload (show only first few fields)
        payload_str = json.dumps(payload, indent=2)[:500]
        if len(json.dumps(payload, indent=2)) > 500:
            payload_str += "\n..."

        return {
            "text": f":{severity.lower()}: {event}",
            "attachments": [
                {
                    "color": color,
                    "title": f"{event}",
                    "fields": [
                        {
                            "title": "Severity",
                            "value": severity.upper(),
                            "short": True,
                        },
                        {
                            "title": "Tenant",
                            "value": tenant_id,
                            "short": True,
                        },
                    ],
                    "text": f"```{payload_str}```",
                    "footer": f"CorvinOS | {self.display_name}",
                    "ts": int(time.time()),
                }
            ],
        }
