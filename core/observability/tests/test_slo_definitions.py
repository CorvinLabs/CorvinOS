"""
Tests for SLO definitions (Phase 5).

Tests:
- SLO target definitions
- Measurement recording and retrieval
- Compliance calculation
- Status aggregation
"""

import pytest
from datetime import datetime, timedelta
from core.observability.slo_definitions import (
    SLOTarget,
    SLOMeasurement,
    SLOStatus,
    SLODefinitions,
    SLOMonitor,
    get_slo_monitor,
)


class TestSLOTarget:
    """Tests for SLO target definitions."""

    def test_plugin_availability_slo(self):
        """Plugin availability SLO is well-defined."""
        slos = SLODefinitions.get_all_slos()
        slo = slos["plugin_availability"]

        assert slo.target_value == 0.995
        assert slo.name == "Plugin Availability"
        assert slo.unit == "availability"
        assert slo.error_budget_percent == 0.5  # 0.5%
        assert slo.alert_threshold == 0.990

    def test_latency_slo(self):
        """Latency SLO is well-defined."""
        slos = SLODefinitions.get_all_slos()
        slo = slos["delegation_latency_p95"]

        assert slo.target_value == 200.0
        assert slo.name == "Delegation Latency (p95)"
        assert slo.unit == "latency_ms"
        assert slo.alert_threshold == 250.0

    def test_audit_integrity_slo(self):
        """Audit integrity SLO is well-defined."""
        slos = SLODefinitions.get_all_slos()
        slo = slos["audit_chain_integrity"]

        assert slo.target_value == 1.0  # 100%
        assert slo.name == "Audit Chain Integrity"
        assert slo.unit == "integrity"

    def test_error_budget_monthly(self):
        """Error budget computation for availability SLO."""
        slo = SLOTarget(
            name="Test",
            description="",
            target_value=0.99,
            unit="availability",
        )

        # 30-day month = 43,200 minutes
        # 1% error budget = 432 minutes (7.2 hours)
        expected = 43200 * 0.01
        assert abs(slo.error_budget_monthly - expected) < 0.1

    def test_get_slo_by_name(self):
        """Can retrieve SLO by name."""
        slo = SLODefinitions.get_slo_by_name("plugin_availability")
        assert slo is not None
        assert slo.target_value == 0.995

        missing = SLODefinitions.get_slo_by_name("nonexistent")
        assert missing is None


class TestSLOMeasurement:
    """Tests for SLO measurements."""

    def test_measurement_creation(self):
        """Measurement can be created."""
        now = datetime.utcnow()
        measurement = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=now - timedelta(days=30),
            window_end=now,
            status=SLOStatus.HEALTHY,
            good_count=1000,
            bad_count=5,
            total_count=1005,
        )
        assert measurement.slo_name == "plugin_availability"
        assert measurement.measured_value == 0.9951

    def test_measurement_compliance_for_availability(self):
        """Compliance percent calculated for availability metrics."""
        measurement = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.HEALTHY,
        )
        # 0.9951 / 0.995 = 100.1%
        assert measurement.compliance_percent > 100.0

    def test_measurement_serialization(self):
        """Measurement serializes to dict."""
        now = datetime.utcnow()
        measurement = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=now - timedelta(days=30),
            window_end=now,
            status=SLOStatus.HEALTHY,
        )
        d = measurement.to_dict()
        assert d["slo_name"] == "plugin_availability"
        assert d["status"] == "healthy"
        assert d["measured_value"] == 0.9951


class TestSLOMonitor:
    """Tests for SLO monitoring."""

    @pytest.fixture
    def monitor(self):
        """Fresh monitor for each test."""
        return SLOMonitor()

    def test_add_measurement(self, monitor):
        """Measurements can be added."""
        measurement = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.HEALTHY,
        )
        monitor.add_measurement(measurement)
        assert len(monitor.measurements) == 1

    def test_get_current_measurements(self, monitor):
        """Get latest measurement per SLO."""
        now = datetime.utcnow()

        # Add two measurements for same SLO (different times)
        m1 = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=now - timedelta(days=60),
            window_end=now - timedelta(days=30),
            status=SLOStatus.HEALTHY,
        )
        m2 = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9952,
            target_value=0.995,
            unit="availability",
            window_start=now - timedelta(days=30),
            window_end=now,
            status=SLOStatus.HEALTHY,
        )

        monitor.add_measurement(m1)
        monitor.add_measurement(m2)

        current = monitor.get_current_measurements()
        assert len(current) == 1
        # Should get the latest (m2)
        assert current["plugin_availability"].measured_value == 0.9952

    def test_overall_status_healthy(self, monitor):
        """Overall status is healthy when all SLOs meet targets."""
        for slo_name in ["plugin_availability", "delegation_latency_p95"]:
            measurement = SLOMeasurement(
                slo_name=slo_name,
                measured_value=100.0,
                target_value=150.0,
                unit="test",
                window_start=datetime.utcnow() - timedelta(days=30),
                window_end=datetime.utcnow(),
                status=SLOStatus.HEALTHY,
            )
            monitor.add_measurement(measurement)

        assert monitor.get_overall_status() == SLOStatus.HEALTHY

    def test_overall_status_warning(self, monitor):
        """Overall status is warning when any SLO is warning."""
        # One healthy
        m1 = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.HEALTHY,
        )

        # One warning
        m2 = SLOMeasurement(
            slo_name="delegation_latency_p95",
            measured_value=225.0,
            target_value=200.0,
            unit="latency_ms",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.WARNING,
        )

        monitor.add_measurement(m1)
        monitor.add_measurement(m2)

        assert monitor.get_overall_status() == SLOStatus.WARNING

    def test_overall_status_critical(self, monitor):
        """Overall status is critical when any SLO is critical."""
        # One critical
        m1 = SLOMeasurement(
            slo_name="audit_chain_integrity",
            measured_value=0.98,
            target_value=1.0,
            unit="integrity",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.CRITICAL,
        )

        monitor.add_measurement(m1)

        assert monitor.get_overall_status() == SLOStatus.CRITICAL

    def test_get_report(self, monitor):
        """Get full SLO report."""
        now = datetime.utcnow()
        measurement = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=now - timedelta(days=30),
            window_end=now,
            status=SLOStatus.HEALTHY,
            good_count=1000,
            bad_count=5,
        )
        monitor.add_measurement(measurement)

        report = monitor.get_report()
        assert "timestamp_utc" in report
        assert report["overall_status"] == "healthy"
        assert "slos" in report
        assert "summary" in report
        assert report["summary"]["healthy_slos"] == 1


class TestSLOIntegration:
    """Integration tests."""

    def test_full_monitoring_workflow(self):
        """Full workflow: create SLOs → measure → report."""
        monitor = SLOMonitor()

        # 1. Get SLO definitions
        slos = SLODefinitions.get_all_slos()
        assert len(slos) == 3

        # 2. Record measurements
        now = datetime.utcnow()
        measurements_data = [
            ("plugin_availability", 0.9951, SLOStatus.HEALTHY),
            ("delegation_latency_p95", 185.0, SLOStatus.HEALTHY),
            ("audit_chain_integrity", 1.0, SLOStatus.HEALTHY),
        ]

        for slo_name, value, status in measurements_data:
            slo = slos[slo_name]
            measurement = SLOMeasurement(
                slo_name=slo_name,
                measured_value=value,
                target_value=slo.target_value,
                unit=slo.unit,
                window_start=now - timedelta(days=30),
                window_end=now,
                status=status,
            )
            monitor.add_measurement(measurement)

        # 3. Get report
        report = monitor.get_report()
        assert report["overall_status"] == "healthy"
        assert len(report["slos"]) == 3
        assert report["summary"]["healthy_slos"] == 3


class TestSLOGlobal:
    """Test global SLO monitor."""

    def test_get_slo_monitor_singleton(self):
        """Global SLO monitor is singleton."""
        m1 = get_slo_monitor()
        m2 = get_slo_monitor()
        assert m1 is m2
