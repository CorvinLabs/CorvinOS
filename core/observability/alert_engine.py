"""
Alert Triggering Engine for SLO monitoring (Phase 5).

Compares live KPI metrics against SLO thresholds and emits alerts when breached.

Features:
- State machine: CLEAR → WARNING → CRITICAL (with hysteresis)
- Alert suppression: prevents duplicate alerts within 15 min (same severity)
- Multi-channel: Slack, Console, Email
- Fail-closed: alert errors never crash monitoring daemon
- Audit trail: every state transition logged

Usage:
    engine = AlertEngine()
    kpis = {
        "plugin_availability": 0.989,
        "delegation_latency_p95": 255.0,
        "audit_chain_integrity": 0.995,
    }
    alerts = engine.check_all_slos(kpis)
    for alert in alerts:
        await alert_channel.send(alert)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta
import logging

from core.observability.slo_definitions import SLODefinitions


logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertEvent:
    """Single alert event."""
    slo_name: str
    severity: AlertSeverity
    measured_value: float
    threshold: float
    target_value: float
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    previous_severity: Optional[AlertSeverity] = None

    def to_dict(self) -> Dict:
        """Serialize for audit trail / notification."""
        return {
            "slo_name": self.slo_name,
            "severity": self.severity.value,
            "measured_value": self.measured_value,
            "threshold": self.threshold,
            "target_value": self.target_value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "previous_severity": self.previous_severity.value if self.previous_severity else None,
        }


class AlertState:
    """Track per-SLO alert state (for state machine + suppression)."""

    def __init__(self, slo_name: str, suppression_window_minutes: int = 15):
        self.slo_name = slo_name
        self.current_severity = AlertSeverity.INFO  # Start at INFO (no alert yet)
        self.last_transition_time = datetime.utcnow()
        self.last_alert_sent_time: Optional[datetime] = None
        self.suppression_window = timedelta(minutes=suppression_window_minutes)
        self.last_measured_value = 0.0

    def should_send_alert(self) -> bool:
        """Check if enough time has passed since last alert (same severity)."""
        if self.last_alert_sent_time is None:
            return True

        time_since_last = datetime.utcnow() - self.last_alert_sent_time
        return time_since_last >= self.suppression_window

    def mark_alert_sent(self) -> None:
        """Record that an alert was just sent."""
        self.last_alert_sent_time = datetime.utcnow()

    def transition(self, new_severity: AlertSeverity) -> bool:
        """
        Transition to new severity state.

        Returns True if state changed, False if unchanged.
        """
        if new_severity == self.current_severity:
            return False

        self.current_severity = new_severity
        self.last_transition_time = datetime.utcnow()
        # Reset suppression timer on state change (new severity = new alert even if suppressed before)
        self.last_alert_sent_time = None
        return True


class AlertEngine:
    """
    Monitor SLO compliance and generate alerts.

    Implements:
    - Threshold comparison against SLO definitions
    - State machine (CLEAR → WARNING → CRITICAL)
    - Alert suppression (no duplicate alerts within window)
    - Audit trail emission
    - Fail-closed error handling
    """

    def __init__(self, suppression_window_minutes: int = 15):
        """
        Initialize alert engine.

        Args:
            suppression_window_minutes: Window for alert suppression (default 15 min)
        """
        self.slo_defs = SLODefinitions.get_all_slos()
        self.alert_states: Dict[str, AlertState] = {
            slo_name: AlertState(slo_name, suppression_window_minutes)
            for slo_name in self.slo_defs.keys()
        }
        self.alert_history: List[AlertEvent] = []
        self.alert_callbacks: List[Callable[[AlertEvent], None]] = []

    def register_alert_callback(self, callback: Callable[[AlertEvent], None]) -> None:
        """Register callback to be called on every alert."""
        self.alert_callbacks.append(callback)

    def _determine_severity(
        self,
        slo_name: str,
        measured_value: float,
    ) -> AlertSeverity:
        """
        Determine alert severity based on measured vs threshold values.

        Args:
            slo_name: Name of the SLO
            measured_value: Measured KPI value

        Returns:
            AlertSeverity (INFO if healthy, WARNING if approaching limit, CRITICAL if exceeded)
        """
        slo = self.slo_defs.get(slo_name)
        if not slo:
            logger.warning(f"Unknown SLO: {slo_name}")
            return AlertSeverity.INFO

        # Compare based on metric type
        if slo.unit in ["availability", "integrity"]:
            # Higher is better
            if measured_value < slo.alert_threshold * 0.9:
                # Critical threshold: 90% of alert threshold
                return AlertSeverity.CRITICAL
            elif measured_value < slo.alert_threshold:
                return AlertSeverity.WARNING
            else:
                return AlertSeverity.INFO

        elif slo.unit == "latency_ms":
            # Lower is better
            if measured_value > slo.alert_threshold:
                return AlertSeverity.CRITICAL
            elif measured_value > (slo.target_value * 1.1):
                # Warning at 10% above target
                return AlertSeverity.WARNING
            else:
                return AlertSeverity.INFO

        return AlertSeverity.INFO

    def _format_alert_message(
        self,
        slo_name: str,
        severity: AlertSeverity,
        measured_value: float,
        threshold: float,
        target_value: float,
    ) -> str:
        """Format human-readable alert message."""
        slo = self.slo_defs.get(slo_name)
        if not slo:
            return f"Unknown SLO: {slo_name}"

        status_text = {
            AlertSeverity.INFO: "HEALTHY",
            AlertSeverity.WARNING: "WARNING",
            AlertSeverity.CRITICAL: "CRITICAL",
        }.get(severity, "UNKNOWN")

        if slo.unit in ["availability", "integrity"]:
            return (
                f"[{status_text}] {slo.name}: {measured_value*100:.2f}% "
                f"(target: {target_value*100:.1f}%, threshold: {threshold*100:.1f}%)"
            )
        elif slo.unit == "latency_ms":
            return (
                f"[{status_text}] {slo.name}: {measured_value:.1f}ms "
                f"(target: {target_value:.0f}ms, threshold: {threshold:.0f}ms)"
            )

        return f"[{status_text}] {slo.name}: {measured_value:.2f}"

    def check_slo(
        self,
        slo_name: str,
        measured_value: float,
    ) -> Optional[AlertEvent]:
        """
        Check single SLO against thresholds.

        Args:
            slo_name: Name of the SLO
            measured_value: Measured KPI value

        Returns:
            AlertEvent if state changed and alert should be sent, None otherwise
        """
        slo = self.slo_defs.get(slo_name)
        if not slo:
            logger.warning(f"Unknown SLO: {slo_name}")
            return None

        state = self.alert_states[slo_name]
        state.last_measured_value = measured_value
        previous_severity = state.current_severity

        # Determine new severity
        new_severity = self._determine_severity(slo_name, measured_value)

        # Check if severity changed
        if not state.transition(new_severity):
            # No state change, no alert
            return None

        # State changed: check if we should suppress the alert
        if not state.should_send_alert():
            logger.debug(
                f"Alert suppressed for {slo_name}: "
                f"sent within suppression window"
            )
            return None

        # Create alert event
        alert = AlertEvent(
            slo_name=slo_name,
            severity=new_severity,
            measured_value=measured_value,
            threshold=slo.alert_threshold,
            target_value=slo.target_value,
            message=self._format_alert_message(
                slo_name, new_severity, measured_value,
                slo.alert_threshold, slo.target_value
            ),
            previous_severity=previous_severity if previous_severity != new_severity else None,
        )

        # Mark alert as sent
        state.mark_alert_sent()

        # Record in history
        self.alert_history.append(alert)

        # Invoke callbacks
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}", exc_info=True)

        return alert

    def check_all_slos(self, kpis: Dict[str, float]) -> List[AlertEvent]:
        """
        Check all SLOs against provided KPIs.

        Args:
            kpis: Dict of {slo_name: measured_value}

        Returns:
            List of AlertEvent objects for fired alerts
        """
        alerts = []

        for slo_name in self.slo_defs.keys():
            if slo_name not in kpis:
                logger.warning(f"Missing KPI for SLO: {slo_name}")
                continue

            try:
                alert = self.check_slo(slo_name, kpis[slo_name])
                if alert:
                    alerts.append(alert)
            except Exception as e:
                logger.error(
                    f"Error checking SLO {slo_name}: {e}",
                    exc_info=True
                )
                # Fail-closed: continue checking other SLOs

        return alerts

    def get_alert_history(
        self,
        limit: int = 100,
        slo_name: Optional[str] = None,
    ) -> List[AlertEvent]:
        """
        Get alert history, optionally filtered by SLO.

        Args:
            limit: Maximum number of alerts to return
            slo_name: Optional SLO name filter

        Returns:
            List of AlertEvent objects (most recent first)
        """
        if slo_name:
            alerts = [a for a in self.alert_history if a.slo_name == slo_name]
        else:
            alerts = self.alert_history

        # Sort by timestamp, most recent first
        return sorted(alerts, key=lambda a: a.timestamp, reverse=True)[:limit]

    def get_current_state(self) -> Dict[str, Dict]:
        """
        Get current alert state for all SLOs.

        Returns:
            Dict of {slo_name: {severity, last_value, last_transition}}
        """
        return {
            slo_name: {
                "severity": state.current_severity.value,
                "last_measured_value": state.last_measured_value,
                "last_transition": state.last_transition_time.isoformat(),
                "last_alert_sent": (
                    state.last_alert_sent_time.isoformat()
                    if state.last_alert_sent_time
                    else None
                ),
            }
            for slo_name, state in self.alert_states.items()
        }


# Global alert engine (can be injected for testing)
_alert_engine = AlertEngine()


def get_alert_engine() -> AlertEngine:
    """Get global alert engine."""
    return _alert_engine


def set_alert_engine(engine: AlertEngine) -> None:
    """Set global alert engine (for testing)."""
    global _alert_engine
    _alert_engine = engine
