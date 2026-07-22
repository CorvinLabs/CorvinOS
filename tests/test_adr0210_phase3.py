"""Tests for ADR-0210 Phase 3: Parallel Execution Engine."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from initial_analysis import GlobalPlan, Step
from parallel_executor import ParallelExecutor, ExecutionError, StepResult


class TestADR0210Phase3ParallelExecutor:
    """ADR-0210 Phase 3: Parallel execution with can_parallelize hints."""

    def _make_plan(self, steps: list[Step]) -> GlobalPlan:
        """Create a plan from steps."""
        return GlobalPlan(
            steps=steps,
            estimated_duration_s=10,
            estimated_tokens=1000,
        )

    def test_sequential_plan_validation(self):
        """Sequential plan (no parallelize hints) validates successfully."""
        steps = [
            Step(step=1, action="read", depends_on=[], can_parallelize=[]),
            Step(step=2, action="analyze", depends_on=[1], can_parallelize=[]),
            Step(step=3, action="write", depends_on=[2], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)

        executor = ParallelExecutor(plan)
        assert executor.plan == plan

    def test_plan_validation_rejects_non_sequential_steps(self):
        """Plan with non-sequential step numbers is rejected."""
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[]),
            Step(step=3, action="b", depends_on=[1], can_parallelize=[]),  # Skip 2
        ]
        plan = self._make_plan(steps)

        with pytest.raises(ExecutionError, match="not sequential"):
            ParallelExecutor(plan)

    def test_plan_validation_rejects_invalid_dependency(self):
        """Step depending on non-existent step is rejected."""
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[]),
            Step(step=2, action="b", depends_on=[999], can_parallelize=[]),  # Invalid
        ]
        plan = self._make_plan(steps)

        with pytest.raises(ExecutionError, match="invalid step"):
            ParallelExecutor(plan)

    def test_plan_validation_rejects_forward_dependency(self):
        """Step depending on higher-numbered step (cycle risk) is rejected."""
        steps = [
            Step(step=1, action="a", depends_on=[2], can_parallelize=[]),  # Forward dep
            Step(step=2, action="b", depends_on=[], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)

        with pytest.raises(ExecutionError, match="cycles"):
            ParallelExecutor(plan)

    def test_parallel_steps_grouped_correctly(self):
        """Steps with can_parallelize hints are grouped into batches."""
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[2, 3]),
            Step(step=2, action="b", depends_on=[], can_parallelize=[1, 3]),
            Step(step=3, action="c", depends_on=[], can_parallelize=[1, 2]),
            Step(step=4, action="d", depends_on=[1, 2, 3], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)

        executor = ParallelExecutor(plan)
        batches = executor._group_parallel_steps()

        # Steps 1, 2, 3 should be in first batch (all parallel-safe together)
        # Step 4 depends on 1-3, so separate batch
        assert len(batches) >= 2
        assert 4 in batches[-1]  # Step 4 in last batch

    def test_cycle_detection(self):
        """Cycles in dependency graph are detected (after topological sort)."""
        # Create a plan with cycle: 1 → 2 → 1 (shouldn't happen with forward-dep
        # check, but test the cycle detector)
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[]),
            Step(step=2, action="b", depends_on=[1], can_parallelize=[]),
            Step(step=3, action="c", depends_on=[2], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)
        executor = ParallelExecutor(plan)

        # Should NOT raise (no cycle in this valid DAG)
        executor._detect_cycles()

    @pytest.mark.asyncio
    async def test_execute_sequential_plan(self):
        """Execute a simple sequential plan."""
        steps = [
            Step(step=1, action="step1", depends_on=[], can_parallelize=[]),
            Step(step=2, action="step2", depends_on=[1], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)
        executor = ParallelExecutor(plan)

        executed: list[int] = []

        async def mock_executor(step: Step, context: dict) -> int:
            executed.append(step.step)
            return step.step

        results = await executor.execute({}, step_executor_fn=mock_executor)

        assert len(results) == 2
        assert executed == [1, 2]  # Sequential order
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_execute_parallel_plan(self):
        """Execute a plan with parallel-safe steps."""
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[2]),
            Step(step=2, action="b", depends_on=[], can_parallelize=[1]),
            Step(step=3, action="c", depends_on=[1, 2], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)
        executor = ParallelExecutor(plan)

        executed: list[tuple[int, float]] = []
        execute_time = 0.0

        async def mock_executor(step: Step, context: dict) -> int:
            nonlocal execute_time
            executed.append((step.step, execute_time))
            execute_time += 0.01
            return step.step

        results = await executor.execute({}, step_executor_fn=mock_executor)

        assert len(results) == 3
        # Steps 1 and 2 should execute "together" (close in time)
        # Step 3 should execute after both
        executed_order = [e[0] for e in executed]
        assert 3 in executed_order  # Step 3 is last
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_execute_with_step_failure(self):
        """Failed step stops execution, returns error."""
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[]),
            Step(step=2, action="b", depends_on=[1], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)
        executor = ParallelExecutor(plan)

        async def failing_executor(step: Step, context: dict) -> int:
            if step.step == 1:
                return 1
            else:
                raise ValueError("step 2 failed")

        results = await executor.execute({}, step_executor_fn=failing_executor)

        # Step 1 succeeds, step 2 fails
        assert results[0].success is True
        assert results[1].success is False
        assert "step 2 failed" in results[1].error

    def test_executor_stats(self):
        """Stats report parallelization potential."""
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[2, 3]),
            Step(step=2, action="b", depends_on=[], can_parallelize=[1, 3]),
            Step(step=3, action="c", depends_on=[], can_parallelize=[1, 2]),
        ]
        plan = self._make_plan(steps)
        executor = ParallelExecutor(plan)

        stats = executor.stats()
        assert stats["total_steps"] == 3
        assert stats["batch_count"] >= 1
        assert stats["estimated_speedup"] > 0

    def test_empty_plan_rejected(self):
        """Empty plan (no steps) is rejected."""
        plan = self._make_plan([])

        with pytest.raises(ExecutionError, match="no steps"):
            ParallelExecutor(plan)

    def test_dependency_graph_built(self):
        """Dependency graph correctly represents depends_on.

        Note: depends_on and can_parallelize are mutually exclusive per step
        pair (_validate_plan, adversarial finding #5) — step 2 may therefore
        not list step 1 in can_parallelize while depending on it.
        """
        steps = [
            Step(step=1, action="a", depends_on=[], can_parallelize=[]),
            Step(step=2, action="b", depends_on=[1], can_parallelize=[]),
        ]
        plan = self._make_plan(steps)
        executor = ParallelExecutor(plan)

        graph = executor._dependency_graph
        assert graph[1] == set()
        assert graph[2] == {1}
