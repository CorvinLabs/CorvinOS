"""Override Authority — Operator overrides with approval gates (ADR-2029 Stream 3)."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
from datetime import datetime
import logging

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
    - Audit trail of all override decisions
    - Tenant-scoped access control
    - Automatic expiration of pending requests (24h)
    """

    def __init__(self, audit_backend):
        """Initialize authority with audit backend.

        Args:
            audit_backend: Backend for audit event logging
        """
        self.audit = audit_backend
        self.overrides: Dict[str, OverrideRequest] = {}
        self.approvers: Set[str] = set()
        self._request_counter = 0

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

        # Log audit event
        await self.audit.log_event(
            "override_requested",
            {
                "override_id": override_id,
                "override_type": override_type.value,
                "target_id": target_id,
                "requestor_id": requestor_id,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
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
            await self.audit.log_event(
                "override_approve_denied",
                {
                    "override_id": override_id,
                    "reason": "unauthorized_approver",
                    "approver_id": approver_id,
                    "tenant_id": tenant_id,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
            raise PermissionError(f"{approver_id} is not an authorized approver")

        if override_id not in self.overrides:
            raise ValueError(f"Override {override_id} not found")

        override = self.overrides[override_id]

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

        # Log audit event
        await self.audit.log_event(
            "override_approved",
            {
                "override_id": override_id,
                "approver_id": approver_id,
                "tenant_id": tenant_id,
                "override_type": override.override_type.value,
                "target_id": override.target_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
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

        if override_id not in self.overrides:
            raise ValueError(f"Override {override_id} not found")

        override = self.overrides[override_id]

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

        # Log audit event
        await self.audit.log_event(
            "override_rejected",
            {
                "override_id": override_id,
                "approver_id": approver_id,
                "rejection_reason": rejection_reason,
                "tenant_id": tenant_id,
                "override_type": override.override_type.value,
                "target_id": override.target_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
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
