"""Tests for CheckpointManager (k=2).

10 unit + integration tests covering:
- Checkpoint creation
- JSON serialization/deserialization
- Checkpoint persistence
- Checkpoint history tracking
- Audit logging
"""

import json
import pytest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.session_manager.checkpoint import (
    CheckpointManager,
    SessionCheckpoint,
    TaskState,
    SubgoalRecord,
    ArtifactRecord,
    LearningState,
    ContextEssentials,
)


class TestSessionCheckpoint:
    """Test SessionCheckpoint dataclass."""

    def test_checkpoint_creation(self):
        """Test basic checkpoint creation."""
        task_state = TaskState(
            task_id="audit-001",
            goal="Audit system for compliance",
            constraints=["time limit 16 hours", "no manual intervention"],
        )

        checkpoint = SessionCheckpoint(
            session_id="sess-001",
            task_id="audit-001",
            phase="execution",
            tenant_id="default",
            task_state=task_state,
        )

        assert checkpoint.session_id == "sess-001"
        assert checkpoint.task_state.goal == "Audit system for compliance"

    def test_checkpoint_to_dict_serialization(self):
        """Test checkpoint serialization to dict."""
        task_state = TaskState(task_id="t1", goal="Test goal")
        checkpoint = SessionCheckpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
            task_state=task_state,
            iterations_at_checkpoint=25,
            token_count_at_checkpoint=5000,
        )

        checkpoint_dict = checkpoint.to_dict()

        assert checkpoint_dict["session_id"] == "s1"
        assert checkpoint_dict["task_id"] == "t1"
        assert checkpoint_dict["iterations_at_checkpoint"] == 25
        assert checkpoint_dict["token_count_at_checkpoint"] == 5000

    def test_checkpoint_from_dict_deserialization(self):
        """Test checkpoint deserialization from dict."""
        checkpoint_dict = {
            "checkpoint_id": "cp-001",
            "session_id": "s1",
            "task_id": "t1",
            "phase": "execution",
            "tenant_id": "default",
            "created_at": "2026-08-25T12:00:00Z",
            "trigger_type": "context_limit",
            "iterations_at_checkpoint": 45,
            "token_count_at_checkpoint": 180000,
            "task_state": {
                "task_id": "t1",
                "goal": "Audit compliance",
                "constraints": ["16 hours"],
            },
            "open_subgoals": [],
            "artifacts": [],
            "learning_state": None,
            "context_essentials": None,
        }

        checkpoint = SessionCheckpoint.from_dict(checkpoint_dict)

        assert checkpoint.session_id == "s1"
        assert checkpoint.task_id == "t1"
        assert checkpoint.iterations_at_checkpoint == 45

    def test_checkpoint_requires_session_id(self):
        """Test that checkpoint requires session_id."""
        with pytest.raises(ValueError, match="session_id"):
            SessionCheckpoint(
                session_id="",
                task_id="t1",
                phase="execution",
                tenant_id="default",
            )

    def test_checkpoint_audit_event(self):
        """Test checkpoint audit event generation."""
        checkpoint = SessionCheckpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
            trigger_type="iteration_cap",
            iterations_at_checkpoint=50,
        )

        audit_event = checkpoint.to_audit_event()

        assert audit_event["event_type"] == "session.checkpoint_created"
        assert audit_event["session_id"] == "s1"
        assert audit_event["trigger_type"] == "iteration_cap"
        assert audit_event["state_summary"]["iterations"] == 50


class TestCheckpointManager:
    """Test CheckpointManager."""

    def setup_method(self):
        """Setup test fixtures."""
        self.tmpdir = TemporaryDirectory()
        self.manager = CheckpointManager(
            checkpoint_dir=Path(self.tmpdir.name)
        )

    def teardown_method(self):
        """Cleanup."""
        self.tmpdir.cleanup()

    def test_create_checkpoint_basic(self):
        """Test basic checkpoint creation."""
        checkpoint = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
            iterations=25,
            token_count=5000,
        )

        assert checkpoint.session_id == "s1"
        assert checkpoint.iterations_at_checkpoint == 25

    def test_create_checkpoint_with_full_state(self):
        """Test checkpoint with full state."""
        task_state = TaskState(task_id="t1", goal="Audit compliance")
        subgoal = SubgoalRecord(
            description="Check configuration files",
            status="completed",
            work_done="Reviewed 50 config files",
        )
        artifact = ArtifactRecord(
            name="compliance_report.json",
            path="/artifacts/report.json",
            essential=True,
            reason="Contains audit findings",
        )
        learning = LearningState(
            strategies_tried=["config_review", "log_analysis"],
            success_rate=0.8,
        )

        checkpoint = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
            task_state=task_state,
            open_subgoals=[subgoal],
            artifacts=[artifact],
            learning_state=learning,
        )

        assert len(checkpoint.open_subgoals) == 1
        assert len(checkpoint.artifacts) == 1
        assert checkpoint.learning_state.success_rate == 0.8

    def test_get_checkpoint(self):
        """Test retrieving a checkpoint."""
        checkpoint1 = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
        )

        retrieved = self.manager.get_checkpoint(checkpoint1.checkpoint_id)

        assert retrieved is not None
        assert retrieved.checkpoint_id == checkpoint1.checkpoint_id

    def test_get_latest_checkpoint(self):
        """Test getting latest checkpoint for task."""
        # Create multiple checkpoints
        cp1 = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="planning",
            tenant_id="default",
        )

        cp2 = self.manager.create_checkpoint(
            session_id="s2",
            task_id="t1",
            phase="execution",
            tenant_id="default",
        )

        latest = self.manager.get_latest_checkpoint("t1")

        assert latest is not None
        assert latest.checkpoint_id == cp2.checkpoint_id

    def test_list_checkpoints_for_task(self):
        """Test listing all checkpoints for a task."""
        cp1 = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="planning",
            tenant_id="default",
        )

        cp2 = self.manager.create_checkpoint(
            session_id="s2",
            task_id="t1",
            phase="execution",
            tenant_id="default",
        )

        checkpoints = self.manager.list_checkpoints_for_task("t1")

        assert len(checkpoints) == 2
        assert checkpoints[0].checkpoint_id == cp1.checkpoint_id
        assert checkpoints[1].checkpoint_id == cp2.checkpoint_id

    def test_persist_and_restore_checkpoint(self):
        """Test checkpoint persistence and restoration."""
        cp_orig = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
            iterations=42,
            token_count=100000,
        )

        # Verify file exists
        cp_file = Path(self.tmpdir.name) / f"{cp_orig.checkpoint_id}.json"
        assert cp_file.exists()

        # Restore checkpoint
        cp_restored = self.manager.restore_checkpoint(cp_orig.checkpoint_id)

        assert cp_restored is not None
        assert cp_restored.iterations_at_checkpoint == 42
        assert cp_restored.token_count_at_checkpoint == 100000

    def test_checkpoint_json_format(self):
        """Test that checkpoint JSON is well-formed."""
        checkpoint = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
        )

        cp_file = Path(self.tmpdir.name) / f"{checkpoint.checkpoint_id}.json"
        with open(cp_file) as f:
            data = json.load(f)

        # Verify key fields
        assert data["session_id"] == "s1"
        assert data["task_id"] == "t1"
        assert "created_at" in data

    def test_checkpoint_history_tracking(self):
        """Test checkpoint history is tracked per task."""
        cp1 = self.manager.create_checkpoint(
            session_id="s1",
            task_id="t1",
            phase="planning",
            tenant_id="default",
        )

        cp2 = self.manager.create_checkpoint(
            session_id="s2",
            task_id="t1",
            phase="execution",
            tenant_id="default",
        )

        cp3 = self.manager.create_checkpoint(
            session_id="s3",
            task_id="t2",
            phase="planning",
            tenant_id="default",
        )

        history_t1 = self.manager.checkpoint_history["t1"]
        history_t2 = self.manager.checkpoint_history["t2"]

        assert len(history_t1) == 2
        assert len(history_t2) == 1
        assert history_t1[0] == cp1.checkpoint_id
        assert history_t1[1] == cp2.checkpoint_id

    def test_checkpoint_manager_startup_shutdown(self):
        """Test manager lifecycle."""
        manager = CheckpointManager(checkpoint_dir=Path(self.tmpdir.name))

        class MockHub:
            pass

        hub = MockHub()
        manager.startup(hub)
        assert manager.hub == hub

        manager.shutdown()


class TestCheckpointSerialization:
    """Test checkpoint serialization edge cases."""

    def test_datetime_serialization(self):
        """Test datetime serialization in checkpoint."""
        now = datetime.utcnow()
        checkpoint = SessionCheckpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
            created_at=now,
        )

        data = checkpoint.to_dict()
        assert "Z" in data["created_at"]

        restored = SessionCheckpoint.from_dict(data)
        # Allow 1 second tolerance for rounding
        assert abs((restored.created_at - now).total_seconds()) < 1

    def test_complex_nested_structures(self):
        """Test serialization of complex nested structures."""
        subgoals = [
            SubgoalRecord(
                description="Subgoal 1",
                status="completed",
                work_done="Work done 1",
            ),
            SubgoalRecord(
                description="Subgoal 2",
                status="in_progress",
                work_done="Work done 2",
            ),
        ]

        checkpoint = SessionCheckpoint(
            session_id="s1",
            task_id="t1",
            phase="execution",
            tenant_id="default",
            open_subgoals=subgoals,
        )

        data = checkpoint.to_dict()
        restored = SessionCheckpoint.from_dict(data)

        assert len(restored.open_subgoals) == 2
        assert restored.open_subgoals[0].description == "Subgoal 1"
        assert restored.open_subgoals[1].status == "in_progress"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
