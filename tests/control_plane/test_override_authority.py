"""E2E tests for Override Authority (Phase 9b Stream 3)."""

import pytest
from core.control_plane.override_authority import (
    OverrideAuthority,
    OverrideType,
    ApprovalStatus,
    PermissionError,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    async def log_event(self, event_type: str, payload: dict):
        """Log event."""
        self.events.append({"type": event_type, "payload": payload})


@pytest.mark.asyncio
async def test_request_override():
    """Test requesting an override."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    result = await authority.request_override(
        OverrideType.FORCE_ENABLE,
        "learning_subsystem",
        "Emergency need for learning subsystem",
        "operator_1",
        "tenant_1",
    )

    assert result["status"] == "pending_approval"
    assert "override_id" in result
    assert audit.events[-1]["type"] == "override_requested"


@pytest.mark.asyncio
async def test_approve_override():
    """Test approving an override."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    # Add approver
    authority.add_approver("admin_1")

    # Request override
    override = await authority.request_override(
        OverrideType.FORCE_ENABLE,
        "learning",
        "Emergency",
        "operator_1",
        "tenant_1",
    )

    # Approve
    result = await authority.approve_override(override["override_id"], "admin_1", "tenant_1")

    assert result["status"] == "approved"
    assert audit.events[-1]["type"] == "override_approved"


@pytest.mark.asyncio
async def test_reject_override():
    """Test rejecting an override."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    # Add approver
    authority.add_approver("admin_1")

    # Request override
    override = await authority.request_override(
        OverrideType.FORCE_DISABLE,
        "learning",
        "Not justified",
        "operator_1",
        "tenant_1",
    )

    # Reject
    result = await authority.reject_override(
        override["override_id"],
        "admin_1",
        "Risk too high",
        "tenant_1",
    )

    assert result["status"] == "rejected"
    assert audit.events[-1]["type"] == "override_rejected"


@pytest.mark.asyncio
async def test_unauthorized_approver():
    """Test non-approver cannot approve."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    override = await authority.request_override(
        OverrideType.FORCE_ENABLE,
        "learning",
        "Emergency",
        "operator_1",
        "tenant_1",
    )

    # Try to approve as non-admin
    with pytest.raises(PermissionError):
        await authority.approve_override(override["override_id"], "non_admin", "tenant_1")

    assert audit.events[-1]["type"] == "override_approve_denied"


@pytest.mark.asyncio
async def test_empty_reason_rejection():
    """Test empty reason is rejected."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    with pytest.raises(ValueError):
        await authority.request_override(
            OverrideType.FORCE_ENABLE,
            "learning",
            "",  # Empty reason
            "operator_1",
            "tenant_1",
        )


@pytest.mark.asyncio
async def test_get_override_status():
    """Test getting override status."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    override = await authority.request_override(
        OverrideType.EMERGENCY_STOP,
        "workflow",
        "Runaway process",
        "operator_1",
        "tenant_1",
    )

    status = authority.get_override_status(override["override_id"], "tenant_1")

    assert status["approval_status"] == "pending"
    assert status["override_type"] == "emergency_stop"
    assert status["target_id"] == "workflow"


@pytest.mark.asyncio
async def test_duplicate_approval_denied():
    """Test cannot approve an already-approved override."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)
    authority.add_approver("admin_1")

    override = await authority.request_override(
        OverrideType.FORCE_ENABLE,
        "learning",
        "Emergency",
        "operator_1",
        "tenant_1",
    )

    # Approve once
    await authority.approve_override(override["override_id"], "admin_1", "tenant_1")

    # Try to approve again
    with pytest.raises(ValueError, match="already approved"):
        await authority.approve_override(override["override_id"], "admin_1", "tenant_1")


@pytest.mark.asyncio
async def test_list_pending_overrides():
    """Test listing pending overrides."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)
    authority.add_approver("admin_1")

    # Create multiple overrides
    await authority.request_override(
        OverrideType.FORCE_ENABLE,
        "learning",
        "Emergency",
        "operator_1",
        "tenant_1",
    )

    await authority.request_override(
        OverrideType.FORCE_DISABLE,
        "vibe",
        "Maintenance",
        "operator_2",
        "tenant_1",
    )

    # Approve one
    pending = authority.list_pending_overrides("tenant_1")
    first_override_id = pending[0]["override_id"]
    await authority.approve_override(first_override_id, "admin_1", "tenant_1")

    # List pending (should have 1)
    remaining = authority.list_pending_overrides("tenant_1")
    assert len(remaining) == 1
    assert remaining[0]["override_type"] == "force_disable"


@pytest.mark.asyncio
async def test_approver_management():
    """Test adding/removing approvers."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    # Initially no approvers
    assert not authority.is_approver("admin_1")

    # Add approver
    authority.add_approver("admin_1")
    assert authority.is_approver("admin_1")

    # Remove approver
    authority.remove_approver("admin_1")
    assert not authority.is_approver("admin_1")


@pytest.mark.asyncio
async def test_tenant_isolation():
    """Test tenant isolation for overrides."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    # Create overrides for different tenants
    override_t1 = await authority.request_override(
        OverrideType.FORCE_ENABLE,
        "learning",
        "Emergency",
        "operator_1",
        "tenant_1",
    )

    override_t2 = await authority.request_override(
        OverrideType.FORCE_ENABLE,
        "learning",
        "Emergency",
        "operator_1",
        "tenant_2",
    )

    # Tenant 1 should not access tenant 2's override
    with pytest.raises(ValueError, match="Access denied"):
        authority.get_override_status(override_t2["override_id"], "tenant_1")

    # Tenant 2 can access its own
    status = authority.get_override_status(override_t2["override_id"], "tenant_2")
    assert status["override_id"] == override_t2["override_id"]


@pytest.mark.asyncio
async def test_override_types():
    """Test all override types."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(audit)

    override_types = [
        OverrideType.FORCE_ENABLE,
        OverrideType.FORCE_DISABLE,
        OverrideType.EMERGENCY_STOP,
        OverrideType.BYPASS_GATE,
        OverrideType.FORCE_RESTART,
    ]

    for override_type in override_types:
        result = await authority.request_override(
            override_type,
            "target",
            f"Test {override_type.value}",
            "operator",
            "tenant_1",
        )
        assert "override_id" in result
