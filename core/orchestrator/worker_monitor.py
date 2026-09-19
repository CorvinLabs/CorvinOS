"""
Worker Monitor for Real-Time Health Tracking and Performance Analysis
Implements ADR-0758 Phase 1 worker metrics + health probing.

Components:
1. WorkerRegistry — track active workers with resource metrics
2. HealthChecker — periodic health probes (heartbeat, resource checks)
3. MetricsCollector — aggregate performance (latency, throughput, errors)
4. AlertSystem — trigger alerts on threshold breaches

Use case: Monitor 100+ concurrent workers, detect failures <5s, <1% false alerts.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime, timedelta
import threading
import logging
import time
import json
from enum import Enum
from collections import deque
import statistics

logger = logging.getLogger(__name__)


class WorkerStatus(Enum):
    """Worker lifecycle states"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DEAD = "dead"


class AlertSeverity(Enum):
    """Alert priority levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class WorkerMetrics:
    """Snapshot of worker performance metrics"""
    worker_id: str
    tenant_id: str
    timestamp: datetime
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    latency_ms: float = 0.0
    throughput: int = 0  # tasks per minute
    error_rate: float = 0.0  # 0.0-1.0
    tasks_completed: int = 0
    tasks_failed: int = 0
    last_heartbeat: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON output"""
        return {
            "worker_id": self.worker_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp.isoformat(),
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "latency_ms": self.latency_ms,
            "throughput": self.throughput,
            "error_rate": round(self.error_rate, 4),
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "last_heartbeat": self.last_heartbeat.isoformat(),
        }


@dataclass
class Alert:
    """Alert event for threshold violations"""
    alert_id: str
    worker_id: str
    tenant_id: str
    severity: AlertSeverity
    metric_name: str
    metric_value: float
    threshold: float
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for audit trail"""
        return {
            "alert_id": self.alert_id,
            "worker_id": self.worker_id,
            "tenant_id": self.tenant_id,
            "severity": self.severity.value,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }


class WorkerRegistry:
    """
    Central registry of active workers with current metrics.
    Ensures tenant isolation (GDPR Art. 5).
    """

    def __init__(self):
        self._workers: Dict[str, WorkerMetrics] = {}
        self._lock = threading.RLock()

    def register(self, metrics: WorkerMetrics) -> None:
        """Register or update worker metrics"""
        with self._lock:
            self._workers[metrics.worker_id] = metrics
            logger.debug(f"Worker {metrics.worker_id} registered (tenant: {metrics.tenant_id})")

    def deregister(self, worker_id: str) -> None:
        """Remove worker from registry"""
        with self._lock:
            if worker_id in self._workers:
                del self._workers[worker_id]
                logger.debug(f"Worker {worker_id} deregistered")

    def get(self, worker_id: str) -> Optional[WorkerMetrics]:
        """Retrieve worker metrics"""
        with self._lock:
            return self._workers.get(worker_id)

    def get_all(self, tenant_id: Optional[str] = None) -> List[WorkerMetrics]:
        """List all workers, optionally filtered by tenant"""
        with self._lock:
            workers = list(self._workers.values())
            if tenant_id:
                workers = [w for w in workers if w.tenant_id == tenant_id]
            return workers

    def count(self, tenant_id: Optional[str] = None) -> int:
        """Count active workers"""
        return len(self.get_all(tenant_id))


class HealthChecker:
    """
    Periodic health probes of registered workers.
    Heartbeat + resource checks (CPU, memory).
    Detects failures in <5 seconds (configurable).
    """

    def __init__(
        self,
        registry: WorkerRegistry,
        check_interval_sec: float = 2.0,
        heartbeat_timeout_sec: float = 5.0,
    ):
        self.registry = registry
        self.check_interval_sec = check_interval_sec
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._probe_fn: Optional[Callable[[str], Dict[str, Any]]] = None

    def set_probe_function(self, fn: Callable[[str], Dict[str, Any]]) -> None:
        """
        Set function to probe worker health.
        fn(worker_id) -> {"cpu": float, "memory": float, "alive": bool, ...}
        """
        self._probe_fn = fn

    def start(self) -> None:
        """Start health checking loop"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self._thread.start()
        logger.info("HealthChecker started")

    def stop(self) -> None:
        """Stop health checking loop"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("HealthChecker stopped")

    def _health_check_loop(self) -> None:
        """Main loop: periodic probing"""
        while self._running:
            try:
                workers = self.registry.get_all()
                for metrics in workers:
                    self._probe_worker(metrics.worker_id)
                time.sleep(self.check_interval_sec)
            except Exception as e:
                logger.error(f"Health check error: {e}", exc_info=True)
                time.sleep(self.check_interval_sec)

    def _probe_worker(self, worker_id: str) -> Dict[str, Any]:
        """Probe single worker; return probe results or None if unhealthy"""
        metrics = self.registry.get(worker_id)
        if not metrics:
            return {}

        # Use injected probe function or simulate
        if self._probe_fn:
            try:
                probe_result = self._probe_fn(worker_id)
                self._update_metrics_from_probe(metrics, probe_result)
                return probe_result
            except Exception as e:
                logger.warning(f"Probe failed for {worker_id}: {e}")
                metrics.last_heartbeat = datetime.utcnow() + timedelta(seconds=self.heartbeat_timeout_sec)
                return {}

        # Simulate: CPU 30–70%, memory 200–500MB (demo)
        import random
        probe_result = {
            "cpu": random.uniform(30, 70),
            "memory": random.uniform(200, 500),
            "alive": True,
        }
        self._update_metrics_from_probe(metrics, probe_result)
        return probe_result

    def _update_metrics_from_probe(self, metrics: WorkerMetrics, probe: Dict[str, Any]) -> None:
        """Update metrics with probe result"""
        metrics.cpu_percent = probe.get("cpu", metrics.cpu_percent)
        metrics.memory_mb = probe.get("memory", metrics.memory_mb)
        metrics.last_heartbeat = datetime.utcnow()


class MetricsCollector:
    """
    Aggregate performance metrics: latency (p50/p99), throughput, errors.
    Maintains sliding window of recent observations (configurable window).
    """

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._latencies: Dict[str, deque] = {}  # worker_id → deque of latencies_ms
        self._throughputs: Dict[str, deque] = {}
        self._error_counts: Dict[str, deque] = {}  # worker_id → deque of error rates
        self._lock = threading.RLock()

    def record_latency(self, worker_id: str, latency_ms: float) -> None:
        """Record a single latency observation"""
        with self._lock:
            if worker_id not in self._latencies:
                self._latencies[worker_id] = deque(maxlen=self.window_size)
            self._latencies[worker_id].append(latency_ms)

    def record_error(self, worker_id: str, error_rate: float) -> None:
        """Record error rate (0.0-1.0)"""
        with self._lock:
            if worker_id not in self._error_counts:
                self._error_counts[worker_id] = deque(maxlen=self.window_size)
            self._error_counts[worker_id].append(error_rate)

    def record_throughput(self, worker_id: str, tasks_per_minute: int) -> None:
        """Record throughput (tasks/min)"""
        with self._lock:
            if worker_id not in self._throughputs:
                self._throughputs[worker_id] = deque(maxlen=self.window_size)
            self._throughputs[worker_id].append(tasks_per_minute)

    def get_latency_percentile(self, worker_id: str, percentile: float = 99.0) -> Optional[float]:
        """Get latency percentile (p50, p99, etc.)"""
        with self._lock:
            latencies = self._latencies.get(worker_id, [])
            if not latencies:
                return None
            sorted_lat = sorted(latencies)
            idx = int(len(sorted_lat) * (percentile / 100))
            return sorted_lat[min(idx, len(sorted_lat) - 1)]

    def get_avg_latency(self, worker_id: str) -> Optional[float]:
        """Get average latency"""
        with self._lock:
            latencies = self._latencies.get(worker_id, [])
            if not latencies:
                return None
            return statistics.mean(latencies)

    def get_avg_error_rate(self, worker_id: str) -> Optional[float]:
        """Get average error rate"""
        with self._lock:
            errors = self._error_counts.get(worker_id, [])
            if not errors:
                return None
            return statistics.mean(errors)

    def get_avg_throughput(self, worker_id: str) -> Optional[float]:
        """Get average throughput"""
        with self._lock:
            throughputs = self._throughputs.get(worker_id, [])
            if not throughputs:
                return None
            return statistics.mean(throughputs)

    def clear(self, worker_id: Optional[str] = None) -> None:
        """Clear metrics for worker or all workers"""
        with self._lock:
            if worker_id:
                self._latencies.pop(worker_id, None)
                self._throughputs.pop(worker_id, None)
                self._error_counts.pop(worker_id, None)
            else:
                self._latencies.clear()
                self._throughputs.clear()
                self._error_counts.clear()


class AlertSystem:
    """
    Trigger alerts when metrics breach thresholds.
    Maintains alert history (recent N alerts).
    Logs to audit trail (compliance).
    """

    def __init__(self, history_size: int = 1000):
        self.history_size = history_size
        self._alerts: deque = deque(maxlen=history_size)
        self._thresholds: Dict[str, Dict[str, float]] = {
            "cpu_percent": {"critical": 80.0, "warning": 60.0},
            "memory_mb": {"critical": 1024.0, "warning": 800.0},
            "latency_p99_ms": {"critical": 5000.0, "warning": 2000.0},
            "error_rate": {"critical": 0.10, "warning": 0.05},
        }
        self._lock = threading.Lock()
        self._alert_callbacks: List[Callable[[Alert], None]] = []

    def set_thresholds(self, thresholds: Dict[str, Dict[str, float]]) -> None:
        """Override default thresholds"""
        with self._lock:
            self._thresholds.update(thresholds)

    def add_callback(self, fn: Callable[[Alert], None]) -> None:
        """Register callback for alerts"""
        with self._lock:
            self._alert_callbacks.append(fn)

    def check_and_alert(
        self,
        worker_id: str,
        tenant_id: str,
        metrics: Dict[str, float],
    ) -> List[Alert]:
        """
        Check metrics against thresholds; emit alerts if breached.
        Returns: list of alerts emitted.
        """
        alerts_emitted = []

        with self._lock:
            thresholds = self._thresholds

        for metric_name, metric_value in metrics.items():
            if metric_name not in thresholds:
                continue

            threshold_levels = thresholds[metric_name]

            # Check critical
            if metric_value > threshold_levels.get("critical", float("inf")):
                alert = self._create_alert(
                    worker_id,
                    tenant_id,
                    metric_name,
                    metric_value,
                    threshold_levels["critical"],
                    AlertSeverity.CRITICAL,
                )
                alerts_emitted.append(alert)
            # Check warning
            elif metric_value > threshold_levels.get("warning", float("inf")):
                alert = self._create_alert(
                    worker_id,
                    tenant_id,
                    metric_name,
                    metric_value,
                    threshold_levels["warning"],
                    AlertSeverity.WARNING,
                )
                alerts_emitted.append(alert)

        # Emit callbacks
        for alert in alerts_emitted:
            with self._lock:
                self._alerts.append(alert)
                for callback in self._alert_callbacks:
                    try:
                        callback(alert)
                    except Exception as e:
                        logger.error(f"Alert callback error: {e}")

        return alerts_emitted

    def _create_alert(
        self,
        worker_id: str,
        tenant_id: str,
        metric_name: str,
        metric_value: float,
        threshold: float,
        severity: AlertSeverity,
    ) -> Alert:
        """Create alert event"""
        import uuid
        alert_id = f"alert-{uuid.uuid4().hex[:8]}"
        message = f"Worker {worker_id} {metric_name}={metric_value:.2f} exceeds {severity.value} threshold {threshold:.2f}"

        return Alert(
            alert_id=alert_id,
            worker_id=worker_id,
            tenant_id=tenant_id,
            severity=severity,
            metric_name=metric_name,
            metric_value=metric_value,
            threshold=threshold,
            message=message,
        )

    def get_alerts(
        self,
        worker_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Alert]:
        """Retrieve alert history"""
        with self._lock:
            alerts = list(self._alerts)

        # Filter
        if worker_id:
            alerts = [a for a in alerts if a.worker_id == worker_id]
        if tenant_id:
            alerts = [a for a in alerts if a.tenant_id == tenant_id]

        # Return most recent N
        return alerts[-limit:] if len(alerts) > limit else alerts

    def clear_history(self) -> None:
        """Clear alert history"""
        with self._lock:
            self._alerts.clear()


class WorkerMonitor:
    """
    Unified worker monitoring orchestrator.
    Integrates registry, health checker, metrics collector, and alert system.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.registry = WorkerRegistry()
        self.health_checker = HealthChecker(self.registry)
        self.metrics_collector = MetricsCollector()
        self.alert_system = AlertSystem()

        # Register alert callback to log via audit
        self.alert_system.add_callback(self._log_alert_to_audit)
        self._lock = threading.Lock()

    def register_worker(self, worker_id: str) -> None:
        """Register new worker for monitoring"""
        metrics = WorkerMetrics(
            worker_id=worker_id,
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow(),
        )
        self.registry.register(metrics)
        logger.info(f"Worker {worker_id} registered in monitor")

    def deregister_worker(self, worker_id: str) -> None:
        """Stop monitoring worker"""
        self.registry.deregister(worker_id)
        self.metrics_collector.clear(worker_id)
        logger.info(f"Worker {worker_id} deregistered from monitor")

    def update_worker_metrics(
        self,
        worker_id: str,
        cpu_percent: Optional[float] = None,
        memory_mb: Optional[float] = None,
        latency_ms: Optional[float] = None,
        throughput: Optional[int] = None,
        error_rate: Optional[float] = None,
        tasks_completed: Optional[int] = None,
        tasks_failed: Optional[int] = None,
    ) -> None:
        """Update metrics for a worker"""
        metrics = self.registry.get(worker_id)
        if not metrics:
            logger.warning(f"Worker {worker_id} not registered")
            return

        # Update fields
        if cpu_percent is not None:
            metrics.cpu_percent = cpu_percent
        if memory_mb is not None:
            metrics.memory_mb = memory_mb
        if latency_ms is not None:
            metrics.latency_ms = latency_ms
            self.metrics_collector.record_latency(worker_id, latency_ms)
        if throughput is not None:
            metrics.throughput = throughput
            self.metrics_collector.record_throughput(worker_id, throughput)
        if error_rate is not None:
            metrics.error_rate = error_rate
            self.metrics_collector.record_error(worker_id, error_rate)
        if tasks_completed is not None:
            metrics.tasks_completed = tasks_completed
        if tasks_failed is not None:
            metrics.tasks_failed = tasks_failed

        metrics.timestamp = datetime.utcnow()
        self.registry.register(metrics)

        # Check thresholds and emit alerts
        check_metrics = {
            "cpu_percent": metrics.cpu_percent,
            "memory_mb": metrics.memory_mb,
            "latency_p99_ms": self.metrics_collector.get_latency_percentile(worker_id, 99.0) or 0,
            "error_rate": metrics.error_rate,
        }
        self.alert_system.check_and_alert(worker_id, self.tenant_id, check_metrics)

    def get_worker_status(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Get current status and aggregated metrics for a worker"""
        metrics = self.registry.get(worker_id)
        if not metrics:
            return None

        return {
            "worker_id": worker_id,
            "tenant_id": metrics.tenant_id,
            "status": self._compute_status(metrics),
            "metrics": metrics.to_dict(),
            "latency_p50_ms": self.metrics_collector.get_latency_percentile(worker_id, 50.0),
            "latency_p99_ms": self.metrics_collector.get_latency_percentile(worker_id, 99.0),
            "avg_latency_ms": self.metrics_collector.get_avg_latency(worker_id),
            "avg_throughput": self.metrics_collector.get_avg_throughput(worker_id),
            "avg_error_rate": self.metrics_collector.get_avg_error_rate(worker_id),
        }

    def get_cluster_status(self) -> Dict[str, Any]:
        """Get aggregated status for all workers (tenant-scoped)"""
        workers = self.registry.get_all(self.tenant_id)

        if not workers:
            return {
                "tenant_id": self.tenant_id,
                "worker_count": 0,
                "status": "no_workers",
            }

        healthy_count = sum(1 for w in workers if self._compute_status_enum(w) == WorkerStatus.HEALTHY)
        degraded_count = sum(1 for w in workers if self._compute_status_enum(w) == WorkerStatus.DEGRADED)
        unhealthy_count = sum(1 for w in workers if self._compute_status_enum(w) == WorkerStatus.UNHEALTHY)

        return {
            "tenant_id": self.tenant_id,
            "worker_count": len(workers),
            "healthy": healthy_count,
            "degraded": degraded_count,
            "unhealthy": unhealthy_count,
            "avg_cpu_percent": sum(w.cpu_percent for w in workers) / len(workers),
            "avg_memory_mb": sum(w.memory_mb for w in workers) / len(workers),
            "recent_alerts": len(self.alert_system.get_alerts(tenant_id=self.tenant_id, limit=10)),
        }

    def _compute_status(self, metrics: WorkerMetrics) -> str:
        """Compute worker status as string"""
        return self._compute_status_enum(metrics).value

    def _compute_status_enum(self, metrics: WorkerMetrics) -> WorkerStatus:
        """Compute worker status"""
        now = datetime.utcnow()
        heartbeat_age = (now - metrics.last_heartbeat).total_seconds()

        # Dead: no heartbeat for 10+ seconds
        if heartbeat_age > 10.0:
            return WorkerStatus.DEAD

        # Unhealthy: CPU >80% OR error_rate >10% OR no heartbeat for 5+ seconds
        if metrics.cpu_percent > 80 or metrics.error_rate > 0.10 or heartbeat_age > 5.0:
            return WorkerStatus.UNHEALTHY

        # Degraded: CPU >60% OR error_rate >5%
        if metrics.cpu_percent > 60 or metrics.error_rate > 0.05:
            return WorkerStatus.DEGRADED

        return WorkerStatus.HEALTHY

    def _log_alert_to_audit(self, alert: Alert) -> None:
        """Callback to log alert to audit trail (compliance)"""
        logger.warning(f"ALERT [{alert.severity.value}] {alert.message}")
        # In real implementation, this would write to audit.jsonl

    def start(self) -> None:
        """Start health checker"""
        self.health_checker.start()

    def stop(self) -> None:
        """Stop health checker"""
        self.health_checker.stop()

    def export_metrics(self) -> Dict[str, Any]:
        """Export metrics as JSON (for dashboard, hourly snapshots)"""
        workers = self.registry.get_all(self.tenant_id)

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": self.tenant_id,
            "cluster_status": self.get_cluster_status(),
            "workers": [self.get_worker_status(w.worker_id) for w in workers],
            "recent_alerts": [a.to_dict() for a in self.alert_system.get_alerts(tenant_id=self.tenant_id, limit=50)],
        }


# Global monitor instances (one per tenant)
_monitors: Dict[str, WorkerMonitor] = {}
_monitor_lock = threading.Lock()


def get_monitor(tenant_id: str = "_default") -> WorkerMonitor:
    """Get or create monitor for tenant"""
    with _monitor_lock:
        if tenant_id not in _monitors:
            _monitors[tenant_id] = WorkerMonitor(tenant_id=tenant_id)
        return _monitors[tenant_id]
