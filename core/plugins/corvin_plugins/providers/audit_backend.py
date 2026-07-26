"""AuditBackend registry — a SECONDARY sink, never the audit trail (ADR-0233).

Core writes every audit event to its own hash-chained ``audit.jsonl`` first and
unconditionally (GDPR Art. 30/32; ADR-0232 § mandatory core).  Only afterwards is
:func:`fanout` called so an installed backend can forward a *copy* to an external
system.  The ordering is the safety property: by the time a backend runs, the
compliance-relevant write has already committed, so no plugin — buggy, slow or
hostile — can suppress, rewrite, reorder or delay it.

Usage (plugin on_load):
    ctx.audit_registry.set_active(self)

Usage (core audit writer, AFTER its own write has committed):
    from corvin_plugins.providers import audit_backend
    audit_backend.fanout("plugin_enabled", body, severity="INFO", tenant_id=tid)

Unlike the ADR-0033 providers this registry has **no default implementation**.
``get_active()`` returns ``None`` when no plugin is installed; a default would
either duplicate every event into the log for no reason or invite the mistake of
treating the backend as the trail.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from corvin_plugins import circuit_breaker as _breakers

if TYPE_CHECKING:
    from corvin_plugins.protocol import AuditBackend as _ABProto

_log = logging.getLogger("corvin.audit.fanout")

#: How many consecutive fan-out failures are logged per backend before the module
#: goes quiet about them.  A permanently broken sink must not turn every audited
#: action into a log line (that would be its own availability problem), but the
#: first failures have to be visible.
_QUIET_AFTER = 5


class AuditBackendRegistry:
    """Holds the active AuditBackend for this process.  Thread-safe.

    Callers MUST NOT cache ``get_active()`` across calls — a hot-reload or a
    ``disable`` may swap or clear it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: _ABProto | None = None
        self._failures = 0

    def set_active(self, provider: _ABProto) -> None:
        with self._lock:
            self._active = provider
            self._failures = 0

    def clear(self) -> None:
        """Detach the backend (plugin disable / unload).  Core is unaffected."""
        with self._lock:
            self._active = None
            self._failures = 0

    def get_active(self) -> _ABProto | None:
        with self._lock:
            return self._active

    def fanout(
        self,
        event_type: str,
        details: dict,
        *,
        severity: str = "INFO",
        tenant_id: str = "_default",
    ) -> bool:
        """Forward a copy of an already-committed event.  NEVER raises.

        Returns True when a backend accepted the event, False when there was no
        backend or it failed.  The return value is diagnostic only — a caller
        MUST NOT branch its own audit behaviour on it, because the authoritative
        write has already happened.
        """
        with self._lock:
            backend = self._active
        if backend is None:
            return False

        breaker = _breakers.get_breaker(
            getattr(backend, "plugin_id", None) or f"anonymous:{type(backend).__name__}"
        )
        try:
            breaker.guard()
        except _breakers.CircuitOpen:
            # A dead sink stops being called until its cooldown elapses. The
            # authoritative record is already on disk, so this costs a monitoring
            # copy, never a compliance record.
            with self._lock:
                self._failures += 1
            return False

        try:
            # A shallow copy keeps a backend from mutating the dict the core
            # writer still holds a reference to.
            backend.fanout(
                event_type, dict(details), severity=severity, tenant_id=tenant_id
            )
        except Exception as exc:  # noqa: BLE001 — a sink must never break the caller
            breaker.record_failure(exc)
            with self._lock:
                self._failures += 1
                count = self._failures
            if count <= _QUIET_AFTER:
                # Exception CLASS ONLY: str(exc) could carry a connection string,
                # a path or a record fragment.  No PII in log lines.
                _log.error(
                    "audit backend fan-out failed (%s), event_type=%s, failure #%d",
                    type(exc).__name__,
                    event_type,
                    count,
                )
            return False

        breaker.record_success()
        with self._lock:
            self._failures = 0
        return True

    def failure_count(self) -> int:
        """Consecutive fan-out failures — surfaced via health_check_all()."""
        with self._lock:
            return self._failures


_registry: AuditBackendRegistry = AuditBackendRegistry()


def get_active() -> _ABProto | None:
    return _registry.get_active()


def set_active(provider: _ABProto) -> None:
    _registry.set_active(provider)


def clear() -> None:
    _registry.clear()


def fanout(
    event_type: str,
    details: dict,
    *,
    severity: str = "INFO",
    tenant_id: str = "_default",
) -> bool:
    """Module-level shorthand for :meth:`AuditBackendRegistry.fanout`."""
    return _registry.fanout(
        event_type, details, severity=severity, tenant_id=tenant_id
    )


def failure_count() -> int:
    return _registry.failure_count()
