"""Phase 2: EventEmitter — Async non-blocking event emission (ADR-0314)."""

from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
import weakref
from typing import Optional

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore

logger = logging.getLogger(__name__)


class EventEmitter:
    """Non-blocking async event emitter (fire-and-forget queue)."""

    def __init__(self, event_store: EventStore, queue_size: int = 10000):
        """Initialize emitter with event store.

        Args:
            event_store: An ``EventStore`` (anything with ``write_event(event)``).
            queue_size: Bounded queue depth (int > 0). MEDIUM FIX #5: Default 10K max.

        The worker is a DAEMON thread with an ``atexit`` flush: the previous
        ``daemon=False`` + ``while True`` combination kept every process that
        ever constructed an emitter alive forever unless ``stop()`` was called —
        both shipped hosts construct one at boot and never did.
        """
        if not hasattr(event_store, "write_event"):
            raise TypeError(
                f"event_store must provide write_event(event); got {type(event_store).__name__}"
            )
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or queue_size <= 0:
            raise TypeError(f"queue_size must be a positive int, got {queue_size!r}")
        self.store = event_store
        self._queue: queue.Queue[Optional[LearningEvent]] = queue.Queue(maxsize=queue_size)
        self._stopped = False
        self._sentinel_sent = False
        self.lost_at_exit = 0
        self.accepted = 0     # emit() returned True
        self.written = 0      # store.write_event returned
        # One lock makes "is the emitter still accepting?" and "put the event in the queue" a single
        # step, and makes stop() flip the flag atomically with respect to both. Without it an emit
        # that passed the _stopped check could put its event BEHIND stop()'s sentinel: emit() said
        # True, the worker had already exited, the event was never written.
        self._lifecycle_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self.dropped = 0  # queue-full drops (observable, never silent)
        self.write_failures = 0
        self._worker_thread = threading.Thread(
            target=self._worker, name="learning-event-emitter", daemon=True
        )
        self._worker_thread.start()
        # ONE module-level exit hook stops every live emitter against ONE shared deadline - a hook
        # per emitter waited EXIT_FLUSH_S each, one after another (review R2-SC-09).
        _LIVE.add(self)

    def _worker(self) -> None:
        """Background worker: consume queue and write events."""
        while True:
            try:
                event = self._queue.get(timeout=5.0)
                if event is None:  # Sentinel: stop
                    break
                try:
                    self.store.write_event(event)
                    with self._stats_lock:
                        self.written += 1
                except BaseException as e:  # noqa: BLE001
                    if not isinstance(e, Exception):
                        # SystemExit/KeyboardInterrupt from a store: the worker is gone. Mark the
                        # emitter dead so emit() refuses instead of accepting into a queue nobody
                        # reads (review R2-SC-08).
                        with self._lifecycle_lock:
                            self._stopped = True
                        with self._stats_lock:
                            self.write_failures += 1
                        logger.error(f"Learning event worker died ({type(e).__name__}); emitter stopped")
                        return
                    # FIX #12: Log data loss instead of silent fail
                    with self._stats_lock:
                        self.write_failures += 1
                    logger.error(f"Failed to write learning event (LOST): {event.event_id} — {e}")
            except queue.Empty:
                pass

    def emit(self, event: LearningEvent) -> bool:
        """Emit event (non-blocking).

        Returns:
            True if queued, False if queue full (dropped)
        """
        # FIX #7: Validate tenant_id scope (GDPR Art. 32 — no mixed-tenant queue)
        if not event.tenant_id or not isinstance(event.tenant_id, str):
            logger.error(f"Rejected event: invalid tenant_id={event.tenant_id!r}")
            return False

        with self._lifecycle_lock:
            if self._stopped or not self._worker_thread.is_alive():
                logger.error(f"Rejected event after stop(): {event.event_id}")
                return False
            try:
                self._queue.put_nowait(event)
                with self._stats_lock:
                    self.accepted += 1
                return True
            except queue.Full:
                with self._stats_lock:
                    self.dropped += 1
                    dropped = self.dropped
        logger.warning(f"Queue full, dropping event {event.event_id} (dropped so far: {dropped})")
        return False  # Queue full, drop event

    def stop(self, timeout: float = 5.0) -> None:
        """Stop accepting, then wait for every accepted event to be written.

        Idempotent and still WAITING on a repeat call: a first call that timed out used to leave a
        second call (and the atexit flush) returning at once with the worker still writing - events
        accepted with True were cut off at exit (review 2026-10-12, SC-11).
        """
        with self._lifecycle_lock:
            self._stopped = True   # from here no emit() can enqueue: every accepted event precedes the sentinel
            send_sentinel = not self._sentinel_sent
            self._sentinel_sent = True
        if send_sentinel:
            try:
                # Bounded: with a full queue and a stuck worker a plain put() blocked forever.
                self._queue.put(None, timeout=timeout)  # Sentinel
            except queue.Full:
                with self._lifecycle_lock:
                    self._sentinel_sent = False   # a later stop() may still deliver it
                raise RuntimeError("EventEmitter queue is full and its worker is not draining it") from None
        self._worker_thread.join(timeout=timeout)
        if self._worker_thread.is_alive():
            raise RuntimeError("EventEmitter worker thread failed to shut down within timeout")

    def _lost(self) -> int:
        with self._stats_lock:
            return max(0, self.accepted - self.written - self.write_failures)

    def _atexit_flush(self, timeout: float | None = None) -> None:
        """Flush this emitter (kept for callers/tests); the exit hook uses :func:`_flush_all`."""
        _flush_all([self], timeout if timeout is not None else EXIT_FLUSH_S)


#: How long interpreter exit waits - in TOTAL, across every live emitter - for accepted events.
EXIT_FLUSH_S = 30.0
_LIVE: "weakref.WeakSet[EventEmitter]" = weakref.WeakSet()


def _flush_all(emitters=None, timeout: float = EXIT_FLUSH_S) -> None:
    """Stop every emitter against ONE deadline: sentinels first, then join each with what is left.
    Whatever is still unwritten is COUNTED (accepted - written - failures) and logged."""
    emitters = list(_LIVE if emitters is None else emitters)
    deadline = time.monotonic() + max(0.0, timeout)
    for em in emitters:
        with em._lifecycle_lock:
            em._stopped = True
            send = not em._sentinel_sent
            em._sentinel_sent = True
        if send:
            try:
                em._queue.put_nowait(None)
            except queue.Full:
                with em._lifecycle_lock:
                    em._sentinel_sent = False
    for em in emitters:
        if not em._sentinel_sent:
            try:
                em._queue.put(None, timeout=max(0.0, deadline - time.monotonic()))
                em._sentinel_sent = True
            except queue.Full:
                pass
        em._worker_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        lost = em._lost()
        em.lost_at_exit = lost
        if lost:
            logger.error("EventEmitter: %d accepted learning event(s) not written at exit", lost)


atexit.register(_flush_all)
