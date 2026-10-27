"""T-0319 / ADR-2306 decision 3: the planner (InitialAnalysis) call reports its measured tokens.

Before this change ``analysis_runner._run_lm_call`` ran with ``--output-format text``, so the planner's tokens were
absent from every TDE cost figure and a comparison of TDE against a direct turn undercounted TDE. These tests pin:

  * the planner command asks for the JSON envelope and the usage lands on ``analysis.planner_usage``
  * a plain-text reply (older CLI, test double) still works and leaves the usage ``None``, never zero
  * an ``is_error`` envelope is a failure, never parsed as an analysis
  * a stale usage from an earlier call is never attributed to a later call that reported none
  * the engine entry point puts the block in ``result["summary"]["planner_usage"]``, separate from the step totals

No LLM calls (the subprocess boundary is replaced). Live counterpart:
tests/test_tde_e2e_live.py::test_live_planner_reports_usage.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "corvin_operator" / "orchestration"))
sys.path.insert(0, str(ROOT / "corvin_operator" / "bridges" / "shared"))

from initial_analysis import (  # noqa: E402
    Classification, Entities, GlobalPlan, InitialAnalysisRequest, Step,
)
from tde import analysis_runner, worker_ipc  # noqa: E402
from tde.tde_engine import TieredDelegationEngine  # noqa: E402
from tde.worker_ipc import LocalResult  # noqa: E402


def _analysis():
    plan = GlobalPlan(steps=[Step(step=1, action="analyze_data")], estimated_duration_s=5, estimated_tokens=1000)
    return InitialAnalysisRequest(classification=Classification("analysis", "simple", "claude", 0.9),
                                  entities=Entities(), global_plan=plan)


def _envelope(result="{}", **over):
    env = {"type": "result", "subtype": "success", "is_error": False, "result": result, "total_cost_usd": 0.0011,
           "usage": {"input_tokens": 3, "output_tokens": 40, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 60}}
    env.update(over)
    return json.dumps(env)


@pytest.fixture()
def planner(monkeypatch):
    """Replace the subprocess boundary; the test chooses what the CLI printed. Returns the captured argv list."""
    import helper_model

    monkeypatch.setattr(helper_model, "resolve_claude_bin", lambda: "claude")
    monkeypatch.setattr(analysis_runner, "parse_task_analysis_response", lambda raw: _analysis())
    seen: list = []
    reply = {"stdout": _envelope()}

    def fake_run_one_shot(cmd, timeout_s, cwd=None, proc_holder=None):
        seen.append(list(cmd))
        return 0, reply["stdout"], ""

    monkeypatch.setattr(worker_ipc, "run_one_shot", fake_run_one_shot)
    seen_reply = type("R", (), {"set": staticmethod(lambda s: reply.__setitem__("stdout", s)), "cmds": seen})
    return seen_reply


def test_planner_asks_for_the_json_envelope_and_reports_four_components(planner):
    analysis = analysis_runner.run_initial_analysis_sync("fictional task", {})
    cmd = planner.cmds[0]
    assert cmd[cmd.index("--output-format") + 1] == "json"
    u = analysis.planner_usage
    assert (u["input_tokens"], u["output_tokens"], u["cache_read_input_tokens"], u["cache_creation_input_tokens"]) == (3, 40, 900, 60)
    assert u["cost_usd"] == 0.0011
    # the CLI envelope carries no model field: the planner attributes its own configured model (found live, 2026-10-27)
    assert isinstance(u["model"], str) and u["model"].startswith("claude-")


def test_plain_text_reply_still_works_and_leaves_usage_none(planner):
    planner.set('{"classification": "x"}')  # an older CLI / test double: not an envelope
    analysis = analysis_runner.run_initial_analysis_sync("fictional task", {})
    assert analysis.planner_usage is None


def test_error_envelope_is_a_failure_not_an_analysis(planner):
    planner.set(_envelope(result="", is_error=True, subtype="error_max_turns"))
    with pytest.raises(analysis_runner.AnalysisUnavailable, match="error_max_turns"):
        analysis_runner.run_initial_analysis_sync("fictional task", {})


def test_a_stale_usage_is_never_attributed_to_a_later_call(planner, monkeypatch):
    first = analysis_runner.run_initial_analysis_sync("fictional task", {})
    assert first.planner_usage is not None
    monkeypatch.setattr(analysis_runner, "_run_lm_call", lambda prompt, timeout_s, proc_holder=None: "{}")  # reports nothing
    second = analysis_runner.run_initial_analysis_sync("fictional task", {})
    assert second.planner_usage is None


class _UsageIPC:
    async def send_delegation(self, envelope, *, proc_holder=None):
        return {"success": True, "output": {"echo": 1}, "error": None,
                "usage": {"input_tokens": 2, "output_tokens": 3, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 0,
                          "total_tokens": 15, "cost_usd": 0.0002, "model": "claude-haiku-5-5"}}


def _run_engine(analysis, key):
    async def local_exec(step, statement, *, proc_holder=None):
        return LocalResult(output={"echo": step.step})

    engine = TieredDelegationEngine(local_step_executor=local_exec, real_ipc=False)
    return asyncio.run(engine.execute(analysis, {"statement": {"data": "synthetic"}}, worker_ipc=_UsageIPC(),
                                      use_semantic_judge=False, session_key=key, budget_tokens=500_000))


def test_engine_summary_carries_planner_usage_separate_from_step_totals():
    analysis = _analysis()
    analysis.planner_usage = {"input_tokens": 3, "output_tokens": 40, "cache_read_input_tokens": 900,
                              "cache_creation_input_tokens": 60, "cost_usd": 0.0011, "model": "claude-haiku-5-5"}
    summary = _run_engine(analysis, "t0319-planner-a")["summary"]
    assert summary["planner_usage"]["total_tokens"] == 1003
    assert summary["total_tokens"] == 15  # step totals are untouched: the planner is NOT folded in


def test_engine_summary_planner_usage_is_none_when_the_analysis_reported_none():
    assert _run_engine(_analysis(), "t0319-planner-b")["summary"]["planner_usage"] is None
