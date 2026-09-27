"""
Stream A Module A3: ConfidenceScorer Phase 2 Implementation

Phase 1 (DONE): confidence_delta = success_rate
Phase 2 (NOW): Add escalation_rate penalty: confidence_delta = success_rate - (escalation_rate × 0.1)

Escalation_rate = (failed + timed_out) / total outcomes
Rolling window = 10 samples for trend detection (improving/degrading/stable)

References: ADR-2086, ADR-2075, ADR-0081

Adversarial review 2026-09-27:
- success/escalation counts are READ from the input record
  (``success_count`` + ``failed_count``/``timed_out_count`` or
  ``escalation_count``). They used to be fabricated (success = every outcome,
  escalation = a constant 10%), so the "score" was the same 0.99 for any data.
  A record without them is NOT scored (``None``, "not measured") — A2's
  ``OutcomeRecord`` does not carry them yet.
- the trend compared the OLDEST five samples as "recent" (inverted labels).
- the audit record was a stub uuid; it is now committed to the tenant's core
  chain (``learning.confidence_scored``) and a failed commit raises.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
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
        from core.tenants import validate_tenant_id  # noqa: PLC0415

        validate_tenant_id(tenant_id)
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
            # Phase 2: Compute success_rate and escalation_rate from MEASURED counts
            total = max(1, outcome_record.outcome_count)
            success_count = getattr(outcome_record, "success_count", None)
            escalation_count = getattr(outcome_record, "escalation_count", None)
            if escalation_count is None and (
                hasattr(outcome_record, "failed_count") or hasattr(outcome_record, "timed_out_count")
            ):
                escalation_count = (int(getattr(outcome_record, "failed_count", 0) or 0)
                                    + int(getattr(outcome_record, "timed_out_count", 0) or 0))
            if not isinstance(success_count, int) or not isinstance(escalation_count, int):
                logger.warning("ConfidenceScorer: %s carries no success/escalation counts — "
                               "not measured, not scored", outcome_record.skill_id)
                return None
            if (success_count < 0 or escalation_count < 0
                    or success_count + escalation_count > outcome_record.outcome_count):
                logger.error("ConfidenceScorer: inconsistent counts for %s", outcome_record.skill_id)
                return None

            success_rate = success_count / total
            escalation_rate = escalation_count / total

            # Phase 2: Confidence delta formula with escalation penalty
            confidence_delta = success_rate - (escalation_rate * 0.1)

            # Phase 2: Trend detection (10-sample rolling window)
            self._delta_window.append(confidence_delta)
            trend = self._detect_trend()

            # Emit audit events
            audit_ref = self._emit_confidence_scored(
                source_audit_ref=str(getattr(outcome_record, "audit_ref", "") or ""),
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

        deltas = list(self._delta_window)  # oldest -> newest (deque appends right)
        older_avg = sum(deltas[-10:-5]) / 5
        recent_avg = sum(deltas[-5:]) / 5
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
        source_audit_ref: str = "",
    ) -> str:
        """Commit ``learning.confidence_scored`` to the tenant's core chain (fail-closed)."""
        from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

        return core_audit_event(
            "learning.confidence_scored",
            tenant_id=self.tenant_id,
            details={
                "skill_id": str(skill_id)[:128],
                "outcome_count": int(outcome_count),
                "success_count": int(success_count),
                "escalation_count": int(escalation_count),
                "confidence_delta": round(float(confidence_delta), 6),
                "trend": trend,
                "source_audit_ref": source_audit_ref[:64],
                "tenant_id": self.tenant_id,
            },
        )

    def get_processed_count(self) -> int:
        """Return count of processed outcomes."""
        return self._processed_count

    def get_trend_history(self) -> list:
        """Return current rolling window (for diagnostics)."""
        return list(self._delta_window)
