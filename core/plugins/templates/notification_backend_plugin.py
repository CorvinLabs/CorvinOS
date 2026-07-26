"""Template: custom NotificationBackend plugin (ADR-0033).

Copy this file, rename the class, fill in the TODOs, then install via:
    spec.plugins.installed:
      - id: "com.example.my-notifier"
        class_path: "my_package.my_module:MyNotificationPlugin"
        config:
          webhook_url: "https://..."   # API key goes in vault, never here
"""
from __future__ import annotations

from corvin_plugins.protocol import (  # noqa: F401 - CorvinPlugin is the
    CorvinPlugin,  # structural protocol this class satisfies; kept for reference.
    HealthStatus,
    PluginContext,
)


class MyNotificationPlugin:
    """Replace with your actual class name."""

    plugin_id    = "com.example.my-notifier"   # globally unique reverse-domain
    plugin_type  = "notification_backend"
    version      = "1.0.0"
    display_name = "My Notification Backend"

    def __init__(self) -> None:
        self._config: dict = {}

    # ── CorvinPlugin lifecycle ───────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        # TODO: initialize your client (read API key from vault, not from config)
        if ctx.notification_registry is not None:
            ctx.notification_registry.set_active(self)

    def on_unload(self) -> None:
        # TODO: close connections, flush queues
        pass

    def health_check(self) -> HealthStatus:
        # TODO: check connectivity to your notification endpoint
        return HealthStatus(ok=True, message="ok")

    # ── NotificationBackend capability ───────────────────────────────────────

    def notify(
        self,
        event: str,
        payload: dict,
        *,
        tenant_id: str = "_default",
        severity: str = "info",
    ) -> None:
        # Must NOT block > 100 ms.
        # Must NOT put message content or PII in payload.
        # TODO: send to your external system (PagerDuty, OpsGenie, webhook, …)
        pass
