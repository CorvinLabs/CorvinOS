"""Tests for telemetry source of truth registry (ADR-0325)."""

import pytest
from datetime import datetime

from core.telemetry.source_of_truth import (
    MetricType,
    MetricContract,
    MetricValue,
    TelemetryRegistry,
)


class TestMetricContract:
    """Tests for MetricContract."""

    def test_metric_contract_validates_labels(self):
        """Valid labels pass validation."""
        contract = MetricContract(
            name="memory_usage",
            metric_type=MetricType.GAUGE,
            required_labels={"host", "instance"},
        )
        labels = {"host": "server1", "instance": "app1"}
        contract.validate(labels)  # Should not raise

    def test_metric_contract_rejects_missing_labels(self):
        """Missing required labels raise error."""
        contract = MetricContract(
            name="memory_usage",
            metric_type=MetricType.GAUGE,
            required_labels={"host", "instance"},
        )
        labels = {"host": "server1"}  # Missing 'instance'
        with pytest.raises(ValueError, match="missing required label"):
            contract.validate(labels)

    def test_metric_contract_validates_label_types(self):
        """Labels must be strings."""
        contract = MetricContract(
            name="memory_usage",
            metric_type=MetricType.GAUGE,
            required_labels={"host"},
        )
        labels = {"host": 123}  # Not a string
        with pytest.raises(ValueError, match="must be str"):
            contract.validate(labels)

    def test_metric_contract_rejects_non_dict_labels(self):
        """Labels must be dict."""
        contract = MetricContract(
            name="memory_usage",
            metric_type=MetricType.GAUGE,
            required_labels={"host"},
        )
        with pytest.raises(ValueError, match="Labels must be dict"):
            contract.validate("not a dict")


class TestTelemetryRegistry:
    """Tests for TelemetryRegistry."""

    def setup_method(self):
        """Reset registry before each test."""
        TelemetryRegistry._instance = None

    def test_registry_singleton(self):
        """Registry is a singleton."""
        reg1 = TelemetryRegistry()
        reg2 = TelemetryRegistry()
        assert reg1 is reg2

    def test_register_metric_creates_contract(self):
        """register_metric creates a contract."""
        reg = TelemetryRegistry()
        contract = reg.register_metric(
            "requests_total",
            MetricType.COUNTER,
            required_labels={"method"},
        )
        assert contract.name == "requests_total"
        assert contract.metric_type == MetricType.COUNTER

    def test_register_metric_validates_name(self):
        """Invalid name raises error."""
        reg = TelemetryRegistry()
        with pytest.raises(ValueError, match="Invalid metric name"):
            reg.register_metric("", MetricType.GAUGE)

    def test_register_duplicate_metric_with_same_contract(self):
        """Duplicate registration with same contract is OK."""
        reg = TelemetryRegistry()
        contract1 = reg.register_metric("test_metric", MetricType.GAUGE)
        contract2 = reg.register_metric("test_metric", MetricType.GAUGE)
        assert contract1.name == contract2.name

    def test_register_duplicate_metric_with_different_contract(self):
        """Duplicate registration with different contract raises error."""
        reg = TelemetryRegistry()
        reg.register_metric("test_metric", MetricType.GAUGE)
        with pytest.raises(ValueError, match="already registered with different contract"):
            reg.register_metric("test_metric", MetricType.COUNTER)

    def test_record_metric_stores_value(self):
        """record_metric stores value."""
        reg = TelemetryRegistry()
        reg.register_metric("test_metric", MetricType.GAUGE, required_labels={"host"})
        reg.record_metric(
            "test_metric",
            value=42.5,
            labels={"host": "server1"},
            tenant_id="tenant1",
        )
        # Verify stored
        value = reg.get_active("test_metric", tenant_id="tenant1")
        assert value is not None
        assert value.value == 42.5

    def test_record_metric_validates_value_type(self):
        """record_metric validates value is numeric."""
        reg = TelemetryRegistry()
        reg.register_metric("test_metric", MetricType.GAUGE)
        with pytest.raises(ValueError, match="value must be numeric"):
            reg.record_metric(
                "test_metric",
                value="not a number",
                labels={},
                tenant_id="tenant1",
            )

    def test_record_metric_validates_labels(self):
        """record_metric validates labels against contract."""
        reg = TelemetryRegistry()
        reg.register_metric(
            "test_metric",
            MetricType.GAUGE,
            required_labels={"required_label"},
        )
        with pytest.raises(ValueError, match="missing required label"):
            reg.record_metric(
                "test_metric",
                value=42,
                labels={},  # Missing required_label
                tenant_id="tenant1",
            )

    def test_record_metric_requires_valid_tenant_id(self):
        """record_metric validates tenant_id."""
        reg = TelemetryRegistry()
        reg.register_metric("test_metric", MetricType.GAUGE)
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            reg.record_metric(
                "test_metric",
                value=42,
                labels={},
                tenant_id="",
            )

    def test_get_active_returns_latest_value(self):
        """get_active returns most recent value."""
        reg = TelemetryRegistry()
        reg.register_metric("test_metric", MetricType.GAUGE)
        reg.record_metric("test_metric", 10, {}, "tenant1")
        reg.record_metric("test_metric", 20, {}, "tenant1")
        value = reg.get_active("test_metric", tenant_id="tenant1")
        assert value.value == 20

    def test_get_active_raises_for_unregistered_metric(self):
        """get_active raises for unregistered metric."""
        reg = TelemetryRegistry()
        with pytest.raises(ValueError, match="not registered"):
            reg.get_active("unregistered_metric")

    def test_get_active_returns_none_for_never_recorded(self):
        """get_active returns None if never recorded."""
        reg = TelemetryRegistry()
        reg.register_metric("test_metric", MetricType.GAUGE)
        value = reg.get_active("test_metric")
        assert value is None

    def test_validate_consistency_passes_valid_state(self):
        """validate_consistency passes for valid metrics."""
        reg = TelemetryRegistry()
        reg.register_metric(
            "test_metric",
            MetricType.GAUGE,
            required_labels={"host"},
        )
        reg.record_metric("test_metric", 42, {"host": "server1"}, "tenant1")
        reg.validate_consistency()  # Should not raise

    def test_validate_consistency_fails_on_unregistered_metric(self):
        """validate_consistency fails if metric recorded but not registered."""
        reg = TelemetryRegistry()
        # Manually corrupt the registry (test only)
        reg._values[("unregistered", "")] = MetricValue(
            name="unregistered",
            value=42,
            labels={},
            timestamp_utc=datetime.utcnow(),
            tenant_id="tenant1",
        )
        with pytest.raises(ValueError, match="not registered"):
            reg.validate_consistency()

    def test_get_metrics_snapshot_filters_by_tenant(self):
        """get_metrics_snapshot filters by tenant_id."""
        reg = TelemetryRegistry()
        reg.register_metric("test_metric", MetricType.GAUGE)
        reg.record_metric("test_metric", 10, {}, "tenant1")
        reg.record_metric("test_metric", 20, {}, "tenant2")

        snapshot_t1 = reg.get_metrics_snapshot("tenant1")
        assert len(snapshot_t1) == 1
        assert snapshot_t1["test_metric"].value == 10

    def test_get_metrics_snapshot_requires_valid_tenant_id(self):
        """get_metrics_snapshot validates tenant_id."""
        reg = TelemetryRegistry()
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            reg.get_metrics_snapshot("")

    def test_metric_value_to_audit_event(self):
        """MetricValue converts to audit event."""
        value = MetricValue(
            name="test",
            value=42,
            labels={"host": "server1"},
            timestamp_utc=datetime(2026, 8, 14, 12, 0, 0),
            tenant_id="tenant1",
        )
        event = value.to_audit_event()
        assert event["event_type"] == "metric.recorded"
        assert event["metric_name"] == "test"
        assert event["value"] == 42
        assert event["tenant_id"] == "tenant1"

    def test_emit_audit_event_unregistered_metric_raises_error(self):
        """emit_audit_event raises for unregistered metric."""
        reg = TelemetryRegistry()
        with pytest.raises(ValueError, match="not registered"):
            reg.emit_audit_event("unregistered", 42, "tenant1")

    def test_reset_for_testing(self):
        """reset_for_testing clears state."""
        reg = TelemetryRegistry()
        reg.register_metric("test", MetricType.GAUGE)
        reg.record_metric("test", 42, {}, "tenant1")

        reg.reset_for_testing()

        assert len(reg._contracts) == 0
        assert len(reg._values) == 0
