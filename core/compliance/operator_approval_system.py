"""Operator Approval System (GDPR Art. 6, 7)

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

Implements explicit consent model:
- Operator must approve before phase transitions
- 7-day timeout (auto-escalation, not auto-approve)
- Operator can reject with reason
- Every request/decision is written to THE tenant audit chain
  (``forge.paths.tenant_audit_chain``) through ``forge.security_events.
  write_event`` — content-free: ids, phase, decision; the operator id is
  pseudonymised by the writer when PII-shaped, and the free-text reason and
  metrics stay in the tenant's own request/decision store, never in the chain.
- A request is decided at most once: approving or rejecting a request that is
  not ``pending`` is refused.

Until 2026-09-27 this "audit trail" was an unchained ``orchestrator_audit.jsonl``
under ``Path.home()/.corvin`` shared by all tenants — a second, forgeable trail
holding the raw operator id and reason text.
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
        from forge import paths as forge_paths  # type: ignore[import-not-found]

        # Request/decision STORE root (resolved now; CORVIN_HOME honoured). The
        # audit chain is never parametrisable — it is always the canonical one.
        self.corvin_home = Path(corvin_home) if corvin_home else forge_paths.corvin_home()

    def _store_dir(self, tenant_id: str) -> Path:
        from forge import paths as forge_paths  # type: ignore[import-not-found]

        tid = forge_paths.tenant_home(tenant_id).name  # validates the id
        d = self.corvin_home / "tenants" / tid / "global" / "operator_approvals"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _requests_file(self, tenant_id: str) -> Path:
        return self._store_dir(tenant_id) / "requests.jsonl"

    def _decisions_file(self, tenant_id: str) -> Path:
        return self._store_dir(tenant_id) / "decisions.jsonl"

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
        if not self._write_audit_event("operator_approval.requested", tenant_id, {
            "request_id": request_id, "phase_name": phase_name,
            "expires_at": request.expires_at,
        }):
            raise RuntimeError(f"Failed to write approval request to audit trail (tenant {tenant_id})")

        # Store request file (fail-closed: an unstored request cannot be decided)
        try:
            with open(self._requests_file(tenant_id), 'a') as f:
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
            logger.error("Failed to store approval request: %s", type(e).__name__)
            raise RuntimeError(f"Failed to store approval request (tenant {tenant_id})") from e

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
        # Verify request exists, is pending and is not expired
        request = self._get_request(request_id, tenant_id)
        if not request:
            return False, f"Request {request_id} not found"

        if request.get("tenant_id") != tenant_id:
            return False, "Tenant mismatch (ADR-0563)"

        if request.get("status", "pending") != "pending":
            return False, f"Request {request_id} already {request.get('status')}"

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
        if not self._write_decision_audit(decision, request):
            return False, "Failed to write approval to audit trail (GDPR Art. 30)"

        # Update request status
        self._update_request_status(request_id, tenant_id, "approved")

        # Store decision
        try:
            with open(self._decisions_file(tenant_id), 'a') as f:
                f.write(json.dumps(decision.__dict__) + "\n")
        except Exception as e:
            logger.error("Failed to store approval decision: %s", type(e).__name__)
            return False, "Failed to store decision"

        logger.info("Phase transition approved for tenant %s: %s", tenant_id, request_id)
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
        # Verify request exists and is still pending
        request = self._get_request(request_id, tenant_id)
        if not request:
            return False, f"Request {request_id} not found"

        if request.get("tenant_id") != tenant_id:
            return False, "Tenant mismatch (ADR-0563)"

        if request.get("status", "pending") != "pending":
            return False, f"Request {request_id} already {request.get('status')}"

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
        if not self._write_decision_audit(decision, request):
            return False, "Failed to write rejection to audit trail (GDPR Art. 30)"

        # Update request status
        self._update_request_status(request_id, tenant_id, "rejected")

        # Store decision
        try:
            with open(self._decisions_file(tenant_id), 'a') as f:
                f.write(json.dumps(decision.__dict__) + "\n")
        except Exception as e:
            logger.error("Failed to store rejection decision: %s", type(e).__name__)
            return False, "Failed to store decision"

        logger.warning("Phase transition rejected for tenant %s: %s", tenant_id, request_id)
        return True, "Phase transition rejected"

    def check_approval_status(
        self,
        request_id: str,
        tenant_id: str,
    ) -> Tuple[str, Optional[str]]:
        """
        Check approval status (pending, approved, rejected, expired).

        Returns: (status, decision_id or None)
        """
        request = self._get_request(request_id, tenant_id)
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
                self._write_audit_event("operator_approval.expired", tenant_id, {
                    "request_id": request_id, "phase_name": request.get("phase_name"),
                })
                self._update_request_status(request_id, tenant_id, "expired")
                logger.warning(f"Approval request {request_id} expired (tenant {tenant_id})")
                return "expired", None

        # Find decision if approved/rejected
        decision_id = None
        if current_status in ("approved", "rejected"):
            decision_id = self._get_decision_for_request(request_id, tenant_id)

        return current_status, decision_id

    def _write_audit_event(self, event_type: str, tenant_id: str, details: Dict) -> bool:
        """Write one content-free record to THE tenant chain. False on failure
        (callers fail closed)."""
        try:
            from forge import paths as forge_paths  # type: ignore[import-not-found]
            from forge import security_events  # type: ignore[import-not-found]

            security_events.write_event(
                forge_paths.tenant_audit_chain(tenant_id),
                event_type,
                details={**details, "tenant_id": tenant_id,
                         "lom": "core.compliance.operator_approval_system"},
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to write audit event %s: %s", event_type, type(e).__name__)
            return False  # Fail-closed

    def _write_decision_audit(self, decision: "OperatorApprovalDecision", request: Dict) -> bool:
        return self._write_audit_event("operator_approval.decided", decision.tenant_id, {
            "request_id": decision.request_id,
            "decision_id": decision.decision_id,
            "decision": decision.decision,
            "phase_name": request.get("phase_name"),
            "consent_basis": decision.consent_basis,
            # reserved spine key: the writer pseudonymises a PII-shaped id
            "user": decision.operator_id,
        })

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict]:
        if not path.exists():
            return []
        out = []
        with open(path, 'r') as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def _get_request(self, request_id: str, tenant_id: str) -> Optional[Dict]:
        """Get approval request by ID from the tenant's own store."""
        try:
            for data in self._read_jsonl(self._requests_file(tenant_id)):
                if data.get("request_id") == request_id:
                    return data
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to read request: %s", type(e).__name__)
        return None

    def _get_decision_for_request(self, request_id: str, tenant_id: str) -> Optional[str]:
        """Get decision ID for a request"""
        try:
            for data in self._read_jsonl(self._decisions_file(tenant_id)):
                if data.get("request_id") == request_id:
                    return data.get("decision_id")
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to read decision: %s", type(e).__name__)
        return None

    def _update_request_status(self, request_id: str, tenant_id: str, status: str) -> None:
        """Update request status (atomic replace of the tenant's store)."""
        path = self._requests_file(tenant_id)
        requests = self._read_jsonl(path)
        for req in requests:
            if req.get("request_id") == request_id:
                req["status"] = status
                req["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, 'w') as f:
            for req in requests:
                f.write(json.dumps(req) + "\n")
        tmp.replace(path)
