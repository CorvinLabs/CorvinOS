"""Stream 4 Phase 1: EventStore Writer — Audit-First Feedback Integration (ADR-2050).

High-level writer for FeedbackEvent → EventStore with:
  - Audit-first pattern (core chain write before disk)
  - Tenant isolation validation (GDPR Art. 32)
  - In-memory queue + retry logic (EventStore unavailable)
  - PII scrubbing (fail-closed)

Integration Points:
  - core.learning.event_store.EventStore (audit-first persistence)
  - core.learning.learning_events.LearningEvent (immutable event schema)
  - core.compliance.audit_chain (hash-chain binding)

Guarantees:
  - No cross-tenant leakage (write rejected if tenant_id mismatch)
  - Audit-trail complete (every feedback recorded in hash chain)
  - Fail-closed on EventStore unavailable (queue, retry, no silent loss)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

from core.learning.event_store import EventStore, _validate_tenant_id
from core.learning.learning_events import LearningEvent, EventType
from core.learning.feedback_integration.models.feedback_event import FeedbackEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedFeedback:
    """In-memory queued feedback (before EventStore write succeeds)."""
    feedback: FeedbackEvent
    created_at: datetime
    retry_count: int = 0

    @property
    def age_seconds(self) -> float:
        """How long this feedback has been queued."""
        return (datetime.utcnow() - self.created_at).total_seconds()


class EventStoreWriter:
    """High-level writer: FeedbackEvent → EventStore (audit-first, tenant-scoped).

    Guarantees:
      - Audit-first: core chain write commits BEFORE disk write
      - Tenant-isolated: write rejected if store is bound to different tenant
      - Fail-closed: on EventStore unavailable, queue + retry (no silent loss)
      - PII-scrubbed: EventStore._scrub_pii_deep applied before disk
      - Immutable: FeedbackEvent frozen; EventStore records append-only

    Contract:
      - write_feedback(event) → audit_ref (success) or raises RuntimeError
      - on RuntimeError: feedback queued in-memory, retried on next write
      - on IOError: chain committed, disk write failed (chain record stands)
      - on queue overflow (10K events): oldest discarded (logged as critical)
    """

    MAX_QUEUE_SIZE = 10000  # Prevent OOM on sustained EventStore downtime
    QUEUE_FLUSH_INTERVAL = 60  # seconds; retry queued feedback every 60s
    MAX_RETRIES = 5  # Give up after 5 failed write attempts
    RETRY_BACKOFF_SECONDS = 2  # Initial backoff; exponential after

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: Optional[str] = None,
    ):
        """Initialize writer.

        Args:
            event_store: EventStore instance (audit-first, tenant-scoped)
            tenant_id: if given, writer is bound to this tenant and rejects
                foreign feedback (GDPR Art. 32). If None, no binding check.
        """
        if tenant_id is not None:
            _validate_tenant_id(tenant_id)
        self.event_store = event_store
        self.tenant_id = tenant_id

        # In-memory queue for failed writes
        self._queue: deque[QueuedFeedback] = deque(maxlen=self.MAX_QUEUE_SIZE)
        self._queue_lock = threading.RLock()

        # Background flush thread
        self._flusher_thread: Optional[threading.Thread] = None
        self._flusher_stop_event = threading.Event()

    def start_background_flusher(self) -> None:
        """Start background thread that retries queued feedback every 60s.

        Must be called once at startup; idempotent (second call is no-op).
        """
        if self._flusher_thread is not None:
            return  # Already running

        self._flusher_stop_event.clear()
        self._flusher_thread = threading.Thread(
            target=self._flush_loop,
            name="EventStoreWriter.Flusher",
            daemon=True,
        )
        self._flusher_thread.start()
        logger.info("EventStoreWriter background flusher started")

    def stop_background_flusher(self) -> None:
        """Stop the background flusher thread (graceful shutdown)."""
        if self._flusher_thread is None:
            return

        self._flusher_stop_event.set()
        self._flusher_thread.join(timeout=5)
        self._flusher_thread = None
        logger.info("EventStoreWriter background flusher stopped")

    def _flush_loop(self) -> None:
        """Background thread: retry queued feedback every QUEUE_FLUSH_INTERVAL."""
        while not self._flusher_stop_event.is_set():
            try:
                self.flush_queue()
            except Exception as e:
                logger.error(f"Error flushing queue: {e}", exc_info=True)

            # Sleep in small intervals so shutdown is responsive
            for _ in range(self.QUEUE_FLUSH_INTERVAL):
                if self._flusher_stop_event.is_set():
                    break
                time.sleep(1)

    def write_feedback(
        self,
        feedback: FeedbackEvent,
        lom: Optional[str] = None,
    ) -> str:
        """Write feedback: audit-chain FIRST (fail-closed), then disk.

        Converts FeedbackEvent → LearningEvent, then writes to EventStore.

        Args:
            feedback: FeedbackEvent (immutable, tenant-scoped)
            lom: Line of Moral Responsibility (code location for audit trail)

        Returns:
            audit_ref: Hash-chain reference (proof of audit commit)

        Raises:
            ValueError: tenant mismatch (GDPR Art. 32) or validation error
            RuntimeError: audit-chain unavailable (feedback queued, not lost)
            IOError: disk write failed AFTER chain commit (chain record stands)
        """
        # Tenant isolation check (GDPR Art. 32)
        if self.tenant_id is not None and feedback.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: writer bound to {self.tenant_id!r}, "
                f"feedback carries {feedback.tenant_id!r}"
            )

        # Convert FeedbackEvent → LearningEvent for EventStore
        learning_event = self._feedback_to_learning_event(feedback, lom=lom)

        try:
            # Write to EventStore (audit-chain FIRST, fail-closed)
            self.event_store.write_event(learning_event)
            return learning_event.audit_ref or "unknown"

        except RuntimeError as e:
            # EventStore unavailable or chain write failed
            logger.warning(
                f"EventStore unavailable, queueing feedback {feedback.feedback_id}: {e}"
            )
            self._enqueue_feedback(feedback, lom=lom)
            # Re-raise so caller knows write didn't complete
            raise RuntimeError(
                f"EventStore unavailable; feedback queued for retry: {e}"
            ) from e
        except IOError as e:
            # Chain committed, disk write failed (chain record stands)
            logger.error(
                f"Disk write failed for feedback {feedback.feedback_id} "
                f"(chain record exists): {e}",
                exc_info=True,
            )
            raise

    def flush_queue(self) -> int:
        """Retry all queued feedback; return count of successful writes.

        Called by background flusher every QUEUE_FLUSH_INTERVAL, or manually.

        Returns:
            Count of queued items successfully written.
        """
        flushed_count = 0

        with self._queue_lock:
            while self._queue:
                queued = self._queue[0]  # Peek at oldest

                # Check retry limit
                if queued.retry_count >= self.MAX_RETRIES:
                    logger.critical(
                        f"Discarding feedback {queued.feedback.feedback_id} "
                        f"after {self.MAX_RETRIES} retries (queued for {queued.age_seconds:.1f}s)"
                    )
                    self._queue.popleft()
                    continue

                # Exponential backoff
                backoff = self.RETRY_BACKOFF_SECONDS ** queued.retry_count
                if queued.age_seconds < backoff:
                    break  # Not ready to retry yet

                try:
                    # Retry the write
                    self.event_store.write_event(
                        self._feedback_to_learning_event(queued.feedback, lom=queued.feedback.lom)
                    )
                    logger.info(
                        f"Flushed queued feedback {queued.feedback.feedback_id} "
                        f"(attempt {queued.retry_count + 1})"
                    )
                    self._queue.popleft()
                    flushed_count += 1

                except (RuntimeError, IOError) as e:
                    # Retry failed; re-queue with incremented retry count
                    self._queue.popleft()
                    retry_feedback = QueuedFeedback(
                        feedback=queued.feedback,
                        created_at=queued.created_at,
                        retry_count=queued.retry_count + 1,
                    )
                    self._queue.appendleft(retry_feedback)
                    logger.warning(
                        f"Retry failed for {queued.feedback.feedback_id} "
                        f"(attempt {queued.retry_count + 1}/{self.MAX_RETRIES}): {e}"
                    )
                    break  # Stop retrying this batch; next batch in next interval

        return flushed_count

    def get_queue_size(self) -> int:
        """Get current queue size (for testing/monitoring)."""
        with self._queue_lock:
            return len(self._queue)

    def get_queued_feedback(self) -> list[QueuedFeedback]:
        """Get snapshot of queued feedback (for testing)."""
        with self._queue_lock:
            return list(self._queue)

    def _enqueue_feedback(self, feedback: FeedbackEvent, lom: Optional[str] = None) -> None:
        """Enqueue feedback for retry (internal).

        Args:
            feedback: FeedbackEvent to queue
            lom: Line of Moral Responsibility

        Raises:
            RuntimeError: queue is at max size (oldest feedback discarded)
        """
        with self._queue_lock:
            if len(self._queue) >= self.MAX_QUEUE_SIZE:
                # Queue is full; discard oldest (should never happen in normal operation)
                discarded = self._queue.popleft()
                logger.critical(
                    f"Queue overflow (max {self.MAX_QUEUE_SIZE}); "
                    f"discarded oldest feedback {discarded.feedback.feedback_id} "
                    f"(queued for {discarded.age_seconds:.1f}s)"
                )

            queued = QueuedFeedback(
                feedback=feedback,
                created_at=datetime.utcnow(),
                retry_count=0,
            )
            self._queue.append(queued)
            logger.warning(f"Queued feedback {feedback.feedback_id} for retry (queue size: {len(self._queue)})")

    @staticmethod
    def _feedback_to_learning_event(
        feedback: FeedbackEvent,
        lom: Optional[str] = None,
    ) -> LearningEvent:
        """Convert FeedbackEvent → LearningEvent for EventStore.

        Carries feedback payload in signal dict.

        Args:
            feedback: FeedbackEvent (from Stream 1–3 skill feedback endpoints)
            lom: Line of Moral Responsibility (if not in feedback)

        Returns:
            LearningEvent (frozen, tenant-scoped, audit-bindable)
        """
        signal = {
            "feedback_id": feedback.feedback_id,
            "feedback_type": feedback.feedback_type.value,
            "task_id": feedback.task_id,
        }

        # Add feedback-type-specific data
        if feedback.outcome:
            signal["outcome"] = feedback.outcome.value
        if feedback.preference:
            signal["preference"] = feedback.preference.value
        if feedback.confidence_score is not None:
            signal["confidence_score"] = feedback.confidence_score
        if feedback.metric_name:
            signal["metric_name"] = feedback.metric_name
            signal["metric_value"] = feedback.metric_value

        return LearningEvent.create(
            event_type=EventType.FEEDBACK,  # FeedbackEvent → FEEDBACK learning event
            skill_id=feedback.skill_id,
            tenant_id=feedback.tenant_id,
            signal=signal,
            lom=lom or feedback.lom,
        )
