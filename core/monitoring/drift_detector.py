"""
Phase 4 (TEMPLATE): Real-Time Drift Detection & Alerting

Background monitoring service that polls instances every 30s.
See PHASE4_DETECTION_ALERTING_SPEC.md for full design.
"""

from dataclasses import dataclass
from typing import List, Callable
from enum import Enum


class AlertSeverity(Enum):
    """Alert severity levels for routing"""
    CRITICAL = "CRITICAL"  # Page oncall immediately
    HIGH = "HIGH"          # Slack alert + manual review
    MEDIUM = "MEDIUM"      # Log + metrics
    LOW = "LOW"            # Metrics only


@dataclass
class MonitoringEvent:
    """Drift detection event for audit trail"""
    timestamp: str
    instance_id: str
    drift_type: str
    severity: AlertSeverity
    message: str


class DriftDetectionService:
    """
    Phase 4: Real-time drift monitoring

    TODO: Implement 30-second polling loop
    TODO: Add Slack/PagerDuty integration
    TODO: Add metrics collection (Prometheus)
    TODO: Add audit logging
    """

    POLLING_INTERVAL_SECONDS = 30

    def __init__(self):
        self.alert_handlers: List[Callable[[AlertSeverity, str], None]] = []
        self.monitoring_events: List[MonitoringEvent] = []

    def start_monitoring(self):
        """Start background monitoring loop"""
        raise NotImplementedError("Phase 4 implementation pending")

    def monitor_all_instances(self) -> List[MonitoringEvent]:
        """
        Poll all instances for drift

        Returns list of detected drifts

        Implementation:
        1. Get deployment state drifts
        2. Get config drifts
        3. Get plugin drifts
        4. Aggregate + emit alerts
        """
        raise NotImplementedError("Phase 4 implementation pending")

    def alert_with_severity(self, severity: AlertSeverity, message: str):
        """Route alert based on severity"""
        if severity == AlertSeverity.CRITICAL:
            # Page oncall
            self._page_oncall(message)
            # Slack
            self._slack_critical(message)
        elif severity == AlertSeverity.HIGH:
            # Slack alert
            self._slack_alert(message)
        else:
            # Log + metrics
            self._log_event(message)

    def _page_oncall(self, message: str):
        """Page on-call engineer"""
        raise NotImplementedError("Phase 4: PagerDuty integration")

    def _slack_critical(self, message: str):
        """Send Slack critical alert"""
        raise NotImplementedError("Phase 4: Slack integration")

    def _slack_alert(self, message: str):
        """Send Slack alert"""
        raise NotImplementedError("Phase 4: Slack integration")

    def _log_event(self, message: str):
        """Log monitoring event"""
        pass  # Implemented in logger


# Alert routing rules (will be in YAML config)
ALERT_RULES = {
    "CODE_VERSION_DRIFT": {"severity": "CRITICAL", "action": "PAGE_ONCALL"},
    "PLUGIN_MISSING": {"severity": "CRITICAL", "action": "AUTO_REMEDIATE"},
    "CONFIG_DRIFT": {"severity": "HIGH", "action": "ALERT"},
}
