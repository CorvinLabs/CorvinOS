"""
Integration of plugin telemetry into ADR-0345 recursive plugin system.

Hooks:
- register_node: emit HEALTH_CHECK
- delegate_work: emit WORK_DELEGATED + BUDGET_ALLOCATED
- handle_work: emit WORK_RECEIVED / WORK_HANDLED_LOCALLY / WORK_FAILED
- audit_hash_mismatch: emit AUDIT_HASH_MISMATCH
- quarantine: emit CHILD_QUARANTINED

This is the "glue layer" between core/plugins and core/observability.
"""

from typing import Optional, Dict, Any
from core.observability.plugin_telemetry import (
    PluginTelemetryEvent,
    PluginTelemetryEventType,
    PluginTelemetryCollector,
    get_telemetry_collector,
)


class PluginTelemetryHooks:
    """Hooks to emit telemetry from plugin system."""

    def __init__(self, collector: Optional[PluginTelemetryCollector] = None):
        """Initialize hooks with collector."""
        self.collector = collector or get_telemetry_collector()

    def on_plugin_registered(
        self,
        plugin_id: str,
        tenant_id: str,
        parent_id: Optional[str] = None,
        boot_layer: str = "bundled",
        capabilities: Optional[list] = None,
    ) -> None:
        """Emit event when plugin registers."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.HEALTH_CHECK,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            parent_id=parent_id,
            data={
                "action": "registered",
                "boot_layer": boot_layer,
                "capabilities": capabilities or [],
            },
        )
        self.collector.emit_event(event)

    def on_work_received(
        self,
        plugin_id: str,
        tenant_id: str,
        work_id: str,
        required_capability: str,
        priority_tier: str = "standard",
        budget_cost: int = 10,
    ) -> None:
        """Emit event when plugin receives work."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.WORK_RECEIVED,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            work_id=work_id,
            data={
                "required_capability": required_capability,
                "priority_tier": priority_tier,
                "budget_cost": budget_cost,
            },
        )
        self.collector.emit_event(event)

    def on_work_handled_locally(
        self,
        plugin_id: str,
        tenant_id: str,
        work_id: str,
        latency_ms: float,
        budget_cost: int = 10,
    ) -> None:
        """Emit event when plugin handles work locally."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.WORK_HANDLED_LOCALLY,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            work_id=work_id,
            data={
                "latency_ms": latency_ms,
                "budget_cost": budget_cost,
            },
        )
        self.collector.emit_event(event)

    def on_work_delegated(
        self,
        plugin_id: str,
        tenant_id: str,
        work_id: str,
        target_child: str,
        priority_tier: str = "standard",
        budget_cost: int = 10,
        latency_ms: float = 0.0,
    ) -> None:
        """Emit event when plugin delegates work to child."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.WORK_DELEGATED,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            work_id=work_id,
            parent_id=None,  # Will be filled by delegate
            data={
                "target_child": target_child,
                "priority_tier": priority_tier,
                "budget_cost": budget_cost,
                "latency_ms": latency_ms,
            },
        )
        self.collector.emit_event(event)

    def on_work_failed(
        self,
        plugin_id: str,
        tenant_id: str,
        work_id: str,
        error: str,
        latency_ms: float = 0.0,
    ) -> None:
        """Emit event when plugin fails to handle work."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.WORK_FAILED,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            work_id=work_id,
            data={
                "error": error,
                "latency_ms": latency_ms,
            },
        )
        self.collector.emit_event(event)

    def on_budget_exhausted(
        self,
        plugin_id: str,
        tenant_id: str,
        tier: str = "standard",
        used: int = 0,
        limit: int = 100,
    ) -> None:
        """Emit event when budget is exhausted."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.BUDGET_EXHAUSTED,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            data={
                "tier": tier,
                "used": used,
                "limit": limit,
                "exhausted_percent": (used / limit * 100) if limit > 0 else 0,
            },
        )
        self.collector.emit_event(event)

    def on_audit_hash_mismatch(
        self,
        plugin_id: str,
        tenant_id: str,
        parent_id: Optional[str] = None,
        expected_hash: str = "",
        actual_hash: str = "",
    ) -> None:
        """Emit event when audit hash mismatches."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.AUDIT_HASH_MISMATCH,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            parent_id=parent_id,
            data={
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
            },
        )
        self.collector.emit_event(event)

    def on_child_quarantined(
        self,
        plugin_id: str,
        tenant_id: str,
        child_id: str,
        reason: str = "repeated_audit_failures",
    ) -> None:
        """Emit event when child plugin is quarantined."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.CHILD_QUARANTINED,
            plugin_id=child_id,
            tenant_id=tenant_id,
            parent_id=plugin_id,
            data={
                "reason": reason,
            },
        )
        self.collector.emit_event(event)

    def on_fallback_triggered(
        self,
        plugin_id: str,
        tenant_id: str,
        work_id: str,
        failed_child: str,
        fallback_child: str,
        reason: str = "child_failed",
    ) -> None:
        """Emit event when fallback chain is triggered."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.FALLBACK_TRIGGERED,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            work_id=work_id,
            data={
                "failed_child": failed_child,
                "fallback_child": fallback_child,
                "reason": reason,
            },
        )
        self.collector.emit_event(event)


# Global hooks instance (can be injected)
_telemetry_hooks = PluginTelemetryHooks()


def get_telemetry_hooks() -> PluginTelemetryHooks:
    """Get global telemetry hooks."""
    return _telemetry_hooks


def set_telemetry_hooks(hooks: PluginTelemetryHooks) -> None:
    """Set global telemetry hooks (for testing/DI)."""
    global _telemetry_hooks
    _telemetry_hooks = hooks
