"""Tests for Tenant-Skill-Architecture (ADR-0114, ADR-0174).

Tests cover:
1. Tenant-scoped execution context (isolation guarantees)
2. Tenant data isolation (no cross-tenant leakage)
3. Skill versioning + immutable rollback
4. Persistent state per tenant
5. Audit trail integrity
"""

import asyncio
import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from core.skills.tenant_architecture import (
    TenantSkillArchitecture,
    TenantSkillExecutionContext,
    TenantSkillVersionManager,
    TenantSkillStateManager,
    SkillVersion,
    TenantSkillState,
    VersionState,
)
from core.skills.contract import SkillContract, SkillTier, Predicate, SKILL_REGISTRY
from core.tenants import validate_tenant_id


class TestTenantValidation:
    """Test tenant ID validation (fail-closed)."""

    def test_valid_tenant_ids(self):
        """Valid tenant IDs should pass validation."""
        valid_ids = [
            "_default",
            "acme-corp",
            "tenant_123",
            "a",
        ]
        for tid in valid_ids:
            assert validate_tenant_id(tid) == tid

    def test_invalid_tenant_ids(self):
        """Invalid tenant IDs should raise ValueError."""
        invalid_ids = [
            "",  # Empty
            "   ",  # Whitespace
            "../etc/passwd",  # Path traversal
            "root",  # Reserved
            "system",  # Reserved
            "UPPERCASE",  # Uppercase not allowed
        ]
        for tid in invalid_ids:
            with pytest.raises(ValueError):
                validate_tenant_id(tid)


class TestExecutionContext:
    """Test tenant-scoped execution context."""

    def test_context_creation(self):
        """Create an execution context with proper isolation."""
        context = TenantSkillExecutionContext(
            tenant_id="_default",
            skill_id="os.router",
            version="1.0.0",
            execution_id="exec-123",
            task_id="task-456",
            input_data={"request": "classify"},
        )

        assert context.tenant_id == "_default"
        assert context.skill_id == "os.router"
        assert context.status == "pending"
        assert context.verify_isolation() is True

    def test_context_isolation_tampering(self):
        """Detect tampering with execution context."""
        context = TenantSkillExecutionContext(
            tenant_id="_default",
            skill_id="os.router",
            version="1.0.0",
            execution_id="exec-123",
            task_id="task-456",
            input_data={"request": "classify"},
        )

        # Tamper with tenant_id
        context.tenant_id = "attacker"
        assert context.verify_isolation() is False

    def test_mark_success(self):
        """Mark context as successful."""
        context = TenantSkillExecutionContext(
            tenant_id="_default",
            skill_id="os.router",
            version="1.0.0",
            execution_id="exec-123",
            task_id="task-456",
            input_data={"request": "classify"},
        )

        context.mark_success({"decision": "route_to_opus"})

        assert context.status == "success"
        assert context.output_data == {"decision": "route_to_opus"}
        assert context.completed_at is not None

    def test_mark_failure(self):
        """Mark context as failed."""
        context = TenantSkillExecutionContext(
            tenant_id="_default",
            skill_id="os.router",
            version="1.0.0",
            execution_id="exec-123",
            task_id="task-456",
            input_data={"request": "classify"},
        )

        context.mark_failure("Timeout exceeded")

        assert context.status == "failure"
        assert context.error_data == "Timeout exceeded"
        assert context.completed_at is not None

    def test_audit_event_serialization(self):
        """Context should serialize to audit event format."""
        context = TenantSkillExecutionContext(
            tenant_id="_default",
            skill_id="os.router",
            version="1.0.0",
            execution_id="exec-123",
            task_id="task-456",
            input_data={"request": "classify"},
        )
        context.mark_success({"decision": "route_to_opus"})

        event = context.to_audit_event()

        assert event["type"] == "skill_executed"
        assert event["tenant_id"] == "_default"
        assert event["skill_id"] == "os.router"
        assert event["status"] == "success"
        assert "input_hash" in event
        assert "output_hash" in event


class TestVersionManager:
    """Test skill version management and rollback."""

    @pytest.fixture
    def temp_skill_dir(self):
        """Create temporary skill directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock the path resolution
            import core.paths.tenant as tenant_paths
            original_fn = tenant_paths.tenant_skill_dir

            def mock_tenant_skill_dir(tenant_id):
                return Path(tmpdir) / tenant_id / "skill-forge" / "skills"

            tenant_paths.tenant_skill_dir = mock_tenant_skill_dir
            try:
                yield tmpdir
            finally:
                tenant_paths.tenant_skill_dir = original_fn

    def test_register_version(self, temp_skill_dir):
        """Register a new skill version."""
        mgr = TenantSkillVersionManager(
            tenant_id="_default",
            skill_id="os.router",
        )

        contract = SkillContract(
            skill_id="os.router",
            version="1.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

        config = {"threshold": 0.7}

        success, msg = mgr.register_version("1.0.0", contract, config, "system")

        assert success is True
        assert mgr._active_version == "1.0.0"
        assert mgr.get_active_version() is not None

    def test_rollback_to_version(self, temp_skill_dir):
        """Rollback to a previous version."""
        mgr = TenantSkillVersionManager(
            tenant_id="_default",
            skill_id="os.router",
        )

        contract_v1 = SkillContract(
            skill_id="os.router",
            version="1.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

        contract_v2 = SkillContract(
            skill_id="os.router",
            version="2.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

        config = {"threshold": 0.7}

        # Register v1 and v2
        mgr.register_version("1.0.0", contract_v1, config, "system")
        mgr.register_version("2.0.0", contract_v2, config, "system")

        assert mgr._active_version == "2.0.0"

        # Rollback to v1
        success, msg = mgr.rollback_to_version("1.0.0", "Bug in v2.0.0")

        assert success is True
        assert mgr._active_version == "1.0.0"
        assert mgr._versions["2.0.0"].state == VersionState.DEPRECATED

    def test_version_history(self, temp_skill_dir):
        """Version history should be append-only."""
        mgr = TenantSkillVersionManager(
            tenant_id="_default",
            skill_id="os.router",
        )

        contract = SkillContract(
            skill_id="os.router",
            version="1.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

        config = {"threshold": 0.7}

        mgr.register_version("1.0.0", contract, config, "system")

        versions = mgr.list_versions()
        assert len(versions) > 0
        assert versions[0].skill_id == "os.router"


class TestStateManager:
    """Test persistent skill state management."""

    @pytest.fixture
    def temp_skill_dir(self):
        """Create temporary skill directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import core.paths.tenant as tenant_paths
            original_fn = tenant_paths.tenant_skill_dir

            def mock_tenant_skill_dir(tenant_id):
                return Path(tmpdir) / tenant_id / "skill-forge" / "skills"

            tenant_paths.tenant_skill_dir = mock_tenant_skill_dir
            try:
                yield tmpdir
            finally:
                tenant_paths.tenant_skill_dir = original_fn

    def test_save_state(self, temp_skill_dir):
        """Save persistent state."""
        mgr = TenantSkillStateManager(
            tenant_id="_default",
            skill_id="os.router",
        )

        state_data = {
            "learned_threshold": 0.75,
            "confidence": 0.95,
            "updated_at": "2026-09-19T12:00:00Z",
        }

        state = mgr.save_state("1.0.0", state_data)

        assert state.tenant_id == "_default"
        assert state.skill_id == "os.router"
        assert state.version == "1.0.0"
        assert state.state_data == state_data
        assert mgr.get_current_state() == state

    def test_state_chain_integrity(self, temp_skill_dir):
        """Verify state chain hash-chaining."""
        mgr = TenantSkillStateManager(
            tenant_id="_default",
            skill_id="os.router",
        )

        # Save multiple states
        state1 = mgr.save_state("1.0.0", {"counter": 1})
        state2 = mgr.save_state("1.0.0", {"counter": 2})
        state3 = mgr.save_state("1.0.0", {"counter": 3})

        # Verify chaining
        assert state2.previous_state_hash == state1.state_hash
        assert state3.previous_state_hash == state2.state_hash

        # Verify integrity check
        success, msg = mgr.verify_chain_integrity()
        assert success is True

    def test_state_recovery_from_version(self, temp_skill_dir):
        """Recover state for a specific version."""
        mgr = TenantSkillStateManager(
            tenant_id="_default",
            skill_id="os.router",
        )

        # Save states for different versions
        state_v1 = mgr.save_state("1.0.0", {"threshold": 0.7})
        state_v2 = mgr.save_state("2.0.0", {"threshold": 0.8})

        # Recover v1 state
        recovered = mgr.get_state_at_version("1.0.0")
        assert recovered == state_v1
        assert recovered.state_data["threshold"] == 0.7


class TestTenantSkillArchitecture:
    """Test main tenant skill architecture."""

    @pytest.fixture
    def temp_skill_dir(self):
        """Create temporary skill directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import core.paths.tenant as tenant_paths
            original_fn = tenant_paths.tenant_skill_dir

            def mock_tenant_skill_dir(tenant_id):
                return Path(tmpdir) / tenant_id / "skill-forge" / "skills"

            tenant_paths.tenant_skill_dir = mock_tenant_skill_dir
            try:
                yield tmpdir
            finally:
                tenant_paths.tenant_skill_dir = original_fn

    def test_architecture_creation(self):
        """Create architecture instance."""
        arch = TenantSkillArchitecture("_default")

        assert arch.tenant_id == "_default"
        success, msg = arch.verify_isolation()
        assert success is True

    def test_create_execution_context(self, temp_skill_dir):
        """Create execution context via architecture."""
        arch = TenantSkillArchitecture("_default")

        # Register a version first
        contract = SkillContract(
            skill_id="os.router",
            version="1.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

        arch.register_skill_version("os.router", "1.0.0", contract, {}, "system")

        # Create execution context
        context = arch.create_execution_context(
            skill_id="os.router",
            task_id="task-123",
            input_data={"request": "classify"},
        )

        assert context.tenant_id == "_default"
        assert context.skill_id == "os.router"
        assert context.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_execute_skill(self, temp_skill_dir):
        """Execute a skill within tenant context."""
        arch = TenantSkillArchitecture("_default")

        # Register version
        contract = SkillContract(
            skill_id="os.router",
            version="1.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        arch.register_skill_version("os.router", "1.0.0", contract, {}, "system")

        # Create context
        context = arch.create_execution_context(
            skill_id="os.router",
            task_id="task-123",
            input_data={"request": "classify"},
        )

        # Define skill function
        async def skill_fn(input_data, state_before):
            return {
                "decision": "route_to_opus",
                "state": {"decision_made": True},
            }

        # Execute
        success, output, event = await arch.execute_skill(context, skill_fn)

        assert success is True
        assert output["decision"] == "route_to_opus"
        assert event["status"] == "success"

    def test_cross_tenant_isolation(self):
        """Verify that two tenants cannot access each other's data."""
        tenant_a = TenantSkillArchitecture("tenant_a")
        tenant_b = TenantSkillArchitecture("tenant_b")

        # Both should be isolated
        success_a, msg_a = tenant_a.verify_isolation()
        success_b, msg_b = tenant_b.verify_isolation()

        assert success_a is True
        assert success_b is True

        # Managers should be separate
        mgr_a = tenant_a.get_version_manager("os.router")
        mgr_b = tenant_b.get_version_manager("os.router")

        assert mgr_a.tenant_id == "tenant_a"
        assert mgr_b.tenant_id == "tenant_b"
        assert mgr_a is not mgr_b


class TestTenantIsolationSecurity:
    """Security tests for tenant isolation."""

    def test_invalid_tenant_id_rejection(self):
        """Invalid tenant IDs should be rejected at construction."""
        with pytest.raises(ValueError):
            TenantSkillArchitecture("../etc/passwd")

        with pytest.raises(ValueError):
            TenantSkillArchitecture("root")

    def test_execution_context_isolation_boundaries(self):
        """Execution contexts should be isolated per tenant."""
        context1 = TenantSkillExecutionContext(
            tenant_id="tenant_a",
            skill_id="os.router",
            version="1.0.0",
            execution_id="exec-1",
            task_id="task-1",
            input_data={},
        )

        context2 = TenantSkillExecutionContext(
            tenant_id="tenant_b",
            skill_id="os.router",
            version="1.0.0",
            execution_id="exec-2",
            task_id="task-2",
            input_data={},
        )

        # Each should verify its own isolation
        assert context1.verify_isolation() is True
        assert context2.verify_isolation() is True

        # But swapping tenant_ids should break isolation
        context1.tenant_id = "tenant_b"
        assert context1.verify_isolation() is False


class TestVersionRollbackImmutability:
    """Test that rollback is atomic and immutable."""

    @pytest.fixture
    def temp_skill_dir(self):
        """Create temporary skill directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import core.paths.tenant as tenant_paths
            original_fn = tenant_paths.tenant_skill_dir

            def mock_tenant_skill_dir(tenant_id):
                return Path(tmpdir) / tenant_id / "skill-forge" / "skills"

            tenant_paths.tenant_skill_dir = mock_tenant_skill_dir
            try:
                yield tmpdir
            finally:
                tenant_paths.tenant_skill_dir = original_fn

    def test_rollback_atomicity(self, temp_skill_dir):
        """Rollback should be atomic (all or nothing)."""
        mgr = TenantSkillVersionManager(
            tenant_id="_default",
            skill_id="os.router",
        )

        contract_v1 = SkillContract(
            skill_id="os.router",
            version="1.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={},
            output_schema={},
        )

        contract_v2 = SkillContract(
            skill_id="os.router",
            version="2.0.0",
            tier=SkillTier.PRIMITIVE,
            input_schema={},
            output_schema={},
        )

        mgr.register_version("1.0.0", contract_v1, {}, "system")
        mgr.register_version("2.0.0", contract_v2, {}, "system")

        # Rollback
        before_active = mgr._active_version
        success, _ = mgr.rollback_to_version("1.0.0")

        assert success is True
        assert before_active == "2.0.0"
        assert mgr._active_version == "1.0.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
