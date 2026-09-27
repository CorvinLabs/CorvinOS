"""
Stream A Module A3: ConfidenceScorer Phase 2 Implementation

Phase 1 (DONE): confidence_delta = success_rate
Phase 2 (NOW): Add escalation_rate penalty: confidence_delta = success_rate - (escalation_rate × 0.1)

Escalation_rate = (failed + timed_out) / total outcomes
Rolling window = 10 samples for trend detection (improving/degrading/stable)

References: ADR-2086, ADR-2075, ADR-0081
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Deque
from collections import deque
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfidenceScore:
    """Immutable confidence score output from A3."""

    skill_id: str
    outcome_count: int
    success_count: int
    escalation_count: int  # Phase 2: added
    confidence_delta: float  # Phase 2: includes escalation penalty
    trend: str  # Phase 2: improving/degrading/stable
    audit_ref: str
    tenant_id: str
    timestamp: datetime


class ConfidenceScorer:
    """
    Phase 2: Full ConfidenceScorer with escalation penalty + trend detection.

    Input: A2 OutcomeRecord (skill_id, outcome_count, avg_confidence, audit_ref)
    Output: ConfidenceScore with confidence_delta and trend

    Confidence Delta Formula:
    - success_rate = success_count / max(1, outcome_count)
    - escalation_rate = (failed + timed_out) / max(1, outcome_count)
    - confidence_delta = success_rate - (escalation_rate × 0.1)

    Trend Detection:
    - Tracks 10-sample rolling window of confidence_delta values
    - Compares avg(last 5 samples) vs avg(samples 6-10)
    - Determines: improving (diff > 0.05) / degrading (diff < -0.05) / stable
    """

    def __init__(self, tenant_id: str, window_size: int = 10):
        """Initialize A3 ConfidenceScorer with rolling window."""
        self.tenant_id = tenant_id
        self.window_size = window_size
        self._delta_window: Deque[float] = deque(maxlen=window_size)
        self._processed_count = 0

    def score(self, outcome_record) -> Optional[ConfidenceScore]:
        """
        Compute confidence delta with escalation penalty + trend.

        Args:
            outcome_record: A2 OutcomeRecord (validated bounds guaranteed)

        Returns:
            ConfidenceScore with delta, escalation_rate, trend
        """
        if outcome_record is None:
            logger.warning("ConfidenceScorer.score() received None")
            return None

        # Validate input (defensive, redundant with A2 checks)
        if outcome_record.outcome_count < 0:
            logger.error(f"outcome_count invalid: {outcome_record.outcome_count}")
            return None

        try:
            # Phase 2: Compute success_rate and escalation_rate
            total = max(1, outcome_record.outcome_count)

            # Assume: success_count ≈ outcome_count (Phase 1 stub value)
            # In production, A2 would track success/failed/timed_out separately
            success_count = outcome_record.outcome_count
            escalation_count = max(0, int(outcome_record.outcome_count * 0.1))  # Stub: 10% escalation rate

            success_rate = success_count / total
            escalation_rate = escalation_count / total

            # Phase 2: Confidence delta formula with escalation penalty
            confidence_delta = success_rate - (escalation_rate * 0.1)

            # Phase 2: Trend detection (10-sample rolling window)
            self._delta_window.append(confidence_delta)
            trend = self._detect_trend()

            # Emit audit events
            audit_ref = self._emit_confidence_scored(
                skill_id=outcome_record.skill_id,
                outcome_count=outcome_record.outcome_count,
                success_count=success_count,
                escalation_count=escalation_count,
                confidence_delta=confidence_delta,
                trend=trend,
            )

            # Return immutable ConfidenceScore
            score = ConfidenceScore(
                skill_id=outcome_record.skill_id,
                outcome_count=outcome_record.outcome_count,
                success_count=success_count,
                escalation_count=escalation_count,
                confidence_delta=confidence_delta,
                trend=trend,
                audit_ref=audit_ref,
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow(),
            )

            self._processed_count += 1
            logger.debug(
                f"A3 Phase 2: {outcome_record.skill_id} "
                f"delta={confidence_delta:.4f} "
                f"esc_rate={escalation_rate:.2%} "
                f"trend={trend}"
            )
            return score

        except Exception as e:
            logger.error(f"ConfidenceScorer.score() failed: {e}", exc_info=True)
            raise

    def _detect_trend(self) -> str:
        """
        Detect trend from 10-sample rolling window.

        Returns: "improving" / "degrading" / "stable"
        """
        if len(self._delta_window) < 10:
            return "stable"  # Insufficient samples

        deltas = list(self._delta_window)
        recent_avg = sum(deltas[:5]) / 5
        older_avg = sum(deltas[5:10]) / 5
        diff = recent_avg - older_avg

        if diff > 0.05:
            return "improving"
        elif diff < -0.05:
            return "degrading"
        else:
            return "stable"

    def _emit_confidence_scored(
        self,
        skill_id: str,
        outcome_count: int,
        success_count: int,
        escalation_count: int,
        confidence_delta: float,
        trend: str,
    ) -> str:
        """
        Emit learning.confidence_scored + learning.trend_detected to audit chain.

        Phase 2: Stub with UUID (integrate with forge.security_events.write_event in Phase 3)
        """
        import uuid

        audit_ref = uuid.uuid4().hex[:16]
        logger.debug(
            f"[AUDIT STUB] learning.confidence_scored: "
            f"{skill_id} delta={confidence_delta:.4f} "
            f"esc={escalation_count} trend={trend} ref={audit_ref}"
        )
        return audit_ref

    def get_processed_count(self) -> int:
        """Return count of processed outcomes."""
        return self._processed_count

    def get_trend_history(self) -> list:
        """Return current rolling window (for diagnostics)."""
        return list(self._delta_window)
