"""k=2 Tests: ConfidenceScoreboard — State Management (Tier-1/2)."""

import pytest
from datetime import datetime, timezone

from core.learning.confidence_scoreboard import (
    ConfidenceScore,
    ConfidenceTrend,
    ConfidenceScoreboard,
)


class TestConfidenceScoreCreation:
    """Tier-1: Unit tests for ConfidenceScore."""

    def test_valid_score_creation(self):
        """Create a valid confidence score."""
        score = ConfidenceScore(
            score_id="score_001",
            window_id="win_001",
            tenant_id="_default",
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            confidence=0.85,
            sample_count=100,
            timestamp="2026-09-27T12:00:00Z",
        )

        assert score.score_id == "score_001"
        assert score.confidence == 0.85
        assert score.sample_count == 100

    def test_invalid_confidence_too_high(self):
        """Confidence > 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="Confidence must be in"):
            ConfidenceScore(
                score_id="score_002",
                window_id="win_002",
                tenant_id="_default",
                task_id="task-123",
                model_id="claude-opus-5",
                pattern_key="task_completion_rate",
                confidence=1.5,
                sample_count=100,
                timestamp="2026-09-27T12:00:00Z",
            )

    def test_invalid_confidence_negative(self):
        """Confidence < 0.0 raises ValueError."""
        with pytest.raises(ValueError, match="Confidence must be in"):
            ConfidenceScore(
                score_id="score_003",
                window_id="win_003",
                tenant_id="_default",
                task_id="task-123",
                model_id="claude-opus-5",
                pattern_key="task_completion_rate",
                confidence=-0.1,
                sample_count=100,
                timestamp="2026-09-27T12:00:00Z",
            )

    def test_invalid_sample_count_zero(self):
        """sample_count < 1 raises ValueError."""
        with pytest.raises(ValueError, match="sample_count must be >= 1"):
            ConfidenceScore(
                score_id="score_004",
                window_id="win_004",
                tenant_id="_default",
                task_id="task-123",
                model_id="claude-opus-5",
                pattern_key="task_completion_rate",
                confidence=0.5,
                sample_count=0,
                timestamp="2026-09-27T12:00:00Z",
            )

    def test_score_immutability(self):
        """ConfidenceScore is frozen (immutable)."""
        score = ConfidenceScore(
            score_id="score_005",
            window_id="win_005",
            tenant_id="_default",
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            confidence=0.75,
            sample_count=50,
            timestamp="2026-09-27T12:00:00Z",
        )

        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            score.confidence = 0.8


class TestConfidenceTrend:
    """Tier-1: Unit tests for ConfidenceTrend."""

    def test_trend_creation(self):
        """Create a confidence trend."""
        trend = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.82,
            std_dev=0.05,
            trend_direction="improving",
            last_updated="2026-09-27T12:00:00Z",
        )

        assert trend.n_samples == 10
        assert trend.mean_confidence == 0.82

    def test_trend_statistically_valid(self):
        """Trend with n >= 10 is significant."""
        trend_valid = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.82,
            std_dev=0.05,
            trend_direction="stable",
            last_updated="2026-09-27T12:00:00Z",
        )

        trend_invalid = ConfidenceTrend(
            task_id="task-124",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=5,
            mean_confidence=0.82,
            std_dev=0.05,
            trend_direction="stable",
            last_updated="2026-09-27T12:00:00Z",
        )

        assert trend_valid.is_significant() is True
        assert trend_invalid.is_significant() is False

    def test_trend_meets_threshold(self):
        """Trend meets optimizer threshold if mean >= threshold."""
        trend_good = ConfidenceTrend(
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.80,
            std_dev=0.05,
            trend_direction="stable",
            last_updated="2026-09-27T12:00:00Z",
        )

        trend_bad = ConfidenceTrend(
            task_id="task-124",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            n_samples=10,
            mean_confidence=0.50,
            std_dev=0.1,
            trend_direction="degrading",
            last_updated="2026-09-27T12:00:00Z",
        )

        assert trend_good.meets_threshold(0.75) is True
        assert trend_bad.meets_threshold(0.75) is False


class TestConfidenceScoreboard:
    """Tier-1/2: Unit tests for ConfidenceScoreboard."""

    def test_scoreboard_init(self):
        """Initialize a scoreboard."""
        scoreboard = ConfidenceScoreboard(window_size=10, trend_threshold=0.75)

        assert scoreboard.window_size == 10
        assert scoreboard.trend_threshold == 0.75
        assert len(scoreboard._scores) == 0
        assert len(scoreboard._trends) == 0

    def test_scoreboard_window_size_clamped(self):
        """Window size is clamped to [3, 100]."""
        sb_small = ConfidenceScoreboard(window_size=1)
        assert sb_small.window_size == 3

        sb_large = ConfidenceScoreboard(window_size=500)
        assert sb_large.window_size == 100

    def test_scoreboard_threshold_clamped(self):
        """Threshold is clamped to [0.0, 1.0]."""
        sb_low = ConfidenceScoreboard(trend_threshold=-0.5)
        assert sb_low.trend_threshold == 0.0

        sb_high = ConfidenceScoreboard(trend_threshold=1.5)
        assert sb_high.trend_threshold == 1.0

    @pytest.mark.asyncio
    async def test_write_score(self):
        """Write a confidence score."""
        scoreboard = ConfidenceScoreboard()

        score_id = await scoreboard.write_score(
            window_id="win_001",
            tenant_id="_default",
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            confidence=0.85,
            sample_count=100,
        )

        assert score_id.startswith("score_")
        assert score_id in scoreboard._scores

        score = scoreboard._scores[score_id]
        assert score.confidence == 0.85
        assert score.sample_count == 100

    @pytest.mark.asyncio
    async def test_write_score_invalid(self):
        """Writing invalid score raises ValueError."""
        scoreboard = ConfidenceScoreboard()

        with pytest.raises(ValueError):
            await scoreboard.write_score(
                window_id="win_001",
                tenant_id="_default",
                task_id="task-123",
                model_id="claude-opus-5",
                pattern_key="task_completion_rate",
                confidence=1.5,  # Invalid
                sample_count=100,
            )

    @pytest.mark.asyncio
    async def test_get_scores_for_task(self):
        """Retrieve all scores for a task."""
        scoreboard = ConfidenceScoreboard()

        # Write multiple scores for same task
        for i in range(3):
            await scoreboard.write_score(
                window_id=f"win_{i}",
                tenant_id="_default",
                task_id="task-123",
                model_id="claude-opus-5",
                pattern_key="task_completion_rate",
                confidence=0.80 + i * 0.05,
                sample_count=100,
            )

        # Write a score for a different task
        await scoreboard.write_score(
            window_id="win_other",
            tenant_id="_default",
            task_id="task-999",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            confidence=0.90,
            sample_count=100,
        )

        scores = await scoreboard.get_scores_for_task("task-123")
        assert len(scores) == 3

        scores_other = await scoreboard.get_scores_for_task("task-999")
        assert len(scores_other) == 1

    @pytest.mark.asyncio
    async def test_list_triggerable_trends(self):
        """List trends that meet optimizer threshold."""
        scoreboard = ConfidenceScoreboard(trend_threshold=0.75)

        # Write high-confidence scores
        for i in range(10):
            await scoreboard.write_score(
                window_id=f"win_good_{i}",
                tenant_id="_default",
                task_id="task-good",
                model_id="claude-opus-5",
                pattern_key="task_completion_rate",
                confidence=0.80 + i * 0.01,
                sample_count=50,
            )

        # Write low-confidence scores
        for i in range(10):
            await scoreboard.write_score(
                window_id=f"win_bad_{i}",
                tenant_id="_default",
                task_id="task-bad",
                model_id="claude-sonnet-5",
                pattern_key="error_rate",
                confidence=0.40 + i * 0.01,
                sample_count=50,
            )

        triggerable = await scoreboard.list_triggerable_trends()

        # Should include task-good trend (mean >= 0.75), not task-bad
        triggerable_keys = {(t.task_id, t.model_id, t.pattern_key) for t in triggerable}

        assert ("task-good", "claude-opus-5", "task_completion_rate") in triggerable_keys
        assert ("task-bad", "claude-sonnet-5", "error_rate") not in triggerable_keys

    @pytest.mark.asyncio
    async def test_clear_cache(self):
        """Clear in-memory cache."""
        scoreboard = ConfidenceScoreboard()

        # Write scores
        await scoreboard.write_score(
            window_id="win_001",
            tenant_id="_default",
            task_id="task-123",
            model_id="claude-opus-5",
            pattern_key="task_completion_rate",
            confidence=0.85,
            sample_count=100,
        )

        assert len(scoreboard._scores) > 0

        # Clear cache
        await scoreboard.clear_cache()

        assert len(scoreboard._scores) == 0
        assert len(scoreboard._trends) == 0
