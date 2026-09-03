"""Phase 2: EventEmitter — Async non-blocking event emission (ADR-0314)."""

from __future__ import annotations

import atexit
import logging
import queue
import threading
from typing import Optional

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore

logger = logging.getLogger(__name__)


class EventEmitter:
    """Non-blocking async event emitter (fire-and-forget queue)."""

    def __init__(self, event_store: EventStore, queue_size: int = 1000):
        """Initialize emitter with event store.

        Args:
            event_store: An ``EventStore`` (anything with ``write_event(event)``).
            queue_size: Bounded queue depth (int > 0).

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
        self.dropped = 0  # queue-full drops (observable, never silent)
        self.write_failures = 0
        self._worker_thread = threading.Thread(
            target=self._worker, name="learning-event-emitter", daemon=True
        )
        self._worker_thread.start()
        atexit.register(self._atexit_flush)

    def _worker(self) -> None:
        """Background worker: consume queue and write events."""
        while True:
            try:
                event = self._queue.get(timeout=5.0)
                if event is None:  # Sentinel: stop
                    break
                try:
                    self.store.write_event(event)
                except Exception as e:
                    # FIX #12: Log data loss instead of silent fail
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

        if self._stopped:
            logger.error(f"Rejected event after stop(): {event.event_id}")
            return False

        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            self.dropped += 1
            logger.warning(f"Queue full, dropping event {event.event_id} (dropped so far: {self.dropped})")
            return False  # Queue full, drop event

    def stop(self, timeout: float = 5.0) -> None:
        """Stop emitter (wait for queue to flush)."""
        if self._stopped:
            return
        self._stopped = True
        self._queue.put(None)  # Sentinel
        self._worker_thread.join(timeout=timeout)
        if self._worker_thread.is_alive():
            raise RuntimeError("EventEmitter worker thread failed to shut down within timeout")

    def _atexit_flush(self) -> None:
        """Best-effort flush at interpreter exit (daemon thread would otherwise be cut mid-write)."""
        try:
            self.stop(timeout=2.0)
        except Exception:  # noqa: BLE001 — exit path, nothing left to report to
            pass
