#!/usr/bin/env python3
"""Quick verification script for Phase A implementation."""

import tempfile
import json
from pathlib import Path

from core.infinite_session import (
    Snapshot,
    SnapshotType,
    TaskDefParser,
    ExecutionPlan,
    EventStore,
    AutonomyLevel,
    GateType,
)


def test_snapshot_schema():
    """Test snapshot schema."""
    print("\n=== Testing Snapshot Schema ===")

    # Test valid snapshot creation
    state = {"key": "value", "count": 42}
    snapshot = Snapshot.create(
        tenant_id="_default",
        task_id="task_123",
        phase_id="phase_1",
        state_dict=state,
        snapshot_type=SnapshotType.PHASE_CHECKPOINT,
    )
    assert snapshot.tenant_id == "_default"
    assert snapshot.state_dict == state
    print("✓ Snapshot creation works")

    # Test hash verification
    assert snapshot.verify_hash(state) is True
    assert snapshot.verify_hash({"key": "different"}) is False
    print("✓ Hash verification works")

    # Test that empty tenant_id fails (fail-closed)
    try:
        Snapshot.create(
            tenant_id="",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={},
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "tenant_id" in str(e)
        print("✓ Empty tenant_id properly rejected (fail-closed)")

    # Test serialization
    data = snapshot.to_dict()
    assert data["snapshot_id"] == snapshot.snapshot_id
    assert data["snapshot_type"] == "phase_checkpoint"
    print("✓ Serialization works")


def test_task_def_parser():
    """Test task definition parser."""
    print("\n=== Testing Task Definition Parser ===")

    # Test simple task
    task_def = {
        "task_id": "task_001",
        "task_name": "Simple Task",
        "autonomy_level": "1",
        "timeout_seconds": 3600,
        "phases": [
            {
                "phase_id": "phase_1",
                "phase_name": "Phase 1",
                "description": "First phase",
                "skills": ["skill_a"],
            }
        ],
        "success_criteria": {"all_tests_pass": True},
    }

    plan, error = TaskDefParser.parse(task_def)
    assert error == "", f"Parse error: {error}"
    assert plan is not None
    assert plan.task_id == "task_001"
    assert plan.phase_order == ["phase_1"]
    print("✓ Simple task parsing works")

    # Test task with dependencies
    task_def_deps = {
        "task_id": "task_002",
        "task_name": "Dependent Task",
        "autonomy_level": "2",
        "timeout_seconds": 7200,
        "phases": [
            {
                "phase_id": "phase_1",
                "phase_name": "Phase 1",
                "skills": ["skill_a"],
            },
            {
                "phase_id": "phase_2",
                "phase_name": "Phase 2",
                "dependencies": ["phase_1"],
                "skills": ["skill_b"],
            },
            {
                "phase_id": "phase_3",
                "phase_name": "Phase 3",
                "dependencies": ["phase_1", "phase_2"],
                "skills": ["skill_c"],
            },
        ],
        "success_criteria": {},
    }

    plan, error = TaskDefParser.parse(task_def_deps)
    assert error == "", f"Parse error: {error}"
    assert plan.phase_order == ["phase_1", "phase_2", "phase_3"]
    print("✓ Topological sorting works")

    # Test cycle detection
    task_def_cycle = {
        "task_id": "task_003",
        "task_name": "Cyclic Task",
        "autonomy_level": "1",
        "timeout_seconds": 3600,
        "phases": [
            {
                "phase_id": "phase_1",
                "phase_name": "Phase 1",
                "dependencies": ["phase_2"],
            },
            {
                "phase_id": "phase_2",
                "phase_name": "Phase 2",
                "dependencies": ["phase_1"],
            },
        ],
        "success_criteria": {},
    }

    plan, error = TaskDefParser.parse(task_def_cycle)
    assert plan is None
    assert "Cycle detected" in error
    print("✓ Cycle detection works")

    # Test with gates
    task_def_gates = {
        "task_id": "task_005",
        "task_name": "Task with Gates",
        "autonomy_level": "3",
        "timeout_seconds": 3600,
        "phases": [
            {
                "phase_id": "phase_1",
                "phase_name": "Phase 1",
                "gates": [
                    {
                        "gate_id": "gate_1",
                        "gate_type": "test_pass_rate",
                        "params": {"min_rate": 0.95},
                        "required": True,
                    }
                ],
            }
        ],
        "success_criteria": {},
    }

    plan, error = TaskDefParser.parse(task_def_gates)
    assert error == "", f"Parse error: {error}"
    assert len(plan.phases[0].gates) == 1
    assert plan.phases[0].gates[0].gate_type == GateType.TEST_PASS_RATE
    print("✓ Gate parsing works")

    # Test serialization
    data = plan.to_dict()
    assert data["task_id"] == "task_005"
    assert len(data["phases"]) == 1
    print("✓ ExecutionPlan serialization works")


def test_event_store():
    """Test event store."""
    print("\n=== Testing Event Store ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        store = EventStore(tmpdir)

        # Test write and read
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"key": "value"},
        )

        success, error = store.write_snapshot(snapshot)
        assert success is True, f"Write error: {error}"
        print("✓ Snapshot write works")

        read_snapshot, error = store.read_snapshot(
            "_default",
            "task_123",
            "phase_1",
            snapshot.snapshot_id,
        )
        assert error == "", f"Read error: {error}"
        assert read_snapshot is not None
        assert read_snapshot.state_dict == {"key": "value"}
        print("✓ Snapshot read works")

        # Test list snapshots
        snapshot2 = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"key": "value2"},
            prev_snapshot_hash=snapshot.content_hash,
        )
        store.write_snapshot(snapshot2)

        metadata_list, error = store.list_snapshots("_default", "task_123", "phase_1")
        assert error == ""
        assert len(metadata_list) == 2
        print("✓ List snapshots works")

        # Test get latest
        latest, error = store.get_latest_snapshot("_default", "task_123", "phase_1")
        assert error == ""
        assert latest is not None
        print("✓ Get latest snapshot works")

        # Test chain verification
        is_valid, error = store.verify_snapshot_chain("_default", "task_123", "phase_1")
        assert is_valid is True
        print("✓ Chain verification works")

        # Test tenant isolation
        read_attempt, error = store.read_snapshot(
            "tenant_different",
            "task_123",
            "phase_1",
            snapshot.snapshot_id,
        )
        assert read_attempt is None
        assert error  # Should get error (file not found in different tenant)
        print("✓ Tenant isolation works (read blocked)")

        # Test audit callback
        audit_events = []
        def audit_callback(*args, **kwargs):
            audit_events.append(kwargs)
            return True

        snapshot3 = Snapshot.create(
            tenant_id="_default",
            task_id="task_124",
            phase_id="phase_1",
            state_dict={"audit_test": True},
        )
        success, error = store.write_snapshot(snapshot3, audit_callback=audit_callback)
        assert success is True
        assert len(audit_events) == 1
        assert audit_events[0]["event_type"] == "snapshot_created"
        print("✓ Audit callback works")


def test_e2e():
    """End-to-end test."""
    print("\n=== E2E Test ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Parse task definition
        task_def = {
            "task_id": "e2e_task",
            "task_name": "E2E Test Task",
            "autonomy_level": "2",
            "timeout_seconds": 7200,
            "phases": [
                {
                    "phase_id": "phase_1",
                    "phase_name": "Setup",
                    "skills": ["setup_skill"],
                },
                {
                    "phase_id": "phase_2",
                    "phase_name": "Execute",
                    "dependencies": ["phase_1"],
                    "skills": ["execute_skill"],
                    "gates": [
                        {
                            "gate_id": "gate_tests",
                            "gate_type": "test_pass_rate",
                            "params": {"min_rate": 0.95},
                            "required": True,
                        }
                    ],
                },
            ],
            "success_criteria": {"all_gates_pass": True},
        }

        plan, error = TaskDefParser.parse(task_def)
        assert error == ""
        assert plan is not None
        print("✓ Task definition parsed")

        # Create snapshots
        store = EventStore(tmpdir)

        phase1_state = {
            "setup_complete": True,
            "files_created": ["config.json", "data.csv"],
        }
        snapshot1 = Snapshot.create(
            tenant_id="_default",
            task_id=plan.task_id,
            phase_id="phase_1",
            state_dict=phase1_state,
            snapshot_type=SnapshotType.PHASE_CHECKPOINT,
            base_commit="abc123",
        )
        success, error = store.write_snapshot(snapshot1)
        assert success is True
        print("✓ Phase 1 snapshot created")

        # Phase 2 with chain link
        phase2_state = {
            "execution_complete": True,
            "tests_passed": 95,
            "tests_total": 100,
        }
        snapshot2 = Snapshot.create(
            tenant_id="_default",
            task_id=plan.task_id,
            phase_id="phase_2",
            state_dict=phase2_state,
            snapshot_type=SnapshotType.PHASE_CHECKPOINT,
            prev_snapshot_hash=snapshot1.content_hash,
            base_commit="def456",
        )
        success, error = store.write_snapshot(snapshot2)
        assert success is True
        print("✓ Phase 2 snapshot created with chain link")

        # Retrieve and verify
        retrieved1, error = store.read_snapshot(
            "_default",
            plan.task_id,
            "phase_1",
            snapshot1.snapshot_id,
        )
        assert error == ""
        assert retrieved1.state_dict == phase1_state
        print("✓ Phase 1 snapshot retrieved")

        retrieved2, error = store.read_snapshot(
            "_default",
            plan.task_id,
            "phase_2",
            snapshot2.snapshot_id,
        )
        assert error == ""
        assert retrieved2.prev_snapshot_hash == snapshot1.content_hash
        print("✓ Phase 2 snapshot retrieved with chain verification")

        # Verify entire chain
        is_valid, error = store.verify_snapshot_chain(
            "_default",
            plan.task_id,
            "phase_2",
        )
        assert is_valid is True
        print("✓ Complete chain verified")


if __name__ == "__main__":
    try:
        test_snapshot_schema()
        test_task_def_parser()
        test_event_store()
        test_e2e()
        print("\n" + "="*50)
        print("✓ ALL TESTS PASSED")
        print("="*50)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
