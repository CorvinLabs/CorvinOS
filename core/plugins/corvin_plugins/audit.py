"""Plugin lifecycle audit event emitters (Layer 4).

Emits two critical Layer 4 plugin system events into the unified hash chain:
- plugin.initialization_failed: plugin boot error (unrecoverable)
- plugin.execution_timeout: plugin execution timeout

Both events carry metadata only — plugin identity, boot_layer, error class names.
NO error stack traces, NO plugin configuration, NO internal state.

Integration: wire these emitters into plugin_manager.py load_plugin() + execution wrapper.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


# Allow-list (mirrored in security_events.py::_EVENT_ALLOWLIST)
_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "plugin.initialization_failed": frozenset({
        "plugin_id", "boot_layer", "error_class", "tenant_id",
    }),
    "plugin.execution_timeout": frozenset({
        "plugin_id", "boot_layer", "timeout_ms", "tenant_id",
    }),
}


class AuditFieldNotAllowed(ValueError):
    """Raised when an event details dict carries a non-allow-listed key."""


def _check_allow_list(event: str, details: dict[str, Any]) -> None:
    """Validate event details against the allow-list (fail-closed)."""
    allowed = _ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise AuditFieldNotAllowed(
            f"unknown plugin event type: {event!r}; "
            f"register it in core/plugins/corvin_plugins/audit.py::_ALLOWED_FIELDS first",
        )
    extras = set(details.keys()) - allowed
    if extras:
        raise AuditFieldNotAllowed(
            f"{event}: forbidden detail keys {sorted(extras)}; "
            f"allowed: {sorted(allowed)}",
        )


def emit(
    event: str,
    *,
    path: Path,
    tenant_id: str | None = None,
    severity: str | None = None,
    write_event_fn: Callable[..., Any] | None = None,
    **details: Any,
) -> None:
    """Emit one plugin.* event into the unified hash chain.

    Args:
        event: event type (plugin.*)
        path: audit chain file path
        tenant_id: tenant scope
        severity: override severity (defaults from EVENT_SEVERITY)
        write_event_fn: injected write function (testing); production uses forge.security_events
        **details: event-specific metadata (validated against allow-list)
    """
    # Compose details dict
    payload: dict[str, Any] = dict(details)
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id

    _check_allow_list(event, payload)

    if write_event_fn is None:
        from forge.security_events import write_event as _we  # type: ignore[import]
        write_event_fn = _we

    try:
        write_event_fn(path, event, details=payload, severity=severity)
    except Exception as e:  # noqa: BLE001
        # CRITICAL FIX (ARCH-001): Audit emit must not silently catch exceptions
        # Plugin lifecycle events MUST reach the audit trail (ADR-0232 audit-first)
        # Fail-closed: raise exception so caller knows audit write failed
        log.exception("plugin audit emit FAILED for %s: %s", event, type(e).__name__)

        # Re-raise to preserve audit-first semantics: if audit fails, operation fails
        raise RuntimeError(
            f"Plugin audit event {event!r} failed to write to chain (audit-first violation): {type(e).__name__}"
        ) from e


def emit_initialization_failed(
    path: Path,
    plugin_id: str,
    boot_layer: str,
    error_class: str,
    tenant_id: str | None = None,
) -> None:
    """Emit plugin initialization failure event (Layer 4 boot error).

    Fired when a plugin fails to load/initialize during boot. This is a
    configuration or environment issue, not a runtime error.

    Args:
        path: audit chain file path
        plugin_id: unique plugin identifier
        boot_layer: boot layer where plugin failed (compliance/core/bundled/installed)
        error_class: exception class name (e.g., "ImportError", "ValueError")
        tenant_id: tenant scope
    """
    emit(
        "plugin.initialization_failed",
        path=path,
        tenant_id=tenant_id,
        plugin_id=plugin_id,
        boot_layer=boot_layer,
        error_class=error_class,
    )


def emit_execution_timeout(
    path: Path,
    plugin_id: str,
    boot_layer: str,
    timeout_ms: int,
    tenant_id: str | None = None,
) -> None:
    """Emit plugin execution timeout event (Layer 4 execution guard).

    Fired when a plugin's execute() method exceeds its configured timeout.
    The timeout is enforced by the execution wrapper.

    Args:
        path: audit chain file path
        plugin_id: unique plugin identifier
        boot_layer: boot layer of the plugin (compliance/core/bundled/installed)
        timeout_ms: configured timeout threshold in milliseconds
        tenant_id: tenant scope
    """
    emit(
        "plugin.execution_timeout",
        path=path,
        tenant_id=tenant_id,
        plugin_id=plugin_id,
        boot_layer=boot_layer,
        timeout_ms=int(timeout_ms),
    )


__all__ = [
    "AuditFieldNotAllowed",
    "emit",
    "emit_initialization_failed",
    "emit_execution_timeout",
]
