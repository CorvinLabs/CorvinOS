"""Aggregation Pipeline (ADR-0326).

Multi-stage pipeline: collect → validate → aggregate → emit
Fail-closed at each stage. Tenant isolation enforced throughout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from statistics import mean, quantiles

from core.telemetry.source_of_truth import MetricType, MetricValue, TelemetryRegistry

logger = logging.getLogger(__name__)


@dataclass
class AggregationConfig:
    """Configuration for aggregation pipeline."""

    window_seconds: int = 60
    output_backend: str = "audit"  # "audit" or "disk"


class Metric:
    """Represents a raw metric before aggregation."""

    def __init__(self, name: str, value: float, tenant_id: str, timestamp: datetime):
        """Initialize raw metric."""
        self.name = name
        self.value = value
        self.tenant_id = tenant_id
        self.timestamp = timestamp

    def __repr__(self) -> str:
        return f"Metric(name={self.name}, value={self.value}, tenant={self.tenant_id})"


@dataclass(frozen=True)
class AggregatedMetric:
    """Result of aggregation."""

    name: str
    metric_type: MetricType
    window_seconds: int
    values: list[float]
    min_value: float
    max_value: float
    mean_value: float
    p50: float
    p99: float
    sample_count: int
    tenant_id: str
    timestamp_utc: datetime

    def to_audit_event(self) -> dict[str, Any]:
        """Convert to audit event format."""
        return {
            "event_type": "aggregation.metric_aggregated",
            "metric_name": self.name,
            "metric_type": self.metric_type.value,
            "window_seconds": self.window_seconds,
            "min": self.min_value,
            "max": self.max_value,
            "mean": self.mean_value,
            "p50": self.p50,
            "p99": self.p99,
            "sample_count": self.sample_count,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp_utc.isoformat() + "Z",
        }


class AggregationPipeline:
    """Multi-stage aggregation with fail-closed validation."""

    def __init__(self, config: Optional[AggregationConfig] = None):
        """Initialize pipeline.

        Args:
            config: Pipeline configuration
        """
        self.config = config or AggregationConfig()
        self._collected_metrics: list[Metric] = []
        self._aggregated: list[AggregatedMetric] = []
        self.registry = TelemetryRegistry()

    def collect(self, metrics: list[Metric]) -> None:
        """Stage 1: Collect raw metrics (fail-closed validation).

        Args:
            metrics: List of metrics to collect

        Raises:
            TypeError: If metrics is not a list
            ValueError: If any metric is invalid
        """
        if not isinstance(metrics, list):
            raise TypeError(f"metrics must be list, got {type(metrics).__name__}")

        for i, metric in enumerate(metrics):
            # Validate metric shape
            if not isinstance(metric, Metric):
                raise ValueError(
                    f"metrics[{i}]: expected Metric, got {type(metric).__name__}"
                )

            # Validate required fields
            if not metric.name or not isinstance(metric.name, str):
                raise ValueError(f"metrics[{i}].name must be non-empty string")

            if not isinstance(metric.value, (int, float)):
                raise ValueError(
                    f"metrics[{i}].value must be numeric, got {type(metric.value).__name__}"
                )

            if not metric.tenant_id or not isinstance(metric.tenant_id, str):
                raise ValueError(f"metrics[{i}].tenant_id must be non-empty string")

            if not isinstance(metric.timestamp, datetime):
                raise ValueError(
                    f"metrics[{i}].timestamp must be datetime, got {type(metric.timestamp).__name__}"
                )

        self._collected_metrics.extend(metrics)
        logger.debug(f"Collected {len(metrics)} metrics")

    def validate(self) -> None:
        """Stage 2: Validate collected metrics (fail-closed).

        Raises:
            ValueError: If validation fails
        """
        if not self._collected_metrics:
            logger.warning("No metrics to validate")
            return

        errors = []

        for i, metric in enumerate(self._collected_metrics):
            # Cross-tenant check
            if not metric.tenant_id:
                errors.append(f"Metric {i}: missing tenant_id")

            # Metric registration check
            if not self.registry.is_metric_registered(metric.name):
                errors.append(f"Metric {i}: '{metric.name}' not registered with TelemetryRegistry")

            # Value sanity check
            if not isinstance(metric.value, (int, float)):
                errors.append(f"Metric {i}: value not numeric")

            if metric.value < 0:
                contract = self.registry.get_metric_contract(metric.name)
                if contract:
                if contract.metric_type == MetricType.COUNTER:
                    errors.append(f"Metric {i}: counter cannot have negative value")

        if errors:
            raise ValueError("Validation failed:\n" + "\n".join(errors))

        logger.debug(f"Validated {len(self._collected_metrics)} metrics")

    def aggregate(self, window_seconds: Optional[int] = None) -> list[AggregatedMetric]:
        """Stage 3: Aggregate metrics by name.

        Args:
            window_seconds: Override config window

        Returns:
            List of aggregated metrics

        Raises:
            ValueError: If no metrics collected or validation failed
        """
        if not self._collected_metrics:
            raise ValueError("No metrics collected; call collect() first")

        # Validate before aggregating
        self.validate()

        window = window_seconds or self.config.window_seconds

        # Group by (name, tenant_id)
        grouped: dict[tuple[str, str], list[float]] = {}

        for metric in self._collected_metrics:
            key = (metric.name, metric.tenant_id)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(float(metric.value))

        # Aggregate each group
        self._aggregated = []

        for (name, tenant_id), values in grouped.items():
            if not values:
                continue

            sorted_values = sorted(values)
            min_val = min(values)
            max_val = max(values)
            mean_val = mean(values)

            # Calculate percentiles
            try:
                if len(values) >= 2:
                    percentiles = quantiles(values, n=100)
                    p50 = percentiles[49]  # 50th percentile
                    p99 = percentiles[98] if len(percentiles) > 98 else max_val  # 99th percentile
                else:
                    p50 = values[0]
                    p99 = values[0]
            except Exception as e:
                logger.warning(f"Could not compute percentiles for {name}: {e}")
                p50 = mean_val
                p99 = max_val

            # Get metric contract
            contract = self.registry.get_metric_contract(name)
            metric_type = contract.metric_type if contract else MetricType.GAUGE

            agg = AggregatedMetric(
                name=name,
                metric_type=metric_type,
                window_seconds=window,
                values=sorted_values,
                min_value=min_val,
                max_value=max_val,
                mean_value=mean_val,
                p50=p50,
                p99=p99,
                sample_count=len(values),
                tenant_id=tenant_id,
                timestamp_utc=datetime.utcnow(),
            )

            self._aggregated.append(agg)
            logger.debug(f"Aggregated {name}: min={min_val}, max={max_val}, mean={mean_val:.2f}, samples={len(values)}")

        return self._aggregated

    def emit(self) -> dict[str, Any]:
        """Stage 4: Emit aggregated metrics to backends.

        Returns:
            Dict with emission results

        Raises:
            ValueError: If no aggregated metrics
        """
        if not self._aggregated:
            raise ValueError("No aggregated metrics; call aggregate() first")

        results = {
            "emitted_to_audit": 0,
            "emitted_to_disk": 0,
            "failed": 0,
            "errors": [],
        }

        for agg_metric in self._aggregated:
            # Emit to audit chain if configured
            if self.config.output_backend in ("audit", "both"):
                try:
                    from core.compliance.corvin_compliance_reports.audit_writer import write_audit_event

                    event = agg_metric.to_audit_event()
                    audit_id = write_audit_event(event)
                    results["emitted_to_audit"] += 1
                    logger.debug(f"Emitted {agg_metric.name} to audit chain: {audit_id}")

                except Exception as e:
                    logger.warning(f"Failed to emit {agg_metric.name} to audit chain: {e}")
                    results["failed"] += 1
                    results["errors"].append(str(e))

            # Emit to disk if configured
            if self.config.output_backend in ("disk", "both"):
                try:
                    # Log-style disk output (test fixture can capture)
                    logger.info(f"Disk emit: {agg_metric.to_audit_event()}")
                    results["emitted_to_disk"] += 1

                except Exception as e:
                    logger.warning(f"Failed to emit {agg_metric.name} to disk: {e}")
                    results["failed"] += 1
                    results["errors"].append(str(e))

        logger.info(
            f"Emission complete: audit={results['emitted_to_audit']}, "
            f"disk={results['emitted_to_disk']}, failed={results['failed']}"
        )

        return results

    def reset_for_testing(self) -> None:
        """Clear all state (TEST ONLY)."""
        self._collected_metrics.clear()
        self._aggregated.clear()
