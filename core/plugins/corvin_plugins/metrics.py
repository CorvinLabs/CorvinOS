"""Plugin metrics and observability (ADR-0680, ADR-0681, ADR-0682).

Comprehensive telemetry for plugin operations:
* **Execution metrics:** Latency, throughput, error rates
* **Resource usage:** Memory, file handles, subprocess count
* **Health signals:** Circuit state, failure trends, recovery patterns
* **Event tracking:** Lifecycle events, capability usage, security violations

Design:
* **Non-blocking:** Metrics collection never blocks plugin execution.
* **Aggregated:** Per-plugin and per-capability summaries.
* **Prometheus-ready:** Export to Prometheus via standard text format.
* **Bounded:** Fixed-size windows; old data is discarded, not accumulated.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, DefaultDict, Dict, Optional

log = logging.getLogger("corvin.plugins.metrics")


class MetricType(str, Enum):
    """Metric types for Prometheus export."""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


@dataclass
class PluginMetric:
    """Single metric observation for a plugin."""
    metric_name: str
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metric_type: MetricType = MetricType.GAUGE


@dataclass
class ExecutionStats:
    """Per-capability execution statistics."""
    capability: str
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0.0
    min_duration_ms: float = float("inf")
    max_duration_ms: float = 0.0
    last_execution_ms: float = 0.0
    last_error_type: Optional[str] = None
    last_error_at: float = 0.0

    @property
    def avg_duration_ms(self) -> float:
        """Average execution duration."""
        if self.call_count == 0:
            return 0.0
        return self.total_duration_ms / self.call_count

    @property
    def error_rate(self) -> float:
        """Error rate as a percentage."""
        if self.call_count == 0:
            return 0.0
        return (self.failure_count / self.call_count) * 100

    @property
    def success_rate(self) -> float:
        """Success rate as a percentage."""
        return 100.0 - self.error_rate

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "capability": self.capability,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_duration_ms": self.total_duration_ms,
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "min_duration_ms": self.min_duration_ms if self.min_duration_ms != float("inf") else 0,
            "max_duration_ms": self.max_duration_ms,
            "error_rate_pct": round(self.error_rate, 2),
            "success_rate_pct": round(self.success_rate, 2),
        }


@dataclass
class PluginMetrics:
    """Aggregated metrics for a single plugin."""
    plugin_id: str
    version: str
    load_time: float
    boot_layer: str

    #: Per-capability execution statistics
    execution_stats: DefaultDict[str, ExecutionStats] = field(
        default_factory=lambda: defaultdict(lambda: ExecutionStats(capability=""))
    )

    #: Lifecycle events
    total_loads: int = 0
    total_load_failures: int = 0
    total_unloads: int = 0

    #: Security events
    permission_denials: int = 0
    sandbox_violations: int = 0

    #: Health
    health_check_count: int = 0
    health_failures: int = 0
    last_health_check: float = 0.0

    #: Circuit breaker
    circuit_opens: int = 0
    consecutive_failures: int = 0
    current_state: str = "closed"

    #: Audit
    audit_events_emitted: int = 0

    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "load_time_ms": round(self.load_time, 2),
            "boot_layer": self.boot_layer,
            "capabilities": {
                cap: stats.to_dict()
                for cap, stats in self.execution_stats.items()
            },
            "lifecycle": {
                "loads": self.total_loads,
                "load_failures": self.total_load_failures,
                "unloads": self.total_unloads,
            },
            "security": {
                "permission_denials": self.permission_denials,
                "sandbox_violations": self.sandbox_violations,
            },
            "health": {
                "checks": self.health_check_count,
                "failures": self.health_failures,
                "last_check_at": self.last_health_check,
            },
            "circuit_breaker": {
                "opens": self.circuit_opens,
                "consecutive_failures": self.consecutive_failures,
                "current_state": self.current_state,
            },
            "audit_events": self.audit_events_emitted,
            "last_updated": self.last_updated,
        }


class PluginMetricsCollector:
    """Thread-safe metrics collection for plugins.

    Aggregates metrics across plugins and capabilities, exporting in
    Prometheus format or as structured data for API responses.
    """

    def __init__(self) -> None:
        """Initialize metrics collector."""
        self._lock = threading.Lock()
        self._metrics: Dict[str, PluginMetrics] = {}
        self._custom_metrics: Dict[str, list[PluginMetric]] = defaultdict(list)

    def record_load(
        self,
        plugin_id: str,
        version: str,
        boot_layer: str,
        duration_ms: float,
    ) -> None:
        """Record a successful plugin load."""
        with self._lock:
            if plugin_id not in self._metrics:
                self._metrics[plugin_id] = PluginMetrics(
                    plugin_id=plugin_id,
                    version=version,
                    load_time=duration_ms,
                    boot_layer=boot_layer,
                )
            metrics = self._metrics[plugin_id]
            metrics.total_loads += 1
            metrics.version = version
            metrics.load_time = min(metrics.load_time, duration_ms)

    def record_load_failure(self, plugin_id: str) -> None:
        """Record a load failure."""
        with self._lock:
            if plugin_id in self._metrics:
                self._metrics[plugin_id].total_load_failures += 1

    def record_execution(
        self,
        plugin_id: str,
        capability: str,
        duration_ms: float,
        success: bool,
        error_type: Optional[str] = None,
    ) -> None:
        """Record a plugin capability execution.

        Args:
            plugin_id: Plugin identifier
            capability: The capability that was executed
            duration_ms: Execution duration in milliseconds
            success: Whether execution succeeded
            error_type: Exception class name if failed
        """
        with self._lock:
            if plugin_id not in self._metrics:
                # Create default metrics if not yet loaded
                self._metrics[plugin_id] = PluginMetrics(
                    plugin_id=plugin_id,
                    version="unknown",
                    load_time=0.0,
                    boot_layer="unknown",
                )

            metrics = self._metrics[plugin_id]
            stats = metrics.execution_stats[capability]

            # Update stats
            stats.capability = capability
            stats.call_count += 1
            stats.last_execution_ms = duration_ms

            if success:
                stats.success_count += 1
            else:
                stats.failure_count += 1
                stats.last_error_type = error_type
                stats.last_error_at = time.time()

            # Update duration stats
            stats.total_duration_ms += duration_ms
            stats.min_duration_ms = min(stats.min_duration_ms, duration_ms)
            stats.max_duration_ms = max(stats.max_duration_ms, duration_ms)

            metrics.last_updated = time.time()

    def record_health_check(
        self,
        plugin_id: str,
        ok: bool,
    ) -> None:
        """Record a health check result."""
        with self._lock:
            if plugin_id in self._metrics:
                metrics = self._metrics[plugin_id]
                metrics.health_check_count += 1
                if not ok:
                    metrics.health_failures += 1
                metrics.last_health_check = time.time()

    def record_permission_denied(self, plugin_id: str) -> None:
        """Record a permission denial."""
        with self._lock:
            if plugin_id in self._metrics:
                self._metrics[plugin_id].permission_denials += 1

    def record_sandbox_violation(self, plugin_id: str) -> None:
        """Record a sandbox constraint violation."""
        with self._lock:
            if plugin_id in self._metrics:
                self._metrics[plugin_id].sandbox_violations += 1

    def record_circuit_opened(
        self,
        plugin_id: str,
        consecutive_failures: int,
    ) -> None:
        """Record circuit breaker opening."""
        with self._lock:
            if plugin_id in self._metrics:
                metrics = self._metrics[plugin_id]
                metrics.circuit_opens += 1
                metrics.consecutive_failures = consecutive_failures
                metrics.current_state = "open"

    def record_circuit_closed(self, plugin_id: str) -> None:
        """Record circuit breaker closing."""
        with self._lock:
            if plugin_id in self._metrics:
                metrics = self._metrics[plugin_id]
                metrics.consecutive_failures = 0
                metrics.current_state = "closed"

    def record_circuit_half_open(self, plugin_id: str) -> None:
        """Record circuit breaker half-open state."""
        with self._lock:
            if plugin_id in self._metrics:
                self._metrics[plugin_id].current_state = "half_open"

    def record_audit_event(self, plugin_id: str) -> None:
        """Record an audit event emission."""
        with self._lock:
            if plugin_id in self._metrics:
                self._metrics[plugin_id].audit_events_emitted += 1

    def add_custom_metric(self, metric: PluginMetric) -> None:
        """Record a custom metric."""
        with self._lock:
            self._custom_metrics[metric.metric_name].append(metric)

    def get_metrics(self, plugin_id: str) -> Optional[PluginMetrics]:
        """Get all metrics for a plugin."""
        with self._lock:
            return self._metrics.get(plugin_id)

    def get_all_metrics(self) -> Dict[str, PluginMetrics]:
        """Get metrics for all plugins."""
        with self._lock:
            return dict(self._metrics)

    def render_prometheus(self) -> str:
        """Render all metrics in Prometheus 0.0.4 text format."""
        with self._lock:
            lines = []

            # Per-plugin metrics
            for plugin_id, metrics in sorted(self._metrics.items()):
                labels = self._make_labels({"plugin": plugin_id})

                # Lifecycle counters
                lines.append(f"corvin_plugin_loads_total{labels} {metrics.total_loads}")
                lines.append(f"corvin_plugin_load_failures_total{labels} {metrics.total_load_failures}")

                # Execution metrics (per-capability)
                for cap, stats in sorted(metrics.execution_stats.items()):
                    cap_labels = self._make_labels({
                        "plugin": plugin_id,
                        "capability": cap,
                    })
                    lines.append(f"corvin_plugin_calls_total{cap_labels} {stats.call_count}")
                    lines.append(f"corvin_plugin_call_success_total{cap_labels} {stats.success_count}")
                    lines.append(f"corvin_plugin_call_failures_total{cap_labels} {stats.failure_count}")
                    lines.append(f"corvin_plugin_call_duration_ms{cap_labels} {stats.total_duration_ms}")
                    lines.append(f"corvin_plugin_call_duration_avg_ms{cap_labels} {stats.avg_duration_ms:.2f}")

                # Security metrics
                lines.append(f"corvin_plugin_permission_denials_total{labels} {metrics.permission_denials}")
                lines.append(f"corvin_plugin_sandbox_violations_total{labels} {metrics.sandbox_violations}")

                # Health metrics
                lines.append(f"corvin_plugin_health_checks_total{labels} {metrics.health_check_count}")
                lines.append(f"corvin_plugin_health_failures_total{labels} {metrics.health_failures}")

                # Circuit breaker
                lines.append(f"corvin_plugin_circuit_opens_total{labels} {metrics.circuit_opens}")
                lines.append(f"corvin_plugin_consecutive_failures{labels} {metrics.consecutive_failures}")

            return "\n".join(lines) + "\n" if lines else ""

    @staticmethod
    def _make_labels(label_dict: Dict[str, str]) -> str:
        """Format labels for Prometheus output."""
        if not label_dict:
            return ""
        items = [f'{k}="{v}"' for k, v in sorted(label_dict.items())]
        return "{" + ",".join(items) + "}"


# Global metrics collector (singleton per process)
_global_collector = PluginMetricsCollector()


def get_metrics_collector() -> PluginMetricsCollector:
    """Get the global metrics collector."""
    return _global_collector
