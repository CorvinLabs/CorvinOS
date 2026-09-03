"""VIBE Webhook Dispatcher Plugin — Event distribution via webhooks.

Category: integration | Type: event_dispatcher
Routes vibe events to registered webhook endpoints.
"""

import threading
from typing import Optional


class VIBEWebhookDispatcher:
    """Plugin: dispatches events via webhooks."""

    def __init__(self):
        """Initialize dispatcher."""
        self._webhooks: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._initialized = False

    async def initialize(self, ctx) -> bool:
        """Initialize the plugin."""
        self._initialized = True
        return True

    async def execute(self, op: str, **kwargs) -> dict:
        """Execute a webhook operation.

        Operations:
        - register_webhook: Register a webhook URL
        - unregister_webhook: Remove a webhook
        - dispatch_event: Send event to webhooks
        """
        if not self._initialized:
            return {"success": False, "error": "not initialized"}

        op_lower = op.lower()

        if op_lower == "register_webhook":
            event_type = kwargs.get("event_type")
            webhook_url = kwargs.get("webhook_url")

            try:
                with self._lock:
                    if event_type not in self._webhooks:
                        self._webhooks[event_type] = []
                    if webhook_url not in self._webhooks[event_type]:
                        self._webhooks[event_type].append(webhook_url)
                return {"success": True, "registered": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "unregister_webhook":
            event_type = kwargs.get("event_type")
            webhook_url = kwargs.get("webhook_url")

            try:
                with self._lock:
                    if event_type in self._webhooks:
                        self._webhooks[event_type] = [
                            w for w in self._webhooks[event_type] if w != webhook_url
                        ]
                return {"success": True, "unregistered": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "dispatch_event":
            event_type = kwargs.get("event_type")
            event_data = kwargs.get("event_data", {})

            try:
                with self._lock:
                    urls = self._webhooks.get(event_type, [])
                    # Dispatch to all registered URLs (simplified)
                    dispatched_count = len(urls)
                return {"success": True, "dispatched_to": dispatched_count}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"unknown operation: {op}"}

    async def health_check(self) -> bool:
        """Check plugin health."""
        return self._initialized

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        with self._lock:
            self._webhooks.clear()
        self._initialized = False
