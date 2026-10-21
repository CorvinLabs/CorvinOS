"""HTTP E2E: the console runs the all-node-types workflow through the shared DAGRunner.

Real FastAPI router + TestClient, sandboxed CORVIN_HOME, deterministic StubEngine injected via
the module's ENGINE_FACTORY test seam (production never replaces it). Covers: SSE stream that
stays open across a pause, ask_human reply (POST /resume), approval (POST /approve and /reject),
run history, node add/replace/delete routes. Not covered: a real `claude` engine.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import yaml

from test_workflow_resume_hardening import _live_sandbox as _sandbox  # noqa: E402  (same sandbox, same dir)

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "core/workflows/corvin_workflows/examples/all_node_types.awp.yaml"


def _stub_factory():
    from corvin_workflows import StubEngine

    def default(call):
        a = call.agent
        if a == "researcher":
            return {"items": ["a", "b", "c"], "tone": "urgent"}
        if a == "writer":
            return {"text": f"w{call.iteration}"}
        if a == "planner":
            return ({"decision": "DELEGATE", "workers": [{"agent": "analyst"}]} if call.iteration == 1
                    else {"decision": "COMPLETE", "result": {"plan": "p"}})
        if a == "analyst":
            return {"note": "n"}
        if a == "triager":
            return {"class": "urgent"}
        if a == "reviewer":
            return {"summary": f"sum-{call.metadata['node_id']}"}
        raise RuntimeError("boom")

    return lambda tenant, graph: StubEngine(default=default)


def _make(home, tid, wid, text):
    from forge import paths as _fp
    d = _fp.tenant_home(tid) / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{wid}.meta.json").write_text(json.dumps({"id": wid, "name": wid, "phase": "ready"}))
    (d / f"{wid}.awp.yaml").write_text(text)


def _stream_in_thread(client, wid, events):
    def go():
        with client.stream("POST", f"/v1/console/workflows/{wid}/runs", json={"inputs": {}, "dry_run": False}) as r:
            events.append(("status", r.status_code, r.headers.get("X-Run-Id")))
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def _wait(pred, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out")


def _rid(events):
    return _wait(lambda: events and events[0][2])


def _paused_at(client, wid, rid, node):
    def p():
        r = client.get(f"/v1/console/workflows/{wid}/runs/{rid}").json()["run"]
        return r["status"] == "paused" and r["awaiting"]["node_id"] == node
    _wait(p)


@pytest.fixture
def env(tmp_path):
    with _sandbox(tmp_path) as (client, home, tid):
        from corvin_console.routes import workflow_dag_run as d
        old = d.ENGINE_FACTORY
        d.ENGINE_FACTORY = _stub_factory()
        _make(home, tid, "allnodes", EXAMPLE.read_text())
        try:
            yield client, home, tid
        finally:
            d.ENGINE_FACTORY = old


def test_full_run_through_http_pause_reply_approve(env):
    client, _, _ = env
    events: list = []
    t = _stream_in_thread(client, "allnodes", events)
    rid = _rid(events)
    _paused_at(client, "allnodes", rid, "confirm")
    ap = client.get(f"/v1/console/workflows/allnodes/runs/{rid}/approval").json()
    assert ap["status"] == "pending" and ap["kind"] == "ask_human"
    # approve on an ask_human pause is refused: it needs a reply, not an approval
    assert client.post(f"/v1/console/workflows/allnodes/runs/{rid}/approve", json={"comment": ""}).status_code == 409

    r = client.post(f"/v1/console/workflows/allnodes/runs/{rid}/resume", json={"reply": "yes publish"})
    assert r.status_code == 200 and r.json()["status"] == "paused"
    _paused_at(client, "allnodes", rid, "signoff")
    assert client.get(f"/v1/console/workflows/allnodes/runs/{rid}/approval").json()["kind"] == "approval"

    assert client.post(f"/v1/console/workflows/allnodes/runs/{rid}/approve", json={"comment": "ok"}).json()["status"] == "approved"
    t.join(30)
    assert not t.is_alive()
    kinds = [e["type"] for e in events[1:]]
    assert kinds.count("node_awaiting_reply") == 1 and kinds.count("node_awaiting_approval") == 1
    assert kinds[-1] == "run_completed" and events[-1]["ok"] is True
    by_node = {e["node_id"]: e["type"] for e in events[1:] if e.get("node_id") and e["type"].startswith("node_") and "awaiting" not in e["type"]}
    assert by_node["high_path"] == "node_completed" and by_node["low_path"] == "node_skipped"
    assert by_node["flaky"] == "node_failed" and by_node["after_flaky"] == "node_skipped"
    run = client.get(f"/v1/console/workflows/allnodes/runs/{rid}").json()
    assert run["run"]["status"] == "complete" and run["run"]["ok"] is True
    assert len(run["events"]) == len(events) - 1
    # a finished run has no pending question; a second decision is a 404
    assert client.get(f"/v1/console/workflows/allnodes/runs/{rid}/approval").json()["status"] == "none"
    assert client.post(f"/v1/console/workflows/allnodes/runs/{rid}/approve", json={"comment": ""}).status_code == 404


def test_reject_ends_the_run_failed(env):
    client, _, _ = env
    events: list = []
    t = _stream_in_thread(client, "allnodes", events)
    rid = _rid(events)
    _paused_at(client, "allnodes", rid, "confirm")
    client.post(f"/v1/console/workflows/allnodes/runs/{rid}/resume", json={"reply": "yes"})
    _paused_at(client, "allnodes", rid, "signoff")
    assert client.post(f"/v1/console/workflows/allnodes/runs/{rid}/reject", json={"comment": "no"}).status_code == 200
    t.join(30)
    assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is False
    assert any(e.get("type") == "node_failed" and e.get("node_id") == "signoff" for e in events[1:])
    assert client.get(f"/v1/console/workflows/allnodes/runs/{rid}").json()["run"]["status"] == "failed"


def test_deterministic_workflow_needs_no_engine_and_edit_during_pause_is_harmless(env):
    client, home, tid = env
    from corvin_console.routes import workflow_dag_run as d

    def boom(tenant, graph):
        raise AssertionError("no engine may be built for an engine-free graph")
    text = yaml.safe_dump({
        "awp": "1.1.0", "workflow": {"name": "det", "description": "d"},
        "orchestration": {"engine": "dag", "graph": [
            {"id": "calc", "type": "code", "language": "python3", "depends_on": [],
             "source": "def main():\n    return {'n': 5}\n", "outputs": ["n"]},
            {"id": "gate", "type": "approval", "depends_on": ["calc"], "message": "go?"},
            {"id": "done", "type": "route", "mode": "condition", "depends_on": ["gate"],
             "cases": [{"id": "x", "when": {"selector": "calc.n", "op": ">", "value": 3}}, {"id": "y", "when": "default"}]},
        ]}}, sort_keys=False)
    _make(home, tid, "det", text)
    d.ENGINE_FACTORY = d.default_engine  # the REAL factory: must hand out the null engine
    events: list = []
    t = _stream_in_thread(client, "det", events)
    rid = _rid(events)
    _paused_at(client, "det", rid, "gate")
    # editing the workflow while the run waits must not change what the resume executes
    from forge import paths as _fp
    yp = _fp.tenant_home(tid) / "workflows" / "det.awp.yaml"
    yp.write_text(text.replace("n': 5", "n': 1"))
    client.post(f"/v1/console/workflows/det/runs/{rid}/approve", json={"comment": ""})
    t.join(30)
    done = [e for e in events[1:] if e.get("node_id") == "done" and e["type"] == "node_completed"]
    assert done and json.loads(done[0]["output"])["case"] == "x"


def test_node_routes_add_replace_delete_validate_whole_document(env):
    client, _, _ = env
    base = "/v1/console/workflows/allnodes"
    node = {"id": "extra", "type": "merge", "depends_on": ["research"], "strategy": "concat_list",
            "inputs": ["research.items"], "output": "o"}
    r = client.post(f"{base}/nodes", json={"node": node})
    assert r.status_code == 200 and any(n["id"] == "extra" for n in r.json()["graph"])
    assert client.post(f"{base}/nodes", json={"node": node}).status_code == 409
    bad = dict(node, strategy="nope")
    assert client.put(f"{base}/nodes/extra", json={"node": bad}).status_code == 400
    assert client.put(f"{base}/nodes/extra", json={"node": dict(node, id="renamed")}).status_code == 400
    assert client.put(f"{base}/nodes/extra", json={"node": dict(node, output="p")}).status_code == 200
    # deleting a node another node depends on leaves a dangling dependency: refused, file untouched
    assert client.delete(f"{base}/nodes/research").status_code == 400
    assert client.delete(f"{base}/nodes/extra").status_code == 200
    assert client.delete(f"{base}/nodes/extra").status_code == 404
    assert client.post(f"{base}/nodes", json={"node": {"type": "agent"}}).status_code == 400
