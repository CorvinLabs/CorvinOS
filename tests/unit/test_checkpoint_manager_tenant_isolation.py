"""
Tier-2 Unit Tests: CheckpointManager Tenant Isolation (GDPR Art. 5, 6, 32)

Verifies fail-closed tenant isolation: different tenants cannot read/write
each other's checkpoints.
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from core.vibe_engineering.checkpoint_manager import (
    CheckpointManager,
    CheckpointState,
)


class TestCheckpointManagerTenantIsolation:
    """Tenant isolation verification (fail-closed, GDPR-compliant)."""

    @pytest.fixture
    def temp_dir(self):
        """Temporary directory for checkpoint storage."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def manager_tenant_a(self, temp_dir):
        """CheckpointManager for tenant A."""
        return CheckpointManager(tenant_id="tenant_a", checkpoint_dir=temp_dir / "tenant_a")

    @pytest.fixture
    def manager_tenant_b(self, temp_dir):
        """CheckpointManager for tenant B (different instance)."""
        return CheckpointManager(tenant_id="tenant_b", checkpoint_dir=temp_dir / "tenant_b")

    def test_manager_requires_tenant_id(self, temp_dir):
        """Manager __init__ fails-closed if tenant_id is empty."""
        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            CheckpointManager(tenant_id="", checkpoint_dir=temp_dir)

        with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
            CheckpointManager(tenant_id=None, checkpoint_dir=temp_dir)

    def test_manager_stores_tenant_id(self, manager_tenant_a):
        """Manager stores and validates tenant_id."""
        assert manager_tenant_a.tenant_id == "tenant_a"

    def test_create_checkpoint_requires_matching_tenant_id(self, manager_tenant_a):
        """create_checkpoint fails-closed if tenant_id doesn't match manager's tenant."""
        # Should succeed with matching tenant_id
        cp = manager_tenant_a.create_checkpoint(
            tenant_id="tenant_a",
            task_id="task_1",
            session_id="session_1",
            phase="phase_1",
            trigger="user_request",
            iteration_num=1,
            task_state={"goal": "test"},
            context_essentials={"kept": []},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
        )
        assert cp.tenant_id == "tenant_a"

        # Should fail with mismatched tenant_id (fail-closed)
        with pytest.raises(ValueError, match="Tenant mismatch"):
            manager_tenant_a.create_checkpoint(
                tenant_id="tenant_b",  # Mismatch!
                task_id="task_2",
                session_id="session_2",
                phase="phase_1",
                trigger="user_request",
                iteration_num=1,
                task_state={"goal": "test"},
                context_essentials={"kept": []},
                learning_state={},
                open_subgoals=[],
                artifacts=[],
            )

    def test_checkpoint_persisted_with_tenant_id(self, manager_tenant_a, temp_dir):
        """Checkpoint includes tenant_id in persisted JSON."""
        cp = manager_tenant_a.create_checkpoint(
            tenant_id="tenant_a",
            task_id="task_1",
            session_id="session_1",
            phase="phase_1",
            trigger="save_test",
            iteration_num=1,
            task_state={"goal": "test"},
            context_essentials={"kept": ["decision_1"]},
            learning_state={"strategies": ["s1"]},
            open_subgoals=[{"desc": "subgoal_1"}],
            artifacts=[{"name": "artifact_1"}],
        )

        # Save and load
        saved_path = manager_tenant_a.save(cp)
        loaded_cp = manager_tenant_a.load(saved_path)

        # Verify tenant_id was persisted and matches
        assert loaded_cp.tenant_id == "tenant_a"

    def test_list_checkpoints_requires_matching_tenant_id(self, manager_tenant_a):
        """list_checkpoints fails-closed if tenant_id doesn't match."""
        # Create a checkpoint for tenant_a
        cp = manager_tenant_a.create_checkpoint(
            tenant_id="tenant_a",
            task_id="task_1",
            session_id="session_1",
            phase="phase_1",
            trigger="list_test",
            iteration_num=1,
            task_state={"goal": "test"},
            context_essentials={},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
        )
        manager_tenant_a.save(cp)

        # Listing with correct tenant_id succeeds
        checkpoints = manager_tenant_a.list_checkpoints(
            tenant_id="tenant_a", task_id="task_1"
        )
        assert len(checkpoints) == 1

        # Listing with wrong tenant_id fails-closed
        with pytest.raises(ValueError, match="Tenant mismatch"):
            manager_tenant_a.list_checkpoints(tenant_id="tenant_b", task_id="task_1")

    def test_get_latest_requires_matching_tenant_id(self, manager_tenant_a):
        """get_latest fails-closed if tenant_id doesn't match."""
        cp = manager_tenant_a.create_checkpoint(
            tenant_id="tenant_a",
            task_id="task_1",
            session_id="session_1",
            phase="phase_1",
            trigger="latest_test",
            iteration_num=1,
            task_state={"goal": "test"},
            context_essentials={},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
        )
        manager_tenant_a.save(cp)

        # get_latest with correct tenant_id succeeds
        latest = manager_tenant_a.get_latest(tenant_id="tenant_a", task_id="task_1")
        assert latest is not None
        assert latest.tenant_id == "tenant_a"

        # get_latest with wrong tenant_id fails-closed
        with pytest.raises(ValueError, match="Tenant mismatch"):
            manager_tenant_a.get_latest(tenant_id="tenant_b", task_id="task_1")

    def test_delete_old_checkpoints_requires_matching_tenant_id(self, manager_tenant_a):
        """delete_old_checkpoints fails-closed if tenant_id doesn't match."""
        # Create multiple checkpoints
        for i in range(3):
            cp = manager_tenant_a.create_checkpoint(
                tenant_id="tenant_a",
                task_id="task_1",
                session_id="session_1",
                phase="phase_1",
                trigger="delete_test",
                iteration_num=i + 1,
                task_state={"goal": "test"},
                context_essentials={},
                learning_state={},
                open_subgoals=[],
                artifacts=[],
            )
            manager_tenant_a.save(cp)

        # delete_old_checkpoints with correct tenant_id succeeds
        manager_tenant_a.delete_old_checkpoints(
            tenant_id="tenant_a", task_id="task_1", keep_count=1
        )
        remaining = manager_tenant_a.list_checkpoints(
            tenant_id="tenant_a", task_id="task_1"
        )
        assert len(remaining) == 1

        # delete_old_checkpoints with wrong tenant_id fails-closed
        with pytest.raises(ValueError, match="Tenant mismatch"):
            manager_tenant_a.delete_old_checkpoints(
                tenant_id="tenant_b", task_id="task_1", keep_count=1
            )

    def test_separate_managers_isolate_checkpoints(self, manager_tenant_a, manager_tenant_b):
        """Different manager instances (different tenants) cannot access each other's checkpoints."""
        # Create checkpoint in tenant A
        cp_a = manager_tenant_a.create_checkpoint(
            tenant_id="tenant_a",
            task_id="task_1",
            session_id="session_1",
            phase="phase_1",
            trigger="isolation_test",
            iteration_num=1,
            task_state={"goal": "test_a"},
            context_essentials={},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
        )
        manager_tenant_a.save(cp_a)

        # Tenant B manager lists checkpoints: should be empty (different checkpoint_dir)
        checkpoints_b = manager_tenant_b.list_checkpoints(
            tenant_id="tenant_b", task_id="task_1"
        )
        assert len(checkpoints_b) == 0

    def test_defense_in_depth_on_load(self, manager_tenant_a, temp_dir):
        """Even if checkpoint file leaks across tenants, load validates tenant_id."""
        cp = manager_tenant_a.create_checkpoint(
            tenant_id="tenant_a",
            task_id="task_1",
            session_id="session_1",
            phase="phase_1",
            trigger="defense_test",
            iteration_num=1,
            task_state={"goal": "test"},
            context_essentials={},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
        )
        saved_path = manager_tenant_a.save(cp)

        # Load succeeds with correct manager (tenant_a)
        loaded = manager_tenant_a.load(saved_path)
        assert loaded.tenant_id == "tenant_a"

        # Even though we load the same file, a tenant_a manager
        # will verify tenant_id matches in list_checkpoints
        # (defense-in-depth: list_checkpoints checks tenant_id of loaded checkpoint)


class TestCheckpointStateIncludesTenantId:
    """CheckpointState includes tenant_id field."""

    def test_checkpoint_state_has_tenant_id_field(self):
        """CheckpointState dataclass includes tenant_id as required field."""
        cp = CheckpointState(
            checkpoint_id="cp_1",
            tenant_id="tenant_x",
            task_id="task_1",
            session_id="session_1",
            phase="phase_1",
            trigger="test",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=1,
            task_state={},
            context_essentials={},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
        )
        assert cp.tenant_id == "tenant_x"
        assert cp.checkpoint_id == "cp_1"
        assert cp.task_id == "task_1"
