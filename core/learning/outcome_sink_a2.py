"""Stream A Module A2 — OutcomeSink Wiring (HistogramBucket → Audit + A3 Queue).

Learning Loop Closure Stream A (ADR-0613 + ADR-0574):
  A1.EventStoreConsumer → HistogramBucket
  → A2.OutcomeSink.process() [THIS MODULE]
  → audit write (learning.outcome_processed)
  → A3.ConfidenceScorer task enqueue

Audit-first (ADR-0644): Write to core chain FIRST; if write fails, raise RuntimeError.
Fail-closed: invalid outcomes rejected BEFORE audit write.
Tenant isolation: All ops scoped by tenant_id (GDPR Art. 5, 6, 32).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutcomeRecord:
    """Immutable record produced by OutcomeSink.process()."""

    skill_id: str
    outcome_count: int
    avg_confidence: float
    audit_ref: str  # Reference to the audit event written


class OutcomeSink:
    """Process HistogramBucket from A1 → validate → audit → enqueue A3.

    Responsibility:
    - Validate outcome metrics (bounds + NaN checks)
    - Write audit event (audit-first: fail-closed on error)
    - Enqueue A3 (ConfidenceScorer) task via orchestrator
    - Return OutcomeRecord for buffering/testing

    Invariants:
    - outcome_count >= 0 (never negative)
    - avg_confidence in [0.0, 1.0] (never out of bounds)
    - No NaN values (catch IEEE float corruption)
    - Audit write BEFORE any side effects (fail-closed)
    - Tenant isolation (no cross-tenant leakage)
    """

    def __init__(self, tenant_id: str, orchestrator: Optional[Any] = None):
        """Initialize OutcomeSink.

        Args:
            tenant_id: Tenant identifier (scopes all operations)
            orchestrator: Task orchestrator (ADR-0574); if None, skips A3 enqueue
        """
        self.tenant_id = tenant_id
        self.orchestrator = orchestrator
        self._buffer: list[OutcomeRecord] = []

    def validate_outcome(self, outcome_count: int, avg_confidence: float) -> bool:
        """Validate outcome metrics (bounds + NaN checks).

        Args:
            outcome_count: Number of outcomes aggregated (must be >= 0)
            avg_confidence: Average confidence [0.0, 1.0] (no NaN)

        Returns:
            True if valid, False otherwise
        """
        # Check bounds
        if outcome_count < 0:
            _log.warning("OutcomeSink: outcome_count < 0 (%d)", outcome_count)
            return False

        # Check NaN
        try:
            if avg_confidence != avg_confidence:  # NaN check (self-inequality)
                _log.warning("OutcomeSink: avg_confidence is NaN")
                return False
        except TypeError:
            _log.warning("OutcomeSink: avg_confidence is not a float")
            return False

        # Check bounds
        if not (0.0 <= avg_confidence <= 1.0):
            _log.warning(
                "OutcomeSink: avg_confidence out of bounds (%.3f)", avg_confidence
            )
            return False

        return True

    def process(self, histogram_bucket: Any) -> Optional[OutcomeRecord]:
        """Process HistogramBucket: validate → audit → enqueue → return record.

        Args:
            histogram_bucket: HistogramBucket from A1 (has skill_id, outcome_count, etc.)

        Returns:
            OutcomeRecord if successful, None if validation or audit fails

        Raises:
            RuntimeError: If audit write fails (fail-closed)
        """
        # Extract fields from bucket
        skill_id = getattr(histogram_bucket, "skill_id", "")
        outcome_count = getattr(histogram_bucket, "outcome_count", -1)
        avg_confidence = getattr(histogram_bucket, "avg_confidence", -1.0)
        audit_ref = getattr(histogram_bucket, "audit_ref", "")

        # Validate BEFORE audit write
        if not self.validate_outcome(outcome_count, avg_confidence):
            _log.debug(
                "OutcomeSink: validation failed (skill=%s, count=%d, conf=%.3f)",
                skill_id, outcome_count, avg_confidence,
            )
            return None

        # Audit-FIRST: write to core chain
        try:
            audit_ref_result = self._write_audit_event(
                skill_id=skill_id,
                outcome_count=outcome_count,
                avg_confidence=avg_confidence,
                bucket_audit_ref=audit_ref,
            )
        except RuntimeError as exc:
            _log.error(
                "OutcomeSink: audit write FAILED (fail-closed, skill=%s): %s",
                skill_id, type(exc).__name__,
            )
            raise

        # Enqueue A3 (ConfidenceScorer) task
        if self.orchestrator is not None:
            try:
                self._enqueue_a3_task(
                    skill_id=skill_id,
                    outcome_count=outcome_count,
                    avg_confidence=avg_confidence,
                    audit_ref=audit_ref_result,
                )
            except Exception as exc:  # noqa: BLE001 — log but don't fail (A3 is advisory)
                _log.warning(
                    "OutcomeSink: A3 enqueue failed (advisory, skill=%s): %s",
                    skill_id, type(exc).__name__,
                )

        # Create record
        record = OutcomeRecord(
            skill_id=skill_id,
            outcome_count=outcome_count,
            avg_confidence=avg_confidence,
            audit_ref=audit_ref_result,
        )

        # Buffer for testing/diagnostics
        self._buffer.append(record)

        _log.info(
            "OutcomeSink: outcome processed (skill=%s, count=%d, conf=%.3f, audit=%s)",
            skill_id, outcome_count, avg_confidence, audit_ref_result,
        )

        return record

    def _write_audit_event(
        self,
        skill_id: str,
        outcome_count: int,
        avg_confidence: float,
        bucket_audit_ref: str,
    ) -> str:
        """Write audit event to core hash-chain (audit-first, fail-closed).

        Args:
            skill_id: Skill identifier
            outcome_count: Number of outcomes
            avg_confidence: Average confidence
            bucket_audit_ref: Reference to A1 bucket audit event

        Returns:
            Audit event reference (for linking)

        Raises:
            RuntimeError: If audit write fails (fail-closed)
        """
        try:
            from forge.security_events import write_event  # noqa: PLC0415
            from forge.paths import tenant_audit_chain  # noqa: PLC0415
            import uuid

            audit_ref = uuid.uuid4().hex[:16]

            write_event(
                tenant_audit_chain(self.tenant_id),
                "learning.outcome_processed",
                tool="learning_a2",
                details={
                    "outcome_ref": audit_ref,
                    "skill_id": skill_id,
                    "outcome_count": outcome_count,
                    "avg_confidence": round(avg_confidence, 3),
                    "bucket_audit_ref": bucket_audit_ref,
                    "tenant_id": self.tenant_id,
                },
            )
            return audit_ref
        except Exception as exc:
            raise RuntimeError(f"audit write failed: {type(exc).__name__}") from exc

    def _enqueue_a3_task(
        self,
        skill_id: str,
        outcome_count: int,
        avg_confidence: float,
        audit_ref: str,
    ) -> None:
        """Enqueue ConfidenceScorer (A3) task via orchestrator (ADR-0574).

        Args:
            skill_id: Skill identifier
            outcome_count: Number of outcomes
            avg_confidence: Average confidence
            audit_ref: Audit event reference

        Raises:
            Exception: If orchestrator.enqueue() fails (logged but not fatal)
        """
        if self.orchestrator is None:
            return

        task_payload = {
            "task_type": "learning.confidence_score",
            "skill_id": skill_id,
            "outcome_count": outcome_count,
            "avg_confidence": avg_confidence,
            "audit_ref": audit_ref,
            "tenant_id": self.tenant_id,
        }

        self.orchestrator.enqueue(task_payload)

    def get_buffer(self) -> list[OutcomeRecord]:
        """Access buffered records (for testing/diagnostics)."""
        return list(self._buffer)
