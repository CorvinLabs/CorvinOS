"""ADR-0210 Phase 3: Parallel Execution Engine.

Execute steps from GlobalPlan using can_parallelize hints for wall-clock speedup.
Builds dependency DAG, detects cycles, groups parallel-safe steps, executes
concurrently via asyncio.

CI lint: module MUST NOT import anthropic.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from initial_analysis import GlobalPlan, Step, InitialAnalysisRequest

_logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    """Raised on execution failures (cycles, missing dependencies, etc.)."""


@dataclass
class StepResult:
    """Result of executing a single step."""
    step_num: int
    action: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: int = 0


class ParallelExecutor:
    """Execute steps from GlobalPlan with parallelization.

    Uses can_parallelize hints to group independent steps and run them
    concurrently. Validates dependency graph (detects cycles), enforces
    depends_on ordering, executes parallel groups via asyncio.gather().
    """

    def __init__(self, plan: GlobalPlan) -> None:
        """Initialize executor with a plan.

        Args:
            plan: GlobalPlan from InitialAnalysisRequest (contains steps + hints).

        Raises:
            ExecutionError: If plan has cycles or malformed dependencies.
        """
        self.plan = plan
        self._validate_plan()
        self._dependency_graph = self._build_dependency_graph()

    def _validate_plan(self) -> None:
        """Validate that plan is well-formed.

        Checks:
        - Step numbers are sequential (1, 2, 3, ...)
        - can_parallelize and depends_on reference valid step numbers
        - No impossible dependencies (depends_on higher step numbers)
        - depends_on and can_parallelize are mutually exclusive
        """
        if not self.plan.steps:
            raise ExecutionError("Plan has no steps")

        step_nums = {s.step for s in self.plan.steps}
        if step_nums != set(range(1, len(self.plan.steps) + 1)):
            raise ExecutionError(f"Step numbers not sequential: {step_nums}")

        for s in self.plan.steps:
            # Check depends_on references valid steps
            for dep in s.depends_on:
                if dep not in step_nums:
                    raise ExecutionError(f"Step {s.step} depends on invalid step {dep}")
                if dep >= s.step:
                    raise ExecutionError(
                        f"Step {s.step} depends on later step {dep} (cycles)"
                    )
                # Validate mutual exclusivity: can't depend AND parallelize same step
                if dep in s.can_parallelize:
                    raise ExecutionError(
                        f"Step {s.step} both depends on {dep} and parallelize it (conflict)"
                    )
            # Check can_parallelize references valid steps
            for par in s.can_parallelize:
                if par not in step_nums:
                    raise ExecutionError(f"Step {s.step} parallelize invalid {par}")

    def _build_dependency_graph(self) -> dict[int, set[int]]:
        """Build adjacency list: step_num → set of steps it depends on.

        Returns:
            Map of step_num → dependencies (as set of step numbers).
        """
        graph: dict[int, set[int]] = {}
        for step in self.plan.steps:
            graph[step.step] = set(step.depends_on)
        return graph

    def _detect_cycles(self) -> None:
        """Detect cycles in dependency DAG (should be none, but verify).

        Uses DFS to find cycles. Raises ExecutionError if found.
        """
        visited: set[int] = set()
        rec_stack: set[int] = set()

        def has_cycle_dfs(node: int) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for dep in self._dependency_graph.get(node, set()):
                if dep not in visited:
                    if has_cycle_dfs(dep):
                        return True
                elif dep in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        for step_num in self._dependency_graph:
            if step_num not in visited:
                if has_cycle_dfs(step_num):
                    raise ExecutionError(f"Cycle detected in dependency graph")

    def should_parallelize(self) -> bool:
        """Check if plan allows parallelization.

        Per ADR-0210, ``fallback_strategy`` describes what to do when parallel
        execution fails — it is NOT the mode selector. Parallel batching is
        driven by the plan itself: any step carrying ``can_parallelize`` hints
        opts the plan into batched execution (``_group_parallel_steps`` only
        ever co-batches steps whose hints are symmetric, so plans without
        hints degrade to sequential singleton batches anyway). An explicit
        non-"sequential" strategy also enables batching.

        Returns:
            True if any step has can_parallelize hints or fallback_strategy
            is non-"sequential"; False otherwise.
        """
        if self.plan.fallback_strategy != "sequential":
            return True
        return any(s.can_parallelize for s in self.plan.steps)

    def _group_parallel_steps(self) -> list[list[int]]:
        """Group steps into batches for parallel execution.

        A batch is a set of steps with no dependencies on each other.
        Steps are grouped left-to-right; each batch runs before next.

        Returns:
            List of step-number lists (each list = one parallel batch).
        """
        # Topological sort: build execution order respecting depends_on
        ready: set[int] = {s for s in self._dependency_graph if not self._dependency_graph[s]}
        completed: set[int] = set()
        batches: list[list[int]] = []

        while ready:
            # In this batch, find steps that are parallel-safe
            batch: list[int] = []

            for s in sorted(ready):
                step_obj = next(st for st in self.plan.steps if st.step == s)
                # Check if s can parallelize with other ready steps in batch
                # Validate symmetric parallelization: both must list each other
                can_add = True
                for other_step in batch:
                    other_step_obj = next(st for st in self.plan.steps if st.step == other_step)
                    # Both directions must agree: s in other's can_parallelize AND vice versa
                    if not (s in other_step_obj.can_parallelize and other_step in step_obj.can_parallelize):
                        can_add = False
                        break
                if can_add:
                    batch.append(s)

            if batch:
                batches.append(batch)
                completed.update(batch)
                # Remove all batch steps from ready (not just batch[0])
                ready -= set(batch)

                # Find new ready steps (all dependencies now complete)
                for step in self.plan.steps:
                    if (step.step not in completed and
                        all(d in completed for d in step.depends_on)):
                        ready.add(step.step)

        return batches

    async def execute(
        self,
        context: dict[str, Any],
        *,
        step_executor_fn: Callable[[Step, dict[str, Any]], Any],
    ) -> list[StepResult]:
        """Execute all steps respecting dependencies and parallelization hints.

        Args:
            context: Task context (files, state, config, etc.).
            step_executor_fn: Async function that executes a single step.
                             Called as: await step_executor_fn(step, context).

        Returns:
            List of StepResult objects (one per step).
        """
        self._detect_cycles()
        # Batch when the plan opts in (can_parallelize hints or an explicit
        # non-"sequential" strategy) — see should_parallelize()
        if self.should_parallelize():
            batches = self._group_parallel_steps()
        else:
            # Sequential mode: each step in its own batch
            batches = [[s.step] for s in self.plan.steps]

        results_by_step: dict[int, StepResult] = {}
        all_results: list[StepResult] = []

        for batch_num, batch in enumerate(batches):
            _logger.info(f"Executing parallel batch {batch_num + 1}/{len(batches)}: steps {batch}")

            # Gather all step objects for this batch
            batch_steps = [s for s in self.plan.steps if s.step in batch]

            # Execute batch concurrently
            try:
                batch_results = await asyncio.gather(
                    *[step_executor_fn(s, context) for s in batch_steps],
                    return_exceptions=True,
                )

                # Process results
                for step_obj, result in zip(batch_steps, batch_results):
                    if isinstance(result, Exception):
                        step_result = StepResult(
                            step_num=step_obj.step,
                            action=step_obj.action,
                            success=False,
                            error=str(result),
                        )
                    else:
                        step_result = StepResult(
                            step_num=step_obj.step,
                            action=step_obj.action,
                            success=True,
                            output=result,
                        )
                    results_by_step[step_obj.step] = step_result
                    all_results.append(step_result)

                # Check for failures; fail-fast on first error
                if any(not r.success for r in [results_by_step[s] for s in batch]):
                    _logger.warning(f"Batch {batch_num + 1} had failures")
                    break

            except Exception as e:
                _logger.error(f"Batch execution failed: {e}")
                raise ExecutionError(f"Batch {batch_num + 1} execution error: {e}")

        return all_results

    def stats(self) -> dict[str, Any]:
        """Return execution statistics (batches, parallelization potential)."""
        batches = self._group_parallel_steps()
        max_batch_size = max(len(b) for b in batches) if batches else 0
        total_steps = len(self.plan.steps)
        sequential_estimate = sum(len(b) for b in batches)
        parallel_speedup = total_steps / max(1, max_batch_size)

        return {
            "total_steps": total_steps,
            "batch_count": len(batches),
            "max_batch_size": max_batch_size,
            "sequential_steps": sequential_estimate,
            "estimated_speedup": parallel_speedup,
            "estimated_duration_s": self.plan.estimated_duration_s,
        }
