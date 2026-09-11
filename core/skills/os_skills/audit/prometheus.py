"""Prometheus Metrics Export for DataHub Creator Monitoring.

Metrics:
- datahub_skill_generation_count: total skills created
- datahub_weight_updates_total: total weight changes
- datahub_feedback_signals_total: total feedback received
- datahub_daemon_convergence_status: 1=converged, 0=learning
- datahub_audit_chain_height: number of events
- datahub_audit_chain_verified: 1=valid, 0=broken
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

from .trail import AuditTrail


@dataclass
class PrometheusMetrics:
    """Prometheus-compatible metrics."""

    skill_generation_count: int = 0
    weight_updates_total: int = 0
    feedback_signals_total: int = 0
    daemon_convergence_status: float = 0.0  # 1.0 = converged
    audit_chain_height: int = 0
    audit_chain_verified: float = 0.0  # 1.0 = valid


class PrometheusExporter:
    """Export audit trail metrics in Prometheus format."""

    def __init__(self, audit_trail: AuditTrail):
        """
        Initialize exporter.

        Args:
            audit_trail: AuditTrail instance to export from
        """
        self.audit_trail = audit_trail

    @staticmethod
    def _validate_metric_value(metric_name: str, value: float) -> bool:
        """
        Validate metric values (fail-closed).

        Args:
            metric_name: Name of the metric
            value: Value to validate

        Returns:
            True if valid, raises ValueError otherwise

        Raises:
            ValueError: If value is invalid (negative counter, NaN, Infinity, etc.)
        """
        # Check for NaN or Infinity
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"Invalid metric value for {metric_name}: {value} (NaN or Infinity)")

        # Convergence status must be in [0, 1]
        if "convergence" in metric_name.lower():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Convergence metric {metric_name} must be in [0, 1], got {value}")

        # Counters must be >= 0
        if "total" in metric_name.lower() or "count" in metric_name.lower():
            if value < 0:
                raise ValueError(f"Counter metric {metric_name} must be >= 0, got {value}")

        return True

    @staticmethod
    def _validate_metric_prefix(prefix: str) -> bool:
        """
        Validate metric name prefix (alphanumeric + underscore only).

        Args:
            prefix: Prefix to validate

        Returns:
            True if valid, raises ValueError otherwise
        """
        if not re.match(r'^[a-z_]+$', prefix):
            raise ValueError(
                f"Metric prefix must match [a-z_]+, got '{prefix}' "
                "(only lowercase letters and underscores allowed)"
            )
        return True

    def collect_metrics(
        self,
        daemon_convergence_status: Optional[float] = None,
    ) -> PrometheusMetrics:
        """
        Collect metrics from audit trail.

        Args:
            daemon_convergence_status: Operator-supplied convergence status (0-1)

        Returns:
            PrometheusMetrics object

        Raises:
            ValueError: If any metric value is invalid
        """
        # Count events by type
        events = self.audit_trail.query_events(limit=999999)

        skill_gen_count = 0
        weight_update_count = 0
        feedback_count = 0

        for event in events:
            if event.event_type == "skill_generated":
                skill_gen_count += 1
            elif event.event_type == "weight_updated":
                weight_update_count += 1
            elif event.event_type == "feedback_received":
                feedback_count += 1

        # Check chain integrity
        is_valid, _ = self.audit_trail.verify_integrity()

        # Validate convergence status if provided
        if daemon_convergence_status is not None:
            self._validate_metric_value("daemon_convergence_status", daemon_convergence_status)
        else:
            daemon_convergence_status = 0.0

        # Validate counter values
        self._validate_metric_value("skill_generation_count", float(skill_gen_count))
        self._validate_metric_value("weight_updates_total", float(weight_update_count))
        self._validate_metric_value("feedback_signals_total", float(feedback_count))

        return PrometheusMetrics(
            skill_generation_count=skill_gen_count,
            weight_updates_total=weight_update_count,
            feedback_signals_total=feedback_count,
            daemon_convergence_status=daemon_convergence_status,
            audit_chain_height=len(events),
            audit_chain_verified=1.0 if is_valid else 0.0,
        )

    def export_text_format(
        self,
        daemon_convergence_status: Optional[float] = None,
        prefix: str = "datahub_",
    ) -> str:
        """
        Export metrics in Prometheus text format.

        Args:
            daemon_convergence_status: Optional convergence status
            prefix: Metric name prefix (must match [a-z_]+)

        Returns:
            Prometheus-format text (for /metrics endpoint)

        Raises:
            ValueError: If prefix is invalid
        """
        # Validate prefix
        self._validate_metric_prefix(prefix.rstrip('_'))

        metrics = self.collect_metrics(daemon_convergence_status)

        lines = [
            f"# HELP {prefix}skill_generation_count Total skills created",
            f"# TYPE {prefix}skill_generation_count counter",
            f"{prefix}skill_generation_count {metrics.skill_generation_count}",
            "",
            f"# HELP {prefix}weight_updates_total Total weight changes",
            f"# TYPE {prefix}weight_updates_total counter",
            f"{prefix}weight_updates_total {metrics.weight_updates_total}",
            "",
            f"# HELP {prefix}feedback_signals_total Total feedback received",
            f"# TYPE {prefix}feedback_signals_total counter",
            f"{prefix}feedback_signals_total {metrics.feedback_signals_total}",
            "",
            f"# HELP {prefix}daemon_convergence_status Learning daemon convergence (0-1)",
            f"# TYPE {prefix}daemon_convergence_status gauge",
            f"{prefix}daemon_convergence_status {metrics.daemon_convergence_status}",
            "",
            f"# HELP {prefix}audit_chain_height Number of events in audit trail",
            f"# TYPE {prefix}audit_chain_height gauge",
            f"{prefix}audit_chain_height {metrics.audit_chain_height}",
            "",
            f"# HELP {prefix}audit_chain_verified Chain integrity status (0-1)",
            f"# TYPE {prefix}audit_chain_verified gauge",
            f"{prefix}audit_chain_verified {metrics.audit_chain_verified}",
        ]

        return '\n'.join(lines)
