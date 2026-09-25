"""
Phase 4: Real-Time Drift Detection & Alerting — Test Suite

Tests cover:
1. Unit: Alert routing by severity (4 test cases)
2. Integration: Polling loop + drift aggregation (4 test cases)
3. E2E: 3-instance scenario with Slack/PagerDuty (2 test cases)
Total: 10+ test cases, all passing, mocked integrations
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass
from datetime import datetime

from core.monitoring.drift_detector import (
    DriftDetectionService,
    SlackAlerter,
    PagerDutyAlerter,
    AlertSeverity,
    MonitoringEvent,
)
from core.deployment.state_sync import (
    DeploymentStateManager,
    DriftAlert,
    DriftSeverity,
)


class TestAlertSeverityRouting:
    """Unit: Test alert routing by severity"""

    def test_critical_severity_routes_to_pagerduty_and_slack(self):
        """CRITICAL alert → page oncall + Slack"""
        mock_slack = Mock(spec=SlackAlerter)
        mock_pd = Mock(spec=PagerDutyAlerter)

        service = DriftDetectionService(
            slack_alerter=mock_slack,
            pagerduty_alerter=mock_pd,
        )

        service.alert_with_severity(
            DriftSeverity.CRITICAL,
            "Code version mismatch",
            "prod-us-east-1",
            "CODE_VERSION_DRIFT",
        )

        # Both Slack and PagerDuty should be called
        mock_slack.send_alert.assert_called_once()
        mock_pd.trigger_incident.assert_called_once()

    def test_high_severity_routes_to_slack_only(self):
        """HIGH alert → Slack only (no PagerDuty)"""
        mock_slack = Mock(spec=SlackAlerter)
        mock_pd = Mock(spec=PagerDutyAlerter)

        service = DriftDetectionService(
            slack_alerter=mock_slack,
            pagerduty_alerter=mock_pd,
        )

        service.alert_with_severity(
            DriftSeverity.HIGH,
            "Config mismatch",
            "staging-eu-west-1",
            "CONFIG_DRIFT",
        )

        # Only Slack should be called
        mock_slack.send_alert.assert_called_once()
        mock_pd.trigger_incident.assert_not_called()

    def test_medium_severity_logs_no_external_alert(self):
        """MEDIUM alert → log only (no Slack/PagerDuty)"""
        mock_slack = Mock(spec=SlackAlerter)
        mock_pd = Mock(spec=PagerDutyAlerter)

        service = DriftDetectionService(
            slack_alerter=mock_slack,
            pagerduty_alerter=mock_pd,
        )

        service.alert_with_severity(
            DriftSeverity.MEDIUM,
            "Warning config",
            "dev-local",
            "CONFIG_VALIDATION_FAILED",
        )

        # Neither should be called
        mock_slack.send_alert.assert_not_called()
        mock_pd.trigger_incident.assert_not_called()

    def test_low_severity_metrics_only(self):
        """LOW alert → metrics only"""
        mock_slack = Mock(spec=SlackAlerter)
        mock_pd = Mock(spec=PagerDutyAlerter)

        service = DriftDetectionService(
            slack_alerter=mock_slack,
            pagerduty_alerter=mock_pd,
        )

        service.alert_with_severity(
            DriftSeverity.LOW,
            "Low impact metric",
            "bench-test",
            "METRICS_UPDATE",
        )

        # Neither should be called
        mock_slack.send_alert.assert_not_called()
        mock_pd.trigger_incident.assert_not_called()


class TestSlackIntegration:
    """Unit: Slack alert formatting and sending"""

    def test_slack_alert_with_color_mapping(self):
        """Slack alert uses correct color for severity"""
        alerter = SlackAlerter(webhook_url="https://hooks.slack.com/test")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            alerter.send_alert(
                AlertSeverity.CRITICAL,
                "Critical drift detected",
                "prod-1",
                "CODE_VERSION_DRIFT",
            )

            # Verify POST was called
            assert mock_post.called
            payload = mock_post.call_args[1]["json"]

            # Verify color is red for CRITICAL
            assert payload["attachments"][0]["color"] == "#FF0000"

    def test_slack_gracefully_handles_webhook_failure(self):
        """Slack alerter doesn't crash on webhook failure"""
        alerter = SlackAlerter(webhook_url="https://hooks.slack.com/invalid")

        with patch("requests.post") as mock_post:
            mock_post.side_effect = Exception("Network error")
            # Should not raise
            alerter.send_alert(
                AlertSeverity.HIGH,
                "High drift",
                "staging-1",
                "CONFIG_DRIFT",
            )

    def test_slack_disabled_when_webhook_url_missing(self):
        """Slack alerter gracefully disables when no webhook"""
        alerter = SlackAlerter(webhook_url=None)

        with patch("requests.post") as mock_post:
            alerter.send_alert(
                AlertSeverity.CRITICAL,
                "Should not send",
                "prod-1",
                "CODE_VERSION_DRIFT",
            )
            # POST should not be called
            mock_post.assert_not_called()


class TestPagerDutyIntegration:
    """Unit: PagerDuty incident creation"""

    def test_pagerduty_triggers_incident_on_critical(self):
        """PagerDuty incident includes dedup key + remediation"""
        alerter = PagerDutyAlerter(api_key="test_key")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            alerter.trigger_incident(
                "Code version mismatch",
                "prod-us-east-1",
                "CODE_VERSION_DRIFT",
            )

            assert mock_post.called
            payload = mock_post.call_args[1]["json"]

            # Verify dedup key for idempotency
            assert payload["dedup_key"] == "drift_prod-us-east-1_CODE_VERSION_DRIFT"

            # Verify incident details
            assert payload["payload"]["severity"] == "critical"
            assert "remediation_action" in payload["payload"]["custom_details"]

    def test_pagerduty_resolves_incident(self):
        """PagerDuty can resolve an incident"""
        alerter = PagerDutyAlerter(api_key="test_key")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            alerter.resolve_incident("prod-us-east-1", "CODE_VERSION_DRIFT")

            assert mock_post.called
            payload = mock_post.call_args[1]["json"]
            assert payload["event_action"] == "resolve"
            assert payload["dedup_key"] == "drift_prod-us-east-1_CODE_VERSION_DRIFT"

    def test_pagerduty_disabled_when_api_key_missing(self):
        """PagerDuty gracefully disables when no API key"""
        alerter = PagerDutyAlerter(api_key=None)

        with patch("requests.post") as mock_post:
            alerter.trigger_incident(
                "Should not send",
                "prod-1",
                "CODE_VERSION_DRIFT",
            )
            mock_post.assert_not_called()


class TestMonitoringLoop:
    """Integration: Polling loop + drift detection"""

    def test_start_monitoring_creates_background_thread(self):
        """start_monitoring() creates non-blocking background thread"""
        service = DriftDetectionService()

        service.start_monitoring()
        assert service.is_running
        assert service.monitoring_thread is not None
        assert service.monitoring_thread.daemon

        service.stop_monitoring()

    def test_monitor_all_instances_detects_drifts(self):
        """monitor_all_instances() uses Phase 1 drift detection"""
        mock_deployment_manager = Mock(spec=DeploymentStateManager)

        # Mock drift detection result
        drift = DriftAlert(
            alert_type="CODE_VERSION_DRIFT",
            severity=DriftSeverity.CRITICAL,
            instance_id="prod-1",
            expected_value="abc123",
            actual_value="def456",
            message="Code version mismatch",
            timestamp=datetime.utcnow().isoformat(),
        )
        mock_deployment_manager.instance_states = {"prod-1": Mock()}
        mock_deployment_manager.detect_drift.return_value = [drift]

        service = DriftDetectionService(deployment_manager=mock_deployment_manager)
        events = service.monitor_all_instances()

        assert len(events) == 1
        assert events[0].instance_id == "prod-1"
        assert events[0].drift_type == "CODE_VERSION_DRIFT"

    def test_monitor_all_instances_aggregates_multiple_drifts(self):
        """monitor_all_instances() handles multiple instances + drifts"""
        mock_deployment_manager = Mock(spec=DeploymentStateManager)

        drifts_prod = [
            DriftAlert(
                alert_type="CODE_VERSION_DRIFT",
                severity=DriftSeverity.CRITICAL,
                instance_id="prod-1",
                expected_value="abc123",
                actual_value="def456",
                message="Prod code mismatch",
                timestamp=datetime.utcnow().isoformat(),
            ),
        ]

        drifts_staging = [
            DriftAlert(
                alert_type="MANIFEST_HASH_MISMATCH",
                severity=DriftSeverity.HIGH,
                instance_id="staging-1",
                expected_value="hash1",
                actual_value="hash2",
                message="Staging manifest mismatch",
                timestamp=datetime.utcnow().isoformat(),
            ),
        ]

        mock_deployment_manager.instance_states = {"prod-1": Mock(), "staging-1": Mock()}
        mock_deployment_manager.detect_drift.side_effect = [drifts_prod, drifts_staging]

        service = DriftDetectionService(deployment_manager=mock_deployment_manager)
        events = service.monitor_all_instances()

        assert len(events) == 2
        assert events[0].instance_id == "prod-1"
        assert events[1].instance_id == "staging-1"

    def test_monitoring_events_stored_for_dashboard(self):
        """Recent events retrievable for dashboard display"""
        mock_deployment_manager = Mock(spec=DeploymentStateManager)
        drift = DriftAlert(
            alert_type="CODE_VERSION_DRIFT",
            severity=DriftSeverity.CRITICAL,
            instance_id="prod-1",
            expected_value="a",
            actual_value="b",
            message="Mismatch",
            timestamp=datetime.utcnow().isoformat(),
        )
        mock_deployment_manager.instance_states = {"prod-1": Mock()}
        mock_deployment_manager.detect_drift.return_value = [drift]

        service = DriftDetectionService(deployment_manager=mock_deployment_manager)
        service.monitor_all_instances()

        recent = service.get_recent_events(limit=10)
        assert len(recent) == 1
        assert recent[0].drift_type == "CODE_VERSION_DRIFT"


class TestE2E3InstanceScenario:
    """E2E: 3-instance monitoring with alert verification"""

    def test_e2e_3_instance_drift_detection_and_alerting(self):
        """E2E: Monitor 3 instances, detect drifts, route alerts correctly"""
        # Setup mock deployment manager with 3 instances
        mock_deployment_manager = Mock(spec=DeploymentStateManager)

        # Define drifts for each instance
        prod_drifts = [
            DriftAlert(
                alert_type="CODE_VERSION_DRIFT",
                severity=DriftSeverity.CRITICAL,
                instance_id="prod-us-east-1",
                expected_value="v1.2.3",
                actual_value="v1.2.2",
                message="Prod US-East is behind version",
                timestamp=datetime.utcnow().isoformat(),
            ),
        ]

        staging_drifts = [
            DriftAlert(
                alert_type="CONFIG_DRIFT",
                severity=DriftSeverity.HIGH,
                instance_id="staging-eu-west-1",
                expected_value="config_hash_x",
                actual_value="config_hash_y",
                message="Staging config out of sync",
                timestamp=datetime.utcnow().isoformat(),
            ),
        ]

        dev_drifts = []  # Dev is in sync

        mock_deployment_manager.instance_states = {
            "prod-us-east-1": Mock(),
            "staging-eu-west-1": Mock(),
            "dev-local": Mock(),
        }
        mock_deployment_manager.detect_drift.side_effect = [
            prod_drifts,
            staging_drifts,
            dev_drifts,
        ]

        # Setup mocked Slack and PagerDuty
        mock_slack = Mock(spec=SlackAlerter)
        mock_pd = Mock(spec=PagerDutyAlerter)

        service = DriftDetectionService(
            deployment_manager=mock_deployment_manager,
            slack_alerter=mock_slack,
            pagerduty_alerter=mock_pd,
        )

        # Run monitoring
        events = service.monitor_all_instances()

        # Verify: 2 drifts detected (prod + staging)
        assert len(events) == 2

        # Verify: Prod CRITICAL → PagerDuty + Slack
        assert mock_pd.trigger_incident.call_count >= 1
        assert mock_slack.send_alert.call_count >= 1

        # Verify event details
        prod_event = next(e for e in events if e.instance_id == "prod-us-east-1")
        assert prod_event.severity == AlertSeverity.CRITICAL
        assert prod_event.drift_type == "CODE_VERSION_DRIFT"

        staging_event = next(e for e in events if e.instance_id == "staging-eu-west-1")
        assert staging_event.severity == AlertSeverity.HIGH
        assert staging_event.drift_type == "CONFIG_DRIFT"

    def test_e2e_monitoring_loop_runs_for_multiple_intervals(self):
        """E2E: Monitoring loop polling over multiple 30s intervals"""
        mock_deployment_manager = Mock(spec=DeploymentStateManager)

        # Simulate drifts appearing on second poll
        drifts_batch1 = []  # First poll: no drifts
        drifts_batch2 = [
            DriftAlert(
                alert_type="PLUGIN_MISSING",
                severity=DriftSeverity.CRITICAL,
                instance_id="prod-1",
                expected_value="plugin-x-v2.0",
                actual_value="missing",
                message="Plugin X missing on prod",
                timestamp=datetime.utcnow().isoformat(),
            ),
        ]

        mock_deployment_manager.instance_states = {"prod-1": Mock()}
        mock_deployment_manager.detect_drift.side_effect = [drifts_batch1, drifts_batch2]

        mock_slack = Mock(spec=SlackAlerter)
        mock_pd = Mock(spec=PagerDutyAlerter)

        service = DriftDetectionService(
            deployment_manager=mock_deployment_manager,
            slack_alerter=mock_slack,
            pagerduty_alerter=mock_pd,
        )

        # Simulate 2 polling cycles
        events1 = service.monitor_all_instances()
        events2 = service.monitor_all_instances()

        # First cycle: no drifts
        assert len(events1) == 0

        # Second cycle: drift detected
        assert len(events2) == 1
        assert events2[0].drift_type == "PLUGIN_MISSING"

        # Total events in service history: 1
        assert len(service.monitoring_events) == 1


class TestCustomAlertHandlers:
    """Test: Custom alert handler registration"""

    def test_custom_alert_handler_called_on_alert(self):
        """Custom handlers are invoked when alerts route"""
        mock_handler = Mock()
        service = DriftDetectionService()
        service.register_alert_handler(mock_handler)

        service.alert_with_severity(
            DriftSeverity.HIGH,
            "Test alert",
            "test-instance",
            "TEST_DRIFT",
        )

        # Handler should be called
        mock_handler.assert_called_once()
        args = mock_handler.call_args[0]
        assert args[0] == AlertSeverity.HIGH
        assert "Test alert" in args[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
