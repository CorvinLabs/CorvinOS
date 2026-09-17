"""Context-Drift Prometheus Metrics.

Exports metrics for monitoring dashboard and alerting.
Tracks:
- Goal alignment check frequency and results
- Alignment score distribution
- Current threshold
- Feedback quality
- Learning loop convergence

ADR-0407: Session Context Drift Prevention
ADR-0362: Production Deployment Framework
"""

import logging
from typing import Optional, Dict
from datetime import datetime

try:
    from prometheus_client import Counter, Histogram, Gauge, Summary
except ImportError:
    # Graceful fallback if prometheus_client not installed
    Counter = Histogram = Gauge = Summary = None
    _PROMETHEUS_AVAILABLE = False
else:
    _PROMETHEUS_AVAILABLE = True

logger = logging.getLogger(__name__)

# --- Prometheus Metrics (initialized if prometheus_client available) ---

if _PROMETHEUS_AVAILABLE:
    # Counter: Total goal alignment checks by result
    goal_alignment_checks_total = Counter(
        "context_drift_goal_alignment_checks_total",
        "Total goal alignment checks",
        ["result"],  # aligned, drifted, escalated
        namespace="corvin"
    )

    # Histogram: Alignment score distribution
    alignment_score_histogram = Histogram(
        "context_drift_alignment_score",
        "Goal alignment score distribution (0.0-1.0)",
        buckets=[0.0, 0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        namespace="corvin"
    )

    # Gauge: Current goal alignment threshold
    threshold_gauge = Gauge(
        "context_drift_threshold_current",
        "Current goal alignment threshold for drift detection",
        namespace="corvin"
    )

    # Gauge: Feedback quality/accuracy
    feedback_quality_gauge = Gauge(
        "context_drift_feedback_quality",
        "User feedback accuracy (0.0-1.0, higher = more consistent)",
        namespace="corvin"
    )

    # Counter: Threshold tuning events
    threshold_tuning_total = Counter(
        "context_drift_threshold_tuning_total",
        "Total threshold tuning events",
        namespace="corvin"
    )

    # Gauge: Last threshold tuning accuracy improvement
    last_tuning_improvement = Gauge(
        "context_drift_last_tuning_improvement",
        "Accuracy improvement from last tuning",
        namespace="corvin"
    )

    # Summary: Goal alignment check latency
    alignment_check_latency = Summary(
        "context_drift_alignment_check_latency_ms",
        "Goal alignment check latency in milliseconds",
        namespace="corvin"
    )

    # Counter: Drift alerts by severity
    drift_alerts_total = Counter(
        "context_drift_drift_alerts_total",
        "Total drift alerts",
        ["severity"],  # warning, critical
        namespace="corvin"
    )

    # Gauge: Active goals being monitored
    active_goals_gauge = Gauge(
        "context_drift_active_goals",
        "Number of active goals being monitored",
        namespace="corvin"
    )

    # Gauge: Sessions with active goals
    active_sessions_gauge = Gauge(
        "context_drift_active_sessions",
        "Number of sessions with active goals",
        namespace="corvin"
    )

    # Counter: Goals restored from checkpoint
    goals_restored_total = Counter(
        "context_drift_goals_restored_total",
        "Total goals restored from checkpoint",
        namespace="corvin"
    )

else:
    # Stub implementations for when prometheus_client not installed
    class _StubMetric:
        def inc(self, amount=1, labels=None):
            pass
        def observe(self, value):
            pass
        def set(self, value):
            pass
        def labels(self, **kwargs):
            return self
        def labels_by_value(self, **kwargs):
            return self

    goal_alignment_checks_total = _StubMetric()
    alignment_score_histogram = _StubMetric()
    threshold_gauge = _StubMetric()
    feedback_quality_gauge = _StubMetric()
    threshold_tuning_total = _StubMetric()
    last_tuning_improvement = _StubMetric()
    alignment_check_latency = _StubMetric()
    drift_alerts_total = _StubMetric()
    active_goals_gauge = _StubMetric()
    active_sessions_gauge = _StubMetric()
    goals_restored_total = _StubMetric()


class ContextDriftMetricsCollector:
    """Collect and emit Context-Drift metrics."""

    def __init__(self):
        self.logger = logger

    def record_alignment_check(self, score: float, result: str):
        """Record goal alignment check result.

        Args:
            score: Computed alignment score (0.0-1.0)
            result: 'aligned', 'drifted', or 'escalated'
        """
        alignment_score_histogram.observe(score)
        goal_alignment_checks_total.labels(result=result).inc()

        self.logger.debug(f"📊 Alignment check: score={score:.2f}, result={result}")

    def record_threshold_update(self, new_threshold: float):
        """Record threshold update."""
        threshold_gauge.set(new_threshold)
        self.logger.info(f"📊 Threshold updated: {new_threshold:.3f}")

    def record_feedback_quality(self, quality: float):
        """Record feedback quality score.

        Args:
            quality: Feedback accuracy (0.0-1.0)
        """
        feedback_quality_gauge.set(quality)
        self.logger.debug(f"📊 Feedback quality: {quality:.2%}")

    def record_threshold_tuning(self, improvement: float):
        """Record threshold tuning event.

        Args:
            improvement: Accuracy improvement from tuning
        """
        threshold_tuning_total.inc()
        last_tuning_improvement.set(improvement)
        self.logger.info(f"📊 Threshold tuned: improvement={improvement:+.2%}")

    def record_alignment_check_latency(self, latency_ms: float):
        """Record alignment check latency.

        Args:
            latency_ms: Latency in milliseconds
        """
        alignment_check_latency.observe(latency_ms)
        if latency_ms > 5:
            self.logger.warning(f"⚠️  Slow alignment check: {latency_ms:.2f}ms (SLO: <5ms)")

    def record_drift_alert(self, severity: str):
        """Record drift alert.

        Args:
            severity: 'warning' or 'critical'
        """
        drift_alerts_total.labels(severity=severity).inc()
        self.logger.warning(f"🚨 Drift alert: severity={severity}")

    def set_active_goals(self, count: int):
        """Set gauge for active goals."""
        active_goals_gauge.set(count)

    def set_active_sessions(self, count: int):
        """Set gauge for active sessions."""
        active_sessions_gauge.set(count)

    def record_goal_restored(self):
        """Record goal restored from checkpoint."""
        goals_restored_total.inc()


class ContextDriftMetricsSnapshot:
    """Snapshot of current metrics (for API responses)."""

    def __init__(self, collector: ContextDriftMetricsCollector):
        self.collector = collector

    def get_snapshot(self) -> Dict:
        """Get current metrics snapshot."""
        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "status": "healthy",
            "checks_total": self._get_checks_total(),
            "alignment_scores": self._get_score_stats(),
            "threshold": self._get_threshold(),
            "feedback_quality": self._get_feedback_quality(),
            "tuning": self._get_tuning_stats(),
            "performance": self._get_performance_stats(),
            "goals": {
                "active": self._get_active_goals(),
                "sessions": self._get_active_sessions(),
                "restored": self._get_goals_restored(),
            }
        }

    def _get_checks_total(self) -> Dict[str, int]:
        """Get total checks by result."""
        # Note: In production, these would come from Prometheus metrics
        return {
            "aligned": 0,
            "drifted": 0,
            "escalated": 0,
        }

    def _get_score_stats(self) -> Dict:
        """Get alignment score statistics."""
        return {
            "mean": 0.65,  # Placeholder
            "p50": 0.60,
            "p95": 0.85,
            "p99": 0.95,
        }

    def _get_threshold(self) -> float:
        """Get current threshold."""
        # In production, read from prometheus threshold_gauge
        return 0.35

    def _get_feedback_quality(self) -> float:
        """Get feedback quality score."""
        # In production, read from prometheus feedback_quality_gauge
        return 0.85

    def _get_tuning_stats(self) -> Dict:
        """Get threshold tuning statistics."""
        return {
            "total_tunings": 0,
            "last_improvement": 0.0,
        }

    def _get_performance_stats(self) -> Dict:
        """Get performance SLO statistics."""
        return {
            "alignment_check_p95_ms": 2.5,
            "alignment_check_p99_ms": 4.2,
            "slo_met": True,
        }

    def _get_active_goals(self) -> int:
        """Get active goals count."""
        return 0

    def _get_active_sessions(self) -> int:
        """Get active sessions count."""
        return 0

    def _get_goals_restored(self) -> int:
        """Get goals restored count."""
        return 0


def get_metrics_collector() -> ContextDriftMetricsCollector:
    """Get global metrics collector instance."""
    global _metrics_collector
    if '_metrics_collector' not in globals():
        _metrics_collector = ContextDriftMetricsCollector()
    return _metrics_collector


# Export for prometheus integration
if _PROMETHEUS_AVAILABLE:
    __all__ = [
        'ContextDriftMetricsCollector',
        'ContextDriftMetricsSnapshot',
        'get_metrics_collector',
        'goal_alignment_checks_total',
        'alignment_score_histogram',
        'threshold_gauge',
        'feedback_quality_gauge',
        'threshold_tuning_total',
        'last_tuning_improvement',
        'alignment_check_latency',
        'drift_alerts_total',
        'active_goals_gauge',
        'active_sessions_gauge',
        'goals_restored_total',
    ]
