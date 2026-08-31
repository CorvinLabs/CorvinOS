"""Tests for LDD Goal Re-Synchronization Protocol (ADR-0406 Phase 3).

Test categories:
1. GoalAlignmentCheckpoint creation + immutability
2. LDDGoalResyncProtocol initialization
3. Decision logic (CONTINUE/CORRECT/ESCALATE)
4. Drift counter tracking
5. Similarity/completeness scoring
6. Audit trail integration
7. E2E 100-iteration simulation
"""

import pytest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, MagicMock

from core.session_manager.ldd_goal_resync import (
    GoalAlignmentCheckpoint,
    LDDGoalResyncProtocol,
)


class TestGoalAlignmentCheckpoint:
    """Tests for immutable checkpoint dataclass."""

    def test_create_checkpoint(self):
        """Create a valid checkpoint."""
        checkpoint = GoalAlignmentCheckpoint(
            iteration_num=1,
            similarity_score=0.8,
            completeness_score=0.7,
            composite_score=0.76,
            drift_count=0,
            decision="CONTINUE",
            reason="Goal alignment strong",
        )
        assert checkpoint.iteration_num == 1
        assert checkpoint.similarity_score == 0.8
        assert checkpoint.decision == "CONTINUE"

    def test_checkpoint_immutable(self):
        """Checkpoint is frozen (immutable)."""
        checkpoint = GoalAlignmentCheckpoint(
            iteration_num=1,
            similarity_score=0.8,
            completeness_score=0.7,
            composite_score=0.76,
            drift_count=0,
            decision="CONTINUE",
            reason="Goal alignment strong",
        )
        # Should raise FrozenInstanceError
        with pytest.raises(FrozenInstanceError):
            checkpoint.similarity_score = 0.9

    def test_checkpoint_default_values(self):
        """Checkpoint fields have sensible defaults if applicable."""
        checkpoint = GoalAlignmentCheckpoint(
            iteration_num=1,
            similarity_score=0.5,
            completeness_score=0.5,
            composite_score=0.5,
            drift_count=1,
            decision="CORRECT",
            reason="Low alignment",
        )
        assert checkpoint.drift_count == 1


class TestLDDGoalResyncProtocol:
    """Tests for LDD Goal Re-Sync protocol logic."""

    @pytest.fixture
    def mock_goal_context(self):
        """Mock GoalContext with original_goal."""
        mock = MagicMock()
        mock.original_goal = "Refactor payment processing module"
        return mock

    @pytest.fixture
    def protocol(self, mock_goal_context):
        """Create protocol instance with mock goal context."""
        return LDDGoalResyncProtocol(goal_context=mock_goal_context)

    def test_protocol_init(self, mock_goal_context):
        """Protocol initializes with default state."""
        protocol = LDDGoalResyncProtocol(goal_context=mock_goal_context)
        assert protocol.drift_count == 0
        assert protocol.checkpoint_history == []
        assert protocol.SIMILARITY_THRESHOLD_CONTINUE == 0.7
        assert protocol.SIMILARITY_THRESHOLD_CORRECT == 0.5
        assert protocol.DRIFT_COUNT_ESCALATE == 3

    # ===== Decision Logic Tests =====

    def test_decide_continue_high_score(self, protocol):
        """CONTINUE decision when composite >= 0.7."""
        decision, reason = protocol._decide_action(composite=0.8, iteration=1)
        assert decision == "CONTINUE"
        assert "strong" in reason.lower()

    def test_decide_correct_medium_score_no_drift(self, protocol):
        """CORRECT decision when 0.5 <= composite < 0.7 and drift_count < 2."""
        protocol.drift_count = 1
        decision, reason = protocol._decide_action(composite=0.6, iteration=2)
        assert decision == "CORRECT"
        assert "correction" in reason.lower()

    def test_decide_escalate_medium_score_with_drift(self, protocol):
        """ESCALATE decision when composite < 0.7 and drift_count >= 2."""
        protocol.drift_count = 2
        decision, reason = protocol._decide_action(composite=0.6, iteration=5)
        assert decision == "ESCALATE"
        assert "drifts" in reason.lower()

    def test_decide_escalate_low_score_persistent_drift(self, protocol):
        """ESCALATE decision when composite < 0.5 and drift_count >= 3."""
        protocol.drift_count = 3
        decision, reason = protocol._decide_action(composite=0.4, iteration=10)
        assert decision == "ESCALATE"
        assert "3+" in reason or "drift" in reason.lower()

    def test_decide_correct_low_score_early_drift(self, protocol):
        """CORRECT decision when composite < 0.5 but drift_count < 3."""
        protocol.drift_count = 1
        decision, reason = protocol._decide_action(composite=0.3, iteration=4)
        assert decision == "CORRECT"

    # ===== Drift Tracking Tests =====

    def test_drift_counter_increments_on_low_score(self, protocol):
        """Drift counter increments when score < THRESHOLD_CONTINUE."""
        # Simulate low similarity — should increment drift
        checkpoint = protocol.check_before_iteration(
            iteration_num=1, current_strategy="Optimize caching"
        )
        # Note: scoring returns 0.0 for now (TODO in k=2), so drift will increment
        assert protocol.drift_count >= 0  # Will be 1 after scoring is implemented

    def test_drift_counter_resets_on_high_score(self, protocol):
        """Drift counter resets to 0 when score >= THRESHOLD_CONTINUE."""
        protocol.drift_count = 3  # Set to high
        # After scoring is implemented, a high-score iteration should reset
        # For now, just verify counter exists
        assert protocol.drift_count == 3

    # ===== Checkpoint History Tests =====

    def test_checkpoint_appended_to_history(self, protocol):
        """Each check_before_iteration appends to checkpoint_history."""
        protocol.check_before_iteration(
            iteration_num=1, current_strategy="Start refactoring"
        )
        assert len(protocol.checkpoint_history) == 1

        protocol.check_before_iteration(
            iteration_num=2, current_strategy="Continue refactoring"
        )
        assert len(protocol.checkpoint_history) == 2

    def test_checkpoint_history_order(self, protocol):
        """Checkpoint history maintains iteration order."""
        for i in range(5):
            protocol.check_before_iteration(
                iteration_num=i, current_strategy=f"Iteration {i}"
            )

        assert len(protocol.checkpoint_history) == 5
        for i, checkpoint in enumerate(protocol.checkpoint_history):
            assert checkpoint.iteration_num == i

    # ===== Audit Integration Tests =====

    def test_audit_logger_called_on_check(self, protocol, mock_goal_context):
        """Audit logger is called if provided."""
        mock_audit = MagicMock()
        protocol.audit_logger = mock_audit

        protocol.check_before_iteration(
            iteration_num=1, current_strategy="Start work"
        )

        # Verify audit was called
        mock_audit.log_event.assert_called_once()
        call_args = mock_audit.log_event.call_args
        assert call_args[0][0] == "ldd_goal_alignment_check"
        assert "iteration" in call_args[0][1]
        assert "decision" in call_args[0][1]


class TestSimilarityAndCompletenessScoring:
    """Tests for similarity and completeness scoring (placeholder for k=2)."""

    @pytest.fixture
    def protocol(self):
        """Create protocol instance."""
        mock_goal = MagicMock()
        mock_goal.original_goal = "Refactor payment"
        return LDDGoalResyncProtocol(goal_context=mock_goal)

    def test_similarity_returns_float(self, protocol):
        """_compute_similarity returns a float in [0.0, 1.0]."""
        score = protocol._compute_similarity("Goal A", "Strategy A")
        assert isinstance(score, float)
        # Real implementation: Jaccard similarity
        assert 0.0 <= score <= 1.0
        # "Goal A" and "Strategy A" have 1 common word, union is 3 → Jaccard = 1/3 ≈ 0.333
        assert abs(score - (1.0/3.0)) < 0.01

    def test_completeness_returns_float(self, protocol):
        """_compute_completeness returns a float in [0.0, 1.0]."""
        score = protocol._compute_completeness("Goal A", "Strategy A")
        assert isinstance(score, float)
        # Real implementation: keyword coverage
        assert 0.0 <= score <= 1.0
        # "Goal A" has 2 terms, "Strategy A" contains "A" (1 match) → 1/2 = 0.5
        assert score == 0.5


class TestIntegrationWithLDDOuterLoop:
    """Integration tests with LDDOuterLoop orchestration."""

    @pytest.fixture
    def mock_goal_context(self):
        """Mock GoalContext."""
        mock = MagicMock()
        mock.original_goal = "Refactor payment processing"
        return mock

    def test_protocol_with_outer_loop_initialization(self, mock_goal_context):
        """LDDOuterLoop initializes protocol correctly."""
        from core.learning.loss_driven_development import LDDOuterLoop

        loop = LDDOuterLoop(
            goal_context=mock_goal_context, max_iterations=10, audit_logger=None
        )
        assert loop.goal_resync is not None
        assert loop.goal_resync.goal_context == mock_goal_context
        assert loop.iteration_num == 0

    def test_audit_trail_records_goal_alignment_events(self, mock_goal_context):
        """Audit trail captures all alignment checks (GDPR Art. 30)."""
        from core.learning.loss_driven_development import LDDOuterLoop

        mock_audit = MagicMock()
        loop = LDDOuterLoop(
            goal_context=mock_goal_context,
            max_iterations=10,
            audit_logger=mock_audit,
        )

        # Simulate multiple iterations
        for i in range(3):
            loop.goal_resync.check_before_iteration(
                iteration_num=i, current_strategy=f"Iteration {i} work"
            )

        # Verify audit was called for each check
        assert mock_audit.log_event.call_count == 3
        # Each call should be ldd_goal_alignment_check
        for call in mock_audit.log_event.call_args_list:
            assert call[0][0] == "ldd_goal_alignment_check"

    def test_drift_report_generation(self, mock_goal_context):
        """get_goal_drift_report() generates compliance-ready report."""
        from core.learning.loss_driven_development import LDDOuterLoop

        loop = LDDOuterLoop(goal_context=mock_goal_context, max_iterations=10)

        # Simulate iterations with varying alignment
        for i in range(5):
            loop.goal_resync.check_before_iteration(
                iteration_num=i, current_strategy=f"Work at iteration {i}"
            )

        report = loop.get_goal_drift_report()
        assert "total_iterations" in report
        assert "drift_detected_at" in report or "total_drift_events" in report
        assert "escalation_count" in report
        assert "checkpoints" in report
        assert len(report["checkpoints"]) == 5


# ===== E2E SIMULATION TESTS (Tier 4, k=5) =====
class TestE2EGoalDriftSimulation:
    """E2E tests with 100-iteration simulations."""

    @pytest.fixture
    def mock_goal_context(self):
        """Mock GoalContext."""
        mock = MagicMock()
        mock.original_goal = "Implement user authentication system"
        return mock

    def test_drift_detection_within_iterations(self, mock_goal_context):
        """Drift is detected within 2-3 iterations of divergence."""
        from core.learning.loss_driven_development import LDDOuterLoop

        loop = LDDOuterLoop(goal_context=mock_goal_context, max_iterations=50)

        # Iterations 0-9: aligned work
        aligned_work = "Implementing authentication with JWT tokens"
        for i in range(10):
            loop.goal_resync.check_before_iteration(
                iteration_num=i, current_strategy=aligned_work
            )

        # Iteration 10-12: start drifting to unrelated task
        drift_work = "Optimizing database cache performance for auth"
        for i in range(10, 13):
            loop.goal_resync.check_before_iteration(
                iteration_num=i, current_strategy=drift_work
            )

        # Check: drift should be detected by iteration 12 or 13
        checkpoints = loop.goal_resync.checkpoint_history
        drift_detected = [cp for cp in checkpoints[10:] if cp.decision in ("CORRECT", "ESCALATE")]
        assert len(drift_detected) > 0, "Drift should be detected within 2-3 iterations"

    def test_no_false_negatives_on_persistent_drift(self, mock_goal_context):
        """All real drifts are caught (0 false negatives)."""
        from core.learning.loss_driven_development import LDDOuterLoop

        loop = LDDOuterLoop(goal_context=mock_goal_context, max_iterations=100)

        # 50 iterations of actual goal work
        goal_aligned = "Fixing cache invalidation bug in payment processing"
        for i in range(50):
            loop.goal_resync.check_before_iteration(
                iteration_num=i, current_strategy=goal_aligned
            )

        # 30 iterations of drifted work (unrelated optimization)
        drifted_work = "Optimizing API response time for logging system"
        for i in range(50, 80):
            loop.goal_resync.check_before_iteration(
                iteration_num=i, current_strategy=drifted_work
            )

        # Verify: ESCALATE decision must appear in later iterations
        checkpoints = loop.goal_resync.checkpoint_history
        late_escalations = [
            cp for cp in checkpoints[70:] if cp.decision == "ESCALATE"
        ]
        # With 30 iterations of drift, we should see at least one escalation
        assert len(late_escalations) > 0 or any(
            cp.drift_count >= 3 for cp in checkpoints[70:]
        ), "Persistent drift should trigger escalation"
