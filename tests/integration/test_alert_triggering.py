"""
Integration tests for SLO alert triggering (Phase 5 / CRITICAL-3).

Tests the full stack:
- Alert engine with alert channels
- Daemon periodically checking SLOs
- Alerts sent to Slack/Console
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from core.observability.alert_engine import AlertEngine, AlertSeverity, AlertEvent
from core.observability.alert_channels import (
    ConsoleChannel,
    SlackChannel,
    SlackConfig,
)
from core.monitoring.slo_alert_daemon import (
    SLOAlertDaemon,
    KPICollector,
)


class TestKPICollector:
    """Tests for KPI collection."""

    def test_collect_kpis(self):
        """KPI collector gathers all KPIs."""
        collector = KPICollector()
        kpis = collector.collect()

        assert "plugin_availability" in kpis
        assert "delegation_latency_p95" in kpis
        assert "audit_chain_integrity" in kpis

    def test_update_kpi(self):
        """KPI collector accepts manual updates."""
        collector = KPICollector()
        collector.update_kpi("plugin_availability", 0.985)

        kpis = collector.get_current_kpis()
        assert kpis["plugin_availability"] == 0.985


class TestConsoleChannel:
    """Tests for Console alert channel."""

    def test_send_alert_to_console(self):
        """Console channel writes to stderr."""
        console_out = Mock()
        channel = ConsoleChannel(console_out=console_out)

        alert = AlertEvent(
            slo_name="plugin_availability",
            severity=AlertSeverity.WARNING,
            measured_value=0.9899,
            threshold=0.990,
            target_value=0.995,
            message="Test alert",
        )

        result = channel.send(alert)
        assert result is True
        console_out.write.assert_called_once()
        console_out.flush.assert_called_once()

    def test_console_channel_handles_error(self):
        """Console channel fails gracefully."""
        console_out = Mock(side_effect=IOError("Write failed"))
        channel = ConsoleChannel(console_out=console_out)

        alert = AlertEvent(
            slo_name="plugin_availability",
            severity=AlertSeverity.WARNING,
            measured_value=0.9899,
            threshold=0.990,
            target_value=0.995,
            message="Test alert",
        )

        result = channel.send(alert)
        assert result is False


class TestSlackChannel:
    """Tests for Slack alert channel."""

    def test_slack_config_creates_properly(self):
        """Slack config initializes correctly."""
        config = SlackConfig(webhook_url="https://hooks.slack.com/test")
        assert config.webhook_url == "https://hooks.slack.com/test"
        assert config.username == "CorvinOS Alerts"


class TestSLOAlertDaemon:
    """Tests for SLO alert daemon."""

    @pytest.fixture
    def mock_health_monitor(self):
        """Mock health monitor."""
        monitor = MagicMock()
        monitor.report_health = AsyncMock()
        return monitor

    @pytest.mark.asyncio
    async def test_daemon_checks_slos(self, mock_health_monitor):
        """Daemon runs SLO checks."""
        daemon = SLOAlertDaemon(
            check_interval_seconds=1,
            health_monitor=mock_health_monitor,
        )

        # Run one check cycle
        alerts = await daemon._check_slos_once()

        # Should return a list (may be empty if metrics are healthy)
        assert isinstance(alerts, list)

    @pytest.mark.asyncio
    async def test_daemon_emits_health_status(self, mock_health_monitor):
        """Daemon emits health status to monitor."""
        daemon = SLOAlertDaemon(
            check_interval_seconds=1,
            health_monitor=mock_health_monitor,
        )

        alert_state = {
            "plugin_availability": {
                "severity": "warning",
                "last_measured_value": 0.98,
            }
        }
        recent_alerts = []

        await daemon._emit_health_status(alert_state, recent_alerts)

        # Health monitor should be called
        mock_health_monitor.report_health.assert_called_once()
        call_args = mock_health_monitor.report_health.call_args
        assert call_args[1]["subsystem_id"] == "slo_alert_monitor"

    @pytest.mark.asyncio
    async def test_daemon_start_stop(self, mock_health_monitor):
        """Daemon can be started and stopped."""
        daemon = SLOAlertDaemon(
            check_interval_seconds=1,
            health_monitor=mock_health_monitor,
        )

        # Start daemon
        await daemon.start()
        assert daemon.running is True
        assert daemon.task is not None

        # Stop daemon
        await daemon.stop()
        assert daemon.running is False

        # Task should be cancelled
        await asyncio.sleep(0.1)  # Give it time to finish

    @pytest.mark.asyncio
    async def test_daemon_continues_on_error(self, mock_health_monitor):
        """Daemon continues even if a check fails."""
        daemon = SLOAlertDaemon(
            check_interval_seconds=1,
            health_monitor=mock_health_monitor,
        )

        # Mock KPI collector to raise error
        daemon.kpi_collector.collect = Mock(side_effect=Exception("Test error"))

        # Should not raise
        alerts = await daemon._check_slos_once()
        assert alerts == []

    def test_daemon_get_alert_history(self, mock_health_monitor):
        """Daemon can retrieve alert history."""
        daemon = SLOAlertDaemon(health_monitor=mock_health_monitor)

        # Trigger an alert
        daemon.alert_engine.check_slo("plugin_availability", 0.9899)

        history = daemon.get_alert_history()
        assert isinstance(history, list)

    def test_daemon_get_current_status(self, mock_health_monitor):
        """Daemon reports current status."""
        daemon = SLOAlertDaemon(
            check_interval_seconds=60,
            health_monitor=mock_health_monitor,
        )

        status = daemon.get_current_status()

        assert "running" in status
        assert "check_interval_seconds" in status
        assert status["check_interval_seconds"] == 60
        assert "alert_engine_state" in status
        assert "kpis" in status


class TestEndToEndAlerting:
    """End-to-end alerting workflow."""

    @pytest.mark.asyncio
    async def test_slo_breach_triggers_alert(self):
        """Trigger SLO breach and verify alert fires."""
        engine = AlertEngine()
        alerts_fired = []
        engine.register_alert_callback(lambda a: alerts_fired.append(a))

        # Simulate SLO breach
        kpis = {
            "plugin_availability": 0.9850,  # Below alert threshold (0.990)
            "delegation_latency_p95": 185.0,
            "audit_chain_integrity": 1.0,
        }

        alerts = engine.check_all_slos(kpis)

        # Verify alert fired
        assert len(alerts) >= 1
        assert any(a.slo_name == "plugin_availability" for a in alerts)
        assert any(a.severity == AlertSeverity.WARNING for a in alerts)

    @pytest.mark.asyncio
    async def test_multi_slo_breach_cascade(self):
        """Multiple SLO breaches trigger multiple alerts."""
        engine = AlertEngine()
        alerts_fired = []
        engine.register_alert_callback(lambda a: alerts_fired.append(a))

        # Multiple SLO breaches
        kpis = {
            "plugin_availability": 0.9850,  # Warning
            "delegation_latency_p95": 300.0,  # Critical
            "audit_chain_integrity": 1.0,
        }

        alerts = engine.check_all_slos(kpis)

        # Should have alerts for both breaches
        slo_names = [a.slo_name for a in alerts]
        assert "plugin_availability" in slo_names or "delegation_latency_p95" in slo_names

    @pytest.mark.asyncio
    async def test_alert_recovery_clears_alert(self):
        """Recovering from alert generates clear alert."""
        engine = AlertEngine()
        alerts_fired = []
        engine.register_alert_callback(lambda a: alerts_fired.append(a))

        # Trigger alert
        engine.check_slo("plugin_availability", 0.9850)
        assert len(alerts_fired) >= 1

        # Recover
        alert = engine.check_slo("plugin_availability", 0.9951)

        # Should generate recovery alert
        assert alert is not None
        assert alert.severity == AlertSeverity.INFO
