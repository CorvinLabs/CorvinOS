"""Review 2026-11-19, PLS-4: the console's own (legacy) delegation loop fails honestly.

Until this fix ``_run_delegation_loop_node`` turned three non-results into a successful node:
an answer that is no decision was returned verbatim as the result ("treat as COMPLETE"), a
DELEGATE naming no worker returned the invented text "Task completed.", and the end of the
budget returned the concatenated worker outputs (or a placeholder string). The manager had no
way to say the task cannot be done.

The console runs for real (uvicorn over a socket, a real session, the real pre-spawn gates, a
real ``claude`` subprocess per spawn); only the model's ANSWER comes from the recording shim.
Each case goes through ``POST /v1/console/workflows/<id>/runs`` and reads the SSE stream.

Run: .venv/bin/python -m pytest -q -o addopts="" -p no:cacheprovider \
     core/console/tests/test_legacy_delegation_loop_pls4_e2e.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parents[2] / "corvin_operator" / "bridges" / "shared"
for _p in (str(_HERE), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dag_adv_shim as ds  # noqa: E402
from test_dag_adversarial_f1_e2e import agent, doc  # noqa: E402
from test_dag_adversarial_f1b_e2e import run_with_rid  # noqa: E402
from test_node_model_console_e2e import _make  # noqa: E402
from dag_adv_console import live_sandbox, module_console  # noqa: E402,F401

MANAGER = "You are coordinating work"      # head of the manager prompt
WORKER = "Execute this task:"              # head of a worker prompt


def _graph(max_loops: int = 2, max_workers: int = 2) -> str:
    # The same shape the AD-23 test uses to reach the console's own executor: an agent, a
    # fan_out over its text lines and the loop.
    return doc([
        agent("plain"),
        {"id": "fan", "type": "fan_out", "agent": "c", "depends_on": ["plain"],
         "items_from": "plain.output", "instructions": "MARK-FAN"},
        {"id": "loop", "type": "delegation_loop", "agent": "d", "depends_on": [],
         "instructions": "MARK-LOOP",
         "config": {"manager": "m", "budget": {"max_loops": max_loops,
                                               "max_total_workers": max_workers}}},
    ])


def _rules(manager_action: dict, worker_reply: str = "worker output") -> list[dict]:
    return [
        # The gate's verdict is stated, not left to the shim's default: a confident clear, so
        # every refusal in this file is the loop's own and never the acceptable-use gate's.
        {"match": {"kind": "gate"},
         "action": {"reply": {"violated_rule_id": "", "confidence": 0.99, "reason": "ok"}}},
        {"match": {"kind": "legacy_node", "prompt_regex": "MARK-PLAIN"}, "action": {"reply": "alpha"}},
        {"match": {"kind": "legacy_node", "prompt_regex": MANAGER}, "action": manager_action},
        {"match": {"kind": "legacy_node", "prompt_regex": WORKER}, "action": {"reply": worker_reply}},
    ]


@pytest.fixture
def shim(tmp_path, monkeypatch):
    return ds.install(tmp_path, monkeypatch, mode="canned")


def _loop_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("node_id") == "loop"
            and e.get("type") in ("node_completed", "node_failed")]


def _spawns(shim, head: str) -> list[dict]:
    return [r for r in shim.rows("legacy_node") if head in json.dumps(r)]


FAILING = [
    pytest.param({"reply": "I am sorry, I cannot help with that."},
                 "not a decision", 1, 0, id="prose-is-not-a-result"),
    pytest.param({"reply": ""}, "not a decision", 1, 0, id="empty-answer"),
    pytest.param({"reply": {"ok": True}}, "not a decision", 1, 0, id="json-without-decision"),
    pytest.param({"reply": {"decision": "FAIL", "reason": "the source has no rows"}},
                 "the source has no rows", 1, 0, id="manager-FAIL"),
    pytest.param({"reply": {"decision": "DELEGATE", "workers": []}},
                 "delegated to no worker", 1, 0, id="empty-DELEGATE"),
    pytest.param({"reply": {"decision": "DELEGATE"}},
                 "delegated to no worker", 1, 0, id="DELEGATE-without-workers"),
    pytest.param({"reply": {"decision": "COMPLETE"}},
                 "COMPLETE without a result", 1, 0, id="COMPLETE-without-result"),
    pytest.param({"reply": {"decision": "MAYBE"}}, "unknown decision", 1, 0, id="unknown-decision"),
    pytest.param({"reply": {"decision": "DELEGATE", "workers": [{"instructions": "dig"}]}},
                 "budget exhausted", 2, 2, id="budget-ends-without-COMPLETE"),
]


@pytest.mark.parametrize("manager_action,needle,n_manager,n_worker", FAILING)
def test_a_loop_without_a_result_ends_node_failed(tmp_path, shim, manager_action, needle,
                                                  n_manager, n_worker):
    shim.set_rules(_rules(manager_action))
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", _graph())
        _rid, events = run_with_rid(client, "wf")

    assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is False, events[-3:]
    loop = _loop_events(events)
    assert [e["type"] for e in loop] == ["node_failed"], loop
    assert needle in loop[0]["error"], loop[0]["error"]
    # positive control: the console's own executor ran the loop, with exactly these spawns
    assert len(_spawns(shim, MANAGER)) == n_manager
    assert len(_spawns(shim, WORKER)) == n_worker
    # nothing the manager or a worker wrote became a node output
    assert not any(e.get("node_id") == "loop" and e.get("output") for e in events)


def test_complete_with_a_result_is_the_only_success(tmp_path, shim):
    shim.set_rules(_rules({"sequence": [
        {"reply": {"decision": "DELEGATE", "workers": [{"instructions": "dig"}]}},
        {"reply": "```json\n" + json.dumps({"decision": "COMPLETE", "result": "final answer 42"}) + "\n```"},
    ]}))
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", _graph())
        _rid, events = run_with_rid(client, "wf")

    assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True, events[-3:]
    loop = _loop_events(events)
    assert [e["type"] for e in loop] == ["node_completed"], loop
    assert loop[0]["output"] == "final answer 42"
    assert len(_spawns(shim, MANAGER)) == 2 and len(_spawns(shim, WORKER)) == 1


def test_the_manager_is_told_how_to_fail(tmp_path, shim):
    """The protocol half: FAIL is offered in the prompt the manager actually receives."""
    shim.set_rules(_rules({"reply": {"decision": "COMPLETE", "result": "ok"}}))
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", _graph())
        run_with_rid(client, "wf")
    manager = _spawns(shim, MANAGER)
    assert manager, "the manager must have spawned"
    blob = json.dumps(manager[0])
    assert '\\"decision\\": \\"FAIL\\"' in blob or '"decision": "FAIL"' in blob, blob[:600]


def test_parse_manager_decision_infers_nothing_from_prose():
    from corvin_console.routes import workflows as wf

    ok = {"decision": "COMPLETE", "result": "r"}
    assert wf._parse_manager_decision(json.dumps(ok)) == ok
    assert wf._parse_manager_decision("thinking...\n" + json.dumps(ok) + "\n") == ok
    assert wf._parse_manager_decision("```json\n" + json.dumps(ok, indent=2) + "\n```") == ok
    for raw in ("", "   ", "done", "decision: COMPLETE", '{"result": "r"}', "[1, 2]",
                '"COMPLETE"', "{not json}", None):
        assert wf._parse_manager_decision(raw) is None, raw
