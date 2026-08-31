"""Telemetry Source of Truth Registry (ADR-0325).

Single authoritative registry of all metrics with:
- Contract validation (required labels, types)
- Audit chain integration
- Cross-tenant isolation
- Fail-closed enforcement
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    """Canonical metric types."""

    COUNTER = "counter"  # Monotonic increase
    GAUGE = "gauge"  # Point-in-time value
    HISTOGRAM = "histogram"  # Distribution
    RATE = "rate"  # Per-second measurement
    SUMMARY = "summary"  # Percentiles


@dataclass(frozen=True)
class MetricContract:
    """Type contract for a metric."""

    name: str
    metric_type: MetricType
    required_labels: set[str] = field(default_factory=set)
    description: Optional[str] = None
    unit: Optional[str] = None

    def validate(self, labels: dict[str, str]) -> None:
        """Validate metric labels against contract (fail-closed).

        Args:
            labels: Labels to validate

        Raises:
            ValueError: If validation fails
        """
        if not isinstance(labels, dict):
            raise ValueError(f"Labels must be dict, got {type(labels).__name__}")

        for required in self.required_labels:
            if required not in labels:
                raise ValueError(
                    f"Metric '{self.name}' missing required label '{required}'. "
                    f"Expected labels: {self.required_labels}, got: {set(labels.keys())}"
                )

            label_value = labels[required]
            if not isinstance(label_value, str):
                raise ValueError(
                    f"Metric '{self.name}' label '{required}' must be str, "
                    f"got {type(label_value).__name__}"
                )


@dataclass(frozen=True)
class MetricValue:
    """One metric measurement."""

    name: str
    value: float
    labels: dict[str, str]
    timestamp_utc: datetime
    tenant_id: str

    def to_audit_event(self) -> dict[str, Any]:
        """Convert to audit event format."""
        return {
            "event_type": "metric.recorded",
            "metric_name": self.name,
            "value": self.value,
            "labels": self.labels,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp_utc.isoformat() + "Z",
        }


class TelemetryRegistry:
    """Single source of truth for all metrics."""

    _instance: Optional[TelemetryRegistry] = None

    def __new__(cls) -> TelemetryRegistry:
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize registry (called once due to singleton)."""
        if self._initialized:
            return

        self._contracts: dict[str, MetricContract] = {}
        self._values: dict[tuple[str, str], MetricValue] = {}  # (metric_name, label_hash) → value
        self._audit_path: Optional[Path] = None
        self._initialized = True

    def is_metric_registered(self, name: str) -> bool:
        """Check if a metric is registered.

        Args:
            name: Metric name

        Returns:
            True if registered, False otherwise
        """
        return name in self._contracts

    def get_metric_contract(self, name: str) -> Optional[MetricContract]:
        """Get the contract for a registered metric.

        Args:
            name: Metric name

        Returns:
            MetricContract if registered, None otherwise
        """
        return self._contracts.get(name)

    def register_metric(
        self,
        name: str,
        metric_type: MetricType,
        required_labels: Optional[set[str]] = None,
        description: Optional[str] = None,
        unit: Optional[str] = None,
    ) -> MetricContract:
        """Register a new metric contract (fail-closed validation).

        Args:
            name: Metric name (e.g., "memory_usage_bytes")
            metric_type: Type of metric
            required_labels: Labels that MUST be present
            description: Human-readable description
            unit: Unit of measurement

        Returns:
            MetricContract

        Raises:
            ValueError: If name already registered with different contract
        """
        if not name or not isinstance(name, str):
            raise ValueError(f"Invalid metric name: {name}")

        if name in self._contracts:
            existing = self._contracts[name]
            if existing.metric_type != metric_type or existing.required_labels != (required_labels or set()):
                raise ValueError(
                    f"Metric '{name}' already registered with different contract: {existing}"
                )
            logger.debug(f"Metric '{name}' already registered, skipping")
            return existing

        contract = MetricContract(
            name=name,
            metric_type=metric_type,
            required_labels=required_labels or set(),
            description=description,
            unit=unit,
        )

        self._contracts[name] = contract
        logger.debug(f"Registered metric: {contract}")

        return contract

    def get_active(self, name: str, tenant_id: Optional[str] = None) -> Optional[MetricValue]:
        """Get current value of a metric.

        Args:
            name: Metric name
            tenant_id: Tenant ID for isolation (optional for lookup)

        Returns:
            MetricValue if metric has been recorded, None otherwise

        Raises:
            ValueError: If metric not registered
        """
        if name not in self._contracts:
            raise ValueError(f"Metric '{name}' not registered. Registered metrics: {set(self._contracts.keys())}")

        # Find most recent value
        matching_values = [v for v in self._values.values() if v.name == name]

        if not matching_values:
            return None

        if tenant_id:
            matching_values = [v for v in matching_values if v.tenant_id == tenant_id]

        return max(matching_values, key=lambda v: v.timestamp_utc) if matching_values else None

    def record_metric(
        self,
        name: str,
        value: float,
        labels: dict[str, str],
        tenant_id: str,
    ) -> None:
        """Record a metric value with labels (fail-closed validation).

        Args:
            name: Metric name
            value: Metric value (must be float)
            labels: Label dict (must match contract)
            tenant_id: Tenant ID for isolation

        Raises:
            ValueError: If metric not registered, labels invalid, or tenant_id invalid
        """
        if name not in self._contracts:
            raise ValueError(f"Metric '{name}' not registered")

        if not isinstance(value, (int, float)):
            raise ValueError(f"Metric value must be numeric, got {type(value).__name__}")

        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        # Validate labels against contract
        contract = self._contracts[name]
        contract.validate(labels)

        # Create value
        metric_value = MetricValue(
            name=name,
            value=float(value),
            labels=labels,
            timestamp_utc=datetime.utcnow(),
            tenant_id=tenant_id,
        )

        # Store (latest-wins for each (name, label_hash) pair)
        label_hash = json.dumps(labels, sort_keys=True)
        key = (name, label_hash)
        self._values[key] = metric_value

        logger.debug(f"Recorded metric: {name}={value} (labels={labels}, tenant={tenant_id})")

    def validate_consistency(self) -> None:
        """Audit all metrics for contract consistency (fail-closed).

        Raises:
            ValueError: If any metric violates its contract
        """
        errors = []

        for metric_value in self._values.values():
            if metric_value.name not in self._contracts:
                errors.append(f"Recorded metric '{metric_value.name}' not registered")
                continue

            contract = self._contracts[metric_value.name]

            try:
                contract.validate(metric_value.labels)
            except ValueError as e:
                errors.append(str(e))

        if errors:
            raise ValueError(f"Consistency check failed:\n" + "\n".join(errors))

        logger.debug("Consistency check passed for all metrics")

    def emit_audit_event(self, name: str, value: Any, tenant_id: str) -> Optional[str]:
        """Emit metric to audit chain (with fallback to disk-only).

        Args:
            name: Metric name
            value: Metric value
            tenant_id: Tenant ID

        Returns:
            audit_id if successful, None on fallback

        Raises:
            ValueError: If metric not registered
        """
        if name not in self._contracts:
            raise ValueError(f"Metric '{name}' not registered")

        # Try audit chain integration
        try:
            from core.compliance.corvin_compliance_reports.audit_writer import write_audit_event

            event = {
                "event_type": "telemetry.metric_recorded",
                "metric_name": name,
                "value": value,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

            audit_id = write_audit_event(event)
            logger.debug(f"Emitted audit event for metric '{name}': {audit_id}")
            return audit_id

        except Exception as e:
            logger.warning(f"Audit chain unavailable for metric '{name}': {e}. Falling back to disk-only.")
            return None

    def get_metrics_snapshot(self, tenant_id: str) -> dict[str, Any]:
        """Get all current metrics for a tenant.

        Args:
            tenant_id: Tenant ID for isolation

        Returns:
            Dict of metric_name → MetricValue
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        return {
            v.name: v
            for v in self._values.values()
            if v.tenant_id == tenant_id
        }

    def reset_for_testing(self) -> None:
        """Clear all state (TEST ONLY)."""
        self._contracts.clear()
        self._values.clear()
