"""Tests for TDE Phase 2: AdaptiveDelegationExecutor + WorkerIPC + engines.

No LLM calls — MockWorkerIPC and mock step executors only.
Live E2E (real LM) lives in tests/test_tde_e2e_live.py.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.adaptive_delegation_executor import (
    AdaptiveDelegationExecutor,
    BudgetEnvelope,
    DELEGATION_OVERHEAD_TOKENS,
)
from tde.l34_delegation_gate import L34DelegationGate
from tde.loss_profile_tracker import LossProfileTracker
from tde.worker_ipc import MockWorkerIPC, parse_worker_output
from tde.tde_engine import ClaudeCodeLocalEngine, TieredDelegationEngine
from initial_analysis import (
    Classification, Entities, GlobalPlan, InitialAnalysisRequest, Step,
)
from parallel_executor import ExecutionError


def _plan(steps):
    return GlobalPlan(steps=steps, estimated_duration_s=10, estimated_tokens=5000)


def _analysis(plan, task_type="code_generation", complexity="moderate"):
    return InitialAnalysisRequest(
        classification=Classification(task_type, complexity, "claude", 0.9),
        entities=Entities(),
        global_plan=plan,
    )


async def _echo_executor(step, statement):
    return {"echo": step.step}


class TestDelegationDecision:
    """Three-gate decision logic."""

    def _executor(self, plan=None, *, ipc=None, budget=None, tracker=None):
        plan = plan or _plan([Step(step=1, action="analyze_data")])
        return AdaptiveDelegationExecutor(
            plan, L34DelegationGate(), tracker or LossProfileTracker(),
            worker_ipc=ipc, budget=budget,
        )

    def test_no_ipc_means_local(self):
        ex = self._executor(ipc=None)
        delegate, reason, _ = ex._should_delegate_step(ex.plan.steps[0], {})
        assert delegate is False
        assert reason == "no_worker_ipc"

    def test_l34_blocks_delegation(self):
        ex = self._executor(ipc=MockWorkerIPC())
        delegate, reason, _ = ex._should_delegate_step(
            ex.plan.steps[0], {"password": "hunter2"}
        )
        assert delegate is False
        assert reason == "l34_blocked"

    def test_budget_blocks_delegation(self):
        budget = BudgetEnvelope(max_tokens=DELEGATION_OVERHEAD_TOKENS - 1)
        ex = self._executor(ipc=MockWorkerIPC(), budget=budget)
        delegate, reason, _ = ex._should_delegate_step(ex.plan.steps[0], {})
        assert delegate is False
        assert reason == "budget_exhausted"

    def test_exploration_for_side_effect_free(self):
        ex = self._executor(ipc=MockWorkerIPC())
        delegate, reason, force = ex._should_delegate_step(ex.plan.steps[0], {})
        assert delegate is True
        assert reason == "exploration"
        assert force is True  # exploration forces measurement

    def test_mutating_step_stays_local_without_evidence(self):
        plan = _plan([Step(step=1, action="write_file")])
        ex = self._executor(plan, ipc=MockWorkerIPC())
        delegate, reason, _ = ex._should_delegate_step(plan.steps[0], {})
        assert delegate is False
        assert reason == "no_evidence_mutating_step"

    def test_learned_high_loss_blocks(self):
        tracker = LossProfileTracker()
        for _ in range(6):
            tracker.record_delegation_result(
                task_type="analyze_data", engine="tiered_delegation",
                loss_pct=20.0, measured=True,
            )
        ex = self._executor(ipc=MockWorkerIPC(), tracker=tracker)
        delegate, reason, _ = ex._should_delegate_step(ex.plan.steps[0], {})
        assert delegate is False
        assert reason == "loss_above_threshold"

    def test_learned_low_loss_delegates(self):
        tracker = LossProfileTracker()
        for _ in range(6):
            tracker.record_delegation_result(
                task_type="analyze_data", engine="tiered_delegation",
                loss_pct=1.0, measured=True,
            )
        ex = self._executor(ipc=MockWorkerIPC(), tracker=tracker)
        delegate, reason, _ = ex._should_delegate_step(ex.plan.steps[0], {})
        assert delegate is True
        assert reason == "gates_passed"

    def test_other_engine_evidence_does_not_count(self):
        """claude_code outcomes must not unlock TDE delegation."""
        tracker = LossProfileTracker()
        for _ in range(6):
            tracker.record_delegation_result(
                task_type="analyze_data", engine="claude_code",
                loss_pct=1.0, measured=True,
            )
        ex = self._executor(ipc=MockWorkerIPC(), tracker=tracker)
        delegate, reason, force = ex._should_delegate_step(ex.plan.steps[0], {})
        # No TDE evidence → exploration path (not gates_passed)
        assert reason == "exploration"
        assert force is True


class TestExecutorPlanValidation:
    def test_invalid_dependency_raises(self):
        plan = _plan([Step(step=1, action="a", depends_on=[7])])
        with pytest.raises(ExecutionError):
            AdaptiveDelegationExecutor(plan, L34DelegationGate(), LossProfileTracker())

    def test_forward_dependency_raises(self):
        plan = _plan([
            Step(step=1, action="a", depends_on=[2]),
            Step(step=2, action="b"),
        ])
        with pytest.raises(ExecutionError):
            AdaptiveDelegationExecutor(plan, L34DelegationGate(), LossProfileTracker())

    def test_symmetric_hints_batch_together(self):
        plan = _plan([
            Step(step=1, action="a", can_parallelize=[2]),
            Step(step=2, action="b", can_parallelize=[1]),
            Step(step=3, action="c", depends_on=[1, 2]),
        ])
        ex = AdaptiveDelegationExecutor(plan, L34DelegationGate(), LossProfileTracker())
        batches = ex._group_parallel_batches()
        assert batches[0] == [1, 2]
        assert batches[-1] == [3]

    def test_asymmetric_hints_do_not_batch(self):
        """Only SYMMETRIC can_parallelize opt-in batches steps together."""
        plan = _plan([
            Step(step=1, action="a", can_parallelize=[2]),
            Step(step=2, action="b", can_parallelize=[]),  # does NOT list 1
        ])
        ex = AdaptiveDelegationExecutor(plan, L34DelegationGate(), LossProfileTracker())
        batches = ex._group_parallel_batches()
        assert [1] in batches and [2] in batches


class TestExecutionPaths:
    @pytest.mark.asyncio
    async def test_delegated_execution_uses_ipc(self):
        ipc = MockWorkerIPC()
        plan = _plan([Step(step=1, action="analyze_data")])
        ex = AdaptiveDelegationExecutor(
            plan, L34DelegationGate(), LossProfileTracker(), worker_ipc=ipc,
        )
        results = await ex.execute({}, None, _echo_executor)
        assert len(results) == 1
        assert results[0].was_delegated is True
        assert len(ipc.sent_envelopes) == 1
        env = ipc.sent_envelopes[0]
        assert env.idempotency_key  # deterministic key present

    @pytest.mark.asyncio
    async def test_envelope_snapshot_is_sanitized(self):
        ipc = MockWorkerIPC()
        plan = _plan([Step(step=1, action="analyze_data")])
        ex = AdaptiveDelegationExecutor(
            plan, L34DelegationGate(), LossProfileTracker(), worker_ipc=ipc,
        )
        # 'notes' is PUBLIC by name but contains an email → CONFIDENTIAL content
        await ex.execute(
            {"notes": "contact john@example.com", "code": "x = 1"},
            None, _echo_executor,
        )
        # L34 gate blocks the whole step (fail-closed) — no envelope sent
        assert len(ipc.sent_envelopes) == 0

    @pytest.mark.asyncio
    async def test_budget_charged(self):
        budget = BudgetEnvelope(max_tokens=100_000)
        plan = _plan([Step(step=1, action="analyze_data", estimated_tokens=1000)])
        ex = AdaptiveDelegationExecutor(
            plan, L34DelegationGate(), LossProfileTracker(),
            worker_ipc=MockWorkerIPC(), budget=budget,
        )
        await ex.execute({}, None, _echo_executor)
        assert budget.spent_tokens >= 1000


class TestShadowRunLocalFailure:
    """Round-4 finding: a failing LOCAL shadow-comparison run must not be
    scored as a high-loss MEASURED sample against the delegated output —
    the two runs were never actually compared, so no loss claim should be
    recorded (fall back to proxy instead)."""

    @pytest.mark.asyncio
    async def test_failed_local_shadow_falls_back_to_proxy(self):
        tracker = LossProfileTracker()
        ipc = MockWorkerIPC()  # delegated call always succeeds
        # analyze_data is side-effect-free + no evidence yet -> exploration
        # path -> force_measure=True -> shadow run is attempted.
        plan = _plan([Step(step=1, action="analyze_data")])
        ex = AdaptiveDelegationExecutor(
            plan, L34DelegationGate(), tracker, worker_ipc=ipc,
        )

        async def _always_failing_local(step, statement):
            raise RuntimeError("transient local worker failure")

        results = await ex.execute({}, None, _always_failing_local)

        assert results[0].was_delegated is True
        assert results[0].success is True  # the DELEGATED call succeeded

        # Exactly one loss-tracker entry, and it must be a proxy record
        # (measured=False) — never a measured=True entry derived from
        # comparing against a failed local run.
        assert len(tracker.history) == 1
        entry = tracker.history[0]
        assert entry.measured is False, (
            "a failed local shadow-comparison run must record via proxy, "
            "not as a measured (full-weight) loss sample"
        )
        # Proxy loss for a successful delegated step must be the low
        # "schema_valid" default (1.0%), not the corrupted lexical-distance
        # score a None-vs-output comparison would have produced pre-fix.
        assert entry.loss_pct == 1.0


class TestWorkerOutputParsing:
    def test_plain_json(self):
        assert parse_worker_output('{"output": "hi"}') == "hi"

    def test_fenced_json(self):
        assert parse_worker_output('```json\n{"output": "hi"}\n```') == "hi"

    def test_json_after_preamble(self):
        assert parse_worker_output('Sure!\n{"output": 42}') == 42

    def test_fallback_raw(self):
        assert parse_worker_output("no json here") == "no json here"


class TestEngines:
    @pytest.mark.asyncio
    async def test_claude_code_engine_sequential(self):
        async def exec_fn(step, statement):
            return f"done-{step.step}"

        engine = ClaudeCodeLocalEngine(local_step_executor=exec_fn)
        plan = _plan([Step(step=1, action="a"), Step(step=2, action="b", depends_on=[1])])
        result = await engine.execute(_analysis(plan), {"statement": {}})
        assert result["success"] is True
        assert result["engine"] == "claude_code"
        assert [r.output for r in result["results"]] == ["done-1", "done-2"]

    @pytest.mark.asyncio
    async def test_tde_engine_with_mock_ipc(self):
        async def exec_fn(step, statement):
            return f"local-{step.step}"

        engine = TieredDelegationEngine(local_step_executor=exec_fn)
        plan = _plan([
            Step(step=1, action="analyze_data", can_parallelize=[2]),
            Step(step=2, action="analyze_data", can_parallelize=[1]),
        ])
        result = await engine.execute(
            _analysis(plan), {"statement": {"data": "clean"}},
            worker_ipc=MockWorkerIPC(),
        )
        assert result["success"] is True
        assert result["summary"]["delegated"] == 2  # exploration path delegates

    @pytest.mark.asyncio
    async def test_tde_engine_rejects_invalid_plan(self):
        engine = TieredDelegationEngine()
        result = await engine.execute({}, {})
        assert result["success"] is False
        assert "error" in result


class TestF1RealCounterfactual:
    """ADR-0222 F1: the SHADOW REFERENCE must run on the reference executor
    (a stronger model) when one is provided — not re-run the worker's own
    cheap executor. Without this the loss measures cheap-vs-cheap and is
    structurally blind to the real quality drop."""

    def _executor(self, *, tracker=None):
        plan = _plan([Step(step=1, action="analyze_data")])
        return AdaptiveDelegationExecutor(
            plan, L34DelegationGate(), tracker or LossProfileTracker(),
            worker_ipc=MockWorkerIPC(),
            use_semantic_judge=False,  # lexical fallback → no real claude subprocess
        )

    @pytest.mark.asyncio
    async def test_shadow_uses_reference_executor_when_set(self):
        from tde.adaptive_delegation_executor import StepResult

        called = {"worker": 0, "reference": 0}

        async def worker_exec(step, statement, *, proc_holder=None):
            called["worker"] += 1
            return {"output": "worker answer"}

        async def reference_exec(step, statement, *, proc_holder=None):
            called["reference"] += 1
            return {"output": "reference answer"}

        ex = self._executor()
        ex._reference_executor_fn = reference_exec
        step = ex.plan.steps[0]
        delegated = StepResult(
            step_num=1, action="analyze_data", success=True,
            output={"output": "worker answer"}, was_delegated=True,
            token_usage={"model": "claude-haiku-4-5"},
        )
        # force_measure=True guarantees the shadow fires deterministically.
        await ex._record_outcome(step, {"x": 1}, delegated, worker_exec,
                                 force_measure=True)
        # The shadow REFERENCE ran the strong executor, NOT the worker executor.
        assert called["reference"] == 1
        assert called["worker"] == 0

    @pytest.mark.asyncio
    async def test_shadow_falls_back_to_worker_executor_when_no_reference(self):
        from tde.adaptive_delegation_executor import StepResult

        called = {"worker": 0}

        async def worker_exec(step, statement, *, proc_holder=None):
            called["worker"] += 1
            return {"output": "worker answer"}

        ex = self._executor()  # no reference set → legacy behaviour
        step = ex.plan.steps[0]
        delegated = StepResult(
            step_num=1, action="analyze_data", success=True,
            output={"output": "worker answer"}, was_delegated=True,
            token_usage={"model": "claude-haiku-4-5"},
        )
        await ex._record_outcome(step, {"x": 1}, delegated, worker_exec,
                                 force_measure=True)
        assert called["worker"] == 1  # the shadow re-ran the worker executor

    @pytest.mark.asyncio
    async def test_execute_accepts_and_stores_reference_executor(self):
        async def ref(step, statement, *, proc_holder=None):
            return {"output": "ref"}

        ex = self._executor()
        # execute() with an empty plan of one local step + reference_executor_fn
        # must accept the kwarg and stash it for the shadow path.
        await ex.execute(
            {"data": "clean"}, None, _echo_executor, reference_executor_fn=ref,
        )
        assert ex._reference_executor_fn is ref
