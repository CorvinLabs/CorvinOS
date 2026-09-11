"""Phase 4 API Endpoints for Learning Dashboard.

GET  /api/v1/learning/skills              — skill generation history
GET  /api/v1/learning/weights              — weight updates + history
GET  /api/v1/learning/feedback             — feedback signals + impact
GET  /api/v1/learning/audit                — audit trail (GDPR export)
GET  /api/v1/learning/convergence          — daemon convergence status
GET  /api/v1/learning/metrics              — Prometheus metrics
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .trail import AuditTrail
from .reporter import ComplianceReporter
from .prometheus import PrometheusExporter


# === Request/Response Models ===


class SkillGenerationRecord(BaseModel):
    """Skill generation event for dashboard."""
    skill_id: str
    skill_name: str
    timestamp: str
    loss_before: float = Field(..., ge=0, le=1)
    loss_after: float = Field(..., ge=0, le=1)
    improvement_pct: float
    phase_count: int
    source: Optional[str] = None  # e.g., "user_request", "daemon_regen"


class WeightUpdate(BaseModel):
    """Weight update event for dashboard."""
    timestamp: str
    source_id: str  # e.g., "memory:tier2"
    weight_before: float
    weight_after: float
    change_pct: float
    reason: str  # e.g., "positive_feedback", "convergence_adjustment"


class FeedbackSignal(BaseModel):
    """User feedback signal."""
    timestamp: str
    skill_id: str
    signal: str  # "positive", "negative", "neutral"
    impact_on_loss: float  # change in loss after feedback


class ConvergenceStatus(BaseModel):
    """Daemon convergence metrics."""
    is_converged: bool
    confidence: float = Field(..., ge=0, le=1)  # 0-1 confidence in convergence
    samples_processed: int
    last_update: str
    estimated_weeks_to_stable: float  # projection


class AuditExport(BaseModel):
    """GDPR-compliant audit trail export."""
    export_timestamp: str
    event_count: int
    retention_policy_days: int
    pii_redacted: bool
    user_ids_masked: bool
    jsonl_data: str  # JSONL format


# === API Implementation ===


class LearningDashboardAPI:
    """Backend API for learning dashboard."""

    def __init__(
        self,
        audit_trail: AuditTrail,
        compliance_reporter: ComplianceReporter,
        prometheus_exporter: PrometheusExporter,
    ):
        """
        Initialize dashboard API.

        Args:
            audit_trail: AuditTrail instance
            compliance_reporter: ComplianceReporter instance
            prometheus_exporter: PrometheusExporter instance
        """
        self.audit_trail = audit_trail
        self.compliance_reporter = compliance_reporter
        self.prometheus_exporter = prometheus_exporter

    def get_skill_generation_history(
        self,
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> list[SkillGenerationRecord]:
        """Fetch skill generation history for dashboard timeline."""
        events = self.audit_trail.query_events(
            event_type="skill_generated",
            since=since,
            limit=limit,
        )

        records = []
        for event in events:
            payload = event.payload
            records.append(SkillGenerationRecord(
                skill_id=event.skill_id or "",
                skill_name=payload.get("skill_name", f"skill_{event.skill_id[:8]}"),
                timestamp=event.timestamp,
                loss_before=float(payload.get("loss_before", 0.5)),
                loss_after=float(payload.get("loss_after", 0.5)),
                improvement_pct=float(payload.get("improvement_pct", 0)),
                phase_count=int(payload.get("phase_count", 0)),
                source=payload.get("source"),
            ))

        return sorted(records, key=lambda r: r.timestamp, reverse=True)

    def get_weight_updates(
        self,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> list[WeightUpdate]:
        """Fetch weight update history for line chart."""
        events = self.audit_trail.query_events(
            event_type="weight_updated",
            since=since,
            limit=limit,
        )

        updates = []
        for event in events:
            payload = event.payload
            updates.append(WeightUpdate(
                timestamp=event.timestamp,
                source_id=payload.get("source_id", "unknown"),
                weight_before=float(payload.get("weight_before", 0.5)),
                weight_after=float(payload.get("weight_after", 0.5)),
                change_pct=float(payload.get("change_pct", 0)),
                reason=payload.get("reason", ""),
            ))

        return sorted(updates, key=lambda u: u.timestamp, reverse=True)

    def get_feedback_impact(
        self,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> list[FeedbackSignal]:
        """Fetch user feedback signals and their impact on loss."""
        events = self.audit_trail.query_events(
            event_type="feedback_received",
            since=since,
            limit=limit,
        )

        signals = []
        for event in events:
            payload = event.payload
            signals.append(FeedbackSignal(
                timestamp=event.timestamp,
                skill_id=event.skill_id or "unknown",
                signal=payload.get("signal", "neutral"),
                impact_on_loss=float(payload.get("impact_on_loss", 0)),
            ))

        return sorted(signals, key=lambda s: s.timestamp, reverse=True)

    def get_convergence_status(
        self,
        daemon_convergence_value: Optional[float] = None,
    ) -> ConvergenceStatus:
        """Fetch daemon convergence metrics."""
        # In production, this would query the daemon's actual status
        # For now, return a reasonable default based on audit trail

        feedback_events = self.audit_trail.query_events(
            event_type="feedback_received",
            limit=999999,
        )

        # Convergence heuristic: if feedback impact is decreasing, converged
        samples_processed = len(feedback_events)

        # Dummy convergence estimate
        is_converged = samples_processed > 100
        confidence = min(samples_processed / 500, 1.0) if samples_processed > 0 else 0.0

        return ConvergenceStatus(
            is_converged=is_converged,
            confidence=confidence,
            samples_processed=samples_processed,
            last_update=datetime.now(timezone.utc).isoformat(),
            estimated_weeks_to_stable=max(1, 4 - (samples_processed / 250)),
        )

    def get_audit_export(
        self,
        redact: bool = True,
        since_days: int = 30,
    ) -> AuditExport:
        """Export audit trail (GDPR-compliant)."""
        since = datetime.now(timezone.utc) - timedelta(days=since_days)

        jsonl_data = self.compliance_reporter.export_for_compliance(
            since=since,
            redact=redact,
        )

        event_count = len(jsonl_data.strip().split('\n')) if jsonl_data.strip() else 0

        return AuditExport(
            export_timestamp=datetime.now(timezone.utc).isoformat(),
            event_count=event_count,
            retention_policy_days=self.compliance_reporter.retention_days,
            pii_redacted=redact,
            user_ids_masked=redact,
            jsonl_data=jsonl_data,
        )

    def get_prometheus_metrics(
        self,
        daemon_convergence_value: Optional[float] = None,
    ) -> str:
        """Export Prometheus metrics."""
        return self.prometheus_exporter.export_text_format(
            daemon_convergence_status=daemon_convergence_value
        )
