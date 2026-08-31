"""Metrics collection and aggregation pipeline (ADR-0320).

Collects per-skill, per-user, and system-wide metrics:
- Metric schema (per-task, per-user, per-phase)
- Aggregation pipeline (sum, mean, percentile)
- Time-series storage (partitioned by date)
- Query interface
- Aggregation windows: 1h, 1d, 1w, 1mo
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


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
