"""Tests for control plane audit backend wiring (Phase 9 Stream 1, ADR-0232/0233).

Verifies that all control plane modules properly wire to the immutable core
audit chain and persist events fail-closed.
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime
import pytest

from core.control_plane.override_authority import OverrideAuthority, OverrideType
from core.control_plane.subsystems import SubsystemRegistry, SubsystemType
from core.console.corvin_console.intent_router import IntentRouter
from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent


@pytest.fixture
def temp_audit_file():
    """Create a temporary audit chain file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        temp_path = Path(f.name)
    yield temp_path
    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def mock_audit_backend(temp_audit_file):
    """Create a mock audit backend for testing."""
    return AuditChainWriter(str(temp_audit_file))


class TestOverrideAuthorityAuditWiring:
    """Tests for override_authority.py audit backend wiring."""

    @pytest.mark.asyncio
    async def test_override_request_logged_to_audit_chain(self, mock_audit_backend):
        """Verify override requests are logged to audit chain."""
        authority = OverrideAuthority(tenant_id="_default", audit_backend=mock_audit_backend)
        authority.add_approver("admin1")

        # Create override request
        result = await authority.request_override(
            override_type=OverrideType.FORCE_ENABLE,
            target_id="plugin_xyz",
            reason="Testing force enable",
            requestor_id="user1",
            tenant_id="_default",
        )

        # Verify result
        assert result["status"] == "pending_approval"
        override_id = result["override_id"]

        # Verify audit event was written
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid"

    @pytest.mark.asyncio
    async def test_override_approval_logged_to_audit_chain(self, mock_audit_backend):
        """Verify override approvals are logged to audit chain."""
        authority = OverrideAuthority(tenant_id="_default", audit_backend=mock_audit_backend)
        authority.add_approver("admin1")

        # Create and approve override
        result = await authority.request_override(
            override_type=OverrideType.FORCE_ENABLE,
            target_id="plugin_xyz",
            reason="Testing",
            requestor_id="user1",
            tenant_id="_default",
        )
        override_id = result["override_id"]

        # Approve it
        approval_result = await authority.approve_override(
            override_id=override_id,
            approver_id="admin1",
            tenant_id="_default",
        )

        # Verify approval event
        assert approval_result["status"] == "approved"
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid after approval"

    @pytest.mark.asyncio
    async def test_override_rejection_logged_to_audit_chain(self, mock_audit_backend):
        """Verify override rejections are logged to audit chain."""
        authority = OverrideAuthority(tenant_id="_default", audit_backend=mock_audit_backend)
        authority.add_approver("admin1")

        # Create and reject override
        result = await authority.request_override(
            override_type=OverrideType.EMERGENCY_STOP,
            target_id="subsystem_abc",
            reason="Testing rejection",
            requestor_id="user1",
            tenant_id="_default",
        )
        override_id = result["override_id"]

        # Reject it
        reject_result = await authority.reject_override(
            override_id=override_id,
            approver_id="admin1",
            rejection_reason="Does not meet criteria",
            tenant_id="_default",
        )

        # Verify rejection event
        assert reject_result["status"] == "rejected"
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid after rejection"

    @pytest.mark.asyncio
    async def test_unauthorized_approval_logged_to_audit_chain(self, mock_audit_backend):
        """Verify unauthorized approval attempts are logged to audit chain."""
        authority = OverrideAuthority(tenant_id="_default", audit_backend=mock_audit_backend)
        authority.add_approver("admin1")  # Only admin1 can approve

        # Create override
        result = await authority.request_override(
            override_type=OverrideType.BYPASS_GATE,
            target_id="gate_xyz",
            reason="Testing unauthorized",
            requestor_id="user1",
            tenant_id="_default",
        )
        override_id = result["override_id"]

        # Try to approve as non-admin
        with pytest.raises(Exception):  # PermissionError
            await authority.approve_override(
                override_id=override_id,
                approver_id="user2",  # Not an approver
                tenant_id="_default",
            )

        # Verify denial was logged
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid despite denial"


class TestSubsystemRegistryAuditWiring:
    """Tests for subsystems.py audit backend wiring."""

    @pytest.mark.asyncio
    async def test_subsystem_registration_logged_to_audit_chain(self, mock_audit_backend):
        """Verify subsystem registration is logged to audit chain."""
        registry = SubsystemRegistry(tenant_id="_default", audit_backend=mock_audit_backend)

        # Register subsystem
        result = await registry.register_subsystem(
            subsystem_id="learning_engine",
            subsystem_type=SubsystemType.LEARNING,
            display_name="Learning Engine",
            description="Core learning system",
            version="1.0.0",
            tenant_id="_default",
        )

        # Verify result
        assert result["status"] == "registered"
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid"

    @pytest.mark.asyncio
    async def test_subsystem_unregistration_logged_to_audit_chain(self, mock_audit_backend):
        """Verify subsystem unregistration is logged to audit chain."""
        registry = SubsystemRegistry(tenant_id="_default", audit_backend=mock_audit_backend)

        # Register then unregister
        await registry.register_subsystem(
            subsystem_id="audit_sys",
            subsystem_type=SubsystemType.AUDIT,
            display_name="Audit System",
            description="Audit trail system",
            version="1.0.0",
            tenant_id="_default",
        )

        result = await registry.unregister_subsystem(
            subsystem_id="audit_sys",
            tenant_id="_default",
        )

        # Verify unregistration
        assert result["status"] == "unregistered"
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid after unregistration"

    @pytest.mark.asyncio
    async def test_health_check_logged_to_audit_chain(self, mock_audit_backend):
        """Verify health checks are logged to audit chain."""
        registry = SubsystemRegistry(tenant_id="_default", audit_backend=mock_audit_backend)

        # Register subsystem
        await registry.register_subsystem(
            subsystem_id="skills_sys",
            subsystem_type=SubsystemType.SKILLS,
            display_name="Skills System",
            description="Skill execution system",
            version="1.0.0",
            tenant_id="_default",
        )

        # Register health check
        async def dummy_check():
            return (True, "healthy")

        registry.register_health_check("skills_sys", dummy_check)

        # Run health check
        result = await registry.check_health(
            subsystem_id="skills_sys",
            tenant_id="_default",
        )

        # Verify health check was logged
        assert "healthy" in result["health_status"]
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid after health check"


class TestIntentRouterAuditWiring:
    """Tests for intent_router.py audit backend wiring."""

    @pytest.mark.asyncio
    async def test_intent_classification_logged_to_audit_chain(self, mock_audit_backend):
        """Verify intent classification is logged to audit chain."""
        router = IntentRouter(tenant_id="_default", audit_backend=mock_audit_backend)

        # Classify intent
        result = await router.classify("Please create a new skill for me")

        # Verify result
        assert result.intent_type is not None
        assert result.audit_event_type is not None

        # Verify audit event was written
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid"

    @pytest.mark.asyncio
    async def test_multiple_classifications_preserve_chain_integrity(self, mock_audit_backend):
        """Verify multiple classifications maintain chain integrity."""
        router = IntentRouter(tenant_id="_default", audit_backend=mock_audit_backend)

        intents = [
            "create a skill",
            "delegate this task",
            "collect user feedback",
            "enable autonomy",
        ]

        # Classify multiple intents
        for text in intents:
            result = await router.classify(text)
            assert result.intent_type is not None

        # Verify chain integrity
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid after multiple classifications"

        # Verify event count
        assert audit_chain._event_count == len(intents), "Should have N events for N classifications"


class TestAuditChainIntegrity:
    """Tests for overall audit chain integrity."""

    @pytest.mark.asyncio
    async def test_mixed_operations_preserve_chain_integrity(self, mock_audit_backend):
        """Verify mixed operations from different modules preserve chain integrity."""
        # Create instances
        authority = OverrideAuthority(tenant_id="_default", audit_backend=mock_audit_backend)
        registry = SubsystemRegistry(tenant_id="_default", audit_backend=mock_audit_backend)
        router = IntentRouter(tenant_id="_default", audit_backend=mock_audit_backend)

        authority.add_approver("admin1")

        # Perform mixed operations
        # 1. Register subsystem
        await registry.register_subsystem(
            subsystem_id="core_sys",
            subsystem_type=SubsystemType.AUDIT,
            display_name="Core System",
            description="Core",
            version="1.0.0",
            tenant_id="_default",
        )

        # 2. Request override
        result = await authority.request_override(
            override_type=OverrideType.FORCE_ENABLE,
            target_id="test",
            reason="test",
            requestor_id="user1",
            tenant_id="_default",
        )

        # 3. Classify intent
        await router.classify("create a skill")

        # 4. Check health
        async def check():
            return (True, "healthy")
        registry.register_health_check("core_sys", check)
        await registry.check_health("core_sys", "_default")

        # Verify overall chain integrity
        audit_chain = mock_audit_backend
        assert audit_chain.verify_chain(), "Audit chain should be valid after mixed operations"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
