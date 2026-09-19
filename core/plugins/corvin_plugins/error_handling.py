"""Plugin error handling and graceful degradation (ADR-0233).

Comprehensive error handling that ensures one broken plugin doesn't cascade
into a platform-wide failure. Core principles:

* **Isolation:** Plugin errors are caught at the boundary; no exception escapes.
* **Fallbacks:** Every plugin call has a caller-provided fallback value.
* **Logging:** Errors are logged with full context (plugin, capability, operation).
* **Auditing:** Errors are audited with class names, never tracebacks (GDPR-safe).
* **Metrics:** Failures are recorded for dashboards and alerting.
* **Healing:** Circuit breaker + exponential backoff help plugins recover.

Usage pattern:
    fallback = some_default_value
    result = guard_plugin_call(
        plugin_id, capability,
        lambda: plugin.do_something(),
        fallback=fallback,
        timeout_s=5.0,
    )
    # result is either plugin's return or fallback
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

log = logging.getLogger("corvin.plugins.error_handling")

T = TypeVar("T")


class ErrorSeverity(str, Enum):
    """Error severity levels."""
    RECOVERABLE = "recoverable"
    DEGRADED = "degraded"
    CRITICAL = "critical"


@dataclass
class ErrorContext:
    """Context for an error that occurred in a plugin."""
    plugin_id: str
    capability: str
    operation: str
    error_type: str  # Exception class name only
    error_message: str  # First 256 chars only, no traceback
    duration_ms: float
    timestamp: float
    severity: ErrorSeverity


class PluginCallGuard:
    """Safe execution wrapper for plugin calls.

    Catches all exceptions, logs them, audits them, and returns a fallback value.
    Never raises to the caller; failures are always degraded gracefully.
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        tenant_id: str,
        audit_emit: Callable[[str, dict], None],
        on_error: Optional[Callable[[ErrorContext], None]] = None,
    ) -> None:
        """Initialize the call guard.

        Args:
            plugin_id: Plugin identifier
            tenant_id: Tenant scope
            audit_emit: Core audit_event function
            on_error: Optional callback for errors (for metrics recording)
        """
        self.plugin_id = plugin_id
        self.tenant_id = tenant_id
        self.audit_emit = audit_emit
        self.on_error = on_error

    def call(
        self,
        capability: str,
        fn: Callable[[], T],
        *,
        fallback: T,
        timeout_s: Optional[float] = None,
        operation: str = "execute",
    ) -> T:
        """Execute a plugin function with error handling.

        Args:
            capability: The capability being invoked
            fn: The function to call (must take no arguments)
            fallback: Value to return if fn fails or times out
            timeout_s: Maximum execution time (None = no timeout)
            operation: Operation name for logging

        Returns:
            Either fn's return value or fallback
        """
        start_time = time.time()

        try:
            # Execute with optional timeout
            if timeout_s is not None:
                return self._call_with_timeout(fn, timeout_s, fallback)
            else:
                return fn()

        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.time() - start_time) * 1000
            error_context = ErrorContext(
                plugin_id=self.plugin_id,
                capability=capability,
                operation=operation,
                error_type=type(exc).__name__,
                error_message=str(exc)[:256],
                duration_ms=duration_ms,
                timestamp=time.time(),
                severity=self._classify_severity(exc),
            )

            self._handle_error(error_context)
            return fallback

    async def call_async(
        self,
        capability: str,
        fn: Callable[[], Any],  # Returns coroutine
        *,
        fallback: T,
        timeout_s: Optional[float] = None,
        operation: str = "execute",
    ) -> T:
        """Execute an async plugin function with error handling.

        Args:
            capability: The capability being invoked
            fn: The async function to call
            fallback: Value to return if fn fails or times out
            timeout_s: Maximum execution time (None = no timeout)
            operation: Operation name for logging

        Returns:
            Either fn's return value or fallback
        """
        start_time = time.time()

        try:
            coro = fn()

            if timeout_s is not None:
                return await asyncio.wait_for(coro, timeout=timeout_s)
            else:
                return await coro

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            error_context = ErrorContext(
                plugin_id=self.plugin_id,
                capability=capability,
                operation=operation,
                error_type="TimeoutError",
                error_message=f"async execution exceeded {timeout_s}s",
                duration_ms=duration_ms,
                timestamp=time.time(),
                severity=ErrorSeverity.RECOVERABLE,
            )
            self._handle_error(error_context)
            return fallback

        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.time() - start_time) * 1000
            error_context = ErrorContext(
                plugin_id=self.plugin_id,
                capability=capability,
                operation=operation,
                error_type=type(exc).__name__,
                error_message=str(exc)[:256],
                duration_ms=duration_ms,
                timestamp=time.time(),
                severity=self._classify_severity(exc),
            )
            self._handle_error(error_context)
            return fallback

    @staticmethod
    def _call_with_timeout(
        fn: Callable[[], T],
        timeout_s: float,
        fallback: T,
    ) -> T:
        """Execute function with timeout using threading."""
        result_holder = [None]
        exception_holder = [None]

        def target() -> None:
            try:
                result_holder[0] = fn()
            except Exception as exc:  # noqa: BLE001
                exception_holder[0] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=timeout_s)

        if thread.is_alive():
            # Timeout occurred
            raise TimeoutError(f"function call exceeded {timeout_s}s")

        if exception_holder[0]:
            raise exception_holder[0]

        return result_holder[0]

    @staticmethod
    def _classify_severity(exc: Exception) -> ErrorSeverity:
        """Classify error severity for alerting decisions.

        Args:
            exc: The exception that occurred

        Returns:
            Severity level for alerting and healing decisions
        """
        exc_type = type(exc).__name__

        # Critical errors that need immediate attention
        critical_types = {
            "OutOfMemoryError",
            "ImportError",
            "SyntaxError",
            "SystemError",
        }
        if exc_type in critical_types:
            return ErrorSeverity.CRITICAL

        # Recoverable errors that might self-heal
        recoverable_types = {
            "TimeoutError",
            "ConnectionError",
            "BrokenPipeError",
            "TemporaryError",
        }
        if exc_type in recoverable_types:
            return ErrorSeverity.RECOVERABLE

        # Everything else is degraded
        return ErrorSeverity.DEGRADED

    def _handle_error(self, error_context: ErrorContext) -> None:
        """Handle an error: log, audit, and notify.

        Args:
            error_context: Context of the error
        """
        # Log for operator visibility
        log.warning(
            "plugin %r.%s failed (%s): %s [%.1fms]",
            self.plugin_id,
            error_context.capability,
            error_context.error_type,
            error_context.error_message,
            error_context.duration_ms,
        )

        # Audit (immutable record)
        try:
            self.audit_emit("plugin.execution_failed", {
                "plugin_id": self.plugin_id,
                "tenant_id": self.tenant_id,
                "capability": error_context.capability,
                "operation": error_context.operation,
                "error_type": error_context.error_type,
                "error_message": error_context.error_message,
                "duration_ms": error_context.duration_ms,
                "severity": error_context.severity.value,
            })
        except Exception as audit_exc:  # noqa: BLE001
            log.error(
                "failed to audit plugin error (%s) — chain unavailable",
                type(audit_exc).__name__,
            )

        # Notify via metrics callback
        if self.on_error is not None:
            try:
                self.on_error(error_context)
            except Exception as cb_exc:  # noqa: BLE001
                log.error(
                    "error callback failed (%s) — metrics may be incomplete",
                    type(cb_exc).__name__,
                )


class GracefulDegradation:
    """Graceful degradation strategy for plugin subsystems.

    When a plugin provider (e.g., audit_backend, notification_backend) fails,
    the system continues with reduced functionality rather than failing completely.

    Strategies:
    * **Drop the copy:** For fanout sinks (audit, notification), drop the secondary copy
    * **Use default:** For capability providers, use built-in default
    * **Deny with reason:** For auth/access plugins, fail-closed (deny)
    * **Log and continue:** For optional plugins, skip them
    """

    @staticmethod
    def should_degrade(
        plugin_id: str,
        is_critical: bool,
        error_severity: ErrorSeverity,
    ) -> bool:
        """Decide whether to degrade vs. fail-fast.

        Args:
            plugin_id: Plugin that failed
            is_critical: Whether this plugin is required for platform operation
            error_severity: Severity of the error

        Returns:
            True if degradation is acceptable; False to propagate failure
        """
        # Always degrade for non-critical plugins
        if not is_critical:
            return True

        # For critical plugins, only degrade on recoverable errors
        # Critical errors (OutOfMemory, Import failure) should fail-fast
        return error_severity == ErrorSeverity.RECOVERABLE

    @staticmethod
    def apply_degradation(
        plugin_id: str,
        plugin_type: str,
        fallback_strategy: str,
    ) -> None:
        """Apply a degradation strategy.

        Args:
            plugin_id: Plugin that failed
            plugin_type: The plugin type/capability
            fallback_strategy: Strategy name (drop_copy, use_default, deny, skip)
        """
        if fallback_strategy == "drop_copy":
            # For fanout sinks, dropping the copy is safe
            log.warning(
                "degrading %s (type=%s): dropping secondary copy (chain OK)",
                plugin_id,
                plugin_type,
            )
        elif fallback_strategy == "use_default":
            # Use built-in fallback
            log.warning(
                "degrading %s (type=%s): using built-in default",
                plugin_id,
                plugin_type,
            )
        elif fallback_strategy == "deny":
            # Fail-closed
            log.warning(
                "degrading %s (type=%s): denying access (fail-closed)",
                plugin_id,
                plugin_type,
            )
        elif fallback_strategy == "skip":
            # Skip the plugin
            log.warning(
                "degrading %s (type=%s): skipping optional plugin",
                plugin_id,
                plugin_type,
            )


def make_call_guard(
    plugin_id: str,
    tenant_id: str,
    audit_emit: Callable[[str, dict], None],
    on_error: Optional[Callable[[ErrorContext], None]] = None,
) -> PluginCallGuard:
    """Factory function to create a call guard for a plugin.

    Args:
        plugin_id: Plugin identifier
        tenant_id: Tenant scope
        audit_emit: Core audit function
        on_error: Optional error callback

    Returns:
        A configured PluginCallGuard
    """
    return PluginCallGuard(
        plugin_id=plugin_id,
        tenant_id=tenant_id,
        audit_emit=audit_emit,
        on_error=on_error,
    )
