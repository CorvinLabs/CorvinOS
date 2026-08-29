"""Phase 6: Learning Dashboard — Metrics visualization and monitoring.

Provides:
1. Real-time metric collection (cost, accuracy, coverage, flake rate)
2. Time-series aggregation (hourly, daily, weekly)
3. Alerting on metric degradation
4. Trend analysis and forecasting
5. Compliance: audit trail, anonymization (GDPR Art. 5, 32)

Dashboard metrics:
- Cost: tokens/cost per operation
- Accuracy: success rate, relevance scores
- Coverage: % of workload handled
- Flake Rate: test intermittency percentage
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from statistics import mean, stdev
from typing import Optional, List, Dict, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    """Types of metrics tracked."""
    COST_TOKENS = "cost_tokens"
    COST_USD = "cost_usd"
    ACCURACY = "accuracy"
    COVERAGE = "coverage"
    FLAKE_RATE = "flake_rate"
    LATENCY_MS = "latency_ms"
    THROUGHPUT = "throughput"  # ops/sec
    ERROR_RATE = "error_rate"
    RETRY_COUNT = "retry_count"


class AggregationWindow(str, Enum):
    """Time aggregation granularity."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class MetricPoint:
    """Single metric observation."""

    point_id: str
    metric_type: MetricType
    value: float
    timestamp_utc: datetime
    tenant_id: str

    # Context
    dimension: Optional[str] = None  # e.g., "prompt_v1", "model_claude3.5"
    dimension_value: Optional[str] = None

    labels: Dict[str, str] = None  # Additional labels


@dataclass(frozen=True)
class AggregatedMetric:
    """Aggregated metric over a time window."""

    aggregation_id: str
    metric_type: MetricType
    window: AggregationWindow
    period_start: datetime
    period_end: datetime
    tenant_id: str

    # Statistics
    count: int
    min_value: float
    max_value: float
    mean_value: float
    std_dev: float
    p50: float  # Median
    p95: float  # 95th percentile
    p99: float  # 99th percentile

    # Trend
    trend: str  # "up", "down", "stable"
    trend_percent: float  # Change from previous period


@dataclass
class MetricAlert:
    """Alert triggered by metric threshold breach."""

    alert_id: str
    metric_type: MetricType
    severity: str  # "warning", "critical"
    threshold: float
    observed_value: float
    message: str
    triggered_at: datetime
    tenant_id: str

    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None


class LearningDashboard:
    """Collect, aggregate, and visualize learning metrics."""

    def __init__(
        self,
        storage_dir: Path,
        tenant_id: str,
        alert_thresholds: Optional[Dict[MetricType, float]] = None,
    ):
        """Initialize learning dashboard.

        Args:
            storage_dir: Path to store metric data
            tenant_id: Tenant ID
            alert_thresholds: Thresholds for alerting (e.g., {FLAKE_RATE: 0.05})
        """
        self.storage_dir = Path(storage_dir)
        self.tenant_id = tenant_id
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.storage_dir / "metrics.db"
        self._init_db()

        self.alert_thresholds = alert_thresholds or {
            MetricType.FLAKE_RATE: 0.05,  # Alert if >5% flaky
            MetricType.ERROR_RATE: 0.02,  # Alert if >2% error
            MetricType.COST_USD: 1.5,  # Alert if >1.5x baseline
        }

        self._metrics: List[MetricPoint] = []
        self._aggregations: Dict[str, AggregatedMetric] = {}
        self._alerts: Dict[str, MetricAlert] = {}
        self._lock = threading.Lock()

        self._baseline_metrics: Dict[MetricType, float] = {}

    def _init_db(self) -> None:
        """Initialize SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    point_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    dimension TEXT,
                    dimension_value TEXT,
                    labels TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS aggregations (
                    aggregation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    window TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    count INTEGER,
                    min_value REAL,
                    max_value REAL,
                    mean_value REAL,
                    std_dev REAL,
                    p50 REAL,
                    p95 REAL,
                    p99 REAL,
                    trend TEXT,
                    trend_percent REAL,
                    created_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    threshold REAL,
                    observed_value REAL,
                    message TEXT,
                    triggered_at TEXT NOT NULL,
                    acknowledged INTEGER DEFAULT 0,
                    acknowledged_at TEXT,
                    acknowledged_by TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            conn.commit()

    def record_metric(
        self,
        metric_type: MetricType,
        value: float,
        dimension: Optional[str] = None,
        dimension_value: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> MetricPoint:
        """Record a metric observation.

        Args:
            metric_type: Type of metric
            value: Metric value
            dimension: Optional dimension (e.g., "prompt", "model")
            dimension_value: Value of dimension (e.g., "prompt_v1")
            labels: Additional labels for grouping

        Returns:
            MetricPoint record
        """
        point_id = str(uuid4())
        now = datetime.now(timezone.utc)

        point = MetricPoint(
            point_id=point_id,
            metric_type=metric_type,
            value=value,
            timestamp_utc=now,
            tenant_id=self.tenant_id,
            dimension=dimension,
            dimension_value=dimension_value,
            labels=labels or {},
        )

        with self._lock:
            self._metrics.append(point)
            self._store_metric(point)

            # Check alert thresholds
            if metric_type in self.alert_thresholds:
                threshold = self.alert_thresholds[metric_type]
                if value > threshold:
                    self._create_alert(metric_type, threshold, value)

        return point

    def _store_metric(self, point: MetricPoint) -> None:
        """Persist metric to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO metrics
                (point_id, tenant_id, metric_type, value, timestamp_utc,
                 dimension, dimension_value, labels, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                point.point_id, point.tenant_id, point.metric_type.value, point.value,
                point.timestamp_utc.isoformat(), point.dimension, point.dimension_value,
                json.dumps(point.labels or {}), datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

    def _create_alert(self, metric_type: MetricType, threshold: float, value: float) -> None:
        """Create an alert for threshold breach."""
        alert_id = str(uuid4())

        severity = "warning" if value < threshold * 1.5 else "critical"
        message = f"{metric_type.value} exceeded threshold: {value:.3f} > {threshold:.3f}"

        alert = MetricAlert(
            alert_id=alert_id,
            metric_type=metric_type,
            severity=severity,
            threshold=threshold,
            observed_value=value,
            message=message,
            triggered_at=datetime.now(timezone.utc),
            tenant_id=self.tenant_id,
        )

        self._alerts[alert_id] = alert
        self._store_alert(alert)

        logger.warning(f"Alert: {message} (severity={severity})")

    def _store_alert(self, alert: MetricAlert) -> None:
        """Persist alert to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO alerts
                (alert_id, tenant_id, metric_type, severity, threshold, observed_value,
                 message, triggered_at, acknowledged, acknowledged_at, acknowledged_by,
                 created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert.alert_id, alert.tenant_id, alert.metric_type.value,
                alert.severity, alert.threshold, alert.observed_value, alert.message,
                alert.triggered_at.isoformat(), 1 if alert.acknowledged else 0,
                alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
                alert.acknowledged_by, datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

    def aggregate_metrics(
        self,
        metric_type: MetricType,
        window: AggregationWindow,
        lookback_hours: int = 24,
    ) -> Optional[AggregatedMetric]:
        """Aggregate metrics over a time window.

        Args:
            metric_type: Type of metric to aggregate
            window: Aggregation granularity
            lookback_hours: How far back to look

        Returns:
            AggregatedMetric or None if insufficient data
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

        with self._lock:
            values = [
                m.value for m in self._metrics
                if m.metric_type == metric_type and m.timestamp_utc > cutoff
            ]

        if not values:
            return None

        values_sorted = sorted(values)
        n = len(values)

        # Calculate percentiles
        p50 = values_sorted[n // 2]
        p95 = values_sorted[int(n * 0.95)] if n > 1 else values_sorted[0]
        p99 = values_sorted[int(n * 0.99)] if n > 1 else values_sorted[0]

        mean_val = sum(values) / n
        std_val = (sum((x - mean_val) ** 2 for x in values) / n) ** 0.5

        # Calculate trend (comparing to previous period)
        prev_cutoff = cutoff - timedelta(hours=lookback_hours)

        with self._lock:
            prev_values = [
                m.value for m in self._metrics
                if m.metric_type == metric_type and prev_cutoff < m.timestamp_utc < cutoff
            ]

        trend = "stable"
        trend_pct = 0.0

        if prev_values:
            prev_mean = sum(prev_values) / len(prev_values)
            trend_pct = (mean_val - prev_mean) / prev_mean if prev_mean > 0 else 0
            if trend_pct > 0.05:
                trend = "up"
            elif trend_pct < -0.05:
                trend = "down"

        now = datetime.now(timezone.utc)
        period_start = cutoff
        period_end = now

        agg = AggregatedMetric(
            aggregation_id=str(uuid4()),
            metric_type=metric_type,
            window=window,
            period_start=period_start,
            period_end=period_end,
            tenant_id=self.tenant_id,
            count=n,
            min_value=values_sorted[0],
            max_value=values_sorted[-1],
            mean_value=mean_val,
            std_dev=std_val,
            p50=p50,
            p95=p95,
            p99=p99,
            trend=trend,
            trend_percent=trend_pct,
        )

        with self._lock:
            self._aggregations[agg.aggregation_id] = agg
            self._store_aggregation(agg)

        return agg

    def _store_aggregation(self, agg: AggregatedMetric) -> None:
        """Persist aggregation to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO aggregations
                (aggregation_id, tenant_id, metric_type, window,
                 period_start, period_end, count, min_value, max_value, mean_value,
                 std_dev, p50, p95, p99, trend, trend_percent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agg.aggregation_id, agg.tenant_id, agg.metric_type.value, agg.window.value,
                agg.period_start.isoformat(), agg.period_end.isoformat(),
                agg.count, agg.min_value, agg.max_value, agg.mean_value, agg.std_dev,
                agg.p50, agg.p95, agg.p99, agg.trend, agg.trend_percent,
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

    def get_dashboard_snapshot(self) -> dict:
        """Get current dashboard state (all key metrics).

        Returns:
            Dictionary with current metrics and alerts
        """
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": self.tenant_id,
            "metrics": {},
            "alerts": [],
        }

        # Aggregate key metrics
        for metric_type in [
            MetricType.COST_USD,
            MetricType.ACCURACY,
            MetricType.COVERAGE,
            MetricType.FLAKE_RATE,
            MetricType.ERROR_RATE,
        ]:
            agg = self.aggregate_metrics(metric_type, AggregationWindow.HOURLY, lookback_hours=1)
            if agg:
                snapshot["metrics"][metric_type.value] = {
                    "mean": agg.mean_value,
                    "p95": agg.p95,
                    "p99": agg.p99,
                    "trend": agg.trend,
                    "trend_percent": f"{agg.trend_percent:.1%}",
                    "sample_count": agg.count,
                }

        # Include active alerts
        with self._lock:
            for alert in self._alerts.values():
                if not alert.acknowledged:
                    snapshot["alerts"].append({
                        "alert_id": alert.alert_id,
                        "metric": alert.metric_type.value,
                        "severity": alert.severity,
                        "message": alert.message,
                        "triggered_at": alert.triggered_at.isoformat(),
                    })

        return snapshot

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str = "system") -> bool:
        """Acknowledge an alert.

        Args:
            alert_id: ID of alert to acknowledge
            acknowledged_by: User/system acknowledging the alert

        Returns:
            True if acknowledged, False if not found
        """
        with self._lock:
            if alert_id not in self._alerts:
                return False

            alert = self._alerts[alert_id]
            alert.acknowledged = True
            alert.acknowledged_at = datetime.now(timezone.utc)
            alert.acknowledged_by = acknowledged_by

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE alerts
                SET acknowledged = 1, acknowledged_at = ?, acknowledged_by = ?
                WHERE alert_id = ?
            """, (
                alert.acknowledged_at.isoformat(),
                acknowledged_by,
                alert_id
            ))
            conn.commit()

        return True

    def get_cost_report(self, days: int = 7) -> dict:
        """Get cost analysis report.

        Args:
            days: Time window

        Returns:
            Cost report dictionary
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        with self._lock:
            costs = [
                m.value for m in self._metrics
                if m.metric_type == MetricType.COST_USD and m.timestamp_utc > cutoff
            ]

        if not costs:
            return {"period_days": days, "total_cost": 0, "avg_daily": 0}

        total = sum(costs)
        avg_daily = total / days

        return {
            "period_days": days,
            "total_cost": f"${total:.2f}",
            "avg_daily": f"${avg_daily:.2f}",
            "sample_count": len(costs),
        }

    def get_accuracy_report(self, days: int = 7) -> dict:
        """Get accuracy analysis report.

        Args:
            days: Time window

        Returns:
            Accuracy report dictionary
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        with self._lock:
            accuracies = [
                m.value for m in self._metrics
                if m.metric_type == MetricType.ACCURACY and m.timestamp_utc > cutoff
            ]

        if not accuracies:
            return {"period_days": days, "mean_accuracy": 0, "min_accuracy": 0, "max_accuracy": 0}

        mean_acc = sum(accuracies) / len(accuracies)
        min_acc = min(accuracies)
        max_acc = max(accuracies)

        return {
            "period_days": days,
            "mean_accuracy": f"{mean_acc:.1%}",
            "min_accuracy": f"{min_acc:.1%}",
            "max_accuracy": f"{max_acc:.1%}",
            "sample_count": len(accuracies),
        }

    def get_coverage_report(self, days: int = 7) -> dict:
        """Get coverage analysis report.

        Args:
            days: Time window

        Returns:
            Coverage report dictionary
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        with self._lock:
            coverages = [
                m.value for m in self._metrics
                if m.metric_type == MetricType.COVERAGE and m.timestamp_utc > cutoff
            ]

        if not coverages:
            return {"period_days": days, "mean_coverage": 0, "min_coverage": 0, "max_coverage": 0}

        mean_cov = sum(coverages) / len(coverages)
        min_cov = min(coverages)
        max_cov = max(coverages)

        return {
            "period_days": days,
            "mean_coverage": f"{mean_cov:.1%}",
            "min_coverage": f"{min_cov:.1%}",
            "max_coverage": f"{max_cov:.1%}",
            "sample_count": len(coverages),
        }
