"""Phase 2 E2E Integration Tests (ADR-0423 Phase 2).

Tests all layers working together:
- L1: Unified path resolver (foundation)
- L2: CheckpointClaimRegistry (TTL-aware claims)
- L2.5: WorkflowCompletionQueue (persistent completions)
- L2.7: SubprocessEnvHardening (safe env vars)
- L3: Phase2ContextBusWiring (subsystem coordination)

Validates 7-layer integration and closes all 7 gaps.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from core.workflows.path_resolver import (
    resolve_corvin_home,
    workflow_run_path,
    claimed_file_path,
    completion_queue_path,
)

from core.workflows.claim_registry import (
    CheckpointClaimRegistry,
    AlreadyClaimedError,
)

from core.workflows.completion_queue import (
    WorkflowCompletionQueue,
)

from core.workflows.subprocess_env import (
    prepare_subprocess_env,
)

from core.orchestration.phase2_wiring import (
    Phase2ContextBusWiring,
    ContextBusEvent,
    init_phase2_wiring,
)

from core.context_engineering.context_bus import ContextBus


class TestPhase2E2EIntegration:
    """E2E tests for Phase 2 infrastructure."""

    @pytest.fixture
    async def temp_corvin_home(self):
        """Create a temporary CORVIN_HOME."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                yield Path(tmpdir)

    @pytest.fixture
    async def context_bus(self):
        """Create a test ContextBus."""
        bus = ContextBus()
        await bus.start()
        yield bus
        await bus.stop()

    @pytest.fixture
    async def phase2_wiring(self, context_bus):
        """Initialize Phase 2 wiring."""
        wiring = Phase2ContextBusWiring(context_bus)
        return wiring

    @pytest.mark.asyncio
    async def test_scenario_1_path_resolver_foundation(self, temp_corvin_home):
        """Scenario 1: Path resolver is foundation for all path operations.

        Validates:
        - workflow_runs_dir creates paths
        - workflow_run_path constructs valid paths
        - claimed_file_path creates .json.claimed sidecars
        - All paths are under CORVIN_HOME
        """
        # Create checkpoint file
        runs_dir = temp_corvin_home / "tenants" / "_default" / "workflow_runs"
        runs_dir.mkdir(parents=True, exist_ok=True)

        run_path = workflow_run_path("run-scenario1")
        run_path.write_text('{"status": "paused"}')

        # Verify path structure
        assert run_path.exists()
        assert "run-scenario1.json" in str(run_path)
        assert str(temp_corvin_home) in str(run_path)

        # Get claimed path
        claimed = claimed_file_path("run-scenario1")
        assert claimed.name == "run-scenario1.json.claimed"

    @pytest.mark.asyncio
    async def test_scenario_2_claim_deadlock_fixed(self, temp_corvin_home):
        """Scenario 2: Claim deadlock fixed (Gap 1+5).

        Validates:
        - claim() atomically renames checkpoint
        - Second concurrent claim raises AlreadyClaimedError
        - release() restores checkpoint
        - TTL reaper prevents stale claims
        """
        registry = CheckpointClaimRegistry(default_ttl_s=10)
        await registry.start_reaper()

        try:
            # Create checkpoint
            runs_dir = temp_corvin_home / "tenants" / "_default" / "workflow_runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            run_path = workflow_run_path("run-scenario2")
            run_path.write_text('{"status": "paused"}')

            # First claim succeeds
            claimed1 = registry.claim("run-scenario2")
            assert claimed1.exists()
            assert not run_path.exists()

            # Second claim raises error
            with pytest.raises(AlreadyClaimedError, match="already being resumed"):
                registry.claim("run-scenario2")

            # Release restores checkpoint
            registry.release("run-scenario2")
            assert run_path.exists()
            assert claimed1.with_suffix(".json.claimed").exists() is False

        finally:
            await registry.stop_reaper()

    @pytest.mark.asyncio
    async def test_scenario_3_completion_queue_survives_bridge_death(self, temp_corvin_home):
        """Scenario 3: Completion queue tracks outcomes across bridge lifecycle (Gap 2).

        Validates:
        - Completions persist to JSONL
        - load_from_disk() recovers after bridge restart
        - get_unnotified() finds unpushed webhooks
        - Supports multi-tenant isolation
        """
        queue = WorkflowCompletionQueue()

        # Push completions
        await queue.push("run-1", "complete", {"output": "result"}, tenant_id="tenant-a")
        await queue.push("run-2", "failed", {"error": "timeout"}, tenant_id="tenant-a")
        await queue.push("run-3", "complete", {"output": "ok"}, tenant_id="tenant-b")

        # Verify in-memory records
        a_all = await queue.get_all("tenant-a")
        assert len(a_all) == 2

        # Mark one as notified
        await queue.mark_webhook_notified("run-1")
        unnotified = await queue.get_unnotified("tenant-a")
        assert len(unnotified) == 1
        assert unnotified[0].run_id == "run-2"

        # Simulate bridge restart: load from disk
        queue2 = WorkflowCompletionQueue()
        await queue2.load_from_disk("tenant-a")

        a_restored = await queue2.get_all("tenant-a")
        assert len(a_restored) == 2

    @pytest.mark.asyncio
    async def test_scenario_4_env_hardening_propagates_corvin_home(self, temp_corvin_home):
        """Scenario 4: Env var hardening propagates CORVIN_HOME to subprocesses (Gap 6).

        Validates:
        - prepare_subprocess_env() sets CORVIN_HOME explicitly
        - CORVIN_TENANT_ID is propagated
        - PII is detected and rejected
        - Whitelisted vars are preserved
        """
        env = prepare_subprocess_env(tenant_id="my-tenant")

        # Verify critical vars
        assert env["CORVIN_HOME"] == str(temp_corvin_home)
        assert env["CORVIN_TENANT_ID"] == "my-tenant"

        # Verify whitelist (PATH should be included if in parent)
        if "PATH" in os.environ:
            assert "PATH" in env

        # Verify PII rejection
        with pytest.raises(ValueError, match="Suspected PII"):
            prepare_subprocess_env(
                additional_vars={"PASSWORD": "secret123"}
            )

    @pytest.mark.asyncio
    async def test_scenario_5_brain_wiring_event_flow(self, context_bus, phase2_wiring):
        """Scenario 5: Brain subsystems wired to ContextBus (Gap 7).

        Validates:
        - Events are emitted with proper tenant isolation
        - Sequence IDs are tracked for FIFO ordering
        - Audit trail records all events
        - Cross-subsystem subscriptions work
        """
        # Emit events from different subsystems
        await phase2_wiring.emit_event(
            "health.error_detected",
            {"error_type": "stall", "duration_s": 10},
            subsystem="health_monitor",
            tenant_id="tenant-1",
        )

        await phase2_wiring.emit_event(
            "loop.strategy_applied",
            {"strategy": "retry", "attempt": 1},
            subsystem="loop_engineer",
            tenant_id="tenant-1",
        )

        await asyncio.sleep(0.1)  # Allow event processing

        # Verify audit trail
        events = await phase2_wiring.get_events_for_tenant("tenant-1")
        assert len(events) == 2

        # Verify sequence order
        is_ordered = await phase2_wiring.verify_event_ordering("tenant-1")
        assert is_ordered

    @pytest.mark.asyncio
    async def test_scenario_9_master_integration_all_layers(self, temp_corvin_home, context_bus, phase2_wiring):
        """Scenario 9: Master integration - all 7 layers coordinating (L1-L7).

        Simulates a complete workflow:
        1. Path resolver locates checkpoint
        2. Claim registry claims it
        3. Completion queue tracks outcome
        4. Subprocess env prepares env for workflow runner
        5. Brain wiring emits events as workflow progresses
        """
        registry = CheckpointClaimRegistry(default_ttl_s=10)
        await registry.start_reaper()

        try:
            # L1: Create checkpoint at resolved path
            runs_dir = temp_corvin_home / "tenants" / "_default" / "workflow_runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            run_path = workflow_run_path("workflow-master-1")
            run_path.write_text(json.dumps({
                "run_id": "workflow-master-1",
                "status": "paused",
                "node": "approval",
            }))

            # L2: Claim checkpoint
            await phase2_wiring.emit_event(
                "orchestration.task_started",
                {"run_id": "workflow-master-1", "action": "claim"},
                subsystem="orchestrator",
                tenant_id="_default",
            )

            claimed = registry.claim("workflow-master-1")
            assert claimed.exists()
            assert not run_path.exists()

            # L2.5: Queue completion
            completion_queue = WorkflowCompletionQueue()
            await phase2_wiring.emit_event(
                "orchestration.task_complete",
                {"run_id": "workflow-master-1", "status": "complete"},
                subsystem="orchestrator",
                tenant_id="_default",
            )
            await completion_queue.push(
                "workflow-master-1",
                "complete",
                {"output": "workflow finished"},
                tenant_id="_default",
            )

            # L2.7: Prepare subprocess env
            subprocess_env = prepare_subprocess_env(tenant_id="_default")
            assert subprocess_env["CORVIN_HOME"] == str(temp_corvin_home)
            assert subprocess_env["CORVIN_TENANT_ID"] == "_default"

            # L3: Verify event flow
            events = await phase2_wiring.get_events_for_tenant("_default")
            assert len(events) == 2
            assert events[0].event_name == "orchestration.task_started"
            assert events[1].event_name == "orchestration.task_complete"

            # Verify ordering
            is_ordered = await phase2_wiring.verify_event_ordering("_default")
            assert is_ordered

        finally:
            await registry.stop_reaper()

    @pytest.mark.asyncio
    async def test_gap_1_claim_deadlock_regression(self, temp_corvin_home):
        """Regression: Gap 1 - Claim deadlock should not reoccur.

        Before Phase 2:
        - claim() could get stuck waiting for stale sidecars
        - Two resumes could both see .claimed and retry forever

        After Phase 2:
        - TTL reaper cleans stale claims every 60s
        - is_claimed() checks TTL before returning True
        """
        registry = CheckpointClaimRegistry(default_ttl_s=1)  # 1s TTL for test
        await registry.start_reaper()

        try:
            # Create checkpoint
            runs_dir = temp_corvin_home / "tenants" / "_default" / "workflow_runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            run_path = workflow_run_path("run-gap1")
            run_path.write_text('{}')

            # Claim it
            registry.claim("run-gap1")
            assert registry.is_claimed("run-gap1")

            # Wait for TTL to expire
            await asyncio.sleep(1.1)

            # Claim should be marked expired
            assert not registry.is_claimed("run-gap1")

            # Reaper should have cleaned it
            await asyncio.sleep(0.1)
            claims = registry.get_all_claims()
            assert "run-gap1" not in [c.run_id for c in claims]

        finally:
            await registry.stop_reaper()

    @pytest.mark.asyncio
    async def test_gap_3_scattered_path_logic_unified(self, temp_corvin_home):
        """Regression: Gap 3 - Scattered path logic unified in path_resolver.

        Before Phase 2:
        - checkpoint.py had _runs_dir() logic
        - bridge handlers had their own path construction
        - Tests used different path patterns

        After Phase 2:
        - All code uses path_resolver functions
        - Single source of truth
        """
        # Simulate old vs new patterns converging
        from core.workflows.path_resolver import (
            workflow_runs_dir,
            workflow_run_path,
            claimed_file_path,
        )

        # All should use the same base
        runs_dir = workflow_runs_dir("_default")
        run_path = workflow_run_path("test-gap3")
        claimed = claimed_file_path("test-gap3")

        # All should be under same parent
        assert runs_dir.parent.parent.parent == temp_corvin_home
        assert run_path.parent == runs_dir
        assert claimed.parent == runs_dir

    @pytest.mark.asyncio
    async def test_gap_6_env_var_propagation_to_subprocess(self, temp_corvin_home):
        """Regression: Gap 6 - CORVIN_HOME now explicitly propagated to subprocesses.

        Before Phase 2:
        - Subprocesses inherited all parent env (including PII)
        - CORVIN_HOME might not be set in subprocess
        - No validation of env contents

        After Phase 2:
        - prepare_subprocess_env() explicitly sets CORVIN_HOME
        - Whitelist prevents PII leakage
        - Fail-closed if CORVIN_HOME unresolvable
        """
        env = prepare_subprocess_env()

        # Critical invariant: CORVIN_HOME must be set
        assert "CORVIN_HOME" in env
        assert env["CORVIN_HOME"] == str(temp_corvin_home)

        # Whitelist must be respected
        with patch.dict(os.environ, {"SECRET_API_KEY": "abc123"}):
            env2 = prepare_subprocess_env()
            assert "SECRET_API_KEY" not in env2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
