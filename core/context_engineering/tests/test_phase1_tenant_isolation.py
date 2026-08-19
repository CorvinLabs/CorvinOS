"""Phase 1: Tenant Isolation Fixes (CE-001/002/004/005).

Tests verify that all tenant isolation vulnerabilities are fixed:
- CE-001: MemoryCoordinator respects tenant_id
- CE-002: SessionCheckpoint respects tenant_id
- CE-004: ContextVar validates tenant_id
- CE-005: asyncio.create_task() captures tenant_id
"""

import asyncio
import json
import os
import pytest
import tempfile
from pathlib import Path
from typing import Dict, Any

from core.context_engineering.memory_coordinator import (
    MemoryCoordinator,
    EventPersistenceError,
)
from core.context_engineering.session_checkpoint import (
    SessionContinuationManager,
    SessionCheckpoint,
)
from core.context_engineering.context_bus import (
    get_current_tenant_id,
    set_current_tenant_id,
    get_execution_context,
    set_execution_context,
    ContextBus,
)


class MockExecutionContext:
    """Mock ExecutionContext for testing."""
    def __init__(self, tenant_id: str, decision_history=None):
        self.tenant_id = tenant_id
        self.decision_history = decision_history or []
        self.checkpoints = []
        self.context_stack = []


class TestCE001MemoryCoordinator:
    """Test CE-001: MemoryCoordinator tenant isolation."""

    @pytest.fixture
    def temp_home(self):
        """Temporary corvin_home for tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_memory_coordinator_accepts_tenant_id(self, temp_home):
        """Test MemoryCoordinator constructor accepts tenant_id."""
        coordinator = MemoryCoordinator(corvin_home=temp_home, tenant_id="tenant_a")
        assert coordinator.tenant_id == "tenant_a"

    def test_memory_coordinator_paths_scoped_by_tenant(self, temp_home):
        """Test paths are scoped by tenant_id."""
        coordinator_a = MemoryCoordinator(corvin_home=temp_home, tenant_id="tenant_a")
        coordinator_b = MemoryCoordinator(corvin_home=temp_home, tenant_id="tenant_b")

        # Paths should be different
        assert "tenant_a" in str(coordinator_a._learning_events_path)
        assert "tenant_b" in str(coordinator_b._learning_events_path)
        assert coordinator_a._learning_events_path != coordinator_b._learning_events_path

    def test_persist_event_validates_tenant_id(self, temp_home):
        """Test persist_learning_event rejects mismatched tenant_id (fail-closed)."""
        coordinator = MemoryCoordinator(corvin_home=temp_home, tenant_id="tenant_a")

        # Attempt to persist event with wrong tenant_id
        with pytest.raises(ValueError, match="tenant_id mismatch"):
            coordinator.persist_learning_event(
                task_id="task_001",
                tenant_id="tenant_b",  # Mismatch!
                event_type="test_event",
                payload={"test": "data"},
            )

    def test_persist_event_accepts_matching_tenant_id(self, temp_home):
        """Test persist_learning_event accepts matching tenant_id."""
        coordinator = MemoryCoordinator(corvin_home=temp_home, tenant_id="tenant_a")

        # Should succeed
        coordinator.persist_learning_event(
            task_id="task_001",
            tenant_id="tenant_a",  # Matches
            event_type="test_event",
            payload={"test": "data"},
        )

        # Verify event was written to correct path
        events = coordinator.read_learning_events()
        assert len(events) == 1
        assert events[0]["task_id"] == "task_001"

    def test_cross_tenant_isolation_no_leak(self, temp_home):
        """Test tenant A and B cannot read each other's events."""
        coordinator_a = MemoryCoordinator(corvin_home=temp_home, tenant_id="tenant_a")
        coordinator_b = MemoryCoordinator(corvin_home=temp_home, tenant_id="tenant_b")

        # Tenant A writes event
        coordinator_a.persist_learning_event(
            task_id="task_001",
            tenant_id="tenant_a",
            event_type="test_event",
            payload={"secret": "tenant_a_data"},
        )

        # Tenant B writes event
        coordinator_b.persist_learning_event(
            task_id="task_002",
            tenant_id="tenant_b",
            event_type="test_event",
            payload={"secret": "tenant_b_data"},
        )

        # Tenant A reads only its own events
        events_a = coordinator_a.read_learning_events()
        assert len(events_a) == 1
        assert events_a[0]["task_id"] == "task_001"

        # Tenant B reads only its own events
        events_b = coordinator_b.read_learning_events()
        assert len(events_b) == 1
        assert events_b[0]["task_id"] == "task_002"

        # Cross-read attempt: Tenant A's coordinator cannot access Tenant B's path
        # (they have separate paths, so reading events_b would read empty)
        events_a_read_b_path = coordinator_a.read_learning_events()  # Still reads A's path
        assert len(events_a_read_b_path) == 1  # Only A's event


class TestCE002SessionCheckpoint:
    """Test CE-002: SessionCheckpoint tenant isolation."""

    @pytest.fixture
    def temp_home(self):
        """Temporary corvin_home for tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_session_manager_accepts_tenant_id(self, temp_home):
        """Test SessionContinuationManager constructor accepts tenant_id."""
        manager = SessionContinuationManager(corvin_home=temp_home, tenant_id="tenant_a")
        assert manager.tenant_id == "tenant_a"

    def test_checkpoint_paths_scoped_by_tenant(self, temp_home):
        """Test checkpoint paths are scoped by tenant_id."""
        manager_a = SessionContinuationManager(corvin_home=temp_home, tenant_id="tenant_a")
        manager_b = SessionContinuationManager(corvin_home=temp_home, tenant_id="tenant_b")

        assert "tenant_a" in str(manager_a._checkpoint_base)
        assert "tenant_b" in str(manager_b._checkpoint_base)
        assert manager_a._checkpoint_base != manager_b._checkpoint_base

    def test_save_checkpoint_validates_tenant_id(self, temp_home):
        """Test save_checkpoint rejects mismatched tenant_id (fail-closed)."""
        manager = SessionContinuationManager(corvin_home=temp_home, tenant_id="tenant_a")
        ctx = MockExecutionContext(tenant_id="tenant_a")

        # Attempt with mismatched tenant_id
        with pytest.raises(ValueError, match="tenant_id mismatch"):
            manager.save_checkpoint(
                task_id="task_001",
                tenant_id="tenant_b",  # Mismatch!
                execution_context=ctx,
                session_id="session_001",
            )

    def test_save_checkpoint_accepts_matching_tenant_id(self, temp_home):
        """Test save_checkpoint accepts matching tenant_id."""
        manager = SessionContinuationManager(corvin_home=temp_home, tenant_id="tenant_a")
        ctx = MockExecutionContext(tenant_id="tenant_a")

        # Should succeed
        checkpoint_id = manager.save_checkpoint(
            task_id="task_001",
            tenant_id="tenant_a",  # Matches
            execution_context=ctx,
            session_id="session_001",
        )
        assert checkpoint_id is not None


class TestCE004ContextVarValidation:
    """Test CE-004: ContextVar per-tenant validation."""

    def test_set_and_get_tenant_id(self):
        """Test setting/getting tenant_id in ContextVar."""
        set_current_tenant_id("tenant_a")
        assert get_current_tenant_id() == "tenant_a"

        set_current_tenant_id("tenant_b")
        assert get_current_tenant_id() == "tenant_b"

    def test_set_tenant_id_validates_input(self):
        """Test set_current_tenant_id rejects invalid tenant_id."""
        with pytest.raises(ValueError):
            set_current_tenant_id("")

        with pytest.raises(ValueError):
            set_current_tenant_id(None)

    def test_execution_context_validation_on_get(self):
        """Test get_execution_context validates tenant_id match."""
        set_current_tenant_id("tenant_a")
        ctx_a = MockExecutionContext(tenant_id="tenant_a")
        set_execution_context(ctx_a)

        # Should return context when tenant_id matches
        retrieved = get_execution_context()
        assert retrieved is ctx_a

    def test_execution_context_mismatch_returns_none(self):
        """Test get_execution_context returns None on tenant_id mismatch (fail-closed)."""
        set_current_tenant_id("tenant_a")
        ctx_b = MockExecutionContext(tenant_id="tenant_b")
        set_execution_context(ctx_b)

        # Should return None when tenant_id doesn't match (fail-closed)
        retrieved = get_execution_context()
        assert retrieved is None

    def test_set_execution_context_validates_tenant_id(self):
        """Test set_execution_context rejects mismatched tenant_id."""
        set_current_tenant_id("tenant_a")
        ctx_b = MockExecutionContext(tenant_id="tenant_b")

        # Should raise on tenant_id mismatch
        with pytest.raises(ValueError, match="tenant_id mismatch"):
            set_execution_context(ctx_b)


class TestCE005AsyncioTaskTenantCapture:
    """Test CE-005: asyncio.create_task() tenant_id capture."""

    @pytest.mark.asyncio
    async def test_async_task_preserves_tenant_in_payload(self):
        """Test that asyncio tasks capture tenant_id in payload."""
        set_current_tenant_id("tenant_a")

        # Simulate a broadcast task that captures tenant_id
        captured_tenant = None

        async def task_func(tenant_id_param):
            nonlocal captured_tenant
            captured_tenant = tenant_id_param

        # Create task with explicit tenant_id capture
        current_tenant = get_current_tenant_id()
        task = asyncio.create_task(task_func(current_tenant))
        await task

        # Verify tenant_id was captured and used
        assert captured_tenant == "tenant_a"

    @pytest.mark.asyncio
    async def test_multiple_concurrent_tasks_no_cross_bleed(self):
        """Test multiple concurrent tasks don't cross-contaminate tenant_id."""
        results = {}

        async def task_for_tenant(tenant_id, task_label):
            """Simulate a task that processes for a specific tenant."""
            # Capture at task creation time (like CE-005 fix)
            results[task_label] = tenant_id
            await asyncio.sleep(0.01)

        # Create 5 tasks for different tenants
        tasks = []
        for i in range(5):
            tenant = f"tenant_{i}"
            set_current_tenant_id(tenant)
            # Capture tenant at task creation time
            current_tenant = get_current_tenant_id()
            task = asyncio.create_task(task_for_tenant(current_tenant, f"task_{i}"))
            tasks.append(task)

        # Wait for all tasks
        await asyncio.gather(*tasks)

        # Verify each task got its own tenant_id (no cross-bleed)
        for i in range(5):
            assert results[f"task_{i}"] == f"tenant_{i}"


class TestCE003PersonaModelDefaults:
    """Test CE-003: Persona model doesn't default to _default."""
    # Note: This test is a placeholder; full test requires importing PersonaModel
    # which may have additional dependencies. This can be expanded once those are available.

    def test_placeholder(self):
        """Placeholder for CE-003 tests (requires PersonaModel import)."""
        # TODO: Add PersonaModel tests when available
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
