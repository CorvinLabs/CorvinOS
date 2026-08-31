"""
Service Level Objectives (SLOs) for Phase 5 production hardening.

Three critical SLOs:
1. Plugin Availability: ≥99.5% (uptime/healthy status)
2. Latency (p95): ≤200ms for work delegation
3. Audit Chain Integrity: 100% (zero hash mismatches unresolved)

Each SLO has:
- Target value (the SLO itself)
- Error budget (acceptable failures per month)
- Alert threshold (when to page on-call)
- Measurement window (typically 30d rolling)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime, timedelta


class SLOStatus(Enum):
    """SLO status indicators."""
    HEALTHY = "healthy"  # Within budget
    WARNING = "warning"  # Approaching budget limit
    CRITICAL = "critical"  # Exceeded budget


@dataclass
class SLOTarget:
    """One SLO target."""
    name: str
    description: str
    target_value: float  # e.g., 0.995 for 99.5%
    unit: str  # e.g., "availability", "latency_ms", "errors_per_month"
    measurement_window_days: int = 30  # Typically 30-day rolling
    alert_threshold: float = None  # When to escalate (e.g., 0.99 for 99%)

    def __post_init__(self):
        if self.alert_threshold is None:
            # Default: alert at 95% of target
            self.alert_threshold = self.target_value * 0.95

    @property
    def error_budget_percent(self) -> float:
        """Allowable error as percentage (1 - target)."""
        return (1.0 - self.target_value) * 100

    @property
    def error_budget_monthly(self) -> float:
        """Allowable downtime in minutes per month."""
        # 30-day month = 43,200 minutes
        minutes_per_month = 30 * 24 * 60
        return minutes_per_month * (1.0 - self.target_value)


@dataclass
class SLOMeasurement:
    """Measurement against an SLO."""
    slo_name: str
    measured_value: float
    target_value: float
    unit: str
    window_start: datetime
    window_end: datetime
    status: SLOStatus
    good_count: int = 0  # e.g., successful requests
    bad_count: int = 0  # e.g., failed requests
    total_count: int = 0

    @property
    def compliance_percent(self) -> float:
        """How close to target (0–100%)."""
        if self.unit == "availability" or "percent" in self.unit.lower():
            return (self.measured_value / self.target_value) * 100
        else:
            # For latency: lower is better, so invert
            return (self.target_value / self.measured_value) * 100 if self.measured_value > 0 else 0.0

    def to_dict(self) -> Dict:
        """Serialize for dashboard."""
        return {
            "slo_name": self.slo_name,
            "measured_value": self.measured_value,
            "target_value": self.target_value,
            "unit": self.unit,
            "compliance_percent": self.compliance_percent,
            "status": self.status.value,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "good_count": self.good_count,
            "bad_count": self.bad_count,
            "total_count": self.total_count,
        }


class SLODefinitions:
    """Global SLO definitions for CorvinOS Phase 5."""

    @staticmethod
    def get_all_slos() -> Dict[str, SLOTarget]:
        """Get all SLO targets."""
        return {
            "plugin_availability": SLOTarget(
                name="Plugin Availability",
                description="Percentage of time plugins are healthy (not degraded/quarantined)",
                target_value=0.995,  # 99.5%
                unit="availability",
                measurement_window_days=30,
                alert_threshold=0.990,  # Alert at 99.0%
            ),
            "delegation_latency_p95": SLOTarget(
                name="Delegation Latency (p95)",
                description="95th percentile work delegation latency",
                target_value=200.0,  # milliseconds
                unit="latency_ms",
                measurement_window_days=30,
                alert_threshold=250.0,  # Alert if p95 > 250ms
            ),
            "audit_chain_integrity": SLOTarget(
                name="Audit Chain Integrity",
                description="Zero unresolved audit hash mismatches (100% detection + remediation)",
                target_value=1.0,  # 100%
                unit="integrity",
                measurement_window_days=30,
                alert_threshold=0.99,  # Alert at 99% (1+ unresolved per month)
            ),
        }

    @staticmethod
    def get_slo_by_name(name: str) -> Optional[SLOTarget]:
        """Get SLO by name."""
        slos = SLODefinitions.get_all_slos()
        return slos.get(name)


class SLOMonitor:
    """Monitor SLO compliance."""

    def __init__(self):
        """Initialize SLO monitor."""
        self.measurements: List[SLOMeasurement] = []
        self.slo_defs = SLODefinitions.get_all_slos()

    def add_measurement(self, measurement: SLOMeasurement) -> None:
        """Record a measurement."""
        self.measurements.append(measurement)

    def get_current_measurements(self) -> Dict[str, SLOMeasurement]:
        """Get latest measurement for each SLO."""
        latest = {}
        for slo_name in self.slo_defs.keys():
            slo_measurements = [
                m for m in self.measurements
                if m.slo_name == slo_name
            ]
            if slo_measurements:
                latest[slo_name] = sorted(
                    slo_measurements,
                    key=lambda m: m.window_end,
                    reverse=True
                )[0]
        return latest

    def get_overall_status(self) -> SLOStatus:
        """Get overall SLO health."""
        measurements = self.get_current_measurements()

        if not measurements:
            return SLOStatus.HEALTHY

        statuses = [m.status for m in measurements.values()]

        if SLOStatus.CRITICAL in statuses:
            return SLOStatus.CRITICAL
        elif SLOStatus.WARNING in statuses:
            return SLOStatus.WARNING
        else:
            return SLOStatus.HEALTHY

    def get_report(self) -> Dict:
        """Get full SLO report for dashboard."""
        measurements = self.get_current_measurements()
        overall = self.get_overall_status()

        return {
            "timestamp_utc": datetime.utcnow().isoformat(),
            "overall_status": overall.value,
            "slos": {
                slo_name: m.to_dict()
                for slo_name, m in measurements.items()
            },
            "summary": {
                "total_slos": len(self.slo_defs),
                "healthy_slos": sum(
                    1 for m in measurements.values()
                    if m.status == SLOStatus.HEALTHY
                ),
                "warning_slos": sum(
                    1 for m in measurements.values()
                    if m.status == SLOStatus.WARNING
                ),
                "critical_slos": sum(
                    1 for m in measurements.values()
                    if m.status == SLOStatus.CRITICAL
                ),
            },
        }


# Global SLO monitor (can be injected)
_slo_monitor = SLOMonitor()


def get_slo_monitor() -> SLOMonitor:
    """Get global SLO monitor."""
    return _slo_monitor


def set_slo_monitor(monitor: SLOMonitor) -> None:
    """Set global SLO monitor (for testing)."""
    global _slo_monitor
    _slo_monitor = monitor
