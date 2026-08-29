"""Example Audit Backend Plugin — minimal implementation showing the pattern.

This is a template for building an audit_backend plugin. The audit backend receives
audit events AFTER the core audit writer has already written them to audit.jsonl.

This plugin is a secondary sink; it never suppresses or rewrites the core audit trail.
See the AuditBackend protocol in core/plugins/corvin_plugins/protocol.py for details.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from corvin.core.plugins.corvin_plugins.plugin_interface import PluginInterface
from corvin.core.plugins.corvin_plugins.protocol import (
    AuditBackend,
    HealthStatus,
    PluginContext,
)

logger = logging.getLogger(__name__)


class ExampleAuditBackend(PluginInterface, AuditBackend):
    """Example audit backend that stores events in memory.

    In production, you would send events to:
    - PostgreSQL, MySQL, or other database
    - S3 or other object storage
    - A SIEM (Splunk, ELK, Datadog, etc.)
    - A remote logging service

    This example just stores them in a list for demonstration.
    """

    # ── Required attributes ───────────────────────────────────────────

    plugin_id = "example-audit-backend"
    plugin_type = "audit_backend"
    version = "0.1.0"
    display_name = "Example Audit Backend"

    def __init__(self):
        """Initialize with no arguments (called by the loader)."""
        self.events: list[Dict[str, Any]] = []
        self.is_connected = False

    # ── Lifecycle methods ─────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Called once when the plugin is loaded.

        **Your tasks:**
        1. Validate config from ctx.config
        2. Connect to the external system (database, API, etc.)
        3. Self-register with the capability registry
        4. Emit an audit event for your load

        **Arguments:**
            ctx: PluginContext with config, registries, audit_emit, etc.

        **Contract:**
        - Raise an exception if configuration is invalid (prevents load)
        - Do NOT block for more than 10 seconds
        """
        # Get config
        config = ctx.config
        logger.info(f"Loading {self.display_name} with config: {config}")

        # Example: validate required config
        if "destination" not in config:
            raise ValueError("Missing required config: destination")

        # Example: connect to the backend
        # In this example, we just set a flag
        self.is_connected = True

        # Example: self-register with the registry
        # The registry knows to hand us events now
        ctx.audit_registry.register(self.plugin_id, self)

        # Example: emit an audit event for our own load
        ctx.audit_emit("plugin.loaded", {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "type": self.plugin_type,
        })

        logger.info(f"Successfully loaded {self.display_name}")

    def on_unload(self) -> None:
        """Called when the plugin is unloaded.

        **Your tasks:**
        1. Release external resources (close connections, etc.)
        2. Clean up any state

        **Contract:**
        - Complete quickly (target: <100ms)
        - Do NOT raise exceptions (they are logged but not re-raised)
        - Idempotent (safe to call multiple times)
        """
        try:
            if self.is_connected:
                # Example: close connection
                logger.info(f"Closing {self.display_name}")
                self.is_connected = False
        except Exception as e:
            # Log the error but do NOT raise
            logger.warning(f"Error closing {self.display_name}: {e}")

    def health_check(self) -> HealthStatus:
        """Check the plugin's health (called ~1-2 times per second).

        **Contract:**
        - Return immediately (max 2 seconds, target <100ms)
        - Do NOT block I/O (use cached state only)
        - Do NOT raise exceptions (they are logged and the plugin marked unhealthy)

        **Returns:**
            HealthStatus(ok=True/False, message="...", details={...})
        """
        if not self.is_connected:
            return HealthStatus(
                ok=False,
                message="Not connected",
            )

        return HealthStatus(
            ok=True,
            message=f"Connected and operational ({len(self.events)} events received)",
            details={
                "is_connected": self.is_connected,
                "events_stored": len(self.events),
            },
        )

    # ── Optional lifecycle hooks ──────────────────────────────────────

    def on_enable(self) -> None:
        """Called when the operator enables the plugin (optional)."""
        logger.info(f"Enabled {self.display_name}")

    def on_disable(self) -> None:
        """Called when the operator disables the plugin (optional)."""
        logger.info(f"Disabled {self.display_name}")

    # ── Capability-specific methods (AuditBackend) ───────────────────

    def fanout(
        self,
        event_type: str,
        details: Dict[str, Any],
        *,
        severity: str = "INFO",
        tenant_id: str = "_default",
    ) -> None:
        """Implement AuditBackend.fanout() — receive an audit event.

        This is called by the core audit writer AFTER it has already written
        the event to its own hash-chained audit.jsonl. We receive a COPY and
        can never suppress or rewrite it.

        **Arguments:**
            event_type: Type of event (e.g., "plugin.loaded", "user.login")
            details: Event details (already safe; no PII)
            severity: Severity level (DEBUG, INFO, WARNING, ERROR)
            tenant_id: The tenant this event belongs to

        **Contract:**
        - Never raise (a failing backend never takes down core)
        - Never block for more than 100ms (ideally <10ms)
        - Never try to suppress or rewrite the event
        - Only log the exception class, never the message (no PII)
        """
        try:
            # In this example, we just store the event in memory
            self.events.append({
                "event_type": event_type,
                "details": details,
                "severity": severity,
                "tenant_id": tenant_id,
            })

            logger.debug(f"Fanout received: {event_type} ({severity})")

        except Exception as e:
            # Never raise; just log the exception class
            logger.warning(
                f"{self.display_name}: fanout raised {type(e).__name__}"
            )

    def verify_chain(self) -> HealthStatus:
        """Verify that OUR copy of the audit chain is intact.

        This is NOT consulted to verify the CORE chain. Only report on
        our own backend's copy.

        **Returns:**
            HealthStatus indicating whether our copy is intact
        """
        try:
            # In this example, we just check if we're connected
            if not self.is_connected:
                return HealthStatus(
                    ok=False,
                    message="Not connected",
                )

            return HealthStatus(
                ok=True,
                message=f"Backend chain intact ({len(self.events)} events)",
                details={"events_stored": len(self.events)},
            )
        except Exception as e:
            return HealthStatus(
                ok=False,
                message=f"Verification failed: {type(e).__name__}",
            )

    def enforce_retention(
        self, max_age_days: int, *, tenant_id: str = "_default"
    ) -> Dict[str, Any]:
        """Delete audit events older than max_age_days.

        Called periodically to clean up old events. Implement based on
        your backend's storage.

        **Arguments:**
            max_age_days: How many days of events to retain
            tenant_id: Only delete events for this tenant

        **Returns:**
            Dict with "deleted" (count) and any other metadata
        """
        # In this example, we don't have timestamps, so we can't do real retention
        # In production, you would delete from your database
        return {
            "deleted": 0,
            "message": "Retention not implemented in example backend",
        }

    # ── Optional: export metrics ──────────────────────────────────────

    def get_metrics(self) -> Dict[str, Any]:
        """Export metrics for the admin dashboard (optional)."""
        return {
            "events_stored": len(self.events),
            "is_connected": int(self.is_connected),
        }
