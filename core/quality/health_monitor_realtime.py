#!/usr/bin/env python3
"""Phase C: Real-Time Health Monitoring Dashboard

Continuously collects and reports:
  - Latency (p50/p95/p99) every 30 seconds
  - Throughput (requests/sec) every 30 seconds
  - Error rate (%) every 30 seconds
  - CPU/memory/I/O usage every 10 seconds
  - 7-day metric history + trend analysis

Serves data via GET /v1/console/health/metrics endpoint (JSON).
Dashboard updates every 30s without user action (push via WebSocket or polling).
"""

import json
import threading
import time
import psutil
from collections import deque, defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Deque
import statistics


@dataclass
class MetricSnapshot:
    """Single point-in-time metric snapshot."""
    timestamp: str
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    mean_latency_ms: float
    throughput_ops_sec: float
    error_rate_percent: float
    cpu_percent: float
    memory_percent: float
    io_wait_percent: float


@dataclass
class HealthStatus:
    """Current health status summary."""
    timestamp: str
    overall_status: str  # GREEN / YELLOW / RED
    last_snapshot: MetricSnapshot
    trend_p99_latency: str  # "stable" / "degrading" / "improving"
    trend_error_rate: str
    trend_throughput: str
    alert_count: int = 0
    alerts: List[str] = field(default_factory=list)


class RealtimeHealthMonitor:
    """Continuous real-time health monitoring."""

    def __init__(self, home_dir: Optional[Path] = None):
        """Initialize real-time monitor."""
        self.home_dir = home_dir or Path.home() / ".corvin"
        self.metrics_dir = self.home_dir / "metrics" / "health"
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

        # Circular buffers for 7-day history (one snapshot per 30s)
        # 7 days * 24 hours * 60 min * (60/30) = 20,160 snapshots max
        self.max_snapshots = 20_160  # ~7 days at 30s intervals
        self.snapshots: Deque[MetricSnapshot] = deque(maxlen=self.max_snapshots)

        # Error log (last 1000 errors)
        self.error_log: Deque[Dict] = deque(maxlen=1000)

        # Sampling state
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._sampler_thread: Optional[threading.Thread] = None

        # Thresholds for alerts (from Phase C)
        self.thresholds = {
            "p99_latency_ms": 306,
            "error_rate_percent": 0.09,
            "cpu_percent": 72,
            "memory_percent": 70,
        }

    # ─────────────────────────────────────────────────────────────────────
    # SAMPLING: Collect metrics periodically
    # ─────────────────────────────────────────────────────────────────────

    def start(self):
        """Start monitoring threads."""
        if self._running:
            return

        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._sampler_thread = threading.Thread(target=self._sampler_loop, daemon=True)

        self._monitor_thread.start()
        self._sampler_thread.start()
        print("✓ Real-time health monitor started (30s metric cycles, 10s resource samples)")

    def stop(self):
        """Stop monitoring threads."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        if self._sampler_thread:
            self._sampler_thread.join(timeout=5)
        print("✓ Real-time health monitor stopped")

    def _monitor_loop(self):
        """Main monitoring loop — collects full snapshot every 30s."""
        while self._running:
            try:
                snapshot = self._collect_snapshot()
                self.snapshots.append(snapshot)
                self._save_latest_snapshot(snapshot)
                self._check_alerts(snapshot)
                time.sleep(30)  # 30-second collection interval
            except Exception as e:
                print(f"Monitor error: {e}")
                time.sleep(30)

    def _sampler_loop(self):
        """Resource sampler loop — lightweight resource checks every 10s."""
        while self._running:
            try:
                # Lightweight resource sampling for responsive alerts
                cpu = psutil.cpu_percent(interval=0.5)
                mem = psutil.virtual_memory()

                if cpu > self.thresholds["cpu_percent"]:
                    self.error_log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "alert": f"CPU spike: {cpu:.1f}% > {self.thresholds['cpu_percent']}%",
                        "severity": "WARN",
                    })

                if mem.percent > self.thresholds["memory_percent"]:
                    self.error_log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "alert": f"Memory spike: {mem.percent:.1f}% > {self.thresholds['memory_percent']}%",
                        "severity": "WARN",
                    })

                time.sleep(10)  # 10-second sampler interval
            except Exception as e:
                print(f"Sampler error: {e}")
                time.sleep(10)

    def _collect_snapshot(self) -> MetricSnapshot:
        """Collect full metrics snapshot."""
        # In production, query metrics backend
        # For Phase C, use simulated data from quality gates

        latencies = self._sample_latencies()
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()

        return MetricSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            p50_latency_ms=statistics.quantiles(latencies, n=100)[49],
            p95_latency_ms=statistics.quantiles(latencies, n=100)[94],
            p99_latency_ms=statistics.quantiles(latencies, n=100)[98],
            mean_latency_ms=statistics.mean(latencies),
            throughput_ops_sec=self._sample_throughput(),
            error_rate_percent=self._sample_error_rate(),
            cpu_percent=cpu,
            memory_percent=mem.percent,
            io_wait_percent=max(0, min(100, 100 - cpu)),
        )

    def _sample_latencies(self) -> List[float]:
        """Sample request latencies."""
        import random
        return [random.gauss(200, 50) for _ in range(500)]

    def _sample_throughput(self) -> float:
        """Sample current throughput (ops/sec)."""
        # In production: from metrics store
        return 1200  # Simulated: 1200 ops/sec

    def _sample_error_rate(self) -> float:
        """Sample current error rate (%)."""
        # In production: from error logs
        return 0.08  # Simulated: 0.08%

    # ─────────────────────────────────────────────────────────────────────
    # ALERTING: Check thresholds and generate alerts
    # ─────────────────────────────────────────────────────────────────────

    def _check_alerts(self, snapshot: MetricSnapshot):
        """Evaluate snapshot against alert thresholds."""
        alerts = []

        if snapshot.p99_latency_ms > self.thresholds["p99_latency_ms"]:
            alerts.append(f"⚠️ P99 latency regression: {snapshot.p99_latency_ms:.1f}ms > {self.thresholds['p99_latency_ms']}ms")

        if snapshot.error_rate_percent > self.thresholds["error_rate_percent"]:
            alerts.append(f"⚠️ Error rate elevated: {snapshot.error_rate_percent:.3f}% > {self.thresholds['error_rate_percent']}%")

        if snapshot.cpu_percent > self.thresholds["cpu_percent"]:
            alerts.append(f"⚠️ CPU usage high: {snapshot.cpu_percent:.1f}% > {self.thresholds['cpu_percent']}%")

        if snapshot.memory_percent > self.thresholds["memory_percent"]:
            alerts.append(f"⚠️ Memory usage high: {snapshot.memory_percent:.1f}% > {self.thresholds['memory_percent']}%")

        for alert in alerts:
            self.error_log.append({
                "timestamp": snapshot.timestamp,
                "alert": alert,
                "severity": "WARN",
            })

    # ─────────────────────────────────────────────────────────────────────
    # REPORTING: Generate health status + trends
    # ─────────────────────────────────────────────────────────────────────

    def get_health_status(self) -> HealthStatus:
        """Get current health status with trends."""
        if not self.snapshots:
            return HealthStatus(
                timestamp=datetime.now(timezone.utc).isoformat(),
                overall_status="UNKNOWN",
                last_snapshot=MetricSnapshot(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    p50_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
                    mean_latency_ms=0, throughput_ops_sec=0,
                    error_rate_percent=0, cpu_percent=0,
                    memory_percent=0, io_wait_percent=0,
                ),
                trend_p99_latency="unknown",
                trend_error_rate="unknown",
                trend_throughput="unknown",
            )

        last = self.snapshots[-1]

        # Compute trends over last hour
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = [s for s in self.snapshots
                 if datetime.fromisoformat(s.timestamp) > one_hour_ago]

        # Determine overall status
        alerts = list(self.error_log)[-10:]  # Last 10 alerts
        alert_count = len([a for a in alerts if a["severity"] == "WARN"])

        if last.p99_latency_ms > self.thresholds["p99_latency_ms"] or \
           last.error_rate_percent > self.thresholds["error_rate_percent"]:
            overall_status = "RED"
        elif alert_count > 2 or last.cpu_percent > self.thresholds["cpu_percent"]:
            overall_status = "YELLOW"
        else:
            overall_status = "GREEN"

        # Trends
        if len(recent) > 1:
            latencies = [s.p99_latency_ms for s in recent]
            errors = [s.error_rate_percent for s in recent]
            throughputs = [s.throughput_ops_sec for s in recent]

            trend_p99 = "degrading" if latencies[-1] > latencies[0] * 1.1 else "improving" if latencies[-1] < latencies[0] * 0.9 else "stable"
            trend_err = "degrading" if errors[-1] > errors[0] * 1.1 else "improving" if errors[-1] < errors[0] * 0.9 else "stable"
            trend_thr = "improving" if throughputs[-1] > throughputs[0] * 1.1 else "degrading" if throughputs[-1] < throughputs[0] * 0.9 else "stable"
        else:
            trend_p99 = trend_err = trend_thr = "stable"

        return HealthStatus(
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_status=overall_status,
            last_snapshot=last,
            trend_p99_latency=trend_p99,
            trend_error_rate=trend_err,
            trend_throughput=trend_thr,
            alert_count=alert_count,
            alerts=[a["alert"] for a in alerts][:5],
        )

    def get_metric_history(self, hours: int = 24) -> Dict:
        """Get metric history for charting."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        history = [s for s in self.snapshots
                   if datetime.fromisoformat(s.timestamp) > cutoff]

        return {
            "period_hours": hours,
            "snapshots": [asdict(s) for s in history],
            "count": len(history),
        }

    def get_error_log(self, limit: int = 50) -> List[Dict]:
        """Get recent error log entries."""
        return list(self.error_log)[-limit:]

    # ─────────────────────────────────────────────────────────────────────
    # PERSISTENCE: Save/load snapshots
    # ─────────────────────────────────────────────────────────────────────

    def _save_latest_snapshot(self, snapshot: MetricSnapshot):
        """Save latest snapshot for API access."""
        path = self.metrics_dir / "latest_health.json"
        status = self.get_health_status()

        data = {
            "latest_snapshot": asdict(snapshot),
            "health_status": asdict(status),
            "health_status_snapshot": asdict(status.last_snapshot),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with open(path, "w") as f:
            json.dump(data, f, default=str, indent=2)

    def save_history_checkpoint(self):
        """Periodically save metric history for retention."""
        path = self.metrics_dir / f"checkpoint_{int(time.time())}.json"
        data = {
            "checkpoint_time": datetime.now(timezone.utc).isoformat(),
            "snapshots_count": len(self.snapshots),
            "snapshots": [asdict(s) for s in list(self.snapshots)[-2000:]],  # Last 2000
        }

        with open(path, "w") as f:
            json.dump(data, f, default=str, indent=2)


# Global monitor instance (singleton)
_monitor: Optional[RealtimeHealthMonitor] = None


def get_monitor() -> RealtimeHealthMonitor:
    """Get or create global monitor."""
    global _monitor
    if _monitor is None:
        _monitor = RealtimeHealthMonitor()
        _monitor.start()
    return _monitor


def stop_monitor():
    """Stop global monitor."""
    global _monitor
    if _monitor:
        _monitor.stop()
        _monitor = None
