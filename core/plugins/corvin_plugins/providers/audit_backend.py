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
import queue
import threading
import time
from typing import TYPE_CHECKING

from corvin_plugins import circuit_breaker as _breakers

if TYPE_CHECKING:
    from corvin_plugins.protocol import AuditBackend as _ABProto

_log = logging.getLogger("corvin.audit.fanout")

#: Attribute names that would make this module capable of OWNING the audit trail
#: instead of being a secondary sink. Declared here so the boot tripwire and its
#: test read the SAME list — they drifted apart once already (the test forbade
#: five names, the tripwire checked four, so adding `write_event` would have
#: failed the test while passing the boot gate).
TRAIL_OWNING_ATTRS: tuple[str, ...] = (
    "set_writer",
    "replace_writer",
    "set_audit_path",
    "disable_core",
    "write_event",
)

#: Bound on the hand-off queue. The core audit path must never wait on a plugin, so
#: fan-out is a hand-off, not a call: the caller enqueues and returns. When the sink
#: cannot keep up the OLDEST monitoring copy is dropped — the authoritative record is
#: already on disk, so a dropped copy is a monitoring gap, while a blocked caller is
#: an outage of every audited action in the platform.
#:
#: Measured before this existed: a backend with a 400 ms fanout() added 2.07 s to
#: five audit_event() calls, i.e. it blocked every bridge turn, login and tool use.
#: The template asks backends to queue internally; this does not rely on that.
MAX_QUEUED_EVENTS = 4096
#: A sink slower than this per event is treated as a breaker failure — otherwise a
#: merely slow (never raising) backend is invisible to the breaker.
SLOW_SINK_S = 2.0

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
        self._queue: "queue.Queue[tuple[str, dict, str, str]]" = queue.Queue(
            maxsize=MAX_QUEUED_EVENTS
        )
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._dropped = 0

    def set_active(self, provider: _ABProto) -> None:
        with self._lock:
            self._active = provider
            self._failures = 0
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        """Start the drain thread on first use.  Daemon: never blocks shutdown."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._drain, name="corvin-audit-fanout", daemon=True
            )
            self._worker.start()

    def _drain(self) -> None:
        """Deliver queued copies.  Runs off the caller's thread, forever.

        The loop body cannot raise: if this thread dies, every later copy sits in
        the queue forever and monitoring goes silent WITHOUT any signal. _deliver()
        is fully guarded, and this is the second belt.
        """
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001
                continue
            try:
                self._deliver(*item)
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "audit fan-out worker caught %s — continuing", type(exc).__name__
                )

    def drain_now(self, timeout: float = 2.0) -> int:
        """Deliver everything queued, synchronously.  For tests and shutdown.

        Returns the number of events delivered.
        """
        delivered = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            self._deliver(*item)
            delivered += 1
        return delivered

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def dropped_count(self) -> int:
        return self._dropped

    def clear(self) -> None:
        """Detach the backend (plugin disable / unload).  Core is unaffected.

        Queued copies for the old backend are discarded: delivering them to a
        backend the operator just detached would be worse than losing a monitoring
        copy.
        """
        with self._lock:
            self._active = None
            self._failures = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

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
        """HAND OFF a copy of an already-committed event.  NEVER raises, never waits.

        Returns True when the copy was enqueued, False when there is no backend or
        the queue was full. The return value is diagnostic only — a caller MUST NOT
        branch its own audit behaviour on it, because the authoritative write has
        already happened.
        """
        try:
            with self._lock:
                backend = self._active
            if backend is None:
                return False
            self._ensure_worker()
            # Shallow copy here, on the caller's thread: the core writer still holds
            # a reference to `details` and will keep using it after we return.
            item = (event_type, dict(details), severity, tenant_id)
            try:
                self._queue.put_nowait(item)
                return True
            except queue.Full:
                # Drop the OLDEST, keep the newest, never block.
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(item)
                except (queue.Empty, queue.Full):
                    pass
                self._dropped += 1
                if self._dropped <= _QUIET_AFTER:
                    _log.error(
                        "audit fan-out queue full — dropped a monitoring copy "
                        "(event_type=%s, total dropped=%d)",
                        event_type, self._dropped,
                    )
                return False
        except Exception as exc:  # noqa: BLE001
            # Outermost belt: NOTHING may leave this method. The contract that
            # audit.py relies on is "fanout never raises into the caller", and a
            # leak there gets logged as "audit_event dropped" even though the core
            # record already committed — a false compliance alarm. Reaching this
            # handler means a code path outside _fanout_inner's own guards threw
            # (e.g. a backend whose plugin_id property raises).
            _log.error(
                "audit fan-out leaked %s outside the guarded path — dropped",
                type(exc).__name__,
            )
            return False

    def _deliver(
        self,
        event_type: str,
        details: dict,
        severity: str,
        tenant_id: str,
    ) -> bool:
        """Actually call the backend.  Runs on the drain thread, never on a caller.

        NEVER raises. The guard used to live in fanout(); when fan-out became a
        hand-off the guarded body moved here, and for one commit the exception
        escaped into the worker thread instead — which would kill the drain loop and
        silence monitoring. Anything outside _deliver_inner's own try (a backend
        whose plugin_id property raises, a breaker lookup) is caught here.
        """
        try:
            return self._deliver_inner(event_type, details, severity, tenant_id)
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "audit fan-out failed outside the guarded path (%s) — dropped",
                type(exc).__name__,
            )
            return False

    def _deliver_inner(
        self,
        event_type: str,
        details: dict,
        severity: str,
        tenant_id: str,
    ) -> bool:
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

        started = time.monotonic()
        try:
            backend.fanout(
                event_type, details, severity=severity, tenant_id=tenant_id
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

        elapsed = time.monotonic() - started
        if elapsed > SLOW_SINK_S:
            # A sink that never raises but takes seconds is still broken. Without
            # this the breaker could not see it at all, because the queue absorbs
            # the latency instead of the caller.
            _log.warning(
                "audit sink took %.1fs for one event — counting it as a failure",
                elapsed,
            )
            breaker.record_failure(TimeoutError())
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


def drain_now(timeout: float = 2.0) -> int:
    """Deliver everything queued right now (tests, graceful shutdown)."""
    return _registry.drain_now(timeout)


def queue_depth() -> int:
    return _registry.queue_depth()


def dropped_count() -> int:
    return _registry.dropped_count()


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
