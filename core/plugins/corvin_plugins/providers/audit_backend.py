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
        self._owner_plugin_id: str | None = None
        self._active: _ABProto | None = None
        self._failures = 0
        self._queue: "queue.Queue[tuple[str, dict, str, str]]" = queue.Queue(
            maxsize=MAX_QUEUED_EVENTS
        )
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._dropped = 0

    def set_active(self, provider: _ABProto) -> None:
        """Install ``provider`` as the active one for this process.

        Records WHICH PLUGIN did it (``loading.current()``), so the slot can
        later be released by plugin identity rather than by matching the
        object or guessing from ``plugin_type``. A plugin that installs a
        helper object still owns the slot.
        """
        from .. import loading as _loading

        _who = _loading.current()
        with self._lock:
            # Only a plugin that is LOADING may claim ownership. A set_active()
            # from anywhere else (a request handler, a thread a plugin spawned,
            # a timer) used to write None here — which not only left the new
            # occupant unowned, it ERASED the previous legitimate owner, so the
            # slot could never be released by anyone again. Keeping the old
            # owner is the lesser wrong: the slot still belongs to whoever took
            # it during a load, and unloading them releases it.
            if _who is not None:
                self._owner_plugin_id = _who.plugin_id
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
            finally:
                # Pairs with the get() above. drain_now() waits on this counter, so a
                # missed task_done() would hang shutdown for the full timeout.
                self._queue.task_done()

    def drain_now(self, timeout: float = 2.0) -> int:
        """Wait for the queue to drain.  For tests and shutdown.

        Returns the number of copies that were still pending when we started (0 when
        the pipeline was already empty).

        The caller NEVER delivers. An earlier version pulled items and called the
        backend on the calling thread, which made ``timeout`` a lie: it bounded the
        loop *between* items, not a single delivery, so one wedged sink held the
        shutdown open for as long as it felt like — measured 30 s against a 0.5 s
        timeout. That is the outbox-poller failure class (a hanging sendFn stalled
        delivery for 38 minutes with no log line). Delivery stays on the worker
        thread, which is a daemon and dies with the process; this method only ever
        waits, and the deadline is therefore real.
        """
        deadline = time.monotonic() + timeout
        pending = self._queue.unfinished_tasks
        if not pending:
            return 0
        # The worker may have been stopped (or never started, if fanout() was never
        # called on this registry) — without it nothing would ever drain.
        self._ensure_worker()
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _log.warning(
                        "audit fan-out drain timed out with %d copy(ies) pending",
                        self._queue.unfinished_tasks,
                    )
                    break
                self._queue.all_tasks_done.wait(timeout=min(0.05, remaining))
        return pending

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
            self._owner_plugin_id = None
            self._active = None
            self._failures = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()

    def clear_if_active(self, provider: object) -> bool:
        """Detach only if ``provider`` is the one currently installed.

        Instance-checked on purpose. A plugin unloading must not evict a backend
        that a DIFFERENT plugin installed after it — clearing by type alone would
        drop the slot while the other plugin still believes it is the sink, and
        for audit that means a fan-out stream that silently stops.

        The check and the clear are one critical section. Releasing the lock
        between them left a window in which another plugin could take the slot
        via ``set_active`` and then have it cleared out from under it — along
        with its queued copies.
        """
        with self._lock:
            if self._active is not provider:
                return False
            self._active = None
            self._failures = 0
            self._owner_plugin_id = None
        self._drain_queue()
        return True

    def _drain_queue(self) -> None:
        """Discard queued copies for a backend that is no longer installed."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()

    def get_active(self) -> _ABProto | None:
        with self._lock:
            return self._active

    def release_owned_by(self, plugin_id: str) -> bool:
        """Release the slot if ``plugin_id`` is the plugin that took it.

        Identity-based, which is the point: the object in the slot may be a
        helper the plugin created rather than the plugin itself, and the
        plugin's ``plugin_type`` may not even name this registry. Ownership is
        recorded at ``set_active`` time and is the only thing that answers
        "is this slot yours" correctly.
        """
        with self._lock:
            if self._owner_plugin_id is None or self._owner_plugin_id != plugin_id:
                return False
            self._owner_plugin_id = None
            self._active = None
            return True

    def owner_plugin_id(self) -> str | None:
        """The plugin that installed the current provider, if it is known."""
        with self._lock:
            return self._owner_plugin_id

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
                    self._queue.task_done()  # the discarded copy is accounted for
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
            owner = self._owner_plugin_id
        if backend is None:
            return False

        # Key the breaker on the OWNING PLUGIN, not on the object in the slot.
        # `get_breaker()` decides containment from the owner's boot layer, and a
        # plugin that installed a helper object (`set_active(self._sink)`) has no
        # plugin_id on that helper — the key became "anonymous:Sink", which the
        # registry has never heard of, so the compliance exemption silently did
        # not apply and the sink could be contained after all. Two identities for
        # one plugin is the whole defect.
        breaker = _breakers.get_breaker(
            owner
            or getattr(backend, "plugin_id", None)
            or f"anonymous:{type(backend).__name__}"
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


def clear_if_active(provider: object) -> bool:
    return _registry.clear_if_active(provider)


def release_owned_by(plugin_id: str) -> bool:
    return _registry.release_owned_by(plugin_id)
