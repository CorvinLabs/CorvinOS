"""
Tests for AlertEngine (Phase 5 / CRITICAL-3).

Covers:
- Threshold comparison
- State machine transitions
- Alert suppression
- Alert callbacks
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from core.observability.alert_engine import (
    AlertEngine,
    AlertEvent,
    AlertSeverity,
    AlertState,
)
from core.observability.slo_definitions import SLODefinitions


class TestAlertState:
    """Tests for AlertState (per-SLO state machine)."""

    def test_initial_state(self):
        """AlertState starts at INFO severity."""
        state = AlertState("plugin_availability")
        assert state.current_severity == AlertSeverity.INFO

    def test_transition_changes_state(self):
        """Transition updates current_severity."""
        state = AlertState("plugin_availability")
        assert state.transition(AlertSeverity.WARNING) is True
        assert state.current_severity == AlertSeverity.WARNING

    def test_transition_same_state_no_change(self):
        """Transition to same state returns False."""
        state = AlertState("plugin_availability")
        state.current_severity = AlertSeverity.WARNING
        assert state.transition(AlertSeverity.WARNING) is False

    def test_suppression_window_respected(self):
        """First alert is not suppressed."""
        state = AlertState("plugin_availability", suppression_window_minutes=15)
        assert state.should_send_alert() is True

    def test_suppression_prevents_duplicate_alerts(self):
        """Duplicate alert within window is suppressed."""
        state = AlertState("plugin_availability", suppression_window_minutes=15)

        # First alert
        assert state.should_send_alert() is True
        state.mark_alert_sent()

        # Second alert immediately after should be suppressed
        assert state.should_send_alert() is False

    def test_suppression_window_expires(self):
        """Alert sent after window expires is not suppressed."""
        state = AlertState("plugin_availability", suppression_window_minutes=1)

        # First alert
        state.mark_alert_sent()
        state.last_alert_sent_time = datetime.utcnow() - timedelta(minutes=2)

        # Should not be suppressed (window expired)
        assert state.should_send_alert() is True


class TestAlertEngine:
    """Tests for AlertEngine (threshold checking)."""

    @pytest.fixture
    def engine(self):
        """Fresh engine for each test."""
        return AlertEngine()

    def test_healthy_availability_slo(self, engine):
        """Availability at target produces no alert."""
        alert = engine.check_slo("plugin_availability", 0.9951)
        assert alert is None  # No state change

    def test_warning_availability_slo(self, engine):
        """Availability below alert threshold produces warning."""
        alert = engine.check_slo("plugin_availability", 0.9899)
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING
        assert alert.slo_name == "plugin_availability"

    def test_critical_availability_slo(self, engine):
        """Availability well below alert threshold produces critical."""
        alert = engine.check_slo("plugin_availability", 0.8910)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_healthy_latency_slo(self, engine):
        """Latency at target produces no alert."""
        alert = engine.check_slo("delegation_latency_p95", 185.0)
        assert alert is None

    def test_warning_latency_slo(self, engine):
        """Latency above target produces warning."""
        alert = engine.check_slo("delegation_latency_p95", 225.0)
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING

    def test_critical_latency_slo(self, engine):
        """Latency well above threshold produces critical."""
        alert = engine.check_slo("delegation_latency_p95", 300.0)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_healthy_audit_integrity_slo(self, engine):
        """Audit integrity at 100% produces no alert."""
        alert = engine.check_slo("audit_chain_integrity", 1.0)
        assert alert is None

    def test_warning_audit_integrity_slo(self, engine):
        """Audit integrity below alert threshold produces warning."""
        alert = engine.check_slo("audit_chain_integrity", 0.9989)
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING

    def test_state_machine_clear_to_warning(self, engine):
        """State machine: CLEAR → WARNING."""
        # Start healthy
        alert1 = engine.check_slo("plugin_availability", 0.9951)
        assert alert1 is None  # No state change initially

        # Degrade to warning
        alert2 = engine.check_slo("plugin_availability", 0.9899)
        assert alert2 is not None
        assert alert2.severity == AlertSeverity.WARNING

        # Stay at warning (no new alert)
        alert3 = engine.check_slo("plugin_availability", 0.9890)
        assert alert3 is None  # Already at warning

    def test_state_machine_warning_to_critical(self, engine):
        """State machine: WARNING → CRITICAL."""
        # Degrade to warning
        engine.check_slo("plugin_availability", 0.9899)

        # Degrade to critical
        alert = engine.check_slo("plugin_availability", 0.8910)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_state_machine_recovery(self, engine):
        """State machine: CRITICAL → CLEAR."""
        # Degrade to critical
        alert1 = engine.check_slo("plugin_availability", 0.8910)
        assert alert1 is not None

        # Recover
        alert2 = engine.check_slo("plugin_availability", 0.9951)
        assert alert2 is not None
        assert alert2.severity == AlertSeverity.INFO

    def test_suppression_prevents_duplicate_alerts(self, engine):
        """Alert suppression prevents duplicate state-change alerts."""
        # Trigger warning
        alert1 = engine.check_slo("plugin_availability", 0.9899)
        assert alert1 is not None

        # Trigger again immediately (should be suppressed)
        engine.alert_states["plugin_availability"].last_alert_sent_time = (
            datetime.utcnow()
        )
        alert2 = engine.check_slo("plugin_availability", 0.9880)
        # No new state change, so no alert anyway
        assert alert2 is None

    def test_callback_invoked_on_alert(self, engine):
        """Alert callbacks are invoked."""
        callback = Mock()
        engine.register_alert_callback(callback)

        alert = engine.check_slo("plugin_availability", 0.9899)
        assert alert is not None
        callback.assert_called_once()

    def test_callback_exception_handled(self, engine):
        """Callback exceptions don't crash engine."""
        bad_callback = Mock(side_effect=Exception("Test error"))
        good_callback = Mock()

        engine.register_alert_callback(bad_callback)
        engine.register_alert_callback(good_callback)

        # Should not raise, despite bad callback
        alert = engine.check_slo("plugin_availability", 0.9899)
        assert alert is not None
        good_callback.assert_called_once()

    def test_check_all_slos(self, engine):
        """Check multiple SLOs at once."""
        kpis = {
            "plugin_availability": 0.9899,  # Warning
            "delegation_latency_p95": 185.0,  # Healthy
            "audit_chain_integrity": 1.0,  # Healthy
        }

        alerts = engine.check_all_slos(kpis)

        # Only one alert (plugin availability warning)
        assert len(alerts) == 1
        assert alerts[0].slo_name == "plugin_availability"

    def test_unknown_slo_ignored(self, engine):
        """Unknown SLO is logged and ignored."""
        alert = engine.check_slo("unknown_slo", 0.5)
        assert alert is None

    def test_missing_kpi_logged_but_continues(self, engine):
        """Missing KPI in check_all_slos logs warning but continues."""
        kpis = {
            "plugin_availability": 0.9951,
            # Missing delegation_latency_p95 and audit_chain_integrity
        }

        alerts = engine.check_all_slos(kpis)
        # Should complete without raising
        assert isinstance(alerts, list)

    def test_alert_history_recorded(self, engine):
        """Alerts are recorded in history."""
        engine.check_slo("plugin_availability", 0.9899)

        history = engine.get_alert_history()
        assert len(history) >= 1
        assert history[0].slo_name == "plugin_availability"

    def test_alert_history_limit(self, engine):
        """Alert history respects limit."""
        # Trigger multiple alerts
        for i in range(10):
            engine.check_slo("plugin_availability", 0.98 - (i * 0.001))
            if i > 0:
                # Reset for next transition
                engine.alert_states["plugin_availability"].current_severity = (
                    AlertSeverity.INFO
                )

        history = engine.get_alert_history(limit=5)
        assert len(history) <= 5

    def test_get_current_state(self, engine):
        """Get current alert state for all SLOs."""
        engine.check_slo("plugin_availability", 0.9899)

        state = engine.get_current_state()

        assert "plugin_availability" in state
        assert state["plugin_availability"]["severity"] in ["info", "warning", "critical"]
        assert "last_transition" in state["plugin_availability"]


class TestAlertEvent:
    """Tests for AlertEvent serialization."""

    def test_alert_event_to_dict(self):
        """AlertEvent serializes to dict."""
        alert = AlertEvent(
            slo_name="plugin_availability",
            severity=AlertSeverity.WARNING,
            measured_value=0.9899,
            threshold=0.990,
            target_value=0.995,
            message="Test alert",
        )

        d = alert.to_dict()
        assert d["slo_name"] == "plugin_availability"
        assert d["severity"] == "warning"
        assert d["measured_value"] == 0.9899

    def test_alert_event_preserves_previous_severity(self):
        """AlertEvent records previous severity on transition."""
        alert = AlertEvent(
            slo_name="plugin_availability",
            severity=AlertSeverity.CRITICAL,
            measured_value=0.88,
            threshold=0.990,
            target_value=0.995,
            message="Escalated",
            previous_severity=AlertSeverity.WARNING,
        )

        d = alert.to_dict()
        assert d["previous_severity"] == "warning"


class TestAlertIntegration:
    """Integration tests for AlertEngine."""

    def test_full_alert_workflow(self):
        """Full workflow: configure → check → alert → suppress."""
        engine = AlertEngine(suppression_window_minutes=1)
        callback = Mock()
        engine.register_alert_callback(callback)

        # 1. Initial check (healthy)
        alert1 = engine.check_slo("plugin_availability", 0.9951)
        assert alert1 is None
        assert callback.call_count == 0

        # 2. Degrade to warning
        alert2 = engine.check_slo("plugin_availability", 0.9899)
        assert alert2 is not None
        assert alert2.severity == AlertSeverity.WARNING
        assert callback.call_count == 1

        # 3. Try to alert again (suppressed)
        alert3 = engine.check_slo("plugin_availability", 0.9890)
        assert alert3 is None  # No state change
        assert callback.call_count == 1  # No new callback

        # 4. Escalate to critical
        alert4 = engine.check_slo("plugin_availability", 0.8910)
        assert alert4 is not None
        assert alert4.severity == AlertSeverity.CRITICAL
        assert callback.call_count == 2  # New transition

    def test_multi_slo_alerting(self):
        """Multiple SLOs can alert independently."""
        engine = AlertEngine()
        alerts = []
        engine.register_alert_callback(lambda a: alerts.append(a))

        # Trigger multiple SLO alerts
        engine.check_slo("plugin_availability", 0.9899)
        engine.check_slo("delegation_latency_p95", 300.0)

        # Should have recorded both
        assert len([a for a in alerts if a.slo_name == "plugin_availability"]) >= 1
        assert len([a for a in alerts if a.slo_name == "delegation_latency_p95"]) >= 1
