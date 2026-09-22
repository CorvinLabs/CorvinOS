"""
Production Monitoring for Feedback Integration — ADR-2033 Week 2

Prometheus metrics, health checks, and alerting infrastructure.

Metrics:
  - feedback_events_total (counter): Total feedback events processed
  - feedback_latency_ms (histogram): Feedback processing latency
  - feedback_queue_size (gauge): Current queue depth
  - feedback_errors_total (counter): Processing errors
  - skill_config_updates_total (counter): Config updates applied

Health Check:
  - /health/feedback — returns 200 if queue operational, 503 if degraded
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LatencySample:
    """Latency sample (timestamp, duration_ms)."""
    timestamp: str
    duration_ms: float


@dataclass
class FeedbackMetrics:
    """Prometheus-style metrics for feedback system."""
    # Counters (monotonic increase only)
    total_feedback_processed: int = 0
    total_config_updates: int = 0
    total_errors: int = 0

    # Gauges (can go up/down)
    queue_size: int = 0
    queue_max_size: int = 1000

    # Histograms (distribution)
    latency_samples: List[LatencySample] = field(default_factory=list)
    latency_max_samples: int = 1000  # Keep last 1000 samples

    # Per-skill metrics
    skill_feedback_count: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skill_error_count: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Timestamps
    last_feedback_at: Optional[str] = None
    last_error_at: Optional[str] = None

    def record_feedback(self, skill_id: str, latency_ms: float):
        """Record successful feedback processing."""
        self.total_feedback_processed += 1
        self.skill_feedback_count[skill_id] += 1
        self.last_feedback_at = datetime.utcnow().isoformat() + "Z"

        # Record latency sample
        self.latency_samples.append(LatencySample(
            timestamp=datetime.utcnow().isoformat() + "Z",
            duration_ms=latency_ms
        ))
        # Keep only last N samples
        if len(self.latency_samples) > self.latency_max_samples:
            self.latency_samples = self.latency_samples[-self.latency_max_samples:]

    def record_config_update(self, skill_id: str):
        """Record config update."""
        self.total_config_updates += 1

    def record_error(self, skill_id: str, error: str):
        """Record error."""
        self.total_errors += 1
        self.skill_error_count[skill_id] += 1
        self.last_error_at = datetime.utcnow().isoformat() + "Z"
        logger.error(f"Feedback processing error [{skill_id}]: {error}")

    def set_queue_size(self, size: int):
        """Update queue size gauge."""
        self.queue_size = size

    def get_latency_p99(self) -> float:
        """Calculate p99 latency from recent samples (ms)."""
        if not self.latency_samples:
            return 0.0
        latencies = sorted([s.duration_ms for s in self.latency_samples])
        idx = int(len(latencies) * 0.99)
        if idx >= len(latencies):
            idx = len(latencies) - 1
        return latencies[idx]

    def get_latency_p50(self) -> float:
        """Calculate p50 (median) latency (ms)."""
        if not self.latency_samples:
            return 0.0
        latencies = sorted([s.duration_ms for s in self.latency_samples])
        idx = len(latencies) // 2
        return latencies[idx]

    def get_latency_p95(self) -> float:
        """Calculate p95 latency (ms)."""
        if not self.latency_samples:
            return 0.0
        latencies = sorted([s.duration_ms for s in self.latency_samples])
        idx = int(len(latencies) * 0.95)
        if idx >= len(latencies):
            idx = len(latencies) - 1
        return latencies[idx]

    def get_error_rate(self) -> float:
        """Calculate error rate (0.0–1.0)."""
        total = self.total_feedback_processed + self.total_errors
        if total == 0:
            return 0.0
        return self.total_errors / total

    def to_dict(self) -> dict:
        """Export metrics as dict (for Prometheus or console)."""
        return {
            "total_feedback_processed": self.total_feedback_processed,
            "total_config_updates": self.total_config_updates,
            "total_errors": self.total_errors,
            "error_rate": self.get_error_rate(),
            "queue_size": self.queue_size,
            "queue_max_size": self.queue_max_size,
            "latency_p50_ms": self.get_latency_p50(),
            "latency_p95_ms": self.get_latency_p95(),
            "latency_p99_ms": self.get_latency_p99(),
            "skill_feedback_count": dict(self.skill_feedback_count),
            "skill_error_count": dict(self.skill_error_count),
            "last_feedback_at": self.last_feedback_at,
            "last_error_at": self.last_error_at,
        }


class HealthCheck:
    """Health check for feedback system."""

    def __init__(self, metrics: FeedbackMetrics):
        """
        Initialize health check.

        Args:
            metrics: FeedbackMetrics instance to monitor
        """
        self.metrics = metrics

    def is_healthy(self) -> bool:
        """
        Check if feedback system is healthy.

        Healthy iff:
        - Queue depth < 80% of max
        - Error rate < 1%
        - p99 latency < 100ms

        Returns:
            True if healthy, False if degraded
        """
        queue_utilization = self.metrics.queue_size / max(self.metrics.queue_max_size, 1)
        error_rate = self.metrics.get_error_rate()
        p99_latency = self.metrics.get_latency_p99()

        is_queue_ok = queue_utilization < 0.8
        is_error_rate_ok = error_rate < 0.01  # < 1%
        is_latency_ok = p99_latency < 100.0  # < 100ms

        return is_queue_ok and is_error_rate_ok and is_latency_ok

    def get_status(self) -> dict:
        """
        Get detailed health status.

        Returns:
            {
                "status": "healthy" | "degraded",
                "queue_utilization": 0.0–1.0,
                "error_rate": 0.0–1.0,
                "latency_p99_ms": float,
                "checks": {
                    "queue": "ok" | "warning",
                    "errors": "ok" | "warning",
                    "latency": "ok" | "warning",
                }
            }
        """
        queue_utilization = self.metrics.queue_size / max(self.metrics.queue_max_size, 1)
        error_rate = self.metrics.get_error_rate()
        p99_latency = self.metrics.get_latency_p99()

        queue_status = "ok" if queue_utilization < 0.8 else "warning"
        error_status = "ok" if error_rate < 0.01 else "warning"
        latency_status = "ok" if p99_latency < 100.0 else "warning"

        return {
            "status": "healthy" if self.is_healthy() else "degraded",
            "queue_utilization": queue_utilization,
            "queue_depth": self.metrics.queue_size,
            "queue_max": self.metrics.queue_max_size,
            "error_rate": error_rate,
            "latency_p99_ms": p99_latency,
            "checks": {
                "queue": queue_status,
                "errors": error_status,
                "latency": latency_status,
            },
        }


class AlertManager:
    """Alert rules and escalation for feedback system."""

    # Alert thresholds (configurable)
    ALERT_LATENCY_MS = 100.0  # p99 > 100ms
    ALERT_ERROR_RATE = 0.01   # > 1%
    ALERT_QUEUE_UTILIZATION = 0.8  # > 80%

    def __init__(self, metrics: FeedbackMetrics):
        """
        Initialize alert manager.

        Args:
            metrics: FeedbackMetrics instance
        """
        self.metrics = metrics
        self.active_alerts: List[str] = []

    def check_alerts(self) -> List[dict]:
        """
        Check all alert conditions.

        Returns:
            List of active alerts: [
                {
                    "alert": "high_latency",
                    "severity": "warning" | "critical",
                    "message": "p99 latency 125ms > 100ms threshold",
                    "value": 125.0,
                    "threshold": 100.0,
                    "timestamp": "2026-09-22T...",
                }
            ]
        """
        alerts = []

        # Alert: High latency
        p99_latency = self.metrics.get_latency_p99()
        if p99_latency > self.ALERT_LATENCY_MS:
            alerts.append({
                "alert": "high_latency",
                "severity": "warning",
                "message": f"p99 latency {p99_latency:.1f}ms > {self.ALERT_LATENCY_MS}ms threshold",
                "value": p99_latency,
                "threshold": self.ALERT_LATENCY_MS,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })

        # Alert: High error rate
        error_rate = self.metrics.get_error_rate()
        if error_rate > self.ALERT_ERROR_RATE:
            alerts.append({
                "alert": "high_error_rate",
                "severity": "critical",
                "message": f"Error rate {error_rate*100:.1f}% > {self.ALERT_ERROR_RATE*100}% threshold",
                "value": error_rate,
                "threshold": self.ALERT_ERROR_RATE,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })

        # Alert: Queue congestion
        queue_utilization = self.metrics.queue_size / max(self.metrics.queue_max_size, 1)
        if queue_utilization > self.ALERT_QUEUE_UTILIZATION:
            alerts.append({
                "alert": "queue_congestion",
                "severity": "warning",
                "message": f"Queue {queue_utilization*100:.1f}% full ({self.metrics.queue_size}/{self.metrics.queue_max_size} items)",
                "value": queue_utilization,
                "threshold": self.ALERT_QUEUE_UTILIZATION,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })

        self.active_alerts = [a["alert"] for a in alerts]
        return alerts
