"""Operator Approval System (GDPR Art. 6, 7)

Implements explicit consent model:
- Operator must approve before phase transitions
- 7-day timeout (auto-escalation, not auto-approve)
- Operator can reject with reason
- All approvals written to audit chain
- Metrics snapshot captured with each approval
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import json
from pathlib import Path
import hashlib
import uuid

logger = logging.getLogger(__name__)


@dataclass
class OperatorApprovalRequest:
    """Request for operator approval (GDPR Art. 6)"""
    request_id: str
    tenant_id: str  # MANDATORY (ADR-0563)
    phase_name: str  # "pilot" -> "canary" -> "full"
    created_at: str  # ISO 8601
    expires_at: str  # 7 days later
    metrics_snapshot: Dict[str, float]  # Current metrics to review
    description: str  # Phase transition description

    def to_audit_event(self) -> Dict:
        """Convert to audit event format"""
        return {
            "event_type": "operator_approval_requested",
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "phase_name": self.phase_name,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metrics_snapshot": dict(self.metrics_snapshot),
        }


@dataclass
class OperatorApprovalDecision:
    """Operator's approval or rejection decision"""
    decision_id: str
    request_id: str
    tenant_id: str  # MANDATORY (ADR-0563)
    operator_id: str
    decision: str  # "approved" or "rejected"
    decided_at: str  # ISO 8601
    reason: Optional[str] = None  # Optional rejection reason
    consent_basis: str = "Art. 6(1)(f)"  # Consent legal basis

    def to_audit_event(self) -> Dict:
        """Convert to audit event format"""
        return {
            "event_type": "operator_approval_decided",
            "tenant_id": self.tenant_id,
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "operator_id": self.operator_id,
            "decision": self.decision,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "consent_basis": self.consent_basis,
            "details": {
                "consent_basis": self.consent_basis,
                "gdpr_article": "6(1)(f)",  # Legitimate interest
            },
        }


class OperatorApprovalGate:
    """
    Gate that requires explicit operator approval before phase transitions.

    GDPR Art. 6 (lawful basis): Explicit consent
    GDPR Art. 7 (right to withdraw): Operator rejection
    ADR-0563: Tenant isolation (tenant_id on all records)
    """

    def __init__(self, corvin_home: Optional[Path] = None):
        self.corvin_home = corvin_home or Path.home() / ".corvin"
        self.audit_path = self.corvin_home / "orchestrator_audit.jsonl"
        self.requests_file = self.corvin_home / "approval_requests.jsonl"
        self.decisions_file = self.corvin_home / "approval_decisions.jsonl"

    def request_approval(
        self,
        tenant_id: str,
        phase_name: str,
        current_metrics: Dict[str, float],
        description: str = "",
    ) -> OperatorApprovalRequest:
        """
        Request operator approval for phase transition.

        GDPR Art. 6: Explicit consent
        ADR-0563: Tenant isolation (tenant_id required)
        """
        request_id = f"approval-{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(days=7)

        request = OperatorApprovalRequest(
            request_id=request_id,
            tenant_id=tenant_id,  # MANDATORY
            phase_name=phase_name,
            created_at=created_at.isoformat(),
            expires_at=expires_at.isoformat(),
            metrics_snapshot=dict(current_metrics),
            description=description,
        )

        # Write to audit trail (fail-closed if write fails)
        if not self._write_audit_event(request.to_audit_event()):
            raise RuntimeError(f"Failed to write approval request to audit trail (tenant {tenant_id})")

        # Store request file
        try:
            with open(self.requests_file, 'a') as f:
                f.write(json.dumps({
                    "request_id": request_id,
                    "tenant_id": tenant_id,
                    "phase_name": phase_name,
                    "created_at": created_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "metrics_snapshot": dict(current_metrics),
                    "description": description,
                    "status": "pending",
                }) + "\n")
        except Exception as e:
            logger.error(f"Failed to store approval request: {e}")

        logger.info(f"Approval requested for phase {phase_name} in tenant {tenant_id}: {request_id}")
        return request

    def approve_transition(
        self,
        request_id: str,
        tenant_id: str,
        operator_id: str,
    ) -> Tuple[bool, str]:
        """
        Operator approves phase transition.

        GDPR Art. 6: Explicit consent documented
        GDPR Art. 7: Can be withdrawn via rejection
        ADR-0563: Tenant isolation enforced
        """
        # Verify request exists and is not expired
        request = self._get_request(request_id)
        if not request:
            return False, f"Request {request_id} not found"

        if request.get("tenant_id") != tenant_id:
            return False, f"Tenant mismatch: {request.get('tenant_id')} != {tenant_id} (ADR-0563)"

        expires_at = datetime.fromisoformat(request.get("expires_at", ""))
        if datetime.now(timezone.utc) > expires_at:
            return False, f"Request expired at {request.get('expires_at')}"

        # Record decision
        decision = OperatorApprovalDecision(
            decision_id=f"decision-{uuid.uuid4().hex[:12]}",
            request_id=request_id,
            tenant_id=tenant_id,  # MANDATORY (ADR-0563)
            operator_id=operator_id,
            decision="approved",
            decided_at=datetime.now(timezone.utc).isoformat(),
            consent_basis="Art. 6(1)(f)",  # Legitimate interest
        )

        # Write to audit trail (fail-closed)
        if not self._write_audit_event(decision.to_audit_event()):
            return False, "Failed to write approval to audit trail (GDPR Art. 30)"

        # Update request status
        self._update_request_status(request_id, "approved")

        # Store decision
        try:
            with open(self.decisions_file, 'a') as f:
                f.write(json.dumps(decision.__dict__) + "\n")
        except Exception as e:
            logger.error(f"Failed to store approval decision: {e}")
            return False, f"Failed to store decision: {e}"

        logger.info(f"Phase transition approved by {operator_id} for tenant {tenant_id}: {request_id}")
        return True, f"Phase transition approved (tenant {tenant_id})"

    def reject_transition(
        self,
        request_id: str,
        tenant_id: str,
        operator_id: str,
        reason: str,
    ) -> Tuple[bool, str]:
        """
        Operator rejects phase transition (GDPR Art. 7 - right to withdraw).

        GDPR Art. 7: Explicit withdrawal
        ADR-0563: Tenant isolation enforced
        """
        # Verify request exists
        request = self._get_request(request_id)
        if not request:
            return False, f"Request {request_id} not found"

        if request.get("tenant_id") != tenant_id:
            return False, f"Tenant mismatch (ADR-0563)"

        # Record rejection decision
        decision = OperatorApprovalDecision(
            decision_id=f"decision-{uuid.uuid4().hex[:12]}",
            request_id=request_id,
            tenant_id=tenant_id,  # MANDATORY (ADR-0563)
            operator_id=operator_id,
            decision="rejected",
            decided_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
            consent_basis="Art. 7(3)",  # Right to withdraw
        )

        # Write to audit trail (fail-closed)
        if not self._write_audit_event(decision.to_audit_event()):
            return False, "Failed to write rejection to audit trail (GDPR Art. 30)"

        # Update request status
        self._update_request_status(request_id, "rejected")

        # Store decision
        try:
            with open(self.decisions_file, 'a') as f:
                f.write(json.dumps(decision.__dict__) + "\n")
        except Exception as e:
            logger.error(f"Failed to store rejection decision: {e}")
            return False, f"Failed to store decision: {e}"

        logger.warning(f"Phase transition rejected by {operator_id} for tenant {tenant_id}: {reason}")
        return True, f"Phase transition rejected (reason: {reason})"

    def check_approval_status(
        self,
        request_id: str,
        tenant_id: str,
    ) -> Tuple[str, Optional[str]]:
        """
        Check approval status (pending, approved, rejected, expired).

        Returns: (status, decision_id or None)
        """
        request = self._get_request(request_id)
        if not request:
            return "not_found", None

        if request.get("tenant_id") != tenant_id:
            return "tenant_mismatch", None

        current_status = request.get("status", "pending")

        # Check expiration
        if current_status == "pending":
            expires_at = datetime.fromisoformat(request.get("expires_at", ""))
            if datetime.now(timezone.utc) > expires_at:
                # Auto-escalate (not auto-approve) - requires manual intervention
                self._update_request_status(request_id, "expired")
                logger.warning(f"Approval request {request_id} expired (tenant {tenant_id})")
                return "expired", None

        # Find decision if approved/rejected
        decision_id = None
        if current_status in ("approved", "rejected"):
            decision_id = self._get_decision_for_request(request_id)

        return current_status, decision_id

    def _write_audit_event(self, event: Dict) -> bool:
        """Write event to audit trail (fail-closed if audit write fails)"""
        try:
            # Add audit metadata
            event_record = {
                "event_id": f"evt-{uuid.uuid4().hex[:12]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenant_id": event.get("tenant_id"),  # MANDATORY
                **event,
            }

            # Write to audit trail
            with open(self.audit_path, 'a') as f:
                f.write(json.dumps(event_record) + "\n")

            return True
        except Exception as e:
            logger.error(f"Failed to write audit event: {e}")
            return False  # Fail-closed

    def _get_request(self, request_id: str) -> Optional[Dict]:
        """Get approval request by ID"""
        if not self.requests_file.exists():
            return None

        try:
            with open(self.requests_file, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("request_id") == request_id:
                        return data
        except Exception as e:
            logger.error(f"Failed to read request: {e}")

        return None

    def _get_decision_for_request(self, request_id: str) -> Optional[str]:
        """Get decision ID for a request"""
        if not self.decisions_file.exists():
            return None

        try:
            with open(self.decisions_file, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("request_id") == request_id:
                        return data.get("decision_id")
        except Exception as e:
            logger.error(f"Failed to read decision: {e}")

        return None

    def _update_request_status(self, request_id: str, status: str) -> None:
        """Update request status"""
        if not self.requests_file.exists():
            return

        try:
            # Read all requests
            requests = []
            with open(self.requests_file, 'r') as f:
                for line in f:
                    if line.strip():
                        requests.append(json.loads(line))

            # Update the matching request
            for req in requests:
                if req.get("request_id") == request_id:
                    req["status"] = status
                    req["updated_at"] = datetime.now(timezone.utc).isoformat()

            # Write back
            with open(self.requests_file, 'w') as f:
                for req in requests:
                    f.write(json.dumps(req) + "\n")
        except Exception as e:
            logger.error(f"Failed to update request status: {e}")
