"""Test goal persistence across session splits + integrity verification (Phase 1).

Tests:
- Goal persisted in checkpoint
- Goal restored when resuming from checkpoint
- Goal integrity verified on restore
- Backward compatibility (old checkpoints without goal)
- Multi-split scenarios (3+ splits, goal consistent)
"""

import pytest
from core.session_manager.checkpoint import SessionCheckpoint, TaskState
from core.session_manager.goal_context import GoalContext
from datetime import datetime


class TestGoalPersistenceInCheckpoint:
    """Test goal persistence in checkpoints."""

    def test_checkpoint_with_goal_context(self):
        """Test creating checkpoint with goal_context."""
        goal = "Implement distributed task queue"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        assert checkpoint.goal_context == goal_ctx
        assert checkpoint.goal_context.goal == goal

    def test_checkpoint_serialization_with_goal_context(self):
        """Test checkpoint serialization includes goal_context."""
        goal = "Build API gateway"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        data = checkpoint.to_dict()

        assert data["goal_context"] is not None
        assert data["goal_context"]["goal"] == goal
        assert data["goal_context"]["goal_hash"] == goal_ctx.goal_hash

    def test_checkpoint_deserialization_with_goal_context(self):
        """Test checkpoint deserialization restores goal_context."""
        goal = "Optimize database queries"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        data = checkpoint.to_dict()
        restored_checkpoint = SessionCheckpoint.from_dict(data)

        assert restored_checkpoint.goal_context is not None
        assert restored_checkpoint.goal_context.goal == goal
        assert restored_checkpoint.goal_context.goal_hash == goal_ctx.goal_hash

    def test_checkpoint_without_goal_context_backward_compat(self):
        """Test backward compatibility with old checkpoints (no goal_context)."""
        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=None,  # Old checkpoint
        )

        data = checkpoint.to_dict()
        assert data["goal_context"] is None

        # Should deserialize without error
        restored = SessionCheckpoint.from_dict(data)
        assert restored.goal_context is None


class TestGoalIntegrityVerificationOnRestore:
    """Test goal hash integrity on restoration."""

    def test_goal_integrity_verified_on_restore(self):
        """Test that goal hash is verified when restored from checkpoint."""
        goal = "Fix critical bug in production"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        # Serialize and deserialize
        data = checkpoint.to_dict()
        restored = SessionCheckpoint.from_dict(data)

        # Goal should be verifiable
        assert restored.goal_context is not None
        assert restored.goal_context.verify_integrity() is True

    def test_corrupted_goal_hash_fails_on_restore(self):
        """Test that corrupted goal hash fails on restoration (fail-closed)."""
        goal = "Implement new feature"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        data = checkpoint.to_dict()
        # Corrupt the goal in the serialized data
        data["goal_context"]["goal"] = "Different goal text"

        # Should raise AssertionError on deserialization
        with pytest.raises(AssertionError, match="Goal integrity check failed"):
            SessionCheckpoint.from_dict(data)

    def test_corrupted_goal_hash_value_fails(self):
        """Test that corrupted goal_hash value fails on restoration."""
        goal = "Implement new feature"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        data = checkpoint.to_dict()
        # Corrupt the hash value
        data["goal_context"]["goal_hash"] = "0" * 64

        # Should raise AssertionError on deserialization
        with pytest.raises(AssertionError, match="Goal integrity check failed"):
            SessionCheckpoint.from_dict(data)


class TestMultipleSplitsGoalPersistence:
    """Test goal persistence across multiple session splits."""

    def test_goal_persists_across_two_splits(self):
        """Test goal remains consistent across two splits."""
        goal = "Build scalable microservice"
        goal_ctx = GoalContext.create(goal)

        # First checkpoint
        cp1 = SessionCheckpoint(
            session_id="sess-1",
            task_id="task-1",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
            iterations_at_checkpoint=10,
        )

        # Simulate session split: serialize and restore
        data1 = cp1.to_dict()
        cp1_restored = SessionCheckpoint.from_dict(data1)

        # Second checkpoint (after some work)
        cp2 = SessionCheckpoint(
            session_id="sess-2",
            task_id="task-1",
            phase="phase2",
            tenant_id="tenant-default",
            goal_context=cp1_restored.goal_context,  # Carry forward
            iterations_at_checkpoint=20,
        )

        # Verify goal unchanged across splits
        assert cp1.goal_context.goal == cp2.goal_context.goal
        assert cp1.goal_context.goal_hash == cp2.goal_context.goal_hash

        # Serialize and restore second checkpoint
        data2 = cp2.to_dict()
        cp2_restored = SessionCheckpoint.from_dict(data2)

        # Goal should still match
        assert cp1.goal_context.goal == cp2_restored.goal_context.goal
        assert cp1.goal_context.goal_hash == cp2_restored.goal_context.goal_hash

    def test_goal_persists_across_three_splits(self):
        """Test goal remains consistent across three splits."""
        goal = "Optimize performance by 50%"
        goal_ctx_initial = GoalContext.create(goal)

        previous_goal_ctx = goal_ctx_initial

        for i in range(1, 4):  # Three splits
            cp = SessionCheckpoint(
                session_id=f"sess-{i}",
                task_id="task-x",
                phase=f"phase{i}",
                tenant_id="tenant-default",
                goal_context=previous_goal_ctx,
                iterations_at_checkpoint=i * 10,
            )

            # Verify goal integrity at each split
            assert cp.goal_context is not None
            assert cp.goal_context.verify_integrity() is True

            # Serialize and restore for next split
            data = cp.to_dict()
            cp_restored = SessionCheckpoint.from_dict(data)
            previous_goal_ctx = cp_restored.goal_context

        # Final verification: goal unchanged after 3 splits
        assert previous_goal_ctx.goal == goal


class TestGoalContextInAuditTrail:
    """Test goal context in checkpoint audit events."""

    def test_checkpoint_audit_event_includes_goal_context(self):
        """Test that checkpoint audit event includes goal_context summary."""
        goal = "Deploy to production"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        audit_event = checkpoint.to_audit_event()

        # Goal context summary should be in audit event (hash, not text)
        assert audit_event["state_summary"]["goal_context"] is not None
        assert audit_event["state_summary"]["goal_context"]["goal_hash"] == goal_ctx.goal_hash

    def test_audit_event_contains_no_goal_text(self):
        """Test that audit event contains goal_hash but never goal text."""
        goal = "Secret business logic implementation"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        audit_event = checkpoint.to_audit_event()
        event_str = str(audit_event)

        # Goal text should NOT be in audit event
        assert "Secret business logic" not in event_str
        # Only hash and metadata
        assert "goal_hash" in event_str


class TestGoalContextEdgeCases:
    """Test edge cases in goal persistence."""

    def test_very_long_goal_text(self):
        """Test goal with very long text."""
        goal = "A" * 10000  # 10k character goal
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        data = checkpoint.to_dict()
        restored = SessionCheckpoint.from_dict(data)

        assert restored.goal_context.goal == goal
        assert restored.goal_context.verify_integrity() is True

    def test_goal_with_special_characters(self):
        """Test goal with special characters and unicode."""
        goal = "Implement API for 日本語 text processing: special chars @#$%^&*()"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        data = checkpoint.to_dict()
        restored = SessionCheckpoint.from_dict(data)

        assert restored.goal_context.goal == goal
        assert restored.goal_context.verify_integrity() is True

    def test_goal_with_newlines(self):
        """Test goal with embedded newlines."""
        goal = "Task 1: Implement feature X\nTask 2: Write tests\nTask 3: Deploy"
        goal_ctx = GoalContext.create(goal)

        checkpoint = SessionCheckpoint(
            session_id="sess-123",
            task_id="task-456",
            phase="phase1",
            tenant_id="tenant-default",
            goal_context=goal_ctx,
        )

        data = checkpoint.to_dict()
        restored = SessionCheckpoint.from_dict(data)

        assert restored.goal_context.goal == goal
        assert restored.goal_context.verify_integrity() is True
