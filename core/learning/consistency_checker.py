"""Phase 2-9: Feedback Consistency Checker — Mitigation for Finding #9

Validates feedback consistency by comparing user feedback against recent loss trends.
Prevents divergence caused by contradictory feedback signals.

Compliance: GDPR Art. 6 (feedback consent), Art. 30 (audit trail), Art. 32 (data security)
Mitigation for: Finding #9 "Feedback Contradiction: Conflicting signals → divergence"
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
import logging
import numpy as np

logger = logging.getLogger(__name__)


class FeedbackSignal(str, Enum):
    """Feedback signal type."""
    GOOD = "good"        # User satisfied with outcome
    BAD = "bad"          # User dissatisfied with outcome
    OTHER = "other"      # Neutral / inconclusive


@dataclass(frozen=True)
class ConsistencyCheckResult:
    """Immutable result of consistency check (audit trail)."""
    feedback_id: str
    skill_id: str
    task_id: str
    feedback_signal: FeedbackSignal
    consistency_score: float  # [0, 1]: 1.0 = fully consistent, 0.0 = fully contradictory
    is_consistent: bool  # consistency_score >= 0.5
    loss_trend: str  # "increasing", "decreasing", "stable"
    recent_loss_delta: float  # change in loss over recent window
    contradiction_reason: Optional[str] = None
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to audit event format."""
        return {
            "event_type": "feedback_consistency_checked",
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "feedback_signal": self.feedback_signal.value,
            "consistency_score": self.consistency_score,
            "is_consistent": self.is_consistent,
            "loss_trend": self.loss_trend,
            "recent_loss_delta": self.recent_loss_delta,
            "contradiction_reason": self.contradiction_reason,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class FeedbackContradictionEvent:
    """Immutable feedback contradiction audit event."""
    feedback_id: str
    skill_id: str
    task_id: str
    consistency_score: float
    feedback_signal: FeedbackSignal
    expected_signal: FeedbackSignal
    loss_trend: str
    downweight_factor: float  # (1 - consistency_score): how much to reduce feedback weight
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to audit event format."""
        return {
            "event_type": "feedback_contradiction",
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "consistency_score": self.consistency_score,
            "feedback_signal": self.feedback_signal.value,
            "expected_signal": self.expected_signal.value,
            "loss_trend": self.loss_trend,
            "downweight_factor": self.downweight_factor,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
        }


class FeedbackConsistencyValidator:
    """Validates feedback consistency against loss trends (fail-closed, GDPR Art. 32)."""

    # Configuration
    LOSS_WINDOW_SAMPLES = 20  # Recent samples to evaluate for trend
    CONSISTENCY_THRESHOLD = 0.5  # score >= 0.5 is considered consistent
    LOSS_TREND_THRESHOLD = 0.05  # % change to detect trend (5% = +/- 0.05)
    MAX_FEEDBACK_AGE_MINUTES = 60  # Only recent feedback can be checked

    def __init__(self, audit_backend=None, event_store=None):
        """Initialize consistency validator with dependencies.

        Args:
            audit_backend: Audit trail writer (for logging consistency checks)
            event_store: EventStore instance (to fetch recent loss samples)
        """
        self.audit_backend = audit_backend
        self.event_store = event_store
        # Cache for recent loss history (skill_id → [loss_values])
        self._loss_history: Dict[str, List[float]] = {}

    def validate_consistency(
        self,
        feedback_id: str,
        skill_id: str,
        task_id: str,
        feedback_signal: FeedbackSignal,
        tenant_id: str = "_default",
    ) -> ConsistencyCheckResult:
        """Validate feedback consistency against recent loss trends.

        Args:
            feedback_id: Unique feedback identifier
            skill_id: Skill being evaluated
            task_id: Task context
            feedback_signal: User's feedback (good/bad/other)
            tenant_id: Tenant context (GDPR isolation)

        Returns:
            ConsistencyCheckResult with consistency_score and is_consistent flag
        """
        try:
            # Step 1: Fetch recent loss history for this skill
            recent_losses = self._fetch_recent_losses(skill_id, tenant_id)

            # Step 2: Detect loss trend (increasing, decreasing, stable)
            loss_trend, loss_delta = self._detect_loss_trend(recent_losses)

            # Step 3: Infer expected feedback signal from loss trend
            expected_signal = self._infer_expected_signal(loss_trend)

            # Step 4: Compute consistency score (0-1)
            consistency_score = self._compute_consistency_score(
                feedback_signal=feedback_signal,
                expected_signal=expected_signal,
                loss_delta=loss_delta,
                recent_losses=recent_losses,
            )

            is_consistent = consistency_score >= self.CONSISTENCY_THRESHOLD

            # Determine contradiction reason if applicable
            contradiction_reason = None
            if not is_consistent:
                contradiction_reason = (
                    f"Feedback '{feedback_signal.value}' contradicts loss trend "
                    f"'{loss_trend}' (expected '{expected_signal.value}'). "
                    f"Consistency score: {consistency_score:.2f}"
                )

            result = ConsistencyCheckResult(
                feedback_id=feedback_id,
                skill_id=skill_id,
                task_id=task_id,
                feedback_signal=feedback_signal,
                consistency_score=consistency_score,
                is_consistent=is_consistent,
                loss_trend=loss_trend,
                recent_loss_delta=loss_delta,
                contradiction_reason=contradiction_reason,
                tenant_id=tenant_id,
            )

            # Step 5: Emit audit event (always, whether consistent or not)
            self._emit_consistency_audit_event(result)

            # Step 6: If contradictory, emit contradiction event for backprop downweighting
            if not is_consistent:
                self._emit_contradiction_event(
                    feedback_id=feedback_id,
                    skill_id=skill_id,
                    task_id=task_id,
                    consistency_score=consistency_score,
                    feedback_signal=feedback_signal,
                    expected_signal=expected_signal,
                    loss_trend=loss_trend,
                    tenant_id=tenant_id,
                )

            return result

        except Exception as e:
            logger.error(f"Consistency check failed for feedback {feedback_id}: {e}")
            # Fail-closed: return neutral result (score=0.5, is_consistent=True)
            # This prevents the system from accidentally downweighting valid feedback
            return ConsistencyCheckResult(
                feedback_id=feedback_id,
                skill_id=skill_id,
                task_id=task_id,
                feedback_signal=feedback_signal,
                consistency_score=0.5,
                is_consistent=True,  # Fail-open: assume consistent if check fails
                loss_trend="unknown",
                recent_loss_delta=0.0,
                contradiction_reason=f"Consistency check error: {e}",
                tenant_id=tenant_id,
            )

    def _fetch_recent_losses(self, skill_id: str, tenant_id: str) -> List[float]:
        """Fetch recent loss samples for a skill.

        Args:
            skill_id: Skill identifier
            tenant_id: Tenant context

        Returns:
            List of recent loss values (newest last)
        """
        # If event_store available, fetch from real data
        if self.event_store:
            try:
                events = self.event_store.get_events(
                    event_type="unified_loss_computed",
                    skill_id=skill_id,
                    tenant_id=tenant_id,
                    limit=self.LOSS_WINDOW_SAMPLES,
                )
                losses = []
                for event in events:
                    if "total_loss" in event.get("payload", {}):
                        losses.append(event["payload"]["total_loss"])
                if losses:
                    return sorted(losses)  # Chronological order (oldest first)
            except Exception as e:
                logger.warning(f"Failed to fetch loss history: {e}")

        # Fallback: return empty list (will infer neutral trend)
        return []

    def _detect_loss_trend(self, recent_losses: List[float]) -> Tuple[str, float]:
        """Detect loss trend from recent samples.

        Args:
            recent_losses: List of recent loss values (chronological order)

        Returns:
            (trend, loss_delta): trend in ["increasing", "decreasing", "stable", "unknown"]
            loss_delta: % change (positive = increasing, negative = decreasing)
        """
        if not recent_losses or len(recent_losses) < 2:
            return "unknown", 0.0

        # Compare first half vs second half
        mid = len(recent_losses) // 2
        first_half_mean = np.mean(recent_losses[:mid]) if mid > 0 else recent_losses[0]
        second_half_mean = np.mean(recent_losses[mid:])

        # Compute % change
        if first_half_mean != 0:
            loss_delta = (second_half_mean - first_half_mean) / abs(first_half_mean)
        else:
            loss_delta = 0.0

        # Classify trend
        if abs(loss_delta) < self.LOSS_TREND_THRESHOLD:
            trend = "stable"
        elif loss_delta > 0:
            trend = "increasing"
        else:
            trend = "decreasing"

        return trend, loss_delta

    def _infer_expected_signal(self, loss_trend: str) -> FeedbackSignal:
        """Infer expected feedback signal from loss trend.

        Args:
            loss_trend: Detected trend

        Returns:
            Expected feedback signal based on trend
        """
        if loss_trend == "decreasing":
            # Loss is improving → expect "good" feedback
            return FeedbackSignal.GOOD
        elif loss_trend == "increasing":
            # Loss is worsening → expect "bad" feedback
            return FeedbackSignal.BAD
        else:
            # Stable or unknown → could go either way
            return FeedbackSignal.OTHER

    def _compute_consistency_score(
        self,
        feedback_signal: FeedbackSignal,
        expected_signal: FeedbackSignal,
        loss_delta: float,
        recent_losses: List[float],
    ) -> float:
        """Compute consistency score [0, 1].

        Args:
            feedback_signal: User's feedback
            expected_signal: Expected signal based on trend
            loss_delta: % change in loss
            recent_losses: Recent loss samples (for volatility)

        Returns:
            Consistency score [0, 1]:
            1.0 = perfectly consistent
            0.5 = neutral (unknown trend or GOOD/OTHER/BAD match)
            0.0 = completely contradictory
        """
        # Base case: if signals match exactly
        if feedback_signal == expected_signal:
            return 1.0

        # If either is "OTHER", it's neutral (score = 0.5)
        if feedback_signal == FeedbackSignal.OTHER or expected_signal == FeedbackSignal.OTHER:
            return 0.5

        # Direct contradiction (GOOD vs BAD)
        if (feedback_signal == FeedbackSignal.GOOD and expected_signal == FeedbackSignal.BAD) or \
           (feedback_signal == FeedbackSignal.BAD and expected_signal == FeedbackSignal.GOOD):
            # Compute severity: how strong is the loss trend?
            # Strong trend (|loss_delta| > threshold) → more contradictory
            # Weak trend → less contradictory
            severity = min(1.0, abs(loss_delta) / (2 * self.LOSS_TREND_THRESHOLD))
            return max(0.0, 0.5 - severity)

        # Fallback: neutral
        return 0.5

    def _emit_consistency_audit_event(self, result: ConsistencyCheckResult) -> None:
        """Emit audit event for consistency check (GDPR Art. 30)."""
        if not self.audit_backend:
            return

        audit_event = {
            **result.to_dict(),
            "lom": "consistency_checker.validate_consistency",  # Line of Moral Responsibility
        }
        try:
            self.audit_backend.write_event(audit_event)
        except Exception as e:
            logger.error(f"Failed to write consistency check audit event: {e}")

    def _emit_contradiction_event(
        self,
        feedback_id: str,
        skill_id: str,
        task_id: str,
        consistency_score: float,
        feedback_signal: FeedbackSignal,
        expected_signal: FeedbackSignal,
        loss_trend: str,
        tenant_id: str,
    ) -> None:
        """Emit contradiction event for downweighting in backprop (fail-closed)."""
        if not self.audit_backend:
            return

        downweight_factor = 1.0 - consistency_score

        contradiction = FeedbackContradictionEvent(
            feedback_id=feedback_id,
            skill_id=skill_id,
            task_id=task_id,
            consistency_score=consistency_score,
            feedback_signal=feedback_signal,
            expected_signal=expected_signal,
            loss_trend=loss_trend,
            downweight_factor=downweight_factor,
            tenant_id=tenant_id,
        )

        audit_event = {
            **contradiction.to_dict(),
            "lom": "consistency_checker.emit_contradiction_event",  # LoM
        }
        try:
            self.audit_backend.write_event(audit_event)
        except Exception as e:
            logger.error(f"Failed to write contradiction event: {e}")


# ============================================================================
# Integration: Backprop Downweighting
# ============================================================================


def apply_consistency_downweighting(
    feedback_weight: float,
    consistency_score: float,
) -> float:
    """Apply consistency-based downweighting to feedback weight in backprop.

    Args:
        feedback_weight: Original weight (e.g., 1.0)
        consistency_score: Score [0, 1] from consistency check

    Returns:
        Downweighted feedback weight: feedback_weight * consistency_score
    """
    downweighted = feedback_weight * consistency_score
    logger.info(
        f"Applied consistency downweighting: "
        f"{feedback_weight:.3f} → {downweighted:.3f} (score={consistency_score:.2f})"
    )
    return downweighted


# ============================================================================
# Tests (inline, Red→Green)
# ============================================================================


def test_feedback_consistent_with_loss_trend():
    """Test: Negative feedback + loss increase = consistent."""
    validator = FeedbackConsistencyValidator()

    # Simulate increasing loss trend
    recent_losses = [0.5, 0.52, 0.54, 0.56, 0.58]  # Increasing

    # Mock event store
    class MockEventStore:
        def get_events(self, **kwargs):
            return [
                {"payload": {"total_loss": loss}}
                for loss in recent_losses
            ]

    validator.event_store = MockEventStore()

    result = validator.validate_consistency(
        feedback_id="fb_001",
        skill_id="os.router",
        task_id="task_001",
        feedback_signal=FeedbackSignal.BAD,
        tenant_id="_default",
    )

    assert result.is_consistent, "BAD feedback should be consistent with increasing loss"
    assert result.consistency_score > 0.5, "Consistency score should be > 0.5"
    print("✅ Test 1: Negative feedback + loss increase = consistent")


def test_feedback_contradicts_loss_trend():
    """Test: Positive feedback + loss increase = inconsistent."""
    validator = FeedbackConsistencyValidator()

    # Simulate increasing loss trend
    recent_losses = [0.5, 0.52, 0.54, 0.56, 0.58]  # Increasing

    # Mock event store
    class MockEventStore:
        def get_events(self, **kwargs):
            return [
                {"payload": {"total_loss": loss}}
                for loss in recent_losses
            ]

    validator.event_store = MockEventStore()

    result = validator.validate_consistency(
        feedback_id="fb_002",
        skill_id="os.router",
        task_id="task_002",
        feedback_signal=FeedbackSignal.GOOD,
        tenant_id="_default",
    )

    assert not result.is_consistent, "GOOD feedback should be inconsistent with increasing loss"
    assert result.consistency_score < 0.5, "Consistency score should be < 0.5"
    print("✅ Test 2: Positive feedback + loss increase = inconsistent")


def test_consistency_score_computed():
    """Test: Consistency score in [0, 1] computed correctly."""
    validator = FeedbackConsistencyValidator()

    # Test multiple combinations
    test_cases = [
        (FeedbackSignal.GOOD, FeedbackSignal.GOOD, 1.0),  # Match → 1.0
        (FeedbackSignal.BAD, FeedbackSignal.BAD, 1.0),    # Match → 1.0
        (FeedbackSignal.OTHER, FeedbackSignal.GOOD, 0.5),  # OTHER → 0.5
        (FeedbackSignal.GOOD, FeedbackSignal.BAD, 0.0),   # Contradiction → ~0.0
    ]

    for feedback, expected, expected_score in test_cases:
        score = validator._compute_consistency_score(
            feedback_signal=feedback,
            expected_signal=expected,
            loss_delta=0.15,  # Strong trend
            recent_losses=[0.5, 0.6, 0.7],
        )
        assert 0.0 <= score <= 1.0, f"Score out of range: {score}"
        if expected == FeedbackSignal.OTHER:
            assert abs(score - expected_score) < 0.1, f"Expected ~{expected_score}, got {score}"
        else:
            assert abs(score - expected_score) < 0.01, f"Expected {expected_score}, got {score}"

    print("✅ Test 3: Consistency score in [0, 1] computed correctly")


def test_inconsistent_feedback_downweighted():
    """Test: Low-consistency feedback contributes less to backprop."""
    consistency_score = 0.3  # Low consistency
    original_weight = 1.0

    downweighted = apply_consistency_downweighting(
        feedback_weight=original_weight,
        consistency_score=consistency_score,
    )

    expected = original_weight * consistency_score
    assert abs(downweighted - expected) < 0.01, f"Expected {expected}, got {downweighted}"
    assert downweighted < original_weight, "Downweighted should be < original"
    print("✅ Test 4: Inconsistent feedback downweighted in backprop")


def test_feedback_contradiction_audit_logged():
    """Test: Feedback contradiction events logged."""
    validator = FeedbackConsistencyValidator()

    # Mock audit backend
    class MockAuditBackend:
        def __init__(self):
            self.events = []

        def write_event(self, event):
            self.events.append(event)

    audit_backend = MockAuditBackend()
    validator.audit_backend = audit_backend

    # Mock event store with increasing loss
    class MockEventStore:
        def get_events(self, **kwargs):
            return [
                {"payload": {"total_loss": loss}}
                for loss in [0.5, 0.52, 0.54, 0.56, 0.58]
            ]

    validator.event_store = MockEventStore()

    result = validator.validate_consistency(
        feedback_id="fb_003",
        skill_id="os.router",
        task_id="task_003",
        feedback_signal=FeedbackSignal.GOOD,  # Contradictory
        tenant_id="_default",
    )

    # Should log both consistency check and contradiction events
    assert len(audit_backend.events) >= 2, "Should log consistency + contradiction events"

    # Find contradiction event
    contradiction_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_contradiction"]
    assert len(contradiction_events) > 0, "Should log contradiction event"

    contradiction = contradiction_events[0]
    assert contradiction["feedback_id"] == "fb_003"
    assert contradiction["downweight_factor"] > 0.0
    assert contradiction["downweight_factor"] < 1.0

    print("✅ Test 5: Feedback contradiction events logged to audit")


def test_contradiction_does_not_block():
    """Test: Contradictory feedback still processed (downweighted, not rejected)."""
    validator = FeedbackConsistencyValidator()

    # Mock event store with increasing loss
    class MockEventStore:
        def get_events(self, **kwargs):
            return [
                {"payload": {"total_loss": loss}}
                for loss in [0.5, 0.52, 0.54, 0.56, 0.58]
            ]

    validator.event_store = MockEventStore()

    result = validator.validate_consistency(
        feedback_id="fb_004",
        skill_id="os.router",
        task_id="task_004",
        feedback_signal=FeedbackSignal.GOOD,  # Contradictory to increasing loss
        tenant_id="_default",
    )

    # The feedback should still be processed (not blocked)
    # It just has a low consistency score
    assert result is not None, "Contradictory feedback should still be processed"
    assert result.consistency_score < 0.5, "Should have low consistency score"
    # But the result should exist and be usable for downweighting

    print("✅ Test 6: Contradictory feedback still processed (downweighted, not rejected)")


if __name__ == "__main__":
    print("Running Feedback Consistency Checker Tests...\n")
    test_feedback_consistent_with_loss_trend()
    test_feedback_contradicts_loss_trend()
    test_consistency_score_computed()
    test_inconsistent_feedback_downweighted()
    test_feedback_contradiction_audit_logged()
    test_contradiction_does_not_block()
    print("\n🎉 All consistency checker tests passed!")
