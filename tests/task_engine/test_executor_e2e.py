"""End-to-end executor test (3-phase DAG, state continuity, audit chain)."""

import pytest
from core.task_engine.executor import TaskExecutor
from core.task_engine.task_def import TaskDefinition
from .fixtures import mock_3phase_dag, mock_skill_1, mock_skill_2, mock_skill_3


class TestTaskExecutorE2E:
    """E2E tests for TaskExecutor (ADR-0540–0545)."""

    def test_3phase_dag_complete_success(self):
        """Test: 3-phase task DAG runs end-to-end without errors."""
        # Setup
        task_def = mock_3phase_dag()
        executor = TaskExecutor(tenant_id="_default")

        # Register mock skills
        executor.register_skill("mock-skill-1", mock_skill_1)
        executor.register_skill("mock-skill-2", mock_skill_2)
        executor.register_skill("mock-skill-3", mock_skill_3)

        # Execute
        result = executor.run(task_def)

        # Assert
        assert result.success, f"Task should succeed, but got error: {result.error}"
        assert result.task_id == "test-3phase-dag"
        assert result.final_phase == "phase-3-test"
        assert len(result.audit_events) > 0, "Should have audit events"

    def test_state_continuity_across_phases(self):
        """Test: State carries over from phase 1 → phase 2 → phase 3."""
        # Setup
        task_def = mock_3phase_dag()
        executor = TaskExecutor(tenant_id="_default")

        # Register skills that each add to state
        def skill_1(input_data):
            return {"phase": "1", "counter": (input_data.get("counter", 0) + 1)}

        def skill_2(input_data):
            return {"phase": "2", "counter": (input_data.get("counter", 0) + 1)}

        def skill_3(input_data):
            return {"phase": "3", "counter": (input_data.get("counter", 0) + 1)}

        executor.register_skill("mock-skill-1", skill_1)
        executor.register_skill("mock-skill-2", skill_2)
        executor.register_skill("mock-skill-3", skill_3)

        # Execute
        result = executor.run(task_def)

        # Assert: all phases should have run (counter incremented 3 times)
        assert result.success
        # Note: final snapshot contains final state
        assert result.snapshot is not None
        assert result.snapshot.state.get("counter") == 3

    def test_audit_trail_unbroken_chain(self):
        """Test: Audit-trail is unbroken across all phases (zero gaps)."""
        # Setup
        task_def = mock_3phase_dag()
        executor = TaskExecutor(tenant_id="_default")
        executor.register_skill("mock-skill-1", mock_skill_1)
        executor.register_skill("mock-skill-2", mock_skill_2)
        executor.register_skill("mock-skill-3", mock_skill_3)

        # Execute
        result = executor.run(task_def)

        # Assert: chain is valid
        assert result.success
        chain_valid = executor.event_store.verify_chain(task_def.task_id)
        assert chain_valid, "Audit chain should be unbroken"

        # Verify all events are chain-linked
        events = result.audit_events
        for i in range(1, len(events)):
            assert events[i].prev_hash == events[i - 1].hash, \
                f"Event {i} should be linked to event {i-1}"

    def test_session_bridging_events(self):
        """Test: Session boundary events (task_session_bridged) are present."""
        # Setup
        task_def = mock_3phase_dag()
        executor = TaskExecutor(tenant_id="_default")
        executor.register_skill("mock-skill-1", mock_skill_1)
        executor.register_skill("mock-skill-2", mock_skill_2)
        executor.register_skill("mock-skill-3", mock_skill_3)

        # Execute
        result = executor.run(task_def)

        # Assert: session bridge events should exist
        assert result.success
        bridge_events = [e for e in result.audit_events if e.event_type == "task_session_bridged"]
        assert len(bridge_events) == 2, "Should have 2 bridge events (between 3 phases)"

        # Verify bridge event structure
        for bridge in bridge_events:
            assert "source_session" in bridge.payload
            assert "dest_session" in bridge.payload
            assert "state_hash" in bridge.payload
            assert bridge.payload.get("state_hash_verified") == True

    def test_snapshot_creation(self):
        """Test: Snapshots are created at each phase boundary."""
        # Setup
        task_def = mock_3phase_dag()
        executor = TaskExecutor(tenant_id="_default")
        executor.register_skill("mock-skill-1", mock_skill_1)
        executor.register_skill("mock-skill-2", mock_skill_2)
        executor.register_skill("mock-skill-3", mock_skill_3)

        # Execute
        result = executor.run(task_def)

        # Assert: snapshots exist
        assert result.success
        snapshot_events = [e for e in result.audit_events if e.event_type == "task_snapshot_created"]
        assert len(snapshot_events) == 2, "Should have 2 snapshots (between 3 phases + final)"

    def test_tenant_isolation(self):
        """Test: Task is tenant-scoped (ADR-0007, Fix 2.x)."""
        # Setup
        task_def = mock_3phase_dag()
        executor_a = TaskExecutor(tenant_id="tenant_a")
        executor_b = TaskExecutor(tenant_id="tenant_b")

        # Register skills
        for executor in [executor_a, executor_b]:
            executor.register_skill("mock-skill-1", mock_skill_1)
            executor.register_skill("mock-skill-2", mock_skill_2)
            executor.register_skill("mock-skill-3", mock_skill_3)

        # Execute task in tenant_a (should fail because task tenant is _default)
        with pytest.raises(ValueError):
            executor_a.run(task_def)

        # Execute task in correct tenant
        executor_default = TaskExecutor(tenant_id="_default")
        executor_default.register_skill("mock-skill-1", mock_skill_1)
        executor_default.register_skill("mock-skill-2", mock_skill_2)
        executor_default.register_skill("mock-skill-3", mock_skill_3)
        result = executor_default.run(task_def)
        assert result.success

        # Verify all events are tenant-scoped
        for event in result.audit_events:
            assert event.tenant_id == "_default"

    def test_rollback_on_phase_failure(self):
        """Test: Task rolls back if phase fails (ADR-0542, Fix 4.x)."""
        # Setup: phase 2 will fail
        task_def = mock_3phase_dag()
        executor = TaskExecutor(tenant_id="_default")

        executor.register_skill("mock-skill-1", mock_skill_1)
        executor.register_skill("mock-skill-2", lambda x: {"error": "Simulated failure"})  # Fail
        executor.register_skill("mock-skill-3", mock_skill_3)

        # Execute (should fail)
        result = executor.run(task_def)

        # Assert
        assert not result.success
        assert result.final_phase == "phase-2-refactor"
        assert "error" in result.error

        # Verify rollback event was emitted
        rollback_events = [e for e in result.audit_events if e.event_type == "task_rolled_back"]
        assert len(rollback_events) == 1, "Should have rollback event"

    def test_audit_events_completeness(self):
        """Test: All expected audit events are present."""
        # Setup
        task_def = mock_3phase_dag()
        executor = TaskExecutor(tenant_id="_default")
        executor.register_skill("mock-skill-1", mock_skill_1)
        executor.register_skill("mock-skill-2", mock_skill_2)
        executor.register_skill("mock-skill-3", mock_skill_3)

        # Execute
        result = executor.run(task_def)

        # Collect event types
        event_types = set(e.event_type for e in result.audit_events)

        # Assert: expected events present
        expected = {
            "task_started",
            "phase_started",
            "phase_skills_executed",
            "phase_gate_evaluated",
            "phase_complete",
            "task_snapshot_created",
            "task_session_bridged",
            "task_complete",
            "audit_chain_verified",
        }
        for expected_type in expected:
            assert expected_type in event_types, f"Missing event type: {expected_type}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
