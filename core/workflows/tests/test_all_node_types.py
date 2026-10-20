"""The all-node-types reference workflow (examples/all_node_types.awp.yaml).

Runs the REAL DAGRunner / resume_workflow / checkpoint store with the deterministic
StubEngine (no model, no network). Pins three things:

* every registered node type appears in the example, so a new type cannot ship
  without a reference node (and a node type that cannot run fails here, not in a
  customer's workflow);
* the whole graph executes: branch (route), join (merge), two pauses (ask_human,
  approval), fail_branch containment, deliver/answer writing real outbox envelopes;
* reject / expiry / branch-join regressions found in the workflows review
  (REVIEW-0051: WF-02, WF-04, WF-05).

Not covered here, and not pretended to be: a real `claude` engine, TTS, and a running
bridge turning the outbox envelopes into chat messages.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from corvin_workflows import DAGRunner, StubEngine, WorkflowDoc, validate
from corvin_workflows import checkpoint as ckpt
from corvin_workflows.node_types import NODE_TYPES
from corvin_workflows.runner import PauseExpired, WorkflowChanged, resume_workflow
from corvin_workflows.storage import load_workflow

EXAMPLE = Path(__file__).resolve().parents[1] / "corvin_workflows" / "examples" / "all_node_types.awp.yaml"


def _stub(findings: int = 3, tone: str = "urgent") -> StubEngine:
    def default(call):
        a = call.agent
        if a == "researcher":
            return {"items": [f"f{i}" for i in range(findings)], "tone": tone}
        if a == "writer":
            return {"text": f"w{call.iteration}"}
        if a == "planner":
            if call.iteration == 1:
                return {"decision": "DELEGATE", "workers": [{"agent": "analyst"}]}
            return {"decision": "COMPLETE", "result": {"plan": "p"}}
        if a == "analyst":
            return {"note": "n"}
        if a == "triager":
            return {"class": tone if tone in ("urgent", "routine") else "routine"}
        if a == "reviewer":
            return {"summary": f"sum-{call.metadata['node_id']}"}
        if a == "flaky_source":
            raise RuntimeError("boom")
        raise KeyError(a)

    return StubEngine(default=default)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setenv("ADAPTER_OUTBOX", str(outbox))
    return outbox


@pytest.fixture
def _doc(tmp_path):
    """Loads the example from a file (resume reloads the workflow from its path)."""
    def make(explicit_chat: bool = False) -> WorkflowDoc:
        if not explicit_chat:
            return load_workflow(EXAMPLE)
        import yaml
        data = yaml.safe_load(EXAMPLE.read_text())
        for n in data["orchestration"]["graph"]:
            if n["type"] == "deliver":
                n["config"]["chat_id"] = "qa-chat"
            if n["type"] == "answer":
                n["chat_id"] = "qa-chat"
        p = tmp_path / "all_node_types_explicit.awp.yaml"
        p.write_text(yaml.safe_dump(data, sort_keys=False))
        return load_workflow(p)
    return make


def _envelopes(outbox: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(outbox.glob("*.json"))]


def test_example_uses_every_registered_node_type(_doc):
    doc = _doc()
    validate(doc)
    used = {n["type"] for n in doc.graph}
    assert used == set(NODE_TYPES), f"missing: {sorted(set(NODE_TYPES) - used)}"
    assert {n["mode"] for n in doc.graph if n["type"] == "route"} == {"condition", "classify"}


def test_full_run_pause_ask_human_pause_approval_complete(_sandbox, _doc):
    doc = _doc(explicit_chat=True)
    eng = _stub()
    run = DAGRunner(doc, engine=eng, console_run=True).run({"topic": "x"})

    # ask_human with chat_id=auto in a console run pauses for the operator (WF-02)
    assert run.state == "paused" and run.paused_at_node == "confirm"
    assert run.nodes["join"].output == {"report": ["sum-high_path", "urgent"]}
    assert run.nodes["low_path"].status == "skipped"
    assert run.nodes["flaky"].status == "failed"
    assert run.nodes["after_flaky"].status == "skipped"
    assert "signoff" not in run.nodes

    r2 = resume_workflow(run.run_id, "yes, publish", engine=eng)
    assert r2.state == "paused" and r2.paused_at_node == "signoff"
    assert r2.nodes["confirm"].output["publish"] is True

    r3 = resume_workflow(run.run_id, "approve: looks good", engine=eng)
    assert r3.state == "complete"
    assert r3.nodes["signoff"].output["approved"] is True
    assert r3.nodes["notify"].output["delivered"] is True
    assert r3.nodes["reply"].output["sent"] is True
    kinds = sorted(e.get("_workflow_deliver") and "deliver" or e.get("_workflow_answer") and "answer" or "other"
                   for e in _envelopes(_sandbox))
    assert kinds == ["answer", "deliver"]
    assert ckpt.load(run.run_id) is None  # a completed run leaves no checkpoint


def test_console_run_deliver_and_answer_with_auto_chat_do_not_fabricate_a_chat(_sandbox, _doc):
    doc = _doc()
    eng = _stub()
    run = DAGRunner(doc, engine=eng, console_run=True).run({})
    resume_workflow(run.run_id, "yes", engine=eng)
    done = resume_workflow(run.run_id, "approve", engine=eng)
    assert done.state == "complete"
    assert done.nodes["notify"].output["delivered"] is False
    assert done.nodes["reply"].output["sent"] is False
    assert _envelopes(_sandbox) == []


def test_reject_fails_the_run_and_nothing_downstream_runs(_sandbox, _doc):
    doc = _doc(explicit_chat=True)
    eng = _stub()
    run = DAGRunner(doc, engine=eng, console_run=True).run({})
    resume_workflow(run.run_id, "yes", engine=eng)
    out = resume_workflow(run.run_id, "reject: not good enough", engine=eng)
    assert out.state == "failed"
    assert "rejected" in out.error and "not good enough" in out.error
    assert "notify" not in out.nodes and "reply" not in out.nodes
    assert _envelopes(_sandbox) == []


def test_unrecognised_free_text_reply_is_a_rejection(_doc):
    doc = _doc()
    eng = _stub()
    run = DAGRunner(doc, engine=eng, console_run=True).run({})
    resume_workflow(run.run_id, "yes", engine=eng)
    out = resume_workflow(run.run_id, "hmm maybe later", engine=eng)  # fail-closed
    assert out.state == "failed"


def test_other_branch_runs_when_the_condition_does_not_match(_doc):
    doc = _doc()
    eng = _stub(findings=1, tone="routine")
    run = DAGRunner(doc, engine=eng, console_run=True).run({})
    assert run.nodes["size_gate"].output["case"] == "low"
    assert run.nodes["high_path"].status == "skipped"
    assert run.nodes["low_path"].status == "success"
    # WF-05: the join still runs with the one live branch
    assert run.nodes["join"].status == "success"
    assert run.nodes["join"].output == {"report": ["sum-low_path", "routine"]}
    assert run.paused_at_node == "confirm"


def test_join_is_skipped_only_when_every_dependency_is_dead():
    graph = [
        {"id": "gate", "type": "route", "mode": "condition", "depends_on": [],
         "cases": [{"id": "a", "when": {"selector": "x", "op": "==", "value": 1}},
                   {"id": "b", "when": "default"}]},
        {"id": "pa", "type": "agent", "agent": "r", "branch": "a", "depends_on": ["gate"]},
        {"id": "pb", "type": "agent", "agent": "r", "branch": "b", "depends_on": ["gate"]},
        {"id": "pc", "type": "agent", "agent": "r", "branch": "c", "depends_on": ["gate"]},
        {"id": "join_all_dead", "type": "merge", "strategy": "concat_list",
         "inputs": ["pa.v", "pc.v"], "output": "o", "depends_on": ["pa", "pc"]},
        {"id": "after", "type": "agent", "agent": "r", "depends_on": ["join_all_dead"]},
        {"id": "join_live", "type": "merge", "strategy": "concat_list",
         "inputs": ["pa.v", "pb.v"], "output": "o", "depends_on": ["pa", "pb"]},
    ]
    doc = WorkflowDoc(awp_version="1.1.0", name="join_rules", description="d",
                      orchestration={"engine": "dag", "graph": graph}, raw={})
    validate(doc)
    run = DAGRunner(doc, engine=StubEngine(default=lambda c: {"v": c.metadata["node_id"]})).run({"x": 2})
    st = {k: v.status for k, v in run.nodes.items()}
    assert st["pa"] == "skipped" and st["pc"] == "skipped" and st["pb"] == "success"
    assert st["join_all_dead"] == "skipped" and st["after"] == "skipped"
    assert st["join_live"] == "success"


def test_paused_run_past_its_deadline_cannot_be_resumed(_doc):
    doc = _doc()
    eng = _stub()
    run = DAGRunner(doc, engine=eng, console_run=True).run({})
    resume_workflow(run.run_id, "yes", engine=eng)  # now paused at the approval (timeout_s 3600)
    saved = ckpt.load(run.run_id)
    assert saved["expires_at_ms"] and saved["expires_at_ms"] > time.time() * 1000
    saved["expires_at_ms"] = int(time.time() * 1000) - 1
    path = ckpt._run_path(run.run_id, None)
    path.write_text(json.dumps(saved))
    with pytest.raises(PauseExpired):
        resume_workflow(run.run_id, "approve", engine=eng)
    assert ckpt.load(run.run_id) is not None  # kept for inspection


def test_editing_the_workflow_file_after_the_pause_blocks_the_resume(tmp_path):
    src = EXAMPLE.read_text()
    f = tmp_path / "wf.awp.yaml"
    f.write_text(src)
    doc = load_workflow(f)
    eng = _stub()
    run = DAGRunner(doc, engine=eng, console_run=True).run({})
    assert run.state == "paused"
    f.write_text(src.replace("Should the report be published?", "Publish now?"))
    with pytest.raises(WorkflowChanged):
        resume_workflow(run.run_id, "yes", engine=eng)
    assert ckpt.load(run.run_id) is not None  # still there, not consumed
