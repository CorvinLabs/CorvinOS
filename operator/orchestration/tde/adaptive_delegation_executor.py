"""ADR-0214: Adaptive Delegation Executor (Phase 2).

Real TDE execution engine:
- Parallel batch execution via ADR-0210 ParallelExecutor grouping
  (cycle detection + symmetric can_parallelize opt-in — reused, not re-implemented)
- Three-gate delegation decision: L34 (fail-closed) → budget (hard) → loss (soft)
- Real delegation through WorkerIPC (see worker_ipc.py)
- Bounded exploration: without loss history the loss gate would block forever
  (default 10% > 5% threshold); side-effect-free steps are therefore delegated
  with a FORCED shadow-run measurement until MIN_SAMPLES of real evidence exist
- Sampling-based loss measurement afterwards (5% shadow, 95% proxy),
  shadow-runs restricted to side-effect-free actions
- Hash-chained tde.* audit events (content-free)
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
    from initial_analysis import GlobalPlan, InitialAnalysisRequest, Step
    from parallel_executor import ExecutionError, ParallelExecutor
except ImportError:  # pragma: no cover - only when orchestration dir not on sys.path
    from ..initial_analysis import GlobalPlan, InitialAnalysisRequest, Step  # type: ignore
    from ..parallel_executor import ExecutionError, ParallelExecutor  # type: ignore

from . import tde_audit
from .l34_delegation_gate import L34DelegationGate
from .loss_profile_tracker import LossProfileTracker

_logger = logging.getLogger(__name__)

# Steps whose executor_fn produces only a return value (no filesystem/network
# mutation) — the only ones safe to run twice for a shadow comparison.
SIDE_EFFECT_FREE_ACTIONS = {
    "read_file", "list_files", "check_existence", "analyze_data",
    "reason_about", "evaluate", "synthesize", "generate_code",
    "generate_text", "generate_report",
}

# Estimated fixed token overhead of one delegation RPC (serialization,
# envelope, worker system prompt).
DELEGATION_OVERHEAD_TOKENS = 8000

# Soft loss gate: block delegation when learned loss exceeds this fraction.
QUALITY_THRESHOLD = 0.05

# Post-exploration shadow-run sampling rate.
SHADOW_SAMPLE_RATE = 0.05

# Cap for step outputs fed forward into the working statement for dependent
# steps (bounded so a huge step output doesn't blow up later prompts).
_STEP_OUTPUT_FEED_CAP = 8000


@dataclass
class BudgetEnvelope:
    """Hard token budget for one TDE task (reserve upfront, not per batch)."""
    max_tokens: int
    spent_tokens: int = 0

    def remaining(self) -> int:
        return max(0, self.max_tokens - self.spent_tokens)

    def charge(self, tokens: int) -> None:
        self.spent_tokens += max(0, int(tokens))


@dataclass
class DelegationEnvelope:
    """Envelope for delegating a step to a remote worker."""
    step: Step
    decision_context: GlobalPlan  # L34-filtered plan
    statement_snapshot: dict[str, Any]  # Sanitized statement
    budget: dict[str, Any]  # {"max_tokens": ..., "remaining": ...}
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
    decision_reason: str = ""


class AdaptiveDelegationExecutor:
    """Execute GlobalPlan with adaptive delegation."""

    def __init__(
        self,
        plan: GlobalPlan,
        l34_gate: L34DelegationGate,
        loss_tracker: LossProfileTracker,
        *,
        worker_ipc: Optional[Any] = None,
        budget: Optional[BudgetEnvelope] = None,
        complexity: str = "moderate",
        max_classification: str = "INTERNAL",
        use_semantic_judge: bool = True,
    ):
        """Initialize executor.

        Raises:
            ExecutionError: if the plan is malformed (invalid deps, cycles,
                non-sequential step numbers) — steps are never silently dropped.
        """
        self.plan = plan
        self.l34_gate = l34_gate
        self.loss_tracker = loss_tracker
        self.worker_ipc = worker_ipc
        self.budget = budget
        self.complexity = complexity
        self.max_classification = max_classification
        # False → skip the LLM judge in _measure_loss (unit tests, offline);
        # falls back to the discounted lexical metric.
        self.use_semantic_judge = use_semantic_judge
        # Validates the plan (raises ExecutionError) and provides the
        # symmetric-hint parallel grouping from ADR-0210 Phase 3.
        self._parallel = ParallelExecutor(plan)
        self._steps_by_num = {s.step: s for s in plan.steps}
        # Per-plan exploration counter (bounded forced measurements per action).
        self._exploration_counts: dict[str, int] = {}

    async def execute(
        self,
        statement: dict[str, Any],
        task_analysis: Optional[InitialAnalysisRequest],
        step_executor_fn: Callable[[Step, dict[str, Any]], Any],
    ) -> list[StepResult]:
        """
        Execute plan with adaptive delegation.

        Args:
            statement: Current statement context
            task_analysis: Classification + plan from Phase 1 (optional; refines
                complexity for loss bookkeeping)
            step_executor_fn: Async function to execute a single step locally

        Returns:
            List of StepResult
        """
        if task_analysis is not None:
            self.complexity = task_analysis.classification.complexity

        all_results: list[StepResult] = []
        batches = self._group_parallel_batches()
        delegated_count = 0
        # Working context: step outputs are fed forward so dependent steps
        # (depends_on) actually SEE their inputs — without this, a
        # "synthesize step 1+2" step could only hallucinate (round-2 finding).
        working_statement = dict(statement)
        failed_steps: set[int] = set()

        for batch_num, batch in enumerate(batches):
            _logger.info("Executing batch %d/%d: steps %s", batch_num + 1, len(batches), batch)

            tasks = []
            scheduled: list[int] = []
            decisions: dict[int, tuple[bool, str, bool]] = {}

            for step_num in batch:
                step = self._steps_by_num[step_num]

                # Fail-fast: a step whose dependency failed must not run
                # (mirrors ParallelExecutor's abort semantics, but records an
                # explicit result instead of dropping the step silently).
                if any(d in failed_steps for d in step.depends_on):
                    all_results.append(StepResult(
                        step_num=step_num, action=step.action, success=False,
                        error="skipped: dependency failed",
                        decision_reason="dependency_failed",
                    ))
                    failed_steps.add(step_num)
                    continue

                delegate, reason, force_measure = self._should_delegate_step(
                    step, working_statement
                )
                decisions[step_num] = (delegate, reason, force_measure)
                scheduled.append(step_num)
                tde_audit.emit(
                    "delegation_decision",
                    step_action=step.action, delegate=delegate, reason_code=reason,
                )

                # Reserve budget AT DECISION TIME — charging after the batch
                # let N parallel steps pass Gate 2 against the same remaining()
                # and overshoot the "hard" budget N-fold (round-2 finding).
                if self.budget is not None:
                    charged = step.estimated_tokens
                    if delegate:
                        charged += DELEGATION_OVERHEAD_TOKENS
                    self.budget.charge(charged)

                if delegate:
                    envelope = self._build_envelope(step, working_statement)
                    tasks.append(self._execute_delegated(step, envelope))
                else:
                    tasks.append(self._execute_local(step, working_statement, step_executor_fn))

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Snapshot of the statement AS THE BATCH SAW IT: shadow runs must
            # compare against the same inputs the delegated run received —
            # same-batch output mutations would bias the loss measurement
            # (round-3 finding).
            batch_input = dict(working_statement)

            for step_num, result in zip(scheduled, batch_results):
                step = self._steps_by_num[step_num]
                delegate, reason, force_measure = decisions[step_num]

                if isinstance(result, BaseException):
                    step_result = StepResult(
                        step_num=step_num,
                        action=step.action,
                        success=False,
                        error=str(result),
                        was_delegated=delegate,
                        decision_reason=reason,
                    )
                else:
                    step_result = result
                    step_result.decision_reason = reason

                if step_result.was_delegated:
                    delegated_count += 1
                    await self._record_outcome(
                        step, batch_input, step_result, step_executor_fn,
                        force_measure=force_measure,
                    )
                if step_result.success:
                    # Feed the output forward (bounded) for dependent steps.
                    working_statement[f"step_{step_num}_output"] = str(
                        step_result.output
                    )[:_STEP_OUTPUT_FEED_CAP]
                else:
                    failed_steps.add(step_num)
                all_results.append(step_result)

        tde_audit.emit(
            "plan_executed",
            step_count=len(self.plan.steps),
            batch_count=len(batches),
            delegated_count=delegated_count,
            local_count=len(all_results) - delegated_count,
        )
        return all_results

    # ── decision ──────────────────────────────────────────────────────────

    def _should_delegate_step(
        self, step: Step, statement: dict[str, Any]
    ) -> tuple[bool, str, bool]:
        """Three-gate decision.

        Returns:
            (delegate, reason_code, force_measurement)
        """
        # Gate 0: an IPC backend must exist at all.
        if self.worker_ipc is None:
            return False, "no_worker_ipc", False

        # Gate 1: L34 data-safety (fail-closed).
        gate_result = self.l34_gate.can_delegate_step(
            step, statement, max_classification=self.max_classification
        )
        if not gate_result.can_delegate:
            tde_audit.emit("l34_blocked", scope="step", reason_code="classification_exceeded")
            return False, "l34_blocked", False

        # Gate 2: budget (hard constraint).
        if self.budget is not None and self.budget.remaining() < (
            DELEGATION_OVERHEAD_TOKENS + step.estimated_tokens
        ):
            return False, "budget_exhausted", False

        # Gate 3: loss (soft, learned) — TDE's own delegation track record only.
        # "No evidence" must come from the evidence mass, NEVER from comparing
        # the estimate against the default value: a genuinely LEARNED loss of
        # >=10% is indistinguishable from the no-data default by value alone
        # (caught by test_learned_high_loss_blocks in this review).
        estimated_loss = self.loss_tracker.estimate_loss_for_task_type(
            step.action, self.complexity, engine="tiered_delegation"
        )
        no_evidence = (
            self.loss_tracker.evidence_for(
                step.action, self.complexity, engine="tiered_delegation"
            ) < self.loss_tracker.MIN_SAMPLES
        )

        if no_evidence:
            # Exploration: without evidence the conservative default (10%)
            # exceeds the threshold (5%) and delegation would never start.
            # Side-effect-free steps are delegated WITH forced measurement to
            # build real evidence; mutating steps stay local until evidence
            # from safe steps exists. Bounded per action within this plan:
            # a 50-step batch of identical actions must not trigger 50 forced
            # double executions when MIN_SAMPLES suffice (round-2 finding).
            if step.action in SIDE_EFFECT_FREE_ACTIONS:
                explored = self._exploration_counts.get(step.action, 0)
                if explored >= self.loss_tracker.MIN_SAMPLES:
                    return False, "exploration_budget_exhausted", False
                self._exploration_counts[step.action] = explored + 1
                return True, "exploration", True
            return False, "no_evidence_mutating_step", False

        if estimated_loss > QUALITY_THRESHOLD:
            return False, "loss_above_threshold", False

        return True, "gates_passed", False

    def _build_envelope(self, step: Step, statement: dict[str, Any]) -> DelegationEnvelope:
        safe_plan = self.l34_gate.filter_plan(self.plan, max_classification=self.max_classification)
        budget_view: dict[str, Any] = {}
        if self.budget is not None:
            budget_view = {
                "max_tokens": self.budget.max_tokens,
                "remaining": self.budget.remaining(),
            }
        return DelegationEnvelope(
            step=step,
            decision_context=safe_plan,
            statement_snapshot=self.l34_gate.sanitize_snapshot(
                statement,
                required_vars=self._get_required_vars(step, statement),
                max_classification=self.max_classification,
            ),
            budget=budget_view,
            idempotency_key=self._deterministic_key(step, statement),
        )

    # ── execution paths ───────────────────────────────────────────────────

    async def _execute_delegated(
        self,
        step: Step,
        envelope: DelegationEnvelope,
    ) -> StepResult:
        """Execute step via WorkerIPC delegation."""
        start = time.time()
        try:
            ipc_result = await self.worker_ipc.send_delegation(envelope)
            duration = int((time.time() - start) * 1000)
            success = bool(ipc_result.get("success"))
            result = StepResult(
                step_num=step.step,
                action=step.action,
                success=success,
                output=ipc_result.get("output"),
                error=ipc_result.get("error"),
                duration_ms=duration,
                was_delegated=True,
            )
        except Exception as e:
            result = StepResult(
                step_num=step.step,
                action=step.action,
                success=False,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
                was_delegated=True,
            )
        tde_audit.emit(
            "step_delegated",
            step_action=step.action, success=result.success,
            duration_ms=result.duration_ms,
            ipc=type(self.worker_ipc).__name__,
        )
        return result

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

    # ── learning ──────────────────────────────────────────────────────────

    async def _record_outcome(
        self,
        step: Step,
        statement: dict[str, Any],
        step_result: StepResult,
        step_executor_fn: Callable[[Step, dict[str, Any]], Any],
        *,
        force_measure: bool,
    ) -> None:
        """Record delegation outcome: shadow measurement or proxy."""
        can_shadow = step.action in SIDE_EFFECT_FREE_ACTIONS
        do_shadow = can_shadow and (
            force_measure or random.random() < SHADOW_SAMPLE_RATE
        )

        if do_shadow and step_result.success:
            # Shadow runs cost a full second execution — charge the budget
            # (previously unbooked, round-2 finding).
            if self.budget is not None:
                self.budget.charge(step.estimated_tokens)
            local_result = await self._execute_local(step, statement, step_executor_fn)
            loss_pct = await self._measure_loss(step, local_result.output, step_result.output)
            self.loss_tracker.record_delegation_result(
                task_type=step.action,
                engine="tiered_delegation",
                loss_pct=loss_pct,
                complexity=self.complexity,
                measured=True,
            )
            tde_audit.emit(
                "loss_recorded", task_type=step.action, engine="tiered_delegation",
                loss_pct=loss_pct, measured=True,
            )
        else:
            self.loss_tracker.record_via_proxy(
                task_type=step.action,
                engine="tiered_delegation",
                schema_valid=step_result.success,
                downstream_ok=step_result.success,
                complexity=self.complexity,
            )

    # ── helpers ───────────────────────────────────────────────────────────

    def _group_parallel_batches(self) -> list[list[int]]:
        """Group steps into parallel-safe batches (ADR-0210 grouping).

        Symmetric can_parallelize opt-in; steps without mutual hints run in
        their own sequential batch. Cycle/dependency validation happened in
        __init__ via ParallelExecutor (raises instead of dropping steps).
        """
        if self._parallel.should_parallelize():
            batches = self._parallel._group_parallel_steps()
        else:
            batches = [[s.step] for s in self.plan.steps]

        scheduled = {n for b in batches for n in b}
        missing = set(self._steps_by_num) - scheduled
        if missing:  # defense-in-depth: never silently drop steps
            raise ExecutionError(f"Unschedulable steps: {sorted(missing)}")
        return batches

    def _deterministic_key(self, step: Step, statement: dict[str, Any]) -> str:
        """Generate deterministic idempotency key (survives process restart).

        Includes step identity (action + description) and the plan shape —
        step-number + scalar-statement alone collided across different plans
        (round-2 finding).
        """
        stmt_json = json.dumps(
            {str(k): v for k, v in sorted(statement.items(), key=lambda kv: str(kv[0]))
             if isinstance(v, (str, int, float, bool, type(None)))},
            sort_keys=True,
            default=str,
        )
        plan_shape = ",".join(f"{s.step}:{s.action}" for s in self.plan.steps)
        payload = f"{step.step}|{step.action}|{step.description}|{plan_shape}|{stmt_json}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    async def _measure_loss(
        self, step: Step, local_output: Any, delegated_output: Any
    ) -> float:
        """Semantic loss (LLM judge) with lexical fallback.

        Live E2E showed lexical distance reports ~80% between equivalent LLM
        outputs — only a semantic judge yields a usable quality signal. When
        the judge is unavailable, fall back to Jaccard but CAP its influence:
        identical outputs still measure 0, and divergence is discounted
        because wording differences dominate the metric.
        """
        judged = None
        if self.use_semantic_judge:
            try:
                from .loss_judge import judge_loss_sync  # noqa: PLC0415

                judged = await asyncio.to_thread(
                    judge_loss_sync,
                    step.description or step.action,
                    local_output,
                    delegated_output,
                )
            except Exception:
                judged = None
        if judged is not None:
            return judged
        # Fallback: lexical distance, discounted (x0.25) — high wording
        # variance must not read as high semantic loss.
        return round(self._compute_loss(local_output, delegated_output) * 0.25, 2)

    def _compute_loss(self, local_output: Any, delegated_output: Any) -> float:
        """Lexical loss between local and delegated output (0-100).

        Token-set Jaccard distance over stringified outputs: 0 = identical,
        100 = fully disjoint. Weak signal — see _measure_loss for why the
        semantic judge is preferred.
        """
        try:
            a = set(str(local_output).split())
            b = set(str(delegated_output).split())
        except Exception:
            return 100.0
        if not a and not b:
            return 0.0
        union = a | b
        if not union:
            return 0.0
        jaccard = len(a & b) / len(union)
        return round((1.0 - jaccard) * 100.0, 2)

    def _get_required_vars(self, step: Step, statement: dict[str, Any]) -> set[str]:
        """Variables required by this step (conservative: all statement keys).

        Narrowing per step requires entity wiring from InitialAnalysis;
        the conservative superset is the safe default (everything is still
        L34-sanitized before leaving the process).
        """
        return set(statement.keys())
