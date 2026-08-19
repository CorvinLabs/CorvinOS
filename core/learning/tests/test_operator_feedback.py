"""Tests for Operator Feedback Loop Integration (Gap 7, ADR-0327)."""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from unittest.mock import Mock, patch
import tempfile

from core.learning.operator_feedback import (
    OperatorFeedbackHandler,
    FeedbackAggregator,
    OutlierDetector,
    FeedbackStats,
    OutlierStats,
)
from core.learning.event_schema import (
    LearningEvent,
    LearningEventType,
    OperatorRatedToolPayload,
    OperatorRatedSkillPayload,
)
from core.learning.event_store import EventStore


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_learning.db"
        yield db_path


@pytest.fixture
def event_store(temp_db):
    """Create EventStore for testing."""
    store = EventStore(temp_db)
    yield store


@pytest.fixture
def feedback_handler(event_store):
    """Create OperatorFeedbackHandler for testing."""
    handler = OperatorFeedbackHandler(event_store=event_store, min_sample_size=3)
    yield handler


def create_tool_rating_event(
    tool_id: str = "tool_1",
    tool_name: str = "TestTool",
    rating: int = 5,
    tenant_id: str = "_default",
    feedback_text: Optional[str] = None,
) -> LearningEvent:
    """Helper to create an OPERATOR_RATED_TOOL event."""
    return LearningEvent(
        event_type=LearningEventType.OPERATOR_RATED_TOOL,
        tenant_id=tenant_id,
        instance_id="test_instance",
        skill_name=None,
        session_id="session_1",
        timestamp_utc=datetime.now(timezone.utc),
        payload={
            "tool_id": tool_id,
            "tool_name": tool_name,
            "rating": rating,
            "feedback_text": feedback_text,
            "task_id": "task_1",
            "session_id": "session_1",
            "timestamp_utc": datetime.utcnow().isoformat(),
        },
    )


def create_skill_rating_event(
    skill_id: str = "skill_1",
    skill_name: str = "TestSkill",
    rating: int = 5,
    tenant_id: str = "_default",
    feedback_text: Optional[str] = None,
) -> LearningEvent:
    """Helper to create an OPERATOR_RATED_SKILL event."""
    return LearningEvent(
        event_type=LearningEventType.OPERATOR_RATED_SKILL,
        tenant_id=tenant_id,
        instance_id="test_instance",
        skill_name=skill_name,
        session_id="session_1",
        timestamp_utc=datetime.now(timezone.utc),
        payload={
            "skill_id": skill_id,
            "skill_name": skill_name,
            "rating": rating,
            "feedback_text": feedback_text,
            "task_id": "task_1",
            "session_id": "session_1",
            "timestamp_utc": datetime.utcnow().isoformat(),
        },
    )


# ============================================================================
# Tests for OutlierDetector
# ============================================================================


class TestOutlierDetector:
    """Tests for outlier detection logic."""

    def test_no_outlier_with_insufficient_history(self):
        """Outlier detection disabled when history < minimum_sample_size."""
        result = OutlierDetector.detect_outlier(
            rating=1,
            existing_ratings=[5, 5, 5, 5],  # Only 4 samples, threshold is 5
            minimum_sample_size=5,
        )
        assert result.is_outlier is False
        assert "Insufficient" in result.reason

    def test_outlier_detection_with_high_z_score(self):
        """High Z-score rating flagged as outlier."""
        result = OutlierDetector.detect_outlier(
            rating=5,
            existing_ratings=[1, 1, 1, 1, 1, 2, 1, 2],  # Average ~1.2
            minimum_sample_size=5,
        )
        assert result.is_outlier is True
        assert result.z_score is not None
        assert abs(result.z_score) > OutlierDetector.Z_SCORE_THRESHOLD

    def test_outlier_detection_with_low_z_score(self):
        """Low Z-score rating flagged as outlier."""
        result = OutlierDetector.detect_outlier(
            rating=1,
            existing_ratings=[5, 5, 5, 5, 5, 4, 5, 4],  # Average ~4.75
            minimum_sample_size=5,
        )
        assert result.is_outlier is True
        assert result.z_score is not None
        assert abs(result.z_score) > OutlierDetector.Z_SCORE_THRESHOLD

    def test_no_outlier_normal_rating(self):
        """Normal rating within Z-score threshold."""
        result = OutlierDetector.detect_outlier(
            rating=4,
            existing_ratings=[3, 4, 5, 4, 3, 4, 5, 4],  # Average ~4
            minimum_sample_size=5,
        )
        assert result.is_outlier is False
        assert result.z_score is not None
        assert abs(result.z_score) <= OutlierDetector.Z_SCORE_THRESHOLD

    def test_outlier_with_zero_variance(self):
        """Rating differs from uniform existing data."""
        result = OutlierDetector.detect_outlier(
            rating=5,
            existing_ratings=[3, 3, 3, 3, 3],
            minimum_sample_size=5,
        )
        # Should flag as outlier since it differs significantly from uniform data
        assert result.is_outlier is True

    def test_outlier_with_single_sample(self):
        """Single sample insufficient for reliable outlier detection."""
        result = OutlierDetector.detect_outlier(
            rating=5,
            existing_ratings=[3],
            minimum_sample_size=5,
        )
        assert result.is_outlier is False


# ============================================================================
# Tests for FeedbackAggregator
# ============================================================================


class TestFeedbackAggregator:
    """Tests for feedback aggregation logic."""

    def test_aggregate_single_rating(self):
        """Aggregation with single rating."""
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            ratings=[5],
        )
        assert stats.sample_count == 1
        assert stats.average_rating == 5.0
        assert stats.median_rating == 5.0
        assert stats.std_dev is None
        assert stats.confidence == 0.2

    def test_aggregate_multiple_ratings(self):
        """Aggregation with multiple ratings."""
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            ratings=[4, 5, 3, 5, 4],
        )
        assert stats.sample_count == 5
        assert 4.0 <= stats.average_rating <= 4.2
        assert stats.std_dev is not None
        assert stats.confidence == 0.6

    def test_aggregate_empty_ratings(self):
        """Aggregation with no ratings."""
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            ratings=[],
        )
        assert stats.sample_count == 0
        assert stats.average_rating == 3.0  # Neutral default
        assert stats.confidence == 0.0

    def test_confidence_thresholds(self):
        """Confidence increases with sample count."""
        confidences = []
        for count in [1, 2, 5, 10, 20, 30]:
            confidence = FeedbackAggregator.compute_confidence(count)
            confidences.append(confidence)
        # Confidence should be increasing
        assert confidences == sorted(confidences)

    def test_sentiment_classification(self):
        """Sentiment classification based on average rating."""
        assert FeedbackAggregator.classify_sentiment(1.5) == "negative"
        assert FeedbackAggregator.classify_sentiment(2.5) == "neutral"
        assert FeedbackAggregator.classify_sentiment(3.5) == "positive"
        assert FeedbackAggregator.classify_sentiment(4.5) == "very_positive"

    def test_median_calculation(self):
        """Median computed correctly from sorted ratings."""
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            ratings=[1, 2, 3, 4, 5],
        )
        assert stats.median_rating == 3.0

    def test_min_max_ratings(self):
        """Min/max ratings captured."""
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            ratings=[1, 2, 5, 3, 4],
        )
        assert stats.min_rating == 1
        assert stats.max_rating == 5


# ============================================================================
# Tests for OperatorFeedbackHandler
# ============================================================================


class TestOperatorFeedbackHandler:
    """Tests for feedback handler subsystem."""

    @pytest.mark.asyncio
    async def test_record_tool_rating(self, feedback_handler, event_store):
        """Record a tool rating event."""
        await feedback_handler.record_tool_rating(
            tool_id="tool_1",
            tool_name="TestTool",
            rating=5,
            tenant_id="_default",
            feedback_text="Works great!",
        )
        # Verify event was persisted
        events = event_store.read_events_by_type(
            event_type=LearningEventType.OPERATOR_RATED_TOOL
        )
        assert len(events) == 1
        assert events[0].payload["tool_id"] == "tool_1"
        assert events[0].payload["rating"] == 5

    @pytest.mark.asyncio
    async def test_record_tool_rating_invalid(self, feedback_handler):
        """Tool rating validation (1-5 range)."""
        with pytest.raises(ValueError):
            await feedback_handler.record_tool_rating(
                tool_id="tool_1",
                tool_name="TestTool",
                rating=10,  # Invalid
                tenant_id="_default",
            )

    @pytest.mark.asyncio
    async def test_record_skill_rating(self, feedback_handler, event_store):
        """Record a skill rating event."""
        await feedback_handler.record_skill_rating(
            skill_id="skill_1",
            skill_name="TestSkill",
            rating=4,
            tenant_id="_default",
            feedback_text="Good but could improve",
        )
        # Verify event was persisted
        events = event_store.read_events_by_type(
            event_type=LearningEventType.OPERATOR_RATED_SKILL
        )
        assert len(events) == 1
        assert events[0].payload["skill_id"] == "skill_1"
        assert events[0].payload["rating"] == 4

    @pytest.mark.asyncio
    async def test_record_skill_rating_invalid(self, feedback_handler):
        """Skill rating validation (1-5 range)."""
        with pytest.raises(ValueError):
            await feedback_handler.record_skill_rating(
                skill_id="skill_1",
                skill_name="TestSkill",
                rating=0,  # Invalid
                tenant_id="_default",
            )

    def test_get_tool_feedback_stats(self, feedback_handler, event_store):
        """Retrieve aggregated tool feedback statistics."""
        # Create multiple rating events
        for rating in [5, 5, 4, 5, 3]:
            event = create_tool_rating_event(tool_id="tool_1", rating=rating)
            event_store.write_event(event)

        # Get aggregated stats
        stats = feedback_handler.get_tool_feedback_stats(
            tool_id="tool_1",
            tenant_id="_default",
        )

        assert stats.entity_id == "tool_1"
        assert stats.sample_count == 5
        assert 4.0 <= stats.average_rating <= 4.5
        assert stats.feedback_sentiment == "very_positive"

    def test_get_skill_feedback_stats(self, feedback_handler, event_store):
        """Retrieve aggregated skill feedback statistics."""
        # Create multiple rating events
        for rating in [4, 4, 3, 4]:
            event = create_skill_rating_event(skill_id="skill_1", rating=rating)
            event_store.write_event(event)

        # Get aggregated stats
        stats = feedback_handler.get_skill_feedback_stats(
            skill_id="skill_1",
            tenant_id="_default",
        )

        assert stats.entity_id == "skill_1"
        assert stats.sample_count == 4
        assert 3.5 <= stats.average_rating <= 4.0
        assert stats.feedback_sentiment == "positive"

    def test_feedback_stats_tenant_isolation(self, feedback_handler, event_store):
        """Feedback stats respect tenant isolation."""
        # Create ratings for two different tenants
        event1 = create_tool_rating_event(rating=5, tenant_id="tenant_1")
        event2 = create_tool_rating_event(rating=1, tenant_id="tenant_2")
        event_store.write_event(event1)
        event_store.write_event(event2)

        # Get stats for each tenant
        stats1 = feedback_handler.get_tool_feedback_stats(
            tool_id="tool_1",
            tenant_id="tenant_1",
        )
        stats2 = feedback_handler.get_tool_feedback_stats(
            tool_id="tool_1",
            tenant_id="tenant_2",
        )

        # Each tenant should see only their own ratings
        assert stats1.average_rating == 5.0
        assert stats2.average_rating == 1.0

    def test_feedback_stats_time_window(self, feedback_handler, event_store):
        """Feedback stats respect time window filter."""
        # Create old rating (8 days ago)
        old_time = datetime.now(timezone.utc) - timedelta(days=8)
        old_event = LearningEvent(
            event_type=LearningEventType.OPERATOR_RATED_TOOL,
            tenant_id="_default",
            instance_id="test",
            skill_name=None,
            session_id="session_1",
            timestamp_utc=old_time,
            payload={
                "tool_id": "tool_1",
                "tool_name": "TestTool",
                "rating": 1,  # Poor old rating
                "feedback_text": None,
            },
        )
        # Create recent rating
        recent_event = create_tool_rating_event(rating=5)

        event_store.write_event(old_event)
        event_store.write_event(recent_event)

        # Get stats for 7-day window (default)
        stats = feedback_handler.get_tool_feedback_stats(
            tool_id="tool_1",
            tenant_id="_default",
            window_days=7,
        )

        # Should only see recent rating
        assert stats.sample_count == 1
        assert stats.average_rating == 5.0

    def test_compute_promotion_adjustment_very_positive(self, feedback_handler):
        """Very positive feedback lowers promotion threshold."""
        stats = FeedbackStats(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            sample_count=10,
            average_rating=4.8,
            median_rating=5.0,
            std_dev=0.2,
            min_rating=4,
            max_rating=5,
            confidence=0.9,
            window_days=7,
            timestamp_utc=datetime.now(timezone.utc),
            feedback_sentiment="very_positive",
        )

        adjusted, reason = feedback_handler.compute_promotion_adjustment(
            stats,
            base_threshold=0.7,
        )

        # Should lower threshold (make promotion easier)
        assert adjusted < 0.7
        assert "very_positive" in reason

    def test_compute_promotion_adjustment_negative(self, feedback_handler):
        """Negative feedback raises promotion threshold."""
        stats = FeedbackStats(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            sample_count=10,
            average_rating=2.1,
            median_rating=2.0,
            std_dev=0.5,
            min_rating=1,
            max_rating=3,
            confidence=0.8,
            window_days=7,
            timestamp_utc=datetime.now(timezone.utc),
            feedback_sentiment="negative",
        )

        adjusted, reason = feedback_handler.compute_promotion_adjustment(
            stats,
            base_threshold=0.7,
        )

        # Should raise threshold (make promotion harder)
        assert adjusted > 0.7
        assert "negative" in reason

    def test_compute_promotion_adjustment_insufficient_samples(self, feedback_handler):
        """Insufficient samples = no adjustment."""
        stats = FeedbackStats(
            entity_id="tool_1",
            entity_type="tool",
            entity_name="TestTool",
            sample_count=1,  # Below min_sample_size (3)
            average_rating=5.0,
            median_rating=5.0,
            std_dev=None,
            min_rating=5,
            max_rating=5,
            confidence=0.2,
            window_days=7,
            timestamp_utc=datetime.now(timezone.utc),
            feedback_sentiment="very_positive",
        )

        adjusted, reason = feedback_handler.compute_promotion_adjustment(
            stats,
            base_threshold=0.7,
        )

        # Should not adjust with insufficient samples
        assert adjusted == 0.7
        assert "Insufficient" in reason

    def test_cache_invalidation(self, feedback_handler, event_store):
        """Cache is invalidated when new ratings are recorded."""
        # Pre-populate cache
        event = create_tool_rating_event(rating=5)
        event_store.write_event(event)
        stats1 = feedback_handler.get_tool_feedback_stats("tool_1")
        assert stats1.sample_count == 1

        # Cache should be valid
        assert feedback_handler._is_cache_valid()

        # Record new rating (should invalidate cache)
        import asyncio
        asyncio.run(
            feedback_handler.record_tool_rating(
                tool_id="tool_1",
                tool_name="TestTool",
                rating=4,
            )
        )

        # Cache should be invalid
        assert not feedback_handler._is_cache_valid()

        # Fresh query should see new rating
        stats2 = feedback_handler.get_tool_feedback_stats("tool_1")
        assert stats2.sample_count == 2


# ============================================================================
# Integration Tests
# ============================================================================


class TestOperatorFeedbackIntegration:
    """Integration tests combining multiple components."""

    def test_full_feedback_loop_tool(self, feedback_handler, event_store):
        """Full feedback loop: rate tool → aggregate → compute adjustment."""
        import asyncio

        # Step 1: Record multiple tool ratings
        for rating in [5, 5, 4, 5]:
            asyncio.run(
                feedback_handler.record_tool_rating(
                    tool_id="my_tool",
                    tool_name="MyTool",
                    rating=rating,
                )
            )

        # Step 2: Get aggregated feedback
        stats = feedback_handler.get_tool_feedback_stats("my_tool")
        assert stats.sample_count == 4
        assert 4.5 <= stats.average_rating <= 4.75
        assert stats.feedback_sentiment == "very_positive"

        # Step 3: Compute promotion adjustment
        adjusted_threshold, reason = feedback_handler.compute_promotion_adjustment(
            stats,
            base_threshold=0.7,
        )
        # Very positive feedback should lower threshold
        assert adjusted_threshold < 0.7
        assert "very_positive" in reason

    def test_full_feedback_loop_skill(self, feedback_handler, event_store):
        """Full feedback loop: rate skill → aggregate → compute adjustment."""
        import asyncio

        # Step 1: Record multiple skill ratings
        for rating in [2, 2, 3, 2]:
            asyncio.run(
                feedback_handler.record_skill_rating(
                    skill_id="my_skill",
                    skill_name="MySkill",
                    rating=rating,
                )
            )

        # Step 2: Get aggregated feedback
        stats = feedback_handler.get_skill_feedback_stats("my_skill")
        assert stats.sample_count == 4
        assert 2.0 <= stats.average_rating <= 2.5
        assert stats.feedback_sentiment == "negative"

        # Step 3: Compute promotion adjustment
        adjusted_threshold, reason = feedback_handler.compute_promotion_adjustment(
            stats,
            base_threshold=0.7,
        )
        # Negative feedback should raise threshold
        assert adjusted_threshold > 0.7
        assert "negative" in reason

    def test_mixed_feedback_neutral(self, feedback_handler, event_store):
        """Mixed feedback (neutral average) → no adjustment."""
        import asyncio

        # Record mixed ratings averaging to neutral
        for rating in [2, 3, 4, 4, 3]:
            asyncio.run(
                feedback_handler.record_tool_rating(
                    tool_id="neutral_tool",
                    tool_name="NeutralTool",
                    rating=rating,
                )
            )

        stats = feedback_handler.get_tool_feedback_stats("neutral_tool")
        assert 3.0 <= stats.average_rating <= 3.5
        assert stats.feedback_sentiment == "neutral"

        adjusted_threshold, _ = feedback_handler.compute_promotion_adjustment(
            stats,
            base_threshold=0.7,
        )
        # Neutral feedback should not significantly adjust
        assert 0.65 <= adjusted_threshold <= 0.75
