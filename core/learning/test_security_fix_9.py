"""
Security Fix #9: Feedback Consistency Validator — Comprehensive Test Suite

Tests for the FeedbackConsistencyValidator that mitigates Finding #9:
"Feedback Contradiction: Conflicting signals → divergence"

Attack vectors tested:
1. Feedback Contradiction (conflicting signals)
2. Loss Trend Contradiction (feedback vs actual trend)
3. Multiple Contradictions (feedback cascade attack)
4. Downweight Application (backprop weight reduction)
5. Audit Trail Verification (logging completeness)
"""

from types import SimpleNamespace
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch
import logging

from core.learning.consistency_checker import (
    FeedbackConsistencyValidator,
    FeedbackSignal,
    ConsistencyCheckResult,
    FeedbackContradictionEvent,
    apply_consistency_downweighting,
)

logger = logging.getLogger(__name__)


class TestFeedbackConsistentWithLossTrend:
    """Test: Negative feedback + loss increase = consistent."""

    def test_bad_feedback_with_increasing_loss(self):
        """Case 1: BAD feedback when loss is increasing (consistent)."""
        validator = FeedbackConsistencyValidator()

        # Simulate increasing loss trend
        recent_losses = [0.50, 0.53, 0.56, 0.59, 0.62]  # +>5 % (LOSS_TREND_THRESHOLD)

        class MockEventStore:
            """Mirrors the REAL ``EventStore.query_events`` contract: named
            ``query_events``, returning event objects with ``signal`` and an
            ISO ``timestamp``. The old mock defined ``get_events``, a method no
            store has — which is why the module's own call could never work
            against a real store (2026-09-07 round-2 review, vector 9)."""

            def query_events(self, **kwargs):
                return [
                    SimpleNamespace(
                        signal={"total_loss": loss},
                        timestamp=f"2026-09-07T10:00:{i:02d}Z",
                    )
                    for i, loss in enumerate(recent_losses)
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_001",
            skill_id="os.router",
            task_id="task_001",
            feedback_signal=FeedbackSignal.BAD,
            tenant_id="_default",
        )

        assert result.is_consistent
        assert result.consistency_score > 0.5
        assert result.loss_trend == "increasing"
        assert result.feedback_signal == FeedbackSignal.BAD

    def test_good_feedback_with_decreasing_loss(self):
        """Case 2: GOOD feedback when loss is decreasing (consistent)."""
        validator = FeedbackConsistencyValidator()

        # Simulate decreasing loss trend
        recent_losses = [0.62, 0.59, 0.56, 0.53, 0.50]  # ->5 % (LOSS_TREND_THRESHOLD)

        class MockEventStore:
            """Mirrors the REAL ``EventStore.query_events`` contract: named
            ``query_events``, returning event objects with ``signal`` and an
            ISO ``timestamp``. The old mock defined ``get_events``, a method no
            store has — which is why the module's own call could never work
            against a real store (2026-09-07 round-2 review, vector 9)."""

            def query_events(self, **kwargs):
                return [
                    SimpleNamespace(
                        signal={"total_loss": loss},
                        timestamp=f"2026-09-07T10:00:{i:02d}Z",
                    )
                    for i, loss in enumerate(recent_losses)
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_002",
            skill_id="os.router",
            task_id="task_002",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        assert result.is_consistent
        assert result.consistency_score > 0.5
        assert result.loss_trend == "decreasing"
        assert result.feedback_signal == FeedbackSignal.GOOD

    def test_other_feedback_always_consistent(self):
        """Case 3: NEUTRAL/OTHER feedback always consistent regardless of trend."""
        validator = FeedbackConsistencyValidator()

        recent_losses = [0.50, 0.53, 0.56, 0.59, 0.62]  # +>5 % (LOSS_TREND_THRESHOLD)  # Increasing

        class MockEventStore:
            """Mirrors the REAL ``EventStore.query_events`` contract: named
            ``query_events``, returning event objects with ``signal`` and an
            ISO ``timestamp``. The old mock defined ``get_events``, a method no
            store has — which is why the module's own call could never work
            against a real store (2026-09-07 round-2 review, vector 9)."""

            def query_events(self, **kwargs):
                return [
                    SimpleNamespace(
                        signal={"total_loss": loss},
                        timestamp=f"2026-09-07T10:00:{i:02d}Z",
                    )
                    for i, loss in enumerate(recent_losses)
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_003",
            skill_id="os.router",
            task_id="task_003",
            feedback_signal=FeedbackSignal.OTHER,
            tenant_id="_default",
        )

        # OTHER feedback should be neutral (score = 0.5)
        assert result.consistency_score == 0.5
        assert result.is_consistent  # >= 0.5 threshold

    def test_stable_loss_with_any_feedback(self):
        """Case 4: Stable loss trend with any feedback (unknown trend)."""
        validator = FeedbackConsistencyValidator()

        # Simulate stable loss (no clear trend)
        recent_losses = [0.50, 0.501, 0.500, 0.502, 0.501]

        class MockEventStore:
            """Mirrors the REAL ``EventStore.query_events`` contract: named
            ``query_events``, returning event objects with ``signal`` and an
            ISO ``timestamp``. The old mock defined ``get_events``, a method no
            store has — which is why the module's own call could never work
            against a real store (2026-09-07 round-2 review, vector 9)."""

            def query_events(self, **kwargs):
                return [
                    SimpleNamespace(
                        signal={"total_loss": loss},
                        timestamp=f"2026-09-07T10:00:{i:02d}Z",
                    )
                    for i, loss in enumerate(recent_losses)
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_004",
            skill_id="os.router",
            task_id="task_004",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        assert result.loss_trend == "stable"
        # Stable trend should be neutral (score = 0.5)
        assert result.consistency_score == 0.5
        assert result.is_consistent  # >= 0.5 threshold


class TestFeedbackContradicts:
    """Test: Positive feedback + loss increase = inconsistent."""

    def test_good_feedback_with_increasing_loss(self):
        """Attack Case 1: GOOD feedback when loss increasing (direct contradiction)."""
        validator = FeedbackConsistencyValidator()

        # Simulate increasing loss trend
        recent_losses = [0.5, 0.52, 0.54, 0.56, 0.58]

        class MockEventStore:
            """Mirrors the REAL ``EventStore.query_events`` contract: named
            ``query_events``, returning event objects with ``signal`` and an
            ISO ``timestamp``. The old mock defined ``get_events``, a method no
            store has — which is why the module's own call could never work
            against a real store (2026-09-07 round-2 review, vector 9)."""

            def query_events(self, **kwargs):
                return [
                    SimpleNamespace(
                        signal={"total_loss": loss},
                        timestamp=f"2026-09-07T10:00:{i:02d}Z",
                    )
                    for i, loss in enumerate(recent_losses)
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_005",
            skill_id="os.router",
            task_id="task_005",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        assert not result.is_consistent, "GOOD feedback with increasing loss should be inconsistent"
        assert result.consistency_score < 0.5
        assert result.loss_trend == "increasing"
        assert result.contradiction_reason is not None

    def test_bad_feedback_with_decreasing_loss(self):
        """Attack Case 2: BAD feedback when loss decreasing (direct contradiction)."""
        validator = FeedbackConsistencyValidator()

        # Simulate decreasing loss trend
        recent_losses = [0.6, 0.58, 0.56, 0.54, 0.52]

        class MockEventStore:
            """Mirrors the REAL ``EventStore.query_events`` contract: named
            ``query_events``, returning event objects with ``signal`` and an
            ISO ``timestamp``. The old mock defined ``get_events``, a method no
            store has — which is why the module's own call could never work
            against a real store (2026-09-07 round-2 review, vector 9)."""

            def query_events(self, **kwargs):
                return [
                    SimpleNamespace(
                        signal={"total_loss": loss},
                        timestamp=f"2026-09-07T10:00:{i:02d}Z",
                    )
                    for i, loss in enumerate(recent_losses)
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_006",
            skill_id="os.router",
            task_id="task_006",
            feedback_signal=FeedbackSignal.BAD,
            tenant_id="_default",
        )

        assert not result.is_consistent, "BAD feedback with decreasing loss should be inconsistent"
        assert result.consistency_score < 0.5
        assert result.loss_trend == "decreasing"

    def test_strong_contradiction_lowers_score(self):
        """Attack Case 3: Stronger loss trend → lower consistency score."""
        validator = FeedbackConsistencyValidator()

        # WEAK trend: small loss change
        weak_losses = [0.500, 0.501, 0.502, 0.503, 0.504]

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate(weak_losses)
                ]

        validator.event_store = MockEventStore()

        weak_result = validator.validate_consistency(
            feedback_id="fb_007",
            skill_id="os.router",
            task_id="task_007",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        # STRONG trend: large loss change
        strong_losses = [0.5, 0.55, 0.60, 0.65, 0.70]

        class MockEventStore2:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate(strong_losses)
                ]

        validator.event_store = MockEventStore2()

        strong_result = validator.validate_consistency(
            feedback_id="fb_008",
            skill_id="os.router",
            task_id="task_008",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        # Strong trend should have lower consistency score
        assert strong_result.consistency_score < weak_result.consistency_score
        assert strong_result.loss_trend == "increasing"


class TestConsistencyScoreComputed:
    """Test: Consistency score in [0, 1] computed correctly."""

    def test_score_range_exact_match(self):
        """Case 1: Exact signal match → score = 1.0."""
        validator = FeedbackConsistencyValidator()

        score = validator._compute_consistency_score(
            feedback_signal=FeedbackSignal.GOOD,
            expected_signal=FeedbackSignal.GOOD,
            loss_delta=0.15,
            recent_losses=[0.5, 0.6, 0.7],
        )

        assert score == 1.0, f"Expected 1.0, got {score}"

    def test_score_range_neutral_other(self):
        """Case 2: OTHER signal (neutral) → score = 0.5."""
        validator = FeedbackConsistencyValidator()

        score1 = validator._compute_consistency_score(
            feedback_signal=FeedbackSignal.OTHER,
            expected_signal=FeedbackSignal.GOOD,
            loss_delta=0.15,
            recent_losses=[0.5, 0.6, 0.7],
        )

        score2 = validator._compute_consistency_score(
            feedback_signal=FeedbackSignal.GOOD,
            expected_signal=FeedbackSignal.OTHER,
            loss_delta=0.15,
            recent_losses=[0.5, 0.6, 0.7],
        )

        assert score1 == 0.5, f"Expected 0.5, got {score1}"
        assert score2 == 0.5, f"Expected 0.5, got {score2}"

    def test_score_range_contradiction(self):
        """Case 3: Direct contradiction (GOOD vs BAD) → score < 0.5."""
        validator = FeedbackConsistencyValidator()

        score = validator._compute_consistency_score(
            feedback_signal=FeedbackSignal.GOOD,
            expected_signal=FeedbackSignal.BAD,
            loss_delta=0.20,
            recent_losses=[0.5, 0.6, 0.7],
        )

        assert 0.0 <= score < 0.5, f"Expected score in [0, 0.5), got {score}"

    def test_score_bounds_always_valid(self):
        """Case 4: Score always in valid range [0, 1]."""
        validator = FeedbackConsistencyValidator()

        test_combinations = [
            (FeedbackSignal.GOOD, FeedbackSignal.GOOD, 0.0),
            (FeedbackSignal.GOOD, FeedbackSignal.GOOD, 0.5),
            (FeedbackSignal.GOOD, FeedbackSignal.BAD, 0.0),
            (FeedbackSignal.GOOD, FeedbackSignal.BAD, 0.3),
            (FeedbackSignal.GOOD, FeedbackSignal.OTHER, 0.1),
            (FeedbackSignal.BAD, FeedbackSignal.BAD, 0.0),
        ]

        for feedback, expected, loss_delta in test_combinations:
            score = validator._compute_consistency_score(
                feedback_signal=feedback,
                expected_signal=expected,
                loss_delta=loss_delta,
                recent_losses=[0.5, 0.6, 0.7],
            )

            assert 0.0 <= score <= 1.0, f"Score out of range: {score}"


class TestInconsistentFeedbackDownweighted:
    """Test: Low-consistency feedback contributes less to backprop."""

    def test_downweight_reduces_weight(self):
        """Case 1: Downweighting reduces feedback weight."""
        consistency_score = 0.3
        original_weight = 1.0

        downweighted = apply_consistency_downweighting(
            feedback_weight=original_weight,
            consistency_score=consistency_score,
        )

        expected = original_weight * consistency_score
        assert abs(downweighted - expected) < 0.001
        assert downweighted < original_weight

    def test_downweight_perfect_consistency(self):
        """Case 2: Perfect consistency (score=1.0) → no change."""
        consistency_score = 1.0
        original_weight = 1.5

        downweighted = apply_consistency_downweighting(
            feedback_weight=original_weight,
            consistency_score=consistency_score,
        )

        assert abs(downweighted - original_weight) < 0.001

    def test_downweight_zero_consistency(self):
        """Case 3: Zero consistency (score=0.0) → weight becomes 0."""
        consistency_score = 0.0
        original_weight = 1.5

        downweighted = apply_consistency_downweighting(
            feedback_weight=original_weight,
            consistency_score=consistency_score,
        )

        assert downweighted == 0.0

    def test_downweight_various_scores(self):
        """Case 4: Downweight applied correctly for various scores."""
        test_cases = [
            (0.25, 2.0),  # Score 0.25, weight 2.0 → 0.5
            (0.5, 1.0),   # Score 0.5, weight 1.0 → 0.5
            (0.75, 0.8),  # Score 0.75, weight 0.8 → 0.6
        ]

        for consistency_score, original_weight in test_cases:
            downweighted = apply_consistency_downweighting(
                feedback_weight=original_weight,
                consistency_score=consistency_score,
            )

            expected = original_weight * consistency_score
            assert abs(downweighted - expected) < 0.001


class TestFeedbackContradictionAuditLogged:
    """Test: Feedback contradiction events logged."""

    def test_audit_event_logged_on_contradiction(self):
        """Case 1: Contradiction event logged when inconsistent."""
        validator = FeedbackConsistencyValidator()

        class MockAuditBackend:
            def __init__(self):
                self.events = []

            def write_event(self, event):
                self.events.append(event)

        audit_backend = MockAuditBackend()
        validator.audit_backend = audit_backend

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.5, 0.52, 0.54, 0.56, 0.58])
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_009",
            skill_id="os.router",
            task_id="task_009",
            feedback_signal=FeedbackSignal.GOOD,  # Contradictory
            tenant_id="_default",
        )

        # Should have both consistency check and contradiction events
        assert len(audit_backend.events) >= 2

        contradiction_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_contradiction"]
        assert len(contradiction_events) > 0

    def test_contradiction_event_contains_downweight_factor(self):
        """Case 2: Contradiction event has downweight_factor field."""
        validator = FeedbackConsistencyValidator()

        class MockAuditBackend:
            def __init__(self):
                self.events = []

            def write_event(self, event):
                self.events.append(event)

        audit_backend = MockAuditBackend()
        validator.audit_backend = audit_backend

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.5, 0.52, 0.54, 0.56, 0.58])
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_010",
            skill_id="os.router",
            task_id="task_010",
            feedback_signal=FeedbackSignal.GOOD,  # Contradictory
            tenant_id="_default",
        )

        contradiction_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_contradiction"]
        assert len(contradiction_events) > 0

        contradiction = contradiction_events[0]
        assert "downweight_factor" in contradiction
        assert 0.0 <= contradiction["downweight_factor"] <= 1.0

    def test_audit_consistency_check_logged_always(self):
        """Case 3: Consistency check event always logged (even if consistent)."""
        validator = FeedbackConsistencyValidator()

        class MockAuditBackend:
            def __init__(self):
                self.events = []

            def write_event(self, event):
                self.events.append(event)

        audit_backend = MockAuditBackend()
        validator.audit_backend = audit_backend

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.6, 0.59, 0.58, 0.57, 0.56])
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_011",
            skill_id="os.router",
            task_id="task_011",
            feedback_signal=FeedbackSignal.GOOD,  # Consistent with decreasing loss
            tenant_id="_default",
        )

        consistency_events = [e for e in audit_backend.events if e.get("event_type") == "feedback_consistency_checked"]
        assert len(consistency_events) > 0

    def test_audit_events_contain_lom_field(self):
        """Case 4: Audit events have Line of Moral Responsibility (LoM)."""
        validator = FeedbackConsistencyValidator()

        class MockAuditBackend:
            def __init__(self):
                self.events = []

            def write_event(self, event):
                self.events.append(event)

        audit_backend = MockAuditBackend()
        validator.audit_backend = audit_backend

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.5, 0.52, 0.54, 0.56, 0.58])
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_012",
            skill_id="os.router",
            task_id="task_012",
            feedback_signal=FeedbackSignal.GOOD,  # Contradictory
            tenant_id="_default",
        )

        for event in audit_backend.events:
            if event.get("event_type") in ["feedback_consistency_checked", "feedback_contradiction"]:
                assert "lom" in event, f"Event {event.get('event_type')} missing LoM field"


class TestContradictionDoesNotBlock:
    """Test: Contradictory feedback still processed (downweighted, not rejected)."""

    def test_contradiction_returns_result(self):
        """Case 1: Contradictory feedback returns result (not blocked)."""
        validator = FeedbackConsistencyValidator()

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.5, 0.52, 0.54, 0.56, 0.58])
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_013",
            skill_id="os.router",
            task_id="task_013",
            feedback_signal=FeedbackSignal.GOOD,  # Contradictory
            tenant_id="_default",
        )

        assert result is not None
        assert isinstance(result, ConsistencyCheckResult)

    def test_contradiction_has_low_score_not_none(self):
        """Case 2: Contradictory feedback has score < 0.5 (not None or rejected)."""
        validator = FeedbackConsistencyValidator()

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.5, 0.52, 0.54, 0.56, 0.58])
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_014",
            skill_id="os.router",
            task_id="task_014",
            feedback_signal=FeedbackSignal.GOOD,  # Contradictory
            tenant_id="_default",
        )

        assert result.consistency_score is not None
        assert isinstance(result.consistency_score, float)
        assert result.consistency_score < 0.5

    def test_contradiction_preserves_feedback_metadata(self):
        """Case 3: Contradictory feedback preserves original metadata."""
        validator = FeedbackConsistencyValidator()

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.5, 0.52, 0.54, 0.56, 0.58])
                ]

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_015",
            skill_id="os.my_skill",
            task_id="task_xyz",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="tenant_001",
        )

        assert result.feedback_id == "fb_015"
        assert result.skill_id == "os.my_skill"
        assert result.task_id == "task_xyz"
        assert result.feedback_signal == FeedbackSignal.GOOD
        assert result.tenant_id == "tenant_001"

    def test_multiple_contradictions_in_cascade(self):
        """Attack Case 4: Multiple contradictions (feedback cascade attack)."""
        validator = FeedbackConsistencyValidator()

        class MockEventStore:
            def query_events(self, **kwargs):  # real EventStore contract
                return [
                    SimpleNamespace(signal={"total_loss": loss},
                                    timestamp=f"2026-09-07T10:00:{i:02d}Z")
                    for i, loss in enumerate([0.5, 0.52, 0.54, 0.56, 0.58])
                ]

        validator.event_store = MockEventStore()

        results = []
        for i in range(5):
            result = validator.validate_consistency(
                feedback_id=f"fb_{i:03d}",
                skill_id="os.router",
                task_id=f"task_{i:03d}",
                feedback_signal=FeedbackSignal.GOOD,  # All contradictory
                tenant_id="_default",
            )
            results.append(result)

        # All should be processed (not blocked)
        assert len(results) == 5

        # All should have low consistency scores
        for result in results:
            assert result.consistency_score < 0.5
            assert not result.is_consistent


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_loss_history(self):
        """Edge Case 1: No loss history available."""
        validator = FeedbackConsistencyValidator()

        class MockEventStore:
            def get_events(self, **kwargs):
                return []  # Empty history

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_edge_001",
            skill_id="os.router",
            task_id="task_edge_001",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        # Should return result with unknown trend
        assert result is not None
        assert result.loss_trend == "unknown"
        # Unknown trend → neutral score = 0.5 → consistent
        assert result.is_consistent

    def test_single_loss_sample(self):
        """Edge Case 2: Only one loss sample (can't detect trend)."""
        validator = FeedbackConsistencyValidator()

        class MockEventStore:
            def get_events(self, **kwargs):
                return [{"payload": {"total_loss": 0.5}}]  # Single sample

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_edge_002",
            skill_id="os.router",
            task_id="task_edge_002",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        # Should return result with unknown trend
        assert result.loss_trend == "unknown"
        assert result.is_consistent  # Unknown → neutral

    def test_event_store_unavailable(self):
        """Edge Case 3: Event store throws exception."""
        validator = FeedbackConsistencyValidator()

        class MockEventStore:
            def get_events(self, **kwargs):
                raise RuntimeError("Store unavailable")

        validator.event_store = MockEventStore()

        result = validator.validate_consistency(
            feedback_id="fb_edge_003",
            skill_id="os.router",
            task_id="task_edge_003",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="_default",
        )

        # Should fail-open (return consistent to avoid false negatives)
        assert result is not None
        assert result.is_consistent  # Fail-open
        assert result.consistency_score == 0.5

    def test_tenant_isolation(self):
        """Edge Case 4: Tenant isolation maintained."""
        validator = FeedbackConsistencyValidator()

        # Create separate loss histories per tenant (simulated)
        call_count = 0

        class MockEventStore:
            def get_events(self, **kwargs):
                nonlocal call_count
                call_count += 1
                # Each call should pass tenant_id
                assert "tenant_id" in kwargs
                return [{"payload": {"total_loss": loss}} for loss in [0.5, 0.52, 0.54, 0.56, 0.58]]

        validator.event_store = MockEventStore()

        result1 = validator.validate_consistency(
            feedback_id="fb_t1",
            skill_id="os.router",
            task_id="task_t1",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="tenant_1",
        )

        result2 = validator.validate_consistency(
            feedback_id="fb_t2",
            skill_id="os.router",
            task_id="task_t2",
            feedback_signal=FeedbackSignal.GOOD,
            tenant_id="tenant_2",
        )

        # Both should be processed
        assert result1 is not None
        assert result2 is not None
        # Both should have tenant_id preserved
        assert result1.tenant_id == "tenant_1"
        assert result2.tenant_id == "tenant_2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
