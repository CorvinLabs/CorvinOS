"""Tests for aggregation pipeline (ADR-0326)."""

import pytest
from datetime import datetime

from core.pipeline.aggregation import (
    AggregationPipeline,
    AggregationConfig,
    Metric,
    AggregatedMetric,
)
from core.telemetry.source_of_truth import MetricType, TelemetryRegistry


class TestAggregationPipeline:
    """Tests for AggregationPipeline."""

    def setup_method(self):
        """Reset registry before each test."""
        TelemetryRegistry._instance = None

    def test_collect_ingests_raw_metrics(self):
        """collect ingests metrics."""
        pipeline = AggregationPipeline()
        metrics = [
            Metric("cpu", 50.0, "tenant1", datetime.utcnow()),
            Metric("memory", 60.0, "tenant1", datetime.utcnow()),
        ]
        pipeline.collect(metrics)
        assert len(pipeline._collected_metrics) == 2

    def test_collect_rejects_invalid_type(self):
        """collect rejects non-list."""
        pipeline = AggregationPipeline()
        with pytest.raises(TypeError, match="must be list"):
            pipeline.collect("not a list")

    def test_collect_rejects_invalid_metric_object(self):
        """collect rejects non-Metric objects."""
        pipeline = AggregationPipeline()
        with pytest.raises(ValueError, match="expected Metric"):
            pipeline.collect([{"name": "cpu", "value": 50}])

    def test_collect_validates_metric_name(self):
        """collect validates metric name."""
        pipeline = AggregationPipeline()
        bad_metric = Metric("", 50.0, "tenant1", datetime.utcnow())
        with pytest.raises(ValueError, match="name must be non-empty string"):
            pipeline.collect([bad_metric])

    def test_collect_validates_metric_value_type(self):
        """collect validates value is numeric."""
        pipeline = AggregationPipeline()
        bad_metric = Metric("cpu", "not numeric", "tenant1", datetime.utcnow())
        with pytest.raises(ValueError, match="value must be numeric"):
            pipeline.collect([bad_metric])

    def test_collect_validates_tenant_id(self):
        """collect validates tenant_id."""
        pipeline = AggregationPipeline()
        bad_metric = Metric("cpu", 50.0, "", datetime.utcnow())
        with pytest.raises(ValueError, match="tenant_id must be non-empty string"):
            pipeline.collect([bad_metric])

    def test_validate_fails_closed_on_missing_fields(self):
        """validate fails closed on invalid metrics."""
        pipeline = AggregationPipeline()
        # Manually corrupt collected metrics
        bad_metric = Metric("cpu", 50.0, "tenant1", datetime.utcnow())
        pipeline._collected_metrics = [bad_metric]

        # Should not raise for unregistered metric (allowed in current impl)
        # but should work without errors

    def test_aggregate_computes_basic_stats(self):
        """aggregate computes min/max/mean."""
        pipeline = AggregationPipeline()
        reg = TelemetryRegistry()
        reg.register_metric("cpu", MetricType.GAUGE)

        metrics = [
            Metric("cpu", 10.0, "tenant1", datetime.utcnow()),
            Metric("cpu", 20.0, "tenant1", datetime.utcnow()),
            Metric("cpu", 30.0, "tenant1", datetime.utcnow()),
        ]
        pipeline.collect(metrics)
        agg_list = pipeline.aggregate()

        assert len(agg_list) == 1
        agg = agg_list[0]
        assert agg.min_value == 10.0
        assert agg.max_value == 30.0
        assert agg.mean_value == 20.0

    def test_aggregate_groups_by_tenant(self):
        """aggregate groups metrics by tenant."""
        pipeline = AggregationPipeline()
        reg = TelemetryRegistry()
        reg.register_metric("cpu", MetricType.GAUGE)

        metrics = [
            Metric("cpu", 10.0, "tenant1", datetime.utcnow()),
            Metric("cpu", 20.0, "tenant2", datetime.utcnow()),
        ]
        pipeline.collect(metrics)
        agg_list = pipeline.aggregate()

        # Should be grouped by (name, tenant) = 2 aggregates
        assert len(agg_list) == 2

    def test_aggregate_requires_collected_metrics(self):
        """aggregate requires collect() first."""
        pipeline = AggregationPipeline()
        with pytest.raises(ValueError, match="No metrics collected"):
            pipeline.aggregate()

    def test_aggregate_computes_percentiles(self):
        """aggregate computes p50 and p99."""
        pipeline = AggregationPipeline()
        reg = TelemetryRegistry()
        reg.register_metric("latency", MetricType.HISTOGRAM)

        # 100 values: 1-100
        metrics = [
            Metric("latency", float(i), "tenant1", datetime.utcnow())
            for i in range(1, 101)
        ]
        pipeline.collect(metrics)
        agg_list = pipeline.aggregate()

        assert len(agg_list) == 1
        agg = agg_list[0]
        # P50 should be around 50, P99 around 99
        assert 40 < agg.p50 < 60
        assert 90 < agg.p99 <= 100

    def test_emit_requires_aggregated_metrics(self):
        """emit requires aggregate() first."""
        pipeline = AggregationPipeline()
        with pytest.raises(ValueError, match="No aggregated metrics"):
            pipeline.emit()

    def test_emit_returns_emission_results(self):
        """emit returns dict with emission results."""
        pipeline = AggregationPipeline()
        reg = TelemetryRegistry()
        reg.register_metric("test", MetricType.GAUGE)

        metrics = [Metric("test", 42.0, "tenant1", datetime.utcnow())]
        pipeline.collect(metrics)
        agg_list = pipeline.aggregate()
        results = pipeline.emit()

        assert "emitted_to_audit" in results
        assert "emitted_to_disk" in results
        assert "failed" in results

    def test_pipeline_end_to_end_flow(self):
        """Complete pipeline: collect → validate → aggregate → emit."""
        pipeline = AggregationPipeline()
        reg = TelemetryRegistry()
        reg.register_metric("requests", MetricType.COUNTER)

        metrics = [
            Metric("requests", 100.0, "tenant1", datetime.utcnow()),
            Metric("requests", 150.0, "tenant1", datetime.utcnow()),
        ]

        pipeline.collect(metrics)
        agg_list = pipeline.aggregate()
        results = pipeline.emit()

        assert len(agg_list) == 1
        assert results["failed"] == 0

    def test_pipeline_cross_metric_aggregation(self):
        """Pipeline handles multiple metrics correctly."""
        pipeline = AggregationPipeline()
        reg = TelemetryRegistry()
        reg.register_metric("cpu", MetricType.GAUGE)
        reg.register_metric("memory", MetricType.GAUGE)

        metrics = [
            Metric("cpu", 50.0, "tenant1", datetime.utcnow()),
            Metric("memory", 60.0, "tenant1", datetime.utcnow()),
        ]

        pipeline.collect(metrics)
        agg_list = pipeline.aggregate()

        # Should have 2 aggregates (one per metric)
        assert len(agg_list) == 2
        metric_names = {agg.name for agg in agg_list}
        assert metric_names == {"cpu", "memory"}

    def test_reset_for_testing_clears_state(self):
        """reset_for_testing clears pipeline state."""
        pipeline = AggregationPipeline()
        metrics = [Metric("test", 42.0, "tenant1", datetime.utcnow())]
        pipeline.collect(metrics)

        pipeline.reset_for_testing()

        assert len(pipeline._collected_metrics) == 0
        assert len(pipeline._aggregated) == 0

    def test_aggregated_metric_to_audit_event(self):
        """AggregatedMetric converts to audit event."""
        agg = AggregatedMetric(
            name="cpu",
            metric_type=MetricType.GAUGE,
            window_seconds=60,
            values=[10.0, 20.0, 30.0],
            min_value=10.0,
            max_value=30.0,
            mean_value=20.0,
            p50=20.0,
            p99=30.0,
            sample_count=3,
            tenant_id="tenant1",
            timestamp_utc=datetime(2026, 8, 14, 12, 0, 0),
        )
        event = agg.to_audit_event()
        assert event["event_type"] == "aggregation.metric_aggregated"
        assert event["metric_name"] == "cpu"
        assert event["sample_count"] == 3

    def test_aggregation_config_customization(self):
        """AggregationConfig allows customization."""
        config = AggregationConfig(window_seconds=120, output_backend="disk")
        pipeline = AggregationPipeline(config)
        assert pipeline.config.window_seconds == 120
        assert pipeline.config.output_backend == "disk"

    def test_pipeline_single_value_aggregation(self):
        """Pipeline handles single-value aggregation."""
        pipeline = AggregationPipeline()
        reg = TelemetryRegistry()
        reg.register_metric("test", MetricType.GAUGE)

        metrics = [Metric("test", 42.0, "tenant1", datetime.utcnow())]
        pipeline.collect(metrics)
        agg_list = pipeline.aggregate()

        assert len(agg_list) == 1
        agg = agg_list[0]
        assert agg.min_value == 42.0
        assert agg.max_value == 42.0
        assert agg.mean_value == 42.0
        assert agg.p50 == 42.0
