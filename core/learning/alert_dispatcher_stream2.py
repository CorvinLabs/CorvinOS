"""Stream 2: Alert Dispatcher (Story 17 — High error rate → Slack alert).

Monitors error rates across skills.
Triggers P0 alerts when error rate spike detected.
Sends to #incidents Slack channel.
"""

import logging
import json
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AlertDispatcher:
    """Monitor and dispatch alerts for high error rates."""

    # Alert threshold: error rate > 5%
    ERROR_RATE_THRESHOLD = 0.05
    # Alert if error rate increased by >20%
    ERROR_SPIKE_THRESHOLD = 0.20

    def __init__(self, alert_home: Path, tenant_id: str):
        """Initialize alert dispatcher.

        Args:
            alert_home: Root directory for alert storage
            tenant_id: Tenant scope
        """
        if not tenant_id:
            raise ValueError("tenant_id required")

        self.alert_home = Path(alert_home)
        self.tenant_id = tenant_id
        self.alerts_dir = self.alert_home / tenant_id / "alerts"

        # Create directories
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

    def check_error_rate(
        self,
        skill_id: str,
        current_error_rate: float,
        previous_error_rate: Optional[float] = None,
    ) -> Optional[Dict]:
        """Check if error rate should trigger an alert.

        Args:
            skill_id: Skill ID
            current_error_rate: Current error rate (0-1)
            previous_error_rate: Previous error rate for spike detection

        Returns:
            Alert dict if alert needed, else None
        """
        # Check if error rate is too high
        if current_error_rate > self.ERROR_RATE_THRESHOLD:
            alert = {
                "alert_id": skill_id + "_" + str(int(datetime.now(timezone.utc).timestamp())),
                "skill_id": skill_id,
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "alert_type": "high_error_rate",
                "severity": "P0" if current_error_rate > 0.10 else "P1",
                "error_rate": current_error_rate,
                "reason": f"Error rate {current_error_rate:.1%} exceeds threshold {self.ERROR_RATE_THRESHOLD:.1%}",
            }

            # Check for spike
            if previous_error_rate is not None:
                spike = (current_error_rate - previous_error_rate) / (previous_error_rate + 0.001)
                if spike > self.ERROR_SPIKE_THRESHOLD:
                    alert["reason"] += f" (spike: {previous_error_rate:.1%} → {current_error_rate:.1%})"
                    alert["is_spike"] = True

            return alert

        return None

    def dispatch_alert(self, alert: Dict) -> bool:
        """Dispatch alert to operators (record + Slack).

        Args:
            alert: Alert dict

        Returns:
            True if dispatch successful
        """
        try:
            # Record alert
            alert_file = self.alerts_dir / f"{alert['alert_id']}.json"
            with open(alert_file, "w") as f:
                json.dump(alert, f, indent=2)

            # Format Slack message
            slack_msg = self._format_slack_message(alert)

            # TODO: Send to Slack #incidents channel
            # For now, just log
            logger.warning(f"ALERT: {alert['alert_id']}: {alert['reason']}")
            logger.warning(f"slack_message: {slack_msg}")

            return True

        except Exception as e:
            logger.error(f"dispatch_alert_error: {e}")
            return False

    def _format_slack_message(self, alert: Dict) -> str:
        """Format alert as Slack message."""
        msg = (
            f":warning: *{alert['severity']} Alert: {alert['alert_type']}*\n"
            f"*Skill:* {alert['skill_id']}\n"
            f"*Error Rate:* {alert['error_rate']:.1%}\n"
            f"*Reason:* {alert['reason']}\n"
            f"*Time:* {alert['timestamp']}"
        )
        return msg

    def get_recent_alerts(self, hours: int = 24) -> list:
        """Get recent alerts (for dashboard)."""
        try:
            from datetime import timedelta
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            alerts = []

            for alert_file in sorted(self.alerts_dir.glob("*.json")):
                try:
                    with open(alert_file, "r") as f:
                        alert_data = json.load(f)
                        alert_time = datetime.fromisoformat(alert_data["timestamp"].replace('Z', '+00:00'))
                        if alert_time > cutoff_time:
                            alerts.append(alert_data)
                except Exception as e:
                    logger.error(f"read_alert_error: {alert_file.name}: {e}")

            return sorted(alerts, key=lambda a: a["timestamp"], reverse=True)

        except Exception as e:
            logger.error(f"get_alerts_error: {e}")
            return []


__all__ = ["AlertDispatcher"]
