"""Phase 3a E2E Tests: Confidence Gate Learning with operator feedback loop.

Fictional task scenarios:
    1. Bug-fix routing (operator confirms correct routing)
    2. Feature request (system misroutes, operator corrects)
    3. Refactoring (system hesitant, operator validates decision)
    4. Incident (high confidence, operator validates)
    5. Documentation (low confidence, operator confirms safe)

Tests confidence threshold optimization via operator feedback.

ADR: ADR-0269 (Confidence Gate Learning)
"""

import pytest
from datetime import datetime
from pathlib import Path
import tempfile

from ..feedback_loop import RoutingFeedback, FeedbackStore, ConfidenceGateLearner


@pytest.fixture
def feedback_store():
    """Create temporary feedback store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "feedback.jsonl"
        yield FeedbackStore(storage_path=store_path)


# ============================================================================
# E2E Fictional Task Scenarios (Tier 4 E2E)
# ============================================================================

class TestPhase3aConfidenceLearningE2E:
    """End-to-end tests with realistic fictional task scenarios."""

    def test_e2e_bug_fix_correct_routing(self, feedback_store):
        """Scenario 1: Bug-fix routed to native (correct by operator).

        Task: "Fix crash in voice module for long audio"
        System: confidence 0.85 → native
        Operator: "correct" ✅
        """
        feedback = RoutingFeedback(
            task_id="task_001_bug_fix_voice",
            raw_task="Fix crash in voice module for long audio files playback",
            predicted_target="native",
            predicted_confidence=0.85,
            actual_target="native",
            operator_feedback="correct",
            operator_id="shumway",
            notes="Confirmed: Haiku E2E-driven-iteration was right approach",
        )

        # Record feedback
        assert feedback_store.record_feedback(feedback)

        # Verify storage
        all_feedback = feedback_store.load_all()
        assert len(all_feedback) == 1
        assert all_feedback[0].operator_feedback == "correct"

    def test_e2e_feature_misrouted_then_corrected(self, feedback_store):
        """Scenario 2: Feature request misrouted by system, operator corrects.

        Task: "Add batch processing for SQL queries"
        System: confidence 0.65 → native (conservative)
        Operator: actually needed ACS (complex data processing)
        Operator feedback: "incorrect"
        """
        feedback = RoutingFeedback(
            task_id="task_002_feature_batch",
            raw_task="Add batch processing capability for SQL queries on warehouse data",
            predicted_target="native",
            predicted_confidence=0.65,
            actual_target="acs",
            operator_feedback="incorrect",
            operator_id="shumway",
            notes="System was too conservative; ACS delegation was needed for volume",
        )

        assert feedback_store.record_feedback(feedback)
        all_feedback = feedback_store.load_all()
        assert all_feedback[0].predicted_confidence == 0.65

    def test_e2e_refactor_correct_but_hesitant(self, feedback_store):
        """Scenario 3: Refactoring routed correctly but with low confidence.

        Task: "Refactor session management layer across all modules"
        System: confidence 0.45 → native (uncertain)
        Operator: "correct" (even though confidence was low)

        Insight: System should increase threshold for refactors.
        """
        feedback = RoutingFeedback(
            task_id="task_003_refactor_sessions",
            raw_task="Refactor session management across all modules for consistency",
            predicted_target="native",
            predicted_confidence=0.45,
            actual_target="native",
            operator_feedback="correct",
            operator_id="shumway",
            notes="Low confidence was incorrect; refactor needed Opus for strategy",
        )

        assert feedback_store.record_feedback(feedback)
        all_feedback = feedback_store.load_all()
        assert len(all_feedback) == 1

    def test_e2e_incident_high_confidence_correct(self, feedback_store):
        """Scenario 4: Incident routed with high confidence to Opus.

        Task: "CRITICAL: Memory leak causing production outage"
        System: confidence 0.92 → Opus (TDE)
        Operator: "correct" (needed expert reasoning)
        """
        feedback = RoutingFeedback(
            task_id="task_004_incident_memory",
            raw_task="CRITICAL: Memory leak in voice adapter causing production outage",
            predicted_target="tde",
            predicted_confidence=0.92,
            actual_target="tde",
            operator_feedback="correct",
            operator_id="shumway",
            notes="High confidence routing to Opus was right; needed complex debugging",
        )

        assert feedback_store.record_feedback(feedback)

    def test_e2e_documentation_low_confidence_confirmed(self, feedback_store):
        """Scenario 5: Documentation task, low confidence, operator confirms.

        Task: "Update README with deployment guide"
        System: confidence 0.30 → native (Haiku)
        Operator: "correct" (simple task, no complexity)
        """
        feedback = RoutingFeedback(
            task_id="task_005_docs_readme",
            raw_task="Update README.md with step-by-step deployment guide for new users",
            predicted_target="native",
            predicted_confidence=0.30,
            actual_target="native",
            operator_feedback="correct",
            operator_id="shumway",
            notes="Low confidence was appropriate; Haiku handled it perfectly",
        )

        assert feedback_store.record_feedback(feedback)


# ============================================================================
# Threshold Optimization Tests (Tier 3 Integration)
# ============================================================================

class TestPhase3aThresholdOptimization:
    """Test confidence threshold optimization via feedback."""

    def test_accuracy_at_default_threshold(self, feedback_store):
        """Compute accuracy at default 0.70 threshold."""
        # Add mixed feedback (correct and incorrect)
        feedbacks = [
            RoutingFeedback(
                task_id=f"task_{i:03d}",
                raw_task=f"Task {i}",
                predicted_target="native",
                predicted_confidence=0.50 + (i * 0.05),  # 0.50, 0.55, 0.60, ...
                actual_target="native",
                operator_feedback="correct" if (i % 2 == 0) else "incorrect",
            )
            for i in range(10)
        ]

        for fb in feedbacks:
            assert feedback_store.record_feedback(fb)

        # Compute accuracy at default 0.70
        correct, total, accuracy = feedback_store.accuracy_for_threshold(0.70)
        assert total == 10
        assert accuracy >= 0.0 and accuracy <= 100.0

    def test_learner_finds_optimal_threshold(self, feedback_store):
        """Test that learner finds optimal threshold from feedback."""
        # Build dataset: mix of correct/incorrect at different confidence levels
        feedbacks = [
            # Low confidence (0.2-0.4): mostly correct (conservative is safe)
            RoutingFeedback("t1", "Task", "native", 0.25, "native", "correct"),
            RoutingFeedback("t2", "Task", "native", 0.30, "native", "correct"),
            RoutingFeedback("t3", "Task", "native", 0.35, "acs", "incorrect"),  # should defer to native
            # Mid confidence (0.5-0.7): mixed
            RoutingFeedback("t4", "Task", "native", 0.50, "native", "correct"),
            RoutingFeedback("t5", "Task", "acs", 0.55, "native", "incorrect"),  # over-routed
            RoutingFeedback("t6", "Task", "native", 0.65, "native", "correct"),
            # High confidence (0.8-0.95): mostly correct
            RoutingFeedback("t7", "Task", "tde", 0.80, "tde", "correct"),
            RoutingFeedback("t8", "Task", "tde", 0.85, "tde", "correct"),
            RoutingFeedback("t9", "Task", "tde", 0.90, "tde", "correct"),
            RoutingFeedback("t10", "Task", "acs", 0.95, "acs", "correct"),
        ]

        for fb in feedbacks:
            assert feedback_store.record_feedback(fb)

        # Run learner
        learner = ConfidenceGateLearner(feedback_store)
        optimal_threshold = learner.find_optimal_threshold()

        # Optimal should be somewhere in [0.4, 0.8] range
        # (balancing precision vs recall)
        assert 0.4 <= optimal_threshold <= 0.8

    def test_learner_recommendation(self, feedback_store):
        """Test learner recommendation API."""
        feedbacks = [
            RoutingFeedback("t1", "Task", "native", 0.70, "native", "correct"),
            RoutingFeedback("t2", "Task", "native", 0.72, "native", "correct"),
            RoutingFeedback("t3", "Task", "acs", 0.65, "native", "incorrect"),
        ]

        for fb in feedbacks:
            feedback_store.record_feedback(fb)

        learner = ConfidenceGateLearner(feedback_store)
        recommendation = learner.recommend_threshold()

        assert "threshold" in recommendation
        assert "feedback_count" in recommendation
        assert "action" in recommendation
        assert recommendation["feedback_count"] == 3


# ============================================================================
# Feedback Store Integration Tests (Tier 2 Unit + Tier 3 Integration)
# ============================================================================

class TestPhase3aFeedbackStore:
    """Test feedback storage and retrieval."""

    def test_record_and_retrieve_feedback(self, feedback_store):
        """Record feedback and retrieve it."""
        fb = RoutingFeedback(
            task_id="test_task_123",
            raw_task="Test task description",
            predicted_target="native",
            predicted_confidence=0.75,
            actual_target="native",
            operator_feedback="correct",
        )

        assert feedback_store.record_feedback(fb)

        all_feedback = feedback_store.load_all()
        assert len(all_feedback) == 1
        assert all_feedback[0].task_id == "test_task_123"

    def test_load_since_timestamp(self, feedback_store):
        """Load feedback since given timestamp."""
        # Record first feedback
        fb1 = RoutingFeedback("t1", "Task", "native", 0.5, "native", "correct",
                             timestamp="2026-08-20T10:00:00")
        feedback_store.record_feedback(fb1)

        # Record second feedback later
        fb2 = RoutingFeedback("t2", "Task", "native", 0.7, "native", "correct",
                             timestamp="2026-08-20T12:00:00")
        feedback_store.record_feedback(fb2)

        # Load since 11:00 (should only get fb2)
        recent = feedback_store.load_since("2026-08-20T11:00:00")
        assert len(recent) == 1
        assert recent[0].task_id == "t2"

    def test_immutable_append_only_log(self, feedback_store):
        """Verify feedback log is append-only (immutable)."""
        fb1 = RoutingFeedback("t1", "Task", "native", 0.5, "native", "correct")
        feedback_store.record_feedback(fb1)

        # Load first time
        first_load = feedback_store.load_all()
        assert len(first_load) == 1

        # Record another
        fb2 = RoutingFeedback("t2", "Task", "acs", 0.8, "acs", "correct")
        feedback_store.record_feedback(fb2)

        # Load second time
        second_load = feedback_store.load_all()
        assert len(second_load) == 2
        # First entry unchanged
        assert second_load[0].task_id == "t1"


# ============================================================================
# Tier 4 Completion Gate (Production Readiness)
# ============================================================================

class TestPhase3aCompletionGate:
    """Verify Phase 3a requirements met for production."""

    def test_phase3a_requirements(self, feedback_store):
        """Checklist: operator feedback loop, threshold learning, recommendations."""
        # 1. Feedback recording
        fb = RoutingFeedback("test", "Task", "native", 0.75, "native", "correct")
        assert feedback_store.record_feedback(fb), "Feedback recording failed"

        # 2. Retrieval
        all_fb = feedback_store.load_all()
        assert len(all_fb) == 1, "Feedback retrieval failed"

        # 3. Threshold optimization
        learner = ConfidenceGateLearner(feedback_store)
        optimal = learner.find_optimal_threshold()
        assert isinstance(optimal, float), "Learner failed to find threshold"
        assert 0.0 <= optimal <= 1.0, "Threshold out of bounds"

        # 4. Recommendation API
        rec = learner.recommend_threshold()
        assert "threshold" in rec and "action" in rec, "Recommendation incomplete"

        # 5. Immutable log (append-only)
        fb2 = RoutingFeedback("t2", "Task2", "acs", 0.8, "acs", "correct")
        feedback_store.record_feedback(fb2)
        assert len(feedback_store.load_all()) == 2, "Immutable log violated"
