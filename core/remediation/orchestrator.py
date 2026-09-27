"""
Phase 5: Remediation Orchestrator

Coordinates safe auto-remediation and approval workflows.
Handles state machine, multi-drift scenarios, and rollback coordination.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

Honesty rules (2026-09-27): a drift is REMEDIATED only when a fix executor
reported a verified SUCCESS — none exists yet, so today nothing reaches
REMEDIATED. A high-risk drift is never blocked on: ``process_drift`` files the
approval request and returns ``AWAITING_APPROVAL`` immediately (it used to
poll for up to 60 s with ``time.sleep``), and ``resume_after_decision`` picks
the request up once an operator decided. An APPROVED high-risk drift is not a
fixed one: without a high-risk executor it ends FAILED (``not_implemented``),
never REMEDIATED. Every lifecycle event is written to the tenant's core chain.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from .drift_categories import DriftCategory, DriftCategorizer, RiskAssessment
from .auto_remediate import NOT_IMPLEMENTED, SafeAutoRemediator, RemediationResult
from .approval_workflow import ApprovalGate, ApprovalRequest, ApprovalState
from ._audit import remediation_audit


logger = logging.getLogger(__name__)


class RemediationState(Enum):
    """Remediation state machine"""
    DETECTED = "detected"
    CATEGORIZED = "categorized"
    AUTO_FIXING = "auto_fixing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED_AWAITING_FIX = "approved_awaiting_fix"
    FIXING = "fixing"
    REMEDIATED = "remediated"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"


@dataclass
class RemediationEvent:
    """Remediation lifecycle event"""
    event_type: str
    drift_id: str
    drift_type: str
    instance_id: str
    state: RemediationState
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    details: Optional[Dict] = None
    error: Optional[str] = None


@dataclass
class RemediationPlan:
    """Remediation plan for multiple drifts"""
    plan_id: str
    total_drifts: int
    safe_drifts: int
    high_risk_drifts: int
    blocked_drifts: int
    assessments: List[RiskAssessment] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class RemediationOrchestrator:
    """Orchestrates remediation across multiple drifts"""

    def __init__(
        self,
        audit_backend=None,
        plugin_installer=None,
        pagerduty_alerter=None,
        slack_alerter=None,
        *,
        tenant_id: str = "_default",
    ):
        """
        Initialize orchestrator.

        Args:
            audit_backend: Audit backend for logging
            plugin_installer: PluginInstaller from Phase 4
            pagerduty_alerter: PagerDuty integration
            slack_alerter: Slack integration
        """
        self.tenant_id = tenant_id
        self.categorizer = DriftCategorizer()
        self.remediator = SafeAutoRemediator(
            audit_backend=audit_backend, plugin_installer=plugin_installer, tenant_id=tenant_id
        )
        self.approval_gate = ApprovalGate(
            audit_backend=audit_backend,
            pagerduty_alerter=pagerduty_alerter,
            slack_alerter=slack_alerter,
        )
        self.audit_backend = audit_backend
        self.events = []  # Audit trail of all remediation events

    def process_drift(
        self, drift_type: str, drift_id: str, instance_id: str
    ) -> Tuple[RemediationState, Optional[str]]:
        """
        Process a single drift through remediation pipeline.

        Args:
            drift_type: Type of drift detected
            drift_id: Unique drift identifier
            instance_id: Instance affected by drift

        Returns:
            (final_state, result_id) where result_id is remediation/approval request ID
        """
        logger.info(f"🔍 Processing drift: {drift_type} ({drift_id}) on {instance_id}")

        # 1. DETECTED
        self._emit_event(
            RemediationEvent(
                event_type="drift_detected",
                drift_id=drift_id,
                drift_type=drift_type,
                instance_id=instance_id,
                state=RemediationState.DETECTED,
            )
        )

        # 2. CATEGORIZE
        assessment = self.categorizer.categorize_drift(drift_type, drift_id, instance_id)
        self._emit_event(
            RemediationEvent(
                event_type="drift_categorized",
                drift_id=drift_id,
                drift_type=drift_type,
                instance_id=instance_id,
                state=RemediationState.CATEGORIZED,
                details={
                    "category": assessment.category.value,
                    "severity": assessment.severity.value,
                    "risk_score": assessment.estimated_risk_score,
                },
            )
        )

        # 3. ROUTE TO HANDLER
        if assessment.category == DriftCategory.SAFE_AUTO_FIX:
            return self._handle_safe_drift(assessment, instance_id)
        elif assessment.category == DriftCategory.REQUIRES_APPROVAL:
            return self._handle_high_risk_drift(assessment, instance_id)
        elif assessment.category == DriftCategory.BLOCKED:
            return self._handle_blocked_drift(assessment, instance_id)
        else:
            logger.error(f"Unknown category for {drift_id}: {assessment.category}")
            return RemediationState.FAILED, None

    def orchestrate_multi_drift(
        self, drifts: List[Tuple[str, str, str]]
    ) -> Tuple[RemediationPlan, Dict[str, Tuple[RemediationState, Optional[str]]]]:
        """
        Orchestrate remediation for multiple drifts across instances.

        Args:
            drifts: List of (drift_type, drift_id, instance_id) tuples

        Returns:
            (remediation_plan, results) where results is {drift_id: (state, result_id)}
        """
        import uuid

        plan_id = f"plan-{uuid.uuid4().hex[:12]}"
        logger.info(f"📋 Orchestrating multi-drift remediation: {plan_id} ({len(drifts)} drifts)")

        # Categorize all drifts
        assessments, summary = self.categorizer.categorize_multiple(
            [(dt, di, ii) for dt, di, ii in drifts]
        )

        plan = RemediationPlan(
            plan_id=plan_id,
            total_drifts=summary["total"],
            safe_drifts=summary["safe_auto_fix"],
            high_risk_drifts=summary["requires_approval"],
            blocked_drifts=summary["blocked"],
            assessments=assessments,
        )

        logger.info(
            f"📊 Remediation plan: {plan.safe_drifts} safe, "
            f"{plan.high_risk_drifts} high-risk, {plan.blocked_drifts} blocked"
        )

        # Process each drift
        results = {}
        for assessment in assessments:
            # Extract instance_id from drifts list
            instance_id = None
            for dt, di, ii in drifts:
                if di == assessment.drift_id:
                    instance_id = ii
                    break

            state, result_id = self.process_drift(
                assessment.drift_type, assessment.drift_id, instance_id or "unknown"
            )
            results[assessment.drift_id] = (state, result_id)

        # Emit plan summary — its state is what the drifts actually reached,
        # never a blanket REMEDIATED.
        states = [st for st, _ in results.values()]
        if states and all(st == RemediationState.REMEDIATED for st in states):
            summary_state = RemediationState.REMEDIATED
        elif any(st == RemediationState.FAILED for st in states):
            summary_state = RemediationState.FAILED
        elif any(st == RemediationState.AWAITING_APPROVAL for st in states):
            summary_state = RemediationState.AWAITING_APPROVAL
        else:
            summary_state = RemediationState.BLOCKED
        self._emit_event(
            RemediationEvent(
                event_type="multi_drift_orchestrated",
                drift_id=plan_id,
                drift_type="multi",
                instance_id="cluster",
                state=summary_state,
                details={
                    "total": plan.total_drifts,
                    "safe": plan.safe_drifts,
                    "high_risk": plan.high_risk_drifts,
                    "blocked": plan.blocked_drifts,
                    "results": results,
                },
            )
        )

        return plan, results

    def _handle_safe_drift(self, assessment: RiskAssessment, instance_id: str) -> Tuple[RemediationState, Optional[str]]:
        """Handle safe drift with automatic remediation"""
        logger.info(f"🟢 Safe drift: auto-remediating {assessment.drift_id}")

        # 1. AUTO_FIXING
        self._emit_event(
            RemediationEvent(
                event_type="auto_remediation_started",
                drift_id=assessment.drift_id,
                drift_type=assessment.drift_type,
                instance_id=instance_id,
                state=RemediationState.AUTO_FIXING,
            )
        )

        # 2. EXECUTE REMEDIATION
        result = self.remediator.remediate_safe_drift(assessment)

        # 3. EMIT RESULT
        if result.status == "SUCCESS":
            self._emit_event(
                RemediationEvent(
                    event_type="remediation_success",
                    drift_id=assessment.drift_id,
                    drift_type=assessment.drift_type,
                    instance_id=instance_id,
                    state=RemediationState.REMEDIATED,
                    details={
                        "remediation_type": result.remediation_type,
                        "duration_seconds": result.duration_seconds,
                    },
                )
            )
            logger.info(f"✅ Safe drift REMEDIATED: {assessment.drift_id}")
            return RemediationState.REMEDIATED, result.drift_id
        elif result.status == NOT_IMPLEMENTED:
            self._emit_event(
                RemediationEvent(
                    event_type="remediation_not_implemented",
                    drift_id=assessment.drift_id,
                    drift_type=assessment.drift_type,
                    instance_id=instance_id,
                    state=RemediationState.FAILED,
                    error=result.error,
                )
            )
            logger.warning(f"Safe drift NOT remediated (no executor): {assessment.drift_id}")
            return RemediationState.FAILED, None
        elif result.status == "ROLLED_BACK":
            self._emit_event(
                RemediationEvent(
                    event_type="remediation_rolled_back",
                    drift_id=assessment.drift_id,
                    drift_type=assessment.drift_type,
                    instance_id=instance_id,
                    state=RemediationState.ROLLED_BACK,
                    error=result.error,
                )
            )
            logger.warning(f"🔙 Safe drift ROLLED BACK: {assessment.drift_id}")
            return RemediationState.ROLLED_BACK, result.drift_id
        else:
            self._emit_event(
                RemediationEvent(
                    event_type="remediation_failed",
                    drift_id=assessment.drift_id,
                    drift_type=assessment.drift_type,
                    instance_id=instance_id,
                    state=RemediationState.FAILED,
                    error=result.error,
                )
            )
            logger.error(f"❌ Safe drift FAILED: {assessment.drift_id} ({result.error})")
            return RemediationState.FAILED, None

    def _handle_high_risk_drift(self, assessment: RiskAssessment, instance_id: str) -> Tuple[RemediationState, Optional[str]]:
        """Handle high-risk drift requiring approval"""
        logger.info(f"🟠 High-risk drift: requesting approval for {assessment.drift_id}")

        # 1. AWAITING_APPROVAL
        self._emit_event(
            RemediationEvent(
                event_type="approval_requested",
                drift_id=assessment.drift_id,
                drift_type=assessment.drift_type,
                instance_id=instance_id,
                state=RemediationState.AWAITING_APPROVAL,
            )
        )

        # 2. REQUEST APPROVAL — and return. The decision arrives later through
        # the console approve/reject routes; see ``resume_after_decision``.
        approval_request = self.approval_gate.request_approval(
            assessment, instance_id, tenant_id=self.tenant_id
        )
        return RemediationState.AWAITING_APPROVAL, approval_request.request_id

    def resume_after_decision(self, request_id: str) -> Tuple[RemediationState, Optional[str]]:
        """Advance a high-risk drift once its approval request was decided.

        Non-blocking. APPROVED does not mean fixed: there is no high-risk fix
        executor, so an approved drift ends FAILED (``not_implemented``) —
        never REMEDIATED. REJECTED / EXPIRED → BLOCKED; still open →
        AWAITING_APPROVAL.
        """
        decision = self.approval_gate.get_request_for_tenant(request_id, self.tenant_id)
        if decision is None:
            return RemediationState.FAILED, None
        self.approval_gate._expire_if_due(decision)
        base = dict(
            drift_id=decision.drift_id,
            drift_type=decision.drift_type,
            instance_id=decision.instance_id,
        )

        if decision.state == ApprovalState.APPROVED:
            self._emit_event(RemediationEvent(
                event_type="approval_approved",
                state=RemediationState.APPROVED_AWAITING_FIX,
                details={"approved_by": decision.approved_by},
                **base,
            ))
            self._emit_event(RemediationEvent(
                event_type="remediation_not_implemented",
                state=RemediationState.FAILED,
                error="high_risk_executor_not_implemented",
                **base,
            ))
            logger.warning(f"Approved high-risk drift NOT remediated (no executor): {decision.drift_id}")
            return RemediationState.FAILED, request_id

        if decision.state == ApprovalState.REJECTED:
            self._emit_event(RemediationEvent(
                event_type="approval_rejected",
                state=RemediationState.BLOCKED,
                details={"rejected_by": decision.rejected_by},
                **base,
            ))
            return RemediationState.BLOCKED, request_id

        if decision.state == ApprovalState.EXPIRED:
            self._emit_event(RemediationEvent(
                event_type="approval_expired",
                state=RemediationState.BLOCKED,
                error="approval_expired",
                **base,
            ))
            return RemediationState.BLOCKED, request_id

        return RemediationState.AWAITING_APPROVAL, request_id

    def _handle_blocked_drift(self, assessment: RiskAssessment, instance_id: str) -> Tuple[RemediationState, Optional[str]]:
        """Handle blocked drift (stay in failed state, alert operator)"""
        logger.warning(f"🔴 Blocked drift: {assessment.drift_id}")

        self._emit_event(
            RemediationEvent(
                event_type="drift_blocked",
                drift_id=assessment.drift_id,
                drift_type=assessment.drift_type,
                instance_id=instance_id,
                state=RemediationState.BLOCKED,
                error=assessment.notes or "Drift cannot be auto-remediated",
            )
        )

        logger.warning(f"⚠️ Blocked drift requires manual intervention: {assessment.drift_id}")
        return RemediationState.BLOCKED, None

    def _emit_event(self, event: RemediationEvent):
        """Record the event: tenant core chain FIRST (raises if it does not
        commit), then the in-process list, then an additive ``audit_backend``
        copy. Free text (``error`` may carry an operator's reason or a drift
        note) never enters the chain — only whether there was one."""
        remediation_audit(
            "remediation.lifecycle",
            tenant_id=self.tenant_id,
            event=event.event_type,
            drift_id=event.drift_id,
            drift_type=event.drift_type,
            instance_id=event.instance_id,
            state=event.state.value,
            error_code="present" if event.error else None,
        )
        self.events.append(event)

        if self.audit_backend:
            try:
                self.audit_backend.write_event({
                    "event_type": event.event_type,
                    "drift_id": event.drift_id,
                    "drift_type": event.drift_type,
                    "instance_id": event.instance_id,
                    "state": event.state.value,
                    "timestamp": event.timestamp,
                    "details": event.details,
                    "error": event.error,
                })
            except Exception as e:
                logger.error(f"audit_backend copy failed (core chain record committed): {e}")
