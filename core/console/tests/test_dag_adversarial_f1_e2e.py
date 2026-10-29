"""Adversarial DAG end-to-end, stage F1 (fault injection): plan sections 3, 4 and 5.

The console runs for real (uvicorn over a socket, a real session, the real pre-spawn gates, the real
runner and validators, a real ``claude`` subprocess per node). Only the LLM's ANSWER is given: a
recording shim (``dag_adv_shim``, oracle O1) sits first on ``PATH`` and answers from a rules file.
This tier is fault injection, not a real-LLM test; the live tier is ``test_dag_adversarial_live_e2e``.

Everything runs in a sandboxed ``CORVIN_HOME``; nothing touches the live install.

Run: .venv/bin/python -m pytest -q -o addopts="" -p no:cacheprovider core/console/tests/test_dag_adversarial_f1_e2e.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parents[2] / "corvin_operator" / "bridges" / "shared"
for _p in (str(_HERE), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dag_adv_shim as ds  # noqa: E402
from test_node_model_console_e2e import (  # noqa: E402
    _as_tenant, _chain_events, _make, _model_of, _run,
)
from dag_adv_console import fresh_sandbox, live_sandbox, module_console  # noqa: E402,F401


# ── helpers ──────────────────────────────────────────────────────────────────

def doc(nodes: list[dict], name: str = "adv_wf") -> str:
    return yaml.safe_dump(
        {"awp": "1.0.0", "workflow": {"name": name, "description": "adversarial fixture"},
         "orchestration": {"engine": "dag", "graph": nodes}},
        sort_keys=False)


def agent(nid: str, deps: list[str] | None = None, **extra) -> dict:
    n = {"id": nid, "type": "agent", "agent": "a", "depends_on": deps or [], "instructions": f"MARK-{nid.upper()}"}
    n.update(extra)
    return n


def merge(nid: str, deps: list[str], inputs: list[str]) -> dict:
    return {"id": nid, "type": "merge", "depends_on": deps, "strategy": "concat_list",
            "inputs": inputs, "output": "out"}


def assert_chain_intact(tid: str) -> None:
    """INV-S3: the tenant's hash chain verifies (the verifier the boot tripwire uses)."""
    import audit  # corvin_operator/bridges/shared
    from forge import paths as fp
    path = Path(fp.tenant_audit_chain(tid))
    if not path.exists():
        return
    ok, problems = audit.verify_audit(path)
    assert ok, problems


def put_yaml(client, wid: str, text: str):
    return client.put(f"/v1/console/workflows/{wid}/yaml", json={"yaml": text})


@pytest.fixture
def shim(tmp_path, monkeypatch):
    return ds.install(tmp_path, monkeypatch, mode="canned")


# ── O1/O2/O3 agree on both executors (INV-M1, INV-M6, plan 3.3) ──────────────

LEGACY_MODEL_NODES = doc([
    agent("with_model", model="opus"),
    agent("plain"),
    {"id": "fan", "type": "fan_out", "agent": "c", "depends_on": ["plain"], "items_from": "plain.output",
     "instructions": "MARK-FAN"},
])

DAG_MODEL_NODES = doc([
    agent("with_model", model="opus"),
    agent("plain"),
    merge("join", ["with_model", "plain"], ["with_model.ok", "plain.ok"]),
])


def test_three_oracles_agree_on_the_model_of_every_spawn_on_both_executors(tmp_path, shim):
    from model_selector import tier_model
    # the console's own executor takes plain text; its fan_out items are the lines of the upstream text
    shim.set_rules([{"match": {"kind": "legacy_node", "prompt_regex": "MARK-PLAIN"},
                     "action": {"reply": "alpha\nbeta"}}])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "legacy", LEGACY_MODEL_NODES)
        _make(tid, "dag", DAG_MODEL_NODES)
        n_resolved = len(_chain_events(tid, "workflow.node_model_resolved"))
        n_ends = len(_chain_events(tid, "engine.span.end"))
        for wid in ("legacy", "dag"):
            events = _run(client, wid)
            assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True, (wid, events[-3:])
        resolved = {(d["wid"], d["node_id"], d.get("item_index")): d
                    for d in _chain_events(tid, "workflow.node_model_resolved")[n_resolved:]}
        ends = _chain_events(tid, "engine.span.end")[n_ends:]
        nodes = shim.nodes()
        # O1 (what argv carried) against O2 (what the product says it resolved) per spawn
        for row in nodes:
            marker = next(m for m in ("MARK-WITH_MODEL", "MARK-PLAIN", "MARK-FAN") if m in row["prompt"])
            requested = _model_of(row["argv"])
            if marker == "MARK-WITH_MODEL":
                assert requested == tier_model("opus"), row["argv"]
            elif marker == "MARK-PLAIN" and row["kind"] == "dag_node":
                assert requested == "haiku"                 # the DAG engine's own alias
            elif marker == "MARK-PLAIN":
                assert requested is None                    # the legacy executor's own argv
            # O3 (what the CLI says answered) is what the span closes on: never the request
            assert row["reported_model"] == (requested or "cli-default-model")
        assert {k[1] for k in resolved} == {"with_model", "plain", "fan"}
        assert resolved[("legacy", "with_model", None)]["source"] == "node"
        assert resolved[("dag", "with_model", None)]["model_id"] == tier_model("opus")
        # every node spawn has exactly one closed span, on the model the shim reported
        assert len(ends) == len(nodes)
        assert sorted(d["model_id"] for d in ends) == sorted(r["reported_model"] for r in nodes)
        assert_chain_intact(tid)


# ── hostile documents through the real save route (AD-02..AD-09) ─────────────

def _chain(n: int) -> list[dict]:
    return [agent(f"n{i}", [f"n{i-1}"] if i else []) for i in range(n)]


HOSTILE = [
    ("alias_bomb", "awp: '1.0.0'\nworkflow: &a {name: x, description: y}\nb: *a\n", 400, "aliases"),
    ("duplicate_ids", doc([agent("a"), agent("a")]), 400, "duplicate node id"),
    ("uppercase_id", doc([agent("Bad")]), 400, "node id invalid"),
    ("space_id", doc([agent("a b")]), 400, "node id invalid"),
    ("traversal_id", doc([agent("../x")]), 400, "node id invalid"),
    ("reserved_state_key_id", doc([agent("__run_id__")]), 400, "node id invalid"),
    ("unicode_id", doc([agent("münchen")]), 400, "node id invalid"),
    ("unknown_type", doc([{"id": "a", "type": "teleport", "depends_on": []}]), 400, "unknown type"),
    ("missing_agent", doc([{"id": "a", "type": "agent", "depends_on": [], "instructions": "x"}]), 400, "agent"),
    ("dangling_dependency", doc([agent("a", ["ghost"])]), 400, "unknown id"),
    ("depends_on_is_string", doc([agent("a", "b")]), 400, "depends_on"),
    ("self_cycle", doc([agent("a", ["a"])]), 400, "cycle"),
    ("three_cycle", doc([agent("a", ["c"]), agent("b", ["a"]), agent("c", ["b"])]), 400, "cycle"),
    ("selector_outside_chain", doc([agent("a"), agent("b"), merge("m", ["a"], ["b.ok"])]), 400, "depends_on chain"),
    ("branch_without_route", doc([agent("a"), agent("b", ["a"], branch="x")]), 400, "route"),
    ("graph_not_a_list", "awp: '1.0.0'\nworkflow: {name: x, description: y}\norchestration: {engine: dag, graph: {a: 1}}\n", 400, "list"),
    ("graph_entry_not_a_mapping", "awp: '1.0.0'\nworkflow: {name: x, description: y}\norchestration: {engine: dag, graph: [1, 2]}\n", 400, "mapping"),
    ("engine_not_a_string", "awp: '1.0.0'\nworkflow: {name: x, description: y}\norchestration: {engine: [dag], graph: []}\n", None, None),
    ("model_on_merge", doc([agent("a"), {**merge("m", ["a"], ["a.ok"]), "model": "opus"}]), 400, "take no 'model'"),
    ("model_on_delegation_loop",
     doc([{"id": "d", "type": "delegation_loop", "depends_on": [], "model": "opus",
           "config": {"manager": "m", "budget": {"max_loops": 1, "max_total_workers": 1}}}]), 400, "take no 'model'"),
    ("model_on_condition_route",
     doc([agent("a"), {"id": "r", "type": "route", "mode": "condition", "depends_on": ["a"], "model": "opus",
                       "cases": [{"id": "d", "when": "default"}]}]), 400, "never calls a model"),
    ("too_many_nodes", doc(_chain(201)), 400, "at most 200 nodes"),
]


@pytest.mark.parametrize("case,text,status,needle", HOSTILE, ids=[c[0] for c in HOSTILE])
def test_a_hostile_document_is_refused_with_a_named_reason_and_nothing_is_written(
        tmp_path, shim, case, text, status, needle):
    with live_sandbox(tmp_path) as (client, home, tid):
        good = doc([agent("keep")])
        _make(tid, "wf", good)
        from forge import paths as fp
        path = fp.tenant_home(tid) / "workflows" / "wf.awp.yaml"
        before = path.read_text()
        r = put_yaml(client, "wf", text)
        if status is None:                       # shape the contract only requires to be a controlled answer
            assert r.status_code in (200, 400, 422), r.text
        else:
            assert r.status_code == status, (case, r.status_code, r.text[:300])
            assert needle in r.text, (case, r.text[:300])
        if r.status_code != 200:
            assert path.read_text() == before, "a refused document must leave the file unchanged (INV-D1)"
        assert shim.count() == 0, "saving a document never spawns anything"
        assert_chain_intact(tid)


def test_a_graph_of_exactly_200_nodes_is_accepted_and_a_long_valid_chain_needs_no_recursion(tmp_path, shim):
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", doc([agent("keep")]))
        r = put_yaml(client, "wf", doc(_chain(200)))
        assert r.status_code == 200, r.text[:300]
        assert len(r.json()["graph"]) == 200
        assert shim.count() == 0


def test_a_document_over_256_kib_is_refused_by_the_request_model_and_by_the_validator(tmp_path, shim):
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", doc([agent("keep")]))
        big = doc([agent("a", instructions="x" * (300 * 1024))])
        r = put_yaml(client, "wf", big)
        assert r.status_code in (413, 422), r.status_code
        assert shim.count() == 0


# ── invented and malformed `model:` values (AD-08, INV-M2) ───────────────────

MUST_REJECT = ["sonnet-9", "claude-opus-99", "GPT-5", "opus --dangerously-skip-permissions",
               "opus; rm -rf /", "--model", "-p", "../opus", "opus\nsonnet", "nonsense/opus", 5, [], ["opus"],
               {"a": "b"}, True]
MAY_ACCEPT = ["opus", "  opus ", "haiku", "sonnet", "OPUS", "anthropic/claude-opus-5-5", "claude-opus-5-5"]


@pytest.mark.parametrize("value", MUST_REJECT, ids=[repr(v)[:32] for v in MUST_REJECT])
def test_an_invented_model_value_is_rejected_at_save_time_and_never_replaced(tmp_path, shim, value):
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", doc([agent("keep")]))
        r = put_yaml(client, "wf", doc([agent("a", model=value)]))
        assert r.status_code == 400, (value, r.status_code, r.text[:200])
        assert "model" in r.text.lower()
        assert shim.count() == 0


@pytest.mark.parametrize("value", MAY_ACCEPT, ids=[repr(v) for v in MAY_ACCEPT])
def test_a_model_value_that_is_accepted_is_stored_verbatim_never_substituted(tmp_path, shim, value):
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", doc([agent("keep")]))
        r = put_yaml(client, "wf", doc([agent("a", model=value)]))
        assert r.status_code in (200, 400), r.text[:200]
        if r.status_code == 200:
            stored = yaml.safe_load(client.get("/v1/console/workflows/wf/yaml").json()["yaml"])
            assert stored["orchestration"]["graph"][0]["model"] == value   # no silent replacement


# ── an oversized upstream output must fail the next node in a controlled way (AD-46) ──

def test_an_upstream_output_over_the_argument_limit_fails_the_next_dag_node_and_closes_its_span(tmp_path, shim):
    """The DAG engine passes the prompt as ONE argv element (kernel limit 128 KiB per element); a node
    whose upstream state is larger must fail with a named reason, spawn nothing and close its span."""
    shim.set_rules([{"match": {"kind": "dag_node", "prompt_regex": "MARK-BIG"},
                     "action": {"reply": {"blob": "x" * 200_000}}}])
    graph = doc([agent("big"), agent("next", ["big"]), merge("join", ["big"], ["big.blob"])])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", graph)
        before = len(_chain_events(tid, "engine.span.end"))
        events = _run(client, "wf")
        failed = [e for e in events if e.get("type") == "node_failed" and e.get("node_id") == "next"]
        assert failed, events[-4:]
        assert "argument limit" in json.dumps(failed[0]) or "E2BIG" in json.dumps(failed[0]), failed[0]
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is False
        assert [r for r in shim.nodes() if "MARK-NEXT" in r["prompt"]] == []     # nothing reached the CLI
        errs = [d for d in _chain_events(tid, "engine.span.end")[before:] if d["status"] == "error"]
        assert len(errs) == 1, "the span opened for the refused spawn must end in error, exactly once"
        assert_chain_intact(tid)
