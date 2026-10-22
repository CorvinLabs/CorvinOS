"""Two-instance E2E: a workflow node `peer_agent` on instance A delegates a step to an agent on
instance B (ADR-2276 / PLAN-0963 S7).

Real everything on the wire, as in test_federation_cross_peer_e2e.py: B runs a real
``a2a_http_server`` on an ephemeral 127.0.0.1 port, A's node uses the REAL ``RemoteTriggerSender``
(HMAC-signed TaskEnvelope, instance pin, signed response). A's run is a real ``DAGRunner`` run with the
real node executor, outbound gate, delegation ledger and A's tenant audit chain. B's receiver writes to a
REAL hash chain of its own (a temp file, ``verify_chain`` checks it). Only the model call at the very end
of B's worker path is fake (it records what it was asked). The whole thing runs in a sandbox
CORVIN_HOME with an empty peer directory: no live peer, relay or endpoint is contacted.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
for p in (str(REPO / "core" / "workflows"), str(REPO / "corvin_operator" / "forge")):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_federation_cross_peer_e2e import (  # noqa: E402,F401  (fixture re-use)
    TENANT, _RecordingEngine, _chain_events, federation_pair,
)

from corvin_workflows import DAGRunner, StubEngine  # noqa: E402
from corvin_workflows import node_types as nt  # noqa: E402
from corvin_workflows.storage import WorkflowDoc, _parse_yaml  # noqa: E402
from forge import security_events as real_se  # noqa: E402


@pytest.fixture
def two(federation_pair, monkeypatch, tmp_path):
    """The harness pair, with B's receiver writing a real chain and A's node using A's real sender."""
    import spawn_gates

    ctx = federation_pair
    chain_b = tmp_path / "B" / "chain" / "audit.jsonl"
    chain_b.parent.mkdir(parents=True)
    se_b = ctx["se_b"]

    def write_b(path, event_type, **kw):
        se_b.events.append({"event_type": event_type, **kw})
        return real_se.write_event(chain_b, event_type, **{k: v for k, v in kw.items() if k != "path"})

    monkeypatch.setattr(se_b, "write_event", write_b)
    monkeypatch.setattr(se_b, "get_audit_chain_tail", lambda path: real_se.get_audit_chain_tail(chain_b))
    # A's sender writes A2A.* records and reads its chain tail through the same real chain as the node
    from core.paths import tenant_audit_chain

    chain_a = tenant_audit_chain(TENANT)
    chain_a.parent.mkdir(parents=True, exist_ok=True)
    se_a = ctx["sender"]._inst_forge_se
    monkeypatch.setattr(se_a, "write_event",
                        lambda path, event_type, **kw: real_se.write_event(chain_a, event_type, **kw))
    monkeypatch.setattr(se_a, "get_audit_chain_tail", lambda path: real_se.get_audit_chain_tail(chain_a))
    # B's own L44 refuses a marked instruction (its call carries channel="a2a"); A's outbound gate
    # (channel="workflow-peer") passes it, so the refusal provably comes from the PEER.
    monkeypatch.setattr(
        spawn_gates, "check_l44",
        lambda text, tenant, **kw: "[house-rules] refused by B" if (
            kw.get("channel") == "a2a" and "BLOCKED_BY_B" in text) else None,
    )
    monkeypatch.setattr(nt, "_PEER_SENDER_FACTORY", lambda: ctx["sender"])
    ctx["chain_b"] = chain_b
    return ctx


def _wf(graph_nodes: list[dict]) -> WorkflowDoc:
    return WorkflowDoc.from_dict(_parse_yaml(yaml.safe_dump({
        "awp": "1.1.0", "workflow": {"name": "wf_peer_e2e", "description": "d"},
        "orchestration": {"engine": "dag", "graph": graph_nodes}}, sort_keys=False)))


def _peer(nid="legal_review", instr="Please review this public contract.", **over):
    n = {"id": nid, "type": "peer_agent", "depends_on": [], "endpoint_id": "peer-B",
         "agent_id": "opus-code", "instruction": instr}
    n.update(over)
    return n


def _run(nodes, run_id=None):
    return DAGRunner(_wf(nodes), engine=StubEngine(), tenant_id=TENANT).run({}, run_id=run_id)


def _hashes(path: Path) -> set[str]:
    return {json.loads(x)["hash"] for x in path.read_text().splitlines() if x.strip()}


def test_workflow_delegates_to_the_peer_agent_and_both_chains_hold_the_hop(two):
    from core.paths import tenant_audit_chain

    run = _run([_peer(), {"id": "digest", "type": "merge", "strategy": "concat_list",
                          "inputs": ["legal_review.result"], "output": "digest",
                          "depends_on": ["legal_review"]}], run_id="e2erun0001")
    assert run.state == "complete", run.error
    out = run.nodes["legal_review"].output
    assert out["ok"] is True and out["untrusted"] is True and "done by claude-opus-5" in out["result"]
    assert out["peer_instance_id"] == two["iid_b"] and out["agent_address"] == f"agent://{two['iid_b']}/opus-code"
    assert len(_RecordingEngine.spawns) == 1, "the peer agent ran exactly once"
    assert run.nodes["digest"].output["digest"], "a downstream selector reads the peer text"

    a_chain = tenant_audit_chain(TENANT)
    ev = [e.get("event_type") for e in _chain_events(two["home"])]
    assert ev.index("workflow.peer_delegated") < ev.index("federation.task_delegated") \
        < ev.index("federation.task_result_received") < ev.index("workflow.peer_completed")
    # the hop is cited in BOTH chains by hash: our tail travelled in the envelope, the peer's came back signed
    assert out["our_chain_tail"] and out["peer_chain_tail"]
    assert out["peer_chain_tail"] in _hashes(two["chain_b"]), "the peer's anchor is a record of B's own chain"
    assert out["our_chain_tail"] in _hashes(a_chain), "the anchor A sent is a record of A's own chain"
    b_events = [json.loads(x)["event_type"] for x in two["chain_b"].read_text().splitlines() if x.strip()]
    assert "federation.task_received" in b_events and "federation.task_completed" in b_events
    for chain in (a_chain, two["chain_b"]):
        ok, problems = real_se.verify_chain(chain)
        assert ok, problems
    assert "Please review this public contract" not in a_chain.read_text(), "no instruction text in A's chain"
    assert "Please review this public contract" not in two["chain_b"].read_text()


def test_peer_refusal_fails_the_node_and_fail_branch_continues_the_rest(two):
    nodes = [
        _peer(instr="BLOCKED_BY_B please", retry={"error_strategy": "fail_branch"}),
        {"id": "after", "type": "merge", "strategy": "concat_list", "inputs": ["legal_review.result"],
         "output": "x", "depends_on": ["legal_review"]},
        {"id": "side", "type": "merge", "strategy": "concat_list", "inputs": [], "output": "y",
         "depends_on": []},
    ]
    run = _run(nodes, run_id="e2erun0002")
    assert run.state == "complete"
    assert run.nodes["legal_review"].status == "failed" and "peer_refused" in run.nodes["legal_review"].error
    assert run.nodes["after"].status == "skipped" and run.nodes["side"].status == "success"
    assert _RecordingEngine.spawns == [], "B refused before its worker started"
    done = [e for e in _chain_events(two["home"]) if e.get("event_type") == "workflow.peer_completed"][0]
    assert done["details"]["status"] == "rejected"
    for chain in (two["chain_b"],):
        ok, problems = real_se.verify_chain(chain)
        assert ok, problems


def test_classified_instruction_never_reaches_the_peer(two):
    run = _run([_peer(instr="[class:confidential] the customer list")], run_id="e2erun0003")
    assert run.state == "failed" and "classified_above_public" in run.error
    assert _RecordingEngine.spawns == [] and not two["chain_b"].exists() or \
        "task_received" not in two["chain_b"].read_text()
    ev = [e.get("event_type") for e in _chain_events(two["home"])]
    assert "workflow.peer_refused" in ev and "federation.task_delegated" not in ev


def test_a_repeated_run_id_never_runs_the_peer_agent_twice(two):
    assert _run([_peer()], run_id="e2erun0004").state == "complete"
    again = _run([_peer()], run_id="e2erun0004")
    assert again.state == "failed" and "outcome_unknown" in again.error
    assert len(_RecordingEngine.spawns) == 1
    ev = [e.get("event_type") for e in _chain_events(two["home"])]
    assert "workflow.peer_outcome_unknown" in ev


def test_capability_addressing_uses_fresh_catalog_agents_only(two):
    from core.federation.peer_catalog import PeerCatalog

    node = _peer(endpoint_id=None, agent_id=None, capability="analysis")
    node = {k: v for k, v in node.items() if v is not None}
    stale = _run([node], run_id="e2erun0005")
    assert stale.state == "failed" and "no_peer_agent" in stale.error, "no catalog yet: nothing to rank"
    PeerCatalog(TENANT).refresh("peer-B", sender=two["sender"])
    fresh = _run([node], run_id="e2erun0006")
    assert fresh.state == "complete", fresh.error
    assert fresh.nodes["legal_review"].output["agent_address"].endswith("/haiku-cheap"), "cheapest analysis agent"
    # age the snapshot beyond the TTL: the same node can no longer pick it
    pc = PeerCatalog(TENANT)
    snap = pc._snapshots()["peer-B"]
    pc._append({**snap, "fetched_at": time.time() - 10_000})
    aged = _run([node], run_id="e2erun0007")
    assert aged.state == "failed" and "no_peer_agent" in aged.error


def test_an_answer_from_another_instance_than_the_catalog_names_fails_the_node(two):
    from core.federation.peer_catalog import PeerCatalog

    pc = PeerCatalog(TENANT)
    pc.refresh("peer-B", sender=two["sender"])
    snap = pc._snapshots()["peer-B"]
    pc._append({**snap, "peer_instance_id": "iid-other-00000000"})
    run = _run([_peer()], run_id="e2erun0008")
    assert run.state == "failed" and "instance_id_mismatch" in run.error


def test_the_sandbox_contacted_only_the_local_peer(two, monkeypatch):
    """No live endpoint, relay or peer exists in this test's world: the only registered endpoint is
    the harness' loopback server."""
    import remote_trigger_sender as rts

    ids = rts.RemoteEndpointRegistry(two["sender"]._registry._dir).list_ids()
    assert ids == ["peer-B"]
    url = json.loads((two["sender"]._registry._dir / "peer-B.json").read_text())["url"]
    assert url.startswith("http://127.0.0.1:")
