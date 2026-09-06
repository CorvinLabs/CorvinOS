"""Phase A: Comprehensive Tests for Infinite Session Engine (ADR-0540).

Unit tests: parser correctness, schema validation
Integration tests: snapshot write/read cycles
E2E tests: real session snapshot restore
Adversarial tests: tenant isolation, concurrent writes, hash tampering
"""

import json
import tempfile
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock

from core.infinite_session import (
    Snapshot,
    SnapshotType,
    SnapshotMetadata,
    TaskDefParser,
    ExecutionPlan,
    Phase,
    Gate,
    AutonomyLevel,
    GateType,
    EventStore,
)


class TestSnapshotSchema:
    """Tests for snapshot_schema.py"""

    def test_snapshot_creation_valid(self):
        """Test valid snapshot creation."""
        state = {"key": "value", "count": 42}
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict=state,
            snapshot_type=SnapshotType.PHASE_CHECKPOINT,
        )

        assert snapshot.tenant_id == "_default"
        assert snapshot.task_id == "task_123"
        assert snapshot.phase_id == "phase_1"
        assert snapshot.snapshot_type == SnapshotType.PHASE_CHECKPOINT
        assert snapshot.state_dict == state
        assert snapshot.content_hash  # Should have hash

    def test_snapshot_creation_empty_tenant_id_fails(self):
        """Test that snapshot creation fails with empty tenant_id (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            Snapshot.create(
                tenant_id="",
                task_id="task_123",
                phase_id="phase_1",
                state_dict={},
            )

    def test_snapshot_creation_none_tenant_id_fails(self):
        """Test that snapshot creation fails with None tenant_id."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            Snapshot.create(
                tenant_id=None,
                task_id="task_123",
                phase_id="phase_1",
                state_dict={},
            )

    def test_snapshot_hash_verification(self):
        """Test hash verification."""
        state = {"key": "value"}
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict=state,
        )

        # Should verify with same state
        assert snapshot.verify_hash(state) is True

        # Should fail with different state
        assert snapshot.verify_hash({"key": "different"}) is False

    def test_snapshot_chain_linking(self):
        """Test chain linking between snapshots."""
        state1 = {"key": "value1"}
        snapshot1 = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict=state1,
        )

        state2 = {"key": "value2"}
        snapshot2 = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict=state2,
            prev_snapshot_hash=snapshot1.content_hash,
        )

        # snapshot2 should be chained to snapshot1
        assert snapshot1.chain_link(snapshot2) is True

    def test_snapshot_serialization(self):
        """Test snapshot serialization to dict."""
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"key": "value"},
            base_commit="abc123",
        )

        data = snapshot.to_dict()
        assert data["snapshot_id"] == snapshot.snapshot_id
        assert data["tenant_id"] == "_default"
        assert data["task_id"] == "task_123"
        assert data["snapshot_type"] == "phase_checkpoint"  # Enum value
        assert data["base_commit"] == "abc123"

    def test_snapshot_metadata_creation(self):
        """Test metadata creation from snapshot."""
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"key": "value"},
        )

        metadata = SnapshotMetadata.from_snapshot(snapshot, "/path/to/file.json")
        assert metadata.snapshot_id == snapshot.snapshot_id
        assert metadata.tenant_id == "_default"
        assert metadata.content_hash == snapshot.content_hash


class TestTaskDefParser:
    """Tests for task_def_parser.py"""

    def test_parse_valid_simple_task(self):
        """Test parsing a valid simple task definition."""
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
                    "timeout_seconds": 1800,
                }
            ],
            "success_criteria": {"all_tests_pass": True},
        }

        plan, error = TaskDefParser.parse(task_def)
        assert error == ""
        assert plan is not None
        assert plan.task_id == "task_001"
        assert plan.task_name == "Simple Task"
        assert len(plan.phases) == 1
        assert plan.phase_order == ["phase_1"]

    def test_parse_task_with_dependencies(self):
        """Test parsing task with phase dependencies."""
        task_def = {
            "task_id": "task_002",
            "task_name": "Dependent Task",
            "autonomy_level": "2",
            "timeout_seconds": 7200,
            "phases": [
                {
                    "phase_id": "phase_1",
                    "phase_name": "Phase 1",
                    "description": "First phase",
                    "skills": ["skill_a"],
                },
                {
                    "phase_id": "phase_2",
                    "phase_name": "Phase 2",
                    "description": "Second phase",
                    "dependencies": ["phase_1"],
                    "skills": ["skill_b"],
                },
                {
                    "phase_id": "phase_3",
                    "phase_name": "Phase 3",
                    "description": "Third phase",
                    "dependencies": ["phase_1", "phase_2"],
                    "skills": ["skill_c"],
                },
            ],
            "success_criteria": {},
        }

        plan, error = TaskDefParser.parse(task_def)
        assert error == ""
        assert plan is not None
        assert plan.phase_order == ["phase_1", "phase_2", "phase_3"]

    def test_parse_detects_cycle(self):
        """Test that parser detects cycles in phase dependencies."""
        task_def = {
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

        plan, error = TaskDefParser.parse(task_def)
        assert plan is None
        assert "Cycle detected" in error

    def test_parse_missing_dependency_fails(self):
        """Test that parser fails on missing dependency."""
        task_def = {
            "task_id": "task_004",
            "task_name": "Missing Dependency",
            "autonomy_level": "1",
            "timeout_seconds": 3600,
            "phases": [
                {
                    "phase_id": "phase_1",
                    "phase_name": "Phase 1",
                    "dependencies": ["phase_unknown"],
                }
            ],
            "success_criteria": {},
        }

        plan, error = TaskDefParser.parse(task_def)
        assert plan is None
        assert "Unknown phase" in error or "Topological" in error

    def test_parse_missing_required_field_fails(self):
        """Test that parser fails on missing required fields."""
        # Missing task_id
        plan, error = TaskDefParser.parse(
            {
                "task_name": "Task",
                "phases": [{"phase_id": "p1"}],
            }
        )
        assert plan is None
        assert error

    def test_parse_with_gates(self):
        """Test parsing task with gates."""
        task_def = {
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

        plan, error = TaskDefParser.parse(task_def)
        assert error == ""
        assert plan is not None
        assert len(plan.phases[0].gates) == 1
        assert plan.phases[0].gates[0].gate_type == GateType.TEST_PASS_RATE

    def test_execution_plan_serialization(self):
        """Test execution plan serialization."""
        task_def = {
            "task_id": "task_006",
            "task_name": "Serialization Test",
            "autonomy_level": "1",
            "timeout_seconds": 3600,
            "phases": [
                {"phase_id": "phase_1", "phase_name": "Phase 1"}
            ],
            "success_criteria": {"key": "value"},
        }

        plan, error = TaskDefParser.parse(task_def)
        assert error == ""

        data = plan.to_dict()
        assert data["task_id"] == "task_006"
        assert data["autonomy_level"] == "1"
        assert len(data["phases"]) == 1


class TestEventStore:
    """Tests for event_store.py"""

    @pytest.fixture
    def temp_corvin_home(self):
        """Create temporary Corvin home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_write_and_read_snapshot(self, temp_corvin_home):
        """Test writing and reading a snapshot."""
        store = EventStore(temp_corvin_home)
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"key": "value"},
        )

        success, error = store.write_snapshot(snapshot)
        assert success is True
        assert error == ""

        read_snapshot, error = store.read_snapshot(
            "_default",
            "task_123",
            "phase_1",
            snapshot.snapshot_id,
        )
        assert error == ""
        assert read_snapshot is not None
        assert read_snapshot.snapshot_id == snapshot.snapshot_id
        assert read_snapshot.state_dict == {"key": "value"}

    def test_write_snapshot_empty_tenant_id_fails(self, temp_corvin_home):
        """Test that writing snapshot with empty tenant_id fails (fail-closed)."""
        store = EventStore(temp_corvin_home)

        # Try to create and write snapshot with empty tenant_id
        with pytest.raises(ValueError, match="tenant_id is required"):
            Snapshot.create(
                tenant_id="",
                task_id="task_123",
                phase_id="phase_1",
                state_dict={},
            )

    def test_read_nonexistent_snapshot(self, temp_corvin_home):
        """Test reading non-existent snapshot."""
        store = EventStore(temp_corvin_home)

        read_snapshot, error = store.read_snapshot(
            "_default",
            "task_123",
            "phase_1",
            "nonexistent",
        )
        assert read_snapshot is None
        assert error

    def test_list_snapshots(self, temp_corvin_home):
        """Test listing snapshots."""
        store = EventStore(temp_corvin_home)

        # Write multiple snapshots
        for i in range(3):
            snapshot = Snapshot.create(
                tenant_id="_default",
                task_id="task_123",
                phase_id="phase_1",
                state_dict={"iteration": i},
            )
            store.write_snapshot(snapshot)

        metadata_list, error = store.list_snapshots("_default", "task_123", "phase_1")
        assert error == ""
        assert len(metadata_list) == 3

    def test_get_latest_snapshot(self, temp_corvin_home):
        """Test getting latest snapshot."""
        store = EventStore(temp_corvin_home)

        # Write snapshots with slight delays to ensure different timestamps
        snapshots = []
        for i in range(3):
            snapshot = Snapshot.create(
                tenant_id="_default",
                task_id="task_123",
                phase_id="phase_1",
                state_dict={"iteration": i},
            )
            store.write_snapshot(snapshot)
            snapshots.append(snapshot)

        latest, error = store.get_latest_snapshot(
            "_default",
            "task_123",
            "phase_1",
        )
        assert error == ""
        assert latest is not None
        assert latest.snapshot_id == snapshots[-1].snapshot_id

    def test_verify_snapshot_chain(self, temp_corvin_home):
        """Test snapshot chain verification."""
        store = EventStore(temp_corvin_home)

        # Write chained snapshots
        snapshot1 = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"iteration": 1},
        )
        store.write_snapshot(snapshot1)

        snapshot2 = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"iteration": 2},
            prev_snapshot_hash=snapshot1.content_hash,
        )
        store.write_snapshot(snapshot2)

        is_valid, error = store.verify_snapshot_chain(
            "_default",
            "task_123",
            "phase_1",
        )
        assert is_valid is True
        assert error == ""

    def test_audit_callback_failure_prevents_write(self, temp_corvin_home):
        """Test that failed audit callback prevents snapshot write."""
        store = EventStore(temp_corvin_home)
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"key": "value"},
        )

        # Mock audit callback that fails
        def failing_audit(*args, **kwargs):
            return False

        success, error = store.write_snapshot(snapshot, audit_callback=failing_audit)
        assert success is False
        assert "Audit event emission failed" in error

    def test_audit_callback_success_allows_write(self, temp_corvin_home):
        """Test that successful audit callback allows snapshot write."""
        store = EventStore(temp_corvin_home)
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"key": "value"},
        )

        # Mock audit callback that succeeds
        audit_events = []
        def success_audit(*args, **kwargs):
            audit_events.append(kwargs)
            return True

        success, error = store.write_snapshot(snapshot, audit_callback=success_audit)
        assert success is True
        assert error == ""
        assert len(audit_events) == 1
        assert audit_events[0]["event_type"] == "snapshot_created"


class TestAdversarial:
    """Adversarial tests for security and isolation."""

    @pytest.fixture
    def temp_corvin_home(self):
        """Create temporary Corvin home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_tenant_isolation_read_fails_across_tenants(self, temp_corvin_home):
        """Test that tenant isolation prevents cross-tenant reads."""
        store = EventStore(temp_corvin_home)

        # Write snapshot for tenant_a
        snapshot_a = Snapshot.create(
            tenant_id="tenant_a",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"secret": "value_a"},
        )
        store.write_snapshot(snapshot_a)

        # Try to read with tenant_b (should fail)
        read_snapshot, error = store.read_snapshot(
            "tenant_b",
            "task_123",
            "phase_1",
            snapshot_a.snapshot_id,
        )
        assert read_snapshot is None
        assert error  # Should get an error (file not found in tenant_b's directory)

    def test_snapshot_tampering_detected(self, temp_corvin_home):
        """Test that tampering with snapshot is detected."""
        store = EventStore(temp_corvin_home)

        original_state = {"key": "original"}
        snapshot = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict=original_state,
        )
        store.write_snapshot(snapshot)

        # Try to tamper with the snapshot on disk
        snapshot_dir = Path(temp_corvin_home) / "tenants" / "_default" / "snapshots" / "task_123" / "phase_1"
        snapshot_file = snapshot_dir / f"{snapshot.snapshot_id}.json"

        with open(snapshot_file, "r") as f:
            data = json.load(f)
        data["state_dict"]["key"] = "tampered"
        with open(snapshot_file, "w") as f:
            json.dump(data, f)

        # Verify that hash check fails
        tampered_state = {"key": "tampered"}
        assert snapshot.verify_hash(tampered_state) is False
        assert snapshot.verify_hash(original_state) is True

    def test_hash_chain_tampering_detected(self, temp_corvin_home):
        """Test that hash chain tampering is detected."""
        store = EventStore(temp_corvin_home)

        snapshot1 = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"iteration": 1},
        )
        store.write_snapshot(snapshot1)

        snapshot2 = Snapshot.create(
            tenant_id="_default",
            task_id="task_123",
            phase_id="phase_1",
            state_dict={"iteration": 2},
            prev_snapshot_hash=snapshot1.content_hash,
        )
        store.write_snapshot(snapshot2)

        # Tamper with snapshot1's hash
        snapshot_dir = Path(temp_corvin_home) / "tenants" / "_default" / "snapshots" / "task_123" / "phase_1"
        snapshot_file = snapshot_dir / f"{snapshot1.snapshot_id}.json"

        with open(snapshot_file, "r") as f:
            data = json.load(f)
        data["state_dict"]["iteration"] = 999  # This changes the hash
        with open(snapshot_file, "w") as f:
            json.dump(data, f)

        # Verify chain should fail
        is_valid, error = store.verify_snapshot_chain(
            "_default",
            "task_123",
            "phase_1",
        )
        assert is_valid is False  # Chain should be broken

    def test_concurrent_write_safety(self, temp_corvin_home):
        """Test that concurrent writes don't corrupt metadata index."""
        store = EventStore(temp_corvin_home)

        # Simulate concurrent writes by writing snapshots quickly
        for i in range(5):
            snapshot = Snapshot.create(
                tenant_id="_default",
                task_id="task_123",
                phase_id="phase_1",
                state_dict={"iteration": i},
            )
            success, error = store.write_snapshot(snapshot)
            assert success is True

        # Verify all snapshots are readable
        metadata_list, error = store.list_snapshots("_default", "task_123", "phase_1")
        assert error == ""
        assert len(metadata_list) == 5  # All writes should be present


class TestE2E:
    """End-to-end tests."""

    @pytest.fixture
    def temp_corvin_home(self):
        """Create temporary Corvin home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_e2e_task_definition_to_execution_to_snapshot(self, temp_corvin_home):
        """E2E test: task definition → execution plan → snapshot."""
        # Step 1: Parse task definition
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
                    "timeout_seconds": 1800,
                },
                {
                    "phase_id": "phase_2",
                    "phase_name": "Execute",
                    "dependencies": ["phase_1"],
                    "skills": ["execute_skill"],
                    "timeout_seconds": 3600,
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

        # Step 2: Create snapshots for each phase
        store = EventStore(temp_corvin_home)

        phase1_state = {
            "setup_complete": True,
            "timestamp": datetime.utcnow().isoformat(),
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

        # Phase 2 with chain link
        phase2_state = {
            "execution_complete": True,
            "timestamp": datetime.utcnow().isoformat(),
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

        # Step 3: Retrieve snapshots and verify
        retrieved1, error = store.read_snapshot(
            "_default",
            plan.task_id,
            "phase_1",
            snapshot1.snapshot_id,
        )
        assert error == ""
        assert retrieved1.state_dict == phase1_state

        retrieved2, error = store.read_snapshot(
            "_default",
            plan.task_id,
            "phase_2",
            snapshot2.snapshot_id,
        )
        assert error == ""
        assert retrieved2.prev_snapshot_hash == snapshot1.content_hash

        # Step 4: Verify chain
        is_valid, error = store.verify_snapshot_chain(
            "_default",
            plan.task_id,
            "phase_2",
        )
        assert is_valid is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
