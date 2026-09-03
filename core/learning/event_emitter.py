"""Phase 2: EventEmitter — Async non-blocking event emission (ADR-0314)."""

from __future__ import annotations

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
        """Initialize emitter with event store."""
        self.store = event_store
        self._queue: queue.Queue[LearningEvent] = queue.Queue(maxsize=queue_size)
        self._worker_thread = threading.Thread(target=self._worker, daemon=False)
        self._worker_thread.start()

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

        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            logger.warning(f"Queue full, dropping event {event.event_id}")
            return False  # Queue full, drop event

    def stop(self) -> None:
        """Stop emitter (wait for queue to flush)."""
        self._queue.put(None)  # Sentinel
        self._worker_thread.join(timeout=5.0)
        if self._worker_thread.is_alive():
            raise RuntimeError("EventEmitter worker thread failed to shut down within timeout")
