"""Measurement Collector for Skill Learning — ADR-0XXX (Track 3).

Continuous measurement collection during skill execution:
- Execution latency (p50, p95, p99)
- Success rate
- Resource usage (CPU, memory)
- Token cost
- Quality score

All measurements are:
1. Tenant-scoped (ADR-0007)
2. Audit-first (ADR-0232/0233)
3. Immutable (append-only)
4. Continuous across sessions (no data loss)
"""

from __future__ import annotations

import logging
import psutil
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class MeasurementType(str, Enum):
    """Canonical measurement types."""

    LATENCY = "latency"  # ms
    SUCCESS_RATE = "success_rate"  # 0-1
    CPU_USAGE = "cpu_usage"  # percent
    MEMORY_USAGE = "memory_usage"  # MB
    TOKEN_COST = "token_cost"  # integer tokens
    QUALITY_SCORE = "quality_score"  # 0-1


@dataclass(frozen=True)
class Measurement:
    """Immutable single measurement (GDPR Art. 32)."""

    measurement_id: str
    measurement_type: MeasurementType
    value: float
    timestamp_utc: datetime
    skill_id: str
    execution_id: str
    session_id: str
    tenant_id: str
    tags: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        """Convert to audit-safe payload (no PII)."""
        return {
            "measurement_id": self.measurement_id,
            "measurement_type": self.measurement_type.value,
            "value": self.value,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "skill_id": self.skill_id,
            "execution_id": self.execution_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "tags": self.tags,
        }


@dataclass(frozen=True)
class PercentileMeasurements:
    """Aggregated percentile measurements over a time window."""

    measurement_type: MeasurementType
    skill_id: str
    session_id: str
    tenant_id: str
    timestamp_utc: datetime
    count: int = 0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    min_value: float = 0.0
    max_value: float = 0.0
    sum_value: float = 0.0
    mean_value: float = 0.0

    def to_payload(self) -> Dict[str, Any]:
        """Convert to audit-safe payload."""
        return {
            "measurement_type": self.measurement_type.value,
            "skill_id": self.skill_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "count": self.count,
            "percentiles": {
                "p50": f"{self.p50:.4f}",
                "p95": f"{self.p95:.4f}",
                "p99": f"{self.p99:.4f}",
            },
            "aggregates": {
                "mean": f"{self.mean_value:.4f}",
                "min": f"{self.min_value:.4f}",
                "max": f"{self.max_value:.4f}",
                "sum": f"{self.sum_value:.4f}",
            },
        }


class MeasurementCollector:
    """Collect measurements during skill execution (ADR-0XXX Track 3).

    Supports:
    - Per-execution measurements (individual metric)
    - Per-session aggregation (p50, p95, p99)
    - Continuous collection across session boundaries
    - Tenant isolation (GDPR Art. 32)
    - Audit-first integration (ADR-0232/0233)
    """

    def __init__(
        self,
        tenant_id: str,
        session_id: str,
        skill_id: str,
        tenant_home: Optional[Path] = None,
    ):
        """Initialize measurement collector.

        Args:
            tenant_id: Tenant identifier (fail-closed on validation)
            session_id: Session identifier (unique per operator session)
            skill_id: Skill identifier (e.g., "os.delegation_router")
            tenant_home: Tenant home directory (for persistence)
        """
        self._validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.skill_id = skill_id
        self.tenant_home = tenant_home or Path.home() / ".corvin" / "tenants" / tenant_id

        # In-memory buffer for measurements (audit-first design)
        self._measurements: List[Measurement] = []
        self._process = psutil.Process()
        self._start_time = time.time()

    @staticmethod
    def _validate_tenant_id(tenant_id: str) -> None:
        """Validate tenant_id format (no path traversal, GDPR Art. 32)."""
        import re

        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: must be non-empty string, got {tenant_id!r}")

        if not re.match(r"^[a-zA-Z0-9_-]+$", tenant_id):
            raise ValueError(f"Invalid tenant_id format: {tenant_id!r}")

    def record_latency(
        self,
        execution_id: str,
        latency_ms: float,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Measurement:
        """Record execution latency measurement.

        Args:
            execution_id: Unique execution identifier
            latency_ms: Latency in milliseconds
            tags: Optional metadata (e.g., {"retry_count": 2})

        Returns:
            Immutable Measurement record
        """
        if latency_ms < 0:
            raise ValueError(f"Latency must be non-negative: {latency_ms}")

        return self._record_measurement(
            measurement_type=MeasurementType.LATENCY,
            value=latency_ms,
            execution_id=execution_id,
            tags=tags or {},
        )

    def record_success_rate(
        self,
        execution_id: str,
        success: bool,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Measurement:
        """Record success/failure measurement.

        Args:
            execution_id: Unique execution identifier
            success: True if execution succeeded
            tags: Optional metadata

        Returns:
            Immutable Measurement record (1.0 for success, 0.0 for failure)
        """
        value = 1.0 if success else 0.0
        return self._record_measurement(
            measurement_type=MeasurementType.SUCCESS_RATE,
            value=value,
            execution_id=execution_id,
            tags=tags or {"success": success},
        )

    def record_resource_usage(
        self,
        execution_id: str,
        measurement_type: MeasurementType = MeasurementType.CPU_USAGE,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Measurement:
        """Record CPU or memory usage during execution.

        Args:
            execution_id: Unique execution identifier
            measurement_type: CPU_USAGE or MEMORY_USAGE
            tags: Optional metadata

        Returns:
            Immutable Measurement record
        """
        if measurement_type not in (MeasurementType.CPU_USAGE, MeasurementType.MEMORY_USAGE):
            raise ValueError(
                f"Invalid resource measurement type: {measurement_type}. "
                "Must be CPU_USAGE or MEMORY_USAGE."
            )

        if measurement_type == MeasurementType.CPU_USAGE:
            # CPU percent usage (0-100)
            value = self._process.cpu_percent(interval=0.1)
        else:
            # Memory in MB
            value = self._process.memory_info().rss / (1024 * 1024)

        return self._record_measurement(
            measurement_type=measurement_type,
            value=value,
            execution_id=execution_id,
            tags=tags or {},
        )

    def record_token_cost(
        self,
        execution_id: str,
        input_tokens: int,
        output_tokens: int,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Measurement:
        """Record token usage (input + output).

        Args:
            execution_id: Unique execution identifier
            input_tokens: Input token count
            output_tokens: Output token count
            tags: Optional metadata

        Returns:
            Immutable Measurement record
        """
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError(f"Token counts must be non-negative: in={input_tokens}, out={output_tokens}")

        total_tokens = input_tokens + output_tokens
        return self._record_measurement(
            measurement_type=MeasurementType.TOKEN_COST,
            value=float(total_tokens),
            execution_id=execution_id,
            tags=tags or {"input_tokens": input_tokens, "output_tokens": output_tokens},
        )

    def record_quality_score(
        self,
        execution_id: str,
        score: float,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Measurement:
        """Record quality score (0-1).

        Args:
            execution_id: Unique execution identifier
            score: Quality score (0-1)
            tags: Optional metadata (e.g., {"dimension": "relevance"})

        Returns:
            Immutable Measurement record
        """
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Quality score must be in [0, 1]: {score}")

        return self._record_measurement(
            measurement_type=MeasurementType.QUALITY_SCORE,
            value=score,
            execution_id=execution_id,
            tags=tags or {},
        )

    def _record_measurement(
        self,
        measurement_type: MeasurementType,
        value: float,
        execution_id: str,
        tags: Dict[str, Any],
    ) -> Measurement:
        """Internal: record measurement to buffer.

        All measurements are:
        - Immutable (frozen dataclass)
        - Tenant-scoped
        - Timestamped
        - Buffered for audit-first write

        Args:
            measurement_type: Type of measurement
            value: Numeric value
            execution_id: Execution identifier
            tags: Metadata tags

        Returns:
            Immutable Measurement record
        """
        measurement = Measurement(
            measurement_id=str(uuid4()),
            measurement_type=measurement_type,
            value=value,
            timestamp_utc=datetime.utcnow(),
            skill_id=self.skill_id,
            execution_id=execution_id,
            session_id=self.session_id,
            tenant_id=self.tenant_id,
            tags=tags,
        )

        # Buffer for audit-first write
        self._measurements.append(measurement)
        logger.debug(
            f"Recorded {measurement_type.value}: {value} "
            f"(execution={execution_id}, tenant={self.tenant_id})"
        )

        return measurement

    def get_all_measurements(self) -> List[Measurement]:
        """Return all measurements collected in this session.

        Returns:
            List of immutable Measurement records
        """
        return list(self._measurements)

    def get_measurements_by_type(
        self,
        measurement_type: MeasurementType,
    ) -> List[Measurement]:
        """Filter measurements by type.

        Args:
            measurement_type: Type to filter on

        Returns:
            List of matching measurements
        """
        return [m for m in self._measurements if m.measurement_type == measurement_type]

    def calculate_percentiles(
        self,
        measurement_type: MeasurementType,
    ) -> Optional[PercentileMeasurements]:
        """Calculate percentiles for a measurement type (p50, p95, p99).

        Args:
            measurement_type: Type to aggregate

        Returns:
            PercentileMeasurements with p50/p95/p99 or None if insufficient data
        """
        measurements = self.get_measurements_by_type(measurement_type)
        if not measurements:
            return None

        values = sorted([m.value for m in measurements])
        count = len(values)

        # Calculate percentiles
        def percentile(p):
            index = (p / 100.0) * (count - 1)
            lower = int(index)
            upper = lower + 1
            weight = index - lower
            if upper >= count:
                return values[-1]
            return values[lower] * (1 - weight) + values[upper] * weight

        return PercentileMeasurements(
            measurement_type=measurement_type,
            skill_id=self.skill_id,
            session_id=self.session_id,
            tenant_id=self.tenant_id,
            timestamp_utc=datetime.utcnow(),
            count=count,
            p50=percentile(50),
            p95=percentile(95),
            p99=percentile(99),
            min_value=min(values),
            max_value=max(values),
            sum_value=sum(values),
            mean_value=sum(values) / count,
        )

    def session_summary(self) -> Dict[str, Any]:
        """Return session summary (all collected measurements).

        Returns:
            Dict with session metadata and aggregated measurements
        """
        elapsed = time.time() - self._start_time
        summary = {
            "session_id": self.session_id,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "measurement_count": len(self._measurements),
            "elapsed_seconds": f"{elapsed:.2f}",
            "measurements_by_type": {},
        }

        for mtype in MeasurementType:
            percentiles = self.calculate_percentiles(mtype)
            if percentiles:
                summary["measurements_by_type"][mtype.value] = percentiles.to_payload()

        return summary
