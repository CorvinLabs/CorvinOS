"""
Phase 5: Operator Approval Workflow

Handles approval requests for high-risk drifts.
24-hour timeout with PagerDuty escalation.

NOT WIRED (request side): no production caller creates approval requests as of
2026-09-27 (adversarial review) — ``RemediationOrchestrator`` has no caller.
The console routes (``routes/remediation_routes.py``) do read and decide
requests on the process-global gate.

Every request, decision, expiry and escalation is written to the TENANT's core
hash chain (``_audit.remediation_audit``) BEFORE the state changes; a record
that does not commit refuses the transition. Requests are tenant-bound: a
tenant can neither see nor decide another tenant's request.
"""

import json
import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any
import time

from .drift_categories import DriftCategory, RiskAssessment
from ._audit import remediation_audit


logger = logging.getLogger(__name__)


class ApprovalState(Enum):
    """Approval request states"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ESCALATED = "escalated"


@dataclass
class ApprovalRequest:
    """Approval request for high-risk remediation"""
    request_id: str
    drift_id: str
    drift_type: str
    instance_id: str
    risk_assessment: Dict[str, Any]
    state: ApprovalState
    requested_at: str
    requested_by: str = "system"
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
    decision_at: Optional[str] = None
    decision_reason: Optional[str] = None
    expires_at: Optional[str] = None
    escalated_at: Optional[str] = None
    escalation_incident_id: Optional[str] = None
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "drift_id": self.drift_id,
            "drift_type": self.drift_type,
            "instance_id": self.instance_id,
            "risk_assessment": self.risk_assessment,
            "state": self.state.value,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "rejected_by": self.rejected_by,
            "decision_at": self.decision_at,
            "decision_reason": self.decision_reason,
            "expires_at": self.expires_at,
            "escalated_at": self.escalated_at,
            "escalation_incident_id": self.escalation_incident_id,
        }


class ApprovalGate:
    """Manages operator approval for high-risk drifts"""

    APPROVAL_TIMEOUT_HOURS = 24
    PAGERDUTY_ESCALATION_MINUTES = 120  # 2 hours after request

    def __init__(
        self,
        approval_backend=None,
        audit_backend=None,
        pagerduty_alerter=None,
        slack_alerter=None,
    ):
        """
        Initialize approval gate.

        Args:
            approval_backend: Backend for storing/fetching approvals
            audit_backend: Audit backend for logging
            pagerduty_alerter: PagerDuty integration for escalation
            slack_alerter: Slack integration for notifications
        """
        self.approval_backend = approval_backend
        self.audit_backend = audit_backend
        self.pagerduty_alerter = pagerduty_alerter
        self.slack_alerter = slack_alerter
        self.pending_requests = {}  # In-memory cache: request_id -> ApprovalRequest

    #: States an operator may still decide. An escalated request is still open —
    #: escalation pages someone, it does not close the request.
    _DECIDABLE = (ApprovalState.PENDING, ApprovalState.ESCALATED)

    def request_approval(
        self,
        assessment: RiskAssessment,
        instance_id: str,
        requested_by: str = "system",
        *,
        tenant_id: str = "_default",
    ) -> ApprovalRequest:
        """
        Create approval request for high-risk drift.

        Args:
            assessment: RiskAssessment from DriftCategorizer
            instance_id: Instance affected by drift
            requested_by: User/system requesting approval

        Returns:
            ApprovalRequest
        """
        if assessment.category != DriftCategory.REQUIRES_APPROVAL:
            raise ValueError(
                f"Only REQUIRES_APPROVAL drifts need approval (got {assessment.category.value})"
            )

        request_id = f"apr-{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow()
        expires_at = (now + timedelta(hours=self.APPROVAL_TIMEOUT_HOURS)).isoformat()

        approval_request = ApprovalRequest(
            request_id=request_id,
            drift_id=assessment.drift_id,
            drift_type=assessment.drift_type,
            instance_id=instance_id,
            risk_assessment={
                "severity": assessment.severity.value,
                "risk_score": assessment.estimated_risk_score,
                "estimated_duration_seconds": assessment.estimated_duration_seconds,
                "impacts_services": assessment.impacts_services,
                "requires_rollback": assessment.requires_rollback,
                "notes": assessment.notes,
            },
            state=ApprovalState.PENDING,
            requested_at=now.isoformat(),
            requested_by=requested_by,
            expires_at=expires_at,
            tenant_id=tenant_id,
        )

        # Audit FIRST: no chain record → no request.
        self._audit_event("approval_requested", approval_request)

        # Store in memory cache
        self.pending_requests[request_id] = approval_request

        # Store in backend (if available)
        if self.approval_backend:
            try:
                self.approval_backend.store_request(approval_request)
            except Exception as e:
                logger.error(f"Failed to store approval request: {e}")

        # Send Slack notification
        self._notify_slack_request(approval_request)

        logger.info(f"✉️ Approval requested: {request_id} for {assessment.drift_id}")

        return approval_request

    def wait_for_approval(
        self, request_id: str, timeout_seconds: int = 30, poll_interval_seconds: int = 2
    ) -> Optional[ApprovalRequest]:
        """
        Poll for operator decision on approval request.

        BLOCKS the calling thread (``time.sleep``) for up to
        ``timeout_seconds``. Never call it on a request path or an event loop;
        ``RemediationOrchestrator`` no longer does.

        Args:
            request_id: ID of approval request
            timeout_seconds: Max time to wait for decision
            poll_interval_seconds: How often to check for decision

        Returns:
            ApprovalRequest with decision, or None if timeout
        """
        deadline = datetime.utcnow() + timedelta(seconds=timeout_seconds)

        while datetime.utcnow() < deadline:
            request = self._get_request(request_id)

            if request is None:
                logger.warning(f"Approval request not found: {request_id}")
                return None

            # Check if decision has been made
            if request.state in [ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.EXPIRED]:
                logger.info(f"✓ Approval decision received: {request_id} → {request.state.value}")
                return request

            if self._expire_if_due(request):
                return request

            # Check if should escalate
            if request.state == ApprovalState.PENDING:
                time_pending = (datetime.utcnow() - datetime.fromisoformat(request.requested_at)).total_seconds() / 60
                if time_pending > self.PAGERDUTY_ESCALATION_MINUTES:
                    if request.escalated_at is None:
                        logger.warning(f"Escalating approval request to PagerDuty: {request_id}")
                        self._escalate_to_pagerduty(request)

            # Wait and poll again
            time.sleep(poll_interval_seconds)

        # Poll window over: the request stays open until its own expiry — the
        # caller simply stopped waiting.
        logger.warning(f"Stopped waiting for approval: {request_id}")
        request = self._get_request(request_id)
        if request is not None:
            self._expire_if_due(request)
        return request

    def _expire_if_due(self, request: ApprovalRequest) -> bool:
        """Mark an open request EXPIRED once ``expires_at`` has passed (audited)."""
        if request.state not in self._DECIDABLE or not request.expires_at:
            return request.state == ApprovalState.EXPIRED
        try:
            expired = datetime.utcnow() >= datetime.fromisoformat(request.expires_at)
        except ValueError:
            expired = True  # unparseable deadline: fail-closed
        if not expired:
            return False
        prior = request.state
        request.state = ApprovalState.EXPIRED
        try:
            self._audit_event("approval_expired", request)
        except Exception:
            request.state = prior
            raise
        return True

    def get_request_for_tenant(self, request_id: str, tenant_id: str) -> Optional[ApprovalRequest]:
        """The request if it exists AND belongs to ``tenant_id``; else ``None``."""
        request = self._get_request(request_id)
        if request is None or request.tenant_id != tenant_id:
            return None
        return request

    def requests_for_tenant(self, tenant_id: str) -> list:
        return [r for r in self.pending_requests.values() if r.tenant_id == tenant_id]

    def approve_request(
        self,
        request_id: str,
        approved_by: str,
        reason: Optional[str] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> ApprovalRequest:
        """
        Operator approves remediation.

        Args:
            request_id: ID of approval request
            approved_by: User approving the request
            reason: Optional reason for approval

        Returns:
            Updated ApprovalRequest
        """
        request = self._open_request(request_id, tenant_id, "approve")
        self._decide(request, ApprovalState.APPROVED, approved_by, reason)

        # Update backend
        if self.approval_backend:
            try:
                self.approval_backend.update_request(request)
            except Exception as e:
                logger.error(f"Failed to update approval request: {e}")

        # Send Slack notification
        self._notify_slack_decision(request, "approved")

        logger.info(f"✅ Approval approved: {request_id} by {approved_by}")

        return request

    def reject_request(
        self,
        request_id: str,
        rejected_by: str,
        reason: Optional[str] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> ApprovalRequest:
        """
        Operator rejects remediation.

        Args:
            request_id: ID of approval request
            rejected_by: User rejecting the request
            reason: Reason for rejection

        Returns:
            Updated ApprovalRequest
        """
        request = self._open_request(request_id, tenant_id, "reject")
        self._decide(request, ApprovalState.REJECTED, rejected_by, reason)

        # Update backend
        if self.approval_backend:
            try:
                self.approval_backend.update_request(request)
            except Exception as e:
                logger.error(f"Failed to update approval request: {e}")

        # Send Slack notification
        self._notify_slack_decision(request, "rejected")

        logger.info(f"❌ Approval rejected: {request_id} by {rejected_by}")

        return request

    def _open_request(
        self, request_id: str, tenant_id: Optional[str], verb: str
    ) -> ApprovalRequest:
        """The still-decidable request, or ``ValueError`` (unknown / other
        tenant / expired / already decided)."""
        request = self._get_request(request_id)
        if request is None or (tenant_id is not None and request.tenant_id != tenant_id):
            raise ValueError(f"Approval request not found: {request_id}")
        if self._expire_if_due(request):
            raise ValueError(f"Cannot {verb} request {request_id}: it has expired")
        if request.state not in self._DECIDABLE:
            raise ValueError(
                f"Cannot {verb} request in state {request.state.value} (must be pending)"
            )
        return request

    def _decide(
        self, request: ApprovalRequest, state: ApprovalState, decider: str, reason: Optional[str]
    ) -> None:
        """Audit-FIRST state transition: the decision is written to the tenant
        chain before it takes effect; if the record does not commit, the
        request is left exactly as it was and the error propagates."""
        snapshot = (request.state, request.approved_by, request.rejected_by,
                    request.decision_at, request.decision_reason)
        request.state = state
        if state == ApprovalState.APPROVED:
            request.approved_by = decider
        else:
            request.rejected_by = decider
        request.decision_at = datetime.utcnow().isoformat()
        request.decision_reason = reason
        try:
            self._audit_event("approval_decided", request)
        except Exception:
            (request.state, request.approved_by, request.rejected_by,
             request.decision_at, request.decision_reason) = snapshot
            raise

    def cancel_remediation(self, request_id: str) -> ApprovalRequest:
        """
        Cancel remediation for rejected/expired approval.

        Args:
            request_id: ID of approval request

        Returns:
            Updated ApprovalRequest
        """
        request = self._get_request(request_id)
        if request is None:
            raise ValueError(f"Approval request not found: {request_id}")

        # Audit log
        self._audit_event("remediation_cancelled", request)

        logger.info(f"⏹️ Remediation cancelled: {request_id}")

        return request

    def _get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Get approval request by ID (check memory cache first, then backend)"""
        # Check memory cache
        if request_id in self.pending_requests:
            return self.pending_requests[request_id]

        # Check backend
        if self.approval_backend:
            try:
                request = self.approval_backend.get_request(request_id)
                if request:
                    self.pending_requests[request_id] = request
                    return request
            except Exception as e:
                logger.error(f"Failed to fetch approval request from backend: {e}")

        return None

    def _notify_slack_request(self, request: ApprovalRequest):
        """Send Slack notification for new approval request"""
        if not self.slack_alerter:
            return

        try:
            message = f"""
🔔 **Approval Required**
Drift: {request.drift_type} ({request.drift_id})
Instance: {request.instance_id}
Risk Level: {request.risk_assessment.get('severity', 'unknown')}
Est. Duration: {request.risk_assessment.get('estimated_duration_seconds', 0)}s
Request ID: {request.request_id}
Expires: {request.expires_at}
            """
            self.slack_alerter.send_alert(
                severity="HIGH",
                message=message,
                instance_id=request.instance_id,
                drift_type=request.drift_type,
            )
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")

    def _notify_slack_decision(self, request: ApprovalRequest, decision: str):
        """Send Slack notification for approval decision"""
        if not self.slack_alerter:
            return

        try:
            emoji = "✅" if decision == "approved" else "❌"
            message = f"""
{emoji} **Approval {decision.upper()}**
Drift: {request.drift_type} ({request.drift_id})
Request ID: {request.request_id}
Decision By: {request.approved_by or request.rejected_by}
Reason: {request.decision_reason or "None"}
            """
            self.slack_alerter.send_alert(
                severity="MEDIUM",
                message=message,
                instance_id=request.instance_id,
                drift_type=request.drift_type,
            )
        except Exception as e:
            logger.error(f"Failed to send Slack decision notification: {e}")

    def _escalate_to_pagerduty(self, request: ApprovalRequest):
        """Escalate approval request to PagerDuty incident"""
        if not self.pagerduty_alerter:
            return

        try:
            message = f"Approval request {request.request_id} for {request.drift_type} on {request.instance_id} pending >2 hours"
            self.pagerduty_alerter.trigger_incident(
                message=message,
                instance_id=request.instance_id,
                drift_type=request.drift_type,
            )
            prior = (request.state, request.escalated_at)
            request.escalated_at = datetime.utcnow().isoformat()
            request.state = ApprovalState.ESCALATED
            try:
                self._audit_event("approval_escalated", request)
            except Exception:
                request.state, request.escalated_at = prior
                raise
        except Exception as e:
            logger.error(f"Failed to escalate to PagerDuty: {e}")

    def _audit_event(self, event_type: str, request: ApprovalRequest):
        """Write the event to the request's tenant chain (raises if it does not
        commit), then hand an additive copy to an injected ``audit_backend``."""
        decision = None
        decided_by = request.approved_by or request.rejected_by
        if event_type == "approval_decided":
            decision = request.state.value
        remediation_audit(
            f"remediation.{event_type}",
            tenant_id=request.tenant_id,
            request_id=request.request_id,
            drift_id=request.drift_id,
            drift_type=request.drift_type,
            instance_id=request.instance_id,
            state=request.state.value,
            decision=decision,
            decided_by=decided_by,
            has_reason=bool(request.decision_reason) if decision else None,
        )
        if not self.audit_backend:
            return
        try:
            self.audit_backend.write_event({
                "event_type": event_type,
                "request_id": request.request_id,
                "drift_id": request.drift_id,
                "drift_type": request.drift_type,
                "state": request.state.value,
                "approved_by": request.approved_by,
                "rejected_by": request.rejected_by,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.error(f"audit_backend copy failed (core chain record committed): {e}")


# Global approval gate instance
_approval_gate = None


def get_approval_gate() -> ApprovalGate:
    """Get or create global approval gate"""
    global _approval_gate
    if _approval_gate is None:
        _approval_gate = ApprovalGate()
    return _approval_gate
