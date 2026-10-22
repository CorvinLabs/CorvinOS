"""HTTP E2E: a workflow with a `peer_agent` node runs through the console and the readiness route
never sends (ADR-2276 / PLAN-0963 S6).

Real FastAPI app over a real socket (same sandbox as the DAG console E2E), the REAL engine factory
(a peer node needs no engine), the real node executor, outbound gate, ledger and audit chain. Only the
sender's `send` boundary is a fake here - the real signed HTTP exchange between two instances is
exercised in tests/federation/test_workflow_peer_agent_e2e.py.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

import pytest
import yaml

from test_workflow_resume_hardening import _live_sandbox as _sandbox  # noqa: E402

BASE = "/v1/console/workflows"


@dataclass
class _Res:
    ok: bool = True
    status: str = "ok"
    task_id: str = ""
    instance_id: str = "iid-acme-0001"
    instance_id_match: bool = True
    data: dict = field(default_factory=lambda: {"output": "Contract looks fine."})
    attachments: list = field(default_factory=list)
    duration_ms: int = 5
    error_category: str | None = None
    error: str | None = None
    our_chain_tail: str = "a" * 64
    peer_chain_tail: str = "b" * 64


class _FakeSender:
    def __init__(self):
        self.calls: list[dict] = []
        cfg = {"endpoint_id": "ep_acme", "instance_id": "iid-acme-0001", "enabled": True}
        self._registry = type("R", (), {"load": lambda _s, _e: dict(cfg)})()

    def send(self, endpoint_id, instruction, **kw):
        self.calls.append({"endpoint_id": endpoint_id, "instruction": instruction, **kw})
        r = _Res()
        r.task_id = kw["task_id"]
        return r


_WF = yaml.safe_dump({
    "awp": "1.1.0", "workflow": {"name": "peerwf", "description": "d"},
    "orchestration": {"engine": "dag", "graph": [
        {"id": "legal_review", "type": "peer_agent", "depends_on": [], "endpoint_id": "ep_acme",
         "agent_id": "reviewer", "instruction": "Please review this public text."},
        {"id": "digest", "type": "merge", "strategy": "concat_list", "depends_on": ["legal_review"],
         "inputs": ["legal_review.result"], "output": "digest"},
    ]}}, sort_keys=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from corvin_workflows import node_types as nt

    with _sandbox(tmp_path) as (client, home, tid):
        sender = _FakeSender()
        monkeypatch.setattr(nt, "_PEER_SENDER_FACTORY", lambda: sender)
        from forge import paths as _fp
        d = _fp.tenant_home(tid) / "workflows"
        d.mkdir(parents=True, exist_ok=True)
        (d / "peerwf.meta.json").write_text(json.dumps({"id": "peerwf", "name": "peerwf", "phase": "ready"}))
        (d / "peerwf.awp.yaml").write_text(_WF)
        yield client, home, tid, sender


def _chain(tid):
    from forge import paths as _fp
    p = _fp.tenant_home(tid) / "global" / "forge" / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def test_console_run_delegates_and_streams_node_events(env):
    client, _, tid, sender = env
    events: list = []

    def go():
        with client.stream("POST", f"{BASE}/peerwf/runs", json={"inputs": {}, "dry_run": False}) as r:
            events.append(("status", r.status_code, r.headers.get("X-Run-Id")))
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(60)
    assert not t.is_alive()
    assert events[0][1] == 200
    rid = events[0][2]
    done = {e["node_id"]: e for e in events[1:] if e.get("type") == "node_completed"}
    assert set(done) == {"legal_review", "digest"}
    assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True
    assert len(sender.calls) == 1 and sender.calls[0]["federation"]["parent_task_id"] == rid
    ev = [e["event_type"] for e in _chain(tid)]
    assert ev.index("workflow.peer_delegated") < ev.index("federation.task_delegated") \
        < ev.index("workflow.peer_completed")
    rec = [e for e in _chain(tid) if e["event_type"] == "workflow.peer_delegated"][0]
    assert rec["details"]["wid"] == "peerwf" and rec["details"]["rid"] == rid
    assert "Please review this public text" not in json.dumps(_chain(tid))
    # the stored run keeps the peer node's output as data
    run = client.get(f"{BASE}/peerwf/runs/{rid}").json()
    assert run["run"]["status"] == "complete"
    assert any("Contract looks fine." in json.dumps(e) for e in run["events"])


def test_readiness_route_and_synthetic_check_send_nothing(env):
    client, _, tid, sender = env
    r = client.get(f"{BASE}/peerwf/readiness")
    assert r.status_code == 200 and r.json()["synthetic"] is True
    body = r.json()
    reach = next(s for s in body["signals"] if s["name"] == "reachability")
    assert reach["status"] == "pass", body
    assert sender.calls == []
    ev = [e["event_type"] for e in _chain(tid)]
    assert "federation.task_delegated" not in ev and "workflow.peer_delegated" not in ev


def test_a_workflow_made_only_of_a_peer_node_is_run_by_the_dag_engine(env):
    """`peer_agent` is DAG-only: without that, the console's generic per-node executor would run
    it as an agent step on the local engine and nothing would reach the peer."""
    client, _, tid, sender = env
    from forge import paths as _fp

    d = _fp.tenant_home(tid) / "workflows"
    only = yaml.safe_dump({
        "awp": "1.1.0", "workflow": {"name": "peeronly", "description": "d"},
        "orchestration": {"engine": "dag", "graph": [
            {"id": "ask", "type": "peer_agent", "depends_on": [], "endpoint_id": "ep_acme",
             "agent_id": "reviewer", "instruction": "Please review this public text."}]}}, sort_keys=False)
    (d / "peeronly.meta.json").write_text(json.dumps({"id": "peeronly", "name": "peeronly", "phase": "ready"}))
    (d / "peeronly.awp.yaml").write_text(only)
    events: list = []
    with client.stream("POST", f"{BASE}/peeronly/runs", json={"inputs": {}, "dry_run": False}) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True
    assert len(sender.calls) == 1
