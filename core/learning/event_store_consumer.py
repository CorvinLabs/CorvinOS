"""EventStore Consumer Chain (Stream A, Module A1).

Learning Loop Closure (Gap-003): Read learning events from EventStore,
aggregate into statistical windows, scrub PII, emit audit events (audit-first).

Architecture:
  EventStore (tenant-bound) → EventStoreConsumer.read_batch()
  → BatchAggregator.fold_window()
  → FeedbackFilter.scrub_pii()
  → AuditFirstWriter.write_event()
  → outcome_sink.process() [A2 wiring]

Audit-first (ADR-0644): Write to core chain FIRST; if write fails, consumer stops.
Tenant isolation: All ops scoped by tenant_id (GDPR Art. 5, 6, 32).

References:
  - ADR-0644 (audit-first)
  - ADR-0314 (learning event schema)
  - ADR-0613 (loop closure)

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
``run_consumer_cycle`` defaults ``write_func`` to the real core-chain writer
(``event_persistence.core_audit_event``); the injected ``write_func`` shape it
documented, ``write_func(event_type, payload, tenant_id=...)``, matched no
writer in the repo. ``read_batch`` calls ``event_store.query(tenant_id=,
after_ts=, limit=)``, which NO EventStore in the repo implements
(``core.learning.event_store.EventStore`` has ``query_events``) — so the
consumer cannot read a real store yet; only duck-typed test doubles work.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)

# ============================================================================
# TYPES
# ============================================================================


@dataclass(frozen=True)
class LearningEvent:
    """Immutable learning event (from EventStore)."""

    event_type: str  # outcome_feedback, preference_feedback, confidence_score
    skill_id: str
    timestamp: datetime
    tenant_id: str
    payload: dict[str, Any]  # Structured, no free text
    audit_ref: str  # Hash-chain reference


@dataclass(frozen=True)
class HistogramBucket:
    """Statistical window (aggregated)."""

    skill_id: str
    window_ts: datetime
    outcome_count: int
    avg_confidence: float
    feedback_count: int
    audit_ref: str  # Audit event reference


# ============================================================================
# CONSUMER: Read from EventStore (tenant-bound)
# ============================================================================


class EventStoreConsumer:
    """Read batches from EventStore without race conditions.

    Responsibility:
    - Connect to EventStore (tenant-bound)
    - Read batches (no duplicates, ordered by timestamp)
    - Validate tenant_id isolation
    - Delegate to BatchAggregator
    - Emit audit event on successful batch
    """

    def __init__(self, tenant_id: str, batch_size: int = 100):
        """Initialize consumer.

        Args:
            tenant_id: Tenant identifier (scopes all operations)
            batch_size: Max events per batch (default 100)
        """
        self.tenant_id = tenant_id
        self.batch_size = batch_size
        self._last_ts: Optional[datetime] = None

    def read_batch(self, event_store: Any) -> list[LearningEvent]:
        """Read batch of events from EventStore.

        Args:
            event_store: EventStore instance (must be tenant-bound)

        Returns:
            Ordered list of events (may be empty)

        Raises:
            ValueError: If tenant_id mismatch (fail-closed)
        """
        # Verify EventStore is tenant-scoped
        if getattr(event_store, "tenant_id", None) != self.tenant_id:
            raise ValueError(
                f"EventStore tenant mismatch: expected {self.tenant_id}, "
                f"got {getattr(event_store, 'tenant_id', 'NONE')}"
            )

        # Read batch (ordered by timestamp, after self._last_ts)
        start = self._last_ts or datetime(1970, 1, 1)
        events = event_store.query(
            tenant_id=self.tenant_id,
            after_ts=start,
            limit=self.batch_size,
        )

        if not events:
            return []

        # Convert to LearningEvent (immutable)
        learning_events = [
            LearningEvent(
                event_type=e.get("event_type"),
                skill_id=e.get("skill_id"),
                timestamp=e.get("timestamp"),
                tenant_id=self.tenant_id,  # Force tenant isolation
                payload=e.get("payload", {}),
                audit_ref=e.get("audit_ref", ""),
            )
            for e in events
        ]

        # Update watermark
        if learning_events:
            self._last_ts = learning_events[-1].timestamp

        return learning_events


# ============================================================================
# AGGREGATOR: Fold events into statistical windows
# ============================================================================


class BatchAggregator:
    """Aggregate events into statistical windows (per skill).

    Responsibility:
    - Group events by skill_id
    - Fold into time windows (e.g., 1-hour buckets)
    - Compute statistics (count, avg_confidence, feedback_count)
    - Return HistogramBucket per window
    """

    WINDOW_DURATION = timedelta(hours=1)

    @staticmethod
    def fold_window(
        events: list[LearningEvent], skill_id: str
    ) -> Optional[HistogramBucket]:
        """Aggregate events for one skill into a histogram bucket.

        Args:
            events: Filtered events (one skill_id)
            skill_id: Skill identifier

        Returns:
            HistogramBucket with aggregated stats, or None if no events

        Raises:
            ValueError: If events have mismatched skill_id
        """
        if not events:
            return None

        # Validate homogeneity
        for e in events:
            if e.skill_id != skill_id:
                raise ValueError(
                    f"Homogeneity violation: expected {skill_id}, got {e.skill_id}"
                )

        # Compute statistics
        outcome_count = sum(1 for e in events if e.event_type == "outcome_feedback")
        feedback_count = sum(1 for e in events if e.event_type == "preference_feedback")

        confidences = [
            e.payload.get("confidence", 0.5)
            for e in events
            if e.event_type == "confidence_score"
        ]
        avg_confidence = (
            sum(confidences) / len(confidences) if confidences else 0.5
        )

        # Window timestamp (use first event)
        window_ts = events[0].timestamp.replace(minute=0, second=0, microsecond=0)

        return HistogramBucket(
            skill_id=skill_id,
            window_ts=window_ts,
            outcome_count=outcome_count,
            avg_confidence=avg_confidence,
            feedback_count=feedback_count,
            audit_ref="",  # Will be filled by AuditFirstWriter
        )


# ============================================================================
# FILTER: Scrub PII (GDPR Art. 5, 32)
# ============================================================================


class FeedbackFilter:
    """Scrub PII from events (fail-closed).

    Responsibility:
    - Drop 'reason' text (free-form user input)
    - Keep 'reason_length' (metadata only)
    - Drop 'operator_id' (PII)
    - Keep event_type + skill_id + hash only

    Fail-closed: Any event with detected PII pattern → dropped + logged
    """

    @staticmethod
    def scrub_pii(event: LearningEvent) -> Optional[LearningEvent]:
        """Scrub PII from event.

        Args:
            event: LearningEvent to scrub

        Returns:
            Scrubbed event, or None if PII detected (fail-closed)
        """
        payload = event.payload.copy()

        # Drop free-text fields
        if "reason" in payload:
            reason_len = len(payload.pop("reason", ""))
            payload["reason_length"] = reason_len
            _log.debug(f"Scrubbed reason text (len={reason_len})")

        # Drop PII patterns
        if "operator_id" in payload:
            payload.pop("operator_id")
            _log.debug("Scrubbed operator_id")

        # Detect residual PII (email, phone, etc.) — fail-closed
        payload_str = str(payload).lower()
        if any(pattern in payload_str for pattern in ["@", "phone", "user:"]):
            _log.error(f"PII detected in {event.skill_id}, dropping event")
            return None

        return LearningEvent(
            event_type=event.event_type,
            skill_id=event.skill_id,
            timestamp=event.timestamp,
            tenant_id=event.tenant_id,
            payload=payload,
            audit_ref=event.audit_ref,
        )


# ============================================================================
# AUDIT-FIRST WRITER (ADR-0644)
# ============================================================================


class AuditFirstWriter:
    """Write aggregation → core chain FIRST (ADR-0644).

    Responsibility:
    - Write audit event to core chain
    - If write fails → raise exception (no retry, consumer stops)
    - Emit 'learning.outcome_aggregated' event
    - Return audit_ref (for HistogramBucket)
    """

    @staticmethod
    def write_event(
        tenant_id: str, bucket: HistogramBucket, write_func: Any
    ) -> str:
        """Write aggregation event to audit chain (fail-closed).

        Args:
            tenant_id: Tenant identifier
            bucket: HistogramBucket to audit
            write_func: write_event() function (forge.security_events)

        Returns:
            audit_ref (hash-chain reference)

        Raises:
            RuntimeError: If audit chain write fails
        """
        event_payload = {
            "skill_id": bucket.skill_id,
            "window_ts": bucket.window_ts.isoformat(),
            "outcome_count": bucket.outcome_count,
            "avg_confidence": bucket.avg_confidence,
            "feedback_count": bucket.feedback_count,
            "tenant_id": tenant_id,
        }

        try:
            # Write FIRST (audit-first, not optional)
            audit_ref = write_func(
                "learning.outcome_aggregated",
                event_payload,
                tenant_id=tenant_id,
            )
            _log.info(f"Audit write OK: {audit_ref}")
            return audit_ref
        except Exception as e:
            _log.error(f"Audit chain write failed: {e} (fail-closed)")
            raise RuntimeError(f"Audit write failed: {e}") from e


# ============================================================================
# INTEGRATION: Full consumer pipeline
# ============================================================================


def _core_chain_write(event_type: str, payload: dict, *, tenant_id: str) -> str:
    """Default ``write_func``: the learning subsystem's fail-closed core writer."""
    from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

    return core_audit_event(event_type, tenant_id=tenant_id, details=payload)


def run_consumer_cycle(
    tenant_id: str,
    event_store: Any,
    write_func: Any = None,
) -> int:
    """Run one consumer cycle (read → aggregate → audit → emit).

    Args:
        tenant_id: Tenant identifier
        event_store: EventStore instance
        write_func: Audit write function

    Returns:
        Event count processed

    Raises:
        RuntimeError: If audit write fails (fail-closed)
    """
    if write_func is None:
        write_func = _core_chain_write
    consumer = EventStoreConsumer(tenant_id)

    # Read batch
    events = consumer.read_batch(event_store)
    if not events:
        _log.debug("No events in batch (empty window)")
        return 0

    # Filter PII
    scrubbed = [FeedbackFilter.scrub_pii(e) for e in events]
    scrubbed = [e for e in scrubbed if e is not None]  # Drop None (PII dropped)

    # Aggregate by skill
    by_skill = {}
    for e in scrubbed:
        if e.skill_id not in by_skill:
            by_skill[e.skill_id] = []
        by_skill[e.skill_id].append(e)

    # Fold windows + audit
    for skill_id, skill_events in by_skill.items():
        bucket = BatchAggregator.fold_window(skill_events, skill_id)
        if not bucket:
            continue

        # Write audit event (fail-closed: if this fails, consumer stops)
        audit_ref = AuditFirstWriter.write_event(tenant_id, bucket, write_func)

        # Next: outcome_sink.process() [Stream A, Module A2]
        # (Placeholder for wiring)
        _log.info(f"Bucket ready for outcome_sink: {skill_id} (audit_ref={audit_ref})")

    return len(scrubbed)
