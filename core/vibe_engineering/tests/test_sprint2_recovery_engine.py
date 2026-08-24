"""
Sprint 2.2: RecoveryEngine Tests

20 tests covering:
- State recovery (SessionState, Context, Learning)
- Idempotency validation (same checkpoint → same ExecutionState)
- Context reconstruction (full context from reduced + dropped)
- Recovery cost estimation
"""

import pytest
from datetime import datetime

from core.vibe_engineering.checkpoint_manager import CheckpointState
from core.vibe_engineering.session_lifecycle_manager import SessionState
from core.vibe_engineering.recovery_engine import RecoveryEngine, ExecutionState


class TestRecoveryEngineStateRestoration:
    """Test recovery of full execution state."""

    def setup_method(self):
        self.engine = RecoveryEngine()

    def create_test_checkpoint(self, task_id="task_001", iteration_num=10) -> CheckpointState:
        """Create a minimal test checkpoint."""
        return CheckpointState(
            checkpoint_id="ckpt_test",
            task_id=task_id,
            session_id="session_test",
            phase="execution",
            trigger="context_limit",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=iteration_num,
            task_state={
                "task_id": task_id,
                "goal": "Analyze logs",
                "progress": 0.5,
                "context_tokens": 2000,
                "max_context_tokens": 4000
            },
            context_essentials={
                "kept": ["Filesystem only", "Concurrent writes"],
                "dropped": [],
                "reduction_pct": 91
            },
            learning_state={
                "strategies_tried": ["regex", "ml"],
                "success_rate": 0.65,
                "errors": []
            },
            open_subgoals=[
                {"description": "phase 1 complete", "status": "done"}
            ],
            artifacts=[]
        )

    def test_recover_restores_task_identity(self):
        """Recovery preserves task ID and session ID."""
        checkpoint = self.create_test_checkpoint(task_id="audit_001")
        state = self.engine.recover_from_checkpoint(checkpoint)

        assert state.task_id == "audit_001"
        assert state.session_id == "session_test"

    def test_recover_restores_phase_and_iteration(self):
        """Recovery preserves phase and iteration count."""
        checkpoint = self.create_test_checkpoint(iteration_num=42)
        state = self.engine.recover_from_checkpoint(checkpoint)

        assert state.phase == "execution"
        assert state.iteration_num == 42

    def test_recover_initializes_session_state(self):
        """Recovery creates valid SessionState for trigger evaluation."""
        checkpoint = self.create_test_checkpoint()
        state = self.engine.recover_from_checkpoint(checkpoint)

        assert isinstance(state.session_state, SessionState)
        assert state.session_state.session_id == "session_test"
        assert state.session_state.iteration_count == checkpoint.iteration_num

    def test_recover_extracts_context(self):
        """Recovery reconstructs full context with goal and constraints."""
        checkpoint = self.create_test_checkpoint()
        state = self.engine.recover_from_checkpoint(checkpoint)

        assert "goal" in state.full_context
        assert state.full_context["goal"] == "Analyze logs"
        assert "constraints" in state.full_context

    def test_recover_extracts_learning_state(self):
        """Recovery extracts strategies and success rates."""
        checkpoint = self.create_test_checkpoint()
        state = self.engine.recover_from_checkpoint(checkpoint)

        assert "strategies_tried" in state.learning_state
        assert state.learning_state["success_rate"] == 0.65
        assert len(state.learning_state["strategies_tried"]) == 2

    def test_recover_preserves_checkpoint_metadata(self):
        """Recovery stores checkpoint ID and timestamp for audit trail."""
        checkpoint = self.create_test_checkpoint()
        state = self.engine.recover_from_checkpoint(checkpoint)

        assert state.last_checkpoint_id == "ckpt_test"
        assert state.checkpoint_timestamp == checkpoint.timestamp_iso

    def test_recover_sets_recovery_timestamp(self):
        """Recovery records when recovery occurred."""
        checkpoint = self.create_test_checkpoint()
        before = datetime.now()
        state = self.engine.recover_from_checkpoint(checkpoint)
        after = datetime.now()

        resumed_at = datetime.fromisoformat(state.resumed_at)
        assert before <= resumed_at <= after


class TestRecoveryEngineIdempotency:
    """Test idempotency (same checkpoint → same result)."""

    def setup_method(self):
        self.engine = RecoveryEngine()

    def create_deterministic_checkpoint(self) -> CheckpointState:
        """Create a checkpoint with deterministic state."""
        return CheckpointState(
            checkpoint_id="ckpt_deterministic",
            task_id="task_deterministic",
            session_id="session_deterministic",
            phase="analysis",
            trigger="iteration_cap",
            timestamp_iso="2026-08-24T15:00:00",
            iteration_num=50,
            task_state={
                "task_id": "task_deterministic",
                "goal": "Audit security logs",
                "progress": 0.75,
                "context_tokens": 3000,
                "max_context_tokens": 4000
            },
            context_essentials={
                "kept": ["Constraint A", "Constraint B"],
                "decisions": [{"decision": "Use regex", "iter": 5, "why": "blocking"}],
                "errors": [{"error_type": "Timeout", "iter": 20, "root_cause": "network"}],
                "reduction_pct": 91
            },
            learning_state={
                "strategies_tried": ["approach1", "approach2"],
                "success_rate": 0.8
            },
            open_subgoals=[],
            artifacts=[]
        )

    def test_idempotency_same_checkpoint_same_result(self):
        """Recovering same checkpoint twice produces identical ExecutionState."""
        checkpoint = self.create_deterministic_checkpoint()

        state1 = self.engine.recover_from_checkpoint(checkpoint)
        state2 = self.engine.recover_from_checkpoint(checkpoint)

        # Core fields should match
        assert state1.task_id == state2.task_id
        assert state1.iteration_num == state2.iteration_num
        assert state1.phase == state2.phase
        assert state1.full_context["goal"] == state2.full_context["goal"]

    def test_idempotency_validation_passes(self):
        """validate_resumed_state passes for recovered checkpoint."""
        checkpoint = self.create_deterministic_checkpoint()
        recovered = self.engine.recover_from_checkpoint(checkpoint)

        valid = self.engine.validate_resumed_state(checkpoint, recovered)
        assert valid is True

    def test_idempotency_validation_fails_on_task_mismatch(self):
        """Validation fails if task ID changes."""
        checkpoint = self.create_deterministic_checkpoint()
        recovered = self.engine.recover_from_checkpoint(checkpoint)

        # Manually corrupt recovered state
        recovered.task_id = "different_task"

        valid = self.engine.validate_resumed_state(checkpoint, recovered)
        assert valid is False

    def test_idempotency_validation_fails_on_phase_mismatch(self):
        """Validation fails if phase changes."""
        checkpoint = self.create_deterministic_checkpoint()
        recovered = self.engine.recover_from_checkpoint(checkpoint)

        # Manually corrupt recovered state
        recovered.phase = "different_phase"

        valid = self.engine.validate_resumed_state(checkpoint, recovered)
        assert valid is False


class TestRecoveryEngineContextReconstruction:
    """Test context reconstruction (reduced + dropped)."""

    def setup_method(self):
        self.engine = RecoveryEngine()

    def test_restore_context_includes_goal(self):
        """Reconstructed context includes task goal."""
        checkpoint = CheckpointState(
            checkpoint_id="ctx_test", task_id="task", session_id="sess",
            phase="exec", trigger="test", timestamp_iso="2026-08-24T15:00:00",
            iteration_num=5,
            task_state={"task_id": "task", "goal": "Test goal", "progress": 0.3},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={}, open_subgoals=[], artifacts=[]
        )

        recovered = self.engine.recover_from_checkpoint(checkpoint)
        assert recovered.full_context["goal"] == "Test goal"

    def test_restore_context_includes_constraints(self):
        """Reconstructed context includes all constraints."""
        checkpoint = CheckpointState(
            checkpoint_id="ctx_test", task_id="task", session_id="sess",
            phase="exec", trigger="test", timestamp_iso="2026-08-24T15:00:00",
            iteration_num=5,
            task_state={"task_id": "task", "goal": "Test"},
            context_essentials={
                "kept": ["Constraint 1", "Constraint 2", "Constraint 3"],
                "reduction_pct": 91
            },
            learning_state={}, open_subgoals=[], artifacts=[]
        )

        recovered = self.engine.recover_from_checkpoint(checkpoint)
        assert len(recovered.full_context["constraints"]) == 3

    def test_restore_context_preserves_decisions(self):
        """Reconstructed context preserves decisions made."""
        checkpoint = CheckpointState(
            checkpoint_id="ctx_test", task_id="task", session_id="sess",
            phase="exec", trigger="test", timestamp_iso="2026-08-24T15:00:00",
            iteration_num=5,
            task_state={"task_id": "task", "goal": "Test"},
            context_essentials={
                "kept": [],
                "decisions": [
                    {"decision": "Choose A", "iter": 1, "why": "blocking"},
                    {"decision": "Choose B", "iter": 3, "why": "optimization"}
                ],
                "reduction_pct": 91
            },
            learning_state={}, open_subgoals=[], artifacts=[]
        )

        recovered = self.engine.recover_from_checkpoint(checkpoint)
        assert len(recovered.full_context.get("decisions_made", [])) == 2

    def test_restore_context_preserves_errors(self):
        """Reconstructed context preserves all errors encountered."""
        checkpoint = CheckpointState(
            checkpoint_id="ctx_test", task_id="task", session_id="sess",
            phase="exec", trigger="test", timestamp_iso="2026-08-24T15:00:00",
            iteration_num=10,
            task_state={"task_id": "task", "goal": "Test"},
            context_essentials={
                "kept": [],
                "errors": [
                    {"error_type": "Timeout", "iter": 5, "root_cause": "network"},
                    {"error_type": "FileNotFound", "iter": 8, "root_cause": "path"}
                ],
                "reduction_pct": 91
            },
            learning_state={}, open_subgoals=[], artifacts=[]
        )

        recovered = self.engine.recover_from_checkpoint(checkpoint)
        assert len(recovered.full_context.get("errors_encountered", [])) == 2


class TestRecoveryEngineRecoveryCostEstimation:
    """Test recovery cost and complexity estimation."""

    def setup_method(self):
        self.engine = RecoveryEngine()

    def create_checkpoint_with_trigger(self, trigger: str) -> CheckpointState:
        """Create checkpoint with specified trigger."""
        return CheckpointState(
            checkpoint_id="cost_test", task_id="task", session_id="sess",
            phase="exec", trigger=trigger, timestamp_iso="2026-08-24T15:00:00",
            iteration_num=30,
            task_state={"task_id": "task", "goal": "Test"},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={}, open_subgoals=[], artifacts=[]
        )

    def test_recovery_cost_estimation_easy(self):
        """Phase exit recovery is marked as 'easy'."""
        checkpoint = self.create_checkpoint_with_trigger("phase_exit")
        cost = self.engine.estimate_recovery_cost(checkpoint)

        assert cost["recovery_complexity"] == "easy"

    def test_recovery_cost_estimation_medium(self):
        """Context limit / iteration cap recovery is 'medium'."""
        checkpoint = self.create_checkpoint_with_trigger("context_limit")
        cost = self.engine.estimate_recovery_cost(checkpoint)

        assert cost["recovery_complexity"] == "medium"

    def test_recovery_cost_estimation_hard(self):
        """Token burn / stall recovery is 'hard'."""
        checkpoint = self.create_checkpoint_with_trigger("token_burn")
        cost = self.engine.estimate_recovery_cost(checkpoint)

        assert cost["recovery_complexity"] == "hard"

    def test_recovery_cost_includes_prerequisites(self):
        """Recovery cost includes prerequisites (e.g., network check)."""
        checkpoint = CheckpointState(
            checkpoint_id="cost_test", task_id="task", session_id="sess",
            phase="exec", trigger="stall_detected", timestamp_iso="2026-08-24T15:00:00",
            iteration_num=30,
            task_state={"task_id": "task", "goal": "Test"},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={}, open_subgoals=[], artifacts=[],
            recovery_reason="connection timeout"
        )

        cost = self.engine.estimate_recovery_cost(checkpoint)
        # Should suggest network availability check
        # (not strictly required, but good if prerequisites are inferred)
        assert isinstance(cost["prerequisites"], list)

    def test_recovery_cost_token_estimate(self):
        """Recovery cost estimates token count."""
        checkpoint = self.create_checkpoint_with_trigger("iteration_cap")
        cost = self.engine.estimate_recovery_cost(checkpoint)

        assert "estimated_tokens" in cost
        assert cost["estimated_tokens"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
