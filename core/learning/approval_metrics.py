"""Task 3: Observability & Metrics for L5 k=2 Approval System.

Tracks:
- Approval queue depth (pending approvals per skill)
- Approval latency (time from request to operator decision)
- Auto-approval rate (% auto-approved vs. pending)
- Rejection rate (% rejected)
- Revoke count (total revokes)
- Config apply success rate
- Config apply failure count

Integrates with telemetry system for dashboards and alerting.
"""

import logging
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

logger = logging.getLogger(__name__)


@dataclass
class ApprovalMetrics:
    """Collected approval metrics (snapshot)."""

    # Queue metrics
    pending_count_by_skill: Dict[str, int] = field(default_factory=dict)
    total_pending: int = 0

    # Latency metrics
    approval_latencies_ms: List[float] = field(default_factory=list)
    avg_latency_ms: Optional[float] = None
    p50_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None

    # Decision metrics
    auto_approved_count: int = 0
    manual_approved_count: int = 0
    rejected_count: int = 0
    revoked_count: int = 0

    auto_approved_pct: Optional[float] = None
    rejected_pct: Optional[float] = None

    # Config apply metrics
    config_apply_success_count: int = 0
    config_apply_failure_count: int = 0
    config_apply_success_pct: Optional[float] = None

    # Timestamp
    snapshot_timestamp: str = ""


class ApprovalMetricsCollector:
    """
    Collects metrics from L5 k=2 approval gate.

    Responsibilities:
    1. Hook into approval gate events
    2. Track metrics in memory (time-windowed)
    3. Compute aggregates (avg, percentiles, %s)
    4. Export metrics for telemetry system
    """

    def __init__(self, approval_gate, window_hours: int = 24):
        """
        Initialize metrics collector.

        Args:
            approval_gate: OperatorApprovalGate instance to monitor
            window_hours: Time window for metrics (default 24h)
        """
        self.approval_gate = approval_gate
        self.window_hours = window_hours

        # Tracked events (with timestamps for windowing)
        self.approval_requests: List[Dict] = []  # {approval_id, skill_id, timestamp, confidence}
        self.approvals: List[Dict] = []  # {approval_id, skill_id, latency_ms}
        self.rejections: List[Dict] = []  # {approval_id, skill_id}
        self.revokes: List[Dict] = []  # {approval_id, skill_id}
        self.config_applies: List[Dict] = []  # {approval_id, success, error}

    def record_approval_request(self, approval_id: str, skill_id: str, confidence: float, auto_approved: bool) -> None:
        """Record an approval request."""
        self.approval_requests.append({
            "approval_id": approval_id,
            "skill_id": skill_id,
            "timestamp": datetime.utcnow(),
            "confidence": confidence,
            "auto_approved": auto_approved,
        })

    def record_approval(self, approval_id: str, skill_id: str, latency_ms: float) -> None:
        """Record an operator approval."""
        self.approvals.append({
            "approval_id": approval_id,
            "skill_id": skill_id,
            "latency_ms": latency_ms,
            "timestamp": datetime.utcnow(),
        })

    def record_rejection(self, approval_id: str, skill_id: str) -> None:
        """Record an operator rejection."""
        self.rejections.append({
            "approval_id": approval_id,
            "skill_id": skill_id,
            "timestamp": datetime.utcnow(),
        })

    def record_revoke(self, approval_id: str, skill_id: str) -> None:
        """Record an approval revoke."""
        self.revokes.append({
            "approval_id": approval_id,
            "skill_id": skill_id,
            "timestamp": datetime.utcnow(),
        })

    def record_config_apply(self, approval_id: str, skill_id: str, success: bool, error: Optional[str] = None) -> None:
        """Record a config apply attempt."""
        self.config_applies.append({
            "approval_id": approval_id,
            "skill_id": skill_id,
            "success": success,
            "error": error,
            "timestamp": datetime.utcnow(),
        })

    def _prune_old_events(self) -> None:
        """Remove events older than window."""
        cutoff = datetime.utcnow() - timedelta(hours=self.window_hours)

        self.approval_requests = [
            e for e in self.approval_requests if e["timestamp"] > cutoff
        ]
        self.approvals = [e for e in self.approvals if e["timestamp"] > cutoff]
        self.rejections = [e for e in self.rejections if e["timestamp"] > cutoff]
        self.revokes = [e for e in self.revokes if e["timestamp"] > cutoff]
        self.config_applies = [e for e in self.config_applies if e["timestamp"] > cutoff]

    def compute_metrics(self) -> ApprovalMetrics:
        """
        Compute aggregated metrics from collected events.

        Returns:
            ApprovalMetrics snapshot
        """
        self._prune_old_events()

        metrics = ApprovalMetrics(
            snapshot_timestamp=datetime.utcnow().isoformat() + "Z"
        )

        # Queue depth (from approval gate)
        pending = self.approval_gate.get_pending_approvals()
        metrics.total_pending = len(pending)

        # Queue depth by skill
        skill_pending = defaultdict(int)
        for p in pending:
            skill_id = p.scrubbed_alert.skill_id
            skill_pending[skill_id] += 1
        metrics.pending_count_by_skill = dict(skill_pending)

        # Approval latencies
        latencies = [e["latency_ms"] for e in self.approvals]
        if latencies:
            metrics.approval_latencies_ms = latencies
            metrics.avg_latency_ms = statistics.mean(latencies)
            metrics.p50_latency_ms = statistics.median(latencies)
            if len(latencies) >= 20:
                metrics.p95_latency_ms = self._percentile(latencies, 95)

        # Decision counts
        auto_approved = sum(1 for e in self.approval_requests if e["auto_approved"])
        # Count approvals that are NOT in the auto-approved set (issue #7: avoid double-count)
        auto_approved_ids = {r["approval_id"] for r in self.approval_requests if r["auto_approved"]}
        manual_approved = sum(1 for a in self.approvals if a["approval_id"] not in auto_approved_ids)
        metrics.auto_approved_count = auto_approved
        metrics.manual_approved_count = manual_approved
        metrics.rejected_count = len(self.rejections)
        metrics.revoked_count = len(self.revokes)

        # Percentages
        total_decisions = auto_approved + manual_approved + len(self.rejections)
        if total_decisions > 0:
            metrics.auto_approved_pct = (auto_approved / total_decisions) * 100
            metrics.rejected_pct = (len(self.rejections) / total_decisions) * 100

        # Config apply metrics
        successful_applies = sum(1 for e in self.config_applies if e["success"])
        total_applies = len(self.config_applies)
        if total_applies > 0:
            metrics.config_apply_success_count = successful_applies
            metrics.config_apply_failure_count = total_applies - successful_applies
            metrics.config_apply_success_pct = (successful_applies / total_applies) * 100

        return metrics

    @staticmethod
    def _percentile(data: List[float], percentile: int) -> float:
        """Compute percentile of data."""
        sorted_data = sorted(data)
        index = (percentile / 100) * (len(sorted_data) - 1)
        lower = int(index)
        upper = lower + 1
        if upper >= len(sorted_data):
            return sorted_data[lower]
        weight = index - lower
        return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight


class ApprovalMetricsExporter:
    """
    Exports approval metrics to telemetry system.

    Formats metrics for dashboard consumption and alerting.
    """

    def __init__(self, collector: ApprovalMetricsCollector, telemetry_backend=None):
        """
        Initialize exporter.

        Args:
            collector: ApprovalMetricsCollector to get metrics from
            telemetry_backend: Telemetry system backend (optional)
        """
        self.collector = collector
        self.telemetry_backend = telemetry_backend

    def export_as_json(self) -> Dict:
        """
        Export metrics as JSON for API response.

        Returns:
            Dict with all metrics (API-ready)
        """
        metrics = self.collector.compute_metrics()

        return {
            "snapshot_timestamp": metrics.snapshot_timestamp,
            "approval_queue": {
                "total_pending": metrics.total_pending,
                "by_skill": metrics.pending_count_by_skill,
            },
            "approval_latency": {
                "avg_ms": metrics.avg_latency_ms,
                "p50_ms": metrics.p50_latency_ms,
                "p95_ms": metrics.p95_latency_ms,
                "samples": len(metrics.approval_latencies_ms),
            },
            "decisions": {
                "auto_approved": {
                    "count": metrics.auto_approved_count,
                    "percent": metrics.auto_approved_pct,
                },
                "manual_approved": {
                    "count": metrics.manual_approved_count,
                },
                "rejected": {
                    "count": metrics.rejected_count,
                    "percent": metrics.rejected_pct,
                },
                "revoked": {
                    "count": metrics.revoked_count,
                },
            },
            "config_apply": {
                "success": {
                    "count": metrics.config_apply_success_count,
                    "percent": metrics.config_apply_success_pct,
                },
                "failure": {
                    "count": metrics.config_apply_failure_count,
                },
            },
        }

    def export_to_prometheus(self) -> List[str]:
        """
        Export metrics in Prometheus text format.

        Returns:
            List of metric lines (one per metric)
        """
        metrics = self.collector.compute_metrics()
        lines = []

        # Queue metrics
        lines.append(f"l5_k2_approval_queue_pending_total {metrics.total_pending}")
        for skill_id, count in metrics.pending_count_by_skill.items():
            lines.append(f'l5_k2_approval_queue_pending{{skill_id="{skill_id}"}} {count}')

        # Latency metrics
        if metrics.avg_latency_ms is not None:
            lines.append(f"l5_k2_approval_latency_avg_ms {metrics.avg_latency_ms}")
        if metrics.p50_latency_ms is not None:
            lines.append(f"l5_k2_approval_latency_p50_ms {metrics.p50_latency_ms}")
        if metrics.p95_latency_ms is not None:
            lines.append(f"l5_k2_approval_latency_p95_ms {metrics.p95_latency_ms}")

        # Decision metrics
        lines.append(f"l5_k2_approval_auto_approved_total {metrics.auto_approved_count}")
        lines.append(f"l5_k2_approval_manual_approved_total {metrics.manual_approved_count}")
        lines.append(f"l5_k2_approval_rejected_total {metrics.rejected_count}")
        lines.append(f"l5_k2_approval_revoked_total {metrics.revoked_count}")

        # Percentages
        if metrics.auto_approved_pct is not None:
            lines.append(f"l5_k2_approval_auto_approved_percent {metrics.auto_approved_pct}")
        if metrics.rejected_pct is not None:
            lines.append(f"l5_k2_approval_rejected_percent {metrics.rejected_pct}")

        # Config apply metrics
        lines.append(f"l5_k2_config_apply_success_total {metrics.config_apply_success_count}")
        lines.append(f"l5_k2_config_apply_failure_total {metrics.config_apply_failure_count}")
        if metrics.config_apply_success_pct is not None:
            lines.append(f"l5_k2_config_apply_success_percent {metrics.config_apply_success_pct}")

        return lines

    def emit_to_telemetry(self) -> None:
        """Send metrics to telemetry backend."""
        if not self.telemetry_backend:
            return

        try:
            metrics_json = self.export_as_json()
            self.telemetry_backend.emit("l5_k2_approval_metrics", metrics_json)
        except Exception as e:
            logger.error(f"[ApprovalMetrics] Failed to emit to telemetry: {e}")


# ============================================================================
# Integration with FastAPI Routes
# ============================================================================

_global_metrics_collector: Optional[ApprovalMetricsCollector] = None
_global_metrics_exporter: Optional[ApprovalMetricsExporter] = None


def initialize_approval_metrics(approval_gate, telemetry_backend=None) -> ApprovalMetricsExporter:
    """
    Initialize approval metrics system (called from app.py startup).

    Args:
        approval_gate: OperatorApprovalGate instance
        telemetry_backend: Optional telemetry system

    Returns:
        ApprovalMetricsExporter instance
    """
    global _global_metrics_collector, _global_metrics_exporter

    _global_metrics_collector = ApprovalMetricsCollector(approval_gate)
    _global_metrics_exporter = ApprovalMetricsExporter(
        _global_metrics_collector,
        telemetry_backend=telemetry_backend,
    )

    logger.info("[ApprovalMetrics] Initialized approval metrics collection")
    return _global_metrics_exporter


def get_approval_metrics_exporter() -> Optional[ApprovalMetricsExporter]:
    """Get the global metrics exporter."""
    return _global_metrics_exporter


def get_approval_metrics() -> Optional[Dict]:
    """Get current approval metrics (for API endpoint)."""
    if not _global_metrics_exporter:
        return None
    return _global_metrics_exporter.export_as_json()
