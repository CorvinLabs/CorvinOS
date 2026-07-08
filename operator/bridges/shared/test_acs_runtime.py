"""Tests for acs_runtime.py — ADR-0104 M2/M3/M4."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from . import acs_runtime as _rt
except ImportError:
    import acs_runtime as _rt  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_spec() -> dict:
    return {
        "awp": "1.0.0",
        "workflow": {
            "name": "test-acs-workflow",
            "description": "A test ACS workflow",
            "version": "1.0.0",
        },
        "orchestration": {
            "engine": "delegation_loop",
            "delegation_loop": {
                "budget": {
                    "max_loops": 5,
                    "max_depth": 2,
                    "max_total_workers": 10,
                    "max_wall_time": 300,
                }
            },
        },
        "state": {"initial": {"topic": "test topic"}},
    }


# ---------------------------------------------------------------------------
# Worker subprocess cancellation — adversarial review HIGH finding
# ---------------------------------------------------------------------------
# asyncio.to_thread() does not interrupt a blocking subprocess.run() already
# running in the executor thread when the awaiting Task is cancelled -- the
# claude -p child process kept running (burning CPU/tokens/API cost) for up
# to _WORKER_TIMEOUT more seconds after the ACS run had already returned.
# _WorkerProcessHolder + _call_worker_sync(proc_holder=...) close this.

def test_worker_process_holder_kill_terminates_real_process():
    import subprocess as _sp
    holder = _rt._WorkerProcessHolder()
    proc = _sp.Popen(["sleep", "30"])
    holder.popen = proc
    assert proc.poll() is None  # still running
    holder.kill()
    proc.wait(timeout=5)
    assert proc.poll() is not None  # actually terminated, not left running


def test_worker_process_holder_kill_is_noop_when_no_process():
    holder = _rt._WorkerProcessHolder()
    holder.kill()  # must not raise when nothing was ever assigned


def test_worker_process_holder_kill_is_noop_on_already_finished_process():
    import subprocess as _sp
    holder = _rt._WorkerProcessHolder()
    proc = _sp.Popen(["true"])
    proc.wait()
    holder.popen = proc
    holder.kill()  # must not raise / attempt to kill an already-reaped process


def test_call_worker_sync_populates_proc_holder():
    """The live Popen handle must be exposed via proc_holder BEFORE the
    (potentially long) communicate() call blocks -- that's the whole point:
    the caller needs it available to kill from outside this thread."""
    holder = _rt._WorkerProcessHolder()
    with (
        patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", "test-model")),
        patch.object(_rt, "_assert_engine_licensed", return_value=None),
        patch.object(_rt, "_claude_binary", return_value="echo"),
        patch.object(_rt, "_apply_provider_redirect", return_value=None),
        patch.object(_rt.shutil, "which", return_value="/bin/echo"),
    ):
        _rt._call_worker_sync(
            "prompt", "system", "test-model", {}, proc_holder=holder,
        )
    # The process has finished (echo exits immediately) but the holder must
    # have been populated with it while it was running.
    assert holder.popen is not None


def test_call_manager_sync_populates_proc_holder():
    """Adversarial review finding: _call_manager_sync used plain
    subprocess.run() with no way for a cancelling caller to kill the actual
    process — the same bug class already fixed for _call_worker_sync. This
    proves the manager call site now exposes a live Popen handle the same way."""
    holder = _rt._WorkerProcessHolder()
    with (
        patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", "test-model")),
        patch.object(_rt, "_assert_engine_licensed", return_value=None),
        patch.object(_rt, "_claude_binary", return_value="echo"),
        patch.object(_rt, "_apply_provider_redirect", return_value=None),
        patch.object(_rt.shutil, "which", return_value="/bin/echo"),
    ):
        _rt._call_manager_sync("prompt", "test-model", proc_holder=holder)
    assert holder.popen is not None


def test_call_manager_sync_still_works_without_a_proc_holder():
    # proc_holder is optional (backward compatible with any caller that
    # doesn't need cancellation-kill support).
    with (
        patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", "test-model")),
        patch.object(_rt, "_assert_engine_licensed", return_value=None),
        patch.object(_rt, "_claude_binary", return_value="echo"),
        patch.object(_rt, "_apply_provider_redirect", return_value=None),
        patch.object(_rt.shutil, "which", return_value="/bin/echo"),
    ):
        out, tok = _rt._call_manager_sync("prompt", "test-model")
    assert isinstance(out, str)
    assert tok > 0


# ---------------------------------------------------------------------------
# BudgetEnvelope tests
# ---------------------------------------------------------------------------

def test_budget_no_breach():
    b = _rt.BudgetEnvelope(max_loops=10, max_total_tokens=1000)
    b.loops_used = 5
    b.tokens_used = 500
    assert b.check() is None


def test_budget_loops_breach():
    b = _rt.BudgetEnvelope(max_loops=5)
    b.loops_used = 5
    breach = b.check()
    assert breach is not None
    assert "max_loops" in breach


def test_budget_tokens_breach():
    b = _rt.BudgetEnvelope(max_total_tokens=1000)
    b.tokens_used = 1001
    breach = b.check()
    assert breach is not None
    assert "max_total_tokens" in breach


def test_budget_tool_calls_breach_fires_once_incremented():
    # Adversarial review finding: tool_calls_used was NEVER incremented
    # anywhere in the file, so a configured max_tool_calls could never
    # fire — this is a basic sanity check that the mechanism itself works
    # once something actually increments the counter (now done in
    # _run_one from the post-run trace-extraction tool-call count).
    b = _rt.BudgetEnvelope(max_tool_calls=5)
    assert b.check() is None
    b.tool_calls_used = 5
    breach = b.check()
    assert breach is not None
    assert "max_tool_calls" in breach


def test_budget_fraction():
    b = _rt.BudgetEnvelope(max_loops=100, max_total_tokens=10000, max_depth=4)
    child = b.fraction(0.5)
    assert child.max_loops == 50
    assert child.max_total_tokens == 5000
    assert child.max_depth == 3  # depth decremented


def test_budget_fraction_depth_floor():
    b = _rt.BudgetEnvelope(max_depth=0)
    child = b.fraction(0.5)
    assert child.max_depth == 0  # floor at 0


# ---------------------------------------------------------------------------
# Manager prompt building
# ---------------------------------------------------------------------------

def test_build_manager_prompt_contains_workflow_id():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="test-run",
            workflow_id="test-acs-workflow",
            workflow_spec=spec,
            budget=budget,
            run_dir=Path(td),
            state={"topic": "climate risk"},
        )
        prompt = _rt._build_manager_prompt(ctx)
        assert "test-acs-workflow" in prompt
        assert "climate risk" in prompt
        assert "ITERATION" in prompt
        assert "BUDGET REMAINING" in prompt


def test_build_manager_prompt_with_worker_results():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="test-run",
            workflow_id="test-acs-workflow",
            workflow_spec=spec,
            budget=budget,
            run_dir=Path(td),
            iteration=1,
        )
        ctx.worker_results.append(_rt.WorkerResult(
            worker_id="worker_1",
            status="success",
            result={"analysis": "done"},
            confidence=0.9,
        ))
        prompt = _rt._build_manager_prompt(ctx)
        assert "worker_1" in prompt
        assert "0.90" in prompt


# ---------------------------------------------------------------------------
# _is_valid_subtask_list — adversarial review finding: a hallucinated
# manager decision returning "subtasks" as a non-list (e.g. a JSON object)
# passed the old bare `if not subtasks:` check (a non-empty dict is
# truthy), then crashed the WHOLE run with AttributeError/TypeError deeper
# in enumerate()/list-slicing, instead of just retrying the iteration.
# ---------------------------------------------------------------------------

def test_is_valid_subtask_list_accepts_a_real_list_of_dicts():
    assert _rt._is_valid_subtask_list([{"id": "t1"}, {"id": "t2"}]) is True


def test_is_valid_subtask_list_accepts_empty_list():
    assert _rt._is_valid_subtask_list([]) is True


def test_is_valid_subtask_list_rejects_a_dict():
    assert _rt._is_valid_subtask_list({"id": "t1"}) is False


def test_is_valid_subtask_list_rejects_a_list_of_strings():
    assert _rt._is_valid_subtask_list(["t1", "t2"]) is False


def test_is_valid_subtask_list_rejects_a_string():
    assert _rt._is_valid_subtask_list("not-a-list") is False


def test_is_valid_subtask_list_rejects_none():
    assert _rt._is_valid_subtask_list(None) is False


# ---------------------------------------------------------------------------
# Manager decision parsing
# ---------------------------------------------------------------------------

def test_parse_delegate_decision():
    json_text = json.dumps({
        "decision": "DELEGATE",
        "reasoning": "Need to research first",
        "subtasks": [
            {"id": "task_1", "instructions": "research topic", "expected_output": {}}
        ],
    })
    d = _rt._parse_manager_decision(json_text)
    assert d is not None
    assert d["decision"] == "DELEGATE"
    assert len(d["subtasks"]) == 1


def test_parse_complete_decision():
    json_text = json.dumps({
        "decision": "COMPLETE",
        "reasoning": "Task is done",
        "complete_artifacts": {
            "summary": "All done",
            "output_paths": ["report.md"],
            "quality_score": 0.95,
        },
    })
    d = _rt._parse_manager_decision(json_text)
    assert d is not None
    assert d["decision"] == "COMPLETE"


def test_parse_fail_decision():
    json_text = json.dumps({
        "decision": "FAIL",
        "reasoning": "Cannot proceed",
        "fail_reason": "insufficient data",
    })
    d = _rt._parse_manager_decision(json_text)
    assert d is not None
    assert d["decision"] == "FAIL"


def test_parse_decision_from_noisy_output():
    text = (
        "I'll return my decision now.\n\n"
        '{"decision": "DELEGATE", "reasoning": "need work", "subtasks": []}\n\n'
        "That's my answer."
    )
    d = _rt._parse_manager_decision(text)
    assert d is not None
    assert d["decision"] == "DELEGATE"


def test_parse_decision_invalid_json():
    d = _rt._parse_manager_decision("This is not JSON at all.")
    assert d is None


# ---------------------------------------------------------------------------
# Worker output parsing
# ---------------------------------------------------------------------------

def test_parse_worker_success():
    json_text = json.dumps({
        "status": "success",
        "result": {"findings": ["result A", "result B"]},
        "confidence": 0.92,
        "usage": {"llm_tokens": 500, "tool_calls": 3},
    })
    wr = _rt._parse_worker_output(json_text, "worker_1")
    assert wr.status == "success"
    assert wr.confidence == pytest.approx(0.92)
    assert wr.worker_id == "worker_1"
    assert wr.usage["llm_tokens"] == 500


def test_parse_worker_failed():
    json_text = json.dumps({
        "status": "failed",
        "result": {},
        "confidence": 0.0,
        "error": "tool_not_found",
    })
    wr = _rt._parse_worker_output(json_text, "worker_2")
    assert wr.status == "failed"
    assert wr.error == "tool_not_found"


def test_parse_worker_no_json():
    wr = _rt._parse_worker_output("I'm sorry, I couldn't complete this task.", "worker_3")
    assert wr.status == "failed"
    assert "worker_3" == wr.worker_id
    assert wr.error


# ---------------------------------------------------------------------------
# ACSRuntime dry-run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acs_dry_run_valid_workflow():
    rt = _rt.ACSRuntime(tenant_id="_test")
    with patch.object(_rt, "_run_dir") as mock_rd, \
         patch.object(_rt, "_write_audit"):
        with tempfile.TemporaryDirectory() as td:
            mock_rd.return_value = Path(td) / "runs" / "test-run"
            result = await rt.run(
                _minimal_spec(),
                dry_run=True,
                run_id="test-run-dry",
            )
    assert result.status == "success"
    assert "dry_run" in result.summary


@pytest.mark.asyncio
async def test_acs_dry_run_invalid_workflow():
    rt = _rt.ACSRuntime(tenant_id="_test")
    bad_spec = {"awp": "invalid", "workflow": {"name": "BadName"}}
    result = await rt.run(bad_spec, dry_run=True)
    assert result.status == "failed"
    assert "validation failed" in result.error


@pytest.mark.asyncio
async def test_acs_file_not_found():
    rt = _rt.ACSRuntime(tenant_id="_test")
    result = await rt.run("/nonexistent/workflow.awp.yaml")
    assert result.status == "failed"
    assert "not found" in result.error


# ---------------------------------------------------------------------------
# Budget from spec
# ---------------------------------------------------------------------------

def test_budget_from_spec_defaults():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    assert budget.max_loops == 5
    assert budget.max_depth == 2
    assert budget.max_total_workers == 10
    assert budget.max_wall_time == 300


def test_budget_from_spec_empty():
    spec = {"orchestration": {}}
    budget = _rt._budget_from_spec(spec)
    assert budget.max_loops == 100
    assert budget.max_depth == 4


def test_clamp_positive_cap_rejects_zero_and_negative():
    # BudgetEnvelope.check() treats max_loops/max_total_workers <= 0 as
    # "unbounded" (its own `> 0` guard) -- 0 or negative must fall back to
    # the default, not silently disable enforcement (adversarial review).
    assert _rt._clamp_positive_cap(0, default=100, ceiling=5000) == 100
    assert _rt._clamp_positive_cap(-1, default=100, ceiling=5000) == 100


def test_clamp_positive_cap_clamps_to_ceiling():
    assert _rt._clamp_positive_cap(999_999, default=100, ceiling=5000) == 5000


def test_clamp_positive_cap_passes_through_valid_value():
    assert _rt._clamp_positive_cap(42, default=100, ceiling=5000) == 42


def test_budget_from_spec_clamps_zero_max_loops_and_workers():
    spec = {
        "orchestration": {"delegation_loop": {"budget": {
            "max_loops": 0, "max_total_workers": -5,
        }}}
    }
    budget = _rt._budget_from_spec(spec)
    assert budget.max_loops == 100      # fell back to default, not 0
    # Falls back to the SSOT delegation default (8), NOT the old inflated 500 —
    # aligned with chat_runtime / settings.py / validator R35 (ceiling 64).
    assert budget.max_total_workers == 8  # fell back to default, not -5


def test_budget_from_spec_clamps_max_workers_per_iteration_ceiling():
    # Adversarial review finding: unlike max_loops/max_total_workers, this
    # field had NO clamp at all — a workflow YAML (or budget_override, which
    # merges into this same spec dict before _budget_from_spec runs) could
    # set it to an arbitrary integer, fanning out that many CONCURRENT
    # worker subprocesses in a single iteration at any recursion depth.
    spec = {"orchestration": {"delegation_loop": {"budget": {
        "max_workers_per_iteration": 999_999,
    }}}}
    budget = _rt._budget_from_spec(spec)
    assert budget.max_workers_per_iteration == 100


def test_budget_from_spec_max_workers_per_iteration_zero_falls_back_to_default():
    spec = {"orchestration": {"delegation_loop": {"budget": {
        "max_workers_per_iteration": 0,
    }}}}
    budget = _rt._budget_from_spec(spec)
    assert budget.max_workers_per_iteration == 6


# ---------------------------------------------------------------------------
# RunContext.root_budget — adversarial review CRITICAL fix
# ---------------------------------------------------------------------------
# BudgetEnvelope.check() was called ONLY at the top-level manager loop
# against the ROOT budget. Recursive delegation gives each sub-tree its OWN
# independent budget via .fraction(), whose workers_used/tokens_used
# counters were incremented but never checked against anything, so the real
# aggregate usage across the whole recursion tree could vastly exceed
# max_total_workers/max_total_tokens. root_budget threads the SAME
# BudgetEnvelope object through every level so the true aggregate can be
# checked and incremented at any depth.

def test_root_budget_defaults_to_self_when_unset():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="w1", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
        )
        assert ctx.root_budget is ctx.budget


def test_root_budget_survives_dataclasses_replace_with_a_new_fractioned_budget():
    import dataclasses as _dc

    spec = _minimal_spec()
    root_budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="w1", workflow_spec=spec,
            budget=root_budget, run_dir=Path(td),
        )
        sub_ctx = _dc.replace(ctx, budget=ctx.budget.fraction(0.5))
        # The sub-context's LOCAL budget is a new, smaller object...
        assert sub_ctx.budget is not root_budget
        # ...but root_budget still points at the ORIGINAL root object, at
        # any recursion depth — this is what makes global-ceiling
        # enforcement possible at all.
        assert sub_ctx.root_budget is root_budget

        # A second level of recursion must still resolve to the same root.
        sub_sub_ctx = _dc.replace(sub_ctx, budget=sub_ctx.budget.fraction(0.5))
        assert sub_sub_ctx.root_budget is root_budget
        assert sub_sub_ctx.budget is not sub_ctx.budget


@pytest.mark.asyncio
async def test_dispatch_workers_refuses_when_root_budget_already_breached():
    """The core fix: even though the LOCAL (fractioned) branch budget has
    plenty of room, _dispatch_workers must refuse to spawn anything once the
    GLOBAL root ceiling has been breached by usage anywhere else in the tree."""
    spec = _minimal_spec()
    root_budget = _rt._budget_from_spec(spec)
    root_budget.max_total_workers = 5
    root_budget.workers_used = 5  # already at the global ceiling

    local_budget = root_budget.fraction(1.0)  # a fresh, unbroken LOCAL budget
    assert local_budget.check() is None, "sanity: the local branch budget itself has room"

    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="w1", workflow_spec=spec,
            budget=local_budget, run_dir=Path(td), root_budget=root_budget,
        )
        results = await _rt._dispatch_workers(
            [{"id": "t1", "instructions": "x"}], ctx, depth=1,
            manager_model="m", worker_model="w",
        )
    assert results == []


@pytest.mark.asyncio
async def test_dispatch_workers_proceeds_when_root_budget_has_room():
    """Sanity counterpart: dispatch must not be blocked when the global
    ceiling has NOT been breached (verifies the check isn't just always-deny)."""
    spec = _minimal_spec()
    root_budget = _rt._budget_from_spec(spec)
    root_budget.max_total_workers = 500
    root_budget.workers_used = 0

    local_budget = root_budget.fraction(1.0)

    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="w1", workflow_spec=spec,
            budget=local_budget, run_dir=Path(td), root_budget=root_budget,
        )
        with patch.object(_rt, "_write_audit") as mock_audit:
            results = await _rt._dispatch_workers(
                [], ctx, depth=1, manager_model="m", worker_model="w",
            )
    assert results == []
    # Distinguishes this from the breach short-circuit: no breach audit
    # event fired, proving the early-return path was not taken merely
    # because subtasks happened to be empty.
    breach_calls = [c for c in mock_audit.call_args_list if c.args[1] == "acs.budget_breach"]
    assert breach_calls == []


# ---------------------------------------------------------------------------
# budget_override — adversarial review CRITICAL fix
# ---------------------------------------------------------------------------
# Previously applied via blind setattr(budget, k, ...) AFTER
# validate_workflow_dict() had already run, so it (a) never got R31/R32's
# max_depth ceiling enforcement and (b) had no field allow-list, letting a
# caller overwrite internal accounting state (start_time, loops_used, ...)
# via the same HTTP field. Now merged into the spec's own budget dict BEFORE
# validation, restricted to the legitimate cap-field allow-list.

@pytest.mark.asyncio
async def test_budget_override_max_depth_beyond_ceiling_fails_validation():
    """The exact regression this session fixed once already (max_depth=200
    default) must not be re-openable via a single budget_override call."""
    rt = _rt.ACSRuntime(tenant_id="_test")
    result = await rt.run(
        _minimal_spec(),
        dry_run=True,
        budget_override={"max_depth": 999},
    )
    assert result.status == "failed"
    assert "validation failed" in result.error


@pytest.mark.asyncio
async def test_budget_override_max_depth_within_ceiling_succeeds():
    rt = _rt.ACSRuntime(tenant_id="_test")
    with patch.object(_rt, "_run_dir") as mock_rd, \
         patch.object(_rt, "_write_audit"):
        with tempfile.TemporaryDirectory() as td:
            mock_rd.return_value = Path(td) / "runs" / "test-run"
            result = await rt.run(
                _minimal_spec(),
                dry_run=True,
                budget_override={"max_depth": 8},
                run_id="test-run-override-ok",
            )
    assert result.status == "success"


@pytest.mark.asyncio
async def test_budget_override_ignores_internal_accounting_fields():
    """start_time/loops_used/etc must never reach the merged spec -- only
    the 8 legitimate cap fields in _BUDGET_OVERRIDE_ALLOWED_FIELDS may."""
    rt = _rt.ACSRuntime(tenant_id="_test")
    spec = _minimal_spec()
    with patch.object(_rt, "_run_dir") as mock_rd, \
         patch.object(_rt, "_write_audit"):
        with tempfile.TemporaryDirectory() as td:
            mock_rd.return_value = Path(td) / "runs" / "test-run"
            await rt.run(
                spec,
                dry_run=True,
                budget_override={
                    "max_depth": 3,           # allowed -- must be applied
                    "start_time": 99999999999,  # NOT allowed -- must be dropped
                    "loops_used": 5,            # NOT allowed -- must be dropped
                },
                run_id="test-run-override-filter",
            )
    merged_budget = spec["orchestration"]["delegation_loop"]["budget"]
    assert merged_budget["max_depth"] == 3
    assert "start_time" not in merged_budget
    assert "loops_used" not in merged_budget


# ---------------------------------------------------------------------------
# L34 gate
# ---------------------------------------------------------------------------
# The former `_rt._l34_gate(...)` helper was removed in ADR-0158 M2 — acs_runtime
# now routes L34 through the shared `spawn_gates.check_l34` SSOT (called inline
# with classification=<level>). The behaviour the removed unit tests covered is
# now verified at the SSOT and its integration sites, so re-testing it here would
# only duplicate setup:
#   * DataFlowGuard matrix (PUBLIC/INTERNAL/CONFIDENTIAL/SECRET, unknown-engine
#     fail-closed, secret-egress) — test_data_classification.py
#   * adapter → check_l34 integration with tenant configs (unknown engine id,
#     SECRET blocked on cloud engine, fail-open without config) —
#     test_adapter_compliance_gate.py


# ---------------------------------------------------------------------------
# Worker prompt building
# ---------------------------------------------------------------------------

def test_build_worker_prompt():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
            state={"context": "some context"},
        )
        subtask = {
            "id": "t1",
            "instructions": "Analyse the data.",
            "expected_output": {"type": "object", "properties": {"result": {"type": "string"}}},
            "success_criteria": "Result is non-empty",
        }
        prompt = _rt._build_worker_prompt(subtask, ctx)
        assert "t1" in prompt
        assert "Analyse the data" in prompt
        assert "result" in prompt


def test_build_worker_system_no_delegate():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
        )
        system = _rt._build_worker_system({}, ctx, depth=0, can_delegate=False)
        assert "Worker Agent" in system
        assert "sub-manager" not in system


def test_build_worker_system_with_delegate():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
        )
        ctx.budget.max_depth = 4
        system = _rt._build_worker_system({}, ctx, depth=1, can_delegate=True)
        assert "sub-manager" in system


def test_build_worker_system_with_dynamic_tools():
    spec = _minimal_spec()
    budget = _rt._budget_from_spec(spec)
    with tempfile.TemporaryDirectory() as td:
        ctx = _rt.RunContext(
            run_id="r1", workflow_id="wf", workflow_spec=spec,
            budget=budget, run_dir=Path(td),
        )
        ctx.dynamic_tools["analysis.compute_stats"] = "def compute_stats(): pass"
        system = _rt._build_worker_system({}, ctx, depth=0, can_delegate=False)
        assert "analysis.compute_stats" in system
        assert "DYNAMIC TOOLS AVAILABLE" in system
