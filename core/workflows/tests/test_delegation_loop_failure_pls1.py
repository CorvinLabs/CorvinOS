"""Review 2026-11-19, PLS-1: a delegation_loop that ends WITHOUT a result is a node failure.

Until this fix a manager FAIL, an unknown ``decision`` and an answer that is no decision
object were returned as a successful node output (``terminal.state == "failed"`` inside a
``status == "success"`` node): the run ended ``complete`` and downstream nodes consumed a
result that does not exist. A loop that only ran out of budget stays a success but is now
named by ``RunResult.partial_nodes``.

Every test drives the real ``DAGRunner`` through ``validate`` with the scripted engine of the
adversarial F0 suite - no model, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "core" / "workflows")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_workflows import DAGRunner, validate  # noqa: E402
from corvin_workflows.node_types import DelegationLoopFailed  # noqa: E402

from .test_dag_adversarial_f0 import ScriptedEngine, agent, loop_node, make_doc  # noqa: E402


def _event_type(record: dict) -> str:
    return str(record.get("event"))


def _run(nodes, script):
    doc = make_doc(nodes)
    validate(doc)
    eng = ScriptedEngine(script=script)
    records: list[dict] = []
    runner = DAGRunner(doc, engine=eng, tenant_id="_default", audit_sink=records.append)
    res = runner.run(inputs={})
    return res, eng, [(_event_type(r), r) for r in records]


def _manager_says(decision):
    """Script: the loop's manager answers ``decision``; every other call is a plain agent."""
    def script(call):
        if call.metadata.get("role") == "manager":
            return decision
        return {"ok": True}
    return script


BAD_DECISIONS = [
    pytest.param({"decision": "FAIL", "reason": "the data source is empty"}, "reported FAIL", id="FAIL"),
    pytest.param({"decision": "RETRY_LATER"}, "unknown decision", id="unknown-decision"),
    pytest.param({"result": {"score": 1}}, "unknown decision", id="no-decision-key"),
    pytest.param({}, "unknown decision", id="empty-object"),
    pytest.param("I could not do that, sorry.", "not a decision object", id="prose"),
    pytest.param(None, "not a decision object", id="null"),
    pytest.param([{"decision": "COMPLETE"}], "not a decision object", id="list"),
]


@pytest.mark.parametrize("decision,needle", BAD_DECISIONS)
def test_a_loop_that_ends_without_a_result_fails_the_run(decision, needle):
    """Default error strategy (abort): the run is ``failed`` and the downstream node never runs."""
    res, eng, events = _run([loop_node(), agent("after", deps=["d"])], _manager_says(decision))

    assert res.state == "failed", res.state
    assert res.nodes["d"].status == "failed"
    assert res.nodes["d"].output == {}, "a failed loop hands nothing downstream"
    assert needle in (res.nodes["d"].error or ""), res.nodes["d"].error
    assert "DelegationLoopFailed" in (res.error or "")
    assert "after" not in res.nodes
    assert [c.agent for c in eng.history if c.metadata.get("role") != "manager"] == [], \
        "no worker and no downstream agent was spawned"
    failed = [kw for et, kw in events if et == "node.failed"]
    assert len(failed) == 1 and failed[0]["node_id"] == "d"


def test_a_manager_fail_is_final_and_never_retried():
    """A retry would re-run the whole loop and its workers for a verdict already given."""
    node = loop_node()
    node["retry"] = {"max_retries": 3, "interval_s": 0}
    res, eng, events = _run([node], _manager_says({"decision": "FAIL", "reason": "no"}))

    assert res.state == "failed"
    assert res.nodes["d"].attempts == 1
    assert len([c for c in eng.history if c.metadata.get("role") == "manager"]) == 1
    assert len([1 for et, _ in events if et == "node.attempt_failed"]) == 1


def test_a_manager_fail_after_workers_ran_keeps_the_count_and_still_fails():
    calls = {"n": 0}

    def script(call):
        if call.metadata.get("role") == "manager":
            calls["n"] += 1
            if calls["n"] == 1:
                return {"decision": "DELEGATE", "workers": [{"agent": "w", "instructions": "do"}]}
            return {"decision": "FAIL", "reason": "the worker's output is unusable"}
        return {"ok": True}

    res, eng, _ = _run([loop_node()], script)
    assert res.state == "failed"
    assert "iteration 2" in res.nodes["d"].error
    assert [c.agent for c in eng.history if c.metadata.get("role") == "worker"] == ["w"]


def test_fail_branch_contains_the_failure_and_skips_only_downstream():
    """``fail_branch``: the loop's branch is dead, the independent branch still completes."""
    node = loop_node()
    node["retry"] = {"error_strategy": "fail_branch"}
    res, eng, events = _run(
        [node, agent("after", deps=["d"]), agent("independent")],
        _manager_says({"decision": "FAIL", "reason": "no"}),
    )

    assert res.state == "complete"
    assert res.nodes["d"].status == "failed"
    assert res.nodes["after"].status == "skipped"
    assert res.nodes["independent"].status == "success"
    assert [c.agent for c in eng.history if c.metadata.get("role") != "manager"] == ["independent"]
    branch = [kw for et, kw in events if et == "node.branch_failed"]
    assert branch and branch[0]["node_id"] == "d" and branch[0]["skipped"] == ["after"]
    assert res.partial_nodes == []


def test_the_exception_carries_where_the_loop_stopped():
    from corvin_workflows.node_types import _execute_delegation_loop

    with pytest.raises(DelegationLoopFailed) as info:
        _execute_delegation_loop(
            node=loop_node(), engine=ScriptedEngine(script=lambda c: {"decision": "FAIL"}),
            state={}, inputs={}, audit=lambda *a, **k: None)
    assert info.value.final_failure is True
    assert info.value.iteration == 1 and info.value.workers_spawned == 0
    assert "manager said FAIL" in str(info.value)


# ── budget exhaustion: still a success, but named as partial ─────────────────

def _always_delegate(call):
    if call.metadata.get("role") == "manager":
        return {"decision": "DELEGATE", "workers": [{"agent": "w", "instructions": "do"}]}
    return {"ok": True}


@pytest.mark.parametrize("budget,reason", [
    ({"max_loops": 2, "max_total_workers": 50}, "max_loops reached (2)"),
    ({"max_loops": 50, "max_total_workers": 3}, "max_total_workers reached (3)"),
])
def test_a_budget_exhausted_loop_is_a_partial_node_not_a_failure(budget, reason):
    res, _, _ = _run([loop_node(budget=budget), agent("after", deps=["d"])], _always_delegate)

    assert res.state == "complete"
    assert res.nodes["d"].status == "success"
    assert res.nodes["d"].output["_terminal"] == {
        "state": "partial", "iteration": res.nodes["d"].output["_terminal"]["iteration"], "reason": reason}
    assert res.nodes["d"].output["result"] is None
    assert res.partial_nodes == ["d"]
    assert res.nodes["after"].status == "success", "abort is not the rule for a partial loop"


def test_a_completed_loop_is_not_partial():
    res, _, _ = _run([loop_node()], _manager_says({"decision": "COMPLETE", "result": {"score": 7}}))
    assert res.state == "complete" and res.partial_nodes == []
    assert res.nodes["d"].output["score"] == 7
    assert res.nodes["d"].output["_terminal"]["state"] == "complete"


def test_no_terminal_state_failed_can_reach_a_successful_node():
    """The invariant behind PLS-1, over every decision shape above: a node whose status is
    ``success`` never carries ``terminal.state == "failed"``."""
    shapes = [p.values[0] for p in BAD_DECISIONS] + [
        {"decision": "COMPLETE", "result": {}}, {"decision": "DELEGATE", "workers": []}]
    for decision in shapes:
        res, _, _ = _run([loop_node()], _manager_says(decision))
        node = res.nodes["d"]
        if node.status == "success":
            assert node.output["_terminal"]["state"] in ("complete", "partial"), decision
        else:
            assert res.state == "failed", decision


# ── R2-BR-8: DELEGATE that delegates nothing ─────────────────────────────────

@pytest.mark.parametrize("workers", [
    pytest.param([], id="empty-list"),
    pytest.param(None, id="null"),
    pytest.param("w", id="string"),
    pytest.param({"agent": "w"}, id="object-not-list"),
    pytest.param(["w"], id="list-of-strings"),
    pytest.param([{"instructions": "no agent"}], id="worker-without-agent"),
])
def test_a_delegate_without_usable_workers_fails_at_once(workers):
    """It used to call the manager again until max_loops and end `partial` (20 paid manager
    calls for a decision that was never actionable); the legacy executor raises here."""
    decision = {"decision": "DELEGATE"}
    if workers is not None:
        decision["workers"] = workers
    res, eng, _ = _run([loop_node(budget={"max_loops": 5, "max_total_workers": 5}),
                        agent("after", deps=["d"])], _manager_says(decision))
    assert res.state == "failed"
    assert "without a usable workers list" in (res.nodes["d"].error or "")
    assert len([c for c in eng.history if c.metadata.get("role") == "manager"]) == 1
    assert [c for c in eng.history if c.metadata.get("role") != "manager"] == []
    assert res.partial_nodes == []


# ── R2-BR-7: the CLI and the readiness check read partial_nodes too ──────────

def _doc_and_result(script, budget=None):
    doc = make_doc([loop_node(budget=budget) if budget else loop_node()])
    validate(doc)
    eng = ScriptedEngine(script=script)
    res = DAGRunner(doc, engine=eng, tenant_id="_default", audit_sink=lambda r: None).run(inputs={})
    return doc, res, eng


def test_the_cli_reports_a_budget_exhausted_run_as_partial_with_a_non_zero_exit():
    import json as _json
    from corvin_workflows import cli

    doc, res, eng = _doc_and_result(_always_delegate, {"max_loops": 2, "max_total_workers": 50})
    assert res.state == "complete" and res.partial_nodes == ["d"]      # precondition
    text, code = cli._format_run_result(doc, res, {}, format_="text", engine=eng)
    assert code == cli.EXIT_PARTIAL != 0
    first = text.splitlines()[0]
    assert first.startswith("[PARTIAL]") and "`partial`" in first
    assert "The result is incomplete" in text
    assert "partial" in text and "iter=2" in text, "the loop line reads the node's own keys"
    assert not any(ch in text for ch in "✅❌⏸❓"), "status icon literal in CLI output"

    payload, jcode = cli._format_run_result(doc, res, {}, format_="json", engine=eng)
    data = _json.loads(payload)
    assert jcode == cli.EXIT_PARTIAL
    assert data["state"] == "complete" and data["status"] == "partial" and data["partial_nodes"] == ["d"]


def test_the_cli_keeps_exit_0_for_a_finished_run_and_1_for_a_failed_one():
    import json as _json
    from corvin_workflows import cli

    doc, res, eng = _doc_and_result(_manager_says({"decision": "COMPLETE", "result": {"score": 7}}))
    text, code = cli._format_run_result(doc, res, {}, format_="text", engine=eng)
    assert code == 0 and text.startswith("[OK]")
    data = _json.loads(cli._format_run_result(doc, res, {}, format_="json", engine=eng)[0])
    assert data["status"] == "complete" and data["partial_nodes"] == []

    doc, res, eng = _doc_and_result(_manager_says({"decision": "FAIL", "reason": "no"}))
    text, code = cli._format_run_result(doc, res, {}, format_="text", engine=eng)
    assert code == 1 and text.startswith("[FAILED]")


def test_readiness_reachability_fails_on_a_partial_loop():
    from corvin_workflows import readiness

    doc, res, _ = _doc_and_result(_always_delegate, {"max_loops": 2, "max_total_workers": 50})
    sig = readiness._reachability(doc, res, False, {})
    assert sig.status == "fail" and "without COMPLETE" in sig.detail and "'d'" in sig.detail

    doc, res, _ = _doc_and_result(_manager_says({"decision": "COMPLETE", "result": {}}))
    assert readiness._reachability(doc, res, False, {}).status == "pass"
