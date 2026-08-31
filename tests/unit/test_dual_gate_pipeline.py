"""
Unit Tests for Dual-Gate Context Pipeline — ADR-0300

Tests for capability + audit gating.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

from core.pipeline import (
    DualGatePipeline,
    PipelineContext,
    PipelineExecutionError,
    CapabilityGateError,
    AuditGateError,
)
from core.audit import AuditChain


class MockCapabilityChecker:
    """Mock capability checker for testing."""

    def __init__(self):
        self.capabilities = {}

    def grant_capability(self, actor: str, capability: str, tenant_id: str):
        """Grant a capability."""
        key = (actor, capability, tenant_id)
        self.capabilities[key] = True

    def has_capability(self, actor: str, capability: str, tenant_id: str) -> bool:
        """Check if actor has capability."""
        key = (actor, capability, tenant_id)
        return self.capabilities.get(key, False)


class TestPipelineContext:
    """Test pipeline context."""

    def test_context_creation(self):
        """Create pipeline context."""
        ctx = PipelineContext(
            actor="user_123",
            capability="write_settings",
            action="update",
            resource="settings:theme",
            tenant_id="tenant_1",
            details={"theme": "dark"},
        )
        assert ctx.actor == "user_123"
        assert ctx.capability == "write_settings"
        assert ctx.action == "update"

    def test_context_minimal(self):
        """Create context with minimal fields."""
        ctx = PipelineContext(
            actor="user_123",
            capability="read",
            action="fetch",
            resource="doc:1",
            tenant_id="tenant_1",
        )
        assert ctx.details is None


class TestDualGatePipeline:
    """Test dual-gate pipeline."""

    @pytest.fixture
    def temp_audit(self):
        """Create temporary audit log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "audit.jsonl"

    @pytest.fixture
    def pipeline(self, temp_audit):
        """Create pipeline with mocks."""
        audit_chain = AuditChain(temp_audit)
        capability_checker = MockCapabilityChecker()
        return DualGatePipeline(audit_chain, capability_checker)

    def test_set_and_get_context(self, pipeline):
        """Set and retrieve context via ContextVars."""
        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )
        pipeline.set_context(ctx)

        assert pipeline.get_actor() == "user_1"
        assert pipeline.get_capability() == "write"
        assert pipeline.get_tenant_id() == "tenant_1"
        assert pipeline.get_resource() == "doc:1"

    def test_capability_check_granted(self, pipeline):
        """Capability check succeeds when granted."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        result = pipeline.check_capability("user_1", "write", "tenant_1")
        assert result is True

    def test_capability_check_denied(self, pipeline):
        """Capability check fails when denied."""
        result = pipeline.check_capability("user_1", "write", "tenant_1")
        assert result is False

    def test_capability_check_structural_error(self, pipeline):
        """Capability check raises on structural error."""
        pipeline.capability_checker.has_capability = MagicMock(
            side_effect=RuntimeError("Checker broken")
        )

        with pytest.raises(CapabilityGateError):
            pipeline.check_capability("user_1", "write", "tenant_1")

    def test_record_audit_success(self, pipeline):
        """Record audit entry successfully."""
        pipeline.record_audit(
            event_type="write",
            actor="user_1",
            action="update",
            resource="doc:1",
            result="success",
            tenant_id="tenant_1",
        )

        assert pipeline.audit_chain.entry_count() == 1

    def test_record_audit_with_details(self, pipeline):
        """Record audit with details dict."""
        details = {"field": "value", "nested": {"a": 1}}
        pipeline.record_audit(
            event_type="write",
            actor="user_1",
            action="update",
            resource="doc:1",
            result="success",
            tenant_id="tenant_1",
            details=details,
        )

        entries = pipeline.audit_chain.get_entries()
        assert entries[0].details == details

    def test_record_audit_structural_error(self, pipeline):
        """Record audit raises on structural error."""
        pipeline.audit_chain.record = MagicMock(
            side_effect=RuntimeError("Audit broken")
        )

        with pytest.raises(AuditGateError):
            pipeline.record_audit(
                event_type="write",
                actor="user_1",
                action="update",
                resource="doc:1",
                result="success",
                tenant_id="tenant_1",
            )

    def test_execute_guarded_success(self, pipeline):
        """Execute guarded: capability check + audit + execute."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        def my_func():
            return "success_result"

        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )

        result = pipeline.execute_guarded(ctx, my_func)

        assert result == "success_result"
        # Audit entries: pre-execution + success
        assert pipeline.audit_chain.entry_count() >= 1

    def test_execute_guarded_capability_denied(self, pipeline):
        """Execute guarded: capability denied."""
        # Do not grant capability

        def my_func():
            return "should_not_reach"

        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )

        with pytest.raises(CapabilityGateError):
            pipeline.execute_guarded(ctx, my_func)

        # Audit recorded denial
        assert pipeline.audit_chain.entry_count() == 1
        entries = pipeline.audit_chain.get_entries()
        assert entries[0].event_type == "capability_denied"

    def test_execute_guarded_function_raises(self, pipeline):
        """Execute guarded: function execution raises exception."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        def my_func():
            raise ValueError("Function error")

        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )

        with pytest.raises(ValueError):
            pipeline.execute_guarded(ctx, my_func)

        # Audit recorded pre-execution and failure
        assert pipeline.audit_chain.entry_count() >= 2
        entries = pipeline.audit_chain.get_entries()
        # Last entry should be failure
        assert any(e.result == "failure" for e in entries)

    def test_execute_guarded_with_args(self, pipeline):
        """Execute guarded with function arguments."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        def my_func(a, b, c=None):
            return a + b + (c or 0)

        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )

        result = pipeline.execute_guarded(ctx, my_func, 1, 2, c=3)
        assert result == 6

    def test_execute_guarded_context_isolated(self, pipeline):
        """Context is isolated per execution."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )
        pipeline.capability_checker.grant_capability(
            "user_2", "read", "tenant_2"
        )

        def check_context():
            return (pipeline.get_actor(), pipeline.get_capability())

        ctx1 = PipelineContext(
            actor="user_1",
            capability="write",
            action="a",
            resource="r1",
            tenant_id="tenant_1",
        )

        result1 = pipeline.execute_guarded(ctx1, check_context)
        assert result1 == ("user_1", "write")

        ctx2 = PipelineContext(
            actor="user_2",
            capability="read",
            action="b",
            resource="r2",
            tenant_id="tenant_2",
        )

        result2 = pipeline.execute_guarded(ctx2, check_context)
        assert result2 == ("user_2", "read")

    @pytest.mark.asyncio
    async def test_execute_guarded_async_success(self, pipeline):
        """Execute guarded async: capability + audit + execute."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        async def my_async_func():
            await asyncio.sleep(0.01)
            return "async_success"

        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )

        result = await pipeline.execute_guarded_async(ctx, my_async_func)
        assert result == "async_success"

    @pytest.mark.asyncio
    async def test_execute_guarded_async_sync_function(self, pipeline):
        """Execute guarded async with sync function."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        def my_sync_func():
            return "sync_in_async"

        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )

        result = await pipeline.execute_guarded_async(ctx, my_sync_func)
        assert result == "sync_in_async"

    def test_audit_chain_hash_verification(self, pipeline):
        """Audit chain maintains hash integrity through pipeline."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        def my_func():
            return "ok"

        ctx = PipelineContext(
            actor="user_1",
            capability="write",
            action="update",
            resource="doc:1",
            tenant_id="tenant_1",
        )

        pipeline.execute_guarded(ctx, my_func)

        # Verify chain integrity
        assert pipeline.audit_chain.verify_chain() is True

    def test_multiple_operations_audit_chain(self, pipeline):
        """Multiple operations build continuous hash chain."""
        pipeline.capability_checker.grant_capability(
            "user_1", "write", "tenant_1"
        )

        def func_a():
            return 1

        def func_b():
            return 2

        ctx1 = PipelineContext(
            actor="user_1",
            capability="write",
            action="a",
            resource="r1",
            tenant_id="tenant_1",
        )

        ctx2 = PipelineContext(
            actor="user_1",
            capability="write",
            action="b",
            resource="r2",
            tenant_id="tenant_1",
        )

        pipeline.execute_guarded(ctx1, func_a)
        pipeline.execute_guarded(ctx2, func_b)

        # All entries linked via hash chain
        entries = pipeline.audit_chain.get_entries()
        assert len(entries) >= 2
        assert entries[0].prior_hash == "genesis"
        assert entries[1].prior_hash == entries[0].self_hash
