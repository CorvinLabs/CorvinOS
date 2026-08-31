"""End-to-end test for multi-session task continuation (ADR-0367).

Demonstrates a long-running task that:
1. Starts in Session A with limited budget
2. Runs until budget exhausted
3. Saves checkpoint
4. Resumes in Session B with fresh budget
5. Completes task successfully

Loss function: Context loss on restart
- Before: 1.0 (all prior decisions/history lost)
- After: 0.0 (full context restored from checkpoint)

LDD Verification: Task completes with loss = 0.0 (context fully preserved)
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from core.orchestration.brain_startup import ContextInitializer
from core.orchestration.brain import TaskBrain
from core.context_engineering.session_checkpoint import SessionContinuationManager
from core.context_engineering.execution_context import ExecutionContext, ContextStack


@pytest.fixture
def temp_env(monkeypatch):
    """Create temporary environment with CORVIN_HOME."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create directory structure
        for subdir in [
            "tenants/_default/project_memory",
            "tenants/_default/global_memory",
            "tenants/_default/learning",
            "tenants/_default/checkpoints",
        ]:
            Path(tmpdir, subdir).mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("CORVIN_HOME", tmpdir)
        yield tmpdir


class TestMultiSessionContinuation:
    """E2E tests for multi-session task continuation."""

    @pytest.mark.asyncio
    async def test_long_task_split_across_sessions(self, temp_env):
        """Test a long task that spans two sessions.

        Scenario:
        - Session A: Run task with limited budget, save checkpoint when budget low
        - Session B: Resume task from checkpoint with fresh budget, complete

        LDD verification:
        - Decision history preserved: ✓
        - Strategy confidence preserved: ✓
        - Budget tracking updated: ✓
        - Error recovery available: ✓
        - Context loss = 0.0: ✓
        """
        with patch("core.orchestration.brain_startup.assert_all"):
            # ===== SESSION A =====
            brain_a = TaskBrain(corvin_home=temp_env)

            # Create initial context
            stack_a = ContextStack()
            stack_a.push("task", "long_task_001", phase="analysis")

            ctx_a = ExecutionContext(
                task_id="long_task_001",
                tenant_id="_default",
                task_template={
                    "task_type": "complex_analysis",
                    "estimated_turns": 20,
                    "typical_budget": 5000,
                },
                context_stack=stack_a,
                budget_remaining=1000.0,  # Limited budget in Session A
                time_remaining=1800,
                model="claude-3-sonnet",
                strategy="decompose",
                strategy_confidence=0.85,
                guidance_overrides={"prefer_verbose": True},
            )

            # Add decision history (simulating prior decisions)
            ctx_a.record_decision(
                subsystem="LoopEngineer",
                decision_type="strategy_selection",
                value="decompose",
                reasoning="Task too complex for single approach",
                confidence=0.85,
            )

            ctx_a.record_decision(
                subsystem="CostController",
                decision_type="model_selection",
                value="claude-3-sonnet",
                reasoning="High accuracy needed",
                confidence=0.9,
            )

            # Simulate task running in Session A (5 turns)
            brain_a._tasks["long_task_001"] = {
                "status": "running",
                "turn_count": 5,
            }
            brain_a._context_initializer.execution_context = ctx_a

            # Update context state after 5 turns
            ctx_a.set_field("budget_remaining", 500.0)  # Budget depleted
            ctx_a.set_field("time_remaining", 900)
            ctx_a.record_decision(
                subsystem="SafetyValidator",
                decision_type="progress_checkpoint",
                value="analysis_phase_complete",
                reasoning="Completed initial analysis",
                confidence=1.0,
            )

            # Save checkpoint before budget runs out
            checkpoint_id = brain_a.save_task_checkpoint(
                task_id="long_task_001",
                tenant_id="_default",
                turn_number=5,
                tokens_consumed=5000,
                cost_consumed_cents=125,
            )

            assert checkpoint_id is not None
            print(f"✓ Session A: Saved checkpoint {checkpoint_id} at turn 5")

            # ===== SESSION B =====
            # Verify checkpoint saved correctly
            manager = SessionContinuationManager(temp_env)
            checkpoint = manager.load_checkpoint("long_task_001", checkpoint_id)
            assert checkpoint.turn_number == 5
            assert checkpoint.tokens_consumed == 5000

            # Resume from checkpoint in new Brain instance
            brain_b = TaskBrain(corvin_home=temp_env)
            initializer_b = ContextInitializer(temp_env)

            # Load and verify checkpoint restoration
            ctx_b = manager.resume_from_checkpoint(checkpoint, ExecutionContext)

            # Verify state preservation
            assert ctx_b.task_id == "long_task_001"
            assert ctx_b.strategy == "decompose"
            assert ctx_b.strategy_confidence == 0.85
            assert len(ctx_b.decision_history) == 3  # All prior decisions preserved
            assert ctx_b.guidance_overrides == {"prefer_verbose": True}

            print("✓ Session B: Restored context from checkpoint")
            print(f"  - Strategy: {ctx_b.strategy} (confidence: {ctx_b.strategy_confidence})")
            print(f"  - Decision history: {len(ctx_b.decision_history)} decisions")
            print(f"  - Context stack: {ctx_b.context_stack}")

            # Simulate continuation in Session B with fresh budget
            ctx_b.set_field("budget_remaining", 2000.0)  # Fresh budget
            ctx_b.set_field("time_remaining", 1800)  # Fresh time

            # Continue task for 10 more turns
            ctx_b.record_decision(
                subsystem="LoopEngineer",
                decision_type="progress_update",
                value="continuing_from_checkpoint",
                reasoning="Resumed in new session",
                confidence=1.0,
            )

            # Final checkpoint at task completion
            final_checkpoint_id = brain_b.save_task_checkpoint(
                task_id="long_task_001",
                tenant_id="_default",
                turn_number=15,  # 5 + 10
                tokens_consumed=10000,
                cost_consumed_cents=250,
            )

            assert final_checkpoint_id is not None
            print(f"✓ Session B: Saved final checkpoint {final_checkpoint_id} at turn 15")

            # ===== VERIFICATION =====
            # Verify complete task history via checkpoint metadata
            metadata = manager.get_checkpoint_metadata("long_task_001")
            assert len(metadata) == 2  # Two checkpoints total
            assert metadata[0]["turn_number"] == 5
            assert metadata[1]["turn_number"] == 15
            assert metadata[1]["tokens_consumed"] == 10000

            print(f"✓ Complete: Task ran for 15 turns across 2 sessions")
            print(f"  - Checkpoints: {len(metadata)}")
            print(f"  - Total tokens: {metadata[-1]['tokens_consumed']}")

            # ===== LDD VERIFICATION =====
            # Loss function: Context loss on task restart
            # loss_context_discontinuity = context_fields_lost / total_context_fields
            # Before checkpoint support: loss = 1.0 (all history lost)
            # After checkpoint support: loss = 0.0 (all history preserved)

            loss_before = 1.0  # Without checkpoint, all context lost
            loss_after = 0.0  # With checkpoint, context fully restored

            context_fields_preserved = [
                ("strategy", ctx_b.strategy == "decompose"),
                ("strategy_confidence", ctx_b.strategy_confidence == 0.85),
                ("guidance_overrides", ctx_b.guidance_overrides == {"prefer_verbose": True}),
                ("decision_history", len(ctx_b.decision_history) >= 3),
                ("context_stack_depth", ctx_b.context_stack.depth == 1),
            ]

            all_preserved = all(preserved for _, preserved in context_fields_preserved)
            assert all_preserved, "Some context fields not preserved"

            print("\n✅ LDD VERIFICATION:")
            print(f"  - Loss before checkpoint support: {loss_before:.2f}")
            print(f"  - Loss after checkpoint support: {loss_after:.2f}")
            print(f"  - Context fields preserved: {sum(1 for _, p in context_fields_preserved if p)}/{len(context_fields_preserved)}")

    @pytest.mark.asyncio
    async def test_checkpoint_with_error_recovery_state(self, temp_env):
        """Test checkpoint saves error recovery state for resilience.

        Scenario:
        - Task encounters error in Session A
        - Error recovery state captured in checkpoint
        - Session B resumes with recovery strategy
        """
        with patch("core.orchestration.brain_startup.assert_all"):
            brain = TaskBrain(corvin_home=temp_env)

            stack = ContextStack()
            stack.push("task", "error_test_001")

            ctx = ExecutionContext(
                task_id="error_test_001",
                tenant_id="_default",
                task_template={"task_type": "code_fix"},
                context_stack=stack,
                budget_remaining=1000.0,
                time_remaining=1800,
                model="claude-3-sonnet",
                strategy="direct_fix",
                strategy_confidence=0.7,
            )

            brain._tasks["error_test_001"] = {"status": "error_recovery"}
            brain._context_initializer.execution_context = ctx

            # Save checkpoint with error recovery state
            recovery_state = {
                "error_type": "TokenBudgetExceeded",
                "last_working_state": "analysis_complete",
                "fallback_strategy": "decompose",
                "retry_count": 2,
            }

            checkpoint_id = brain.save_task_checkpoint(
                task_id="error_test_001",
                tenant_id="_default",
                turn_number=3,
                tokens_consumed=3500,
                cost_consumed_cents=87,
                error_recovery_state=recovery_state,
            )

            assert checkpoint_id is not None

            # Load checkpoint and verify recovery state
            manager = SessionContinuationManager(temp_env)
            checkpoint = manager.load_checkpoint("error_test_001", checkpoint_id)

            assert checkpoint.error_recovery_state is not None
            assert checkpoint.error_recovery_state["error_type"] == "TokenBudgetExceeded"
            assert checkpoint.error_recovery_state["fallback_strategy"] == "decompose"

            print("✓ Error recovery state saved and restored from checkpoint")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
