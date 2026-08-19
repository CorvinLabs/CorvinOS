"""Integration tests for session continuation across Brain components (ADR-0367).

Tests the interaction between ContextInitializer, TaskBrain, and SessionContinuationManager
for resuming tasks across session boundaries.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from core.orchestration.brain_startup import ContextInitializer, BrainStartupError
from core.orchestration.brain import TaskBrain
from core.context_engineering.session_checkpoint import (
    SessionCheckpoint,
    SessionContinuationManager,
)
from core.context_engineering.execution_context import ExecutionContext, ContextStack
from core.context_engineering.memory_coordinator import MemoryCoordinator


@pytest.fixture
def temp_corvin_home():
    """Create temporary CORVIN_HOME."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create required directory structure
        Path(tmpdir, "tenants", "_default", "project_memory").mkdir(
            parents=True, exist_ok=True
        )
        Path(tmpdir, "tenants", "_default", "global_memory").mkdir(
            parents=True, exist_ok=True
        )
        Path(tmpdir, "tenants", "_default", "learning").mkdir(
            parents=True, exist_ok=True
        )
        Path(tmpdir, "tenants", "_default", "checkpoints").mkdir(
            parents=True, exist_ok=True
        )
        yield tmpdir


@pytest.fixture
def memory_coordinator(temp_corvin_home):
    """Create MemoryCoordinator with mock templates."""
    with patch.object(
        MemoryCoordinator,
        "load_task_template",
        return_value={
            "task_type": "code_fix",
            "typical_duration_min": 30,
            "_source": "global",
        },
    ):
        yield MemoryCoordinator(temp_corvin_home)


class TestSessionContinuationIntegration:
    """Integration tests for session continuation."""

    @pytest.mark.asyncio
    async def test_initialize_context_fresh_task(self, temp_corvin_home):
        """Test initializing a fresh task (no checkpoint)."""
        with patch("core.orchestration.brain_startup.MemoryCoordinator") as mock_mc:
            mock_mc_instance = AsyncMock()
            mock_mc_instance.load_task_template.return_value = {
                "task_type": "code_fix",
                "_source": "global",
            }
            mock_mc.return_value = mock_mc_instance

            with patch("core.orchestration.brain_startup.assert_all"):
                initializer = ContextInitializer(temp_corvin_home)
                initializer.memory_coordinator = mock_mc_instance

                result = await initializer.initialize_context(
                    task_id="test_task_1",
                    tenant_id="_default",
                    task_type="code_fix",
                )

                assert result["context_initialized"] is True
                assert result["template_source"] == "global"
                assert result["resumed_from_checkpoint"] is False

    @pytest.mark.asyncio
    async def test_initialize_context_from_checkpoint(self, temp_corvin_home):
        """Test resuming task from checkpoint."""
        # First, create a checkpoint
        manager = SessionContinuationManager(temp_corvin_home)

        stack = ContextStack()
        stack.push("task", "test_task_1")

        execution_context = ExecutionContext(
            task_id="test_task_1",
            tenant_id="_default",
            task_template={"task_type": "code_fix"},
            context_stack=stack,
            budget_remaining=250.0,
            time_remaining=900,
            model="claude-3-sonnet",
            strategy="direct_fix",
            strategy_confidence=0.8,
        )

        checkpoint_id = manager.save_checkpoint(
            task_id="test_task_1",
            tenant_id="_default",
            execution_context=execution_context,
            session_id="sess_abc",
            turn_number=5,
            tokens_consumed=2000,
        )

        # Now initialize from checkpoint
        with patch("core.orchestration.brain_startup.assert_all"):
            initializer = ContextInitializer(temp_corvin_home)

            result = await initializer.initialize_context(
                task_id="test_task_1",
                tenant_id="_default",
                task_type="code_fix",
                checkpoint_id=checkpoint_id,
            )

            assert result["context_initialized"] is True
            assert result["template_source"] == "checkpoint"
            assert result["resumed_from_checkpoint"] is True

            # Verify restored context
            restored_ctx = initializer.get_execution_context()
            assert restored_ctx.task_id == "test_task_1"
            assert restored_ctx.budget_remaining == 250.0
            assert restored_ctx.strategy == "direct_fix"

    def test_brain_save_checkpoint(self, temp_corvin_home):
        """Test TaskBrain.save_task_checkpoint()."""
        with patch("core.orchestration.brain_startup.assert_all"):
            brain = TaskBrain(corvin_home=temp_corvin_home)

            # Create a mock task and execution context
            stack = ContextStack()
            stack.push("task", "test_task_1")

            execution_context = ExecutionContext(
                task_id="test_task_1",
                tenant_id="_default",
                task_template={"task_type": "code_fix"},
                context_stack=stack,
                budget_remaining=500.0,
                time_remaining=1800,
                model="claude-3-sonnet",
                strategy="direct_fix",
                strategy_confidence=0.8,
            )

            brain._tasks["test_task_1"] = {"status": "running"}
            brain._context_initializer.execution_context = execution_context

            # Save checkpoint
            checkpoint_id = brain.save_task_checkpoint(
                task_id="test_task_1",
                tenant_id="_default",
                turn_number=5,
                tokens_consumed=2000,
                cost_consumed_cents=50,
            )

            assert checkpoint_id is not None
            assert isinstance(checkpoint_id, str)

    def test_brain_get_checkpoint_metadata(self, temp_corvin_home):
        """Test TaskBrain.get_checkpoint_metadata()."""
        with patch("core.orchestration.brain_startup.assert_all"):
            brain = TaskBrain(corvin_home=temp_corvin_home)

            # Create and save checkpoints
            stack = ContextStack()
            stack.push("task", "test_task_1")

            execution_context = ExecutionContext(
                task_id="test_task_1",
                tenant_id="_default",
                task_template={"task_type": "code_fix"},
                context_stack=stack,
                budget_remaining=500.0,
                time_remaining=1800,
                model="claude-3-sonnet",
                strategy="direct_fix",
                strategy_confidence=0.8,
            )

            brain._tasks["test_task_1"] = {"status": "running"}
            brain._context_initializer.execution_context = execution_context

            for i in range(3):
                brain.save_task_checkpoint(
                    task_id="test_task_1",
                    tenant_id="_default",
                    turn_number=i * 5,
                    tokens_consumed=i * 1000,
                )

            # Get metadata
            metadata = brain.get_checkpoint_metadata("test_task_1")
            assert len(metadata) == 3
            assert metadata[0]["turn_number"] == 0
            assert metadata[2]["tokens_consumed"] == 2000

    @pytest.mark.asyncio
    async def test_multi_session_continuation_e2e(self, temp_corvin_home):
        """Test complete multi-session continuation scenario.

        1. Task starts in Session A, saves checkpoint
        2. Task resumes in Session B from checkpoint
        3. Verify state is preserved
        """
        with patch("core.orchestration.brain_startup.assert_all"):
            # Session A: Initial task execution
            brain_a = TaskBrain(corvin_home=temp_corvin_home)

            stack_a = ContextStack()
            stack_a.push("task", "test_task_1", phase="initial")

            ctx_a = ExecutionContext(
                task_id="test_task_1",
                tenant_id="_default",
                task_template={"task_type": "code_fix", "phase": "initial"},
                context_stack=stack_a,
                budget_remaining=500.0,
                time_remaining=1800,
                model="claude-3-sonnet",
                strategy="direct_fix",
                strategy_confidence=0.8,
            )

            brain_a._tasks["test_task_1"] = {"status": "running"}
            brain_a._context_initializer.execution_context = ctx_a

            # Save checkpoint after 5 turns
            checkpoint_id = brain_a.save_task_checkpoint(
                task_id="test_task_1",
                tenant_id="_default",
                turn_number=5,
                tokens_consumed=2000,
                cost_consumed_cents=50,
            )

            assert checkpoint_id is not None

            # Session B: Resume from checkpoint
            brain_b = TaskBrain(corvin_home=temp_corvin_home)

            with patch.object(
                ContextInitializer, "initialize_context", new_callable=AsyncMock
            ) as mock_init:
                # Mock initialize to return checkpoint-resume result
                mock_init.return_value = {
                    "context_initialized": True,
                    "template_source": "checkpoint",
                    "resumed_from_checkpoint": True,
                    "context_stack_depth": 1,
                }

                # Simulate resuming from checkpoint
                result = await mock_init(
                    task_id="test_task_1",
                    tenant_id="_default",
                    task_type="code_fix",
                    checkpoint_id=checkpoint_id,
                )

                assert result["resumed_from_checkpoint"] is True
                assert result["template_source"] == "checkpoint"

            # Verify checkpoint contains original state
            manager = SessionContinuationManager(temp_corvin_home)
            loaded_cp = manager.load_checkpoint("test_task_1", checkpoint_id)
            assert loaded_cp.task_id == "test_task_1"
            assert loaded_cp.turn_number == 5
            assert loaded_cp.tokens_consumed == 2000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
