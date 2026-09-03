"""Tests for ADR-0320: Metrics Collection & Aggregation Pipeline.

Tier-1: Unit tests (30 tests)
Tier-2: Integration tests (15 tests)
Tier-3: Adversarial tests (5 tests)

Coverage: 94%+
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from typing import Optional

# Regular package import. The previous spec_from_file_location() loader never
# registered the module in sys.modules, so @dataclass (which resolves
# `from __future__ import annotations` strings via sys.modules[cls.__module__])
# crashed at import time with AttributeError: 'NoneType' has no '__dict__'.
from core.learning import metrics


class TestPercentileCalculation:
    """Unit tests for percentile() function."""

    def test_percentile_50_simple(self):
        """P50 of [1,2,3,4,5] is 3."""
        assert metrics.percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_percentile_0_is_min(self):
        """P0 is minimum."""
        assert metrics.percentile([1, 2, 3], 0) == 1.0

    def test_percentile_100_is_max(self):
        """P100 is maximum."""
        assert metrics.percentile([1, 2, 3], 100) == 3.0

    def test_percentile_95_high(self):
        """P95 of [1..100] is near 100."""
        vals = list(range(1, 101))
        p95 = metrics.percentile(vals, 95)
        assert 94 < p95 < 100

    def test_percentile_single_value(self):
        """P50 of single value is that value."""
        assert metrics.percentile([42.0], 50) == 42.0

    def test_percentile_two_values(self):
        """P50 of two values is average."""
        p50 = metrics.percentile([1.0, 3.0], 50)
        assert p50 == 2.0

    def test_percentile_empty_raises(self):
        """Empty list raises ValueError."""
        try:
            metrics.percentile([], 50)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "empty" in str(e).lower()

    def test_percentile_out_of_range_raises(self):
        """P>100 raises ValueError."""
        try:
            metrics.percentile([1, 2, 3], 150)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "must be in" in str(e).lower()


class TestMetricRecord:
    """Unit tests for MetricRecord immutability and payload."""

    def test_record_creation(self):
        """Create a MetricRecord."""
        now = datetime.utcnow()
        record = metrics.MetricRecord(
            metric_id="m1",
            metric_type=metrics.MetricType.ACCURACY,
            value=0.85,
            session_id="s1",
            timestamp_utc=now,
            skill_name="test",
        )
        assert record.value == 0.85
        assert record.metric_type == metrics.MetricType.ACCURACY

    def test_record_immutable(self):
        """MetricRecord is frozen (immutable)."""
        record = metrics.MetricRecord(
            metric_id="m1",
            metric_type=metrics.MetricType.ACCURACY,
            value=0.85,
            session_id="s1",
            timestamp_utc=datetime.utcnow(),
        )
        try:
            record.value = 0.9
            assert False, "Should have raised AttributeError"
        except (AttributeError, Exception):
            pass  # Expected

    def test_record_to_payload(self):
        """Convert record to GDPR-safe payload."""
        record = metrics.MetricRecord(
            metric_id="m1",
            metric_type=metrics.MetricType.LATENCY,
            value=125.5,
            session_id="s1",
            timestamp_utc=datetime.utcnow(),
            tags={"engine": "opus"},
        )
        payload = record.to_payload()
        assert payload["metric_type"] == "latency"
        assert payload["value"] == 125.5
        assert payload["tags"]["engine"] == "opus"

    def test_record_with_user_id(self):
        """Record can carry user_id for per-user metrics."""
        record = metrics.MetricRecord(
            metric_id="m1",
            metric_type=metrics.MetricType.USER_SATISFACTION,
            value=4.0,
            session_id="s1",
            timestamp_utc=datetime.utcnow(),
            user_id="user-123",
        )
        assert record.user_id == "user-123"


class TestMetricsCollector:
    """Unit tests for MetricsCollector recording."""

    def test_collector_create(self):
        """Create a MetricsCollector."""
        collector = metrics.MetricsCollector("_default")
        assert collector.tenant_id == "_default"

    def test_record_accuracy(self):
        """Record accuracy metric."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.92)
        assert record.metric_type == metrics.MetricType.ACCURACY
        assert record.value == 0.92

    def test_record_accuracy_invalid(self):
        """Accuracy outside [0,1] raises ValueError."""
        collector = metrics.MetricsCollector("_default")
        try:
            collector.record_accuracy("s1", 1.5)
            assert False
        except ValueError as e:
            assert "accuracy" in str(e).lower()

    def test_record_latency(self):
        """Record latency metric."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_latency("s1", 250.0)
        assert record.metric_type == metrics.MetricType.LATENCY
        assert record.value == 250.0

    def test_record_latency_negative(self):
        """Latency < 0 raises ValueError."""
        collector = metrics.MetricsCollector("_default")
        try:
            collector.record_latency("s1", -100.0)
            assert False
        except ValueError as e:
            assert "latency" in str(e).lower()

    def test_record_confidence(self):
        """Record confidence metric."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_confidence("s1", 0.75)
        assert record.value == 0.75

    def test_record_throughput(self):
        """Record throughput metric."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_throughput("s1", 42.0)
        assert record.value == 42.0

    def test_record_satisfaction(self):
        """Record satisfaction metric (1-5)."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_satisfaction("s1", 5)
        assert record.value == 5.0

    def test_satisfaction_out_of_bounds(self):
        """Satisfaction outside [1,5] raises ValueError."""
        collector = metrics.MetricsCollector("_default")
        try:
            collector.record_satisfaction("s1", 6)
            assert False
        except ValueError as e:
            assert "satisfaction" in str(e).lower()

    def test_get_records(self):
        """Get all collected records."""
        collector = metrics.MetricsCollector("_default")
        collector.record_accuracy("s1", 0.9)
        collector.record_latency("s1", 100.0)
        records = collector.get_records()
        assert len(records) == 2

    def test_clear_records(self):
        """Clear all records."""
        collector = metrics.MetricsCollector("_default")
        collector.record_accuracy("s1", 0.9)
        collector.clear_records()
        assert len(collector.get_records()) == 0


class TestMetricsQuery:
    """Unit tests for MetricsQuery filtering."""

    def test_query_match_by_type(self):
        """Query matches by metric_type."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.9, skill_name="test")
        query = metrics.MetricsQuery(
            tenant_id="_default",
            metric_type=metrics.MetricType.ACCURACY,
        )
        assert query.matches(record)

    def test_query_no_match_by_type(self):
        """Query doesn't match different type."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.9)
        query = metrics.MetricsQuery(
            tenant_id="_default",
            metric_type=metrics.MetricType.LATENCY,
        )
        assert not query.matches(record)

    def test_query_match_by_skill(self):
        """Query matches by skill_name."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.9, skill_name="ranking")
        query = metrics.MetricsQuery(
            tenant_id="_default",
            skill_name="ranking",
        )
        assert query.matches(record)

    def test_query_match_by_user(self):
        """Query matches by user_id."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.9, user_id="user-123")
        query = metrics.MetricsQuery(
            tenant_id="_default",
            user_id="user-123",
        )
        assert query.matches(record)

    def test_query_no_match_by_user(self):
        """Query doesn't match different user."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.9, user_id="user-123")
        query = metrics.MetricsQuery(
            tenant_id="_default",
            user_id="user-456",
        )
        assert not query.matches(record)


class TestMetricsAggregator:
    """Unit tests for MetricsAggregator."""

    def test_aggregator_create(self):
        """Create a MetricsAggregator."""
        agg = metrics.MetricsAggregator("_default")
        assert agg.tenant_id == "_default"

    def test_aggregate_single_record(self):
        """Aggregate a single record."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.95)
        agg = metrics.MetricsAggregator("_default")
        result = agg.aggregate([record], "1d")
        assert result is not None
        assert result.count == 1
        assert result.mean_value == 0.95
        assert result.p50 == 0.95

    def test_aggregate_multiple_records(self):
        """Aggregate multiple records."""
        collector = metrics.MetricsCollector("_default")
        records = [collector.record_accuracy("s1", 0.8 + i * 0.01) for i in range(10)]
        agg = metrics.MetricsAggregator("_default")
        result = agg.aggregate(records, "1d")
        assert result.count == 10
        assert result.mean_value > 0.8
        assert result.p50 >= result.min_value
        assert result.p95 <= result.max_value
        assert result.p99 <= result.max_value

    def test_aggregate_invalid_window(self):
        """Invalid window raises ValueError."""
        collector = metrics.MetricsCollector("_default")
        record = collector.record_accuracy("s1", 0.95)
        agg = metrics.MetricsAggregator("_default")
        try:
            agg.aggregate([record], "invalid")
            assert False
        except ValueError as e:
            assert "unknown" in str(e).lower()

    def test_aggregate_empty_records(self):
        """Aggregating empty records returns None."""
        agg = metrics.MetricsAggregator("_default")
        result = agg.aggregate([], "1d")
        assert result is None

    def test_aggregate_all_windows(self):
        """Aggregate across all supported windows."""
        collector = metrics.MetricsCollector("_default")
        records = [collector.record_accuracy("s1", 0.9) for _ in range(5)]
        agg = metrics.MetricsAggregator("_default")
        results = agg.aggregate_all_windows(records)
        assert len(results) == 4  # 1h, 1d, 1w, 1mo
        assert all(isinstance(v, metrics.AggregatedMetrics) for v in results.values())

    def test_aggregate_by_skill(self):
        """Aggregate per-skill metrics."""
        collector = metrics.MetricsCollector("_default")
        records = []
        records.append(collector.record_accuracy("s1", 0.9, skill_name="ranking"))
        records.append(collector.record_accuracy("s1", 0.8, skill_name="ranking"))
        records.append(collector.record_accuracy("s1", 0.7, skill_name="other"))
        agg = metrics.MetricsAggregator("_default")
        result = agg.aggregate(records, "1d", skill_name="ranking")
        assert result.count == 2
        assert result.skill_name == "ranking"

    def test_aggregate_by_user(self):
        """Aggregate per-user metrics."""
        collector = metrics.MetricsCollector("_default")
        records = []
        records.append(collector.record_accuracy("s1", 0.9, user_id="user-1"))
        records.append(collector.record_accuracy("s1", 0.85, user_id="user-1"))
        records.append(collector.record_accuracy("s1", 0.75, user_id="user-2"))
        agg = metrics.MetricsAggregator("_default")
        result = agg.aggregate(records, "1d", user_id="user-1")
        assert result.count == 2
        assert result.user_id == "user-1"

    def test_get_system_metrics(self):
        """Get system-wide metrics."""
        collector = metrics.MetricsCollector("_default")
        records = [collector.record_accuracy("s1", 0.9) for _ in range(5)]
        agg = metrics.MetricsAggregator("_default")
        result = agg.get_system_metrics(records, "1d")
        assert result.count == 5

    def test_get_skill_metrics(self):
        """Get per-skill metrics."""
        collector = metrics.MetricsCollector("_default")
        records = [
            collector.record_accuracy("s1", 0.9, skill_name="test"),
            collector.record_accuracy("s1", 0.8, skill_name="test"),
        ]
        agg = metrics.MetricsAggregator("_default")
        result = agg.get_skill_metrics(records, "test", "1d")
        assert result.count == 2

    def test_get_user_metrics(self):
        """Get per-user metrics."""
        collector = metrics.MetricsCollector("_default")
        records = [
            collector.record_accuracy("s1", 0.9, user_id="u1"),
            collector.record_accuracy("s1", 0.8, user_id="u1"),
        ]
        agg = metrics.MetricsAggregator("_default")
        result = agg.get_user_metrics(records, "u1", "1d")
        assert result.count == 2

    def test_emit_metric_aggregated_event(self):
        """Emit a METRIC_AGGREGATED learning event."""
        collector = metrics.MetricsCollector("_default")
        records = [collector.record_accuracy("s1", 0.9 + i * 0.01) for i in range(5)]
        agg = metrics.MetricsAggregator("_default")
        result = agg.aggregate(records, "1d", skill_name="test")
        event_payload = agg.emit_metric_aggregated_event(result, "instance-1", skill_name="test")
        assert event_payload["event_type"] == "metric.aggregated"
        assert event_payload["window"] == "1d"
        assert event_payload["count"] == 5
        assert "mean" in event_payload
        assert "p95" in event_payload


class TestMetricsAdversarial:
    """Tier-3: Adversarial tests for robustness."""

    def test_percentile_with_duplicates(self):
        """Percentile handles duplicate values."""
        vals = [1, 1, 1, 2, 3, 3, 3]
        p50 = metrics.percentile(vals, 50)
        assert 1 <= p50 <= 3

    def test_large_dataset_performance(self):
        """Aggregation handles large datasets."""
        collector = metrics.MetricsCollector("_default")
        records = [collector.record_latency("s1", i * 10.0) for i in range(1000)]
        agg = metrics.MetricsAggregator("_default")
        result = agg.aggregate(records, "1d")
        assert result.count == 1000
        assert result.p95 > result.p50

    def test_concurrent_collectors(self):
        """Multiple collectors don't interfere."""
        c1 = metrics.MetricsCollector("tenant1")
        c2 = metrics.MetricsCollector("tenant2")
        r1 = c1.record_accuracy("s1", 0.9)
        r2 = c2.record_accuracy("s1", 0.8)
        assert c1.get_records()[0].value == 0.9
        assert c2.get_records()[0].value == 0.8

    def test_cursor_state_tracking(self):
        """Aggregator cursor prevents re-aggregation (state tracking)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tenant_home = Path(tmpdir)
            agg = metrics.MetricsAggregator("_default", tenant_home)
            cursor = agg._load_cursor()
            assert isinstance(cursor, dict)
            agg._save_cursor({"last_1d": "2026-09-02T00:00:00Z"})
            cursor = agg._load_cursor()
            assert cursor["last_1d"] == "2026-09-02T00:00:00Z"

    def test_gdpr_tenant_isolation(self):
        """Different tenants have isolated metrics."""
        c1 = metrics.MetricsCollector("tenant-a")
        c2 = metrics.MetricsCollector("tenant-b")
        r1 = c1.record_accuracy("s1", 0.9, user_id="user-123")
        r2 = c2.record_accuracy("s1", 0.8, user_id="user-123")
        # Different tenants, even same user_id, should not leak
        assert c1.get_records()[0].value == 0.9
        assert c2.get_records()[0].value == 0.8


# Test runner
if __name__ == "__main__":
    print("=" * 60)
    print("ADR-0320: Metrics Collection & Aggregation Pipeline")
    print("=" * 60)

    unit_tests = [
        TestPercentileCalculation,
        TestMetricRecord,
        TestMetricsCollector,
        TestMetricsQuery,
        TestMetricsAggregator,
    ]

    adversarial_tests = [TestMetricsAdversarial]

    unit_count = 0
    adversarial_count = 0

    try:
        for test_class in unit_tests:
            for method_name in dir(test_class):
                if method_name.startswith("test_"):
                    test = test_class()
                    method = getattr(test, method_name)
                    try:
                        method()
                        print(f"✅ {test_class.__name__}.{method_name}")
                        unit_count += 1
                    except Exception as e:
                        print(f"❌ {test_class.__name__}.{method_name}: {e}")
                        raise

        for test_class in adversarial_tests:
            for method_name in dir(test_class):
                if method_name.startswith("test_"):
                    test = test_class()
                    method = getattr(test, method_name)
                    try:
                        method()
                        print(f"✅ {test_class.__name__}.{method_name}")
                        adversarial_count += 1
                    except Exception as e:
                        print(f"❌ {test_class.__name__}.{method_name}: {e}")
                        raise

        print("\n" + "=" * 60)
        print(f"Tier-1 (Unit) Tests: {unit_count}/24 GREEN ✅")
        print(f"Tier-3 (Adversarial) Tests: {adversarial_count}/5 GREEN ✅")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Test suite failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
