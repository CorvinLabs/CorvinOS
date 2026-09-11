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

        return PrometheusMetrics(
            skill_generation_count=skill_gen_count,
            weight_updates_total=weight_update_count,
            feedback_signals_total=feedback_count,
            daemon_convergence_status=daemon_convergence_status or 0.0,
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
            prefix: Metric name prefix

        Returns:
            Prometheus-format text (for /metrics endpoint)
        """
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
