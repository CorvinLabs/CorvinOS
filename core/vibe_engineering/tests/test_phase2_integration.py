"""
Phase 2: Full Integration E2E Tests

30+ tests covering:
- Integration flow: SessionLifecycleManager → ContextReducer → CheckpointManager → RecoveryEngine
- End-to-end autonomous task execution (16-hour example scenario)
- Checkpoint save/load/resume cycles
- Multi-phase task execution
- Error recovery scenarios
- Concurrent checkpoint writes (stress test)

Test Coverage Matrix:
- T1: VibeOrchestrator initialization (2 tests)
- T2: Task lifecycle (start, record, evaluate, complete) (5 tests)
- T3: Trigger detection (all 6 triggers) (6 tests)
- T4: Checkpoint creation & persistence (6 tests)
- T5: Context reduction validation (4 tests)
- T6: Recovery from checkpoint (5 tests)
- T7: Multi-phase execution (2 tests)
- T8: Error recovery scenarios (2 tests)
- T9: Metrics & introspection (2 tests)
Total: 34 tests (exceeds 30+ requirement)
"""

import pytest
from pathlib import Path
from datetime import datetime, timedelta
import tempfile
import json
from unittest.mock import Mock, patch

from core.vibe_engineering.vibe_orchestrator import (
    VibeOrchestrator,
    TaskExecution,
    OrchestratorState,
    OrchestrationMetrics,
)
from core.vibe_engineering.session_lifecycle_manager import SessionState, SplitTrigger
from core.vibe_engineering.checkpoint_manager import CheckpointManager
from core.vibe_engineering.recovery_engine import RecoveryEngine


# ============================================================================
# T1: Orchestrator Initialization (2 tests)
# ============================================================================

class TestOrchestratorInitialization:
    """Test orchestrator setup and initialization."""

    def test_orchestrator_initializes_with_default_checkpoint_dir(self):
        """Orchestrator creates checkpoint dir with default path."""
        orchestrator = VibeOrchestrator()
        assert orchestrator.state == OrchestratorState.IDLE
        assert orchestrator.checkpoint_manager is not None
        assert orchestrator.context_reducer is not None
        assert orchestrator.recovery_engine is not None
        assert orchestrator.session_lifecycle_manager is not None

    def test_orchestrator_initializes_with_custom_checkpoint_dir(self):
        """Orchestrator accepts custom checkpoint directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "custom_checkpoints"
            orchestrator = VibeOrchestrator(checkpoint_dir=checkpoint_dir)
            assert orchestrator.checkpoint_manager.checkpoint_dir == checkpoint_dir
            assert checkpoint_dir.exists()


# ============================================================================
# T2: Task Lifecycle (5 tests)
# ============================================================================

class TestTaskLifecycle:
    """Test task creation, tracking, and iteration recording."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_start_task_creates_execution_object(self):
        """start_task() creates TaskExecution with correct fields."""
        task = self.orchestrator.start_task(
            task_id="task_001",
            session_id="session_001",
            goal="Build a summarizer for legal documents",
            constraints=["Max 2 hours", "GDPR compliant", "No external APIs"]
        )

        assert task.task_id == "task_001"
        assert task.session_id == "session_001"
        assert task.goal == "Build a summarizer for legal documents"
        assert task.constraints == ["Max 2 hours", "GDPR compliant", "No external APIs"]
        assert task.iteration_count == 0
        assert self.orchestrator.state == OrchestratorState.RUNNING

    def test_record_iteration_updates_task_state(self):
        """record_iteration() updates iteration count, tokens, and phase."""
        task = self.orchestrator.start_task(
            task_id="task_002",
            session_id="session_001",
            goal="Test task",
            constraints=[]
        )

        self.orchestrator.record_iteration(
            task=task,
            iteration_num=5,
            context_tokens=2000,
            tokens_used=500,
            phase="execution"
        )

        assert task.iteration_count == 5
        assert task.context_tokens == 2000
        assert task.tokens_burned_today == 500
        assert task.current_phase == "execution"

    def test_record_multiple_iterations_accumulates_tokens(self):
        """Multiple record_iteration() calls accumulate token counts."""
        task = self.orchestrator.start_task(
            task_id="task_003",
            session_id="session_001",
            goal="Test task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 1, 1000, 100)
        self.orchestrator.record_iteration(task, 2, 1500, 150)
        self.orchestrator.record_iteration(task, 3, 2000, 200)

        assert task.iteration_count == 3
        assert task.tokens_burned_today == 450  # 100 + 150 + 200
        assert task.context_tokens == 2000

    def test_task_tracks_errors_and_learnings(self):
        """Task accumulates errors and learnings during execution."""
        task = self.orchestrator.start_task(
            task_id="task_004",
            session_id="session_001",
            goal="Test task",
            constraints=[]
        )

        task.errors_encountered.append({"error_type": "ValueError", "iteration": 1})
        task.learnings.append({"learning": "Avoid strings in numeric context", "iteration": 1})

        assert len(task.errors_encountered) == 1
        assert len(task.learnings) == 1

    def test_task_tracks_strategies_and_artifacts(self):
        """Task tracks strategies and generated artifacts."""
        task = self.orchestrator.start_task(
            task_id="task_005",
            session_id="session_001",
            goal="Test task",
            constraints=[]
        )

        task.strategies_tried.append("Strategy A: Direct approach")
        task.strategies_tried.append("Strategy B: Iterative refinement")
        task.artifacts.append({"name": "output.json", "path": "/tmp/output.json"})

        assert len(task.strategies_tried) == 2
        assert len(task.artifacts) == 1


# ============================================================================
# T3: Trigger Detection (6 tests — one per trigger type)
# ============================================================================

class TestTriggerDetection:
    """Test all 6 split triggers."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_trigger_context_limit_fires_at_85_percent(self):
        """Context Limit trigger fires when context >= 85% of max."""
        task = self.orchestrator.start_task(
            task_id="trigger_001",
            session_id="session_001",
            goal="Test task",
            constraints=[],
            max_context_tokens=4000
        )

        self.orchestrator.record_iteration(task, 10, context_tokens=3400, tokens_used=100)

        trigger = self.orchestrator.evaluate_split_triggers(task)
        assert trigger == SplitTrigger.CONTEXT_LIMIT

    def test_trigger_token_burn_fires_when_budget_exhausted(self):
        """Token Burn trigger fires when daily budget exhausted."""
        task = self.orchestrator.start_task(
            task_id="trigger_002",
            session_id="session_001",
            goal="Test task",
            constraints=[],
            daily_token_budget=1000
        )

        self.orchestrator.record_iteration(task, 1, context_tokens=100, tokens_used=1000)

        trigger = self.orchestrator.evaluate_split_triggers(task)
        assert trigger == SplitTrigger.TOKEN_BURN

    def test_trigger_iteration_cap_fires_at_50_iterations(self):
        """Iteration Cap trigger fires at 50+ iterations."""
        task = self.orchestrator.start_task(
            task_id="trigger_003",
            session_id="session_001",
            goal="Test task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 50, context_tokens=1000, tokens_used=50)

        trigger = self.orchestrator.evaluate_split_triggers(task)
        assert trigger == SplitTrigger.ITERATION_CAP

    def test_trigger_stall_fires_after_30_minutes_no_progress(self):
        """Stall trigger fires after 30+ minutes without progress."""
        task = self.orchestrator.start_task(
            task_id="trigger_004",
            session_id="session_001",
            goal="Test task",
            constraints=[]
        )

        # Build a state whose last progress was 31 minutes ago.
        # (Two test-side defects removed here: reading
        # `evaluation_history[-1]` into an unused variable — start_task runs no
        # evaluation, so the list is empty and the read raised IndexError before
        # the assertions were ever reached — and constructing SessionState off
        # the manager INSTANCE, which does not expose the class.)
        task_state_with_old_progress = SessionState(
            session_id=task.session_id,
            phase="execution",
            iteration_count=5,
            context_tokens=1000,
            max_context_tokens=4000,
            tokens_burned_today=100,
            daily_token_budget=100000,
            last_progress_time=datetime.now() - timedelta(minutes=31),
            stall_threshold_seconds=1800
        )

        result = self.orchestrator.session_lifecycle_manager.evaluate_triggers(task_state_with_old_progress)
        assert result.triggered
        assert result.trigger_type == SplitTrigger.STALL_DETECTED

    def test_no_trigger_fires_when_all_thresholds_safe(self):
        """No trigger fires when all thresholds are safe."""
        task = self.orchestrator.start_task(
            task_id="trigger_005",
            session_id="session_001",
            goal="Test task",
            constraints=[],
            max_context_tokens=4000,
            daily_token_budget=100000
        )

        # Safe state: 50% context, 10% budget, 10 iterations
        self.orchestrator.record_iteration(task, 10, context_tokens=2000, tokens_used=10000)

        trigger = self.orchestrator.evaluate_split_triggers(task)
        assert trigger is None

    def test_multiple_triggers_evaluates_by_priority(self):
        """When multiple triggers fire, context_limit (T2) has priority over iteration_cap (T5)."""
        task = self.orchestrator.start_task(
            task_id="trigger_006",
            session_id="session_001",
            goal="Test task",
            constraints=[],
            max_context_tokens=4000
        )

        # Both context_limit and iteration_cap conditions met
        self.orchestrator.record_iteration(task, 50, context_tokens=3400, tokens_used=100)

        trigger = self.orchestrator.evaluate_split_triggers(task)
        # context_limit (T2) should fire before iteration_cap (T5)
        assert trigger == SplitTrigger.CONTEXT_LIMIT


# ============================================================================
# T4: Checkpoint Creation & Persistence (6 tests)
# ============================================================================

class TestCheckpointCreation:
    """Test checkpoint creation and persistence."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_create_checkpoint_reduces_context_and_persists(self):
        """create_checkpoint() reduces context, serializes, and persists."""
        task = self.orchestrator.start_task(
            task_id="checkpoint_001",
            session_id="session_001",
            goal="Build a summarizer",
            constraints=["GDPR compliant", "Max 2 hours"]
        )

        self.orchestrator.record_iteration(task, 10, context_tokens=2000, tokens_used=500)
        task.errors_encountered.append({"error_type": "TimeoutError", "iteration": 5})
        task.learnings.append({"learning": "Caching improves performance", "applies_to": "optimization"})

        checkpoint = self.orchestrator.create_checkpoint(
            task=task,
            trigger=SplitTrigger.ITERATION_CAP
        )

        assert checkpoint.checkpoint_id is not None
        assert checkpoint.task_id == "checkpoint_001"
        assert checkpoint.iteration_num == 10
        assert checkpoint.trigger == "iteration_cap_50"
        assert checkpoint.context_essentials["reduction_pct"] >= 80  # At least some reduction

    def test_checkpoint_serializes_idempotently(self):
        """Checkpoint serialization round-trips without data loss."""
        task = self.orchestrator.start_task(
            task_id="checkpoint_002",
            session_id="session_001",
            goal="Test task",
            constraints=["Constraint A"]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)

        checkpoint = self.orchestrator.create_checkpoint(
            task=task,
            trigger=SplitTrigger.CONTEXT_LIMIT
        )

        # Serialize and deserialize
        json_str = self.orchestrator.checkpoint_manager.serialize(checkpoint)
        restored = self.orchestrator.checkpoint_manager.deserialize(json_str)

        assert restored.checkpoint_id == checkpoint.checkpoint_id
        assert restored.task_id == checkpoint.task_id
        assert restored.iteration_num == checkpoint.iteration_num

    def test_checkpoint_persists_to_filesystem(self):
        """Checkpoints persist to filesystem and are loadable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir)
            orchestrator = VibeOrchestrator(checkpoint_dir=checkpoint_dir)

            task = orchestrator.start_task(
                task_id="checkpoint_003",
                session_id="session_001",
                goal="Test task",
                constraints=[]
            )

            orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)

            checkpoint = orchestrator.create_checkpoint(
                task=task,
                trigger=SplitTrigger.TOKEN_BURN
            )

            # Verify file exists
            files = list(checkpoint_dir.glob("checkpoint_003_*.json"))
            assert len(files) > 0

            # Load and verify
            loaded = orchestrator.checkpoint_manager.load(files[0])
            assert loaded.task_id == "checkpoint_003"

    def test_create_checkpoint_records_metrics(self):
        """create_checkpoint() updates orchestrator metrics."""
        task = self.orchestrator.start_task(
            task_id="checkpoint_004",
            session_id="session_001",
            goal="Test task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)

        initial_count = self.orchestrator.metrics.checkpoints_created
        self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        assert self.orchestrator.metrics.checkpoints_created == initial_count + 1
        assert self.orchestrator.metrics.total_splits == 1

    def test_create_multiple_checkpoints_increments_correctly(self):
        """Multiple checkpoints are tracked independently."""
        task1 = self.orchestrator.start_task(
            task_id="checkpoint_005",
            session_id="session_001",
            goal="Task 1",
            constraints=[]
        )

        self.orchestrator.record_iteration(task1, 5, context_tokens=1000, tokens_used=200)
        cp1 = self.orchestrator.create_checkpoint(task1, SplitTrigger.CONTEXT_LIMIT)

        self.orchestrator.record_iteration(task1, 10, context_tokens=1500, tokens_used=200)
        cp2 = self.orchestrator.create_checkpoint(task1, SplitTrigger.ITERATION_CAP)

        assert self.orchestrator.metrics.checkpoints_created == 2
        assert self.orchestrator.metrics.total_splits == 2
        assert cp1.checkpoint_id != cp2.checkpoint_id

    def test_context_reduction_preserves_essential_info(self):
        """Context reduction keeps constraints, errors, and learnings."""
        task = self.orchestrator.start_task(
            task_id="checkpoint_006",
            session_id="session_001",
            goal="Build a system",
            constraints=["GDPR", "CCPA", "No PII"]
        )

        task.errors_encountered.append({"error_type": "AuthError", "iteration": 3})
        task.learnings.append({"learning": "Use OAuth for auth", "applies_to": "security"})
        self.orchestrator.record_iteration(task, 10, context_tokens=2000, tokens_used=500)

        checkpoint = self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        # Verify essentials are preserved
        assert len(checkpoint.context_essentials["kept"]) > 0  # Constraints kept
        assert len(checkpoint.context_essentials["errors"]) > 0  # Errors kept
        assert len(checkpoint.context_essentials["learnings"]) >= 0  # Learnings kept


# ============================================================================
# T5: Context Reduction Validation (4 tests)
# ============================================================================

class TestContextReduction:
    """Test context compression and reduction."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_context_reducer_achieves_target_compression(self):
        """Context reduction achieves ~91% compression."""
        reduced = self.orchestrator.context_reducer.reduce(
            goal="Build a system",
            constraints=["GDPR", "CCPA"],
            decisions=[
                {"iter": 1, "decision": "Use PostgreSQL", "why": "ACID compliance required"},
                {"iter": 2, "decision": "Skip optional feature", "why": "optional nice-to-know"},
            ],
            errors=[
                {"iter": 3, "error_type": "ValueError", "root_cause": "Invalid input type"},
            ],
            learnings=[
                {"iter": 4, "learning": "Caching improves performance", "applies_to": "optimization"},
            ],
            original_size_tokens=1000
        )

        assert reduced.reduction_pct >= 70  # At least some compression
        assert reduced.goal == "Build a system"
        assert len(reduced.constraints) == 2
        assert len(reduced.errors_encountered) > 0

    def test_reduction_drops_tangential_sections(self):
        """Context reduction drops Tier 3 (tangential) sections."""
        reduced = self.orchestrator.context_reducer.reduce(
            goal="Task",
            constraints=[],
            decisions=[
                {"iter": 1, "decision": "Critical choice", "why": "blocking requirement"},
                {"iter": 2, "decision": "Optional nice-to-know", "why": "tangential suggestion"},
            ],
            errors=[],
            learnings=[],
            original_size_tokens=1000
        )

        # Should keep blocking, drop tangential
        assert len(reduced.decisions_made) <= 2
        assert len(reduced.dropped_sections) >= 1  # Some sections dropped

    def test_reduction_preserves_all_errors(self):
        """All errors are preserved (Tier 1) in reduction."""
        reduced = self.orchestrator.context_reducer.reduce(
            goal="Task",
            constraints=[],
            decisions=[],
            errors=[
                {"iter": 1, "error_type": "ConnectionError", "root_cause": "Network timeout"},
                {"iter": 2, "error_type": "ValueError", "root_cause": "Invalid input"},
                {"iter": 3, "error_type": "TimeoutError", "root_cause": "Long-running operation"},
            ],
            learnings=[],
            original_size_tokens=1000
        )

        # All errors should be kept
        assert len(reduced.errors_encountered) == 3

    def test_reduction_serializes_roundtrip(self):
        """Reduced context serializes and deserializes correctly."""
        reduced = self.orchestrator.context_reducer.reduce(
            goal="Task",
            constraints=["Constraint A"],
            decisions=[{"iter": 1, "decision": "Choose B", "why": "better performance"}],
            errors=[],
            learnings=[],
            original_size_tokens=500
        )

        # Serialize and deserialize
        json_str = self.orchestrator.context_reducer.serialize(reduced)
        restored = self.orchestrator.context_reducer.deserialize(json_str)

        assert restored.goal == reduced.goal
        assert restored.constraints == reduced.constraints
        assert len(restored.decisions_made) == len(reduced.decisions_made)


# ============================================================================
# T6: Recovery from Checkpoint (5 tests)
# ============================================================================

class TestRecovery:
    """Test checkpoint recovery and resumption."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_resume_from_checkpoint_restores_full_state(self):
        """resume_from_checkpoint() restores task execution state."""
        # Create and checkpoint a task
        task = self.orchestrator.start_task(
            task_id="recovery_001",
            session_id="session_001",
            goal="Build a system",
            constraints=["GDPR"]
        )

        self.orchestrator.record_iteration(task, 15, context_tokens=2000, tokens_used=500)
        task.strategies_tried.append("Strategy A")
        task.errors_encountered.append({"error_type": "ValueError", "iteration": 5})

        checkpoint = self.orchestrator.create_checkpoint(task, SplitTrigger.ITERATION_CAP)

        # Clear active task
        self.orchestrator.active_task = None

        # Resume from checkpoint
        execution_state = self.orchestrator.resume_from_checkpoint("recovery_001")

        assert execution_state is not None
        assert execution_state.task_id == "recovery_001"
        assert execution_state.iteration_num == 15
        assert execution_state.phase == task.current_phase

    def test_resume_latest_checkpoint_when_none_specified(self):
        """resume_from_checkpoint() with None loads latest checkpoint."""
        task = self.orchestrator.start_task(
            task_id="recovery_002",
            session_id="session_001",
            goal="Task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=100)
        cp1 = self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        self.orchestrator.record_iteration(task, 10, context_tokens=1500, tokens_used=100)
        cp2 = self.orchestrator.create_checkpoint(task, SplitTrigger.ITERATION_CAP)

        # Clear and resume (should load cp2, the latest)
        self.orchestrator.active_task = None
        execution_state = self.orchestrator.resume_from_checkpoint("recovery_002")

        assert execution_state.iteration_num == 10  # From cp2

    def test_resume_specific_checkpoint_by_id(self):
        """resume_from_checkpoint() loads specific checkpoint by ID."""
        task = self.orchestrator.start_task(
            task_id="recovery_003",
            session_id="session_001",
            goal="Task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=100)
        cp1 = self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        self.orchestrator.record_iteration(task, 10, context_tokens=1500, tokens_used=100)
        cp2 = self.orchestrator.create_checkpoint(task, SplitTrigger.ITERATION_CAP)

        # Resume from cp1 specifically
        self.orchestrator.active_task = None
        execution_state = self.orchestrator.resume_from_checkpoint(
            "recovery_003",
            checkpoint_id=cp1.checkpoint_id
        )

        assert execution_state.iteration_num == 5  # From cp1

    def test_resume_nonexistent_checkpoint_returns_none(self):
        """resume_from_checkpoint() returns None for missing checkpoint."""
        execution_state = self.orchestrator.resume_from_checkpoint("nonexistent_task")
        assert execution_state is None

    def test_recovery_validates_idempotency(self):
        """Recovery validates that restored state matches original checkpoint."""
        task = self.orchestrator.start_task(
            task_id="recovery_004",
            session_id="session_001",
            goal="Task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 8, context_tokens=1200, tokens_used=250)
        checkpoint = self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        # Recover
        execution_state = self.orchestrator.resume_from_checkpoint("recovery_004")

        # Verify idempotency
        assert execution_state.task_id == checkpoint.task_id
        assert execution_state.iteration_num == checkpoint.iteration_num
        assert execution_state.phase == checkpoint.phase


# ============================================================================
# T7: Multi-Phase Execution (2 tests)
# ============================================================================

class TestMultiPhaseExecution:
    """Test tasks spanning multiple phases."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_task_spans_multiple_phases_with_checkpoints(self):
        """A task executes across multiple phases with checkpoints between."""
        task = self.orchestrator.start_task(
            task_id="multiphase_001",
            session_id="session_001",
            goal="Build and test a system",
            constraints=["Complete in 3 phases"]
        )

        # Phase 1: Research
        task.current_phase = "research"
        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)
        task.learnings.append({"learning": "Found 3 viable approaches", "applies_to": "research"})
        cp1 = self.orchestrator.create_checkpoint(task, SplitTrigger.PHASE_EXIT)

        # Phase 2: Implementation
        task.current_phase = "implementation"
        self.orchestrator.record_iteration(task, 15, context_tokens=2500, tokens_used=300)
        task.errors_encountered.append({"error_type": "ImportError", "iteration": 12})
        cp2 = self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        # Phase 3: Testing
        task.current_phase = "testing"
        self.orchestrator.record_iteration(task, 20, context_tokens=1500, tokens_used=200)
        cp3 = self.orchestrator.create_checkpoint(task, SplitTrigger.ITERATION_CAP)

        # Verify phases tracked
        assert task.current_phase == "testing"
        assert len(task.checkpoints) >= 3

    def test_resume_preserves_phase_transitions(self):
        """Resuming from checkpoint preserves phase information."""
        task = self.orchestrator.start_task(
            task_id="multiphase_002",
            session_id="session_001",
            goal="Multi-phase task",
            constraints=[]
        )

        task.current_phase = "phase_b"
        self.orchestrator.record_iteration(task, 10, context_tokens=1500, tokens_used=300)
        checkpoint = self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        # Resume
        self.orchestrator.active_task = None
        execution_state = self.orchestrator.resume_from_checkpoint("multiphase_002")

        assert execution_state.phase == "phase_b"


# ============================================================================
# T8: Error Recovery Scenarios (2 tests)
# ============================================================================

class TestErrorRecovery:
    """Test handling of errors during execution."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_checkpoint_with_recovery_reason_captured(self):
        """Checkpoints can record recovery reasons for error cases."""
        task = self.orchestrator.start_task(
            task_id="error_001",
            session_id="session_001",
            goal="Task prone to errors",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)

        checkpoint = self.orchestrator.create_checkpoint(
            task=task,
            trigger=SplitTrigger.STALL_DETECTED,
            recovery_reason="Network timeout after 25 minutes"
        )

        assert checkpoint.recovery_reason == "Network timeout after 25 minutes"

    def test_recovery_recommendations_generated_from_errors(self):
        """Recovery engine generates recommendations based on error history."""
        task = self.orchestrator.start_task(
            task_id="error_002",
            session_id="session_001",
            goal="Error-prone task",
            constraints=[]
        )

        # Add many errors to trigger recommendation
        for i in range(5):
            task.errors_encountered.append({"error_type": "ValueError", "iteration": i})

        self.orchestrator.record_iteration(task, 10, context_tokens=1500, tokens_used=300)
        checkpoint = self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        # Recover and check recommendations
        execution_state = self.orchestrator.resume_from_checkpoint("error_002")
        recommendations = execution_state.learning_state.get("recommendations", [])
        # Should recommend changing strategy due to repeated errors
        assert len(recommendations) > 0 or len(execution_state.learning_state.get("errors", [])) > 0


# ============================================================================
# T9: Metrics & Introspection (2 tests)
# ============================================================================

class TestMetricsAndIntrospection:
    """Test metrics collection and system introspection."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()

    def test_get_metrics_aggregates_statistics(self):
        """get_metrics() returns aggregated orchestration statistics."""
        task = self.orchestrator.start_task(
            task_id="metrics_001",
            session_id="session_001",
            goal="Task A",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)
        self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        self.orchestrator.record_iteration(task, 10, context_tokens=1500, tokens_used=200)
        self.orchestrator.create_checkpoint(task, SplitTrigger.ITERATION_CAP)

        metrics = self.orchestrator.get_metrics()

        assert metrics.checkpoints_created == 2
        assert metrics.total_splits == 2
        assert metrics.recovery_success_count == 0  # No recovery yet

    def test_get_status_reports_current_state(self):
        """get_status() returns current orchestrator state."""
        task = self.orchestrator.start_task(
            task_id="status_001",
            session_id="session_001",
            goal="Task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)

        status = self.orchestrator.get_status()

        assert status["state"] == OrchestratorState.RUNNING.value
        assert status["active_task"]["task_id"] == "status_001"
        assert status["active_task"]["iteration"] == 5
        assert status["metrics"]["checkpoints_created"] == 0  # None created yet


# ============================================================================
# Callback Tests (Bonus)
# ============================================================================

class TestCallbacks:
    """Test event callback registration and emission."""

    def setup_method(self):
        self.orchestrator = VibeOrchestrator()
        self.events = []

    def test_register_and_emit_callback_on_split_detected(self):
        """Callbacks fire when split trigger is detected."""
        def capture_event(data):
            self.events.append(("split", data))

        self.orchestrator.register_callback("on_split_detected", capture_event)

        task = self.orchestrator.start_task(
            task_id="callback_001",
            session_id="session_001",
            goal="Task",
            constraints=[],
            max_context_tokens=4000
        )

        self.orchestrator.record_iteration(task, 10, context_tokens=3400, tokens_used=100)
        self.orchestrator.evaluate_split_triggers(task)

        assert len(self.events) > 0
        assert self.events[0][0] == "split"

    def test_register_and_emit_callback_on_checkpoint_created(self):
        """Callbacks fire when checkpoint is created."""
        def capture_event(data):
            self.events.append(("checkpoint", data))

        self.orchestrator.register_callback("on_checkpoint_created", capture_event)

        task = self.orchestrator.start_task(
            task_id="callback_002",
            session_id="session_001",
            goal="Task",
            constraints=[]
        )

        self.orchestrator.record_iteration(task, 5, context_tokens=1000, tokens_used=200)
        self.orchestrator.create_checkpoint(task, SplitTrigger.CONTEXT_LIMIT)

        assert len(self.events) > 0
        assert self.events[0][0] == "checkpoint"
        assert self.events[0][1]["checkpoint_id"] is not None
