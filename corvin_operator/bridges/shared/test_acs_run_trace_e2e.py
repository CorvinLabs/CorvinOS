"""ACS run trace: the real manager loop and gate chain produce steps and a loss.

Boundary under test: ``ACSRuntime.run()`` → ``_manager_loop`` → ``_dispatch_workers`` →
``ACSGateChain.evaluate`` → result. Only the three engine calls are replaced (manager
decision, worker output, LLM critique); everything the trace depends on is the real
code. The audit writer is captured so the live chain is never touched.
"""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from . import acs_runtime as _rt
    from . import acs_gate_chain as _gc
except ImportError:
    import acs_runtime as _rt  # type: ignore[no-redef]
    import acs_gate_chain as _gc  # type: ignore[no-redef]

WORKER_TEXT = (
    "Die Quellen stimmen in drei von vier Punkten überein. Der Strompreis stieg zwischen "
    "2021 und 2024 in allen fünf Ländern, im Schnitt um etwa 40 Prozent, mit deutlichen "
    "Unterschieden zwischen den Quellen bei der Ursachenangabe."
)

DELEGATE = {
    "decision": "DELEGATE",
    "reasoning": "split by country",
    "subtasks": [
        {"id": "de", "instructions": "Germany prices", "expected_output": {"type": "object"},
         "success_criteria": "numbers", "priority": "normal",
         "budget_allocation": {"max_tokens": 2000, "max_tool_calls": 2}},
        {"id": "fr", "instructions": "France prices", "expected_output": {"type": "object"},
         "success_criteria": "numbers", "priority": "normal",
         "budget_allocation": {"max_tokens": 2000, "max_tool_calls": 2}},
    ],
}
COMPLETE = {
    "decision": "COMPLETE",
    "reasoning": "both countries covered",
    "complete_artifacts": {"summary": WORKER_TEXT, "output_paths": []},
}


_MISSING = object()


@contextmanager
def _module_overrides(**mods):
    """Set only the named sys.modules entries and restore just those afterwards.

    ``patch.dict(sys.modules, ...)`` restores the WHOLE dict on exit and so drops every
    module imported inside the block (e.g. corvin_console.chat_runtime), which breaks
    later test files that rely on it.
    """
    saved = {k: sys.modules.get(k, _MISSING) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture(autouse=True)
def _no_real_model_calls(monkeypatch):
    """Hermetic by assertion: the run's own L44 gate would start a real `claude` haiku call (6 per
    run, found by an independent review), so the classifier is pinned, and any attempt to spawn the
    CLI or open a network connection anyway fails the test."""
    import house_rules  # type: ignore
    from acs_test_guards import forbid_real_cli_and_network
    monkeypatch.setattr(house_rules, "_house_rules_classifier",
                        lambda task, rules, auth, **_kw: ("", 1.0, "test-benign"))
    forbid_real_cli_and_network(monkeypatch)


def _spec():
    return {
        "awp": "1.0.0",
        "workflow": {"name": "trace-test", "description": "Compare electricity prices",
                     "version": "1.0.0"},
        "orchestration": {"engine": "delegation_loop", "delegation_loop": {}},
        "state": {"initial": {"task": "Compare electricity prices"}},
    }


def test_run_emits_decision_worker_and_gate_steps_with_a_measured_loss(tmp_path):
    manager_answers = iter([json.dumps(DELEGATE), json.dumps(COMPLETE)])
    captured_audit: list[tuple] = []
    observed: list[dict] = []

    def fake_manager(*a, **k):
        return next(manager_answers), 50

    def fake_worker(*a, **k):
        return WORKER_TEXT, 30, None

    with patch.object(_rt, "_call_manager_sync", side_effect=fake_manager), \
         patch.object(_rt, "_call_worker_sync", side_effect=fake_worker), \
         patch.object(_gc, "_run_critique_sync", return_value=None), \
         patch.object(_rt, "_assert_engine_licensed", return_value=None), \
         patch.object(_rt, "_write_audit", side_effect=lambda *a, **k: captured_audit.append(a)), \
         patch.object(_rt, "_corvin_home", return_value=tmp_path):
        runtime = _rt.ACSRuntime(tenant_id="_default", bridge="test", chat="t",
                                 on_step=observed.append)
        result = asyncio.run(runtime.run(_spec(), run_id="trace-e2e"))

    kinds = [s["kind"] for s in result.steps]
    # Iteration 1 delegates to two workers, iteration 2 completes: decision, two worker
    # lines and a gate line for the completion.
    assert kinds[0] == "decision"
    assert result.steps[0]["decision"] == "DELEGATE"
    assert result.steps[0]["n_subtasks"] == 2
    assert [s["kind"] for s in result.steps if s["kind"] == "worker"] == ["worker", "worker"]
    assert any(s["kind"] == "gate" for s in result.steps)
    # The observer saw exactly the same steps, in the same order.
    assert observed == result.steps

    # The loss is the gate chain's own measurement, not an invented number.
    assert result.loss_history, "a completed gate step must record a loss"
    gate = next(s for s in result.steps if s["kind"] == "gate")
    assert gate["loss"] == pytest.approx(result.loss_history[-1])
    assert isinstance(result.loss_history[-1], float)
    # Two manager decision rounds (DELEGATE, then COMPLETE). ``result.iterations`` counts
    # only the DELEGATE round, so the summary must read the trace, not that counter.
    assert len([s for s in result.steps if s["kind"] == "decision"]) == 2
    assert result.iterations == 1


def test_bridge_streams_each_step_as_a_status_line_and_appends_the_summary():
    """Bridge call site: _maybe_delegate_worker → _run_acs_delegation → real run.

    The live status emitter receives one line per step during the run, and the
    delegated answer ends with the run summary (trigger, outcome, steps, loss).
    """
    import sys as _sys
    from unittest.mock import patch as _patch
    import types as _types
    import corvin_console.feature_flags as _ff
    try:
        import license.session_refresh as _sr  # type: ignore
        _prev, _sr._TEST_MODE_SNAPSHOT = _sr._TEST_MODE_SNAPSHOT, "1"   # no licence-server connection
    except Exception:  # noqa: BLE001
        _sr = _prev = None
    try:
        import adapter as _adapter  # type: ignore[no-redef]
    finally:
        if _sr is not None:
            _sr._TEST_MODE_SNAPSHOT = _prev

    manager_answers = iter([json.dumps(DELEGATE), json.dumps(COMPLETE)])
    lines: list[str] = []
    quota = _types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    prompt = ("Recherchiere parallel mit mehreren Workern, wie sich die Strompreise in "
              "fünf Ländern entwickelt haben, und vergleiche die Quellen im Detail.")
    with _patch.object(_ff, "is_enabled",
                       side_effect=lambda fid, tid="_default": fid == "bridge_worker_engine_parity"), \
         _patch.object(_ff, "worker_engine_mode", return_value="acs"), \
         _patch.object(_adapter, "_check_compliance_or_fail", return_value=None), \
         _patch.object(_adapter, "_check_egress_or_fail", return_value=None), \
         _patch.object(_rt, "_call_manager_sync", side_effect=lambda *a, **k: (next(manager_answers), 50)), \
         _patch.object(_rt, "_call_worker_sync", side_effect=lambda *a, **k: (WORKER_TEXT, 30, None)), \
         _patch.object(_gc, "_run_critique_sync", return_value=None), \
         _patch.object(_rt, "_assert_engine_licensed", return_value=None), \
         _patch.object(_rt, "_write_audit", side_effect=lambda *a, **k: None), \
         _module_overrides(**{"license.compute_quota": quota, "acs_runtime": _rt}):
        answer, _ = _adapter._maybe_delegate_worker(
            prompt, channel="discord", chat_key="chat-1", persona="",
            on_status=lambda text, tool_name=None: lines.append(text),
        )

    assert answer is not None
    step_lines = [ln for ln in lines if ln.startswith(("🧭", "🤖", "📊"))]
    assert step_lines[0].startswith("🧭 Iteration 1: Manager teilt in 2 Teilaufgaben")
    assert sum(ln.startswith("🤖 Worker") for ln in step_lines) == 2
    assert any(ln.startswith("📊 Gate") for ln in step_lines)
    assert answer.startswith(WORKER_TEXT[:40])
    assert "ACS-Lauf" in answer and "- Loss-Score (höher = besser): " in answer


def _run_with_manager_answers(tmp_path, answers):
    it = iter(answers)
    with patch.object(_rt, "_call_manager_sync", side_effect=lambda *a, **k: (next(it), 50)), \
         patch.object(_rt, "_call_worker_sync", side_effect=lambda *a, **k: (WORKER_TEXT, 30, None)), \
         patch.object(_gc, "_run_critique_sync", return_value=None), \
         patch.object(_rt, "_assert_engine_licensed", return_value=None), \
         patch.object(_rt, "_write_audit", side_effect=lambda *a, **k: None), \
         patch.object(_rt, "_corvin_home", return_value=tmp_path):
        return asyncio.run(_rt.ACSRuntime(tenant_id="_default", bridge="t", chat="t").run(_spec(), run_id="r"))


def test_an_unreadable_manager_round_is_a_step_and_counts_as_an_iteration(tmp_path):
    """Four manager calls were shown as 2 iterations and numbered from 3 (review finding)."""
    res = _run_with_manager_answers(tmp_path, ["this is not json", "still not json",
                                               json.dumps(DELEGATE), json.dumps(COMPLETE)])
    decisions = [st for st in res.steps if st["kind"] == "decision"]
    assert [d["decision"] for d in decisions] == ["INVALID", "INVALID", "DELEGATE", "COMPLETE"]
    assert [d["iteration"] for d in decisions] == [1, 2, 3, 4]
    # workers_spawned is counted inside the real worker call, which this test replaces; the trace has them.
    assert sum(1 for st in res.steps if st["kind"] == "worker") == 2
    assert "- Schritte: 4 Iteration(en)" in _rt.format_acs_run_summary(res, trigger="x", german=True)
    assert "Antwort des Managers unbrauchbar" in _rt.format_acs_step(decisions[0], german=True)


def test_a_delegate_with_a_malformed_plan_shows_no_invented_subtask_count(tmp_path):
    bad = json.dumps({"decision": "DELEGATE", "reasoning": "x", "subtasks": {"a": 1, "b": 2, "c": 3}})
    res = _run_with_manager_answers(tmp_path, [bad, json.dumps(DELEGATE), json.dumps(COMPLETE)])
    first = res.steps[0]
    assert first["decision"] == "DELEGATE" and first["n_subtasks"] == 0
    assert "Plan des Managers unbrauchbar" in _rt.format_acs_step(first, german=True)


def test_a_manager_call_that_raises_is_counted_as_an_iteration(tmp_path):
    """Two manager calls were reported as '1 iteration(s)': the call that raised emitted no step."""
    calls = {"n": 0}

    def manager(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps(DELEGATE), 50
        raise RuntimeError("engine down")

    with patch.object(_rt, "_call_manager_sync", side_effect=manager), \
         patch.object(_rt, "_call_worker_sync", side_effect=lambda *a, **k: (WORKER_TEXT, 30, None)), \
         patch.object(_rt, "_assert_engine_licensed", return_value=None), \
         patch.object(_rt, "_write_audit", side_effect=lambda *a, **k: None), \
         patch.object(_rt, "_corvin_home", return_value=tmp_path):
        res = asyncio.run(_rt.ACSRuntime(tenant_id="_default", bridge="t", chat="t").run(_spec(), run_id="r2"))
    assert res.status == "failed" and calls["n"] == 2
    assert [s["decision"] for s in res.steps if s["kind"] == "decision"] == ["DELEGATE", "INVALID"]
    assert "- Schritte: 2 Iteration(en)" in _rt.format_acs_run_summary(res, trigger="x", german=True)


def test_every_result_path_carries_a_duration(tmp_path):
    """The unexpected-error path built its ACSResult without elapsed_s, so a run of seconds or hours
    was summarised as 'Dauer: 0 s'."""
    import time

    async def exploding_loop(ctx, *a, **k):
        await asyncio.sleep(0.06)
        raise ValueError("unexpected")

    with patch.object(_rt, "_manager_loop", side_effect=exploding_loop), \
         patch.object(_rt, "_assert_engine_licensed", return_value=None), \
         patch.object(_rt, "_write_audit", side_effect=lambda *a, **k: None), \
         patch.object(_rt, "_corvin_home", return_value=tmp_path):
        res = asyncio.run(_rt.ACSRuntime(tenant_id="_default", bridge="t", chat="t").run(_spec(), run_id="r3"))
    assert res.status == "failed" and "unexpected" in res.error
    assert res.elapsed_s >= 0.05, res.elapsed_s


@pytest.mark.parametrize("score,expected", [(0.2, 0.2), (1.0, 1.0), (0.0, 0.0),
                                            (2, 0.5), (7, 0.5), (10, 0.5), (-0.1, 0.5),
                                            ("x", 0.5), (True, 1.0), (float("nan"), 0.5)])
def test_a_critique_score_outside_zero_to_one_is_no_signal_not_a_perfect_score(score, expected):
    """The prompt asks for 0..1. Clamping turned a 2-out-of-10 into 1.0 (loss total 0.99)."""
    gates = [_gc.GateResult("gate_1_deliverable", True, 1.0),
             _gc.GateResult("gate_2_l0_contract", True, 1.0),
             _gc.GateResult("gate_3_llm_critique", True, 0.5, "", {"critique": {"score": score}})]
    loss = _gc._compute_workflow_loss(gates, {"confidence": 0.9}, "x" * 50, None, _gc.LossProfile(), 1, None)
    assert loss.L_quality == pytest.approx(expected)
