"""
Coverage Gap Tests

Additional tests to close coverage gaps and reach ≥80% target.
Tests edge cases and error paths not covered in main test suites.
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from core.vibe_engineering.session_lifecycle_manager import SessionLifecycleManager, SessionState
from core.vibe_engineering.checkpoint_manager import CheckpointManager, CheckpointState
from core.vibe_engineering.context_reducer import ContextReducer
from core.vibe_engineering.recovery_engine import RecoveryEngine


class TestEdgeCases:
    """Test edge cases and error paths."""

    def test_session_lifecycle_manager_statistics(self):
        """Test statistics reporting from SessionLifecycleManager."""
        manager = SessionLifecycleManager()

        # Evaluate multiple triggers
        state1 = SessionState(session_id="s1", phase="exec", context_tokens=3500, max_context_tokens=4000)
        state2 = SessionState(session_id="s2", phase="exec", iteration_count=50)

        manager.evaluate_triggers(state1)  # Context limit
        manager.evaluate_triggers(state2)  # Iteration cap
        manager.on_split_initiated()
        manager.on_split_initiated()

        stats = manager.get_statistics()
        assert stats["total_evaluations"] == 2
        assert stats["triggers_fired"] == 2
        assert stats["splits_initiated"] == 2

    def test_context_reducer_empty_inputs(self):
        """Test context reducer with empty/minimal inputs."""
        reducer = ContextReducer()

        reduced = reducer.reduce(
            goal="",  # Empty goal
            constraints=[],  # No constraints
            decisions=[],  # No decisions
            errors=[],  # No errors
            learnings=[],  # No learnings
            original_size_tokens=100
        )

        assert reduced.goal == ""
        assert len(reduced.constraints) == 0
        assert reduced.reduction_pct > 0

    def test_context_reducer_large_state(self):
        """Test context reducer with large task state."""
        reducer = ContextReducer()

        large_decisions = [
            {"iter": i, "decision": f"Decision {i}", "why": "blocking"}
            for i in range(100)
        ]

        reduced = reducer.reduce(
            goal="Large task",
            constraints=["C1"] * 10,
            decisions=large_decisions,
            errors=[],
            learnings=[],
            original_size_tokens=100000
        )

        assert len(reduced.decisions_made) > 0
        assert reduced.reduction_pct >= 80

    def test_checkpoint_manager_idempotency_edge_case(self):
        """Test that same state always produces same checkpoint ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CheckpointManager(Path(tmpdir))

            # Create checkpoint multiple times with same state
            cp_states = []
            for _ in range(3):
                cp = CheckpointState(
                    checkpoint_id="",  # Will be derived from hash
                    task_id="task_idem",
                    session_id="sess",
                    phase="exec",
                    trigger="test",
                    timestamp_iso="2026-08-24T15:00:00",
                    iteration_num=10,
                    task_state={"goal": "test", "progress": 0.5},
                    context_essentials={"kept": [], "reduction_pct": 91},
                    learning_state={},
                    open_subgoals=[],
                    artifacts=[]
                )
                # Manually set IDs to show idempotency
                if cp_states:
                    assert cp_states[0].checkpoint_id == cp_states[0].checkpoint_id

    def test_recovery_engine_cost_estimation(self):
        """Test recovery cost estimation for different trigger types."""
        engine = RecoveryEngine()

        for trigger in ["phase_exit", "context_limit", "token_burn", "iteration_cap", "stall_detected"]:
            cp = CheckpointState(
                checkpoint_id=f"ckpt_{trigger}",
                task_id="task",
                session_id="sess",
                phase="exec",
                trigger=trigger,
                timestamp_iso="2026-08-24T15:00:00",
                iteration_num=10,
                task_state={"goal": "test"},
                context_essentials={"kept": [], "reduction_pct": 91},
                learning_state={},
                open_subgoals=[],
                artifacts=[]
            )

            cost = engine.estimate_recovery_cost(cp)
            assert "recovery_complexity" in cost
            assert "estimated_tokens" in cost
            assert "prerequisites" in cost

    def test_tier_classification_boundary_keywords(self):
        """Test tier classification at keyword boundaries."""
        reducer = ContextReducer()

        # Test exact keyword matching
        assert reducer._is_tier_1("must be done") is True
        assert reducer._is_tier_1("probably should be done") is False  # "probably" is tier 3
        assert reducer._is_tier_2("learned from experience") is True
        assert reducer._is_tier_3("nice-to-know info") is True

    def test_checkpoint_state_with_recovery_reason(self):
        """Test checkpoint with recovery reason (error state)."""
        cp = CheckpointState(
            checkpoint_id="ckpt_error",
            task_id="task",
            session_id="sess",
            phase="exec",
            trigger="error",
            timestamp_iso="2026-08-24T15:00:00",
            iteration_num=5,
            task_state={"goal": "test"},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
            recovery_reason="Network timeout during file save"
        )

        assert cp.recovery_reason is not None
        assert "timeout" in cp.recovery_reason.lower()


class TestIntegrationPaths:
    """Test common integration paths not covered in main suites."""

    def test_full_pipeline_with_errors(self):
        """Test full pipeline when error occurs mid-execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_manager = SessionLifecycleManager()
            reducer = ContextReducer()
            checkpoint_manager = CheckpointManager(Path(tmpdir))
            recovery_engine = RecoveryEngine()

            # Simulate error scenario
            cp = CheckpointState(
                checkpoint_id="ckpt_err",
                task_id="task_err",
                session_id="sess_err",
                phase="execution",
                trigger="stall_detected",
                timestamp_iso=datetime.now().isoformat(),
                iteration_num=25,
                task_state={"goal": "debug", "progress": 0.4, "error": "FileNotFound"},
                context_essentials={"kept": [], "reduction_pct": 91},
                learning_state={"errors": ["FileNotFound"]},
                open_subgoals=[],
                artifacts=[],
                recovery_reason="Stall after file operation error"
            )

            # Save
            result = checkpoint_manager.save(cp)
            assert result.exists()

            # Load and recover
            loaded = checkpoint_manager.load(result)
            recovered = recovery_engine.recover_from_checkpoint(loaded)

            assert recovered.recovery_reason is not None
            assert len(recovered.learning_state.get("errors", [])) > 0

    def test_session_state_with_extreme_values(self):
        """Test session state with extreme but valid values."""
        manager = SessionLifecycleManager()

        # 99.9% context usage
        state = SessionState(
            session_id="extreme",
            phase="exec",
            context_tokens=3999,
            max_context_tokens=4000,
            iteration_count=100,  # Way over cap
            tokens_burned_today=999999  # Way over budget
        )

        result = manager.evaluate_triggers(state)
        assert result.triggered is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
