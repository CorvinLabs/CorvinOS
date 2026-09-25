"""
Performance Monitoring (Phase 3d Stream D)
Measures latency, throughput, resource usage for Phase 5 readiness validation

Production SLAs:
- STT inference: P99 < 500ms
- DB queries: P99 < 100ms
- Type detection: P99 < 50ms
- Summary generation: P99 < 1s (LLM-bound)

@date 2026-09-25
@phase Phase 3d: Performance Baselines
"""

import time
import logging
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    """Performance metric types"""
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    CPU = "cpu"
    MEMORY = "memory"
    ERROR_RATE = "error_rate"


@dataclass
class PerformanceMetric:
    """Single performance measurement"""
    timestamp: str
    metric_type: str
    component: str  # "stt", "db", "type_detection", "summary"
    value: float
    unit: str  # "ms", "rps", "%", "bytes"
    p50: Optional[float] = None
    p99: Optional[float] = None

    def to_dict(self) -> Dict:
        return asdict(self)


class PerformanceMonitor:
    """
    Lightweight performance monitoring (Phase 3d MVP).
    Phase 3e: Full integration with observability stack (Prometheus, etc.)
    """

    def __init__(self):
        self._metrics: List[PerformanceMetric] = []
        self._component_timings: Dict[str, List[float]] = {}

    def record_latency(self, component: str, latency_ms: float):
        """Record latency for a component"""
        if component not in self._component_timings:
            self._component_timings[component] = []

        self._component_timings[component].append(latency_ms)

        metric = PerformanceMetric(
            timestamp=datetime.utcnow().isoformat(),
            metric_type=MetricType.LATENCY.value,
            component=component,
            value=latency_ms,
            unit="ms",
        )
        self._metrics.append(metric)

        logger.debug(f"Latency: {component} = {latency_ms:.2f}ms")

    def get_percentile(self, component: str, percentile: int = 99) -> Optional[float]:
        """Get latency percentile for component"""
        if component not in self._component_timings:
            return None

        timings = sorted(self._component_timings[component])
        idx = int(len(timings) * percentile / 100)
        return timings[idx] if idx < len(timings) else None

    def get_sla_status(self) -> Dict[str, Dict[str, bool]]:
        """
        Check SLA compliance (Phase 3d baseline).
        Returns: {component: {sla_name: is_compliant}}
        """
        slas = {
            "stt": {"p99_under_500ms": self.get_percentile("stt", 99) and self.get_percentile("stt", 99) < 500},
            "db": {"p99_under_100ms": self.get_percentile("db", 99) and self.get_percentile("db", 99) < 100},
            "type_detection": {"p99_under_50ms": self.get_percentile("type_detection", 99) and self.get_percentile("type_detection", 99) < 50},
            "summary": {"p99_under_1000ms": self.get_percentile("summary", 99) and self.get_percentile("summary", 99) < 1000},
        }
        return slas

    def get_summary(self) -> Dict:
        """Get performance summary"""
        summary = {}

        for component in self._component_timings.keys():
            timings = self._component_timings[component]
            if timings:
                summary[component] = {
                    "count": len(timings),
                    "min_ms": min(timings),
                    "max_ms": max(timings),
                    "avg_ms": sum(timings) / len(timings),
                    "p50_ms": self.get_percentile(component, 50),
                    "p99_ms": self.get_percentile(component, 99),
                }

        return summary

    def report_sla_status(self):
        """Log SLA compliance report"""
        slas = self.get_sla_status()
        summary = self.get_summary()

        logger.info("=== Performance SLA Status (Phase 5 Readiness) ===")

        for component, sla_checks in slas.items():
            if component in summary:
                stats = summary[component]
                logger.info(f"\n{component.upper()}:")
                logger.info(f"  Requests: {stats['count']}")
                logger.info(f"  Avg: {stats['avg_ms']:.2f}ms, P50: {stats['p50_ms']:.2f}ms, P99: {stats['p99_ms']:.2f}ms")

                for sla_name, is_compliant in sla_checks.items():
                    status = "✅ PASS" if is_compliant else "❌ FAIL"
                    logger.info(f"  {sla_name}: {status}")


# Singleton monitor
_monitor: Optional[PerformanceMonitor] = None


def get_performance_monitor() -> PerformanceMonitor:
    """Get or create performance monitor"""
    global _monitor
    if _monitor is None:
        _monitor = PerformanceMonitor()
    return _monitor


class PerformanceTimer:
    """Context manager for timing operations"""

    def __init__(self, component: str):
        self.component = component
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.time() - self.start_time) * 1000
        monitor = get_performance_monitor()
        monitor.record_latency(self.component, elapsed_ms)


# Convenience function
def measure_latency(component: str):
    """Measure latency for a component"""
    return PerformanceTimer(component)
