"""E2E tests for Diagnostics Dashboard plugin."""

import pytest
import time
from datetime import datetime, timezone

from buildin.observability.diagnostics_dashboard import DiagnosticsDashboard
from core.compliance.tripwire import boot_tripwire


class TestDiagnosticsDashboardE2E:
    """End-to-end tests for Diagnostics Dashboard."""

    @pytest.fixture
    async def dashboard(self):
        """Fixture: initialized dashboard."""
        await boot_tripwire()
        dashboard = DiagnosticsDashboard(tenant_id="test_tenant")
        await dashboard.initialize()
        yield dashboard
        await dashboard.shutdown()

    @pytest.mark.asyncio
    async def test_dashboard_aggregation(self, dashboard):
        """Test aggregation of metrics from all three plugins."""
        # Simulate autonomy tracker data
        await dashboard.update_autonomy_metrics({
            "active_sessions": 5,
            "mean_health_score": 85.5
        })

        # Simulate brain diagnostics data
        await dashboard.update_brain_metrics({
            "overall_score": 82.0,
            "subsystems": {"execution_context": 90, "context_bus": 85}
        })

        # Simulate brain layer monitor data
        await dashboard.update_layer_metrics({
            "layers_monitored": 36,
            "compliance_layers_passed": 36
        })

        # Get aggregated view
        overview = await dashboard.get_overview()
        assert overview["system_health"] is not None
        assert overview["sessions_active"] == 5
        assert overview["compliance_status"] == "PASS"

    @pytest.mark.asyncio
    async def test_dashboard_load_performance(self, dashboard):
        """Test dashboard load time (<500ms)."""
        # Populate with data
        await dashboard.update_autonomy_metrics({"active_sessions": 10})
        await dashboard.update_brain_metrics({"overall_score": 85})
        await dashboard.update_layer_metrics({"layers_monitored": 36})

        # Measure load time
        times = []
        for _ in range(10):
            start = time.time()
            view = await dashboard.get_overview()
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        mean_load = sum(times) / len(times)
        assert mean_load < 500.0, f"Mean load time {mean_load:.0f}ms exceeds 500ms"

    @pytest.mark.asyncio
    async def test_anomaly_display(self, dashboard):
        """Test anomaly visualization on dashboard."""
        # Simulate anomaly data
        anomalies = [
            {
                "subsystem": "context_bus",
                "type": "error_spike",
                "severity": "HIGH",
                "detected_at": datetime.now(timezone.utc).isoformat()
            }
        ]
        await dashboard.update_anomalies(anomalies)

        # Get dashboard view
        view = await dashboard.get_anomalies_view()
        assert len(view["active_anomalies"]) >= 1
        assert any(a["subsystem"] == "context_bus" for a in view["active_anomalies"])

    @pytest.mark.asyncio
    async def test_historical_data_query(self, dashboard):
        """Test historical data retrieval."""
        # Simulate metric history
        for i in range(100):
            await dashboard.record_historical_point({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "system_health": 80 + (i % 20),
                "sessions_active": 5 + (i % 10)
            })

        # Query history
        history = await dashboard.get_history(hours=1)
        assert len(history) > 0
        assert all("timestamp" in point for point in history)

    @pytest.mark.asyncio
    async def test_real_time_updates(self, dashboard):
        """Test real-time metric updates."""
        # Initial state
        overview1 = await dashboard.get_overview()

        # Update metrics
        await dashboard.update_autonomy_metrics({
            "active_sessions": 20,
            "mean_health_score": 88.0
        })

        # Verify update
        overview2 = await dashboard.get_overview()
        assert overview2["sessions_active"] == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
