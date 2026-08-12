"""Tests for Confidence Scorer (ADR-0315)."""

import pytest
from core.learning.confidence_scorer import (
    ConfidenceScorer,
    ConfidenceBand,
    ConfidenceScore,
)


class TestConfidenceScorer:
    """Test confidence scoring logic."""

    @pytest.fixture
    def scorer(self):
        """Create scorer instance."""
        return ConfidenceScorer()

    def test_score_skill_basic(self, scorer):
        """Score a skill with typical metrics."""
        score = scorer.score_skill(
            skill_name="code_review",
            task_type="code_review",
            invocation_count=20,
            error_rate=0.05,
            avg_latency_ms=1000.0,
            latency_stddev_ms=100.0,
        )

        assert isinstance(score, ConfidenceScore)
        assert 0.0 <= score.relevance <= 1.0
        assert 0.0 <= score.reliability <= 1.0
        assert 0.0 <= score.combined <= 1.0
        assert isinstance(score.band, ConfidenceBand)

    def test_high_confidence_score(self, scorer):
        """High relevance + high reliability = high confidence."""
        score = scorer.score_skill(
            skill_name="code_review",
            task_type="code_review",  # Perfect match
            invocation_count=50,
            error_rate=0.02,  # Low errors
            avg_latency_ms=1000.0,
            latency_stddev_ms=50.0,  # Low variance
        )

        # Relevance should be high (0.95)
        assert score.relevance > 0.90
        # Reliability should be high (low error + low variance)
        assert score.reliability > 0.90
        # Combined should be high
        assert score.combined > 0.90
        assert score.band == ConfidenceBand.VERY_HIGH

    def test_low_confidence_score(self, scorer):
        """High error rate = low confidence."""
        score = scorer.score_skill(
            skill_name="summarizer",
            task_type="code_review",  # Poor match
            invocation_count=30,
            error_rate=0.50,  # High errors!
            avg_latency_ms=1000.0,
            latency_stddev_ms=200.0,  # High variance
        )

        # Relevance should be low (0.3)
        assert score.relevance < 0.5
        # Reliability should be low (high error rate)
        assert score.reliability < 0.6
        # Combined should be low
        assert score.combined < 0.6
        assert score.band in [ConfidenceBand.LOW, ConfidenceBand.VERY_LOW]

    def test_insufficient_data_score(self, scorer):
        """< 5 invocations = neutral score."""
        score = scorer.score_skill(
            skill_name="code_review",
            task_type="code_review",
            invocation_count=2,  # Too few!
            error_rate=0.0,
            avg_latency_ms=1000.0,
            latency_stddev_ms=0.0,
        )

        # Reliability should be neutral (0.5) despite perfect metrics
        assert score.reliability == 0.5
        # Relevance should still be high
        assert score.relevance > 0.9
        # Combined should be medium
        assert score.combined > 0.6

    def test_unknown_skill_task_pairing(self, scorer):
        """Unknown pairing = neutral relevance (0.5)."""
        score = scorer.score_skill(
            skill_name="unknown_skill",
            task_type="unknown_task",
            invocation_count=20,
            error_rate=0.0,
            avg_latency_ms=1000.0,
            latency_stddev_ms=0.0,
        )

        # Relevance should be neutral
        assert score.relevance == 0.5
        # Reliability should be high (no errors, no variance)
        assert score.reliability > 0.9

    def test_high_latency_variance(self, scorer):
        """High latency variance = low reliability."""
        score = scorer.score_skill(
            skill_name="code_review",
            task_type="code_review",
            invocation_count=20,
            error_rate=0.0,  # No errors
            avg_latency_ms=1000.0,
            latency_stddev_ms=2000.0,  # High variance (CV > 1.0)
        )

        # Reliability should be lowered by high variance (capped at 1.0 cv = 0.0 latency reliability)
        # Reliability = 0.7 * 1.0 (error) + 0.3 * 0.0 (latency) = 0.7
        assert score.reliability <= 0.7
        # Relevance should still be high
        assert score.relevance > 0.9

    def test_confidence_band_mapping(self, scorer):
        """Verify score → band mapping."""
        # VERY_HIGH: perfect skill/task match + low error + low variance
        score = scorer.score_skill(
            skill_name="code_review",
            task_type="code_review",
            invocation_count=100,
            error_rate=0.01,
            avg_latency_ms=1000.0,
            latency_stddev_ms=10.0,
        )
        assert score.band == ConfidenceBand.VERY_HIGH

        # LOW or VERY_LOW: bad metrics
        score = scorer.score_skill(
            skill_name="unknown",
            task_type="unknown",
            invocation_count=50,
            error_rate=0.90,  # Very high error!
            avg_latency_ms=1000.0,
            latency_stddev_ms=3000.0,  # Very high variance
        )
        assert score.band in [ConfidenceBand.LOW, ConfidenceBand.VERY_LOW]

    def test_user_feedback_overrides_heuristic(self, scorer):
        """User feedback (ADR-0317) should boost relevance."""
        # Poor pairing normally
        score_without_feedback = scorer.score_skill(
            skill_name="summarizer",
            task_type="code_review",
            invocation_count=20,
            error_rate=0.05,
            avg_latency_ms=1000.0,
            latency_stddev_ms=100.0,
            user_feedback_score=None,
        )

        # Same metrics with good user feedback
        score_with_feedback = scorer.score_skill(
            skill_name="summarizer",
            task_type="code_review",
            invocation_count=20,
            error_rate=0.05,
            avg_latency_ms=1000.0,
            latency_stddev_ms=100.0,
            user_feedback_score=0.9,  # User says it's good!
        )

        # Feedback should boost relevance
        assert score_with_feedback.relevance > score_without_feedback.relevance
        # Combined should also be higher
        assert score_with_feedback.combined > score_without_feedback.combined

    def test_all_relevance_pairings(self, scorer):
        """Verify all heuristic pairings work."""
        pairings = [
            ("ranking", "summarize", 0.9),
            ("ranking", "code_review", 0.3),
            ("code_review", "code_review", 0.95),
            ("summarizer", "summarize", 0.95),
        ]

        for skill, task, expected_relevance in pairings:
            score = scorer.score_skill(
                skill_name=skill,
                task_type=task,
                invocation_count=50,
                error_rate=0.0,
                avg_latency_ms=1000.0,
                latency_stddev_ms=0.0,
            )

            assert score.relevance == expected_relevance

    def test_score_bounds(self, scorer):
        """All components stay within [0.0, 1.0]."""
        for error_rate in [0.0, 0.5, 1.0]:
            for stddev in [0.0, 1000.0, 5000.0]:
                score = scorer.score_skill(
                    skill_name="code_review",
                    task_type="code_review",
                    invocation_count=20,
                    error_rate=error_rate,
                    avg_latency_ms=1000.0,
                    latency_stddev_ms=stddev,
                )

                assert 0.0 <= score.relevance <= 1.0
                assert 0.0 <= score.reliability <= 1.0
                assert 0.0 <= score.combined <= 1.0
