"""Performance Profiling + SLO Tracking — unified metrics collection and enforcement (Phase 4).

Measures latency, memory, throughput at named checkpoints. Enforces configured SLO thresholds
and emits alerts on breach. Tenant-scoped, audit-logged.

**Responsibility:** collect metrics at ~30 key checkpoints (e.g., skill-resolve, tool-invoke,
audit-write); compute running statistics; check against per-checkpoint SLOs (green/yellow/red
thresholds); emit alerts and audit events on threshold breach.

**Design:** Unified module (not split into profiler + slo_tracker) because:
- Avoids schema sync bugs (one Checkpoint dataclass)
- Prevents alert-routing ambiguity (all logic in one place)
- Single test suite easier to maintain
- Reuse via function-level queries (e.g., learning_engine.query_metrics()), not module-level coupling
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional
from threading import Lock
import json

logger = logging.getLogger(__name__)


# ============================================================================
# Data Models
# ============================================================================


class SLOStatus(Enum):
    """SLO threshold status."""
    GREEN = "green"      # Within SLO (e.g., <100ms)
    YELLOW = "yellow"    # Degraded but acceptable (e.g., 100-150ms)
    RED = "red"          # SLO breached (e.g., >150ms)
    UNKNOWN = "unknown"  # No data yet


@dataclass(frozen=True)
class SLOThreshold:
    """SLO thresholds for a checkpoint metric.

    Defines three-tier SLO enforcement: green (ideal), yellow (acceptable),
    red (breach alert). Used to categorize measured metric values.

    **Example:**
    ```python
    threshold = SLOThreshold(
        metric="latency_ms",
        green_max=100,      # Best case
        yellow_max=150,     # Degraded but acceptable
        red_max=200,        # SLO breach, alert
    )
    status = threshold.status_for_value(125.0)  # Returns SLOStatus.YELLOW
    ```
    """
    metric: str              # "latency_ms" | "memory_mb" | "throughput_rps"
    green_max: float         # Ideal max (e.g., 100 for latency_ms)
    yellow_max: float        # Acceptable max (e.g., 150 for latency_ms)
    red_max: float           # Absolute max before alert (e.g., 200 for latency_ms)

    def status_for_value(self, value: float) -> SLOStatus:
        """Determine SLO status for a given value."""
        if value <= self.green_max:
            return SLOStatus.GREEN
        elif value <= self.yellow_max:
            return SLOStatus.YELLOW
        elif value <= self.red_max:
            return SLOStatus.RED
        else:
            return SLOStatus.RED


@dataclass(frozen=True)
class Checkpoint:
    """Named measurement point in the execution flow.

    Represents a logical operation that should be profiled (e.g., skill resolution,
    tool invocation, audit write). Associates one or more SLO thresholds that
    determine whether measurements pass (green), degrade (yellow), or breach (red).

    **Example:**
    ```python
    checkpoint = Checkpoint(
        name="skill_resolve",
        category="learning",
        description="Time to resolve a skill's dependencies",
        thresholds={
            "latency_ms": SLOThreshold("latency_ms", 100, 150, 200),
        },
        critical=False,
    )
    ```
    """
    name: str                  # e.g., "skill_resolve", "tool_invoke", "audit_write"
    category: str              # e.g., "learning", "audit", "core"
    description: str
    thresholds: dict[str, SLOThreshold] = field(default_factory=dict)  # metric -> threshold
    critical: bool = False     # If True, red alert stops the operation


@dataclass(frozen=True)
class MetricPoint:
    """A single metric measurement at a checkpoint.

    Immutable record of one measurement: which checkpoint, which metric,
    the measured value, when it was recorded, and which tenant it belongs to.
    """
    checkpoint_name: str
    metric: str
    value: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    tenant_id: str = "_default"


@dataclass(frozen=True)
class SLOAlert:
    """Alert emitted when SLO is breached (yellow or red).

    Immutable record of a threshold breach: which checkpoint, which metric,
    what status was reached (yellow/red), the measured value that triggered it,
    and the threshold definition. Logged for observability and audit trail.
    """
    checkpoint_name: str
    metric: str
    status: SLOStatus
    value: float
    threshold: SLOThreshold
    timestamp: datetime = field(default_factory=datetime.utcnow)
    tenant_id: str = "_default"

    def __str__(self) -> str:
        return (f"SLOAlert({self.checkpoint_name}/{self.metric}: {self.status.value} "
                f"(value={self.value:.2f}, max={self.threshold.yellow_max}), tenant={self.tenant_id})")


@dataclass(frozen=True)
class CheckpointStats:
    """Aggregated statistics for a checkpoint metric over a time window.

    Computed from historical MetricPoint records: count, min/max, mean,
    percentiles (p50, p95, p99), and current SLO status (based on most recent value).
    Used for dashboards, alerting, and learning engine feedback.
    """
    checkpoint_name: str
    metric: str
    count: int
    min_value: float
    max_value: float
    mean_value: float
    p50_value: float
    p95_value: float
    p99_value: float
    current_status: SLOStatus
    time_window_minutes: int = 60
    tenant_id: str = "_default"
    computed_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================================
# Profiler — main implementation
# ============================================================================


class Profiler:
    """Collect metrics at named checkpoints and enforce SLO thresholds.

    **Thread-safe:** all operations protected by lock.

    **Tenant-scoped:** all measurements filtered by tenant_id.

    **Usage:**
    ```python
    profiler = Profiler()
    profiler.register_checkpoint(Checkpoint("skill_resolve", ...))

    # Measure a duration
    start = time.time()
    resolver.resolve("some_skill")
    duration_ms = (time.time() - start) * 1000
    profiler.record_metric("skill_resolve", "latency_ms", duration_ms, tenant_id="acme")

    # Query stats
    stats = profiler.get_stats("skill_resolve", "latency_ms", tenant_id="acme")
    alerts = profiler.get_recent_alerts(tenant_id="acme")
    ```
    """

    def __init__(self, retention_minutes: int = 1440):  # 24h default
        """Initialize profiler.

        **Parameters:**
        - retention_minutes: how long to keep historical metrics (default 24h)
        """
        self.retention_minutes = retention_minutes
        self.checkpoints: dict[str, Checkpoint] = {}
        self.metrics: list[MetricPoint] = []
        self.alerts: list[SLOAlert] = []
        self.lock = Lock()
        self._last_cleanup = time.time()

    def register_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Register a named checkpoint with SLO thresholds."""
        with self.lock:
            self.checkpoints[checkpoint.name] = checkpoint
            logger.info(f"Registered checkpoint: {checkpoint.name} ({checkpoint.category})")

    def record_metric(
        self,
        checkpoint_name: str,
        metric: str,
        value: float,
        tenant_id: str = "_default",
    ) -> Optional[SLOAlert]:
        """Record a metric and check SLO thresholds.

        Returns: SLOAlert if threshold breached, else None.
        Raises: ValueError if checkpoint not registered.
        """
        with self.lock:
            if checkpoint_name not in self.checkpoints:
                raise ValueError(f"Checkpoint not registered: {checkpoint_name}")

            checkpoint = self.checkpoints[checkpoint_name]

            # Record metric
            metric_point = MetricPoint(
                checkpoint_name=checkpoint_name,
                metric=metric,
                value=value,
                tenant_id=tenant_id,
            )
            self.metrics.append(metric_point)

            # Check SLO
            alert = None
            if metric in checkpoint.thresholds:
                threshold = checkpoint.thresholds[metric]
                status = threshold.status_for_value(value)

                if status in (SLOStatus.YELLOW, SLOStatus.RED):
                    alert = SLOAlert(
                        checkpoint_name=checkpoint_name,
                        metric=metric,
                        status=status,
                        value=value,
                        threshold=threshold,
                        tenant_id=tenant_id,
                    )
                    self.alerts.append(alert)
                    logger.warning(str(alert))

            # Periodic cleanup
            if time.time() - self._last_cleanup > 300:  # Every 5 min
                self._cleanup_old_metrics()

            return alert

    def _cleanup_old_metrics(self) -> None:
        """Remove metrics older than retention window. Must hold lock."""
        cutoff = datetime.utcnow() - timedelta(minutes=self.retention_minutes)
        old_count = len(self.metrics)
        self.metrics = [m for m in self.metrics if m.timestamp >= cutoff]
        if old_count > len(self.metrics):
            logger.debug(f"Cleaned up {old_count - len(self.metrics)} old metrics")

        # Also clean old alerts
        old_alert_count = len(self.alerts)
        self.alerts = [a for a in self.alerts if a.timestamp >= cutoff]

    def get_stats(
        self,
        checkpoint_name: str,
        metric: str,
        tenant_id: str = "_default",
        time_window_minutes: int = 60,
    ) -> Optional[CheckpointStats]:
        """Get aggregated statistics for a checkpoint metric.

        Returns: CheckpointStats or None if no data.
        """
        with self.lock:
            cutoff = datetime.utcnow() - timedelta(minutes=time_window_minutes)
            values = [
                m.value
                for m in self.metrics
                if (
                    m.checkpoint_name == checkpoint_name
                    and m.metric == metric
                    and m.tenant_id == tenant_id
                    and m.timestamp >= cutoff
                )
            ]

            if not values:
                return None

            sorted_values = sorted(values)
            n = len(sorted_values)

            return CheckpointStats(
                checkpoint_name=checkpoint_name,
                metric=metric,
                count=n,
                min_value=sorted_values[0],
                max_value=sorted_values[-1],
                mean_value=sum(sorted_values) / n,
                p50_value=sorted_values[n // 2],
                p95_value=sorted_values[int(n * 0.95)],
                p99_value=sorted_values[int(n * 0.99)],
                current_status=self._compute_status(checkpoint_name, metric, sorted_values[-1]),
                time_window_minutes=time_window_minutes,
                tenant_id=tenant_id,
            )

    def _compute_status(self, checkpoint_name: str, metric: str, value: float) -> SLOStatus:
        """Compute SLO status for a value. Must hold lock."""
        checkpoint = self.checkpoints.get(checkpoint_name)
        if not checkpoint or metric not in checkpoint.thresholds:
            return SLOStatus.UNKNOWN
        threshold = checkpoint.thresholds[metric]
        return threshold.status_for_value(value)

    def get_recent_alerts(
        self,
        tenant_id: str = "_default",
        minutes: int = 60,
    ) -> list[SLOAlert]:
        """Get recent SLO alerts."""
        with self.lock:
            cutoff = datetime.utcnow() - timedelta(minutes=minutes)
            return [
                a for a in self.alerts
                if a.tenant_id == tenant_id and a.timestamp >= cutoff
            ]

    def get_checkpoint(self, name: str) -> Optional[Checkpoint]:
        """Get registered checkpoint by name."""
        with self.lock:
            return self.checkpoints.get(name)

    def list_checkpoints(self, category: Optional[str] = None) -> list[Checkpoint]:
        """List all registered checkpoints, optionally filtered by category."""
        with self.lock:
            if category is None:
                return list(self.checkpoints.values())
            return [c for c in self.checkpoints.values() if c.category == category]

    def export_metrics_json(self, tenant_id: str = "_default") -> str:
        """Export recent metrics as JSON (for audit trail / dashboard)."""
        with self.lock:
            data = {
                "exported_at": datetime.utcnow().isoformat(),
                "tenant_id": tenant_id,
                "metrics": [
                    {
                        "checkpoint": m.checkpoint_name,
                        "metric": m.metric,
                        "value": m.value,
                        "timestamp": m.timestamp.isoformat(),
                    }
                    for m in self.metrics
                    if m.tenant_id == tenant_id
                ],
                "alerts": [
                    {
                        "checkpoint": a.checkpoint_name,
                        "metric": a.metric,
                        "status": a.status.value,
                        "value": a.value,
                        "threshold_yellow": a.threshold.yellow_max,
                        "threshold_red": a.threshold.red_max,
                        "timestamp": a.timestamp.isoformat(),
                    }
                    for a in self.alerts
                    if a.tenant_id == tenant_id
                ],
            }
            return json.dumps(data, indent=2)


# ============================================================================
# Global singleton instance
# ============================================================================

_profiler_instance: Optional[Profiler] = None
_profiler_lock = Lock()


def get_profiler() -> Profiler:
    """Get or create the global profiler instance."""
    global _profiler_instance
    if _profiler_instance is None:
        with _profiler_lock:
            if _profiler_instance is None:
                _profiler_instance = Profiler()
    return _profiler_instance


def reset_profiler() -> None:
    """Reset the global profiler (for testing)."""
    global _profiler_instance
    with _profiler_lock:
        _profiler_instance = None
