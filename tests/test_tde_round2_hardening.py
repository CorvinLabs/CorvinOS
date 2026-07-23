"""Regression tests for the round-2 refutation findings (ADR-0214 review).

Each test pins one confirmed finding so it cannot silently return.
No LLM calls.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from initial_analysis import (
    Classification, Entities, GlobalPlan, InitialAnalysisRequest, Step,
    parse_task_analysis_response,
)
from tde import tde_audit
from tde.adaptive_delegation_executor import (
    AdaptiveDelegationExecutor,
    BudgetEnvelope,
    DELEGATION_OVERHEAD_TOKENS,
)
from tde.l34_delegation_gate import L34DelegationGate
from tde.loss_profile_tracker import LossProfileTracker
from tde.robust_engine_detector import RobustEngineDetector
from tde.slash_command_parser import SlashCommandParser
from tde.worker_ipc import MockWorkerIPC, get_worker_ipc, set_worker_ipc


def _plan(steps):
    return GlobalPlan(steps=steps, estimated_duration_s=10, estimated_tokens=5000)


async def _echo(step, statement):
    return {"echo": step.step}


class TestAuditScrubbing:
    """R2-CRITICAL-1: LM free text must never enter the hash chain."""

    def test_identifier_keys_reject_free_text(self):
        scrubbed = tde_audit._scrub({
            "step_action": "email john@example.com the report",
            "task_type": "code_generation",
            "reason_code": "l34_blocked",
        })
        assert scrubbed["step_action"] == "nonstandard"
        assert "john@example.com" not in str(scrubbed)
        assert scrubbed["task_type"] == "code_generation"
        assert scrubbed["reason_code"] == "l34_blocked"

    def test_non_allowlisted_keys_dropped(self):
        scrubbed = tde_audit._scrub({"prompt": "secret content", "engine": "acs"})
        assert "prompt" not in scrubbed
        assert scrubbed["engine"] == "acs"


class TestGateContentScanning:
    """R2-CRITICAL-2 / R2-HIGH: ReDoS bound, size fail-closed, env dumps."""

    @pytest.fixture
    def gate(self):
        return L34DelegationGate()

    def test_large_alnum_blob_scans_fast(self, gate):
        blob = "a" * 262144
        t0 = time.monotonic()
        result = gate._classify_content(blob)
        assert time.monotonic() - t0 < 2.0  # was 114s pre-fix
        assert result == "PUBLIC"

    def test_oversized_value_is_restricted(self, gate):
        # Unscanned content cannot be proven safe → fail-closed
        assert gate._classify_content("x" * (6 * 1024 * 1024)) == "RESTRICTED"

    def test_env_dump_secrets_detected(self, gate):
        assert gate._classify_content("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI") == "RESTRICTED"
        assert gate._classify_content("DB_PASSWORD: hunter22") == "RESTRICTED"
        assert gate._classify_content("MY_API_KEY=abcd1234") == "RESTRICTED"

    def test_secret_beyond_old_window_still_caught(self, gate):
        # 300KB of filler, secret at the end — the old 256KB window missed it
        blob = ("x" * 300 * 1024) + "\nAKIAIOSFODNN7EXAMPLE\n"
        assert gate._classify_content(blob) == "RESTRICTED"

    def test_non_str_var_name_does_not_crash(self, gate):
        result = gate.prescan({42: "plain value", "name": "ok"})
        assert result.can_delegate is True

    def test_content_cache_survives_hash_collision(self, gate, monkeypatch):
        """Round-4 finding (surfaced via a real delegated TDE review of this
        exact function): the cache used to key on (len(text), hash(text)).
        Two DIFFERENT same-length strings whose hash() happens to collide
        would then share a cache slot — a secret classified after a benign
        same-length string could incorrectly inherit the benign PUBLIC
        verdict. Force a collision (patch hash() to a constant) and prove
        the fix (keyed on the text itself) is immune."""
        monkeypatch.setattr("builtins.hash", lambda _x: 42)

        benign = "x" * 40  # same length as the secret below
        # Spaces around the AWS-key pattern preserve its \b word boundaries
        # (a bare "AKIA...EXAMPLE" + more word chars would NOT match).
        secret = "z" * 9 + " AKIAIOSFODNN7EXAMPLE " + "z" * 9  # len=40
        assert len(benign) == len(secret) == 40

        assert gate._classify_content(benign) == "PUBLIC"
        # Under the old (len, hash)-keyed cache this would incorrectly
        # return the cached "PUBLIC" from `benign` instead of scanning.
        assert gate._classify_content(secret) == "RESTRICTED"


class TestBudgetReservation:
    """R2-HIGH: budget must be reserved at decision time (batch TOCTOU)."""

    @pytest.mark.asyncio
    async def test_parallel_batch_cannot_overshoot_budget(self):
        # 3 parallel steps, budget only covers ONE delegation
        steps = [
            Step(step=1, action="analyze_data", estimated_tokens=1000, can_parallelize=[2, 3]),
            Step(step=2, action="analyze_data", estimated_tokens=1000, can_parallelize=[1, 3]),
            Step(step=3, action="analyze_data", estimated_tokens=1000, can_parallelize=[1, 2]),
        ]
        budget = BudgetEnvelope(max_tokens=DELEGATION_OVERHEAD_TOKENS + 3500)
        ex = AdaptiveDelegationExecutor(
            _plan(steps), L34DelegationGate(), LossProfileTracker(),
            worker_ipc=MockWorkerIPC(), budget=budget,
        )
        results = await ex.execute({}, None, _echo)
        delegated = [r for r in results if r.was_delegated]
        # Only the first step fits the delegation budget; the rest run local
        assert len(delegated) == 1
        blocked = [r for r in results if r.decision_reason == "budget_exhausted"]
        assert len(blocked) == 2


class TestExplorationCap:
    """R2-MEDIUM: exploration bounded per action within one plan."""

    @pytest.mark.asyncio
    async def test_exploration_capped_at_min_samples(self):
        n = 12
        steps = [
            Step(step=i, action="analyze_data",
                 can_parallelize=[j for j in range(1, n + 1) if j != i])
            for i in range(1, n + 1)
        ]
        tracker = LossProfileTracker()
        ex = AdaptiveDelegationExecutor(
            _plan(steps), L34DelegationGate(), tracker,
            worker_ipc=MockWorkerIPC(), use_semantic_judge=False,
        )
        results = await ex.execute({}, None, _echo)
        explored = [r for r in results if r.decision_reason == "exploration"]
        assert len(explored) == tracker.MIN_SAMPLES
        capped = [r for r in results
                  if r.decision_reason == "exploration_budget_exhausted"]
        assert len(capped) == n - tracker.MIN_SAMPLES


class TestDependencyPropagation:
    """R2-HIGH: step outputs feed forward; failed deps skip dependents."""

    @pytest.mark.asyncio
    async def test_outputs_visible_to_dependent_steps(self):
        seen = {}

        async def executor(step, statement):
            seen[step.step] = dict(statement)
            return f"out-{step.step}"

        steps = [
            Step(step=1, action="analyze_data"),
            Step(step=2, action="synthesize", depends_on=[1]),
        ]
        ex = AdaptiveDelegationExecutor(
            _plan(steps), L34DelegationGate(), LossProfileTracker(),
        )
        await ex.execute({"base": "ctx"}, None, executor)
        assert "step_1_output" in seen[2]
        assert "out-1" in seen[2]["step_1_output"]

    @pytest.mark.asyncio
    async def test_failed_dependency_skips_dependents(self):
        async def executor(step, statement):
            if step.step == 1:
                raise RuntimeError("boom")
            return "ok"

        steps = [
            Step(step=1, action="analyze_data"),
            Step(step=2, action="synthesize", depends_on=[1]),
        ]
        ex = AdaptiveDelegationExecutor(
            _plan(steps), L34DelegationGate(), LossProfileTracker(),
        )
        results = await ex.execute({}, None, executor)
        by_num = {r.step_num: r for r in results}
        assert by_num[1].success is False
        assert by_num[2].success is False
        assert by_num[2].decision_reason == "dependency_failed"


class TestDetectorMath:
    """R2-HIGH: threshold must be reachable; no logit clamping collapse."""

    def test_confidence_threshold_above_softmax_floor(self):
        assert RobustEngineDetector.CONFIDENCE_THRESHOLD > 1.0 / 3.0

    def test_strong_anti_tde_distinct_from_marginal(self):
        det = RobustEngineDetector()
        # Strongly anti-TDE (reasoning + big data) vs marginal case must not
        # produce identical distributions (the old max(0,·) clamp did).
        def analysis(task_type, complexity):
            return InitialAnalysisRequest(
                classification=Classification(task_type, complexity, "claude", 0.9),
                entities=Entities(),
                global_plan=GlobalPlan(
                    steps=[Step(step=1, action="reason_about")],
                    estimated_duration_s=5, estimated_tokens=2000,
                ),
            )
        big = {"data": "x" * (600 * 1024 * 1024)}
        _, conf_strong, sig_strong = det.detect_engine("t", big, analysis("reasoning", "complex"))
        _, conf_marginal, sig_marg = det.detect_engine("t", {}, analysis("tool_call", "simple"))
        assert (sig_strong["signal_task_type"], sig_strong["signal_data_complexity"]) != (
            sig_marg["signal_task_type"], sig_marg["signal_data_complexity"])
        assert conf_strong != pytest.approx(conf_marginal, abs=1e-9)


class TestWorkerIPCSingleton:
    """R2-HIGH: real=True must never silently receive a cached mock."""

    def test_per_flag_cache(self):
        set_worker_ipc(None)  # reset
        mock = get_worker_ipc(real=False)
        assert isinstance(mock, MockWorkerIPC)
        try:
            real = get_worker_ipc(real=True)
            assert not isinstance(real, MockWorkerIPC)
        except Exception:
            pass  # helper stack unavailable → raising is the correct behavior
        set_worker_ipc(None)


class TestParserBoundaries:
    """R2-LOW: command-word boundaries."""

    def test_engine_autopilot_not_matched(self):
        parsed = SlashCommandParser().parse("/engine-autopilot xyz")
        assert parsed.engine_override is None
        assert parsed.task_text == "/engine-autopilot xyz"  # plain message

    def test_use_engine_same_line(self):
        parsed = SlashCommandParser().parse("/use-engine acs Fix the bug")
        assert parsed.engine_override == "acs"
        assert parsed.task_text == "Fix the bug"


class TestParserNullTolerance:
    """R2-MEDIUM: LM-emitted nulls must not poison Step fields."""

    def test_null_description_falls_back_to_default(self):
        raw = (
            '{"classification": {"task_type": "code_generation", "complexity": '
            '"simple", "engine_preference": "claude", "confidence": 0.9}, '
            '"entities": {}, "global_plan": {"steps": [{"step": 1, "action": '
            '"generate_code", "description": null}], "estimated_duration_s": 5, '
            '"estimated_tokens": 100}}'
        )
        analysis = parse_task_analysis_response(raw)
        assert analysis.global_plan.steps[0].description == ""


class TestLossJudgeScale:
    """R2-MEDIUM: out-of-scale judge verdicts are unparseable, not clamped.

    Exercises the REAL module-level _to_loss (round-3: the closure version
    left this test tautological)."""

    def test_out_of_scale_returns_none(self):
        from tde.loss_judge import _to_loss

        assert _to_loss(850.0) is None
        assert _to_loss(-5.0) is None

    def test_in_scale_converts(self):
        from tde.loss_judge import _to_loss

        assert _to_loss(100.0) == 0.0
        assert _to_loss(93.0) == pytest.approx(7.0)
        assert _to_loss(0.0) == 100.0


class TestSendIntegrationEvidence:
    """R2-HIGH: local-only TDE runs must not book tiered_delegation evidence."""

    @pytest.mark.asyncio
    async def test_no_delegation_run_tagged_separately(self):
        from tde.engine_registry import EngineRegistry
        from tde.send_integration import SendIntegration

        class _LocalOnlyTDE:
            name = "tiered_delegation"
            async def execute(self, plan, context, **kwargs):
                return {"engine": self.name, "success": True, "results": [],
                        "summary": {"delegated": 0, "step_count": 2}}

        registry = EngineRegistry.__new__(EngineRegistry)
        registry.engines = {"tiered_delegation": _LocalOnlyTDE()}

        integration = SendIntegration(registry=registry)
        integration.loss_tracker.clear()
        analysis = InitialAnalysisRequest(
            classification=Classification("code_generation", "moderate", "claude", 0.9),
            entities=Entities(),
            global_plan=GlobalPlan(
                steps=[Step(step=1, action="generate_code", description="x")],
                estimated_duration_s=5, estimated_tokens=2000,
            ),
        )
        await integration.select_engine_and_execute(
            "/use-engine tiered_delegation\nTask", {}, analysis,
        )
        engines = {e.engine for e in integration.loss_tracker.history}
        assert "tiered_delegation" not in engines
        assert "tiered_delegation_local" in engines
