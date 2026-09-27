"""
Stream B: Audit Event Enrichment

Enriches A2 OutcomeRecord with A3 ConfidenceScore data.
Emits learning.enriched_outcome_event to audit chain + dashboard sink.

Phase 1: Basic enrichment (outcome + confidence + trend)
Phase 2: Deduplication + backpressure handling
Phase 3: Cross-stream correlation

References: ADR-2088 (Stream B Design), ADR-0665 (Audit-First Learning)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichedOutcomeEvent:
    """Immutable enriched outcome event for audit trail + dashboard."""

    # A2 fields
    skill_id: str
    outcome_count: int
    avg_confidence: float
    a2_audit_ref: str

    # A3 enrichments
    success_count: int
    escalation_count: int
    confidence_delta: float
    trend: str
    a3_audit_ref: str

    # Metadata
    audit_ref: str
    tenant_id: str
    timestamp: datetime


class AuditEventEnricher:
    """
    Phase 1: Enrich A2 outcomes with A3 scores.

    Input: A2.OutcomeRecord + A3.ConfidenceScore
    Output: EnrichedOutcomeEvent (all fields combined)

    Invariants:
    - Non-blocking: failures logged
    - Append-only: never modifies outcomes
    - Audit-chained: emit to core chain + dashboard sink
    """

    def __init__(self, tenant_id: str):
        """Initialize enricher for tenant."""
        self.tenant_id = tenant_id
        self._enriched_count = 0

    def enrich(
        self,
        outcome_record,  # A2 OutcomeRecord
        confidence_score,  # A3 ConfidenceScore
    ) -> Optional[EnrichedOutcomeEvent]:
        """
        Combine A2 outcome + A3 score into enriched event.

        Args:
            outcome_record: A2 OutcomeRecord
            confidence_score: A3 ConfidenceScore

        Returns:
            EnrichedOutcomeEvent with all fields, or None on validation failure
        """
        if outcome_record is None or confidence_score is None:
            logger.warning("AuditEventEnricher.enrich() received None input")
            return None

        try:
            # Validate correlation (should be same skill)
            if outcome_record.skill_id != confidence_score.skill_id:
                logger.error(
                    f"Skill mismatch: A2={outcome_record.skill_id}, "
                    f"A3={confidence_score.skill_id}"
                )
                return None

            # Emit audit event
            audit_ref = self._emit_enriched_outcome_event(
                skill_id=outcome_record.skill_id,
                outcome_count=outcome_record.outcome_count,
                confidence_delta=confidence_score.confidence_delta,
                trend=confidence_score.trend,
            )

            # Create immutable enriched event
            event = EnrichedOutcomeEvent(
                skill_id=outcome_record.skill_id,
                outcome_count=outcome_record.outcome_count,
                avg_confidence=outcome_record.avg_confidence,
                a2_audit_ref=outcome_record.audit_ref,
                success_count=confidence_score.success_count,
                escalation_count=confidence_score.escalation_count,
                confidence_delta=confidence_score.confidence_delta,
                trend=confidence_score.trend,
                a3_audit_ref=confidence_score.audit_ref,
                audit_ref=audit_ref,
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow(),
            )

            self._enriched_count += 1
            logger.debug(
                f"Stream B: Enriched {outcome_record.skill_id} "
                f"outcomes={outcome_record.outcome_count} "
                f"delta={confidence_score.confidence_delta:.4f} "
                f"trend={confidence_score.trend}"
            )
            return event

        except Exception as e:
            logger.error(f"AuditEventEnricher.enrich() failed: {e}", exc_info=True)
            raise

    def _emit_enriched_outcome_event(
        self,
        skill_id: str,
        outcome_count: int,
        confidence_delta: float,
        trend: str,
    ) -> str:
        """
        Emit learning.enriched_outcome_event to audit chain + dashboard sink.

        Phase 1: Stub with UUID
        Phase 2: Real audit chain write + dashboard subscription
        """
        import uuid

        audit_ref = uuid.uuid4().hex[:16]
        logger.debug(
            f"[AUDIT STUB] learning.enriched_outcome_event: "
            f"{skill_id} outcomes={outcome_count} "
            f"delta={confidence_delta:.4f} trend={trend} ref={audit_ref}"
        )
        return audit_ref

    def get_enriched_count(self) -> int:
        """Return count of enriched events."""
        return self._enriched_count
