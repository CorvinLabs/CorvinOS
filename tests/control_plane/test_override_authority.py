"""E2E tests for Override Authority (Phase 9b Stream 3)."""

import pytest
from core.control_plane.override_authority import (
    OverrideAuthority,
    OverrideType,
    ApprovalStatus,
    PermissionError,
)


class MockAuditBackend:
    """Records what OverrideAuthority hands the core chain writer.

    The authority calls ``write_event_dict`` (the ``AuditChainWriter`` API);
    this stub used to expose an async ``log_event`` nothing calls, and was
    passed positionally into the ``tenant_id`` slot — every test here failed
    with ``tenant_id must be str`` (stale since the core-chain rewiring).
    """

    def __init__(self):
        self.events = []

    def write_event_dict(self, event_type, tenant_id, user_id=None, details=None, severity=None):
        self.events.append({"type": event_type, "tenant_id": tenant_id,
                            "user_id": user_id, "payload": dict(details or {})})
        return f"hash{len(self.events)}"


@pytest.mark.asyncio
async def test_request_override():
    """Test requesting an override."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)
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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)
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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)

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


@pytest.mark.asyncio
async def test_cross_tenant_decision_refused():
    """A session of tenant_2 cannot approve / reject tenant_1's request
    (adversarial review 2026-09-27: approve/reject had no tenant check)."""
    audit = MockAuditBackend()
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)
    authority.add_approver("admin_1")
    ov = await authority.request_override(
        OverrideType.FORCE_ENABLE, "learning", "Emergency", "operator_1", "tenant_1")

    with pytest.raises(ValueError, match="Access denied"):
        await authority.approve_override(ov["override_id"], "admin_1", "tenant_2")
    with pytest.raises(ValueError, match="Access denied"):
        await authority.reject_override(ov["override_id"], "admin_1", "no", "tenant_2")
    assert authority.get_override_status(ov["override_id"], "tenant_1")["approval_status"] == "pending"


@pytest.mark.asyncio
async def test_rejection_reason_text_not_audited():
    audit = MockAuditBackend()
    authority = OverrideAuthority(tenant_id="tenant_1", audit_backend=audit)
    authority.add_approver("admin_1")
    ov = await authority.request_override(
        OverrideType.FORCE_ENABLE, "learning", "Emergency", "operator_1", "tenant_1")
    await authority.reject_override(ov["override_id"], "admin_1", "call me at 555-0100", "tenant_1")
    assert "555-0100" not in str(audit.events)


@pytest.mark.asyncio
async def test_records_land_in_the_chain_of_the_override_tenant(tmp_path, monkeypatch):
    """The console's single authority is built for ``_default``; a request of
    another tenant must be written to THAT tenant's chain (it used to land in
    ``_default``'s) and be readable back through ``get_audit_log``."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    from core.compliance import audit_chain_provider
    from corvin_operator.bridges.shared.paths import tenant_audit_chain

    monkeypatch.setattr(audit_chain_provider, "_CHAIN_WRITERS", {})
    # a console serving tenant acme (the forge writer refuses a record whose
    # tenant is not the process tenant; before the fix the path was _default)
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    authority = OverrideAuthority(tenant_id="_default")
    ov = await authority.request_override(
        OverrideType.FORCE_DISABLE, "vibe", "Maintenance", "fp_operator", "acme")

    acme = tenant_audit_chain("acme")
    assert acme.exists() and ov["override_id"] in acme.read_text()
    default = tenant_audit_chain("_default")
    assert not default.exists() or ov["override_id"] not in default.read_text()
    log = await authority.get_audit_log("acme")
    assert [e["event_type"] for e in log] == ["override_requested"]
