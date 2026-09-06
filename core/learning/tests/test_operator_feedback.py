"""Tests for Operator Feedback Loop (Gap 7, ADR-0327).

Storage contract under test: ratings are ``learning_events.LearningEvent``
(EventType.FEEDBACK, skill_id ``tool:<id>`` / ``skill:<id>``) persisted by
``event_store.EventStore(tenant_home)`` — the ONE pair EventEmitter writes to.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.learning.operator_feedback import (
    RATING_KIND_SKILL,
    RATING_KIND_TOOL,
    FeedbackAggregator,
    FeedbackStats,
    OperatorFeedbackHandler,
    OutlierDetector,
    build_rating_event,
    skill_subject_id,
    tool_subject_id,
)


@pytest.fixture
def event_store(tmp_path: Path) -> EventStore:
    """EventStore rooted at a temp tenant home."""
    return EventStore(tmp_path / "tenants" / "_default")


@pytest.fixture
def feedback_handler(event_store) -> OperatorFeedbackHandler:
    return OperatorFeedbackHandler(event_store=event_store, min_sample_size=3)


def create_tool_rating_event(
    tool_id: str = "tool_1",
    tool_name: str = "TestTool",
    rating: int = 5,
    tenant_id: str = "_default",
    feedback_text: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> LearningEvent:
    ev = build_rating_event(
        kind=RATING_KIND_TOOL, entity_id=tool_id, entity_name=tool_name, rating=rating,
        tenant_id=tenant_id, feedback_text=feedback_text,
    )
    if timestamp is not None:
        ev = LearningEvent(**{**ev.to_dict(), "event_type": ev.event_type,
                              "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"})
    return ev


def create_skill_rating_event(
    skill_id: str = "skill_1",
    skill_name: str = "TestSkill",
    rating: int = 5,
    tenant_id: str = "_default",
) -> LearningEvent:
    return build_rating_event(
        kind=RATING_KIND_SKILL, entity_id=skill_id, entity_name=skill_name, rating=rating,
        tenant_id=tenant_id,
    )


def _feedback_events(store: EventStore, tenant_id: str = "_default", subject: Optional[str] = None):
    return store.query_events(tenant_id, event_type=EventType.FEEDBACK, skill_id=subject)


# ============================================================================
# OutlierDetector
# ============================================================================


class TestOutlierDetector:
    def test_no_outlier_with_insufficient_history(self):
        result = OutlierDetector.detect_outlier(rating=1, existing_ratings=[5, 5])
        assert not result.is_outlier and result.z_score is None

    def test_outlier_detection_with_high_z_score(self):
        result = OutlierDetector.detect_outlier(rating=5, existing_ratings=[1, 1, 1, 1, 1, 2])
        assert result.is_outlier and result.z_score is not None and result.z_score > 2.5

    def test_outlier_detection_with_low_z_score(self):
        result = OutlierDetector.detect_outlier(rating=1, existing_ratings=[5, 5, 5, 5, 5, 4])
        assert result.is_outlier and result.z_score < -2.5

    def test_no_outlier_normal_rating(self):
        result = OutlierDetector.detect_outlier(rating=4, existing_ratings=[3, 4, 5, 4, 3, 5])
        assert not result.is_outlier

    def test_outlier_with_zero_variance(self):
        result = OutlierDetector.detect_outlier(rating=1, existing_ratings=[5, 5, 5, 5, 5])
        assert result.is_outlier and result.z_score is None and not result.should_exclude

    def test_outlier_with_single_sample(self):
        result = OutlierDetector.detect_outlier(rating=1, existing_ratings=[5], minimum_sample_size=1)
        # stdev of a single sample raises → handled, not an outlier
        assert not result.is_outlier


# ============================================================================
# FeedbackAggregator
# ============================================================================


class TestFeedbackAggregator:
    def test_aggregate_single_rating(self):
        stats = FeedbackAggregator.aggregate_feedback("t", "tool", "T", [5])
        assert stats.sample_count == 1 and stats.average_rating == 5.0 and stats.std_dev is None

    def test_aggregate_multiple_ratings(self):
        stats = FeedbackAggregator.aggregate_feedback("t", "tool", "T", [5, 4, 5, 3, 5])
        assert stats.sample_count == 5 and 4.0 <= stats.average_rating <= 4.5
        assert stats.min_rating == 3 and stats.max_rating == 5 and stats.std_dev is not None

    def test_aggregate_empty_ratings(self):
        stats = FeedbackAggregator.aggregate_feedback("t", "tool", "T", [])
        assert stats.sample_count == 0 and stats.average_rating == 3.0 and stats.confidence == 0.0

    def test_confidence_thresholds(self):
        assert FeedbackAggregator.compute_confidence(1) == 0.2
        assert FeedbackAggregator.compute_confidence(10) == 0.8
        assert FeedbackAggregator.compute_confidence(100) == 1.0

    def test_sentiment_classification(self):
        assert FeedbackAggregator.classify_sentiment(1.5) == "negative"
        assert FeedbackAggregator.classify_sentiment(2.5) == "neutral"
        assert FeedbackAggregator.classify_sentiment(3.5) == "positive"
        assert FeedbackAggregator.classify_sentiment(4.5) == "very_positive"

    def test_median_calculation(self):
        stats = FeedbackAggregator.aggregate_feedback("t", "tool", "T", [1, 5, 3])
        assert stats.median_rating == 3.0

    def test_min_max_ratings(self):
        stats = FeedbackAggregator.aggregate_feedback("t", "tool", "T", [2, 4, 3])
        assert stats.min_rating == 2 and stats.max_rating == 4


# ============================================================================
# OperatorFeedbackHandler
# ============================================================================


class TestOperatorFeedbackHandler:
    def test_record_tool_rating(self, feedback_handler, event_store):
        ev = feedback_handler.record_tool_rating(
            tool_id="tool_1", tool_name="TestTool", rating=5, tenant_id="_default",
            feedback_text="Works great!",
        )
        events = _feedback_events(event_store, subject=tool_subject_id("tool_1"))
        assert len(events) == 1
        assert events[0].event_id == ev.event_id
        assert events[0].event_type == EventType.FEEDBACK
        assert events[0].signal["kind"] == RATING_KIND_TOOL
        assert events[0].signal["tool_id"] == "tool_1"
        assert events[0].signal["rating"] == 5
        assert events[0].lom, "LoM is recorded on the event"

    def test_record_tool_rating_invalid(self, feedback_handler, event_store):
        with pytest.raises(ValueError):
            feedback_handler.record_tool_rating(tool_id="tool_1", tool_name="T", rating=10)
        assert _feedback_events(event_store) == []

    def test_record_skill_rating(self, feedback_handler, event_store):
        feedback_handler.record_skill_rating(
            skill_id="skill_1", skill_name="TestSkill", rating=4, tenant_id="_default",
        )
        events = _feedback_events(event_store, subject=skill_subject_id("skill_1"))
        assert len(events) == 1
        assert events[0].signal["kind"] == RATING_KIND_SKILL
        assert events[0].signal["skill_id"] == "skill_1" and events[0].signal["rating"] == 4

    def test_record_skill_rating_invalid(self, feedback_handler):
        with pytest.raises(ValueError):
            feedback_handler.record_skill_rating(skill_id="skill_1", skill_name="S", rating=0)

    def test_record_through_event_emitter(self, event_store):
        emitter = EventEmitter(event_store)
        handler = OperatorFeedbackHandler(event_store=event_store, event_emitter=emitter)
        try:
            handler.record_tool_rating(tool_id="tool_e", tool_name="E", rating=5)
        finally:
            emitter.stop()  # flushes the queue
        events = _feedback_events(event_store, subject=tool_subject_id("tool_e"))
        assert len(events) == 1 and events[0].signal["rating"] == 5

    def test_dropped_emit_is_an_error_not_silence(self, event_store):
        class _Full:
            def emit(self, event):
                return False
        handler = OperatorFeedbackHandler(event_store=event_store, event_emitter=_Full())
        with pytest.raises(RuntimeError, match="dropped"):
            handler.record_tool_rating(tool_id="tool_x", tool_name="X", rating=5)

    def test_get_tool_feedback_stats(self, feedback_handler, event_store):
        for rating in [5, 5, 4, 5, 3]:
            event_store.write_event(create_tool_rating_event(tool_id="tool_1", rating=rating))
        stats = feedback_handler.get_tool_feedback_stats(tool_id="tool_1", tenant_id="_default")
        assert stats.entity_id == "tool_1" and stats.entity_name == "TestTool"
        assert stats.sample_count == 5
        assert 4.0 <= stats.average_rating <= 4.5
        assert stats.feedback_sentiment == "very_positive"

    def test_get_skill_feedback_stats(self, feedback_handler, event_store):
        for rating in [4, 4, 3, 4]:
            event_store.write_event(create_skill_rating_event(skill_id="skill_1", rating=rating))
        stats = feedback_handler.get_skill_feedback_stats(skill_id="skill_1", tenant_id="_default")
        assert stats.entity_id == "skill_1" and stats.sample_count == 4
        assert 3.5 <= stats.average_rating <= 4.0
        assert stats.feedback_sentiment == "positive"

    def test_feedback_stats_tenant_isolation(self, feedback_handler, event_store, monkeypatch):
        # Audit-first store: a record enters the core chain only under the
        # process tenant, so each tenant's event is written AS that tenant.
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_1")
        event_store.write_event(create_tool_rating_event(rating=5, tenant_id="tenant_1"))
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_2")
        event_store.write_event(create_tool_rating_event(rating=1, tenant_id="tenant_2"))
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
        stats1 = feedback_handler.get_tool_feedback_stats(tool_id="tool_1", tenant_id="tenant_1")
        stats2 = feedback_handler.get_tool_feedback_stats(tool_id="tool_1", tenant_id="tenant_2")
        assert stats1.average_rating == 5.0 and stats1.sample_count == 1
        assert stats2.average_rating == 1.0 and stats2.sample_count == 1
        # unknown tenant sees nothing
        assert feedback_handler.get_tool_feedback_stats("tool_1", tenant_id="tenant_3").sample_count == 0

    def test_feedback_stats_entity_isolation(self, feedback_handler, event_store):
        event_store.write_event(create_tool_rating_event(tool_id="tool_1", rating=5))
        event_store.write_event(create_tool_rating_event(tool_id="tool_2", rating=1))
        event_store.write_event(create_skill_rating_event(skill_id="tool_1", rating=1))  # a SKILL named tool_1
        stats = feedback_handler.get_tool_feedback_stats(tool_id="tool_1")
        assert stats.sample_count == 1 and stats.average_rating == 5.0

    def test_feedback_stats_time_window(self, feedback_handler, event_store):
        old_time = datetime.now(timezone.utc) - timedelta(days=8)
        event_store.write_event(create_tool_rating_event(rating=1, timestamp=old_time))
        event_store.write_event(create_tool_rating_event(rating=5))
        stats = feedback_handler.get_tool_feedback_stats(tool_id="tool_1", window_days=7)
        assert stats.sample_count == 1 and stats.average_rating == 5.0
        stats30 = feedback_handler.get_tool_feedback_stats(tool_id="tool_1", window_days=30, use_cache=False)
        assert stats30.sample_count == 2

    def test_compute_promotion_adjustment_very_positive(self, feedback_handler):
        stats = FeedbackStats(
            entity_id="tool_1", entity_type="tool", entity_name="T", sample_count=10,
            average_rating=4.8, median_rating=5.0, std_dev=0.4, min_rating=4, max_rating=5,
            confidence=0.8, window_days=7, timestamp_utc=datetime.now(timezone.utc),
            feedback_sentiment="very_positive",
        )
        adjusted, reason = feedback_handler.compute_promotion_adjustment(stats, base_threshold=0.7)
        assert adjusted < 0.7 and "very_positive" in reason

    def test_compute_promotion_adjustment_negative(self, feedback_handler):
        stats = FeedbackStats(
            entity_id="tool_1", entity_type="tool", entity_name="T", sample_count=10,
            average_rating=1.5, median_rating=1.0, std_dev=0.5, min_rating=1, max_rating=2,
            confidence=0.8, window_days=7, timestamp_utc=datetime.now(timezone.utc),
            feedback_sentiment="negative",
        )
        adjusted, reason = feedback_handler.compute_promotion_adjustment(stats, base_threshold=0.7)
        assert adjusted > 0.7 and "negative" in reason

    def test_compute_promotion_adjustment_insufficient_samples(self, feedback_handler):
        stats = FeedbackStats(
            entity_id="tool_1", entity_type="tool", entity_name="T", sample_count=1,
            average_rating=5.0, median_rating=5.0, std_dev=None, min_rating=5, max_rating=5,
            confidence=0.2, window_days=7, timestamp_utc=datetime.now(timezone.utc),
            feedback_sentiment="very_positive",
        )
        adjusted, reason = feedback_handler.compute_promotion_adjustment(stats, base_threshold=0.7)
        assert adjusted == 0.7 and "Insufficient" in reason

    def test_cache_invalidation(self, feedback_handler, event_store):
        event_store.write_event(create_tool_rating_event(rating=5))
        stats1 = feedback_handler.get_tool_feedback_stats("tool_1")
        assert stats1.sample_count == 1
        assert feedback_handler._is_cache_valid()

        feedback_handler.record_tool_rating(tool_id="tool_1", tool_name="TestTool", rating=4)
        assert not feedback_handler._is_cache_valid()

        stats2 = feedback_handler.get_tool_feedback_stats("tool_1")
        assert stats2.sample_count == 2
