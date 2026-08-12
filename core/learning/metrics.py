"""Metric Collection — aggregation pipeline (ADR-0320)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


class MetricType(str, Enum):
    """Types of metrics to collect."""

    ACCURACY = "accuracy"              # Prediction vs. actual (0.0-1.0)
    LATENCY = "latency"                # Response time in ms
    CONFIDENCE = "confidence"          # Skill's stated confidence (0.0-1.0)
    THROUGHPUT = "throughput"          # Decisions/events per timeframe
    USER_SATISFACTION = "satisfaction" # User rating (1-5)


@dataclass(frozen=True)
class MetricRecord:
    """Immutable record of a metric observation."""

    metric_id: str
    metric_type: MetricType
    value: float
    skill_name: Optional[str]
    session_id: str
    timestamp_utc: datetime
    tags: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        """Convert to event payload."""
        return {
            "metric_id": self.metric_id,
            "metric_type": self.metric_type.value,
            "value": self.value,
            "skill_name": self.skill_name,
            "tags": self.tags,
        }


class MetricsCollector:
    """Collect metrics for observability and learning."""

    def __init__(self, tenant_id: str):
        """Initialize collector.

        Args:
            tenant_id: Tenant ID (for isolation)
        """
        self.tenant_id = tenant_id

    def record_accuracy(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record accuracy metric (prediction vs. actual).

        Args:
            session_id: Session ID
            value: Accuracy score (0.0-1.0)
            skill_name: Optional skill name
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value out of bounds
        """
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"Invalid accuracy value: {value}, must be 0.0-1.0")

        return MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.ACCURACY,
            value=value,
            skill_name=skill_name,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            tags=tags or {},
        )

    def record_latency(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record latency metric (response time in ms).

        Args:
            session_id: Session ID
            value: Latency in milliseconds (>= 0)
            skill_name: Optional skill name
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value invalid
        """
        if value < 0:
            raise ValueError(f"Invalid latency: {value}, must be >= 0")

        return MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.LATENCY,
            value=value,
            skill_name=skill_name,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            tags=tags or {},
        )

    def record_confidence(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record confidence metric (stated confidence 0.0-1.0).

        Args:
            session_id: Session ID
            value: Confidence score (0.0-1.0)
            skill_name: Optional skill name
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value out of bounds
        """
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"Invalid confidence value: {value}, must be 0.0-1.0")

        return MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.CONFIDENCE,
            value=value,
            skill_name=skill_name,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            tags=tags or {},
        )

    def record_throughput(
        self,
        session_id: str,
        value: float,
        skill_name: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record throughput metric (events per timeframe).

        Args:
            session_id: Session ID
            value: Throughput count (>= 0)
            skill_name: Optional skill name
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value invalid
        """
        if value < 0:
            raise ValueError(f"Invalid throughput: {value}, must be >= 0")

        return MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.THROUGHPUT,
            value=value,
            skill_name=skill_name,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            tags=tags or {},
        )

    def record_satisfaction(
        self,
        session_id: str,
        value: int,
        skill_name: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> MetricRecord:
        """Record user satisfaction metric (rating 1-5).

        Args:
            session_id: Session ID
            value: Satisfaction rating (1-5)
            skill_name: Optional skill name
            tags: Optional metadata tags

        Returns:
            MetricRecord

        Raises:
            ValueError: If value out of bounds
        """
        if not (1 <= value <= 5):
            raise ValueError(f"Invalid satisfaction rating: {value}, must be 1-5")

        return MetricRecord(
            metric_id=str(uuid4()),
            metric_type=MetricType.USER_SATISFACTION,
            value=float(value),
            skill_name=skill_name,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            tags=tags or {},
        )
