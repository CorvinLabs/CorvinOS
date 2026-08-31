"""k=5: Live Monitoring + Production Deployment — Monitor context drift incidents."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ContextDriftIncident:
    """A context-drift incident in production."""
    timestamp: datetime
    session_id: str
    original_goal: str
    pipeline_addition: str
    incident_type: str  # "silent_drift", "unexpected_shift", "lost_context"
    severity: str  # "low", "medium", "high"
    user_reported: bool = False
    resolution: str = ""  # "user_correction", "agent_caught", "no_correction"


@dataclass
class LiveMonitoringMetrics:
    """Metrics from live monitoring."""
    total_incidents: int = 0
    context_drift_rate: float = 0.0  # incidents per 100 sessions
    user_satisfaction: float = 0.0  # 1-5 scale
    false_positive_rate: float = 0.0  # % of false alarms
    drift_prevention_rate: float = 0.0  # % caught proactively
    incidents: list[ContextDriftIncident] = field(default_factory=list)

    def record_incident(self, incident: ContextDriftIncident) -> None:
        """Record a context drift incident."""
        self.incidents.append(incident)
        self.total_incidents += 1
        logger.warning(f"Context drift incident: {incident.incident_type} - {incident.original_goal}")


class LiveMonitor:
    """Monitor context-drift incidents in production."""

    def __init__(self, baseline_rate: float = 0.05):
        """Initialize monitor with baseline drift rate.

        Args:
            baseline_rate: Expected baseline context-drift rate (5% = default)
        """
        self.baseline_rate = baseline_rate
        self.metrics = LiveMonitoringMetrics()

    def report_incident(self, incident: ContextDriftIncident) -> None:
        """Report a detected context-drift incident."""
        self.metrics.record_incident(incident)

    def get_summary(self) -> dict:
        """Get monitoring summary."""
        return {
            "total_sessions_monitored": 100,  # Simulated
            "drift_incidents": self.metrics.total_incidents,
            "drift_rate_pct": (self.metrics.total_incidents / 100) * 100,
            "baseline_rate_pct": self.baseline_rate * 100,
            "improvement_pct": max(0, (self.baseline_rate * 100) - ((self.metrics.total_incidents / 100) * 100)),
            "metrics": self.metrics,
        }

    def should_escalate(self) -> bool:
        """Whether metrics indicate need to escalate/rollback."""
        # Escalate if drift rate >15% (3x baseline)
        drift_pct = (self.metrics.total_incidents / 100) * 100
        return drift_pct > (self.baseline_rate * 100 * 3)
