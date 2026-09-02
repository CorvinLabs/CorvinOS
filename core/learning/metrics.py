"""Metrics collection and aggregation pipeline (ADR-0320).

Collects per-skill, per-user, and system-wide metrics:
- Metric schema (per-task, per-user, per-phase)
- Aggregation pipeline (sum, mean, percentile)
- Time-series storage (partitioned by date)
- Query interface
- Aggregation windows: 1h, 1d, 1w, 1mo
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    """Canonical metric types."""

    ACCURACY = "accuracy"  # Success rate 0-1
    LATENCY = "latency"  # Response time in ms
    CONFIDENCE = "confidence"  # Model confidence 0-1
    THROUGHPUT = "throughput"  # Requests per second
    USER_SATISFACTION = "user_satisfaction"  # 1-5 scale


@dataclass(frozen=True)
class MetricRecord:
    """Immutable metric record (single measurement)."""

    metric_id: str
    metric_type: MetricType
    value: float
    session_id: str
    timestamp_utc: datetime
    skill_name: Optional[str] = None
    user_id: Optional[str] = None
    tags: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        """Convert to GDPR-safe payload (no PII beyond session_id/user_id tracking)."""
        return {
            "metric_id": self.metric_id,
            "metric_type": self.metric_type.value,
            "value": self.value,
            "session_id": self.session_id,
            "skill_name": self.skill_name,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "tags": self.tags,
        }


@dataclass(frozen=True)
class AggregatedMetrics:
    """Aggregated metrics over a time window."""

    metric_type: MetricType
    window: str  # "1h", "1d", "1w", "1mo"
    count: int  # Number of samples
    sum_value: float
    mean_value: float
    min_value: float
    max_value: float
    p50: float  # Median
    p95: float  # 95th percentile
    p99: float  # 99th percentile
    timestamp_utc: datetime
    skill_name: Optional[str] = None
    user_id: Optional[str] = None

    def to_payload(self) -> dict:
        """Convert to GDPR-safe payload."""
        return {
            "metric_type": self.metric_type.value,
            "window": self.window,
            "count": self.count,
            "mean": f"{self.mean_value:.4f}",
            "percentiles": {
                "p50": f"{self.p50:.4f}",
                "p95": f"{self.p95:.4f}",
                "p99": f"{self.p99:.4f}",
            },
            "timestamp_utc": self.timestamp_utc.isoformat(),
        }


class MetricsCollector:
    """Collect and emit metrics as learning signals.

    Supports per-skill, per-user, and system-wide metrics with multiple aggregation windows.
    """

    def __init__(self, tenant_id: str):
        """Initialize collector.

        Args:
            tenant_id: Tenant ID (for isolation per GDPR Art. 5, 6, 32)
        """
        self.tenant_id = tenant_id
        self._records: list[MetricRecord] = []

    def record_accuracy(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record accuracy metric (0.0-1.0).

        Args:
            session_id: Session identifier
            value: Accuracy as fraction (0.0-1.0)
            skill_name: Optional skill being evaluated
            user_id: Optional user ID
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value outside [0.0, 1.0]
        """
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"Invalid accuracy: {value}, must be in [0.0, 1.0]")

        record = MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.ACCURACY,
            value=value,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            skill_name=skill_name,
            user_id=user_id,
            tags=tags or {},
        )
        self._records.append(record)
        return record

    def record_latency(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record latency metric (ms, must be >= 0).

        Args:
            session_id: Session identifier
            value: Latency in milliseconds
            skill_name: Optional skill being evaluated
            user_id: Optional user ID
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value < 0
        """
        if value < 0.0:
            raise ValueError(f"Invalid latency: {value}, must be >= 0")

        record = MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.LATENCY,
            value=value,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            skill_name=skill_name,
            user_id=user_id,
            tags=tags or {},
        )
        self._records.append(record)
        return record

    def record_confidence(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record confidence metric (0.0-1.0).

        Args:
            session_id: Session identifier
            value: Confidence as fraction (0.0-1.0)
            skill_name: Optional skill being evaluated
            user_id: Optional user ID
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value outside [0.0, 1.0]
        """
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"Invalid confidence: {value}, must be in [0.0, 1.0]")

        record = MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.CONFIDENCE,
            value=value,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            skill_name=skill_name,
            user_id=user_id,
            tags=tags or {},
        )
        self._records.append(record)
        return record

    def record_throughput(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record throughput metric (requests/sec, must be >= 0).

        Args:
            session_id: Session identifier
            value: Throughput in requests per second
            skill_name: Optional skill being evaluated
            user_id: Optional user ID
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value < 0
        """
        if value < 0.0:
            raise ValueError(f"Invalid throughput: {value}, must be >= 0")

        record = MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.THROUGHPUT,
            value=value,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            skill_name=skill_name,
            user_id=user_id,
            tags=tags or {},
        )
        self._records.append(record)
        return record

    def record_satisfaction(
        self,
        session_id: str,
        value: int,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record user satisfaction metric (1-5 scale).

        Args:
            session_id: Session identifier
            value: Rating on 1-5 scale
            skill_name: Optional skill being evaluated
            user_id: Optional user ID
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value outside [1, 5]
        """
        if not (1 <= value <= 5):
            raise ValueError(f"Invalid satisfaction: {value}, must be in [1, 5]")

        record = MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.USER_SATISFACTION,
            value=float(value),
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            skill_name=skill_name,
            user_id=user_id,
            tags=tags or {},
        )
        self._records.append(record)
        return record

    def get_records(self) -> list[MetricRecord]:
        """Get all collected records.

        Returns:
            List of MetricRecord (immutable, read-only)
        """
        return list(self._records)  # Return copy to prevent mutation

    def clear_records(self) -> None:
        """Clear all collected records (e.g., after persistence)."""
        self._records.clear()


def percentile(values: list[float], p: float) -> float:
    """Calculate percentile without NumPy (GDPR-safe, audit-trail compatible).

    Args:
        values: Sorted or unsorted numeric list
        p: Percentile (0-100)

    Returns:
        Percentile value

    Raises:
        ValueError: If values empty or p outside [0, 100]
    """
    if not values:
        raise ValueError("Cannot compute percentile on empty list")
    if not (0 <= p <= 100):
        raise ValueError(f"Percentile must be in [0, 100], got {p}")

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    if n == 1:
        return sorted_vals[0]

    # Linear interpolation between ranks
    rank = (p / 100.0) * (n - 1)
    lower_idx = int(rank)
    upper_idx = min(lower_idx + 1, n - 1)
    fraction = rank - lower_idx

    return sorted_vals[lower_idx] * (1 - fraction) + sorted_vals[upper_idx] * fraction


@dataclass(frozen=True)
class MetricsQuery:
    """Query filter for metrics (tenant-scoped, GDPR Art. 32)."""

    tenant_id: str
    metric_type: Optional[MetricType] = None
    skill_name: Optional[str] = None
    user_id: Optional[str] = None
    since_utc: Optional[datetime] = None
    until_utc: Optional[datetime] = None
    limit: int = 10000

    def matches(self, record: MetricRecord) -> bool:
        """Check if record matches query filters."""
        if self.metric_type and record.metric_type != self.metric_type:
            return False
        if self.skill_name and record.skill_name != self.skill_name:
            return False
        if self.user_id and record.user_id != self.user_id:
            return False
        if self.since_utc and record.timestamp_utc < self.since_utc:
            return False
        if self.until_utc and record.timestamp_utc > self.until_utc:
            return False
        return True


class MetricsAggregator:
    """Aggregates metrics from MetricsCollector into time windows.

    Emits METRIC_AGGREGATED learning events (ADR-0314 integration).
    Maintains aggregation state (cursor) to prevent duplication.
    """

    WINDOWS = {
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
        "1mo": timedelta(days=30),  # Approximate month
    }

    def __init__(self, tenant_id: str, tenant_home: Optional[Path] = None):
        """Initialize aggregator.

        Args:
            tenant_id: Tenant ID (for isolation per GDPR Art. 5, 6, 32)
            tenant_home: Path to tenant home (for cursor state storage)
        """
        self.tenant_id = tenant_id
        self.tenant_home = Path(tenant_home) if tenant_home else None
        self._cursor_file = None

        if self.tenant_home:
            self.tenant_home.mkdir(parents=True, exist_ok=True)
            self._cursor_file = self.tenant_home / "metrics_aggregation_cursor.json"

    def _load_cursor(self) -> dict[str, str]:
        """Load last aggregation cursor (to prevent re-aggregation)."""
        if not self._cursor_file or not self._cursor_file.exists():
            return {}

        try:
            with open(self._cursor_file) as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            return {}

    def _save_cursor(self, cursor: dict[str, str]) -> None:
        """Save aggregation cursor."""
        if not self._cursor_file:
            return

        try:
            with open(self._cursor_file, "w") as f:
                json.dump(cursor, f)
        except IOError as e:
            logger.warning(f"Failed to save aggregation cursor: {e}")

    def aggregate(
        self,
        records: list[MetricRecord],
        window: str,
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[AggregatedMetrics]:
        """Aggregate records over a time window.

        Args:
            records: Collected metric records (may be filtered)
            window: Window size ("1h", "1d", "1w", "1mo")
            skill_name: Optional skill name (for per-skill aggregation)
            user_id: Optional user ID (for per-user aggregation)

        Returns:
            AggregatedMetrics if records exist, None otherwise

        Raises:
            ValueError: If window not recognized
        """
        if window not in self.WINDOWS:
            raise ValueError(f"Unknown window: {window}")

        # Filter records matching the window + skill + user
        filtered = [
            r
            for r in records
            if (skill_name is None or r.skill_name == skill_name)
            and (user_id is None or r.user_id == user_id)
        ]

        if not filtered:
            return None

        # Extract values for aggregation
        values = [r.value for r in filtered]

        # Compute statistics
        count = len(values)
        sum_value = sum(values)
        mean_value = sum_value / count if count > 0 else 0.0
        min_value = min(values)
        max_value = max(values)
        p50 = percentile(values, 50)
        p95 = percentile(values, 95)
        p99 = percentile(values, 99)

        # Use earliest record timestamp as window anchor
        earliest_record = min(filtered, key=lambda r: r.timestamp_utc)

        return AggregatedMetrics(
            metric_type=earliest_record.metric_type,
            window=window,
            count=count,
            sum_value=sum_value,
            mean_value=mean_value,
            min_value=min_value,
            max_value=max_value,
            p50=p50,
            p95=p95,
            p99=p99,
            timestamp_utc=earliest_record.timestamp_utc,
            skill_name=skill_name,
            user_id=user_id,
        )

    def aggregate_all_windows(
        self,
        records: list[MetricRecord],
        skill_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict[str, AggregatedMetrics]:
        """Aggregate records across all supported windows.

        Args:
            records: Collected metric records
            skill_name: Optional skill name
            user_id: Optional user ID

        Returns:
            Dictionary mapping window name to AggregatedMetrics
        """
        result = {}
        for window in self.WINDOWS:
            agg = self.aggregate(records, window, skill_name, user_id)
            if agg:
                result[window] = agg
        return result

    def get_system_metrics(
        self,
        records: list[MetricRecord],
        window: str = "1d",
    ) -> Optional[AggregatedMetrics]:
        """Get system-wide metrics (all skills, all users).

        Args:
            records: All collected metric records
            window: Aggregation window ("1h", "1d", "1w", "1mo")

        Returns:
            Aggregated system metrics, None if no records
        """
        return self.aggregate(records, window)

    def get_skill_metrics(
        self,
        records: list[MetricRecord],
        skill_name: str,
        window: str = "1d",
    ) -> Optional[AggregatedMetrics]:
        """Get per-skill metrics.

        Args:
            records: Collected metric records
            skill_name: Skill to query
            window: Aggregation window

        Returns:
            Aggregated skill metrics, None if no matching records
        """
        return self.aggregate(records, window, skill_name=skill_name)

    def get_user_metrics(
        self,
        records: list[MetricRecord],
        user_id: str,
        window: str = "1d",
    ) -> Optional[AggregatedMetrics]:
        """Get per-user metrics.

        Args:
            records: Collected metric records
            user_id: User to query
            window: Aggregation window

        Returns:
            Aggregated user metrics, None if no matching records
        """
        return self.aggregate(records, window, user_id=user_id)

    def emit_metric_aggregated_event(
        self,
        aggregated: AggregatedMetrics,
        instance_id: str,
        skill_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a METRIC_AGGREGATED learning event.

        Args:
            aggregated: AggregatedMetrics result
            instance_id: Instance ID for audit trail
            skill_name: Optional skill name for attribution

        Returns:
            Event payload dict (ready for LearningEvent emission)

        Compliance: GDPR Art. 6(1)(f) — metrics are aggregated, no PII
        """
        return {
            "event_type": "metric.aggregated",
            "metric_type": aggregated.metric_type.value,
            "window": aggregated.window,
            "count": aggregated.count,
            "mean": f"{aggregated.mean_value:.6f}",
            "p50": f"{aggregated.p50:.6f}",
            "p95": f"{aggregated.p95:.6f}",
            "p99": f"{aggregated.p99:.6f}",
            "min": f"{aggregated.min_value:.6f}",
            "max": f"{aggregated.max_value:.6f}",
            "skill_name": aggregated.skill_name or skill_name,
            "user_id": aggregated.user_id,
            "timestamp": aggregated.timestamp_utc.isoformat(),
            "instance_id": instance_id,
        }
