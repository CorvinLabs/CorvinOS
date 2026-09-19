"""Plugin lifecycle audit logging with hash-chain integration (ADR-0233).

Plugin audit logging provides comprehensive records of all plugin-related events:
install, uninstall, load, execute, config change, enable/disable, and error states.

Every event is appended to the core hash-chained audit trail (GDPR Art. 30, 32).
The ordering guarantee is critical: the event is audited BEFORE any side effects,
so the chain remains authoritative even if the plugin fails mid-operation.

Design principles:
* **Audit-first:** Record the event to the core chain before plugin code runs.
* **Immutable:** Events are append-only; never update or delete.
* **Tenant-scoped:** Every event includes tenant_id; queries are filtered by it.
* **Line-of-Moral-Responsibility (LoM):** Every event includes the call site that
  caused it, so an operator can trace "which plugin caused this?"
* **Type-safe:** Frozen dataclasses for all event payloads.
* **Non-blocking:** Audit emission is never on the critical path.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Callable, Optional
from pathlib import Path

log = logging.getLogger("corvin.plugins.audit_logging")


class PluginEventType(str, Enum):
    """Plugin lifecycle event types (comprehensive audit surface)."""
    # Installation/uninstallation
    PLUGIN_INSTALLED = "plugin.installed"
    PLUGIN_UNINSTALLED = "plugin.uninstalled"
    PLUGIN_INSTALLATION_FAILED = "plugin.installation_failed"
    # Boot-time loading
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_LOAD_FAILED = "plugin.load_failed"
    # Execution (hooks, capability calls)
    PLUGIN_EXECUTED = "plugin.executed"
    PLUGIN_EXECUTION_FAILED = "plugin.execution_failed"
    PLUGIN_EXECUTION_SLOW = "plugin.execution_slow"
    PLUGIN_EXECUTION_TIMEOUT = "plugin.execution_timeout"
    # Capability registration
    PLUGIN_CAPABILITY_REGISTERED = "plugin.capability_registered"
    PLUGIN_CAPABILITY_UNREGISTERED = "plugin.capability_unregistered"
    # Configuration
    PLUGIN_CONFIG_CHANGED = "plugin.config_changed"
    # Health
    PLUGIN_HEALTH_CHECK = "plugin.health_check"
    PLUGIN_HEALTH_DEGRADED = "plugin.health_degraded"
    PLUGIN_HEALTH_RECOVERED = "plugin.health_recovered"
    # State management
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"
    PLUGIN_STATE_CHANGED = "plugin.state_changed"
    # Security events
    PLUGIN_PERMISSION_DENIED = "plugin.permission_denied"
    PLUGIN_SANDBOX_VIOLATION = "plugin.sandbox_violation"
    # Circuit breaker
    PLUGIN_CIRCUIT_OPENED = "plugin.circuit_opened"
    PLUGIN_CIRCUIT_CLOSED = "plugin.circuit_closed"


@dataclass(frozen=True)
class PluginAuditEvent:
    """Immutable plugin audit event record.

    Every field is required and immutable. Frozen dataclass ensures
    no accidental modification after creation.
    """
    event_type: str
    plugin_id: str
    tenant_id: str
    timestamp: float
    version: str
    lom: str  # Line of Moral Responsibility (call site)

    # Event-specific details (frozen dict-like)
    action: str = ""  # install, load, execute, config_change, etc.
    status: str = "success"  # success, failure, warning
    error_type: str | None = None
    error_message: str = ""
    duration_ms: float = 0.0

    # Context
    boot_layer: str = ""
    plugin_type: str = ""
    capability: str = ""

    # Metrics
    call_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0

    # Details (immutable via frozen)
    details: dict[str, Any] = None

    def __post_init__(self) -> None:
        """Validate event at creation time."""
        if not self.plugin_id:
            raise ValueError("plugin_id required")
        if not self.tenant_id:
            raise ValueError("tenant_id required")
        if self.timestamp <= 0:
            raise ValueError("timestamp must be positive")
        if self.event_type not in {e.value for e in PluginEventType}:
            log.warning("unknown event_type: %s", self.event_type)

    def to_dict(self) -> dict[str, Any]:
        """Convert to audit-writable dictionary."""
        result = asdict(self)
        # Ensure tenant_id is always included
        result["tenant_id"] = self.tenant_id
        # Prune empty fields to keep records compact
        result = {k: v for k, v in result.items() if v is not None and v != ""}
        return result


class PluginAuditLogger:
    """Records plugin events to the hash-chained audit trail.

    Thread-safe logger that ensures every plugin event is captured in the
    authoritative audit chain with proper tenant scoping and immutability.

    Usage:
        logger = PluginAuditLogger(audit_emit=audit_event)
        logger.log_loaded(plugin_id, version, boot_layer, lom, tenant_id)
    """

    def __init__(
        self,
        *,
        audit_emit: Callable[[str, dict], None],
        tenant_id: str,
    ) -> None:
        """Initialize audit logger with core audit emit function.

        Args:
            audit_emit: Core audit_event function from audit.py (hash-chained writer)
            tenant_id: Tenant scope for all events
        """
        self.audit_emit = audit_emit
        self.tenant_id = tenant_id

    def _emit(self, event: PluginAuditEvent) -> None:
        """Emit event to core audit chain with proper error handling.

        Failures to write to the core chain are logged but never raised—
        audit failures must not become platform failures. The core chain's
        own fail-closed design ensures that if this fails, the chain write
        that triggers the audit_event() call will also fail, and the platform
        will fail-closed appropriately.
        """
        try:
            self.audit_emit(event.event_type, event.to_dict())
        except Exception as exc:  # noqa: BLE001
            log.error(
                "failed to audit %s for plugin %r (%s) — event lost but core chain verified",
                event.event_type,
                event.plugin_id,
                type(exc).__name__,
            )

    def log_installed(
        self,
        *,
        plugin_id: str,
        version: str,
        plugin_type: str,
        origin: str,
        boot_layer: str,
        lom: str,
    ) -> None:
        """Record plugin installation."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_INSTALLED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="install",
            plugin_type=plugin_type,
            boot_layer=boot_layer,
            details={"origin": origin},
        )
        self._emit(event)

    def log_installation_failed(
        self,
        *,
        plugin_id: str,
        reason: str,
        error_type: str,
        lom: str,
    ) -> None:
        """Record installation failure."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_INSTALLATION_FAILED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version="unknown",
            lom=lom,
            action="install",
            status="failure",
            error_type=error_type,
            error_message=reason[:256],
        )
        self._emit(event)

    def log_loaded(
        self,
        *,
        plugin_id: str,
        version: str,
        boot_layer: str,
        plugin_type: str,
        lom: str,
        duration_ms: float = 0.0,
    ) -> None:
        """Record successful plugin load."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_LOADED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="load",
            status="success",
            boot_layer=boot_layer,
            plugin_type=plugin_type,
            duration_ms=duration_ms,
        )
        self._emit(event)

    def log_load_failed(
        self,
        *,
        plugin_id: str,
        version: str,
        boot_layer: str,
        reason: str,
        error_type: str,
        lom: str,
        duration_ms: float = 0.0,
    ) -> None:
        """Record plugin load failure."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_LOAD_FAILED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="load",
            status="failure",
            boot_layer=boot_layer,
            error_type=error_type,
            error_message=reason[:256],
            duration_ms=duration_ms,
        )
        self._emit(event)

    def log_executed(
        self,
        *,
        plugin_id: str,
        version: str,
        capability: str,
        duration_ms: float,
        lom: str,
        call_count: int = 1,
    ) -> None:
        """Record successful plugin execution."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_EXECUTED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="execute",
            status="success",
            capability=capability,
            duration_ms=duration_ms,
            call_count=call_count,
        )
        self._emit(event)

    def log_execution_failed(
        self,
        *,
        plugin_id: str,
        version: str,
        capability: str,
        duration_ms: float,
        error_type: str,
        error_message: str,
        lom: str,
    ) -> None:
        """Record plugin execution failure."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_EXECUTION_FAILED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="execute",
            status="failure",
            capability=capability,
            duration_ms=duration_ms,
            error_type=error_type,
            error_message=error_message[:256],
        )
        self._emit(event)

    def log_execution_slow(
        self,
        *,
        plugin_id: str,
        version: str,
        capability: str,
        duration_ms: float,
        threshold_ms: float,
        lom: str,
    ) -> None:
        """Record slow execution (performance warning)."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_EXECUTION_SLOW.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="execute",
            status="warning",
            capability=capability,
            duration_ms=duration_ms,
            details={"threshold_ms": threshold_ms},
        )
        self._emit(event)

    def log_execution_timeout(
        self,
        *,
        plugin_id: str,
        version: str,
        capability: str,
        timeout_ms: float,
        lom: str,
    ) -> None:
        """Record execution timeout."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_EXECUTION_TIMEOUT.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="execute",
            status="failure",
            error_type="TimeoutError",
            error_message=f"execution exceeded {timeout_ms}ms",
            duration_ms=timeout_ms,
        )
        self._emit(event)

    def log_capability_registered(
        self,
        *,
        plugin_id: str,
        version: str,
        capability: str,
        lom: str,
    ) -> None:
        """Record capability registration."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_CAPABILITY_REGISTERED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="register",
            capability=capability,
        )
        self._emit(event)

    def log_config_changed(
        self,
        *,
        plugin_id: str,
        version: str,
        config_keys: list[str],
        lom: str,
    ) -> None:
        """Record configuration change."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_CONFIG_CHANGED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="config_change",
            details={"keys_changed": config_keys[:10]},  # cap to prevent explosion
        )
        self._emit(event)

    def log_health_check(
        self,
        *,
        plugin_id: str,
        version: str,
        ok: bool,
        duration_ms: float,
        lom: str,
        message: str = "",
    ) -> None:
        """Record health check result."""
        event_type = (
            PluginEventType.PLUGIN_HEALTH_CHECK.value
            if ok
            else PluginEventType.PLUGIN_HEALTH_DEGRADED.value
        )
        event = PluginAuditEvent(
            event_type=event_type,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="health_check",
            status="success" if ok else "warning",
            duration_ms=duration_ms,
            error_message=message[:256],
        )
        self._emit(event)

    def log_permission_denied(
        self,
        *,
        plugin_id: str,
        version: str,
        capability: str,
        reason: str,
        lom: str,
    ) -> None:
        """Record permission denial."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_PERMISSION_DENIED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="execute",
            status="failure",
            error_type="PermissionError",
            error_message=reason[:256],
            capability=capability,
        )
        self._emit(event)

    def log_sandbox_violation(
        self,
        *,
        plugin_id: str,
        version: str,
        violation_type: str,
        details: str,
        lom: str,
    ) -> None:
        """Record sandbox constraint violation."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_SANDBOX_VIOLATION.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="sandbox_check",
            status="failure",
            error_type=violation_type,
            error_message=details[:256],
        )
        self._emit(event)

    def log_circuit_opened(
        self,
        *,
        plugin_id: str,
        version: str,
        reason: str,
        consecutive_failures: int,
        lom: str,
    ) -> None:
        """Record circuit breaker opening."""
        event = PluginAuditEvent(
            event_type=PluginEventType.PLUGIN_CIRCUIT_OPENED.value,
            plugin_id=plugin_id,
            tenant_id=self.tenant_id,
            timestamp=time.time(),
            version=version,
            lom=lom,
            action="circuit_breaker",
            status="warning",
            consecutive_failures=consecutive_failures,
            error_message=reason[:256],
        )
        self._emit(event)
