"""Tests for Fix #9: Feedback Contradiction Consistency Validator

Tests validate that:
1. Consistent feedback (GOOD when loss decreasing) is recognized
2. Contradictory feedback (BAD when loss decreasing) is downweighted
3. Consistency score is computed correctly [0, 1]
4. Neutral feedback doesn't cause contradictions
5. Audit events are logged for all checks
6. Fail-closed behavior when data is unavailable

Test coverage: ≥3 required, 8 implemented
"""

from datetime import datetime
from typing import List, Dict, Any

from core.learning.consistency_validator import (
    FeedbackConsistencyValidator,
    FeedbackSignal,
    TrendType,
    ConsistencyCheckResult,
    FeedbackWeightingSignal,
)


class MockEventStore:
    """Mock EventStore for testing."""

    def __init__(self, losses: List[float]):
        """Initialize with loss history.

        Args:
            losses: List of loss values (chronological)
        """
        self.losses = losses
        self.queries = []

    def query_events(self, tenant_id: str, skill_id: str, limit: int):
        """Query events (returns mock events)."""
        self.queries.append({
            "tenant_id": tenant_id,
            "skill_id": skill_id,
            "limit": limit,
        })

        # Return mock events with timestamps
        events = []
        for i, loss in enumerate(self.losses[-limit:]):
            events.append({
                "timestamp": f"2026-09-07T12:{i:02d}:00Z",
                "payload": {"total_loss": loss},
            })
        return events


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def write_event(self, event: Dict[str, Any]) -> None:
        """Record event."""
        self.events.append(event)


# =============================================================================
# TEST 1: Consistent Feedback (Good feedback, decreasing loss)
# =============================================================================

def test_consistent_feedback_good_with_decreasing_loss():
    """Test 1: GOOD feedback is consistent with decreasing loss.

    Scenario:
      - Loss trend: DECREASING (0.5 → 0.3)
      - User feedback: GOOD
      - Expected: consistent (score >= 0.5)
    """
    # Arrange
    losses = [0.50, 0.48, 0.46, 0.44, 0.42, 0.40, 0.38, 0.36, 0.34, 0.30]
    event_store = MockEventStore(losses)
    audit_backend = MockAuditBackend()

    validator = FeedbackConsistencyValidator(
        audit_backend=audit_backend,
        event_store=event_store,
    )

    # Act
    result = validator.validate(
        feedback_id="fb_001",
        skill_id="os.router",
        task_id="task_001",
        feedback_signal=FeedbackSignal.GOOD,
        tenant_id="_default",
    )

    # Assert
    assert result.is_consistent, "GOOD feedback should be consistent with decreasing loss"
    assert result.consistency_score >= 0.5, "Score should indicate consistency"
    assert result.loss_trend == TrendType.DECREASING, "Should detect decreasing trend"
    assert result.feedback_signal == FeedbackSignal.GOOD
    assert len(audit_backend.events) >= 1, "Should emit audit event"

    print("✅ Test 1: GOOD feedback + decreasing loss = CONSISTENT")


# =============================================================================
# TEST 2: Contradictory Feedback (Good feedback, increasing loss)
# =============================================================================

def test_contradictory_feedback_good_with_increasing_loss():
    """Test 2: GOOD feedback is inconsistent with increasing loss.

    Scenario:
      - Loss trend: INCREASING (0.3 → 0.5)
      - User feedback: GOOD
      - Expected: inconsistent (score < 0.5)
    """
    # Arrange
    losses = [0.30, 0.32, 0.34, 0.36, 0.38, 0.40, 0.42, 0.44, 0.46, 0.50]
    event_store = MockEventStore(losses)
    audit_backend = MockAuditBackend()

    validator = FeedbackConsistencyValidator(
        audit_backend=audit_backend,
        event_store=event_store,
    )

    # Act
    result = validator.validate(
        feedback_id="fb_002",
        skill_id="os.router",
        task_id="task_002",
        feedback_signal=FeedbackSignal.GOOD,
        tenant_id="_default",
    )

    # Assert
    assert not result.is_consistent, "GOOD feedback should be inconsistent with increasing loss"
    assert result.consistency_score < 0.5, "Score should indicate inconsistency"
    assert result.loss_trend == TrendType.INCREASING, "Should detect increasing trend"
    assert result.contradiction_reason is not None, "Should explain contradiction"
    assert len(audit_backend.events) >= 1, "Should emit audit event"

    print("✅ Test 2: GOOD feedback + increasing loss = INCONSISTENT")


# =============================================================================
# TEST 3: Consistency Score Range [0, 1]
# =============================================================================

def test_consistency_score_in_valid_range():
    """Test 3: Consistency score is always in [0, 1].

    Tests multiple scenarios to ensure score is bounded.
    """
    # Arrange
    event_store = MockEventStore([0.5, 0.4, 0.3, 0.2])
    audit_backend = MockAuditBackend()
    validator = FeedbackConsistencyValidator(
        audit_backend=audit_backend,
        event_store=event_store,
    )

    test_cases = [
        (FeedbackSignal.GOOD, "Consistent GOOD"),
        (FeedbackSignal.BAD, "Contradictory BAD"),
        (FeedbackSignal.NEUTRAL, "Neutral feedback"),
    ]

    # Act & Assert
    for feedback_signal, description in test_cases:
        result = validator.validate(
            feedback_id=f"fb_{description.replace(' ', '_')}",
            skill_id="test_skill",
            task_id="test_task",
            feedback_signal=feedback_signal,
            tenant_id="_default",
        )

        assert 0.0 <= result.consistency_score <= 1.0, (
            f"Score out of range for {description}: {result.consistency_score}"
        )

    print("✅ Test 3: Consistency score always in [0, 1]")


# =============================================================================
# TEST 4: Neutral Feedback (doesn't trigger contradictions)
# =============================================================================

def test_neutral_feedback_no_contradiction():
    """Test 4: Neutral feedback doesn't trigger contradictions.

    Scenario:
      - Loss trend: INCREASING (worsening)
      - User feedback: NEUTRAL
      - Expected: consistent (score = 0.5)
    """
    # Arrange
    losses = [0.3, 0.35, 0.4, 0.45, 0.5]
    event_store = MockEventStore(losses)
    audit_backend = MockAuditBackend()

    validator = FeedbackConsistencyValidator(
        audit_backend=audit_backend,
        event_store=event_store,
    )

    # Act
    result = validator.validate(
        feedback_id="fb_004",
        skill_id="os.router",
        task_id="task_004",
        feedback_signal=FeedbackSignal.NEUTRAL,
        tenant_id="_default",
    )

    # Assert
    assert result.is_consistent, "Neutral feedback should be consistent"
    assert result.consistency_score == 0.5, "Neutral should score exactly 0.5"
    assert result.contradiction_reason is None, "No contradiction for neutral"

    print("✅ Test 4: Neutral feedback doesn't trigger contradictions")


# =============================================================================
# TEST 5: Weighting Signal Generation
# =============================================================================

def test_weighting_signal_from_consistency_result():
    """Test 5: Weighting signal correctly scales feedback by consistency.

    Scenario:
      - Consistency score: 0.3
      - Expected weight factor: 0.3 (feedback downweighted)
    """
    # Arrange
    losses = [0.3, 0.35, 0.4, 0.45, 0.5]
    event_store = MockEventStore(losses)
    validator = FeedbackConsistencyValidator(event_store=event_store)

    # Act: Get consistency result
    consistency_result = validator.validate(
        feedback_id="fb_005",
        skill_id="os.router",
        task_id="task_005",
        feedback_signal=FeedbackSignal.GOOD,  # Contradictory
        tenant_id="_default",
    )

    # Convert to weighting signal
    weighting_signal = validator.get_weighting_signal(consistency_result)

    # Assert
    assert weighting_signal.weight_factor == consistency_result.consistency_score
    assert weighting_signal.is_contradictory == (not consistency_result.is_consistent)
    assert 0.0 <= weighting_signal.weight_factor <= 1.0

    print("✅ Test 5: Weighting signal correctly scales by consistency")


# =============================================================================
# TEST 6: Fail-Closed Behavior (no loss history)
# =============================================================================

def test_fail_closed_no_loss_history():
    """Test 6: Fail-closed behavior when no loss history available.

    Scenario:
      - Event store unavailable or returns no history
      - Expected: assume neutral (score=0.5, is_consistent=True)
    """
    # Arrange
    validator = FeedbackConsistencyValidator(
        event_store=MockEventStore([]),  # Empty history
    )

    # Act
    result = validator.validate(
        feedback_id="fb_006",
        skill_id="os.router",
        task_id="task_006",
        feedback_signal=FeedbackSignal.BAD,
        tenant_id="_default",
    )

    # Assert
    assert result.is_consistent, "Should fail-closed (assume consistent)"
    assert result.consistency_score == 0.5, "Should assume neutral"
    assert result.loss_trend == TrendType.UNKNOWN, "Should mark trend as unknown"
    assert result.loss_samples_count == 0, "Should report 0 samples"

    print("✅ Test 6: Fail-closed when no loss history available")


# =============================================================================
# TEST 7: Audit Trail Integration
# =============================================================================

def test_audit_events_emitted():
    """Test 7: Audit events are emitted for all consistency checks.

    Scenario:
      - Check feedback consistency
      - Expected: audit event logged with consistency_checked event type
    """
    # Arrange
    losses = [0.5, 0.4, 0.3, 0.2]
    event_store = MockEventStore(losses)
    audit_backend = MockAuditBackend()

    validator = FeedbackConsistencyValidator(
        audit_backend=audit_backend,
        event_store=event_store,
    )

    # Act
    result = validator.validate(
        feedback_id="fb_007",
        skill_id="os.router",
        task_id="task_007",
        feedback_signal=FeedbackSignal.GOOD,
        tenant_id="_default",
    )

    # Assert
    assert len(audit_backend.events) >= 1, "Should emit at least 1 audit event"

    audit_event = audit_backend.events[0]
    assert audit_event["event_type"] == "feedback_consistency_checked"
    assert audit_event["feedback_id"] == "fb_007"
    assert audit_event["skill_id"] == "os.router"
    assert audit_event["consistency_score"] == result.consistency_score
    assert audit_event["lom"] == "consistency_validator.validate"
    assert audit_event["tenant_id"] == "_default"

    print("✅ Test 7: Audit events emitted and correctly formatted")


# =============================================================================
# TEST 8: Trend Detection Accuracy
# =============================================================================

def test_trend_detection():
    """Test 8: Loss trend detection is accurate.

    Tests that the validator correctly identifies:
      - Increasing trend (loss worsening)
      - Decreasing trend (loss improving)
      - Stable trend (loss flat)
    """
    # Arrange
    validator = FeedbackConsistencyValidator()

    # Test increasing trend
    increasing_losses = [0.1, 0.2, 0.3, 0.4, 0.5]
    trend, delta = validator._detect_trend(increasing_losses)
    assert trend == TrendType.INCREASING, "Should detect increasing trend"
    assert delta > 0, "Delta should be positive for increasing"

    # Test decreasing trend
    decreasing_losses = [0.5, 0.4, 0.3, 0.2, 0.1]
    trend, delta = validator._detect_trend(decreasing_losses)
    assert trend == TrendType.DECREASING, "Should detect decreasing trend"
    assert delta < 0, "Delta should be negative for decreasing"

    # Test stable trend
    stable_losses = [0.3, 0.30, 0.31, 0.30, 0.31]
    trend, delta = validator._detect_trend(stable_losses)
    assert trend == TrendType.STABLE, "Should detect stable trend"
    assert abs(delta) < validator.TREND_THRESHOLD, "Delta should be small for stable"

    print("✅ Test 8: Trend detection accurate for all cases")


# =============================================================================
# RUN ALL TESTS
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING FIX #9: FEEDBACK CONTRADICTION VALIDATOR TESTS")
    print("=" * 70)
    print()

    test_consistent_feedback_good_with_decreasing_loss()
    test_contradictory_feedback_good_with_increasing_loss()
    test_consistency_score_in_valid_range()
    test_neutral_feedback_no_contradiction()
    test_weighting_signal_from_consistency_result()
    test_fail_closed_no_loss_history()
    test_audit_events_emitted()
    test_trend_detection()

    print()
    print("=" * 70)
    print("✅ ALL 8 TESTS PASSED")
    print("=" * 70)
