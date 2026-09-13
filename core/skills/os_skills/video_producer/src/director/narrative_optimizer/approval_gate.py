"""User approval workflow for narrative suggestions.

Handles presentation of suggestions to users and collection of approval/rejection
decisions with optional modifications.
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ApprovalStatus(Enum):
    """Status of an approval request"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXPIRED = "expired"


@dataclass
class ApprovalResponse:
    """User's response to an approval request"""
    approval_status: ApprovalStatus
    user_id: str
    timestamp: str
    reasoning: str
    modifications: List[str]
    approved_with_changes: bool = False


class ApprovalGate:
    """User approval workflow for narrative suggestions"""

    def __init__(self, timeout_seconds: int = 3600):
        """Initialize approval gate.

        Args:
            timeout_seconds: How long to wait for approval before expiring
        """
        self.timeout_seconds = timeout_seconds
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}
        self.approval_history: List[ApprovalResponse] = []

    def request_approval(self, suggestion: Dict[str, Any], user_id: str,
                        context: Optional[Dict[str, Any]] = None) -> str:
        """Request user approval for a suggestion.

        Args:
            suggestion: The suggestion to approve
            user_id: ID of user being asked
            context: Additional context (video name, project, etc)

        Returns:
            Approval request ID
        """
        approval_id = self._generate_approval_id()

        approval_event = {
            "id": approval_id,
            "event_type": "narrative_approval_requested",
            "user_id": user_id,
            "suggestion": suggestion,
            "context": context or {},
            "timestamp": datetime.utcnow().isoformat(),
            "status": ApprovalStatus.PENDING.value,
            "response": None
        }

        self.pending_approvals[approval_id] = approval_event

        return approval_id

    def submit_approval(self, approval_id: str,
                       approved: bool,
                       reasoning: str = "",
                       modifications: Optional[List[str]] = None) -> ApprovalResponse:
        """Submit approval response.

        Args:
            approval_id: ID of the approval request
            approved: Whether approved
            reasoning: User's reasoning
            modifications: Suggested modifications (if not approved)

        Returns:
            ApprovalResponse object
        """
        if approval_id not in self.pending_approvals:
            raise ValueError(f"Unknown approval ID: {approval_id}")

        approval_event = self.pending_approvals[approval_id]

        if approved:
            status = ApprovalStatus.APPROVED
        else:
            status = ApprovalStatus.MODIFIED if modifications else ApprovalStatus.REJECTED

        response = ApprovalResponse(
            approval_status=status,
            user_id=approval_event["user_id"],
            timestamp=datetime.utcnow().isoformat(),
            reasoning=reasoning,
            modifications=modifications or [],
            approved_with_changes=approved and bool(modifications)
        )

        # Update approval event
        approval_event["status"] = status.value
        approval_event["response"] = response.__dict__

        self.approval_history.append(response)

        return response

    def get_approval_status(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of an approval request"""
        if approval_id not in self.pending_approvals:
            return None

        return self.pending_approvals[approval_id]

    def is_approved(self, approval_id: str) -> bool:
        """Check if approval has been granted"""
        status_data = self.get_approval_status(approval_id)
        if not status_data:
            return False

        return status_data["status"] == ApprovalStatus.APPROVED.value

    def wait_for_response(self, approval_id: str,
                         poll_interval: float = 1.0,
                         max_wait: float = 3600.0) -> ApprovalResponse:
        """Block until approval response received.

        Args:
            approval_id: ID of approval request
            poll_interval: How often to check (seconds)
            max_wait: Maximum time to wait (seconds)

        Returns:
            ApprovalResponse when received

        Raises:
            TimeoutError: If max_wait exceeded
        """
        import time
        elapsed = 0

        while elapsed < max_wait:
            approval_event = self.pending_approvals.get(approval_id)
            if approval_event and approval_event["response"]:
                return ApprovalResponse(**approval_event["response"])

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(f"Approval {approval_id} expired after {max_wait}s")

    def get_approval_summary(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get summary of approvals.

        Args:
            user_id: Filter by user (optional)

        Returns:
            Summary statistics
        """
        approvals = self.approval_history
        if user_id:
            approvals = [a for a in approvals if a.user_id == user_id]

        if not approvals:
            return {
                "total": 0,
                "approved": 0,
                "rejected": 0,
                "modified": 0,
                "approval_rate": 0.0
            }

        approved_count = sum(1 for a in approvals if a.approval_status == ApprovalStatus.APPROVED)
        rejected_count = sum(1 for a in approvals if a.approval_status == ApprovalStatus.REJECTED)
        modified_count = sum(1 for a in approvals if a.approval_status == ApprovalStatus.MODIFIED)

        return {
            "total": len(approvals),
            "approved": approved_count,
            "rejected": rejected_count,
            "modified": modified_count,
            "approval_rate": approved_count / len(approvals) if approvals else 0.0,
            "most_common_modification": self._get_most_common_modification(approvals)
        }

    def _generate_approval_id(self) -> str:
        """Generate unique approval ID"""
        import uuid
        return f"approval_{uuid.uuid4().hex[:8]}"

    def _get_most_common_modification(self, approvals: List[ApprovalResponse]) -> Optional[str]:
        """Get most frequently requested modification"""
        modifications = {}
        for approval in approvals:
            for mod in approval.modifications:
                modifications[mod] = modifications.get(mod, 0) + 1

        if not modifications:
            return None

        return max(modifications.items(), key=lambda x: x[1])[0]

    def clear_history(self, older_than_hours: int = 24) -> int:
        """Clear old approval history.

        Args:
            older_than_hours: Remove approvals older than this many hours

        Returns:
            Number of entries removed
        """
        from datetime import timedelta
        cutoff_time = datetime.utcnow() - timedelta(hours=older_than_hours)

        original_count = len(self.approval_history)

        self.approval_history = [
            a for a in self.approval_history
            if datetime.fromisoformat(a.timestamp) > cutoff_time
        ]

        return original_count - len(self.approval_history)
