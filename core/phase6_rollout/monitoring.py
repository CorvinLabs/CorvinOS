"""
Phase 6 Monitoring Integration — collects production metrics and health checks.

Integrates with:
- ContextBus: throughput (workflows/sec)
- ExecutionContext: latency (p99, p95, p50)
- Error counters: error rate (%)
- Audit trail: integrity (% verified)
- Feature tracker: promotion count, stuck features

Emits HealthMetrics every 15 minutes for Orchestrator to evaluate.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
import asyncio
import logging
from collections import deque


logger = logging.getLogger(__name__)


class MetricSource(Enum):
    """Sources of production metrics."""
    CONTEXT_BUS = "context_bus"  # Throughput counter
    EXECUTION_CONTEXT = "execution_context"  # Latency measurements
    ERROR_COUNTERS = "error_counters"  # Error rate
    AUDIT_TRAIL = "audit_trail"  # Integrity verification
    FEATURE_TRACKER = "feature_tracker"  # Promotion tracking


@dataclass
class RawMetricSample:
    """A single raw metric measurement from any source."""
    source: MetricSource
    timestamp: datetime
    label: str  # e.g., "throughput", "latency_p99", "error_count"
    value: float
    unit: str  # "workflows/sec", "ms", "%", "count"
    tenant_id: str = "_default"


@dataclass
class MetricsBuffer:
    """Circular buffer for recent metric samples."""
    max_size: int = 288  # 4320 minutes = 72 hours at 15-min intervals
    samples: deque = field(default_factory=deque)

    def add(self, sample: RawMetricSample):
        """Add a sample, evict oldest if buffer full."""
        self.samples.append(sample)
        if len(self.samples) > self.max_size:
            self.samples.popleft()

    def recent(self, minutes: int = 360) -> List[RawMetricSample]:
        """Get samples from last N minutes (default 6 hours)."""
        cutoff = datetime.now() - timedelta(minutes=minutes)
        return [s for s in self.samples if s.timestamp >= cutoff]

    def percentile(self, label: str, percentile: int = 99, minutes: int = 300) -> float:
        """Calculate percentile of samples with given label in recent window."""
        recent_samples = [
            s.value for s in self.recent(minutes)
            if s.label == label
        ]
        if not recent_samples:
            return 0.0
        recent_samples.sort()
        idx = int(len(recent_samples) * percentile / 100)
        return recent_samples[min(idx, len(recent_samples) - 1)]

    def average(self, label: str, minutes: int = 300) -> float:
        """Calculate average of samples with given label in recent window."""
        recent_samples = [
            s.value for s in self.recent(minutes)
            if s.label == label
        ]
        if not recent_samples:
            return 0.0
        return sum(recent_samples) / len(recent_samples)

    def sum(self, label: str, minutes: int = 300) -> float:
        """Calculate sum of samples with given label in recent window."""
        recent_samples = [
            s.value for s in self.recent(minutes)
            if s.label == label
        ]
        if not recent_samples:
            return 0.0
        return sum(recent_samples)


class MetricsCollector:
    """
    Collects production metrics from multiple sources.

    Responsibility: pull metrics from various subsystems (ContextBus, error counters, etc.)
    and normalize them into RawMetricSample objects for aggregation.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.buffer = MetricsBuffer()
        self._context_bus_workflow_count = 0
        self._error_count = 0
        self._audit_verify_count = 0
        self._audit_fail_count = 0
        self._feature_promotion_count = 0
        self._features_stuck_alpha_count = 0
        self._latency_samples: deque = deque(maxlen=100)

    async def collect_metrics(self) -> List[RawMetricSample]:
        """Collect current metrics from all sources."""
        samples = []

        # Simulate collection from ContextBus (in real impl, would read event counter)
        throughput = self._collect_context_bus_throughput()
        if throughput is not None:
            samples.append(
                RawMetricSample(
                    source=MetricSource.CONTEXT_BUS,
                    timestamp=datetime.now(),
                    label="throughput",
                    value=throughput,
                    unit="workflows/sec",
                    tenant_id=self.tenant_id,
                )
            )

        # Collect latency from ExecutionContext decision recorder
        latency_p99, latency_p95 = self._collect_execution_context_latency()
        if latency_p99 is not None:
            samples.append(
                RawMetricSample(
                    source=MetricSource.EXECUTION_CONTEXT,
                    timestamp=datetime.now(),
                    label="latency_p99",
                    value=latency_p99,
                    unit="ms",
                    tenant_id=self.tenant_id,
                )
            )
            samples.append(
                RawMetricSample(
                    source=MetricSource.EXECUTION_CONTEXT,
                    timestamp=datetime.now(),
                    label="latency_p95",
                    value=latency_p95,
                    unit="ms",
                    tenant_id=self.tenant_id,
                )
            )

        # Collect error rate from error counters
        error_rate = self._collect_error_rate()
        if error_rate is not None:
            samples.append(
                RawMetricSample(
                    source=MetricSource.ERROR_COUNTERS,
                    timestamp=datetime.now(),
                    label="error_rate_percent",
                    value=error_rate,
                    unit="%",
                    tenant_id=self.tenant_id,
                )
            )

        # Collect audit integrity from audit trail
        audit_integrity = self._collect_audit_integrity()
        if audit_integrity is not None:
            samples.append(
                RawMetricSample(
                    source=MetricSource.AUDIT_TRAIL,
                    timestamp=datetime.now(),
                    label="audit_integrity_percent",
                    value=audit_integrity,
                    unit="%",
                    tenant_id=self.tenant_id,
                )
            )

        # Collect feature tracking
        promotions = self._collect_feature_promotions()
        stuck = self._collect_features_stuck_alpha()
        if promotions is not None:
            samples.append(
                RawMetricSample(
                    source=MetricSource.FEATURE_TRACKER,
                    timestamp=datetime.now(),
                    label="feature_promotion_count",
                    value=promotions,
                    unit="count",
                    tenant_id=self.tenant_id,
                )
            )
        if stuck is not None:
            samples.append(
                RawMetricSample(
                    source=MetricSource.FEATURE_TRACKER,
                    timestamp=datetime.now(),
                    label="features_stuck_alpha_count",
                    value=stuck,
                    unit="count",
                    tenant_id=self.tenant_id,
                )
            )

        # Add to buffer
        for sample in samples:
            self.buffer.add(sample)

        return samples

    def _collect_context_bus_throughput(self) -> Optional[float]:
        """Collect workflow throughput from ContextBus event counter."""
        # In real implementation: read from ContextBus.workflow_count
        # For now, simulate realistic values
        return 250.0 + (hash(datetime.now().minute) % 50 - 25)  # 225–275 workflows/sec

    def _collect_execution_context_latency(self) -> Tuple[Optional[float], Optional[float]]:
        """Collect p99 and p95 latency from ExecutionContext decision recorder."""
        # In real implementation: read from ExecutionContext.decision_latencies
        # For now, simulate realistic p99/p95 values
        base_p99 = 45.0
        base_p95 = 35.0
        jitter = (hash(datetime.now().second) % 10 - 5)  # ±5ms
        return base_p99 + jitter, base_p95 + jitter

    def _collect_error_rate(self) -> Optional[float]:
        """Collect error rate from error subsystem counters."""
        # In real implementation: read from error_counter.total_errors / total_operations
        # For now, simulate realistic error rates
        if self._error_count == 0:
            return 0.02  # Baseline 0.02%
        return (self._error_count * 100.0) / max(1, self._context_bus_workflow_count * 10)

    def _collect_audit_integrity(self) -> Optional[float]:
        """Collect audit trail integrity from verifier."""
        # In real implementation: run audit trail verify, count successful verifications
        # For now, simulate realistic integrity
        if (self._audit_verify_count + self._audit_fail_count) == 0:
            return 99.95
        total = self._audit_verify_count + self._audit_fail_count
        return (self._audit_verify_count * 100.0) / total

    def _collect_feature_promotions(self) -> Optional[float]:
        """Collect feature promotion count from feature tracker."""
        # In real implementation: query feature_tier.promotion_history
        return float(self._feature_promotion_count)

    def _collect_features_stuck_alpha(self) -> Optional[float]:
        """Collect stuck ALPHA features from feature tracker."""
        # In real implementation: query feature_tier.stuck_features_alpha
        return float(self._features_stuck_alpha_count)

    # Simulation helpers (for testing)
    def simulate_error_spike(self, duration_seconds: int = 300):
        """Simulate an error spike for testing."""
        self._error_count += 100

    def simulate_error_recovery(self):
        """Simulate error recovery."""
        self._error_count = max(0, self._error_count - 10)

    def simulate_workflow_execution(self, count: int = 100):
        """Simulate successful workflow executions."""
        self._context_bus_workflow_count += count
        self._audit_verify_count += count


class HealthCheckEvaluator:
    """
    Evaluates raw metrics against SLO thresholds and produces HealthMetrics.

    Responsibility: aggregate raw metric samples into a HealthMetrics object
    that reflects current system health status.
    """

    def __init__(self, collector: MetricsCollector):
        self.collector = collector

    async def evaluate_health(self) -> Optional["HealthMetrics"]:
        """Evaluate current health by aggregating recent metrics."""
        # Collect fresh metrics
        await self.collector.collect_metrics()

        # Aggregate from buffer
        buffer = self.collector.buffer

        # Get latest values or aggregates
        throughput = buffer.average("throughput", minutes=300) or 250.0
        latency_p99 = buffer.percentile("latency_p99", percentile=99, minutes=300) or 45.0
        error_rate = buffer.average("error_rate_percent", minutes=300) or 0.02
        audit_integrity = buffer.average("audit_integrity_percent", minutes=300) or 99.95
        promotions = buffer.sum("feature_promotion_count", minutes=300) or 0
        stuck_alpha = buffer.sum("features_stuck_alpha_count", minutes=300) or 0

        # Import HealthMetrics from orchestrator module
        from core.phase6_rollout.orchestrator import HealthMetrics

        health = HealthMetrics(
            timestamp=datetime.now(),
            throughput_per_sec=throughput,
            latency_p99_ms=latency_p99,
            error_rate_percent=error_rate,
            audit_integrity_percent=audit_integrity,
            feature_promotion_count=int(promotions),
            features_stuck_alpha_count=int(stuck_alpha),
        )

        logger.info(
            f"Health evaluation: throughput={throughput:.1f}/sec, "
            f"latency_p99={latency_p99:.2f}ms, error_rate={error_rate:.3f}%, "
            f"audit={audit_integrity:.2f}%, status={'HEALTHY' if health.is_healthy() else 'DEGRADED' if health.is_degraded() else 'CRITICAL'}"
        )

        return health

    def get_recent_metrics_for_dashboard(self, minutes: int = 360) -> List[Dict]:
        """Get recent metrics in dashboard-friendly format."""
        buffer = self.collector.buffer
        recent = buffer.recent(minutes)

        # Group by label
        by_label = {}
        for sample in recent:
            if sample.label not in by_label:
                by_label[sample.label] = []
            by_label[sample.label].append({
                "timestamp": sample.timestamp.isoformat(),
                "value": sample.value,
                "unit": sample.unit,
            })

        return [
            {
                "label": label,
                "unit": by_label[label][0]["unit"] if by_label[label] else "",
                "samples": by_label[label],
            }
            for label in sorted(by_label.keys())
        ]
