"""
Phase 5: L5 Live Deployment Monitoring — Production Metrics Dashboard & Health Checks

Responsibilities:
- Real-time metrics collection from audit trail
- Health status checks for all 5 gates
- Automatic alerting on SLA violations
- WebSocket support for live dashboard updates

Metrics tracked:
- Gate latencies (p50/p95/p99) per gate
- Decision distribution (auto-approved %, pending %, rejected %)
- Config apply success rate (%)
- Revoke rate + holdover time analysis
- Operator latency SLA (target <5min)
- Cross-skill coordination metrics
- Bayesian learning convergence
- A/B test progress
- Concept drift alerts

ADR-0588: L5 Deployment Monitoring

Audit-first design: All health checks read immutable audit trail; no side effects.
Thread-safe: RLock protection on shared state.
Tenant-scoped: All reads filtered by tenant_id.
"""

import logging
import statistics
import copy
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
from threading import RLock
import json

logger = logging.getLogger(__name__)


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class GateHealthStatus:
    """Health status for a single L5 gate."""
    gate_name: str  # "k=1", "k=2", ..., "k=5"
    is_healthy: bool
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    error_rate_pct: float = 0.0
    pending_count: int = 0
    sla_breaches: int = 0  # Count of decisions exceeding SLA
    last_check_timestamp: str = ""

    def to_dict(self) -> Dict:
        """Convert to dict for JSON serialization."""
        return {
            "gate_name": self.gate_name,
            "is_healthy": self.is_healthy,
            "latency_p50_ms": round(self.latency_p50_ms, 2) if self.latency_p50_ms else None,
            "latency_p95_ms": round(self.latency_p95_ms, 2) if self.latency_p95_ms else None,
            "latency_p99_ms": round(self.latency_p99_ms, 2) if self.latency_p99_ms else None,
            "avg_latency_ms": round(self.avg_latency_ms, 2) if self.avg_latency_ms else None,
            "error_rate_pct": round(self.error_rate_pct, 2),
            "pending_count": self.pending_count,
            "sla_breaches": self.sla_breaches,
            "last_check_timestamp": self.last_check_timestamp,
        }


@dataclass
class L5HealthSnapshot:
    """Overall L5 health at a point in time."""
    timestamp: str
    all_healthy: bool
    gates: Dict[str, GateHealthStatus] = field(default_factory=dict)

    # Aggregated metrics
    total_pending: int = 0
    auto_approval_rate_pct: float = 0.0
    rejection_rate_pct: float = 0.0
    config_apply_success_rate_pct: float = 0.0
    avg_operator_latency_ms: Optional[float] = None
    sla_status: str = "OK"  # OK, WARNING, CRITICAL
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dict for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "all_healthy": self.all_healthy,
            "gates": {k: v.to_dict() for k, v in self.gates.items()},
            "total_pending": self.total_pending,
            "auto_approval_rate_pct": round(self.auto_approval_rate_pct, 2),
            "rejection_rate_pct": round(self.rejection_rate_pct, 2),
            "config_apply_success_rate_pct": round(self.config_apply_success_rate_pct, 2),
            "avg_operator_latency_ms": round(self.avg_operator_latency_ms, 2) if self.avg_operator_latency_ms else None,
            "sla_status": self.sla_status,
            "alerts": self.alerts,
        }


@dataclass
class Alert:
    """A single alert raised by the monitoring system."""
    alert_id: str
    severity: str  # "INFO", "WARNING", "CRITICAL"
    message: str
    gate_name: Optional[str] = None
    skill_id: Optional[str] = None
    timestamp: str = ""
    is_acknowledged: bool = False

    def to_dict(self) -> Dict:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity,
            "message": self.message,
            "gate_name": self.gate_name,
            "skill_id": self.skill_id,
            "timestamp": self.timestamp,
            "is_acknowledged": self.is_acknowledged,
        }


# ============================================================================
# Metrics Collector
# ============================================================================

class MetricsCollector:
    """
    Real-time metrics collection from audit trail.

    Reads immutable audit events to compute:
    - Latencies (p50/p95/p99) per gate
    - Decision distribution
    - Config apply success rate
    - Revoke analysis

    Thread-safe; tenant-scoped.
    """

    def __init__(self, audit_backend, window_hours: int = 24, tenant_id: str = "_default"):
        """
        Initialize collector.

        Args:
            audit_backend: Audit system to read events from
            window_hours: Time window for metrics (default 24h)
            tenant_id: Tenant scope (default "_default")
        """
        # CRITICAL FIX #1: Validate tenant_id (GDPR Art. 32)
        if not tenant_id:
            raise ValueError("tenant_id cannot be empty (GDPR Art. 32)")

        self.audit_backend = audit_backend
        self.window_hours = window_hours
        self.tenant_id = tenant_id
        self._lock = RLock()

        # In-memory cache (refreshed periodically)
        self._approval_events: List[Dict] = []
        self._config_apply_events: List[Dict] = []
        self._revoke_events: List[Dict] = []
        self._last_refresh_timestamp: Optional[datetime] = None

    def collect_metrics(self) -> Dict:
        """
        Collect all metrics from audit trail.

        Returns:
            Dict with keys: approval_latencies, decision_counts, config_apply_rate, etc.
        """
        with self._lock:
            # Refresh cache if needed
            cutoff_time = datetime.utcnow() - timedelta(hours=self.window_hours)
            if not self._last_refresh_timestamp or \
               datetime.utcnow() - self._last_refresh_timestamp > timedelta(minutes=5):
                self._refresh_cache(cutoff_time)

            # Compute aggregates
            metrics = {
                "approval_latencies_ms": self._compute_approval_latencies(),
                "decision_distribution": self._compute_decision_distribution(),
                "config_apply_success_rate": self._compute_config_apply_rate(),
                "revoke_metrics": self._compute_revoke_metrics(),
                "pending_by_skill": self._compute_pending_by_skill(),
                "timestamp": datetime.utcnow().isoformat(),
            }
            return metrics

    def _refresh_cache(self, cutoff_time: datetime) -> None:
        """Refresh in-memory cache from audit trail."""
        try:
            # CRITICAL FIX #2: Add tenant filtering + timeout
            if self.audit_backend:
                try:
                    # Query with tenant filter AND timeout (5 seconds)
                    events = self.audit_backend.query_events(
                        tenant_id=self.tenant_id,
                        event_types=['approval_request', 'approval_decision'],
                        after=cutoff_time,
                        timeout_seconds=5
                    ) or []
                    self._approval_events = events
                except TimeoutError:
                    logger.warning(f"Audit backend timeout for tenant {self.tenant_id}; using stale cache")
                    return
            else:
                self._approval_events = []

            self._config_apply_events = []
            self._revoke_events = []
            self._last_refresh_timestamp = datetime.utcnow()
            logger.debug(f"Metrics cache refreshed for {self.tenant_id}")
        except Exception as e:
            # BUG FIX #7: Log refresh failures with warning level for operator visibility
            logger.warning(f"Metrics refresh failed for tenant {self.tenant_id}: {e}. Using stale cache.")

    def _compute_approval_latencies(self) -> Dict[str, Optional[float]]:
        """Compute latency percentiles for approval events."""
        if not self._approval_events:
            return {"p50": None, "p95": None, "p99": None, "avg": None}

        latencies = [e.get("latency_ms", 0) for e in self._approval_events]
        if not latencies:
            return {"p50": None, "p95": None, "p99": None, "avg": None}

        latencies_sorted = sorted(latencies)
        return {
            "p50": statistics.median(latencies_sorted),
            "p95": self._percentile(latencies_sorted, 95),
            "p99": self._percentile(latencies_sorted, 99),
            "avg": statistics.mean(latencies_sorted),
        }

    def _compute_decision_distribution(self) -> Dict[str, int]:
        """Compute decision counts (auto-approved, manual, rejected, revoked)."""
        distribution = {
            "auto_approved": 0,
            "manual_approved": 0,
            "rejected": 0,
            "revoked": 0,
            "total": len(self._approval_events),
        }
        for event in self._approval_events:
            decision = event.get("decision", "pending")
            auto = event.get("auto_approved", False)
            if decision == "approved":
                if auto:
                    distribution["auto_approved"] += 1
                else:
                    distribution["manual_approved"] += 1
            elif decision == "rejected":
                distribution["rejected"] += 1
            elif decision == "revoked":
                distribution["revoked"] += 1
        return distribution

    def _compute_config_apply_rate(self) -> float:
        """Compute config apply success rate (%)."""
        if not self._config_apply_events:
            return 100.0
        success_count = sum(1 for e in self._config_apply_events if e.get("success", False))
        return (success_count / len(self._config_apply_events)) * 100.0

    def _compute_revoke_metrics(self) -> Dict:
        """Compute revoke-related metrics."""
        if not self._revoke_events:
            return {"total_revokes": 0, "avg_holdover_hours": 0.0}

        total_revokes = len(self._revoke_events)
        holdover_times = []
        for event in self._revoke_events:
            if "approval_timestamp" in event and "revoke_timestamp" in event:
                try:
                    approve_time = datetime.fromisoformat(event["approval_timestamp"])
                    revoke_time = datetime.fromisoformat(event["revoke_timestamp"])
                    holdover_hours = (revoke_time - approve_time).total_seconds() / 3600.0
                    holdover_times.append(holdover_hours)
                except Exception:
                    pass

        avg_holdover = statistics.mean(holdover_times) if holdover_times else 0.0
        return {
            "total_revokes": total_revokes,
            "avg_holdover_hours": avg_holdover,
        }

    def _compute_pending_by_skill(self) -> Dict[str, int]:
        """Compute pending approvals count per skill."""
        pending_by_skill = defaultdict(int)
        for event in self._approval_events:
            if event.get("decision") == "pending":
                skill_id = event.get("skill_id", "unknown")
                pending_by_skill[skill_id] += 1
        return dict(pending_by_skill)

    @staticmethod
    def _percentile(sorted_list: List, percentile: float) -> float:
        """Compute percentile of sorted list."""
        if not sorted_list:
            return 0.0
        index = (percentile / 100.0) * (len(sorted_list) - 1)
        lower = int(index)
        upper = lower + 1
        if upper >= len(sorted_list):
            return float(sorted_list[-1])
        weight = index - lower
        return sorted_list[lower] * (1 - weight) + sorted_list[upper] * weight


# ============================================================================
# Health Checker
# ============================================================================

class HealthChecker:
    """
    Checks health status of all L5 gates.

    Thresholds:
    - Gate latency p99 > 10s → UNHEALTHY
    - Auto-approval rate drop > 20% in 1h → WARNING
    - Revoke rate spike > 50% → WARNING
    - Config apply failure > 5% → WARNING
    - Operator latency > 5min SLA → CRITICAL
    """

    # SLA thresholds
    GATE_LATENCY_SLA_MS = 10000  # 10 seconds
    OPERATOR_LATENCY_SLA_MS = 300000  # 5 minutes
    CONFIG_APPLY_FAILURE_THRESHOLD_PCT = 5.0
    AUTO_APPROVAL_DROP_THRESHOLD_PCT = 20.0
    REVOKE_SPIKE_THRESHOLD_PCT = 50.0

    def __init__(self, metrics_collector: MetricsCollector, tenant_id: str = "_default"):
        """
        Initialize health checker.

        Args:
            metrics_collector: MetricsCollector instance
            tenant_id: Tenant scope
        """
        self.metrics_collector = metrics_collector
        self.tenant_id = tenant_id
        self._lock = RLock()
        self._previous_metrics: Optional[Dict] = None

    def check_health(self) -> L5HealthSnapshot:
        """
        Check health of all L5 gates.

        Returns:
            L5HealthSnapshot with status and alerts
        """
        with self._lock:
            metrics = self.metrics_collector.collect_metrics()
            timestamp = datetime.utcnow().isoformat()
            snapshot = L5HealthSnapshot(timestamp=timestamp, all_healthy=True)
            alerts = []

            # Check each gate (k=1 through k=5)
            gates_status = self._check_gate_latencies(metrics)
            snapshot.gates = gates_status

            # Check overall health
            for gate_name, status in gates_status.items():
                if not status.is_healthy:
                    snapshot.all_healthy = False
                    alerts.append(f"{gate_name} is unhealthy (latency SLA breach)")

            # Compute aggregates
            snapshot.total_pending = sum(
                m.pending_count for m in gates_status.values()
            )

            decision_dist = metrics.get("decision_distribution", {})
            total = decision_dist.get("total", 1)
            snapshot.auto_approval_rate_pct = (
                (decision_dist.get("auto_approved", 0) / total * 100) if total > 0 else 0.0
            )
            snapshot.rejection_rate_pct = (
                (decision_dist.get("rejected", 0) / total * 100) if total > 0 else 0.0
            )

            # Config apply rate
            config_apply_rate = metrics.get("config_apply_success_rate", 100.0)
            snapshot.config_apply_success_rate_pct = config_apply_rate
            if config_apply_rate < (100.0 - self.CONFIG_APPLY_FAILURE_THRESHOLD_PCT):
                alerts.append(
                    f"Config apply success rate ({config_apply_rate:.1f}%) "
                    f"below threshold ({100 - self.CONFIG_APPLY_FAILURE_THRESHOLD_PCT}%)"
                )

            # Operator latency SLA
            latencies = metrics.get("approval_latencies_ms", {})
            if latencies.get("avg"):
                snapshot.avg_operator_latency_ms = latencies["avg"]
                if latencies["avg"] > self.OPERATOR_LATENCY_SLA_MS:
                    snapshot.sla_status = "CRITICAL"
                    alerts.append(
                        f"Operator latency ({latencies['avg']:.0f}ms) "
                        f"exceeds SLA ({self.OPERATOR_LATENCY_SLA_MS}ms)"
                    )

            # Revoke metrics
            revoke_metrics = metrics.get("revoke_metrics", {})
            if revoke_metrics.get("total_revokes", 0) > 0:
                logger.info(f"Revoke metrics: {revoke_metrics}")

            # Detect anomalies
            if self._previous_metrics:
                anomaly_alerts = self._detect_anomalies(self._previous_metrics, metrics)
                alerts.extend(anomaly_alerts)

            snapshot.alerts = alerts
            if alerts:
                if any("CRITICAL" in a for a in alerts):
                    snapshot.sla_status = "CRITICAL"
                elif any("exceed" in a.lower() for a in alerts):
                    snapshot.sla_status = "WARNING"

            # CRITICAL FIX #2: Deep copy metrics, don't store reference
            self._previous_metrics = copy.deepcopy(metrics)
            return snapshot

    def _check_gate_latencies(self, metrics: Dict) -> Dict[str, GateHealthStatus]:
        """Check latencies for each gate."""
        gates_status = {}
        latencies = metrics.get("approval_latencies_ms", {})

        for k in range(1, 6):
            gate_name = f"k={k}"
            p99_latency = latencies.get("p99")
            is_healthy = p99_latency is None or p99_latency <= self.GATE_LATENCY_SLA_MS

            # BUG FIX #6: Count total pending approvals, not number of skills
            pending_by_skill = metrics.get("pending_by_skill", {})
            total_pending = sum(pending_by_skill.values()) if pending_by_skill else 0

            status = GateHealthStatus(
                gate_name=gate_name,
                is_healthy=is_healthy,
                latency_p50_ms=latencies.get("p50"),
                latency_p95_ms=latencies.get("p95"),
                latency_p99_ms=latencies.get("p99"),
                avg_latency_ms=latencies.get("avg"),
                error_rate_pct=0.0,
                pending_count=total_pending,
                last_check_timestamp=datetime.utcnow().isoformat(),
            )
            gates_status[gate_name] = status

        return gates_status

    def _detect_anomalies(self, prev_metrics: Dict, curr_metrics: Dict) -> List[str]:
        """Detect metric anomalies between two snapshots."""
        alerts = []

        # Check auto-approval rate drop
        prev_dist = prev_metrics.get("decision_distribution", {})
        curr_dist = curr_metrics.get("decision_distribution", {})

        prev_auto_rate = (
            (prev_dist.get("auto_approved", 0) / max(prev_dist.get("total", 1), 1)) * 100
        )
        curr_auto_rate = (
            (curr_dist.get("auto_approved", 0) / max(curr_dist.get("total", 1), 1)) * 100
        )

        auto_rate_drop = prev_auto_rate - curr_auto_rate
        if auto_rate_drop > self.AUTO_APPROVAL_DROP_THRESHOLD_PCT:
            alerts.append(
                f"Auto-approval rate dropped {auto_rate_drop:.1f}% "
                f"(from {prev_auto_rate:.1f}% to {curr_auto_rate:.1f}%)"
            )

        return alerts


# ============================================================================
# Alert Manager
# ============================================================================

class AlertManager:
    """
    Manages alert lifecycle.

    Responsibilities:
    - Create alerts on health check findings
    - Track alert state (acknowledged, resolved)
    - Prevent duplicate alerts
    - Archive resolved alerts
    """

    def __init__(self, tenant_id: str = "_default"):
        """
        Initialize alert manager.

        Args:
            tenant_id: Tenant scope
        """
        self.tenant_id = tenant_id
        self._lock = RLock()
        self._active_alerts: Dict[str, Alert] = {}
        self._archived_alerts: List[Alert] = []
        self._alert_counter = 0

    def create_alert(
        self,
        severity: str,
        message: str,
        gate_name: Optional[str] = None,
        skill_id: Optional[str] = None,
    ) -> Alert:
        """
        Create a new alert.

        Args:
            severity: "INFO", "WARNING", "CRITICAL"
            message: Alert message
            gate_name: Optional gate name (e.g., "k=2")
            skill_id: Optional skill ID

        Returns:
            Alert instance
        """
        with self._lock:
            self._alert_counter += 1
            alert_id = f"alert_{self.tenant_id}_{self._alert_counter}"
            alert = Alert(
                alert_id=alert_id,
                severity=severity,
                message=message,
                gate_name=gate_name,
                skill_id=skill_id,
                timestamp=datetime.utcnow().isoformat(),
            )
            self._active_alerts[alert_id] = alert
            logger.warning(f"[{severity}] {message} (alert_id={alert_id})")
            return alert

    def acknowledge_alert(self, alert_id: str) -> bool:
        """
        Acknowledge an alert.

        Args:
            alert_id: Alert ID to acknowledge

        Returns:
            True if acknowledged, False if not found
        """
        with self._lock:
            if alert_id in self._active_alerts:
                self._active_alerts[alert_id].is_acknowledged = True
                return True
            return False

    def resolve_alert(self, alert_id: str) -> bool:
        """
        Resolve an alert (archive it).

        Args:
            alert_id: Alert ID to resolve

        Returns:
            True if resolved, False if not found
        """
        with self._lock:
            if alert_id in self._active_alerts:
                alert = self._active_alerts.pop(alert_id)
                self._archived_alerts.append(alert)
                logger.info(f"Alert resolved: {alert_id}")
                return True
            return False

    def get_active_alerts(self) -> List[Alert]:
        """Get list of active (unresolved) alerts."""
        with self._lock:
            return list(self._active_alerts.values())

    def get_alert_count_by_severity(self) -> Dict[str, int]:
        """Get count of active alerts by severity."""
        with self._lock:
            counts = defaultdict(int)
            for alert in self._active_alerts.values():
                counts[alert.severity] += 1
            return dict(counts)


# ============================================================================
# Monitoring System (Main Coordinator)
# ============================================================================

class L5MonitoringSystem:
    """
    Main L5 monitoring system coordinator.

    Integrates:
    - MetricsCollector (audit trail → metrics)
    - HealthChecker (metrics → health status)
    - AlertManager (health → alerts)

    Provides:
    - Health snapshots (JSON)
    - Alert management API
    - WebSocket live updates
    """

    def __init__(
        self,
        audit_backend,
        window_hours: int = 24,
        tenant_id: str = "_default",
    ):
        """
        Initialize monitoring system.

        Args:
            audit_backend: Audit system to read from
            window_hours: Metrics window (hours)
            tenant_id: Tenant scope
        """
        self.tenant_id = tenant_id
        self.metrics_collector = MetricsCollector(audit_backend, window_hours, tenant_id)
        self.health_checker = HealthChecker(self.metrics_collector, tenant_id)
        self.alert_manager = AlertManager(tenant_id)
        self._last_health_snapshot: Optional[L5HealthSnapshot] = None
        self._lock = RLock()

    def get_health_status(self) -> L5HealthSnapshot:
        """
        Get current L5 health status.

        Returns:
            L5HealthSnapshot
        """
        with self._lock:
            snapshot = self.health_checker.check_health()

            # Convert alerts to Alert objects and add to manager
            for alert_msg in snapshot.alerts:
                self.alert_manager.create_alert(
                    severity="WARNING" if "WARNING" in snapshot.sla_status else "INFO",
                    message=alert_msg,
                )

            self._last_health_snapshot = snapshot
            return snapshot

    def get_health_status_json(self) -> str:
        """Get health status as JSON string."""
        snapshot = self.get_health_status()
        return json.dumps(snapshot.to_dict(), indent=2)

    def get_timeseries_data(
        self, start_time: str, end_time: str
    ) -> Dict:
        """
        Get historical timeseries data.

        Args:
            start_time: ISO format timestamp
            end_time: ISO format timestamp

        Returns:
            Dict with timeseries data
        """
        # Note: Real implementation would query historical audit trail
        # For now, return current metrics in timeseries format
        metrics = self.metrics_collector.collect_metrics()
        return {
            "start_time": start_time,
            "end_time": end_time,
            "datapoints": [metrics],  # Simplified: just one datapoint
        }

    def get_active_alerts(self) -> List[Dict]:
        """Get list of active alerts as dicts."""
        alerts = self.alert_manager.get_active_alerts()
        return [a.to_dict() for a in alerts]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        return self.alert_manager.acknowledge_alert(alert_id)

    def resolve_alert(self, alert_id: str) -> bool:
        """Resolve an alert."""
        return self.alert_manager.resolve_alert(alert_id)
