"""
Phase 4: Real-Time Drift Detection & Alerting

Background monitoring service that polls instances every 30s.
Detects drifts from Phase 1 (deployment state), Phase 2 (config), Phase 3 (plugins).
Routes alerts via Slack (all severities) and PagerDuty (CRITICAL only).
"""

import os
import json
import logging
import threading
import time
from dataclasses import dataclass, asdict
from typing import List, Callable, Optional, Dict
from enum import Enum
from datetime import datetime
import requests

from core.deployment.state_sync import DeploymentStateManager, DriftAlert, DriftSeverity
from core.plugins.registry_sync import PluginRegistrySynchronizer


logger = logging.getLogger(__name__)


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


class SlackAlerter:
    """Slack integration for drift alerts"""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
        if not self.webhook_url:
            logger.warning("SLACK_WEBHOOK_URL not set; Slack alerts disabled")

    def send_alert(self, severity: AlertSeverity, message: str, instance_id: str, drift_type: str):
        """Send Slack alert with color coding"""
        if not self.webhook_url:
            return

        color_map = {
            AlertSeverity.CRITICAL: "#FF0000",  # Red
            AlertSeverity.HIGH: "#FFA500",       # Orange
            AlertSeverity.MEDIUM: "#FFFF00",     # Yellow
            AlertSeverity.LOW: "#0000FF",        # Blue
        }

        payload = {
            "attachments": [
                {
                    "color": color_map.get(severity, "#999999"),
                    "title": f"{severity.value} — Drift Detected",
                    "text": message,
                    "fields": [
                        {"title": "Instance", "value": instance_id, "short": True},
                        {"title": "Drift Type", "value": drift_type, "short": True},
                        {"title": "Timestamp", "value": datetime.utcnow().isoformat(), "short": True},
                        {"title": "Severity", "value": severity.value, "short": True},
                    ],
                    "footer": "CorvinOS Drift Detection",
                }
            ]
        }

        try:
            response = requests.post(self.webhook_url, json=payload, timeout=5)
            response.raise_for_status()
            logger.info(f"✅ Slack alert sent: {severity.value} — {drift_type}")
        except Exception as e:
            logger.error(f"❌ Slack alert failed: {e}")


class PagerDutyAlerter:
    """PagerDuty integration for critical drift alerts"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("PAGERDUTY_API_KEY")
        if not self.api_key:
            logger.warning("PAGERDUTY_API_KEY not set; PagerDuty alerts disabled")

    def trigger_incident(self, message: str, instance_id: str, drift_type: str):
        """Trigger a PagerDuty incident for critical drift"""
        if not self.api_key:
            return

        payload = {
            "routing_key": self.api_key,
            "event_action": "trigger",
            "dedup_key": f"drift_{instance_id}_{drift_type}",
            "payload": {
                "summary": f"Critical Drift: {drift_type} on {instance_id}",
                "timestamp": datetime.utcnow().isoformat(),
                "severity": "critical",
                "source": "CorvinOS Drift Detection",
                "custom_details": {
                    "instance_id": instance_id,
                    "drift_type": drift_type,
                    "message": message,
                    "remediation_action": self._get_remediation_action(drift_type),
                    "runbook_url": f"https://docs.corvin.internal/runbooks/drift-remediation-{drift_type.lower()}",
                },
            },
        }

        try:
            response = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
                timeout=5,
            )
            response.raise_for_status()
            logger.info(f"✅ PagerDuty incident triggered: {drift_type} on {instance_id}")
        except Exception as e:
            logger.error(f"❌ PagerDuty incident failed: {e}")

    def resolve_incident(self, instance_id: str, drift_type: str):
        """Resolve a PagerDuty incident when drift is fixed"""
        if not self.api_key:
            return

        payload = {
            "routing_key": self.api_key,
            "event_action": "resolve",
            "dedup_key": f"drift_{instance_id}_{drift_type}",
        }

        try:
            response = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
                timeout=5,
            )
            response.raise_for_status()
            logger.info(f"✅ PagerDuty incident resolved: {drift_type} on {instance_id}")
        except Exception as e:
            logger.error(f"❌ PagerDuty resolve failed: {e}")

    def _get_remediation_action(self, drift_type: str) -> str:
        """Get remediation action for drift type"""
        return {
            "CODE_VERSION_DRIFT": "REDEPLOY_INSTANCE",
            "MANIFEST_HASH_MISMATCH": "REDEPLOY_INSTANCE",
            "CONFIG_DRIFT": "SYNC_CONFIG",
            "PLUGIN_MISSING": "INSTALL_PLUGIN",
            "PLUGIN_VERSION_MISMATCH": "UPDATE_PLUGIN",
        }.get(drift_type, "MANUAL_REVIEW")


class DriftDetectionService:
    """
    Phase 4: Real-time drift monitoring with alert routing.

    Polls deployment state every 30 seconds, detects drifts, and routes alerts
    via Slack (all severities) and PagerDuty (CRITICAL only).

    Integration with Phase 1 (DeploymentStateManager) for drift detection.
    Audit trail: all alerts logged to security event log.
    """

    POLLING_INTERVAL_SECONDS = 30

    def __init__(
        self,
        deployment_manager: Optional[DeploymentStateManager] = None,
        slack_alerter: Optional[SlackAlerter] = None,
        pagerduty_alerter: Optional[PagerDutyAlerter] = None,
        plugin_sync_enabled: bool = True,
    ):
        self.deployment_manager = deployment_manager or DeploymentStateManager()
        self.slack_alerter = slack_alerter or SlackAlerter()
        self.pagerduty_alerter = pagerduty_alerter or PagerDutyAlerter()
        self.monitoring_events: List[MonitoringEvent] = []
        self.monitoring_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.alert_handlers: List[Callable[[AlertSeverity, str], None]] = []
        self.plugin_sync_enabled = plugin_sync_enabled

    def start_monitoring(self):
        """Start background monitoring loop (non-blocking)"""
        if self.is_running:
            logger.warning("Monitoring already running")
            return

        self.is_running = True
        self.monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True,
            name="DriftDetectionThread",
        )
        self.monitoring_thread.start()
        logger.info("✅ Drift detection service started (30s polling)")

    def stop_monitoring(self):
        """Stop background monitoring loop"""
        self.is_running = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=5)
        logger.info("❌ Drift detection service stopped")

    def _monitoring_loop(self):
        """Internal: Run 30-second polling loop"""
        while self.is_running:
            try:
                self.monitor_all_instances()
            except Exception as e:
                logger.error(f"❌ Monitoring loop error: {e}", exc_info=True)
            finally:
                time.sleep(self.POLLING_INTERVAL_SECONDS)

    def monitor_all_instances(self) -> List[MonitoringEvent]:
        """
        Poll all instances for drift.

        Returns list of detected drift events.
        Integration:
        - Phase 1 (DeploymentStateManager) for code version drift detection
        - Phase 3 (PluginRegistrySynchronizer) for plugin registry drift detection
        """
        events = []

        if not self.deployment_manager.instance_states:
            logger.warning("No instances registered; skipping monitoring")
            return events

        for instance_id in self.deployment_manager.instance_states:
            try:
                # Phase 1: Get deployment state drifts
                drifts = self.deployment_manager.detect_drift(instance_id)

                for drift in drifts:
                    event = self._create_event_from_drift(drift)
                    events.append(event)
                    self.monitoring_events.append(event)

                    # Route alert based on severity
                    self.alert_with_severity(drift.severity, drift.message, instance_id, drift.alert_type)

                # Phase 3: Get plugin registry drifts
                if self.plugin_sync_enabled:
                    plugin_drifts = self._detect_plugin_drifts(instance_id)
                    for plugin_drift in plugin_drifts:
                        # Convert plugin drift to monitoring event
                        event = MonitoringEvent(
                            timestamp=datetime.utcnow().isoformat(),
                            instance_id=instance_id,
                            drift_type=plugin_drift.drift_type,
                            severity=self._map_plugin_drift_severity(plugin_drift.severity),
                            message=f"Plugin drift: {plugin_drift.plugin_id} — {plugin_drift.drift_type}",
                        )
                        events.append(event)
                        self.monitoring_events.append(event)

                        # Route alert based on severity
                        self.alert_with_severity(
                            DriftSeverity[plugin_drift.severity],
                            f"Plugin {plugin_drift.plugin_id}: {plugin_drift.drift_type}",
                            instance_id,
                            f"PLUGIN_{plugin_drift.drift_type}",
                        )

            except Exception as e:
                logger.error(f"❌ Error monitoring {instance_id}: {e}", exc_info=True)

        return events

    def _detect_plugin_drifts(self, instance_id: str) -> List:
        """
        Detect plugin registry drifts using Phase 3 PluginRegistrySynchronizer.

        Returns: List of PluginDrift objects
        """
        try:
            sync = PluginRegistrySynchronizer(instance_id=instance_id)
            return sync.detect_plugin_drift()
        except Exception as e:
            logger.warning(f"⚠️  Plugin drift detection failed for {instance_id}: {e}")
            return []

    def _map_plugin_drift_severity(self, severity_str: str) -> AlertSeverity:
        """Map plugin drift severity string to AlertSeverity"""
        severity_map = {
            "CRITICAL": AlertSeverity.CRITICAL,
            "HIGH": AlertSeverity.HIGH,
            "MEDIUM": AlertSeverity.MEDIUM,
            "LOW": AlertSeverity.LOW,
        }
        return severity_map.get(severity_str, AlertSeverity.MEDIUM)

    def _create_event_from_drift(self, drift: DriftAlert) -> MonitoringEvent:
        """Convert Phase 1 DriftAlert to MonitoringEvent"""
        return MonitoringEvent(
            timestamp=drift.timestamp,
            instance_id=drift.instance_id,
            drift_type=drift.alert_type,
            severity=self._map_severity(drift.severity),
            message=drift.message,
        )

    def _map_severity(self, phase1_severity: DriftSeverity) -> AlertSeverity:
        """Map Phase 1 DriftSeverity to Phase 4 AlertSeverity"""
        severity_map = {
            DriftSeverity.CRITICAL: AlertSeverity.CRITICAL,
            DriftSeverity.HIGH: AlertSeverity.HIGH,
            DriftSeverity.MEDIUM: AlertSeverity.MEDIUM,
            DriftSeverity.LOW: AlertSeverity.LOW,
        }
        return severity_map.get(phase1_severity, AlertSeverity.MEDIUM)

    def alert_with_severity(self, severity, message: str, instance_id: str, drift_type: str):
        """Route alert based on severity (Slack + PagerDuty)"""
        # Audit trail: log all alerts
        self._audit_alert(severity, message, instance_id, drift_type)

        # Slack: All severities
        self.slack_alerter.send_alert(
            AlertSeverity[severity.name],
            message,
            instance_id,
            drift_type,
        )

        # PagerDuty: CRITICAL only
        if severity == DriftSeverity.CRITICAL:
            self.pagerduty_alerter.trigger_incident(message, instance_id, drift_type)

        # Custom handlers
        for handler in self.alert_handlers:
            try:
                handler(AlertSeverity[severity.name], message)
            except Exception as e:
                logger.error(f"❌ Custom alert handler failed: {e}")

    def _audit_alert(self, severity, message: str, instance_id: str, drift_type: str):
        """Log alert to audit trail"""
        try:
            from core.compliance.security_events import write_event

            write_event(
                "drift_alert_sent",
                {
                    "severity": severity.value,
                    "instance_id": instance_id,
                    "drift_type": drift_type,
                    "message": message,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
        except Exception as e:
            logger.error(f"❌ Audit logging failed: {e}")

    def register_alert_handler(self, handler: Callable[[AlertSeverity, str], None]):
        """Register custom alert handler (for testing)"""
        self.alert_handlers.append(handler)

    def get_recent_events(self, limit: int = 100) -> List[MonitoringEvent]:
        """Get recent monitoring events (for dashboard)"""
        return self.monitoring_events[-limit:]


# Alert routing rules (severity → action mapping)
ALERT_RULES = {
    "CODE_VERSION_DRIFT": {"severity": "CRITICAL", "action": "PAGE_ONCALL"},
    "MANIFEST_HASH_MISMATCH": {"severity": "CRITICAL", "action": "PAGE_ONCALL"},
    "PLUGIN_MISSING": {"severity": "CRITICAL", "action": "PAGE_ONCALL"},
    "PLUGIN_VERSION_MISMATCH": {"severity": "HIGH", "action": "SLACK_ALERT"},
    "CONFIG_DRIFT": {"severity": "HIGH", "action": "SLACK_ALERT"},
    "CONFIG_VALIDATION_FAILED": {"severity": "MEDIUM", "action": "LOG"},
}


# Singleton instance for easy access
_drift_service: Optional[DriftDetectionService] = None


def get_drift_service() -> DriftDetectionService:
    """Get or create singleton drift detection service"""
    global _drift_service
    if _drift_service is None:
        _drift_service = DriftDetectionService()
    return _drift_service
