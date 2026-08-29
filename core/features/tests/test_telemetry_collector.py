"""Tests for feature telemetry collection."""
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from core.features.telemetry_collector import (
    EventType,
    HourlyAggregate,
    TelemetryCollector,
    TelemetryEvent,
)


class TestTelemetryEvent:
    """TelemetryEvent validation tests."""

    def test_create_usage_started_event(self):
        """Test creating a USAGE_STARTED event."""
        event = TelemetryEvent(
            feature_id="my_feature",
            event_type=EventType.USAGE_STARTED,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
        assert event.feature_id == "my_feature"
        assert event.event_type == EventType.USAGE_STARTED
        assert event.validate_pii_safe()

    def test_event_with_duration(self):
        """Test event with duration metadata."""
        event = TelemetryEvent(
            feature_id="my_feature",
            event_type=EventType.USAGE_STOPPED,
            timestamp=datetime.utcnow().isoformat() + "Z",
            duration_ms=1250,
        )
        assert event.duration_ms == 1250

    def test_event_with_feedback(self):
        """Test event with feedback score."""
        event = TelemetryEvent(
            feature_id="my_feature",
            event_type=EventType.FEEDBACK_PROVIDED,
            timestamp=datetime.utcnow().isoformat() + "Z",
            feedback_score=0.85,
        )
        assert event.feedback_score == 0.85

    def test_event_pii_detection_password_keyword(self):
        """Test PII detection for password keyword in error_type."""
        event = TelemetryEvent(
            feature_id="my_feature",
            event_type=EventType.ERROR,
            timestamp=datetime.utcnow().isoformat() + "Z",
            error_type="PasswordValidationError",
        )
        assert not event.validate_pii_safe()

    def test_event_pii_detection_token_keyword(self):
        """Test PII detection for token keyword."""
        event = TelemetryEvent(
            feature_id="my_feature",
            event_type=EventType.ERROR,
            timestamp=datetime.utcnow().isoformat() + "Z",
            error_type="InvalidTokenException",
        )
        assert not event.validate_pii_safe()

    def test_event_to_dict(self):
        """Test event serialization."""
        event = TelemetryEvent(
            feature_id="test_feat",
            event_type=EventType.USAGE_STARTED,
            timestamp="2026-08-29T15:00:00Z",
            duration_ms=500,
        )
        d = event.to_dict()
        assert d["feature_id"] == "test_feat"
        assert d["event_type"] == EventType.USAGE_STARTED
        assert d["duration_ms"] == 500


class TestHourlyAggregate:
    """HourlyAggregate computation tests."""

    def test_create_aggregate(self):
        """Test creating an aggregate."""
        agg = HourlyAggregate(
            feature_id="test",
            hour_start="2026-08-29T15:00:00Z",
            usage_count=100,
            error_count=5,
        )
        assert agg.feature_id == "test"
        assert agg.usage_count == 100
        assert agg.error_count == 5

    def test_compute_error_rate(self):
        """Test error rate computation."""
        agg = HourlyAggregate(
            feature_id="test",
            hour_start="2026-08-29T15:00:00Z",
            usage_count=200,
            error_count=10,
        )
        assert agg.compute_error_rate() == 0.05  # 10/200

    def test_compute_error_rate_zero_usage(self):
        """Test error rate with zero usage."""
        agg = HourlyAggregate(
            feature_id="test",
            hour_start="2026-08-29T15:00:00Z",
            usage_count=0,
            error_count=0,
        )
        assert agg.compute_error_rate() == 0.0

    def test_compute_avg_feedback(self):
        """Test average feedback computation."""
        agg = HourlyAggregate(
            feature_id="test",
            hour_start="2026-08-29T15:00:00Z",
            feedback_count=4,
            feedback_sum=3.2,  # avg 0.8
        )
        assert agg.compute_avg_feedback() == 0.8

    def test_compute_avg_feedback_zero_count(self):
        """Test average feedback with zero count."""
        agg = HourlyAggregate(
            feature_id="test",
            hour_start="2026-08-29T15:00:00Z",
            feedback_count=0,
            feedback_sum=0.0,
        )
        assert agg.compute_avg_feedback() == 0.0

    def test_aggregate_to_dict(self):
        """Test aggregate serialization."""
        agg = HourlyAggregate(
            feature_id="test",
            hour_start="2026-08-29T15:00:00Z",
            usage_count=50,
        )
        d = agg.to_dict()
        assert d["feature_id"] == "test"
        assert d["usage_count"] == 50


class TestTelemetryCollector:
    """TelemetryCollector tests."""

    @pytest.fixture
    def collector(self):
        """Create a collector with temp storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            c = TelemetryCollector(
                storage_path=Path(tmpdir) / "telemetry",
                enabled=True,
            )
            yield c
            c.reset()

    def test_collector_initialization(self, collector):
        """Test collector initialization."""
        assert collector.enabled
        assert collector.retention_days == 90
        assert collector.storage_path.exists()

    def test_collect_event_usage_started(self, collector):
        """Test collecting a USAGE_STARTED event."""
        result = collector.collect_event(
            feature_id="test_feature",
            event_type=EventType.USAGE_STARTED,
            duration_ms=100,
        )
        assert result is True
        assert len(collector._events) == 1

    def test_collect_event_with_feedback(self, collector):
        """Test collecting a FEEDBACK event."""
        result = collector.collect_event(
            feature_id="test_feature",
            event_type=EventType.FEEDBACK_PROVIDED,
            feedback_score=0.9,
        )
        assert result is True
        assert collector._events[0].feedback_score == 0.9

    def test_collect_event_with_error(self, collector):
        """Test collecting an ERROR event."""
        result = collector.collect_event(
            feature_id="test_feature",
            event_type=EventType.ERROR,
            error_type="ValueError",
        )
        assert result is True

    def test_collect_event_pii_detected(self, collector):
        """Test that events with PII are dropped."""
        result = collector.collect_event(
            feature_id="test_feature",
            event_type=EventType.ERROR,
            error_type="AuthenticationTokenError",
        )
        assert result is False
        assert len(collector._events) == 0

    def test_collect_multiple_events_aggregate(self, collector):
        """Test collecting multiple events and aggregation."""
        collector.collect_event("feat1", EventType.USAGE_STARTED)
        collector.collect_event("feat1", EventType.USAGE_STARTED)
        collector.collect_event("feat1", EventType.ERROR)
        collector.collect_event("feat1", EventType.FEEDBACK_PROVIDED, feedback_score=0.8)

        assert len(collector._events) == 4
        # Should have one aggregate (same hour)
        assert len(collector._aggregates) == 1

        # Get aggregate
        agg_key = list(collector._aggregates.keys())[0]
        agg = collector._aggregates[agg_key]
        assert agg.usage_count == 2
        assert agg.error_count == 1
        assert agg.feedback_count == 1

    def test_get_hourly_aggregate(self, collector):
        """Test retrieving a specific hourly aggregate."""
        collector.collect_event("feat1", EventType.USAGE_STARTED)
        collector.collect_event("feat1", EventType.ERROR)

        # Get the hour_start from internal aggregate
        agg_key = list(collector._aggregates.keys())[0]
        hour_start = agg_key[1]

        retrieved = collector.get_hourly_aggregate("feat1", hour_start)
        assert retrieved is not None
        assert retrieved.usage_count == 1
        assert retrieved.error_count == 1

    def test_get_24h_aggregates(self, collector):
        """Test retrieving 24h aggregates."""
        collector.collect_event("feat1", EventType.USAGE_STARTED)
        collector.collect_event("feat1", EventType.USAGE_STARTED)

        aggregates = collector.get_24h_aggregates("feat1")
        assert len(aggregates) >= 1
        assert aggregates[0].usage_count == 2

    def test_persist_to_jsonl(self, collector):
        """Test persisting events to JSONL."""
        collector.collect_event("feat1", EventType.USAGE_STARTED)
        collector.collect_event("feat1", EventType.FEEDBACK_PROVIDED, feedback_score=0.85)

        count = collector.persist_to_jsonl()
        assert count == 2

        # Verify file was written
        jsonl_path = collector.storage_path / "events__default.jsonl"
        assert jsonl_path.exists()

        # Read back and verify
        with open(jsonl_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 2

        event1 = json.loads(lines[0])
        assert event1["feature_id"] == "feat1"
        assert event1["event_type"] == EventType.USAGE_STARTED

        # In-memory events should be cleared
        assert len(collector._events) == 0

    def test_persist_empty_events(self, collector):
        """Test persisting when no events exist."""
        count = collector.persist_to_jsonl()
        assert count == 0

    def test_disabled_collector(self):
        """Test that disabled collector does nothing."""
        collector = TelemetryCollector(enabled=False)
        result = collector.collect_event("feat1", EventType.USAGE_STARTED)
        assert result is False
        assert len(collector._events) == 0

    def test_cleanup_old_events(self, collector):
        """Test cleaning up old events (retention window)."""
        # Write some events with old timestamps
        old_event = TelemetryEvent(
            feature_id="feat1",
            event_type=EventType.USAGE_STARTED,
            timestamp="2026-06-01T12:00:00Z",  # 90 days old
        )

        recent_event = TelemetryEvent(
            feature_id="feat1",
            event_type=EventType.USAGE_STARTED,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

        # Write to file manually
        jsonl_path = collector.storage_path / "events__default.jsonl"
        with open(jsonl_path, "w") as f:
            f.write(old_event.to_json() + "\n")
            f.write(recent_event.to_json() + "\n")

        # Run cleanup
        deleted = collector.cleanup_old_events()
        assert deleted >= 1

        # Verify recent event still exists
        with open(jsonl_path, "r") as f:
            lines = f.readlines()
        # Should have at least 1 line (the recent event)
        assert len(lines) >= 1

    def test_invalid_event_type(self, collector):
        """Test that invalid event types are rejected."""
        result = collector.collect_event(
            feature_id="feat1",
            event_type="INVALID_TYPE",
        )
        assert result is False

    def test_collector_reset(self, collector):
        """Test resetting collector state."""
        collector.collect_event("feat1", EventType.USAGE_STARTED)
        assert len(collector._events) > 0

        collector.reset()
        assert len(collector._events) == 0
        assert len(collector._aggregates) == 0

    def test_collect_event_with_user_count(self, collector):
        """Test collecting event with user count metadata."""
        collector.collect_event(
            "feat1",
            EventType.USAGE_STARTED,
            user_count=1500,
        )
        agg_key = list(collector._aggregates.keys())[0]
        agg = collector._aggregates[agg_key]
        assert agg.user_count_estimate == 1500

    def test_collect_event_with_region_tier(self, collector):
        """Test collecting event with region tier."""
        result = collector.collect_event(
            "feat1",
            EventType.USAGE_STARTED,
            region_tier="tier1",
        )
        assert result is True
        assert collector._events[0].region_tier == "tier1"
