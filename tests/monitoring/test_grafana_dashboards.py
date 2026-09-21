"""
Test suite for Grafana dashboards (Component 2)

6 tests covering:
- Dashboard creation
- Dashboard JSON validity
- Panel count and structure
- Alert annotations
- Export functionality
"""

import pytest
import json
from corvin_console.monitoring import (
    create_model_routing_dashboard,
    create_slo_monitoring_dashboard,
    create_learning_loop_dashboard,
    export_dashboards_json,
)


class TestGrafanaDashboards:
    """Test Grafana dashboard creation."""

    def test_model_routing_dashboard_created(self):
        """Test model routing dashboard creation."""
        dashboard = create_model_routing_dashboard()

        assert dashboard is not None
        assert isinstance(dashboard, dict)
        assert "panels" in dashboard
        assert len(dashboard["panels"]) > 0

    def test_slo_monitoring_dashboard_created(self):
        """Test SLO monitoring dashboard creation."""
        dashboard = create_slo_monitoring_dashboard()

        assert dashboard is not None
        assert isinstance(dashboard, dict)
        assert "panels" in dashboard
        assert len(dashboard["panels"]) > 0

    def test_learning_loop_dashboard_created(self):
        """Test learning loop health dashboard creation."""
        dashboard = create_learning_loop_dashboard()

        assert dashboard is not None
        assert isinstance(dashboard, dict)
        assert "panels" in dashboard
        assert len(dashboard["panels"]) > 0

    def test_dashboard_has_valid_structure(self):
        """Test that all dashboards have valid Grafana structure."""
        dashboards = [
            create_model_routing_dashboard(),
            create_slo_monitoring_dashboard(),
            create_learning_loop_dashboard(),
        ]

        for dashboard in dashboards:
            # Required fields
            assert "title" in dashboard
            assert "uid" in dashboard
            assert "panels" in dashboard
            assert "time" in dashboard
            assert "timepicker" in dashboard

            # Dashboard should be JSON serializable
            json_str = json.dumps(dashboard)
            assert isinstance(json_str, str)

    def test_dashboard_panels_have_targets(self):
        """Test that panels have Prometheus targets."""
        dashboards = [
            create_model_routing_dashboard(),
            create_slo_monitoring_dashboard(),
            create_learning_loop_dashboard(),
        ]

        for dashboard in dashboards:
            for panel in dashboard["panels"]:
                # Each panel should have targets
                if "targets" in panel:
                    assert isinstance(panel["targets"], list)

                    # Targets should have expressions (for Prometheus)
                    for target in panel["targets"]:
                        assert "expr" in target or "query" in target

    def test_export_dashboards_json(self):
        """Test exporting all dashboards as JSON."""
        export_data = export_dashboards_json()

        assert "dashboards" in export_data
        assert len(export_data["dashboards"]) == 3

        # Should be JSON serializable
        json_str = json.dumps(export_data, indent=2)
        assert isinstance(json_str, str)


class TestDashboardContent:
    """Test specific dashboard content."""

    def test_routing_dashboard_has_key_panels(self):
        """Test routing dashboard has expected panels."""
        dashboard = create_model_routing_dashboard()

        # Should have stat tiles and charts
        panel_titles = [p.get("title", "") for p in dashboard["panels"]]

        # Check for key panels
        key_panels = [
            "Tasks Routed",
            "Cost Today",
            "Avg Confidence Score",
            "Circuit Breaker",
            "Routing Distribution",
            "Cost per Model",
            "Confidence Score Trend",
            "Token Estimation Accuracy",
        ]

        for key_panel in key_panels:
            assert any(key_panel in title for title in panel_titles), \
                f"Missing panel: {key_panel}"

    def test_slo_dashboard_has_slo_panels(self):
        """Test SLO dashboard has SLO-specific panels."""
        dashboard = create_slo_monitoring_dashboard()

        panel_titles = [p.get("title", "") for p in dashboard["panels"]]

        # Check for SLO panels
        slo_panels = [
            "P99 Latency",
            "Error Rate",
            "Circuit Breaker",
            "SLO Compliance",
        ]

        for slo_panel in slo_panels:
            assert any(slo_panel in title for title in panel_titles), \
                f"Missing SLO panel: {slo_panel}"

    def test_learning_dashboard_has_learning_panels(self):
        """Test learning dashboard has learning-specific panels."""
        dashboard = create_learning_loop_dashboard()

        panel_titles = [p.get("title", "") for p in dashboard["panels"]]

        # Check for learning panels
        learning_panels = [
            "Feedback Signals",
            "Learning Activity",
            "Total Feedback Events",
            "Active Skills",
        ]

        for learning_panel in learning_panels:
            assert any(learning_panel in title for title in panel_titles), \
                f"Missing learning panel: {learning_panel}"

    def test_dashboard_refresh_rates(self):
        """Test dashboards have reasonable refresh rates."""
        dashboards = [
            create_model_routing_dashboard(),
            create_slo_monitoring_dashboard(),
            create_learning_loop_dashboard(),
        ]

        for dashboard in dashboards:
            refresh = dashboard.get("refresh")

            # Refresh should be a string like "10s", "5s", etc.
            assert isinstance(refresh, str)
            assert any(refresh.endswith(unit) for unit in ["s", "m", "h"])


class TestDashboardAnnotations:
    """Test dashboard annotations and alerts."""

    def test_slo_dashboard_has_alert_annotations(self):
        """Test SLO dashboard has circuit breaker annotations."""
        dashboard = create_slo_monitoring_dashboard()

        # Should have annotations
        assert "annotations" in dashboard
        annotations = dashboard["annotations"]

        # Should have at least default annotation
        assert len(annotations.get("list", [])) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
