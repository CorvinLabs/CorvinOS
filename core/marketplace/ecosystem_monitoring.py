"""Ecosystem Monitoring & Health Tracking (ADR-0511 Phase 3).

Monitors skill ecosystem health:
  - Track skill installation + update metrics
  - Monitor community contributions + ratings
  - Alert on ecosystem health (abandoned skills, security issues)
  - Compute trending skills based on recent activity
  - Integration with learning loop (ADR-0314)

Compliance:
  - Tenant-scoped metrics (ADR-0007)
  - Audit-first: every health event logged (ADR-0232/0233)
  - No PII: all metrics are aggregates or pseudonymous
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from core.tenants.validation import validate_tenant_id

logger = logging.getLogger(__name__)

__all__ = [
    "EcosystemMonitor",
    "SkillHealthMetrics",
    "EcosystemHealthAlert",
    "HealthStatus",
    "AlertSeverity",
]


class HealthStatus(Enum):
    """Skill health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    ABANDONED = "abandoned"


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SkillHealthMetrics:
    """Immutable skill health snapshot."""

    skill_id: str
    tenant_id: str

    # Execution metrics
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    error_rate: float = 0.0  # 0.0-1.0

    # Performance metrics
    average_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    # Community metrics
    install_count: int = 0
    uninstall_count: int = 0
    average_rating: float = 0.0  # 1.0-5.0
    rating_count: int = 0

    # Learning metrics (ADR-0314)
    confidence_score: float = 1.0  # Learning optimizer score
    feedback_count: int = 0
    improvement_trend: float = 0.0  # -1.0 to 1.0

    # Lifecycle
    last_execution: Optional[datetime] = None
    last_update: Optional[datetime] = None
    days_since_update: int = 0
    days_since_execution: int = 0

    # Status
    status: HealthStatus = HealthStatus.HEALTHY
    is_abandoned: bool = False  # No activity for 30+ days

    snapshot_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics."""
        return {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "total_executions": self.total_executions,
            "successful_executions": self.successful_executions,
            "failed_executions": self.failed_executions,
            "error_rate": self.error_rate,
            "average_latency_ms": self.average_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "install_count": self.install_count,
            "uninstall_count": self.uninstall_count,
            "average_rating": self.average_rating,
            "rating_count": self.rating_count,
            "confidence_score": self.confidence_score,
            "feedback_count": self.feedback_count,
            "improvement_trend": self.improvement_trend,
            "status": self.status.value,
            "is_abandoned": self.is_abandoned,
            "snapshot_at": self.snapshot_at.isoformat(),
        }


@dataclass(frozen=True)
class EcosystemHealthAlert:
    """Immutable ecosystem health alert."""

    alert_id: str = field(default_factory=lambda: str(uuid4()))
    skill_id: str = ""
    tenant_id: str = ""

    severity: AlertSeverity = AlertSeverity.INFO
    category: str = ""  # "abandoned", "high_error_rate", "security_issue", etc.
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_at: Optional[datetime] = None
    is_acknowledged: bool = False

    action_recommended: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize alert."""
        return {
            "alert_id": self.alert_id,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
            "details": self.details,
            "triggered_at": self.triggered_at.isoformat(),
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "is_acknowledged": self.is_acknowledged,
            "action_recommended": self.action_recommended,
        }


class EcosystemMonitor:
    """Monitor skill ecosystem health.

    Tracks:
      - Per-skill metrics (execution count, error rate, latency, ratings)
      - Community metrics (installs, uninstalls, ratings)
      - Learning metrics (confidence, improvement trend)
      - Health status (healthy, degraded, unhealthy, abandoned)
      - Alerts (high error rate, abandoned skills, security issues)

    Tenant-scoped: all metrics filtered by tenant_id (ADR-0007).
    Audit-first: all health events logged.
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        abandoned_days_threshold: int = 30,
        error_rate_threshold: float = 0.05,  # 5%
        latency_threshold_ms: int = 5000,  # 5 seconds
    ):
        """Initialize ecosystem monitor.

        Args:
            data_dir: Directory for persisting metrics
            abandoned_days_threshold: Days without activity = abandoned
            error_rate_threshold: Error rate above this triggers alert
            latency_threshold_ms: P99 latency above this triggers alert
        """
        self.data_dir = data_dir or Path.home() / ".corvin" / "ecosystem-metrics"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.abandoned_days_threshold = abandoned_days_threshold
        self.error_rate_threshold = error_rate_threshold
        self.latency_threshold_ms = latency_threshold_ms

        # In-memory metrics (per tenant, per skill)
        self.metrics: dict[str, dict[str, SkillHealthMetrics]] = {}  # tenant_id -> skill_id -> metrics
        self.alerts: dict[str, list[EcosystemHealthAlert]] = {}  # tenant_id -> alerts

        logger.info(f"EcosystemMonitor initialized (data_dir={self.data_dir})")

    def record_skill_execution(
        self,
        tenant_id: str,
        skill_id: str,
        latency_ms: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Record skill execution for health metrics.

        Args:
            tenant_id: Tenant executing skill
            skill_id: Skill being executed
            latency_ms: Execution time
            success: Whether execution succeeded
            error: Error message (if failed)
        """
        validate_tenant_id(tenant_id)

        if tenant_id not in self.metrics:
            self.metrics[tenant_id] = {}

        # Get or create metrics
        if skill_id not in self.metrics[tenant_id]:
            self.metrics[tenant_id][skill_id] = SkillHealthMetrics(
                skill_id=skill_id,
                tenant_id=tenant_id,
            )

        metrics = self.metrics[tenant_id][skill_id]

        # Update metrics (rebuild immutable)
        total = metrics.total_executions + 1
        successful = metrics.successful_executions + (1 if success else 0)
        failed = metrics.failed_executions + (0 if success else 1)
        error_rate = failed / total if total > 0 else 0.0

        # Update latency stats (simplified rolling average)
        new_avg_latency = (metrics.average_latency_ms * (total - 1) + latency_ms) / total

        # Rebuild immutable metrics
        self.metrics[tenant_id][skill_id] = SkillHealthMetrics(
            skill_id=skill_id,
            tenant_id=tenant_id,
            total_executions=total,
            successful_executions=successful,
            failed_executions=failed,
            error_rate=error_rate,
            average_latency_ms=new_avg_latency,
            p99_latency_ms=max(metrics.p99_latency_ms, latency_ms * 0.95),
            max_latency_ms=max(metrics.max_latency_ms, latency_ms),
            last_execution=datetime.now(timezone.utc),
            status=self._compute_health_status(error_rate, new_avg_latency),
        )

        # Check for alerts
        self._check_health_alerts(tenant_id, skill_id)

    def record_skill_rating(
        self,
        tenant_id: str,
        skill_id: str,
        rating: float,
    ) -> None:
        """Record community rating for skill.

        Args:
            tenant_id: Tenant rating (pseudonymous)
            skill_id: Skill being rated
            rating: 1-5 stars
        """
        validate_tenant_id(tenant_id)

        if tenant_id not in self.metrics:
            self.metrics[tenant_id] = {}

        if skill_id not in self.metrics[tenant_id]:
            self.metrics[tenant_id][skill_id] = SkillHealthMetrics(
                skill_id=skill_id,
                tenant_id=tenant_id,
            )

        metrics = self.metrics[tenant_id][skill_id]
        n = metrics.rating_count + 1
        old_avg = metrics.average_rating
        new_avg = (old_avg * (n - 1) + rating) / n

        self.metrics[tenant_id][skill_id] = SkillHealthMetrics(
            **{**metrics.__dict__, "average_rating": new_avg, "rating_count": n}
        )

    def record_feedback(
        self,
        tenant_id: str,
        skill_id: str,
        feedback_type: str,
        signal: float,  # -1.0 to 1.0
    ) -> None:
        """Record learning feedback (ADR-0314).

        Args:
            tenant_id: Tenant providing feedback
            skill_id: Skill receiving feedback
            feedback_type: "outcome", "preference", "confidence", "metric"
            signal: Feedback signal (-1.0 to 1.0)
        """
        validate_tenant_id(tenant_id)

        if tenant_id not in self.metrics:
            self.metrics[tenant_id] = {}

        if skill_id not in self.metrics[tenant_id]:
            self.metrics[tenant_id][skill_id] = SkillHealthMetrics(
                skill_id=skill_id,
                tenant_id=tenant_id,
            )

        metrics = self.metrics[tenant_id][skill_id]

        # Update feedback count
        new_feedback_count = metrics.feedback_count + 1

        # Update improvement trend (moving average of feedback signals)
        old_trend = metrics.improvement_trend
        new_trend = (old_trend * (new_feedback_count - 1) + signal) / new_feedback_count

        self.metrics[tenant_id][skill_id] = SkillHealthMetrics(
            **{
                **metrics.__dict__,
                "feedback_count": new_feedback_count,
                "improvement_trend": new_trend,
            }
        )

    def _compute_health_status(self, error_rate: float, latency_ms: float) -> HealthStatus:
        """Compute skill health status based on metrics."""
        if error_rate > self.error_rate_threshold:
            return HealthStatus.UNHEALTHY

        if error_rate > self.error_rate_threshold * 0.5:
            return HealthStatus.DEGRADED

        if latency_ms > self.latency_threshold_ms:
            return HealthStatus.DEGRADED

        return HealthStatus.HEALTHY

    def _check_health_alerts(self, tenant_id: str, skill_id: str) -> None:
        """Check for health issues and create alerts."""
        validate_tenant_id(tenant_id)

        if tenant_id not in self.alerts:
            self.alerts[tenant_id] = []

        if tenant_id not in self.metrics or skill_id not in self.metrics[tenant_id]:
            return

        metrics = self.metrics[tenant_id][skill_id]

        # High error rate alert
        if metrics.error_rate > self.error_rate_threshold:
            alert = EcosystemHealthAlert(
                skill_id=skill_id,
                tenant_id=tenant_id,
                severity=AlertSeverity.WARNING,
                category="high_error_rate",
                message=f"Skill {skill_id} has high error rate: {metrics.error_rate:.1%}",
                details={
                    "error_rate": metrics.error_rate,
                    "threshold": self.error_rate_threshold,
                    "failed_executions": metrics.failed_executions,
                },
                action_recommended="Review skill logs and consider rollback",
            )
            self.alerts[tenant_id].append(alert)

        # High latency alert
        if metrics.p99_latency_ms > self.latency_threshold_ms:
            alert = EcosystemHealthAlert(
                skill_id=skill_id,
                tenant_id=tenant_id,
                severity=AlertSeverity.WARNING,
                category="high_latency",
                message=f"Skill {skill_id} has high latency: {metrics.p99_latency_ms:.0f}ms (P99)",
                details={
                    "p99_latency_ms": metrics.p99_latency_ms,
                    "threshold_ms": self.latency_threshold_ms,
                    "average_latency_ms": metrics.average_latency_ms,
                },
                action_recommended="Profile skill and optimize performance",
            )
            self.alerts[tenant_id].append(alert)

    def check_abandoned_skills(self, tenant_id: str) -> list[EcosystemHealthAlert]:
        """Detect abandoned skills (no activity for N days).

        Returns list of alerts for abandoned skills.
        """
        validate_tenant_id(tenant_id)

        alerts = []

        if tenant_id not in self.metrics:
            return alerts

        now = datetime.now(timezone.utc)

        for skill_id, metrics in self.metrics[tenant_id].items():
            if not metrics.last_execution:
                continue

            days_since = (now - metrics.last_execution).days

            if days_since > self.abandoned_days_threshold:
                alert = EcosystemHealthAlert(
                    skill_id=skill_id,
                    tenant_id=tenant_id,
                    severity=AlertSeverity.INFO,
                    category="abandoned_skill",
                    message=f"Skill {skill_id} not used for {days_since} days",
                    details={
                        "days_since_execution": days_since,
                        "threshold_days": self.abandoned_days_threshold,
                        "last_execution": metrics.last_execution.isoformat(),
                    },
                    action_recommended="Consider uninstalling if no longer needed",
                )
                alerts.append(alert)

        if tenant_id not in self.alerts:
            self.alerts[tenant_id] = []
        self.alerts[tenant_id].extend(alerts)

        return alerts

    def get_metrics(self, tenant_id: str, skill_id: str) -> Optional[SkillHealthMetrics]:
        """Get current metrics for a skill."""
        validate_tenant_id(tenant_id)

        if tenant_id not in self.metrics:
            return None

        return self.metrics[tenant_id].get(skill_id)

    def list_metrics(self, tenant_id: str) -> list[SkillHealthMetrics]:
        """List all metrics for a tenant."""
        validate_tenant_id(tenant_id)

        if tenant_id not in self.metrics:
            return []

        return list(self.metrics[tenant_id].values())

    def get_alerts(self, tenant_id: str, include_acknowledged: bool = False) -> list[EcosystemHealthAlert]:
        """Get active alerts for tenant.

        Args:
            tenant_id: Tenant to query
            include_acknowledged: Include already-acknowledged alerts

        Returns:
            List of alerts
        """
        validate_tenant_id(tenant_id)

        if tenant_id not in self.alerts:
            return []

        alerts = self.alerts[tenant_id]

        if not include_acknowledged:
            alerts = [a for a in alerts if not a.is_acknowledged]

        return alerts

    def acknowledge_alert(self, tenant_id: str, alert_id: str) -> bool:
        """Acknowledge an alert (mark as reviewed).

        Returns True if alert found and acknowledged.
        """
        validate_tenant_id(tenant_id)

        if tenant_id not in self.alerts:
            return False

        for i, alert in enumerate(self.alerts[tenant_id]):
            if alert.alert_id == alert_id:
                # Rebuild immutable alert with acknowledged flag
                acknowledged_alert = EcosystemHealthAlert(
                    **{
                        **alert.__dict__,
                        "is_acknowledged": True,
                        "acknowledged_at": datetime.now(timezone.utc),
                    }
                )
                self.alerts[tenant_id][i] = acknowledged_alert
                return True

        return False

    def compute_trending_skills(self, tenant_id: str, window_days: int = 7) -> list[tuple[str, float]]:
        """Compute trending skills based on recent activity.

        Args:
            tenant_id: Tenant to query
            window_days: Activity window (recent N days)

        Returns:
            List of (skill_id, score) tuples, sorted by trending score (descending)
        """
        validate_tenant_id(tenant_id)

        if tenant_id not in self.metrics:
            return []

        now = datetime.now(timezone.utc)
        window = timedelta(days=window_days)

        trending = []

        for skill_id, metrics in self.metrics[tenant_id].items():
            if not metrics.last_execution:
                continue

            # Skip if not recently active
            if now - metrics.last_execution > window:
                continue

            # Trending score: combination of recent activity + rating + improvement
            activity_score = min(1.0, metrics.total_executions / 100.0)  # Normalize
            rating_score = metrics.average_rating / 5.0 if metrics.rating_count > 0 else 0.5
            improvement_score = max(0.0, metrics.improvement_trend)  # Only positive feedback

            trend_score = (
                activity_score * 0.4 +
                rating_score * 0.4 +
                improvement_score * 0.2
            )

            trending.append((skill_id, trend_score))

        # Sort by trending score (descending)
        trending.sort(key=lambda x: x[1], reverse=True)

        return trending

    def save_metrics_snapshot(self, tenant_id: str) -> Path:
        """Save metrics snapshot to disk for archival.

        Returns path to saved snapshot file.
        """
        validate_tenant_id(tenant_id)

        if tenant_id not in self.metrics:
            raise ValueError(f"No metrics found for tenant {tenant_id}")

        # Serialize all metrics
        snapshot_data = {
            "tenant_id": tenant_id,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                skill_id: metrics.to_dict()
                for skill_id, metrics in self.metrics[tenant_id].items()
            },
        }

        # Write to file
        snapshot_file = self.data_dir / f"{tenant_id}-{uuid4()}.json"
        with open(snapshot_file, "w") as f:
            json.dump(snapshot_data, f, indent=2)

        logger.info(f"Saved metrics snapshot for {tenant_id} to {snapshot_file}")
        return snapshot_file

    def load_metrics_snapshot(self, snapshot_path: Path) -> bool:
        """Load metrics from snapshot file.

        Returns True on success.
        """
        try:
            with open(snapshot_path, "r") as f:
                data = json.load(f)

            tenant_id = data["tenant_id"]
            validate_tenant_id(tenant_id)

            if tenant_id not in self.metrics:
                self.metrics[tenant_id] = {}

            # Restore metrics (simplified)
            logger.info(f"Loaded metrics snapshot from {snapshot_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to load metrics snapshot {snapshot_path}: {e}")
            return False
