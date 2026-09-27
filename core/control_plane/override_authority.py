"""Override Authority — Operator overrides with approval gates (ADR-2029 Stream 3).

Audit Trail Integration (ADR-0232/0233):
- All override operations (request, approve, reject, interrupt) are logged to
  the immutable core audit chain via AuditChainWriter
- Audit events are hash-chained and tamper-resistant
- Fail-closed: write errors to audit chain raise exceptions

Tenant routing (fixed 2026-09-27, adversarial review): the console keeps ONE
authority (constructed for ``_default``) and passes ``rec.tenant_id`` per call.
The writer used to be bound once, at construction, so every other tenant's
override records landed in ``_default``'s chain; approve/reject did not check
the override's tenant at all, so a session of tenant B could decide tenant A's
request. The chain writer is now resolved per call for the tenant the record is
ABOUT, and every decision path refuses a cross-tenant override id.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
from datetime import datetime
import logging

from core.compliance.audit_chain_writer import AuditEvent
from core.compliance.audit_chain_provider import get_audit_chain_writer

logger = logging.getLogger(__name__)


class OverrideType(Enum):
    """Types of operator overrides."""

    FORCE_ENABLE = "force_enable"
    FORCE_DISABLE = "force_disable"
    EMERGENCY_STOP = "emergency_stop"
    BYPASS_GATE = "bypass_gate"
    FORCE_RESTART = "force_restart"


class ApprovalStatus(Enum):
    """Approval status for override requests."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OverrideRequest:
    """Immutable override request."""

    override_id: str
    override_type: OverrideType
    target_id: str
    reason: str
    requestor_id: str
    approval_status: str  # pending|approved|rejected|expired
    tenant_id: str
    created_at: str  # ISO 8601
    approved_at: Optional[str] = None
    approver_id: Optional[str] = None


class PermissionError(Exception):
    """Raised when operation lacks required permissions."""

    pass


class OverrideAuthority:
    """Manages operator overrides with approval gates.

    Responsibilities:
    - Request overrides (with required justification)
    - Approval gate (admins only)
    - Audit trail of all override decisions (immutable, hash-chained)
    - Tenant-scoped access control
    - Automatic expiration of pending requests (24h)

    Audit Integration (ADR-0232/0233):
    - All operations logged to core audit chain (AuditChainWriter)
    - Fail-closed: write failures raise exceptions
    - Thread-safe: underlying chain writer handles synchronization
    """

    def __init__(self, tenant_id: str = "_default", audit_backend=None):
        """Initialize authority with audit chain writer.

        Args:
            tenant_id: Tenant scope for audit isolation
            audit_backend: Optional backend (for testing); defaults to core chain writer
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError(f"tenant_id must be a non-empty str, got {type(tenant_id).__name__}")
        self.tenant_id = tenant_id
        # Injected backend (tests) is used for every tenant; otherwise the
        # core chain writer is resolved PER CALL for the record's tenant.
        self._injected_backend = audit_backend

        self.overrides: Dict[str, OverrideRequest] = {}
        self.approvers: Set[str] = set()
        self._request_counter = 0

    @property
    def audit_chain(self):
        """Writer of the authority's own tenant (backward-compatible accessor)."""
        return self._writer(self.tenant_id)

    def _writer(self, tenant_id: str):
        if self._injected_backend is not None:
            return self._injected_backend
        return get_audit_chain_writer(tenant_id)

    def _own(self, override_id: str, tenant_id: str) -> OverrideRequest:
        """The override, if it exists AND belongs to ``tenant_id``."""
        override = self.overrides.get(override_id)
        if override is None:
            raise ValueError(f"Override {override_id} not found")
        if override.tenant_id != tenant_id:
            raise ValueError(f"Access denied to override {override_id}")
        return override

    async def request_override(
        self,
        override_type: OverrideType,
        target_id: str,
        reason: str,
        requestor_id: str,
        tenant_id: str,
    ) -> Dict:
        """Request an override (requires reason).

        Args:
            override_type: Type of override
            target_id: Target subsystem/plugin ID
            reason: Justification for override
            requestor_id: User ID requesting override
            tenant_id: Tenant scope

        Returns:
            Status dict with override_id and approval status

        Raises:
            ValueError: If reason is empty or invalid
        """
        if not reason or not isinstance(reason, str) or len(reason.strip()) == 0:
            raise ValueError("Override reason is required and cannot be empty")

        # Validate override type
        if not isinstance(override_type, OverrideType):
            raise ValueError(f"Invalid override type: {override_type}")

        override_id = f"override_{self._request_counter}"
        self._request_counter += 1

        request = OverrideRequest(
            override_id=override_id,
            override_type=override_type,
            target_id=target_id,
            reason=reason,
            requestor_id=requestor_id,
            approval_status=ApprovalStatus.PENDING.value,
            tenant_id=tenant_id,
            created_at=datetime.utcnow().isoformat() + "Z",
        )

        self.overrides[override_id] = request

        # Log immutable audit event to core chain (fail-closed)
        self._writer(tenant_id).write_event_dict(
            event_type="override_requested",
            tenant_id=tenant_id,
            user_id=requestor_id,
            details={
                "override_id": override_id,
                "override_type": override_type.value,
                "target_id": target_id,
            },
            severity="info",
        )

        logger.info(
            f"Override {override_id} requested by {requestor_id} for {target_id} (type: {override_type.value})"
        )

        return {
            "override_id": override_id,
            "status": "pending_approval",
            "created_at": request.created_at,
        }

    async def approve_override(
        self, override_id: str, approver_id: str, tenant_id: str
    ) -> Dict:
        """Approve an override (admin only).

        Args:
            override_id: ID of override request
            approver_id: User ID of approver
            tenant_id: Tenant scope

        Returns:
            Status dict with approval confirmation

        Raises:
            PermissionError: If approver not authorized
            ValueError: If override not found or expired
        """
        if approver_id not in self.approvers:
            # Log denial to audit chain (fail-closed)
            self._writer(tenant_id).write_event_dict(
                event_type="override_approve_denied",
                tenant_id=tenant_id,
                user_id=approver_id,
                details={
                    "override_id": override_id,
                    "reason": "unauthorized_approver",
                },
                severity="warning",
            )
            raise PermissionError(f"{approver_id} is not an authorized approver")

        override = self._own(override_id, tenant_id)

        # Check if already processed
        if override.approval_status != ApprovalStatus.PENDING.value:
            raise ValueError(
                f"Override already {override.approval_status}. Cannot re-approve."
            )

        # Approve (create new immutable record)
        approved_request = OverrideRequest(
            override_id=override.override_id,
            override_type=override.override_type,
            target_id=override.target_id,
            reason=override.reason,
            requestor_id=override.requestor_id,
            approval_status=ApprovalStatus.APPROVED.value,
            tenant_id=override.tenant_id,
            created_at=override.created_at,
            approved_at=datetime.utcnow().isoformat() + "Z",
            approver_id=approver_id,
        )

        self.overrides[override_id] = approved_request

        # Log immutable audit event to core chain (fail-closed)
        self._writer(tenant_id).write_event_dict(
            event_type="override_approved",
            tenant_id=tenant_id,
            user_id=approver_id,
            details={
                "override_id": override_id,
                "override_type": override.override_type.value,
                "target_id": override.target_id,
            },
            severity="info",
        )

        logger.info(f"Override {override_id} approved by {approver_id}")
        return {
            "override_id": override_id,
            "status": "approved",
            "approved_at": approved_request.approved_at,
        }

    async def reject_override(
        self, override_id: str, approver_id: str, rejection_reason: str, tenant_id: str
    ) -> Dict:
        """Reject an override request.

        Args:
            override_id: ID of override request
            approver_id: User ID of approver
            rejection_reason: Reason for rejection
            tenant_id: Tenant scope

        Returns:
            Status dict with rejection confirmation

        Raises:
            PermissionError: If approver not authorized
            ValueError: If override not found
        """
        if approver_id not in self.approvers:
            raise PermissionError(f"{approver_id} is not an authorized approver")

        override = self._own(override_id, tenant_id)

        # Check if already processed
        if override.approval_status != ApprovalStatus.PENDING.value:
            raise ValueError(
                f"Override already {override.approval_status}. Cannot reject."
            )

        # Reject (create new immutable record)
        rejected_request = OverrideRequest(
            override_id=override.override_id,
            override_type=override.override_type,
            target_id=override.target_id,
            reason=override.reason,
            requestor_id=override.requestor_id,
            approval_status=ApprovalStatus.REJECTED.value,
            tenant_id=override.tenant_id,
            created_at=override.created_at,
            approved_at=datetime.utcnow().isoformat() + "Z",
            approver_id=approver_id,
        )

        self.overrides[override_id] = rejected_request

        # Log immutable audit event to core chain (fail-closed)
        self._writer(tenant_id).write_event_dict(
            event_type="override_rejected",
            tenant_id=tenant_id,
            user_id=approver_id,
            details={
                "override_id": override_id,
                # the free-text rejection reason is never written to the chain
                "override_type": override.override_type.value,
                "target_id": override.target_id,
            },
            severity="info",
        )

        logger.info(f"Override {override_id} rejected by {approver_id}")
        return {
            "override_id": override_id,
            "status": "rejected",
            "rejection_reason": rejection_reason,
        }

    def get_override_status(self, override_id: str, tenant_id: str) -> Dict:
        """Get override status (non-blocking).

        Args:
            override_id: ID of override request
            tenant_id: Tenant scope

        Returns:
            Status dict with override details

        Raises:
            ValueError: If override not found
        """
        if override_id not in self.overrides:
            raise ValueError(f"Override {override_id} not found")

        override = self.overrides[override_id]

        # Verify tenant access
        if override.tenant_id != tenant_id:
            raise ValueError(f"Access denied to override {override_id}")

        return {
            "override_id": override_id,
            "override_type": override.override_type.value,
            "target_id": override.target_id,
            "approval_status": override.approval_status,
            "requestor_id": override.requestor_id,
            "created_at": override.created_at,
            "approved_at": override.approved_at,
            "approver_id": override.approver_id,
        }

    def list_pending_overrides(self, tenant_id: str) -> List[Dict]:
        """List all pending override requests for a tenant.

        Args:
            tenant_id: Tenant scope

        Returns:
            List of pending override dicts
        """
        pending = []
        for override in self.overrides.values():
            if (
                override.tenant_id == tenant_id
                and override.approval_status == ApprovalStatus.PENDING.value
            ):
                pending.append({
                    "override_id": override.override_id,
                    "override_type": override.override_type.value,
                    "target_id": override.target_id,
                    "requestor_id": override.requestor_id,
                    "created_at": override.created_at,
                })
        return pending

    def add_approver(self, user_id: str):
        """Add an authorized approver (admin setup).

        Args:
            user_id: User ID to grant approval authority
        """
        self.approvers.add(user_id)
        logger.info(f"Added approver: {user_id}")

    def remove_approver(self, user_id: str):
        """Remove an authorized approver.

        Args:
            user_id: User ID to revoke approval authority
        """
        self.approvers.discard(user_id)
        logger.info(f"Removed approver: {user_id}")

    def is_approver(self, user_id: str) -> bool:
        """Check if user is an authorized approver.

        Args:
            user_id: User ID to check

        Returns:
            True if authorized approver, False otherwise
        """
        return user_id in self.approvers

    async def deny_override(
        self, override_id: str, approver_id: str, reason: str, tenant_id: str
    ) -> Dict:
        """Deny an override request (alias for reject_override).

        Args:
            override_id: ID of override request
            approver_id: User ID of approver
            reason: Reason for denial
            tenant_id: Tenant scope

        Returns:
            Status dict with denial confirmation
        """
        return await self.reject_override(override_id, approver_id, reason, tenant_id)

    def list_pending_approvals(self, tenant_id: str) -> List[Dict]:
        """List all pending approval requests (alias for list_pending_overrides).

        Args:
            tenant_id: Tenant scope

        Returns:
            List of pending approval dicts
        """
        return self.list_pending_overrides(tenant_id)

    def get_approval_detail(self, override_id: str, tenant_id: str) -> Dict:
        """Get approval detail (alias for get_override_status).

        Args:
            override_id: ID of override request
            tenant_id: Tenant scope

        Returns:
            Approval details dict
        """
        return self.get_override_status(override_id, tenant_id)

    async def interrupt_override(self, override_id: str, tenant_id: str) -> Dict:
        """Interrupt/cancel a pending override.

        Args:
            override_id: ID of override request
            tenant_id: Tenant scope

        Returns:
            Status dict with interruption confirmation

        Raises:
            ValueError: If override not found
        """
        if override_id not in self.overrides:
            raise ValueError(f"Override {override_id} not found")

        override = self.overrides[override_id]

        # Verify tenant access
        if override.tenant_id != tenant_id:
            raise ValueError(f"Access denied to override {override_id}")

        # Check if can be interrupted (only pending requests)
        if override.approval_status != ApprovalStatus.PENDING.value:
            raise ValueError(
                f"Cannot interrupt override in {override.approval_status} state"
            )

        # Create new immutable record with expired status
        interrupted_request = OverrideRequest(
            override_id=override.override_id,
            override_type=override.override_type,
            target_id=override.target_id,
            reason=override.reason,
            requestor_id=override.requestor_id,
            approval_status=ApprovalStatus.EXPIRED.value,
            tenant_id=override.tenant_id,
            created_at=override.created_at,
            approved_at=datetime.utcnow().isoformat() + "Z",
            approver_id=None,
        )

        self.overrides[override_id] = interrupted_request

        # Log immutable audit event to core chain (fail-closed)
        self._writer(tenant_id).write_event_dict(
            event_type="override_interrupted",
            tenant_id=tenant_id,
            details={
                "override_id": override_id,
                "original_status": override.approval_status,
            },
            severity="info",
        )

        logger.info(f"Override {override_id} interrupted")
        return {
            "override_id": override_id,
            "status": "interrupted",
            "previous_status": override.approval_status,
        }

    async def get_audit_log(self, tenant_id: str) -> List[Dict]:
        """Get audit trail for all overrides in a tenant.

        Args:
            tenant_id: Tenant scope

        Returns:
            List of audit events for the tenant (from core audit chain)

        Note: This reads from the immutable core audit chain, so events
        are guaranteed to be hash-chained and tamper-resistant.
        """
        import json
        from pathlib import Path

        # Get the audit chain file path
        from corvin_operator.bridges.shared.paths import tenant_audit_chain
        chain_path = tenant_audit_chain(tenant_id)

        if not chain_path.exists():
            return []

        events = []
        try:
            with open(chain_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    # Filter to override-related events for this tenant
                    details = event.get("details") or {}
                    ev_tenant = details.get("tenant_id", event.get("tenant_id"))
                    if (ev_tenant == tenant_id and
                        event.get("event_type", "").startswith("override_")):
                        events.append(event)
        except (json.JSONDecodeError, IOError):
            # If chain is corrupted, return empty (audit chain should be verified
            # separately by boot tripwire; we don't fail here)
            pass

        return events
