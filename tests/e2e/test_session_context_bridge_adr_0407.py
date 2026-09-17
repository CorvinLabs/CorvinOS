"""E2E Test: Session Context Bridge (ADR-0407 Phase 3).

Tests that goal is persisted on checkpoint and restored on resume.
Proves cross-session goal alignment validation works end-to-end.

This is Fix #5 for Phase 3.2-3.4 (Week 2 execution).
"""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

import pytest

# Import the classes we're testing
from core.context_engineering.session_checkpoint import (
    SessionCheckpoint,
    SessionContinuationManager,
)


@dataclass
class MockExecutionContext:
    """Mock ExecutionContext for testing checkpoint persistence."""
    task_id: str
    tenant_id: str
    task_template: dict = field(default_factory=dict)
    budget_remaining: float = 100.0
    time_remaining: int = 3600
    model: str = "claude-opus"
    strategy: str = "divide-and-conquer"
    strategy_confidence: float = 0.8
    guidance_overrides: dict = field(default_factory=dict)
    checkpoints: list = field(default_factory=list)

    # CRITICAL: Goal fields for ADR-0407
    original_goal: str = ""  # Set during init
    goal_alignment_score: float = 1.0
    decision_history: list = field(default_factory=list)

    # Mock goal alignment monitor
    goal_alignment_monitor: object = None


class TestSessionContextBridgeADR0407:
    """Test suite for ADR-0407 Phase 3: Goal restoration on resume."""

    @pytest.fixture
    def temp_corvin_home(self):
        """Create a temporary CORVIN_HOME for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def continuation_manager(self, temp_corvin_home):
        """Create SessionContinuationManager for testing."""
        return SessionContinuationManager(
            corvin_home=temp_corvin_home,
            tenant_id="_default"
        )

    def test_goal_persisted_in_checkpoint(self, continuation_manager):
        """Test that original_goal is saved to checkpoint (phase 1 save)."""
        # Create mock execution context with goal
        task_id = "test_task_123"
        goal = "Implement login feature with OAuth integration"

        exec_context = MockExecutionContext(
            task_id=task_id,
            tenant_id="_default",
            original_goal=goal,
            goal_alignment_score=0.95
        )

        # Save checkpoint
        checkpoint_id = continuation_manager.save_checkpoint(
            task_id=task_id,
            tenant_id="_default",
            execution_context=exec_context,
            session_id="session_abc123",
            turn_number=5
        )

        # Load checkpoint and verify goal is persisted
        checkpoint = continuation_manager.load_checkpoint(
            task_id=task_id,
            tenant_id="_default"
        )

        assert checkpoint is not None, "Checkpoint should be loaded"
        assert checkpoint.original_goal == goal, f"Goal mismatch: {checkpoint.original_goal} != {goal}"
        assert checkpoint.goal_alignment_score == 0.95, "Goal alignment score should be persisted"
        print(f"✅ Goal persisted: '{checkpoint.original_goal}'")

    def test_goal_restored_on_resume(self, continuation_manager):
        """Test that goal is restored when resuming from checkpoint (phase 3 restore)."""
        # Create execution context with goal
        task_id = "test_task_resume"
        goal = "Build payment processing system"

        exec_context = MockExecutionContext(
            task_id=task_id,
            tenant_id="_default",
            original_goal=goal,
            goal_alignment_score=0.87
        )

        # Save checkpoint
        continuation_manager.save_checkpoint(
            task_id=task_id,
            tenant_id="_default",
            execution_context=exec_context,
            session_id="session_xyz789",
            turn_number=10
        )

        # Load checkpoint
        checkpoint = continuation_manager.load_checkpoint(
            task_id=task_id,
            tenant_id="_default"
        )

        # Resume: reconstruct ExecutionContext from checkpoint
        resumed_ctx = continuation_manager.resume_from_checkpoint(
            checkpoint=checkpoint,
            execution_context_cls=MockExecutionContext
        )

        # Verify goal was restored
        assert resumed_ctx.original_goal == goal, f"Goal not restored: {resumed_ctx.original_goal}"
        assert resumed_ctx.goal_alignment_score == 0.87, "Goal alignment score not restored"
        print(f"✅ Goal restored on resume: '{resumed_ctx.original_goal}'")

    def test_cross_session_goal_alignment_invariant(self, continuation_manager):
        """Test that goal persists across session boundaries (full E2E cycle)."""
        # Session 1: Create checkpoint with goal
        task_id = "test_task_e2e"
        original_goal = "Implement multi-factor authentication"

        session1_ctx = MockExecutionContext(
            task_id=task_id,
            tenant_id="_default",
            original_goal=original_goal,
            goal_alignment_score=0.92,
            model="claude-opus"
        )

        continuation_manager.save_checkpoint(
            task_id=task_id,
            tenant_id="_default",
            execution_context=session1_ctx,
            session_id="session_1",
            turn_number=7
        )

        # Session 2: Resume and verify goal is unchanged
        checkpoint = continuation_manager.load_checkpoint(
            task_id=task_id,
            tenant_id="_default"
        )

        session2_ctx = continuation_manager.resume_from_checkpoint(
            checkpoint=checkpoint,
            execution_context_cls=MockExecutionContext
        )

        # Verify goal invariant: must match original even after session boundary
        assert session2_ctx.original_goal == original_goal, \
            f"Goal invariant violated: {session2_ctx.original_goal} != {original_goal}"
        assert session2_ctx.goal_alignment_score == 0.92, \
            "Alignment score invariant violated"

        print(f"✅ Cross-session goal invariant holds: '{session2_ctx.original_goal}'")

    def test_goal_none_handled_gracefully(self, continuation_manager):
        """Test that missing goal (None) doesn't break resume (backward compat)."""
        task_id = "test_task_no_goal"

        exec_context = MockExecutionContext(
            task_id=task_id,
            tenant_id="_default",
            original_goal="",  # Empty goal
            goal_alignment_score=1.0  # Default
        )

        continuation_manager.save_checkpoint(
            task_id=task_id,
            tenant_id="_default",
            execution_context=exec_context,
            session_id="session_no_goal",
            turn_number=3
        )

        checkpoint = continuation_manager.load_checkpoint(
            task_id=task_id,
            tenant_id="_default"
        )

        # Resume with no goal — should not crash
        resumed_ctx = continuation_manager.resume_from_checkpoint(
            checkpoint=checkpoint,
            execution_context_cls=MockExecutionContext
        )

        assert resumed_ctx.original_goal == "", "Empty goal should be preserved"
        print("✅ Backward compat: Empty goal handled gracefully")


class TestADR0407Phase3Compliance:
    """Test ADR-0407 compliance: Goal persistence + restoration."""

    def test_checkpoint_schema_includes_goal_fields(self):
        """Verify SessionCheckpoint schema includes goal fields."""
        cp = SessionCheckpoint(
            checkpoint_id="test_123",
            task_id="task_456",
            session_id="session_789",
            tenant_id="_default",
            context_state={},
            original_goal="Test goal",
            goal_alignment_score=0.85
        )

        # Verify fields exist and are serializable
        data = cp.to_dict()
        assert "original_goal" in data, "Schema missing original_goal field"
        assert "goal_alignment_score" in data, "Schema missing goal_alignment_score field"
        assert data["original_goal"] == "Test goal"
        assert data["goal_alignment_score"] == 0.85

        print("✅ SessionCheckpoint schema includes goal fields")

    def test_checkpoint_json_roundtrip(self):
        """Test that goal survives JSON serialization (persistence layer)."""
        original = SessionCheckpoint(
            checkpoint_id="test_123",
            task_id="task_456",
            session_id="session_789",
            tenant_id="_default",
            context_state={},
            original_goal="Roundtrip test goal",
            goal_alignment_score=0.75
        )

        # Serialize to JSON
        json_str = original.to_json()

        # Deserialize from JSON
        restored = SessionCheckpoint.from_json(json_str)

        # Verify goal fields survived roundtrip
        assert restored.original_goal == original.original_goal
        assert restored.goal_alignment_score == original.goal_alignment_score

        print("✅ Goal survives JSON serialization roundtrip")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
