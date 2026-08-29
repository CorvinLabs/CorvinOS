"""Production Monitoring Framework for ADR-0423 Phase 5.

Exports metrics for production observability:
- Workflow throughput, latency p50/p95/p99
- Error rates by component
- Feature-tier distribution
- Audit trail growth + storage utilization
- MemoryCoordinator activity

Interfaces:
- JSON metrics endpoint (`/v1/monitoring/production`)
- Prometheus-compatible format (optional)
- Alert thresholds (operator-configurable)

Status: PHASE 5 k=1 PRODUCTION MONITORING FOUNDATION
"""

import time
import json
import threading
from typing import Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime, timedelta
from collections import defaultdict, deque
import logging

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Metric type classifications."""
    THROUGHPUT = "throughput"
    LATENCY = "latency"
    ERROR = "error"
    FEATURE = "feature"
    AUDIT = "audit"
    MEMORY = "memory"


@dataclass
class MetricValue:
    """Single metric value with timestamp."""
    name: str
    value: float
    metric_type: MetricType
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "value": self.value,
            "type": self.metric_type.value,
            "timestamp": self.timestamp,
            "tags": self.tags,
        }


@dataclass
class PercentileMetrics:
    """Percentile tracking for latency/throughput."""
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    min: float = float('inf')
    max: float = 0.0
    count: int = 0
    sum: float = 0.0

    def add_value(self, value: float):
        """Add a value to the distribution."""
        self.count += 1
        self.sum += value
        self.min = min(self.min, value)
        self.max = max(self.max, value)

    def compute(self, values: list) -> None:
        """Compute percentiles from a list of values."""
        if not values:
            return
        sorted_values = sorted(values)
        n = len(sorted_values)
        self.p50 = sorted_values[int(n * 0.50)]
        self.p95 = sorted_values[int(n * 0.95)]
        self.p99 = sorted_values[int(n * 0.99)]

    def to_dict(self) -> Dict[str, float]:
        """Serialize to dictionary."""
        return {
            "p50": self.p50,
            "p95": self.p95,
            "p99": self.p99,
            "min": self.min if self.min != float('inf') else 0.0,
            "max": self.max,
            "count": self.count,
            "avg": self.sum / self.count if self.count > 0 else 0.0,
        }


@dataclass
class ProductionMetrics:
    """Aggregate production metrics."""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Throughput (workflows/sec, decisions/sec)
    workflow_throughput: float = 0.0
    decision_throughput: float = 0.0

    # Latency percentiles (milliseconds)
    decision_latency: PercentileMetrics = field(default_factory=PercentileMetrics)

    # Error rates
    error_rate_percent: float = 0.0
    errors_by_component: Dict[str, int] = field(default_factory=dict)

    # Feature-tier distribution
    features_by_tier: Dict[str, int] = field(default_factory=lambda: {
        "ALPHA": 0,
        "BETA": 0,
        "STABLE": 0,
        "PRODUCTION": 0,
    })

    # Audit trail
    audit_trail_events: int = 0
    audit_trail_growth_per_hour: float = 0.0
    audit_storage_mb: float = 0.0

    # Memory/MemoryCoordinator
    memory_split_events: int = 0
    memory_merge_events: int = 0
    active_memory_contexts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "timestamp": self.timestamp,
            "throughput": {
                "workflows_per_sec": self.workflow_throughput,
                "decisions_per_sec": self.decision_throughput,
            },
            "latency_ms": self.decision_latency.to_dict(),
            "errors": {
                "error_rate_percent": self.error_rate_percent,
                "by_component": self.errors_by_component,
            },
            "features": self.features_by_tier,
            "audit": {
                "total_events": self.audit_trail_events,
                "growth_per_hour": self.audit_trail_growth_per_hour,
                "storage_mb": self.audit_storage_mb,
            },
            "memory": {
                "split_events": self.memory_split_events,
                "merge_events": self.memory_merge_events,
                "active_contexts": self.active_memory_contexts,
            },
        }

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class AlertThreshold:
    """Alert threshold configuration."""
    name: str
    metric_name: str
    threshold: float
    comparison: str  # "gt" (>), "lt" (<), "eq" (==)
    severity: str  # "info", "warning", "critical"
    enabled: bool = True

    def check(self, value: float) -> bool:
        """Check if value triggers alert."""
        if not self.enabled:
            return False
        if self.comparison == "gt":
            return value > self.threshold
        elif self.comparison == "lt":
            return value < self.threshold
        elif self.comparison == "eq":
            return value == self.threshold
        return False


class ProductionMonitoringService:
    """Centralized production monitoring service."""

    def __init__(self, max_history_samples: int = 100):
        self.max_history_samples = max_history_samples
        self.lock = threading.Lock()

        # Metrics storage
        self.current_metrics = ProductionMetrics()
        self.metrics_history: deque = deque(maxlen=max_history_samples)

        # Time series for percentile computation
        self.latency_samples: deque = deque(maxlen=10000)
        self.throughput_samples: deque = deque(maxlen=1000)

        # Alerts
        self.alert_thresholds = self._default_alert_thresholds()
        self.recent_alerts: list = []

    def _default_alert_thresholds(self) -> list:
        """Create default alert thresholds."""
        return [
            AlertThreshold("high_error_rate", "error_rate", 1.0, "gt", "critical"),
            AlertThreshold("high_latency", "latency_p99", 500.0, "gt", "warning"),
            AlertThreshold("low_throughput", "throughput", 50.0, "lt", "warning"),
            AlertThreshold("audit_backlog", "audit_events", 100000, "gt", "warning"),
            AlertThreshold("feature_stuck_alpha", "alpha_duration", 30, "gt", "info"),
        ]

    def record_workflow_completion(
        self,
        num_nodes: int,
        duration_ms: float,
        errors: Optional[list] = None,
    ):
        """Record a workflow completion.

        Args:
            num_nodes: Number of nodes in workflow
            duration_ms: Total duration in milliseconds
            errors: Optional list of errors
        """
        with self.lock:
            # Latency: average per decision
            decision_latency = duration_ms / max(num_nodes, 1)
            self.latency_samples.append(decision_latency)

            # Throughput: decisions per second
            throughput_dps = (num_nodes * 1000.0) / max(duration_ms, 1)
            self.throughput_samples.append(throughput_dps)

            if errors:
                for error in errors:
                    self.current_metrics.errors_by_component[error] = \
                        self.current_metrics.errors_by_component.get(error, 0) + 1

    def record_error(self, component: str):
        """Record an error in a component."""
        with self.lock:
            self.current_metrics.errors_by_component[component] = \
                self.current_metrics.errors_by_component.get(component, 0) + 1

    def update_feature_state(self, feature_id: str, tier: str):
        """Update a feature's tier state."""
        with self.lock:
            if tier in self.current_metrics.features_by_tier:
                self.current_metrics.features_by_tier[tier] += 1

    def update_audit_metrics(
        self,
        total_events: int,
        storage_mb: float,
        growth_per_hour: float,
    ):
        """Update audit trail metrics."""
        with self.lock:
            self.current_metrics.audit_trail_events = total_events
            self.current_metrics.audit_storage_mb = storage_mb
            self.current_metrics.audit_trail_growth_per_hour = growth_per_hour

    def update_memory_metrics(
        self,
        split_events: int,
        merge_events: int,
        active_contexts: int,
    ):
        """Update MemoryCoordinator metrics."""
        with self.lock:
            self.current_metrics.memory_split_events = split_events
            self.current_metrics.memory_merge_events = merge_events
            self.current_metrics.active_memory_contexts = active_contexts

    def compute_and_snapshot(self) -> ProductionMetrics:
        """Compute percentiles and create a metrics snapshot."""
        with self.lock:
            # Compute latency percentiles
            if self.latency_samples:
                self.current_metrics.decision_latency.compute(list(self.latency_samples))

            # Compute throughput (workflows/sec)
            if self.throughput_samples:
                avg_throughput = sum(self.throughput_samples) / len(self.throughput_samples)
                self.current_metrics.workflow_throughput = avg_throughput / 100  # Rough conversion

            # Compute error rate
            total_errors = sum(self.current_metrics.errors_by_component.values())
            total_workflows = len(self.throughput_samples)
            if total_workflows > 0:
                self.current_metrics.error_rate_percent = (total_errors / total_workflows) * 100

            # Snapshot
            snapshot = ProductionMetrics(
                timestamp=datetime.utcnow().isoformat(),
                workflow_throughput=self.current_metrics.workflow_throughput,
                decision_throughput=self.current_metrics.decision_latency.count,
                decision_latency=PercentileMetrics(
                    p50=self.current_metrics.decision_latency.p50,
                    p95=self.current_metrics.decision_latency.p95,
                    p99=self.current_metrics.decision_latency.p99,
                    min=self.current_metrics.decision_latency.min,
                    max=self.current_metrics.decision_latency.max,
                    count=self.current_metrics.decision_latency.count,
                    sum=self.current_metrics.decision_latency.sum,
                ),
                error_rate_percent=self.current_metrics.error_rate_percent,
                errors_by_component=self.current_metrics.errors_by_component.copy(),
                features_by_tier=self.current_metrics.features_by_tier.copy(),
                audit_trail_events=self.current_metrics.audit_trail_events,
                audit_trail_growth_per_hour=self.current_metrics.audit_trail_growth_per_hour,
                audit_storage_mb=self.current_metrics.audit_storage_mb,
                memory_split_events=self.current_metrics.memory_split_events,
                memory_merge_events=self.current_metrics.memory_merge_events,
                active_memory_contexts=self.current_metrics.active_memory_contexts,
            )

            self.metrics_history.append(snapshot)
            return snapshot

    def check_alerts(self) -> list:
        """Check all thresholds and return triggered alerts."""
        snapshot = self.compute_and_snapshot()
        alerts = []

        for threshold in self.alert_thresholds:
            if threshold.metric_name == "error_rate":
                value = snapshot.error_rate_percent
            elif threshold.metric_name == "latency_p99":
                value = snapshot.decision_latency.p99
            elif threshold.metric_name == "throughput":
                value = snapshot.workflow_throughput
            elif threshold.metric_name == "audit_events":
                value = snapshot.audit_trail_events
            else:
                continue

            if threshold.check(value):
                alerts.append({
                    "threshold": threshold.name,
                    "metric": threshold.metric_name,
                    "value": value,
                    "threshold_value": threshold.threshold,
                    "severity": threshold.severity,
                    "timestamp": datetime.utcnow().isoformat(),
                })

        self.recent_alerts.extend(alerts)
        return alerts

    def get_json_export(self) -> str:
        """Export current metrics as JSON for Grafana/Prometheus."""
        snapshot = self.compute_and_snapshot()
        return snapshot.to_json()

    def get_metrics_dict(self) -> Dict[str, Any]:
        """Get metrics as dictionary."""
        snapshot = self.compute_and_snapshot()
        return snapshot.to_dict()


# Global singleton instance
_monitoring_service: Optional[ProductionMonitoringService] = None


def get_monitoring_service() -> ProductionMonitoringService:
    """Get or create global monitoring service."""
    global _monitoring_service
    if _monitoring_service is None:
        _monitoring_service = ProductionMonitoringService()
    return _monitoring_service


# Flask route integration (sample)
def register_monitoring_routes(app):
    """Register monitoring routes on a Flask app.

    Example:
        from core.features.production_monitoring import register_monitoring_routes
        register_monitoring_routes(app)
    """
    @app.route("/v1/monitoring/production", methods=["GET"])
    def get_production_metrics():
        """Export current production metrics."""
        svc = get_monitoring_service()
        return svc.get_metrics_dict()

    @app.route("/v1/monitoring/production/json", methods=["GET"])
    def get_production_metrics_json():
        """Export metrics as JSON."""
        svc = get_monitoring_service()
        return svc.get_json_export()

    @app.route("/v1/monitoring/alerts", methods=["GET"])
    def get_alerts():
        """Get recent alerts."""
        svc = get_monitoring_service()
        svc.check_alerts()  # Re-check
        return {
            "alerts": svc.recent_alerts[-100:],  # Last 100 alerts
            "count": len(svc.recent_alerts),
        }
