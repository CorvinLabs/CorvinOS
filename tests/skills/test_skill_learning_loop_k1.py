"""Tier 1-2 Tests for SkillLearningLoop & SkillOptimizer (Phase 7, k=1)."""
import json
import tempfile
from pathlib import Path

import pytest

from core.skills.skill_learning_loop import (
    SkillLearningLoop,
    SkillFeedback,
    SkillMetrics,
)
from core.skills.skill_optimizer import SkillOptimizer, TuningDecision


@pytest.fixture
def sample_feedback_store():
    """Sample feedback data."""
    return {
        "os.routing_optimizer": [
            {
                "version": "1.0.0",
                "execution_id": "exec_001",
                "outcome_correct": True,
                "latency_ms": 42.5,
                "tokens_used": 150,
                "cost_usd": 0.005,
                "timestamp": "2026-09-25T10:00:00Z",
            },
            {
                "version": "1.0.0",
                "execution_id": "exec_002",
                "outcome_correct": True,
                "latency_ms": 38.2,
                "tokens_used": 140,
                "cost_usd": 0.004,
                "timestamp": "2026-09-25T10:01:00Z",
            },
            {
                "version": "1.0.0",
                "execution_id": "exec_003",
                "outcome_correct": False,
                "latency_ms": 55.1,
                "error": "timeout",
                "tokens_used": 200,
                "cost_usd": 0.007,
                "timestamp": "2026-09-25T10:02:00Z",
            },
        ],
        "os.low_confidence_skill": [
            {
                "version": "0.5.0",
                "execution_id": "exec_101",
                "outcome_correct": False,
                "latency_ms": 120.0,
                "error": "invalid_input",
                "tokens_used": 300,
                "cost_usd": 0.010,
                "timestamp": "2026-09-25T10:10:00Z",
            },
            {
                "version": "0.5.0",
                "execution_id": "exec_102",
                "outcome_correct": True,
                "latency_ms": 95.0,
                "tokens_used": 280,
                "cost_usd": 0.009,
                "timestamp": "2026-09-25T10:11:00Z",
            },
        ],
    }


@pytest.fixture
def learning_loop(sample_feedback_store):
    """Initialize SkillLearningLoop with sample data."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_feedback_store, f)
        store_path = f.name

    try:
        loop = SkillLearningLoop(Path(store_path))
        yield loop
    finally:
        Path(store_path).unlink()


class TestSkillLearningLoop:
    """Unit tests for SkillLearningLoop."""

    def test_load_feedback_store(self, learning_loop):
        """Test loading feedback from store."""
        assert len(learning_loop._feedback_store) == 2
        assert "os.routing_optimizer" in learning_loop._feedback_store
        assert len(learning_loop._feedback_store["os.routing_optimizer"]) == 3

    def test_load_feedback_store_missing_file(self):
        """Test handling of missing feedback file."""
        loop = SkillLearningLoop(Path("/nonexistent/feedback.json"))
        assert len(loop._feedback_store) == 0

    def test_record_feedback(self, learning_loop):
        """Test recording new feedback."""
        new_feedback = SkillFeedback(
            skill_id="os.test_skill",
            version="1.0.0",
            execution_id="exec_new",
            outcome_correct=True,
            latency_ms=50.0,
        )

        learning_loop.record_feedback(new_feedback)

        assert "os.test_skill" in learning_loop._feedback_store
        assert len(learning_loop._feedback_store["os.test_skill"]) == 1

    def test_calculate_metrics_confidence(self, learning_loop):
        """Test confidence score calculation."""
        metrics = learning_loop.calculate_metrics("os.routing_optimizer")

        assert metrics is not None
        # 2 correct out of 3 = 66.7% confidence
        assert abs(metrics.confidence_score - 0.667) < 0.01
        assert metrics.total_executions == 3
        assert metrics.correct_outcomes == 2

    def test_calculate_metrics_not_found(self, learning_loop):
        """Test metrics calculation for non-existent skill."""
        metrics = learning_loop.calculate_metrics("nonexistent.skill")
        assert metrics is None

    def test_get_confidence_score(self, learning_loop):
        """Test confidence score retrieval."""
        confidence = learning_loop.get_confidence_score("os.routing_optimizer")
        assert 0.6 <= confidence <= 0.7

    def test_is_production_ready_true(self, learning_loop):
        """Test production-readiness check (should pass with high confidence)."""
        # Create a skill with high confidence
        high_confidence_feedback = [
            SkillFeedback(
                skill_id="os.high_confidence",
                version="1.0.0",
                execution_id=f"exec_{i}",
                outcome_correct=True,  # All correct
                latency_ms=30.0,
            )
            for i in range(20)
        ]

        for fb in high_confidence_feedback:
            learning_loop.record_feedback(fb)

        # Should be production-ready (100% confidence > 75% threshold)
        is_ready = learning_loop.is_production_ready("os.high_confidence", min_confidence=0.75)
        assert is_ready is True

    def test_is_production_ready_false(self, learning_loop):
        """Test production-readiness check (should fail with low confidence)."""
        # os.low_confidence_skill has only 1/2 correct = 50% confidence
        is_ready = learning_loop.is_production_ready(
            "os.low_confidence_skill", min_confidence=0.75
        )
        assert is_ready is False

    def test_metrics_caching(self, learning_loop):
        """Test that metrics are cached."""
        metrics1 = learning_loop.calculate_metrics("os.routing_optimizer")
        metrics2 = learning_loop.calculate_metrics("os.routing_optimizer")

        assert metrics1 is metrics2  # Same object from cache

    def test_cache_invalidation(self, learning_loop):
        """Test cache invalidation."""
        learning_loop.calculate_metrics("os.routing_optimizer")
        assert "os.routing_optimizer" in learning_loop._metrics_cache

        learning_loop.invalidate_cache()
        assert len(learning_loop._metrics_cache) == 0


class TestSkillOptimizer:
    """Unit tests for SkillOptimizer."""

    def test_optimizer_initialization(self, learning_loop):
        """Test SkillOptimizer initialization."""
        optimizer = SkillOptimizer(learning_loop)
        assert optimizer.learning_loop is learning_loop

    def test_propose_tuning_high_confidence(self, learning_loop):
        """Test tuning proposal for high-confidence Skill (should be None)."""
        # os.routing_optimizer has ~67% confidence, but high error rate
        optimizer = SkillOptimizer(learning_loop)

        proposal = optimizer.propose_tuning("os.routing_optimizer")
        # Should propose tuning because error_rate > 10%
        assert proposal is not None
        assert proposal.parameter_name == "retry_threshold"

    def test_propose_tuning_low_confidence(self, learning_loop):
        """Test tuning proposal for low-confidence Skill."""
        optimizer = SkillOptimizer(learning_loop)

        proposal = optimizer.propose_tuning("os.low_confidence_skill")
        # Should propose tuning because confidence < 75%
        assert proposal is not None
        assert proposal.parameter_name in ["retry_threshold", "confidence_threshold"]

    def test_propose_tuning_no_metrics(self, learning_loop):
        """Test tuning proposal for non-existent Skill."""
        optimizer = SkillOptimizer(learning_loop)
        proposal = optimizer.propose_tuning("nonexistent.skill")
        assert proposal is None

    def test_apply_tuning_canary(self, learning_loop):
        """Test canary deployment simulation."""
        from core.skills.skill_optimizer import OptimizationProposal

        optimizer = SkillOptimizer(learning_loop)

        proposal = OptimizationProposal(
            skill_id="os.routing_optimizer",
            version="1.0.0",
            parameter_name="retry_threshold",
            old_value="3",
            new_value="5",
            rationale="Test proposal",
            expected_improvement=8.5,
            confidence=0.72,
        )

        canary_result = optimizer.apply_tuning_canary(proposal)

        assert canary_result["status"] == "in_progress"
        assert canary_result["traffic_pct"] == 10
        assert "improvement_pct" in canary_result

    def test_commit_tuning_success(self, learning_loop):
        """Test successful tuning commit."""
        from core.skills.skill_optimizer import OptimizationProposal

        optimizer = SkillOptimizer(learning_loop)

        proposal = OptimizationProposal(
            skill_id="os.routing_optimizer",
            version="1.0.0",
            parameter_name="retry_threshold",
            old_value="3",
            new_value="5",
            rationale="Test",
            expected_improvement=8.5,
            confidence=0.72,
        )

        canary_result = {
            "confidence_before": 0.667,
            "confidence_after": 0.72,
            "improvement_pct": 8.0,  # Above 5% threshold
        }

        decision = optimizer.commit_tuning(proposal, canary_result)

        assert decision.status == "successful"
        assert decision.improvement_pct == 8.0

    def test_commit_tuning_rollback(self, learning_loop):
        """Test tuning rollback (low improvement)."""
        from core.skills.skill_optimizer import OptimizationProposal

        optimizer = SkillOptimizer(learning_loop)

        proposal = OptimizationProposal(
            skill_id="os.routing_optimizer",
            version="1.0.0",
            parameter_name="retry_threshold",
            old_value="3",
            new_value="5",
            rationale="Test",
            expected_improvement=2.0,
            confidence=0.4,
        )

        canary_result = {
            "confidence_before": 0.667,
            "confidence_after": 0.68,
            "improvement_pct": 2.0,  # Below 5% threshold
        }

        decision = optimizer.commit_tuning(proposal, canary_result)

        assert decision.status == "rolled_back"
        assert decision.improvement_pct == 2.0

    def test_tuning_history(self, learning_loop):
        """Test tuning history tracking."""
        from core.skills.skill_optimizer import OptimizationProposal

        optimizer = SkillOptimizer(learning_loop)

        proposal = OptimizationProposal(
            skill_id="os.test_skill",
            version="1.0.0",
            parameter_name="test_param",
            old_value="old",
            new_value="new",
            rationale="Test",
            expected_improvement=10.0,
            confidence=0.8,
        )

        canary_result = {"confidence_before": 0.5, "confidence_after": 0.6, "improvement_pct": 10.0}
        decision = optimizer.commit_tuning(proposal, canary_result)

        history = optimizer.get_tuning_history("os.test_skill")
        assert len(history) == 1
        assert history[0] == decision
