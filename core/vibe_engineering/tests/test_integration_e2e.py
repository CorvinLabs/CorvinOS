"""
End-to-End Integration Tests

Tests all 4 subsystems working together:
SessionLifecycleManager → ContextReducer → CheckpointManager → RecoveryEngine
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from core.vibe_engineering.session_lifecycle_manager import (
    SessionLifecycleManager,
    SessionState,
    SplitTrigger
)
from core.vibe_engineering.context_reducer import ContextReducer
from core.vibe_engineering.checkpoint_manager import CheckpointManager, CheckpointState
from core.vibe_engineering.recovery_engine import RecoveryEngine
from core.vibe_engineering.checkpoint_fallback import CheckpointFallback


class TestFullAutonomyPipeline:
    """Test complete autonomous task execution + recovery."""

    def setup_method(self):
        """Set up all subsystems."""
        self.tmpdir = tempfile.mkdtemp()

        self.session_manager = SessionLifecycleManager()
        self.context_reducer = ContextReducer(target_reduction_pct=91)
        self.checkpoint_manager = CheckpointManager(Path(self.tmpdir))
        self.recovery_engine = RecoveryEngine()
        self.fallback = CheckpointFallback(self.checkpoint_manager, self.recovery_engine)

    def test_complete_pipeline_context_limit_trigger(self):
        """Full pipeline: trigger detection → context reduction → checkpoint → recovery."""

        # 1. Initialize task
        task_goal = "Analyze security logs"
        constraints = ["Filesystem only", "Concurrent writes expected"]

        # 2. Simulate execution (reach context limit)
        session = SessionState(
            session_id="session_001",
            phase="execution",
            iteration_count=5,
            context_tokens=3500,  # 87.5% of 4000 max
            max_context_tokens=4000
        )

        # 3. Detect split trigger
        trigger_eval = self.session_manager.evaluate_triggers(session)

        assert trigger_eval.triggered is True
        assert trigger_eval.trigger_type == SplitTrigger.CONTEXT_LIMIT

        # 4. Reduce context (91% compression)
        reduced_context = self.context_reducer.reduce(
            goal=task_goal,
            constraints=constraints,
            decisions=[
                {"iter": 1, "decision": "Use regex approach", "why": "blocking"},
                {"iter": 3, "decision": "Consider ML approach", "why": "tangential"}
            ],
            errors=[
                {"iter": 2, "error_type": "FileNotFound", "root_cause": "path issue"}
            ],
            learnings=[
                {"iter": 2, "learning": "Regex alone insufficient", "applies_to": "strategy"}
            ],
            original_size_tokens=10000
        )

        assert reduced_context.reduction_pct >= 80
        assert reduced_context.goal == task_goal

        # 5. Create checkpoint with reduced context
        checkpoint = CheckpointState(
            checkpoint_id="ckpt_e2e_001",
            task_id="task_audit",
            session_id="session_001",
            phase="execution",
            trigger=trigger_eval.trigger_type.value,
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=session.iteration_count,
            task_state={
                "task_id": "task_audit",
                "goal": task_goal,
                "progress": 0.5,
                "context_tokens": session.context_tokens,
                "max_context_tokens": session.max_context_tokens
            },
            context_essentials={
                "kept": constraints,
                "decisions": [{"decision": "Use regex approach", "iter": 1, "why": "blocking"}],
                "errors": [{"error_type": "FileNotFound", "iter": 2, "root_cause": "path issue"}],
                "reduction_pct": reduced_context.reduction_pct
            },
            learning_state={
                "strategies_tried": ["regex"],
                "success_rate": 0.6
            },
            open_subgoals=[
                {"description": "parse logs", "status": "in_progress"}
            ],
            artifacts=[]
        )

        # 6. Save checkpoint (with fallback)
        result = self.fallback.save_with_fallback(checkpoint)
        assert result.success is True

        # 7. Simulate task pause (context limit reached)
        # ... operator reviews progress, approves split ...

        # 8. Resume in new session (recover state)
        loaded_checkpoint = self.fallback.load_with_fallback(checkpoint.checkpoint_id)
        assert loaded_checkpoint is not None

        # 9. Recover full execution state
        execution_state = self.recovery_engine.recover_from_checkpoint(loaded_checkpoint)

        # 10. Validate recovered state
        assert execution_state.task_id == "task_audit"
        assert execution_state.iteration_num == 5
        assert execution_state.full_context["goal"] == task_goal
        assert len(execution_state.full_context["constraints"]) == 2
        assert execution_state.learning_state["success_rate"] == 0.6

        # 11. Verify idempotency
        valid = self.recovery_engine.validate_resumed_state(
            loaded_checkpoint, execution_state
        )
        assert valid is True

    def test_pipeline_stall_detection_and_recovery(self):
        """Pipeline test: stall detection → checkpoint → recovery."""

        # 1. Simulate stalled session (30+ min no progress)
        session = SessionState(
            session_id="session_stall",
            phase="debugging",
            iteration_count=45,
            last_progress_time=datetime.now() - timedelta(seconds=1860)  # 31 min ago
        )

        # 2. Detect stall trigger
        trigger_eval = self.session_manager.evaluate_triggers(session)

        assert trigger_eval.triggered is True
        assert trigger_eval.trigger_type == SplitTrigger.STALL_DETECTED

        # 3. Create and save checkpoint
        checkpoint = CheckpointState(
            checkpoint_id="ckpt_stall_001",
            task_id="task_stalled",
            session_id="session_stall",
            phase="debugging",
            trigger=trigger_eval.trigger_type.value,
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=45,
            task_state={"task_id": "task_stalled", "goal": "Debug issue", "progress": 0.3},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={"strategies_tried": ["approach_a", "approach_b"]},
            open_subgoals=[],
            artifacts=[],
            recovery_reason="stall_detected: 31 minutes without progress"
        )

        result = self.fallback.save_with_fallback(checkpoint)
        assert result.success is True

        # 4. Load and recover
        loaded = self.fallback.load_with_fallback("ckpt_stall_001")
        execution_state = self.recovery_engine.recover_from_checkpoint(loaded)

        # 5. Verify recovery includes reason
        assert execution_state.recovery_reason is not None
        assert "stall_detected" in execution_state.recovery_reason

    def test_pipeline_multiple_splits_same_task(self):
        """Pipeline test: task goes through 2 splits in same session."""

        task_id = "task_multi_split"

        # Split 1: Context limit at iteration 25
        checkpoint1 = CheckpointState(
            checkpoint_id="ckpt_s1_025",
            task_id=task_id,
            session_id="session_split1",
            phase="execution",
            trigger="context_limit",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=25,
            task_state={"task_id": task_id, "goal": "Test", "progress": 0.33},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={"strategies_tried": ["a"]},
            open_subgoals=[],
            artifacts=[]
        )

        result1 = self.fallback.save_with_fallback(checkpoint1)
        assert result1.success is True

        # Split 2: Iteration cap at iteration 50
        checkpoint2 = CheckpointState(
            checkpoint_id="ckpt_s2_050",
            task_id=task_id,
            session_id="session_split2",
            phase="execution",
            trigger="iteration_cap",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=50,
            task_state={"task_id": task_id, "goal": "Test", "progress": 0.66},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={"strategies_tried": ["a", "b"]},
            open_subgoals=[],
            artifacts=[]
        )

        result2 = self.fallback.save_with_fallback(checkpoint2)
        assert result2.success is True

        # Verify both can be recovered
        loaded1 = self.fallback.load_with_fallback("ckpt_s1_025")
        loaded2 = self.fallback.load_with_fallback("ckpt_s2_050")

        assert loaded1 is not None
        assert loaded2 is not None
        assert loaded1.iteration_num == 25
        assert loaded2.iteration_num == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
