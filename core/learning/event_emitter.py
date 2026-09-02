"""Phase 2: EventEmitter — Async non-blocking event emission (ADR-0314)."""

from __future__ import annotations

import queue
import threading
from typing import Optional

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore


class EventEmitter:
    """Non-blocking async event emitter (fire-and-forget queue)."""

    def __init__(self, event_store: EventStore, queue_size: int = 1000):
        """Initialize emitter with event store."""
        self.store = event_store
        self._queue: queue.Queue[LearningEvent] = queue.Queue(maxsize=queue_size)
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
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
                    pass  # Silent fail (fire-and-forget)
            except queue.Empty:
                pass

    def emit(self, event: LearningEvent) -> bool:
        """Emit event (non-blocking).

        Returns:
            True if queued, False if queue full (dropped)
        """
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            return False  # Queue full, drop event

    def stop(self) -> None:
        """Stop emitter (wait for queue to flush)."""
        self._queue.put(None)  # Sentinel
        self._worker_thread.join(timeout=5.0)
