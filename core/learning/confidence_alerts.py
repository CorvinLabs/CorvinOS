"""Confidence Alerting System (Phase 1, Week 3).

Monitors decision confidence and alerts operator when uncertainty is high.
Rate-limits alerts to prevent alert fatigue (max 2/day).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from collections import defaultdict


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ConfidenceAlert:
    """Alert for low-confidence decisions."""

    alert_id: str
    decision_id: str
    task_id: str
    operator_id: str
    confidence: float
    severity: AlertSeverity
    recommendation: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    message: Optional[str] = None


class AlertThresholdManager:
    """Manages confidence thresholds for alerting."""

    def __init__(self, default_threshold: float = 0.7):
        """Initialize threshold manager.

        Args:
            default_threshold: Default confidence threshold (0.0-1.0)
        """
        self.default_threshold = default_threshold
        self.operator_thresholds: dict[str, float] = {}  # Per-operator overrides
        self.task_type_thresholds: dict[str, float] = {}  # Per-task-type overrides

    def get_threshold(self, operator_id: str, task_type: Optional[str] = None) -> float:
        """Get effective threshold for operator/task combination."""
        # Priority: task-type override > operator override > default
        if task_type and task_type in self.task_type_thresholds:
            return self.task_type_thresholds[task_type]

        if operator_id in self.operator_thresholds:
            return self.operator_thresholds[operator_id]

        return self.default_threshold

    def set_operator_threshold(self, operator_id: str, threshold: float) -> None:
        """Set operator-specific threshold."""
        if not (0.0 <= threshold <= 1.0):
            raise ValueError("Threshold must be between 0.0 and 1.0")
        self.operator_thresholds[operator_id] = threshold

    def set_task_type_threshold(self, task_type: str, threshold: float) -> None:
        """Set task-type-specific threshold."""
        if not (0.0 <= threshold <= 1.0):
            raise ValueError("Threshold must be between 0.0 and 1.0")
        self.task_type_thresholds[task_type] = threshold

    def reset_operator_threshold(self, operator_id: str) -> None:
        """Reset operator to default threshold."""
        self.operator_thresholds.pop(operator_id, None)


class AlertRateLimiter:
    """Rate-limits alerts to prevent alert fatigue."""

    def __init__(self, max_alerts_per_day: int = 2):
        """Initialize rate limiter.

        Args:
            max_alerts_per_day: Maximum alerts per operator per day
        """
        self.max_alerts_per_day = max_alerts_per_day
        self.alert_times: dict[str, list[datetime]] = defaultdict(list)

    def can_alert(self, operator_id: str) -> bool:
        """Check if alert is allowed for operator.

        Returns True if under limit, False if rate-limited.
        """
        now = datetime.utcnow()
        day_ago = now - timedelta(days=1)

        # Clean old alerts
        if operator_id in self.alert_times:
            self.alert_times[operator_id] = [
                t for t in self.alert_times[operator_id] if t > day_ago
            ]

        # Check limit
        alert_count = len(self.alert_times[operator_id])
        return alert_count < self.max_alerts_per_day

    def record_alert(self, operator_id: str) -> None:
        """Record that an alert was sent."""
        self.alert_times[operator_id].append(datetime.utcnow())

    def get_alert_count(self, operator_id: str) -> int:
        """Get number of alerts in last 24 hours."""
        now = datetime.utcnow()
        day_ago = now - timedelta(days=1)

        if operator_id not in self.alert_times:
            return 0

        return len([t for t in self.alert_times[operator_id] if t > day_ago])


class AlertHistory:
    """Tracks alert history for analysis."""

    def __init__(self):
        self.alerts: list[ConfidenceAlert] = []

    def add_alert(self, alert: ConfidenceAlert) -> None:
        """Record an alert."""
        self.alerts.append(alert)

    def get_alerts_for_operator(self, operator_id: str, days: int = 7) -> list[ConfidenceAlert]:
        """Get recent alerts for operator."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        return [
            a for a in self.alerts
            if a.operator_id == operator_id and datetime.fromisoformat(a.timestamp) > cutoff
        ]

    def get_alert_statistics(self, operator_id: str) -> dict:
        """Get alert statistics for operator."""
        alerts = self.get_alerts_for_operator(operator_id)

        if not alerts:
            return {
                "total_alerts": 0,
                "info_count": 0,
                "warning_count": 0,
                "critical_count": 0,
                "avg_confidence": 0.0,
            }

        severity_counts = {
            AlertSeverity.INFO: sum(1 for a in alerts if a.severity == AlertSeverity.INFO),
            AlertSeverity.WARNING: sum(1 for a in alerts if a.severity == AlertSeverity.WARNING),
            AlertSeverity.CRITICAL: sum(1 for a in alerts if a.severity == AlertSeverity.CRITICAL),
        }

        avg_confidence = sum(a.confidence for a in alerts) / len(alerts)

        return {
            "total_alerts": len(alerts),
            "info_count": severity_counts[AlertSeverity.INFO],
            "warning_count": severity_counts[AlertSeverity.WARNING],
            "critical_count": severity_counts[AlertSeverity.CRITICAL],
            "avg_confidence": avg_confidence,
        }


class ConfidenceAlertingSystem:
    """Main alerting system combining threshold, rate limiting, and history."""

    def __init__(
        self,
        default_threshold: float = 0.7,
        max_alerts_per_day: int = 2,
    ):
        self.threshold_manager = AlertThresholdManager(default_threshold)
        self.rate_limiter = AlertRateLimiter(max_alerts_per_day)
        self.history = AlertHistory()

    def should_alert(
        self,
        operator_id: str,
        confidence: float,
        task_type: Optional[str] = None,
    ) -> bool:
        """Determine if alert should be sent.

        Returns True if:
        1. Confidence is below operator's threshold
        2. Operator is under rate limit
        """
        threshold = self.threshold_manager.get_threshold(operator_id, task_type)

        if confidence >= threshold:
            return False  # Confidence is high enough

        if not self.rate_limiter.can_alert(operator_id):
            return False  # Rate limited

        return True

    def generate_alert(
        self,
        alert_id: str,
        decision_id: str,
        task_id: str,
        operator_id: str,
        confidence: float,
        task_type: Optional[str] = None,
    ) -> Optional[ConfidenceAlert]:
        """Generate alert if conditions warrant it.

        Returns ConfidenceAlert if sent, None if suppressed.
        """
        if not self.should_alert(operator_id, confidence, task_type):
            return None

        # Determine severity based on how far below threshold
        threshold = self.threshold_manager.get_threshold(operator_id, task_type)
        gap = threshold - confidence

        if gap > 0.3:
            severity = AlertSeverity.CRITICAL
            recommendation = "Consider using a more capable engine (e.g., Claude Opus)"
        elif gap > 0.15:
            severity = AlertSeverity.WARNING
            recommendation = "Consider reviewing the task or using a higher-capability engine"
        else:
            severity = AlertSeverity.INFO
            recommendation = "Monitor this task's outcome for learning"

        alert = ConfidenceAlert(
            alert_id=alert_id,
            decision_id=decision_id,
            task_id=task_id,
            operator_id=operator_id,
            confidence=confidence,
            severity=severity,
            recommendation=recommendation,
            message=f"Low confidence decision: {confidence:.2%} (threshold: {threshold:.2%})",
        )

        # Record in history
        self.history.add_alert(alert)

        # Update rate limiter
        self.rate_limiter.record_alert(operator_id)

        return alert

    def get_statistics(self, operator_id: str) -> dict:
        """Get alerting statistics for operator."""
        return {
            **self.history.get_alert_statistics(operator_id),
            "alerts_remaining_today": max(
                0,
                self.rate_limiter.max_alerts_per_day - self.rate_limiter.get_alert_count(operator_id),
            ),
        }
