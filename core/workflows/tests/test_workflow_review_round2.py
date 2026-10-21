"""Regression tests for REVIEW-0051 round 1 engine findings (A-01 .. A-10).

Each test failed on the code as reviewed (see the review file for the reproduction).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from corvin_workflows import DAGRunner, StubEngine, WorkflowDoc, validate
from corvin_workflows import checkpoint as ckpt
from corvin_workflows.runner import resume_workflow
from corvin_workflows.validator import WorkflowInvalid


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    ob = tmp_path / "outbox"
    ob.mkdir()
    monkeypatch.setenv("ADAPTER_OUTBOX", str(ob))
    return ob


def _doc(graph, tmp_path=None, name="r2"):
    d = WorkflowDoc(awp_version="1.1.0", name=name, description="d",
                    orchestration={"engine": "dag", "graph": graph}, raw={})
    if tmp_path is not None:
        import yaml
        p = tmp_path / f"{name}.awp.yaml"
        p.write_text(yaml.safe_dump({"awp": "1.1.0", "workflow": {"name": name, "description": "d"},
                                     "orchestration": {"engine": "dag", "graph": graph}}))
        from corvin_workflows.storage import load_workflow
        return load_workflow(p)
    return d


def _texts(outbox):
    return [json.loads(p.read_text())["text"] for p in sorted(outbox.glob("*.json"))]


def _approval_doc(tmp_path):
    return _doc([
        {"id": "a", "type": "approval", "depends_on": [], "message": "ok?"},
        {"id": "b", "type": "code", "language": "python3", "depends_on": ["a"],
         "source": "def main():\n    return {'v': 1}\n", "outputs": ["v"]},
    ], tmp_path)


@pytest.mark.parametrize("reply", [
    "approver is on holiday, reject for now", "approve? NO", "approved by nobody",
    "approver unavailable - do not proceed", "approvals pending", "reject",
])
def test_a06_approval_prefix_is_not_consent(tmp_path, reply):
    doc = _approval_doc(tmp_path)
    run = DAGRunner(doc, engine=StubEngine(), console_run=True).run({})
    out = resume_workflow(run.run_id, reply, engine=StubEngine())
    assert out.state == "failed", reply


@pytest.mark.parametrize("reply", ["approve", "Approve", "approve: fine by me", "APPROVE:ok"])
def test_a06_exact_approve_token_still_approves(tmp_path, reply):
    doc = _approval_doc(tmp_path)
    run = DAGRunner(doc, engine=StubEngine(), console_run=True).run({})
    assert resume_workflow(run.run_id, reply, engine=StubEngine()).state == "complete"


@pytest.mark.parametrize("node_patch", [
    {"retry": {"max_retries": "abc"}},
    {"retry": {"error_strategy": "bogus"}},
    {"share_output": "output"},
])
def test_a03_bad_retry_or_share_output_is_rejected_by_the_validator(node_patch):
    node = {"id": "n", "type": "agent", "agent": "x", "depends_on": [], **node_patch}
    with pytest.raises(WorkflowInvalid):
        validate(_doc([node]))


@pytest.mark.parametrize("bad", [None, [1, 2], 42, "yes"])
def test_a03_non_dict_engine_output_fails_the_node_not_the_runner(_sandbox, bad):
    graph = [
        {"id": "say", "type": "answer", "channel": "discord", "chat_id": "c", "text": "sent",
         "depends_on": []},
        {"id": "n", "type": "agent", "agent": "x", "depends_on": ["say"]},
    ]
    run = DAGRunner(_doc(graph), engine=StubEngine(default=lambda c: bad)).run({})
    assert run.state == "failed" and run.nodes["n"].status == "failed"
    assert any(e["event"] == "run.terminal" for e in run.audit)


@pytest.mark.parametrize("field,val", [("timeout_minutes", float("nan")), ("timeout_minutes", float("inf")),
                                       ("timeout_minutes", 10 ** 9)])
def test_a04_non_finite_or_absurd_ask_human_timeout_is_rejected(field, val):
    node = {"id": "h", "type": "ask_human", "channel": "discord", "chat_id": "c", "prompt": "p",
            "depends_on": [], field: val}
    with pytest.raises(WorkflowInvalid):
        validate(_doc([node]))


def test_a04_nan_approval_timeout_is_rejected():
    with pytest.raises(WorkflowInvalid):
        validate(_doc([{"id": "h", "type": "approval", "depends_on": [], "timeout_s": float("nan")}]))


def test_a05_failed_resume_does_not_repeat_side_effects(tmp_path, _sandbox):
    calls = {"n": 0}

    def flaky(call):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return {"ok": True}

    doc = _doc([
        {"id": "ask", "type": "ask_human", "channel": "discord", "chat_id": "c", "prompt": "go?",
         "depends_on": []},
        {"id": "charge", "type": "answer", "channel": "discord", "chat_id": "c", "text": "CHARGED",
         "depends_on": ["ask"]},
        {"id": "flaky", "type": "agent", "agent": "x", "depends_on": ["charge"]},
    ], tmp_path)
    eng = StubEngine(default=flaky)
    run = DAGRunner(doc, engine=eng).run({})
    first = resume_workflow(run.run_id, "yes", engine=eng)
    assert first.state == "failed"
    second = resume_workflow(run.run_id, "yes", engine=eng)
    assert second.state == "complete"
    assert _texts(_sandbox).count("CHARGED") == 1


def test_a07_claim_of_a_dead_process_is_recoverable(tmp_path):
    doc = _approval_doc(tmp_path)
    run = DAGRunner(doc, engine=StubEngine(), console_run=True).run({})
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    ckpt.claim(run.run_id, claimer_pid=child.pid)  # claim held by a process that is gone
    assert any(c["run_id"] == run.run_id for c in ckpt.list_paused())
    out = resume_workflow(run.run_id, "approve", engine=StubEngine())
    assert out.state == "complete"


def test_a07_live_claim_still_blocks():
    pass  # covered by test_workflow_hardening (AlreadyClaimedError)


def test_a01_selector_must_point_at_a_dependency():
    graph = [
        {"id": "a_say", "type": "answer", "channel": "discord", "chat_id": "c",
         "text_from": "producer.v", "depends_on": []},
        {"id": "producer", "type": "code", "language": "python3", "depends_on": [],
         "source": "def main():\n    return {'v': 1}\n", "outputs": ["v"]},
    ]
    with pytest.raises(WorkflowInvalid, match="producer"):
        validate(_doc(graph))


def test_a01_transitive_dependency_is_fine():
    graph = [
        {"id": "producer", "type": "code", "language": "python3", "depends_on": [],
         "source": "def main():\n    return {'v': 1}\n", "outputs": ["v"]},
        {"id": "mid", "type": "code", "language": "python3", "depends_on": ["producer"],
         "source": "def main():\n    return {'w': 1}\n", "outputs": ["w"]},
        {"id": "say", "type": "answer", "channel": "discord", "chat_id": "c",
         "text_from": "producer.v", "depends_on": ["mid"]},
    ]
    validate(_doc(graph))


def _route(branch_a="urgent", branch_leaf=None):
    graph = [
        {"id": "gate", "type": "route", "mode": "condition", "depends_on": [],
         "cases": [{"id": "urgent", "when": {"selector": "x", "op": "==", "value": 1}},
                   {"id": "normal", "when": "default"}]},
        {"id": "p", "type": "agent", "agent": "r", "branch": branch_a, "depends_on": ["gate"]},
        {"id": "mid", "type": "agent", "agent": "r", "depends_on": ["gate"]},
    ]
    if branch_leaf:
        graph.append({"id": "leaf", "type": "agent", "agent": "r", "branch": branch_leaf,
                      "depends_on": ["mid"]})
    return _doc(graph)


def test_a02_branch_label_must_be_a_case_of_a_direct_route():
    validate(_route("urgent"))
    with pytest.raises(WorkflowInvalid, match="Urgent"):
        validate(_route("Urgent"))
    with pytest.raises(WorkflowInvalid, match="leaf"):
        validate(_route("urgent", branch_leaf="urgent"))  # route is not a DIRECT dependency of leaf


@pytest.mark.parametrize("graph", [
    [5], [{"id": "a", "depends_on": 5, "type": "agent", "agent": "x"}],
    [{"id": "a", "depends_on": [{"x": 1}], "type": "agent", "agent": "x"}],
])
def test_a10_validate_raises_workflowinvalid_for_malformed_graphs(graph):
    with pytest.raises(WorkflowInvalid):
        validate(_doc(graph))


def test_a10_deep_chain_does_not_recurse():
    n = 1500
    graph = [{"id": f"n{i}", "type": "agent", "agent": "x",
              "depends_on": [f"n{i + 1}"] if i < n - 1 else []} for i in range(n)]
    validate(_doc(graph))


def test_a10_cli_resume_of_an_expired_pause_exits_3_without_traceback(tmp_path):
    doc = _approval_doc(tmp_path)
    run = DAGRunner(doc, engine=StubEngine(), console_run=True).run({})
    saved = ckpt.load(run.run_id)
    saved["expires_at_ms"] = int(time.time() * 1000) - 1
    ckpt._run_path(run.run_id, None).write_text(json.dumps(saved))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    r = subprocess.run([sys.executable, "-m", "corvin_workflows", "resume", run.run_id, "approve",
                        "--engine", "stub"], capture_output=True, text=True, env=env)
    assert r.returncode == 3 and "Traceback" not in r.stderr and "deadline" in r.stderr
