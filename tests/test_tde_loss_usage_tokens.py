"""T-0319 / ADR-2306 decision 3: the loss profile carries MEASURED tokens, in four components.

Before this change ``record_delegation_result`` accepted ``tokens_delegated`` / ``tokens_local``
but the executor never passed them, so 0 of 141 live entries carried a token count and
``estimate_cost_ratio`` could never return a number. These tests pin the call site:

  * the normaliser (``usage_block``) is content-free and never zero-fills an absent measurement
  * the executor's shadow path (``_record_outcome``) hands BOTH sides' usage to the tracker
  * the production entry ``TieredDelegationEngine.execute`` ends in measured entries with blocks
  * persisted entries round-trip, and a legacy line without the new fields still loads

No LLM calls: stub workers return the same normalised usage shape ``parse_cli_envelope`` produces.
Live counterpart (real claude CLI): tests/test_tde_e2e_live.py::test_live_loss_profile_carries_tokens.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "corvin_operator" / "orchestration"))

from initial_analysis import GlobalPlan, Step  # noqa: E402
from tde.adaptive_delegation_executor import AdaptiveDelegationExecutor, StepResult  # noqa: E402
from tde.l34_delegation_gate import L34DelegationGate  # noqa: E402
from tde.loss_profile_tracker import (  # noqa: E402
    USAGE_KEYS, LossProfileTracker, get_session_tracker, usage_block,
)
from tde.tde_engine import TieredDelegationEngine  # noqa: E402
from tde.worker_ipc import LocalResult, MockWorkerIPC  # noqa: E402


def _usage(inp=10, out=20, cr=100, cw=5, model="claude-haiku-5-5", cost=0.001):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw, "total_tokens": inp + out + cr + cw,
            "cost_usd": cost, "model": model}


def _plan(n=1, action="analyze_data"):
    return GlobalPlan(steps=[Step(step=i + 1, action=action) for i in range(n)],
                      estimated_duration_s=10, estimated_tokens=5000)


class TestUsageBlock:
    def test_four_components_total_cost_and_model(self):
        b = usage_block(_usage())
        assert set(USAGE_KEYS) <= set(b)
        assert b["total_tokens"] == 10 + 20 + 100 + 5
        assert b["cost_usd"] == 0.001 and b["model"] == "claude-haiku-5-5"

    @pytest.mark.parametrize("bad", [None, "text", 7, [], {}, {"input_tokens": 0}])
    def test_absent_or_empty_measurement_stays_absent_never_zero(self, bad):
        assert usage_block(bad) is None

    def test_bool_negative_and_garbage_are_clamped_not_trusted(self):
        b = usage_block({"input_tokens": True, "output_tokens": -5, "cache_read_input_tokens": "x",
                         "cache_creation_input_tokens": 3, "cost_usd": True, "model": 123})
        assert b["input_tokens"] == 0 and b["output_tokens"] == 0 and b["cache_read_input_tokens"] == 0
        assert b["total_tokens"] == 3
        assert b["cost_usd"] is None and b["model"] is None

    def test_block_is_content_free(self):
        b = usage_block({**_usage(), "text": "SECRET PROMPT", "result": "SECRET"})
        assert "SECRET" not in json.dumps(b)
        assert set(b) == set(USAGE_KEYS) | {"total_tokens", "cost_usd", "model"}


class TestTrackerRecording:
    def test_blocks_and_derived_totals(self):
        t = LossProfileTracker()
        t.record_delegation_result("analyze_data", "tiered_delegation", 12.0, model_id="m",
                                   usage_delegated=_usage(inp=1, out=2, cr=3, cw=4),
                                   usage_local=_usage(inp=5, out=6, cr=7, cw=8))
        e = t.history[-1]
        assert e.tokens_delegated == 10 and e.tokens_local == 26
        assert e.usage_delegated["cache_read_input_tokens"] == 3
        assert e.usage_local["cache_creation_input_tokens"] == 8

    def test_explicit_total_wins_over_derived(self):
        t = LossProfileTracker()
        t.record_delegation_result("analyze_data", "tiered_delegation", 1.0,
                                   tokens_delegated=999, usage_delegated=_usage())
        assert t.history[-1].tokens_delegated == 999

    def test_no_usage_leaves_fields_none(self):
        t = LossProfileTracker()
        t.record_delegation_result("analyze_data", "tiered_delegation", 1.0)
        e = t.history[-1]
        assert e.tokens_delegated is None and e.usage_delegated is None and e.usage_local is None

    def test_persist_roundtrip_and_legacy_line(self, tmp_path):
        p = tmp_path / "loss.jsonl"
        t = LossProfileTracker(persist_path=p)
        t.record_delegation_result("read_file", "tiered_delegation", 3.0, model_id="m",
                                   usage_delegated=_usage(), usage_local=_usage(inp=50))
        legacy = {"timestamp": t.history[-1].timestamp, "task_type": "list_files", "model_id": "m",
                  "loss_pct": 2.0, "engine": "tiered_delegation", "complexity": "moderate",
                  "measured": True, "alternative_scores": {}, "tokens_delegated": None, "tokens_local": None}
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(legacy) + "\n")
        t2 = LossProfileTracker(persist_path=p)
        by = {e.task_type: e for e in t2.history}
        assert by["read_file"].usage_delegated["total_tokens"] == 135
        assert by["read_file"].usage_local["input_tokens"] == 50
        assert by["list_files"].usage_delegated is None  # legacy line still loads


class TestExecutorCallSite:
    """The production shadow path: _record_outcome -> _execute_local -> record_delegation_result."""

    def _executor(self, tracker):
        return AdaptiveDelegationExecutor(_plan(), L34DelegationGate(), tracker,
                                          worker_ipc=MockWorkerIPC(), use_semantic_judge=False)

    @pytest.mark.asyncio
    async def test_shadow_entry_carries_both_sides_usage(self):
        tracker = LossProfileTracker()
        ex = self._executor(tracker)

        async def local_exec(step, statement, *, proc_holder=None):
            return LocalResult(output={"output": "answer"}, usage=_usage(inp=7, out=8, cr=0, cw=0, model="claude-haiku-5-5"))

        delegated = StepResult(step_num=1, action="analyze_data", success=True, output={"output": "answer"},
                               was_delegated=True, token_usage=_usage(inp=1, out=2, cr=40, cw=0))
        await ex._record_outcome(ex.plan.steps[0], {"x": 1}, delegated, local_exec, force_measure=True)
        e = tracker.history[-1]
        assert e.measured and e.usage_delegated["cache_read_input_tokens"] == 40
        assert e.usage_local["total_tokens"] == 15
        assert e.tokens_delegated == 43 and e.tokens_local == 15

    @pytest.mark.asyncio
    async def test_plain_value_local_executor_leaves_local_usage_none(self):
        tracker = LossProfileTracker()
        ex = self._executor(tracker)

        async def local_exec(step, statement, *, proc_holder=None):
            return {"output": "answer"}  # custom executor: plain-value contract, no usage

        delegated = StepResult(step_num=1, action="analyze_data", success=True, output={"output": "answer"},
                               was_delegated=True, token_usage=_usage())
        await ex._record_outcome(ex.plan.steps[0], {}, delegated, local_exec, force_measure=True)
        e = tracker.history[-1]
        assert e.usage_delegated is not None and e.usage_local is None and e.tokens_local is None


class _UsageIPC:
    """Stub WorkerIPC returning the usage shape parse_cli_envelope produces."""

    async def send_delegation(self, envelope, *, proc_holder=None):
        return {"success": True, "output": {"echo": envelope.step.step}, "error": None,
                "usage": _usage(inp=2, out=3, cr=60, cw=0, model="claude-haiku-5-5")}


class TestEngineEntryPoint:
    def test_execute_ends_in_measured_entries_with_usage_and_a_computable_cost_ratio(self):
        async def local_exec(step, statement, *, proc_holder=None):
            return LocalResult(output={"echo": step.step}, usage=_usage(inp=4, out=5, cr=0, cw=0, model="claude-haiku-5-5"))

        engine = TieredDelegationEngine(local_step_executor=local_exec, real_ipc=False)
        key = "t0319-engine-entry"
        result = asyncio.run(engine.execute(
            _plan(n=6), {"statement": {"data": "synthetic"}},
            worker_ipc=_UsageIPC(), use_semantic_judge=False, session_key=key, budget_tokens=500_000))
        assert result["success"] is True

        tracker = get_session_tracker(session_key=key)
        measured = [e for e in tracker.history if e.measured]
        assert len(measured) >= tracker.MIN_SAMPLES, "exploration must force enough measured entries"
        assert all(e.usage_delegated and e.usage_local for e in measured)
        assert all(e.usage_delegated["model"] == "claude-haiku-5-5" for e in measured)
        # The consequence the whole change exists for: the cost ratio is computable at last.
        ratio = tracker.estimate_cost_ratio("analyze_data", model_id="claude-haiku-5-5")
        assert ratio is not None and ratio > 0
