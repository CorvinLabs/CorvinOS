"""Tests for Metrics Collection (ADR-0320)."""

import pytest
from core.learning.metrics import (
    MetricRecord,
    MetricsCollector,
    MetricType,
)


class TestMetricRecord:
    """Test metric record model."""

    def test_create_accuracy_record(self):
        """Create an accuracy metric record."""
        from datetime import datetime

        record = MetricRecord(
            metric_id="m1",
            metric_type=MetricType.ACCURACY,
            value=0.85,
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
        )

        assert record.metric_type == MetricType.ACCURACY
        assert record.value == 0.85
        assert record.skill_name == "ranking"

    def test_record_immutability(self):
        """Metric records are immutable."""
        from datetime import datetime

        record = MetricRecord(
            metric_id="m1",
            metric_type=MetricType.ACCURACY,
            value=0.85,
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
        )

        with pytest.raises(AttributeError):
            record.value = 0.9

    def test_record_to_payload(self):
        """Convert record to payload."""
        from datetime import datetime

        record = MetricRecord(
            metric_id="m1",
            metric_type=MetricType.LATENCY,
            value=125.5,
            skill_name="summarizer",
            session_id="session-456",
            timestamp_utc=datetime.utcnow(),
            tags={"engine": "claude-opus", "model": "latest"},
        )

        payload = record.to_payload()

        assert payload["metric_type"] == "latency"
        assert payload["value"] == 125.5
        assert payload["tags"]["engine"] == "claude-opus"


class TestMetricsCollector:
    """Test metrics collector."""

    @pytest.fixture
    def collector(self):
        """Create collector instance."""
        return MetricsCollector("_default")

    def test_record_accuracy(self, collector):
        """Record accuracy metric."""
        record = collector.record_accuracy(
            session_id="session-123",
            value=0.92,
            skill_name="ranking",
        )

        assert record.metric_type == MetricType.ACCURACY
        assert record.value == 0.92
        assert record.metric_id is not None

    def test_accuracy_out_of_bounds(self, collector):
        """Reject accuracy outside 0.0-1.0."""
        with pytest.raises(ValueError, match="Invalid accuracy"):
            collector.record_accuracy(
                session_id="session-123",
                value=1.5,
            )

    def test_record_latency(self, collector):
        """Record latency metric."""
        record = collector.record_latency(
            session_id="session-123",
            value=250.0,
            skill_name="summarizer",
        )

        assert record.metric_type == MetricType.LATENCY
        assert record.value == 250.0

    def test_latency_negative(self, collector):
        """Reject negative latency."""
        with pytest.raises(ValueError, match="Invalid latency"):
            collector.record_latency(
                session_id="session-123",
                value=-100.0,
            )

    def test_record_confidence(self, collector):
        """Record confidence metric."""
        record = collector.record_confidence(
            session_id="session-123",
            value=0.75,
            skill_name="code_review",
        )

        assert record.metric_type == MetricType.CONFIDENCE
        assert record.value == 0.75

    def test_confidence_out_of_bounds(self, collector):
        """Reject confidence outside 0.0-1.0."""
        with pytest.raises(ValueError, match="Invalid confidence"):
            collector.record_confidence(
                session_id="session-123",
                value=-0.1,
            )

    def test_record_throughput(self, collector):
        """Record throughput metric."""
        record = collector.record_throughput(
            session_id="session-123",
            value=42.0,
        )

        assert record.metric_type == MetricType.THROUGHPUT
        assert record.value == 42.0

    def test_throughput_negative(self, collector):
        """Reject negative throughput."""
        with pytest.raises(ValueError, match="Invalid throughput"):
            collector.record_throughput(
                session_id="session-123",
                value=-5.0,
            )

    def test_record_satisfaction(self, collector):
        """Record satisfaction metric."""
        record = collector.record_satisfaction(
            session_id="session-123",
            value=5,
            skill_name="ranking",
        )

        assert record.metric_type == MetricType.USER_SATISFACTION
        assert record.value == 5.0

    def test_satisfaction_out_of_bounds(self, collector):
        """Reject satisfaction outside 1-5."""
        with pytest.raises(ValueError, match="Invalid satisfaction"):
            collector.record_satisfaction(
                session_id="session-123",
                value=6,
            )

    def test_satisfaction_zero(self, collector):
        """Reject satisfaction value 0."""
        with pytest.raises(ValueError, match="Invalid satisfaction"):
            collector.record_satisfaction(
                session_id="session-123",
                value=0,
            )

    def test_metrics_with_tags(self, collector):
        """Records can include metadata tags."""
        record = collector.record_accuracy(
            session_id="session-123",
            value=0.88,
            skill_name="summarizer",
            tags={"model": "opus", "region": "us-west"},
        )

        assert record.tags["model"] == "opus"
        assert record.tags["region"] == "us-west"

    def test_all_metric_types(self, collector):
        """Support all metric types."""
        types = [
            (MetricType.ACCURACY, 0.5, None),
            (MetricType.LATENCY, 100.0, None),
            (MetricType.CONFIDENCE, 0.7, None),
            (MetricType.THROUGHPUT, 10.0, None),
            (MetricType.USER_SATISFACTION, 3, None),
        ]

        for metric_type, value, _ in types:
            if metric_type == MetricType.ACCURACY:
                record = collector.record_accuracy("s1", value)
            elif metric_type == MetricType.LATENCY:
                record = collector.record_latency("s1", value)
            elif metric_type == MetricType.CONFIDENCE:
                record = collector.record_confidence("s1", value)
            elif metric_type == MetricType.THROUGHPUT:
                record = collector.record_throughput("s1", value)
            elif metric_type == MetricType.USER_SATISFACTION:
                record = collector.record_satisfaction("s1", int(value))

            assert record.metric_type == metric_type
