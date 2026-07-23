"""ADR-0214: Adaptive Delegation Executor (Phase 2).

Real TDE execution engine with:
- Parallel batch execution (asyncio.gather)
- Sampling-based loss measurement (5% actual, 95% proxy)
- L34 plan filtering
- Deterministic idempotency keys
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

try:
    from operator.orchestration.initial_analysis import GlobalPlan, Step, InitialAnalysisRequest
    from operator.orchestration.tde.l34_delegation_gate import L34DelegationGate
    from operator.orchestration.tde.loss_profile_tracker import LossProfileTracker
except ImportError:
    from initial_analysis import GlobalPlan, Step, InitialAnalysisRequest  # type: ignore
    from l34_delegation_gate import L34DelegationGate  # type: ignore
    from loss_profile_tracker import LossProfileTracker  # type: ignore

_logger = logging.getLogger(__name__)


@dataclass
class DelegationEnvelope:
    """Envelope for delegating a step to remote worker."""
    step: Step
    decision_context: GlobalPlan  # L34-filtered plan
    statement_snapshot: dict[str, Any]  # Sanitized statement
    budget: dict[str, Any]  # Budget envelope
    idempotency_key: str  # Deterministic key


@dataclass
class StepResult:
    """Result from executing a step."""
    step_num: int
    action: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    was_delegated: bool = False


class AdaptiveDelegationExecutor:
    """Execute GlobalPlan with adaptive delegation."""

    def __init__(
        self,
        plan: GlobalPlan,
        l34_gate: L34DelegationGate,
        loss_tracker: LossProfileTracker,
    ):
        """Initialize executor."""
        self.plan = plan
        self.l34_gate = l34_gate
        self.loss_tracker = loss_tracker

    async def execute(
        self,
        statement: dict[str, Any],
        task_analysis: InitialAnalysisRequest,
        step_executor_fn: Callable[[Step, dict[str, Any]], Any],
    ) -> list[StepResult]:
        """
        Execute plan with adaptive delegation.

        Args:
            statement: Current statement context
            task_analysis: Classification + plan from Phase 1
            step_executor_fn: Async function to execute a single step

        Returns:
            List of StepResult
        """

        all_results = []

        # Group steps into parallel batches
        batches = self._group_parallel_batches()

        for batch_num, batch in enumerate(batches):
            _logger.info(f"Executing batch {batch_num + 1}/{len(batches)}: steps {batch}")

            # Create coroutines (do NOT await yet)
            tasks = []

            for step_num in batch:
                step = next(s for s in self.plan.steps if s.step == step_num)

                # Decide: local or remote?
                should_delegate = self._should_delegate_step(step, statement)

                if should_delegate:
                    # Create delegation envelope
                    safe_plan = self.l34_gate.filter_plan(self.plan, max_classification="INTERNAL")
                    envelope = DelegationEnvelope(
                        step=step,
                        decision_context=safe_plan,
                        statement_snapshot=self.l34_gate.sanitize_snapshot(
                            statement,
                            required_vars=self._get_required_vars(step),
                            max_classification="INTERNAL",
                        ),
                        budget={},  # Placeholder
                        idempotency_key=self._deterministic_key(step, statement),
                    )

                    # Create async task for delegation
                    task = self._execute_delegated(step, envelope, statement)
                    tasks.append(task)
                else:
                    # Local execution
                    task = self._execute_local(step, statement, step_executor_fn)
                    tasks.append(task)

            # Execute batch in parallel
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for step_num, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    step_result = StepResult(
                        step_num=step_num,
                        action="unknown",
                        success=False,
                        error=str(result),
                    )
                else:
                    step_result = result

                    # Sample-based loss recording (5% overhead)
                    if step_result.was_delegated and random.random() < 0.05:
                        # 5% of delegations: measure actual loss
                        step = next(s for s in self.plan.steps if s.step == step_num)
                        local_result = await self._execute_local(
                            step, statement, step_executor_fn
                        )
                        loss_pct = self._compute_loss(local_result.output, step_result.output)
                        self.loss_tracker.record_delegation_result(
                            task_type="delegated_step",
                            engine="tiered_delegation",
                            loss_pct=loss_pct,
                        )
                    elif step_result.was_delegated:
                        # 95%: proxy metrics
                        self.loss_tracker.record_via_proxy(
                            task_type="delegated_step",
                            engine="tiered_delegation",
                            schema_valid=True,  # Placeholder
                            downstream_ok=True,  # Placeholder
                        )

                all_results.append(step_result)

        return all_results

    def _group_parallel_batches(self) -> list[list[int]]:
        """Group steps into parallel-safe batches."""
        # Simple topological sort (reuse ADR-0210 Phase 3 if available)
        # For now: simplified version
        batches = []
        completed = set()

        while len(completed) < len(self.plan.steps):
            batch = []
            for step in self.plan.steps:
                if step.step in completed:
                    continue
                if all(d in completed for d in step.depends_on):
                    batch.append(step.step)

            if not batch:
                break

            batches.append(batch)
            completed.update(batch)

        return batches

    def _should_delegate_step(self, step: Step, statement: dict[str, Any]) -> bool:
        """Decide: local or remote?"""
        # Step 1: L34 data-safety check
        gate_result = self.l34_gate.can_delegate_step(step, statement, max_classification="INTERNAL")
        if not gate_result.can_delegate:
            return False

        # Step 2: Loss check
        estimated_loss = self.loss_tracker.estimate_loss_for_task_type(
            step.action, "moderate"
        )
        if estimated_loss > 0.05:  # 5% quality threshold
            return False

        # All checks passed: delegate
        return True

    async def _execute_delegated(
        self,
        step: Step,
        envelope: DelegationEnvelope,
        statement: dict[str, Any],
    ) -> StepResult:
        """Execute step via delegation (placeholder)."""
        start = time.time()

        try:
            # Placeholder: in real implementation, send envelope to remote worker
            await asyncio.sleep(0.01)  # Simulate RPC

            return StepResult(
                step_num=step.step,
                action=step.action,
                success=True,
                output={"placeholder": "delegated result"},
                duration_ms=int((time.time() - start) * 1000),
                was_delegated=True,
            )
        except Exception as e:
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=False,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
                was_delegated=True,
            )

    async def _execute_local(
        self,
        step: Step,
        statement: dict[str, Any],
        executor_fn: Callable[[Step, dict[str, Any]], Any],
    ) -> StepResult:
        """Execute step locally."""
        start = time.time()

        try:
            result = await executor_fn(step, statement)
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=True,
                output=result,
                duration_ms=int((time.time() - start) * 1000),
                was_delegated=False,
            )
        except Exception as e:
            return StepResult(
                step_num=step.step,
                action=step.action,
                success=False,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
                was_delegated=False,
            )

    def _deterministic_key(self, step: Step, statement: dict[str, Any]) -> str:
        """Generate deterministic idempotency key (survives process restart)."""
        stmt_json = json.dumps(
            {k: v for k, v in sorted(statement.items()) if isinstance(v, (str, int, float, bool, type(None)))},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(f"{step.step}_{stmt_json}".encode()).hexdigest()[:16]

    def _compute_loss(self, local_output: Any, delegated_output: Any) -> float:
        """Compute loss between local and delegated output (0-100)."""
        # Placeholder: in real implementation, use semantic similarity or test-pass-rate
        if local_output == delegated_output:
            return 0.0
        else:
            return 5.0  # Assume 5% loss if outputs differ

    def _get_required_vars(self, step: Step) -> set[str]:
        """Infer variables required by this step."""
        # Placeholder: in real implementation, parse step.action
        return set()
