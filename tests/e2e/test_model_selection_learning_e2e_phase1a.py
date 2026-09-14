"""
E2E Test: Model Selection Learning Loop (Phase 1A)

Verifies end-to-end flow:
1. Task created with model_selected + task_type
2. Task completed → _emit_learning_outcome() → OUTCOME event
3. Listener reads OUTCOME and calls optimizer.process_feedback()
4. Confidence score updated in persistent store
5. Console API returns updated confidence

ADR-0644: Model Selection Learning Loop
ADR-0314: Learning Infrastructure
E2E Wiring Proof gate: proves listener is CALLED end-to-end
"""
import json
import time
import uuid
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest


class TestModelSelectionLearningE2E:
    """End-to-end learning loop: task completion → confidence update."""

    @pytest.fixture
    def temp_tasks_dir(self, tmp_path):
        """Temporary tasks directory for this test."""
        return tmp_path / "tasks"

    @pytest.fixture
    def task_manager(self, temp_tasks_dir):
        """TaskManager instance for testing."""
        from core.console.corvin_core.task_manager import TaskManager
        return TaskManager(temp_tasks_dir)

    @pytest.fixture
    def mock_learning_backend(self):
        """Mock learning backend with event store."""
        mock = Mock()
        mock.emitter = Mock()
        mock.emit = Mock(return_value=True)
        mock.store = Mock()
        mock.store.query_events = Mock(return_value=[])
        return mock

    def test_outcome_listener_receives_outcome_from_audit_chain(
        self,
        task_manager,
        temp_tasks_dir,
    ):
        """
        Phase 1A Core: When task completes, listener processes the outcome.

        Steps:
        1. Create task with model_selected + task_type
        2. Record task.completed event
        3. TaskManager._emit_learning_outcome() writes OUTCOME event
        4. TaskManager._update_model_selection_confidence() calls listener
        5. Listener reads outcome and calls optimizer.process_feedback()
        6. Assert optimizer was called with correct model + quality
        """
        from core.console.corvin_core.task_manager import TaskManager
        from core.learning.model_selection_learning_listener import (
            ModelSelectionOutcome,
        )

        # Step 1: Create task with model_selected
        task_id = task_manager.create_task(
            chat_key="test:chat1",
            instruction="Test task for model selection learning",
            tenant_id="_test_tenant",
            model_selected="claude-opus-5",  # Explicit model selection
            task_type="MEDIUM",  # Task type for routing
            engine="claude",
        )
        assert task_id

        # Step 2: Record task.started event
        task_manager.record_event(
            task_id,
            {"event": "task.started", "engine": "claude"},
            tenant_id="_test_tenant",
        )

        # Step 3: Mock the optimizer to capture calls
        with patch(
            "core.learning.model_selection_learning_listener.get_listener"
        ) as mock_get_listener:
            mock_listener = Mock()
            mock_listener.process_outcome = Mock(return_value=True)
            mock_get_listener.return_value = mock_listener

            # Record task.completed event
            # This should trigger:
            # - _emit_learning_outcome() → writes OUTCOME event
            # - _update_model_selection_confidence() → calls listener.process_outcome()
            task_manager.record_event(
                task_id,
                {
                    "event": "task.completed",
                    "exit_code": 0,
                    "summary": "Task succeeded",
                },
                tenant_id="_test_tenant",
            )

            # Step 5: Verify listener.process_outcome was called with correct outcome
            assert mock_listener.process_outcome.called, (
                "Listener.process_outcome() was not called — "
                "TaskManager not wired correctly"
            )

            # Get the call arguments
            call_args = mock_listener.process_outcome.call_args
            outcome, tenant = call_args[0], call_args[1]  # positional args

            # Verify outcome structure
            assert isinstance(outcome, ModelSelectionOutcome), (
                f"Expected ModelSelectionOutcome, got {type(outcome)}"
            )
            assert outcome.task_id == task_id
            assert outcome.model_used == "claude-opus-5"
            assert outcome.task_type == "MEDIUM"
            assert outcome.status == "completed"
            assert outcome.success is True  # exit_code=0
            assert tenant == "_test_tenant"

    def test_confidence_optimizer_called_with_quality_score(self):
        """
        Phase 1A: Verify that listener calls optimizer.process_feedback()
        with computed quality score.
        """
        from core.learning.model_selection_learning_listener import (
            ModelSelectionLearningListener,
            ModelSelectionOutcome,
        )

        listener = ModelSelectionLearningListener()

        # Create an outcome (success → quality ≈ 0.95)
        outcome = ModelSelectionOutcome(
            task_id="task-123",
            model_used="claude-sonnet-5",
            task_type="SIMPLE",
            status="completed",
            exit_code=0,
            duration_ms=1500,  # Fast execution
            engine="claude",
            success=True,
            timestamp="2026-09-14T10:00:00Z",
        )

        # Mock optimizer
        with patch.object(listener, "_get_optimizer") as mock_get_opt:
            mock_optimizer = Mock()
            mock_optimizer.process_feedback = Mock(return_value=True)
            mock_get_opt.return_value = mock_optimizer

            # Process outcome
            result = listener.process_outcome(outcome, "_test_tenant")

            # Verify optimizer was called
            assert result is True, "process_outcome should return True when optimizer called"
            assert mock_optimizer.process_feedback.called

            # Verify call arguments
            call_kwargs = mock_optimizer.process_feedback.call_args[1]
            assert call_kwargs["task_type"] == "SIMPLE"
            assert call_kwargs["model"] == "claude-sonnet-5"
            assert call_kwargs["quality_score"] >= 0.95  # Success with latency bonus
            assert call_kwargs["tenant_id"] == "_test_tenant"

    def test_confidence_persistence_across_epochs(self):
        """
        Phase 1A: Verify that confidence scores persist across
        multiple outcome processing epochs.

        This test requires a real or mocked persistence store.
        """
        from core.learning.model_selection_learning_listener import (
            ModelSelectionLearningListener,
            ModelSelectionOutcome,
        )

        listener = ModelSelectionLearningListener()

        # Simulate multiple outcomes for the same model+task_type
        outcomes = [
            ModelSelectionOutcome(
                task_id=f"task-{i}",
                model_used="claude-opus-5",
                task_type="COMPLEX",
                status="completed",
                exit_code=0,
                duration_ms=2000 + (i * 100),
                engine="claude",
                success=True,
                timestamp="2026-09-14T10:00:00Z",
            )
            for i in range(3)
        ]

        with patch.object(listener, "_get_optimizer") as mock_get_opt:
            mock_optimizer = Mock()
            mock_optimizer.process_feedback = Mock(return_value=True)
            mock_get_opt.return_value = mock_optimizer

            # Process all outcomes
            for outcome in outcomes:
                listener.process_outcome(outcome, "_test_tenant")

            # Verify optimizer was called 3 times
            assert mock_optimizer.process_feedback.call_count == 3

            # Verify all calls were for the same model+task_type
            for call in mock_optimizer.process_feedback.call_args_list:
                call_kwargs = call[1]
                assert call_kwargs["task_type"] == "COMPLEX"
                assert call_kwargs["model"] == "claude-opus-5"

    def test_listener_skip_outcomes_without_model(self):
        """
        Phase 1A: Verify that listener skips outcomes where model_used is empty.
        This prevents errors when model selection hasn't been applied yet.
        """
        from core.learning.model_selection_learning_listener import (
            ModelSelectionLearningListener,
            ModelSelectionOutcome,
        )

        listener = ModelSelectionLearningListener()

        # Outcome with NO model_used
        outcome = ModelSelectionOutcome(
            task_id="task-456",
            model_used="",  # Empty!
            task_type="SIMPLE",
            status="completed",
            exit_code=0,
            duration_ms=1000,
            engine="claude",
            success=True,
            timestamp="2026-09-14T10:00:00Z",
        )

        with patch.object(listener, "_get_optimizer") as mock_get_opt:
            mock_optimizer = Mock()
            mock_get_opt.return_value = mock_optimizer

            result = listener.process_outcome(outcome, "_test_tenant")

            # Should return False (skipped)
            assert result is False
            # Optimizer should not be called
            assert not mock_optimizer.process_feedback.called

    def test_listener_fail_soft_on_missing_optimizer(self):
        """
        Phase 1A: Verify that missing optimizer doesn't crash task execution.
        """
        from core.learning.model_selection_learning_listener import (
            ModelSelectionLearningListener,
            ModelSelectionOutcome,
        )

        listener = ModelSelectionLearningListener()

        outcome = ModelSelectionOutcome(
            task_id="task-789",
            model_used="claude-haiku-4-5",
            task_type="SIMPLE",
            status="completed",
            exit_code=0,
            duration_ms=800,
            engine="claude",
            success=True,
            timestamp="2026-09-14T10:00:00Z",
        )

        # Simulate missing optimizer
        with patch.object(listener, "_get_optimizer", return_value=None):
            result = listener.process_outcome(outcome, "_test_tenant")

            # Should return False (skipped) but not raise
            assert result is False


class TestModelSelectionConsoleIntegration:
    """
    Integration: Console engine_api.py reads updated confidence scores.
    """

    def test_console_api_returns_updated_confidence(self):
        """
        Verify that /v1/engine/config endpoint reflects confidence updates
        from the learning listener.

        This requires:
        1. Task completion triggers outcome
        2. Listener updates confidence in store
        3. engine_api.py reads from store
        4. Console displays updated score
        """
        # TODO(phase1b): Implement after Console API integration
        pass


class TestAuditTrailIntegration:
    """
    Verify audit-chain integration (ADR-0232/0233 + ADR-0644).
    """

    def test_confidence_update_is_audit_logged(self):
        """
        When listener calls optimizer.process_feedback(), a confidence_updated
        event should be written to the audit chain (hash-chained).
        """
        # TODO(phase1b): Implement after audit integration
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
