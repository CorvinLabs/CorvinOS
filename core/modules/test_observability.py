"""Tests for observability dashboard (ADR-0327)."""

import pytest
import asyncio
from datetime import datetime

from core.modules.observability import (
    DashboardConfig,
    SubscriberConnection,
    ObservabilityDashboard,
)
from core.telemetry.source_of_truth import (
    MetricType,
    MetricValue,
    TelemetryRegistry,
)


class TestObservabilityDashboard:
    """Tests for ObservabilityDashboard."""

    def setup_method(self):
        """Reset registry before each test."""
        TelemetryRegistry._instance = None

    def test_render_metrics_dashboard_returns_json(self):
        """render_metrics_dashboard returns JSON dict."""
        dashboard = ObservabilityDashboard()
        result = dashboard.render_metrics_dashboard("tenant1")

        assert isinstance(result, dict)
        assert "timestamp" in result
        assert "tenant_id" in result
        assert "metrics" in result
        assert result["tenant_id"] == "tenant1"

    def test_render_metrics_dashboard_includes_all_metrics(self):
        """render_metrics_dashboard includes all metrics for tenant."""
        dashboard = ObservabilityDashboard()
        reg = TelemetryRegistry()

        # Record some metrics
        reg.register_metric("cpu", MetricType.GAUGE)
        reg.register_metric("memory", MetricType.GAUGE)
        reg.record_metric("cpu", 50.0, {}, "tenant1")
        reg.record_metric("memory", 60.0, {}, "tenant1")

        result = dashboard.render_metrics_dashboard("tenant1")

        assert result["metric_count"] == 2
        metric_names = {m["name"] for m in result["metrics"]}
        assert "cpu" in metric_names
        assert "memory" in metric_names

    def test_render_metrics_dashboard_validates_tenant_id(self):
        """render_metrics_dashboard validates tenant_id."""
        dashboard = ObservabilityDashboard()

        with pytest.raises(ValueError, match="Invalid tenant_id"):
            dashboard.render_metrics_dashboard("")

    def test_render_metrics_dashboard_cross_tenant_isolation(self):
        """render_metrics_dashboard isolates by tenant."""
        dashboard = ObservabilityDashboard()
        reg = TelemetryRegistry()

        reg.register_metric("cpu", MetricType.GAUGE)
        reg.record_metric("cpu", 50.0, {}, "tenant1")
        reg.record_metric("cpu", 60.0, {}, "tenant2")

        result_t1 = dashboard.render_metrics_dashboard("tenant1")
        result_t2 = dashboard.render_metrics_dashboard("tenant2")

        assert result_t1["metric_count"] == 1
        assert result_t2["metric_count"] == 1
        assert result_t1["metrics"][0]["value"] == 50.0
        assert result_t2["metrics"][0]["value"] == 60.0

    def test_render_metrics_dashboard_respects_max_metrics_limit(self):
        """render_metrics_dashboard caps at max_metrics_per_request."""
        config = DashboardConfig(max_metrics_per_request=2)
        dashboard = ObservabilityDashboard(config)
        reg = TelemetryRegistry()

        # Record 5 metrics
        for i in range(5):
            metric_name = f"metric_{i}"
            reg.register_metric(metric_name, MetricType.GAUGE)
            reg.record_metric(metric_name, float(i), {}, "tenant1")

        result = dashboard.render_metrics_dashboard("tenant1")

        # Should be capped at 2
        assert len(result["metrics"]) == 2

    def test_subscribe_creates_connection(self):
        """subscribe creates SubscriberConnection."""
        dashboard = ObservabilityDashboard()

        async def send_fn(msg):
            pass

        connection = dashboard.subscribe("conn1", "tenant1", send_fn)

        assert connection.connection_id == "conn1"
        assert connection.tenant_id == "tenant1"
        assert "conn1" in dashboard._subscribers

    def test_subscribe_validates_connection_id(self):
        """subscribe validates connection_id."""
        dashboard = ObservabilityDashboard()

        async def send_fn(msg):
            pass

        with pytest.raises(ValueError, match="Invalid connection_id"):
            dashboard.subscribe("", "tenant1", send_fn)

    def test_subscribe_validates_tenant_id(self):
        """subscribe validates tenant_id."""
        dashboard = ObservabilityDashboard()

        async def send_fn(msg):
            pass

        with pytest.raises(ValueError, match="Invalid tenant_id"):
            dashboard.subscribe("conn1", "", send_fn)

    def test_subscribe_validates_send_function(self):
        """subscribe validates send_fn is callable."""
        dashboard = ObservabilityDashboard()

        with pytest.raises(ValueError, match="must be callable"):
            dashboard.subscribe("conn1", "tenant1", "not callable")

    def test_unsubscribe_removes_connection(self):
        """unsubscribe removes connection."""
        dashboard = ObservabilityDashboard()

        async def send_fn(msg):
            pass

        dashboard.subscribe("conn1", "tenant1", send_fn)
        dashboard.unsubscribe("conn1")

        assert "conn1" not in dashboard._subscribers

    def test_unsubscribe_raises_for_missing_connection(self):
        """unsubscribe raises for missing connection."""
        dashboard = ObservabilityDashboard()

        with pytest.raises(ValueError, match="not registered"):
            dashboard.unsubscribe("missing")

    @pytest.mark.asyncio
    async def test_stream_live_metrics_sends_updates(self):
        """stream_live_metrics sends updates to subscriber."""
        dashboard = ObservabilityDashboard(DashboardConfig(refresh_interval_seconds=0.01))
        reg = TelemetryRegistry()

        reg.register_metric("cpu", MetricType.GAUGE)
        reg.record_metric("cpu", 50.0, {}, "tenant1")

        messages = []

        async def send_fn(msg):
            messages.append(msg)

        dashboard.subscribe("conn1", "tenant1", send_fn)

        # Stream for short duration
        update_count = await dashboard.stream_live_metrics("conn1", duration_seconds=0.05)

        assert update_count > 0
        assert len(messages) == update_count

    @pytest.mark.asyncio
    async def test_stream_live_metrics_respects_duration(self):
        """stream_live_metrics respects duration_seconds."""
        dashboard = ObservabilityDashboard(DashboardConfig(refresh_interval_seconds=0.01))

        async def send_fn(msg):
            pass

        dashboard.subscribe("conn1", "tenant1", send_fn)

        update_count = await dashboard.stream_live_metrics("conn1", duration_seconds=0.05)

        # Should be a small number due to short duration
        assert update_count >= 1
        assert update_count <= 10

    @pytest.mark.asyncio
    async def test_stream_live_metrics_unregistered_connection_raises(self):
        """stream_live_metrics raises for unregistered connection."""
        dashboard = ObservabilityDashboard()

        with pytest.raises(ValueError, match="not registered"):
            await dashboard.stream_live_metrics("missing")

    def test_get_subscriber_count_returns_total(self):
        """get_subscriber_count returns total subscribers."""
        dashboard = ObservabilityDashboard()

        async def send_fn(msg):
            pass

        dashboard.subscribe("conn1", "tenant1", send_fn)
        dashboard.subscribe("conn2", "tenant1", send_fn)
        dashboard.subscribe("conn3", "tenant2", send_fn)

        assert dashboard.get_subscriber_count() == 3

    def test_get_subscriber_count_filters_by_tenant(self):
        """get_subscriber_count filters by tenant."""
        dashboard = ObservabilityDashboard()

        async def send_fn(msg):
            pass

        dashboard.subscribe("conn1", "tenant1", send_fn)
        dashboard.subscribe("conn2", "tenant1", send_fn)
        dashboard.subscribe("conn3", "tenant2", send_fn)

        assert dashboard.get_subscriber_count("tenant1") == 2
        assert dashboard.get_subscriber_count("tenant2") == 1

    def test_get_dashboard_stats_returns_metrics(self):
        """get_dashboard_stats returns usage stats."""
        config = DashboardConfig(refresh_interval_seconds=10, max_metrics_per_request=500)
        dashboard = ObservabilityDashboard(config)

        stats = dashboard.get_dashboard_stats()

        assert stats["total_subscribers"] == 0
        assert stats["total_updates_sent"] == 0
        assert stats["refresh_interval_seconds"] == 10
        assert stats["max_metrics_per_request"] == 500

    def test_reset_for_testing_clears_state(self):
        """reset_for_testing clears dashboard state."""
        dashboard = ObservabilityDashboard()

        async def send_fn(msg):
            pass

        dashboard.subscribe("conn1", "tenant1", send_fn)
        dashboard._update_counter = 100

        dashboard.reset_for_testing()

        assert len(dashboard._subscribers) == 0
        assert dashboard._update_counter == 0

    def test_subscriber_connection_repr(self):
        """SubscriberConnection has useful repr."""
        async def send_fn(msg):
            pass

        connection = SubscriberConnection("conn1", "tenant1", send_fn)
        repr_str = repr(connection)

        assert "conn1" in repr_str
        assert "tenant1" in repr_str

    def test_dashboard_config_customization(self):
        """DashboardConfig allows customization."""
        config = DashboardConfig(
            refresh_interval_seconds=10,
            max_metrics_per_request=500,
        )

        assert config.refresh_interval_seconds == 10
        assert config.max_metrics_per_request == 500
        assert config.include_historical is False

    @pytest.mark.asyncio
    async def test_stream_with_broken_connection_cleanup(self):
        """stream_live_metrics cleans up on broken connection."""
        dashboard = ObservabilityDashboard(DashboardConfig(refresh_interval_seconds=0.01))

        call_count = 0

        async def failing_send(msg):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise ConnectionError("Connection broken")

        dashboard.subscribe("conn1", "tenant1", failing_send)

        # Stream should handle error and cleanup
        update_count = await dashboard.stream_live_metrics("conn1", duration_seconds=0.1)

        # Connection should be removed after error
        assert "conn1" not in dashboard._subscribers

    def test_render_includes_refresh_interval_in_response(self):
        """render_metrics_dashboard includes refresh interval."""
        config = DashboardConfig(refresh_interval_seconds=30)
        dashboard = ObservabilityDashboard(config)

        result = dashboard.render_metrics_dashboard("tenant1")

        assert result["refresh_interval_seconds"] == 30
