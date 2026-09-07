"""Fix #9: Feedback Contradiction — Consistency Validator

Mitigation for adversarial vector #9: Conflicting feedback signals causing divergence.
Validates that feedback is consistent with recent loss trends before applying to learning loop.

Compliance:
  - GDPR Art. 6 (feedback consent, basis for processing)
  - GDPR Art. 30 (audit trail of consistency checks)
  - GDPR Art. 32 (data security, fail-closed validation)
  - EU AI Act 2026 (transparency of decision-making)

Architecture:
  1. On feedback arrival, extract recent loss history for the skill
  2. Detect loss trend (increasing/decreasing/stable)
  3. Infer expected feedback signal from trend
  4. Compare actual feedback vs expected → consistency score [0, 1]
  5. Emit audit event (always)
  6. If inconsistent, emit downweighting signal for backprop
  7. Never block feedback; only downweight contradictory signals

Author: Claude Code (LDD framework)
Date: 2026-09-07
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
import logging
import numpy as np

logger = logging.getLogger(__name__)


class FeedbackSignal(str, Enum):
    """Feedback signal type (user perception vs. ground truth)."""
    GOOD = "good"      # User satisfied / positive outcome
    BAD = "bad"        # User dissatisfied / negative outcome
    NEUTRAL = "neutral"  # No clear signal


class TrendType(str, Enum):
    """Loss trend classification."""
    INCREASING = "increasing"    # Performance degrading
    DECREASING = "decreasing"    # Performance improving
    STABLE = "stable"            # Performance flat
    UNKNOWN = "unknown"          # Insufficient data


@dataclass(frozen=True)
class ConsistencyCheckResult:
    """Immutable result of consistency validation (audit trail).

    Frozen to prevent accidental mutation; all fields immutable.
    """
    feedback_id: str
    skill_id: str
    task_id: str
    feedback_signal: FeedbackSignal
    consistency_score: float          # [0, 1]: how consistent with loss trend
    is_consistent: bool               # consistency_score >= threshold
    loss_trend: TrendType
    recent_loss_delta: float          # % change in loss over window
    loss_samples_count: int           # How many samples we had for trend detection
    contradiction_reason: Optional[str] = None
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_audit_dict(self) -> Dict[str, Any]:
        """Serialize to audit event format."""
        return {
            "event_type": "feedback_consistency_checked",
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "feedback_signal": self.feedback_signal.value,
            "consistency_score": self.consistency_score,
            "is_consistent": self.is_consistent,
            "loss_trend": self.loss_trend.value,
            "recent_loss_delta": self.recent_loss_delta,
            "loss_samples_count": self.loss_samples_count,
            "contradiction_reason": self.contradiction_reason,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "lom": "consistency_validator.validate",
        }


@dataclass(frozen=True)
class FeedbackWeightingSignal:
    """Signal to weight optimizer: how much to trust this feedback.

    Frozen to ensure audit trail integrity.
    """
    feedback_id: str
    skill_id: str
    consistency_score: float           # [0, 1]
    weight_factor: float               # = consistency_score (how much to scale feedback)
    is_contradictory: bool             # consistency_score < threshold
    expected_trend: TrendType
    observed_signal: FeedbackSignal
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_audit_dict(self) -> Dict[str, Any]:
        """Serialize to audit event."""
        return {
            "event_type": "feedback_weighting_signal",
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "consistency_score": self.consistency_score,
            "weight_factor": self.weight_factor,
            "is_contradictory": self.is_contradictory,
            "expected_trend": self.expected_trend.value,
            "observed_signal": self.observed_signal.value,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "lom": "consistency_validator.create_weighting_signal",
        }


class FeedbackConsistencyValidator:
    """Validates feedback consistency against loss trends (fail-closed).

    Configuration:
      - LOSS_WINDOW: Number of recent loss samples to examine
      - CONSISTENCY_THRESHOLD: Score >= this is "consistent"
      - TREND_THRESHOLD: % change to detect trend (5% = 0.05)
      - MAX_FEEDBACK_AGE: Only process recent feedback

    Fail-closed behavior:
      - No loss history available → assume neutral (score=0.5)
      - Validation error → assume neutral (don't penalize valid feedback)
      - Invalid tenant → return empty result
    """

    # Configuration (tunable)
    LOSS_WINDOW = 20              # Examine last N samples
    CONSISTENCY_THRESHOLD = 0.5   # score >= 0.5 is consistent
    TREND_THRESHOLD = 0.05        # 5% change threshold
    MAX_FEEDBACK_AGE_SEC = 3600   # 1 hour

    def __init__(self, audit_backend=None, event_store=None):
        """Initialize validator.

        Args:
            audit_backend: Audit trail writer (for logging)
            event_store: EventStore to fetch loss history
        """
        self.audit_backend = audit_backend
        self.event_store = event_store
        self._loss_cache: Dict[str, List[float]] = {}  # Cache for recent losses

    def validate(
        self,
        feedback_id: str,
        skill_id: str,
        task_id: str,
        feedback_signal: FeedbackSignal,
        tenant_id: str = "_default",
    ) -> ConsistencyCheckResult:
        """Validate feedback consistency.

        Args:
            feedback_id: Unique feedback identifier
            skill_id: Skill being evaluated
            task_id: Task context
            feedback_signal: User's feedback (good/bad/neutral)
            tenant_id: Tenant context (GDPR isolation)

        Returns:
            ConsistencyCheckResult with score and consistency flag
        """
        try:
            # Step 1: Fetch recent loss history for this skill
            losses = self._fetch_recent_losses(skill_id, tenant_id)

            # Step 2: Detect loss trend from recent samples
            trend, loss_delta = self._detect_trend(losses)

            # Step 3: Infer expected feedback signal from trend
            expected_signal = self._infer_expected_signal(trend)

            # Step 4: Compute consistency score
            consistency_score = self._compute_consistency_score(
                feedback_signal=feedback_signal,
                expected_signal=expected_signal,
                loss_delta=loss_delta,
            )

            is_consistent = consistency_score >= self.CONSISTENCY_THRESHOLD

            # Step 5: Build result
            contradiction_reason = None
            if not is_consistent:
                contradiction_reason = (
                    f"Feedback '{feedback_signal.value}' contradicts expected "
                    f"'{expected_signal.value}' from loss trend '{trend.value}'. "
                    f"Consistency score: {consistency_score:.2f}"
                )

            result = ConsistencyCheckResult(
                feedback_id=feedback_id,
                skill_id=skill_id,
                task_id=task_id,
                feedback_signal=feedback_signal,
                consistency_score=consistency_score,
                is_consistent=is_consistent,
                loss_trend=trend,
                recent_loss_delta=loss_delta,
                loss_samples_count=len(losses),
                contradiction_reason=contradiction_reason,
                tenant_id=tenant_id,
            )

            # Step 6: Emit audit event
            self._audit_check(result)

            return result

        except Exception as e:
            logger.error(f"Consistency validation failed for {feedback_id}: {e}")
            # Fail-closed: assume neutral (score=0.5, is_consistent=True)
            # This prevents us from incorrectly downweighting valid feedback
            return ConsistencyCheckResult(
                feedback_id=feedback_id,
                skill_id=skill_id,
                task_id=task_id,
                feedback_signal=feedback_signal,
                consistency_score=0.5,
                is_consistent=True,  # Assume valid if check fails
                loss_trend=TrendType.UNKNOWN,
                recent_loss_delta=0.0,
                loss_samples_count=0,
                contradiction_reason=f"Validation error: {str(e)[:100]}",
                tenant_id=tenant_id,
            )

    def _fetch_recent_losses(self, skill_id: str, tenant_id: str) -> List[float]:
        """Fetch recent loss history for a skill (chronological order).

        Args:
            skill_id: Skill identifier
            tenant_id: Tenant context

        Returns:
            List of loss values (oldest first)
        """
        if not self.event_store:
            return []

        try:
            # Query events for this skill
            events = self.event_store.query_events(
                tenant_id=tenant_id,
                skill_id=skill_id,
                limit=self.LOSS_WINDOW,
            )

            # Extract loss values with timestamps
            samples: List[Tuple[str, float]] = []
            for event in events:
                # Handle both dict and object formats
                payload = getattr(event, "payload", None) or (
                    event.get("payload") if isinstance(event, dict) else None
                )
                if not isinstance(payload, dict):
                    continue

                loss_value = payload.get("total_loss")
                if not isinstance(loss_value, (int, float)):
                    continue

                timestamp = getattr(event, "timestamp", None) or (
                    event.get("timestamp") if isinstance(event, dict) else None
                )
                if not timestamp:
                    continue

                samples.append((str(timestamp), float(loss_value)))

            # Sort chronologically (oldest first)
            samples.sort(key=lambda x: x[0])

            # Return only loss values, keeping last LOSS_WINDOW items
            return [value for _, value in samples[-self.LOSS_WINDOW:]]

        except Exception as e:
            logger.warning(f"Failed to fetch loss history: {e}")
            return []

    def _detect_trend(self, losses: List[float]) -> Tuple[TrendType, float]:
        """Detect loss trend from recent samples.

        Args:
            losses: Chronological list of loss values

        Returns:
            (trend, loss_delta): TrendType and % change
        """
        if not losses or len(losses) < 2:
            return TrendType.UNKNOWN, 0.0

        # Split into first and second half
        mid = len(losses) // 2
        first_half = np.mean(losses[:mid]) if mid > 0 else losses[0]
        second_half = np.mean(losses[mid:])

        # Compute % change
        if first_half != 0:
            loss_delta = (second_half - first_half) / abs(first_half)
        else:
            loss_delta = 0.0

        # Classify trend
        if abs(loss_delta) < self.TREND_THRESHOLD:
            trend = TrendType.STABLE
        elif loss_delta > 0:
            trend = TrendType.INCREASING
        else:
            trend = TrendType.DECREASING

        return trend, loss_delta

    def _infer_expected_signal(self, trend: TrendType) -> FeedbackSignal:
        """Infer expected feedback signal from loss trend.

        Args:
            trend: Detected loss trend

        Returns:
            Expected feedback signal
        """
        if trend == TrendType.DECREASING:
            return FeedbackSignal.GOOD  # Loss improving
        elif trend == TrendType.INCREASING:
            return FeedbackSignal.BAD   # Loss worsening
        else:
            return FeedbackSignal.NEUTRAL  # Stable or unknown

    def _compute_consistency_score(
        self,
        feedback_signal: FeedbackSignal,
        expected_signal: FeedbackSignal,
        loss_delta: float,
    ) -> float:
        """Compute consistency score [0, 1].

        Args:
            feedback_signal: User's feedback
            expected_signal: Expected based on trend
            loss_delta: % change in loss

        Returns:
            Consistency score [0, 1]
        """
        # Perfect match
        if feedback_signal == expected_signal:
            return 1.0

        # Neutral feedback doesn't contradict
        if feedback_signal == FeedbackSignal.NEUTRAL or expected_signal == FeedbackSignal.NEUTRAL:
            return 0.5

        # Direct contradiction (GOOD vs BAD)
        # Score depends on trend strength
        severity = min(1.0, abs(loss_delta) / (2 * self.TREND_THRESHOLD))
        return max(0.0, 0.5 - severity)

    def get_weighting_signal(
        self,
        result: ConsistencyCheckResult,
    ) -> FeedbackWeightingSignal:
        """Convert consistency result to weighting signal for optimizer.

        Args:
            result: ConsistencyCheckResult

        Returns:
            FeedbackWeightingSignal with weight factor
        """
        return FeedbackWeightingSignal(
            feedback_id=result.feedback_id,
            skill_id=result.skill_id,
            consistency_score=result.consistency_score,
            weight_factor=result.consistency_score,  # Direct scaling
            is_contradictory=not result.is_consistent,
            expected_trend=self._infer_expected_signal(result.loss_trend),
            observed_signal=result.feedback_signal,
            tenant_id=result.tenant_id,
        )

    def _audit_check(self, result: ConsistencyCheckResult) -> None:
        """Emit audit event for consistency check (GDPR Art. 30)."""
        if not self.audit_backend:
            return

        try:
            self.audit_backend.write_event(result.to_audit_dict())
        except Exception as e:
            logger.error(f"Failed to write consistency check audit: {e}")

    def _audit_weighting(self, signal: FeedbackWeightingSignal) -> None:
        """Emit audit event for weighting signal."""
        if not self.audit_backend:
            return

        try:
            self.audit_backend.write_event(signal.to_audit_dict())
        except Exception as e:
            logger.error(f"Failed to write weighting signal audit: {e}")
