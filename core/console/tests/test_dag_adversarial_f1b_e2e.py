"""Adversarial DAG end-to-end, stage F1 (fault injection), part 2: runs against a lying LLM.

Same tier and harness as ``test_dag_adversarial_f1_e2e`` (recording ``claude`` shim, real console under
uvicorn, real gates, real runner). This file holds the run-time cases: a manager that lies about its
decision, the model's JSON contract broken in every way, operator pin against node value, caps on the
real wiring, cancel, concurrent runs and tenant isolation.

Run: .venv/bin/python -m pytest -q -o addopts="" -p no:cacheprovider core/console/tests/test_dag_adversarial_f1b_e2e.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parents[2] / "corvin_operator" / "bridges" / "shared"
for _p in (str(_HERE), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dag_adv_shim as ds  # noqa: E402
from test_dag_adversarial_f1_e2e import (  # noqa: E402
    DAG_MODEL_NODES, LEGACY_MODEL_NODES, agent, assert_chain_intact, doc, merge,
)
from test_node_model_console_e2e import _as_tenant, _chain_events, _make, _model_of  # noqa: E402
from dag_adv_console import fresh_sandbox, live_sandbox, module_console  # noqa: E402,F401


@pytest.fixture
def shim(tmp_path, monkeypatch):
    return ds.install(tmp_path, monkeypatch, mode="canned")


@pytest.fixture(autouse=True)
def _outbox(tmp_path, monkeypatch):
    """A `deliver`/`ask_human`/`answer` node writes to the bridge outbox, and without this variable to
    the LIVE repo outbox (node_types._write_outbox): every test of this file points it at a tmp dir."""
    d = tmp_path / "outbox"
    d.mkdir()
    monkeypatch.setenv("ADAPTER_OUTBOX", str(d))
    return d


def run_with_rid(client, wid: str, *, holder: dict | None = None) -> tuple[str, list[dict]]:
    """Start a run, read the SSE stream to its end; return (run id from the response header, events)."""
    events: list[dict] = []
    with client.stream("POST", f"/v1/console/workflows/{wid}/runs", json={"inputs": {}, "dry_run": False}) as r:
        assert r.status_code == 200, r.read()
        rid = r.headers["x-run-id"]
        if holder is not None:
            holder["rid"] = rid
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1].get("type") in ("run_completed", "error"):
                    break
    return rid, events


def node_events(events: list[dict], kind: str) -> dict[str, dict]:
    return {e["node_id"]: e for e in events if e.get("type") == kind and "node_id" in e}


def write_pin(home: Path, tid: str, **pins: str) -> Path:
    d = home / "tenants" / tid / "global"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["spec:", "  engine_models:", "    claude_code:"] + [f"      {k}: {v}" for k, v in pins.items()]
    f = d / "tenant.corvin.yaml"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    f.chmod(0o600)
    return f


# ── a manager LLM that lies about its decision (AD-11..AD-16, INV-D5, INV-M7) ──

def loop_graph(max_loops: int = 3, max_workers: int = 4) -> str:
    return doc([
        {"id": "d", "type": "delegation_loop", "agent": "a", "depends_on": [], "instructions": "MARK-D",
         "config": {"manager": "mgr", "budget": {"max_loops": max_loops, "max_total_workers": max_workers}}},
        merge("j", ["d"], ["d.terminal"]),
    ])


def manager_rule(*replies) -> dict:
    return {"match": {"kind": "dag_node", "prompt_regex": "MARK-D"},
            "action": {"sequence": [{"reply": r} for r in replies]}}


def worker_rule() -> dict:
    return {"match": {"kind": "dag_node", "prompt_regex": "WORKER"}, "action": {"reply": {"ok": True}}}


W = {"agent": "w", "instructions": "WORKER"}
# name, manager replies, node d fails?, expected manager calls, expected worker calls
MANAGER_LIES = [
    ("unknown_decision", [{"decision": "EXPLODE"}], False, 1, 0),
    ("no_decision_key", [{"foo": 1}], False, 1, 0),
    ("lowercase_decision", [{"decision": "delegate", "workers": [W]}], False, 1, 0),
    ("manager_returns_a_list", [[1, 2]], True, 1, 0),
    ("workers_is_a_string", [{"decision": "DELEGATE", "workers": "abc"}], True, 1, 0),
    ("workers_is_a_dict", [{"decision": "DELEGATE", "workers": {"agent": "x"}}], True, 1, 0),
    ("workers_is_a_number", [{"decision": "DELEGATE", "workers": 5}], True, 1, 0),
    ("worker_without_agent", [{"decision": "DELEGATE", "workers": [{"instructions": "WORKER"}]}], True, 1, 0),
    ("workers_null_runs_to_the_loop_cap", [{"decision": "DELEGATE", "workers": None}], False, 3, 0),
    ("confidence_is_text", [{"decision": "COMPLETE", "result": {"x": 1}, "confidence": "high"}], True, 1, 0),
    ("confidence_is_nan", [{"decision": "COMPLETE", "result": {"x": 1}, "confidence": float("nan")}], False, 1, 0),
    ("worker_names_a_model_it_may_not_have",
     [{"decision": "DELEGATE", "workers": [{**W, "model": "opus"}]},
      {"decision": "COMPLETE", "result": {"x": 1}}], False, 2, 1),
]


@pytest.mark.parametrize("case,replies,node_fails,managers,workers", MANAGER_LIES, ids=[c[0] for c in MANAGER_LIES])
def test_a_lying_manager_never_crashes_the_run_and_never_picks_its_workers_model(
        tmp_path, shim, case, replies, node_fails, managers, workers):
    shim.set_rules([manager_rule(*replies), worker_rule()])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", loop_graph())
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["type"] == "run_completed", (case, events[-3:])          # never a stream that just stops
        done, failed = node_events(events, "node_completed"), node_events(events, "node_failed")
        if node_fails:
            assert "d" in failed and events[-1]["ok"] is False, (case, events[-3:])
        else:
            assert "d" in done and events[-1]["ok"] is True, (case, events[-3:])
        rows = shim.nodes()
        assert len([r for r in rows if "MARK-D" in r["prompt"]]) == managers, case
        worker_rows = [r for r in rows if "WORKER" in r["prompt"] and "MARK-D" not in r["prompt"]]
        assert len(worker_rows) == workers, case
        # INV-M7: neither the manager nor a worker ran on a model the manager's spec named
        assert {_model_of(r["argv"]) for r in rows} <= {"haiku"}, [r["argv"] for r in rows]
        assert_chain_intact(tid)


def test_a_real_manager_sees_what_its_workers_returned_on_its_next_call__finding_h_dl_1(tmp_path, shim):
    """H-DL-1 (fixed 2026-10-29): _execute_delegation_loop documents that the next manager call 'sees the full
    history' (state['_iterations']), but ClaudeCliEngine._build_prompt dropped every state key that starts with
    '_', so a real manager never saw what its workers returned and got the same prompt each iteration. The
    StubEngine tests cannot see this: the stub reads call.state directly, the real engine builds a prompt."""
    marker = "CANARY-WORKER-RESULT-7f3a"
    shim.set_rules([
        {"match": {"kind": "dag_node", "prompt_regex": "MARK-D"},
         "action": {"sequence": [{"reply": {"decision": "DELEGATE", "workers": [W]}},
                                 {"reply": {"decision": "COMPLETE", "result": {"x": 1}}}]}},
        {"match": {"kind": "dag_node", "prompt_regex": "WORKER"}, "action": {"reply": {"finding": marker}}},
    ])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", loop_graph())
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True, events[-3:]
        managers = [r for r in shim.nodes() if "MARK-D" in r["prompt"] and "WORKER" not in r["prompt"].split("MARK-D")[0]]
        assert len(managers) == 2, "the manager must be called twice (delegate, then complete)"
        assert marker in managers[1]["prompt"], "the second manager prompt does not carry the workers' result"


def test_the_delegation_caps_hold_through_the_console_path_too(tmp_path, shim, monkeypatch):
    """INV-D5 on the real wiring: the constants live in node_types; patching them down proves the
    console run reaches the clamp (the production values are asserted in the F0 module)."""
    import corvin_workflows.node_types as nt
    monkeypatch.setattr(nt, "_MAX_DELEGATION_WORKERS", 3)
    monkeypatch.setattr(nt, "_MAX_DELEGATION_LOOPS", 2)
    many = [W for _ in range(50)]
    shim.set_rules([manager_rule({"decision": "DELEGATE", "workers": many}), worker_rule()])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", loop_graph(max_loops=10**9, max_workers=10**9))
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True, events[-3:]
        rows = shim.nodes()
        assert len([r for r in rows if "WORKER" in r["prompt"] and "MARK-D" not in r["prompt"]]) == 3
        assert len([r for r in rows if "MARK-D" in r["prompt"]]) <= 2


def test_fan_out_is_clamped_through_the_console_path(tmp_path, shim, monkeypatch):
    import corvin_workflows.node_types as nt
    monkeypatch.setattr(nt, "_MAX_FAN_OUT_ITEMS", 7)
    shim.set_rules([{"match": {"kind": "dag_node", "prompt_regex": "MARK-SRC"},
                     "action": {"reply": {"items": list(range(40))}}}])
    graph = doc([agent("src", instructions="MARK-SRC"),
                 {"id": "f", "type": "fan_out", "agent": "c", "depends_on": ["src"], "items_from": "src.items",
                  "instructions": "MARK-F"},
                 merge("j", ["f"], ["f.count"])])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", graph)
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is True, events[-3:]
        assert len([r for r in shim.nodes() if "MARK-F" in r["prompt"]]) == 7


# ── the model's JSON contract broken in every way it can be (AD-18, INV-R4) ──

CONTRACT = [
    ("list", {"reply": [1, 2]}, False),
    ("scalar", {"reply": "42"}, False),
    ("prose", {"reply": "I cannot help with that."}, False),
    ("two_objects", {"reply": '{"a": 1} and {"b": 2}'}, False),
    ("empty", {"reply": ""}, False),
    ("is_error_flag", {"reply": {"ok": True}, "is_error": True}, False),
    ("cli_exit_1", {"exit_code": 1, "stderr": "boom"}, False),
    ("fenced", {"reply": '```json\n{"ok": true}\n```'}, True),
    ("chatter_around", {"reply": 'Sure! {"ok": true} hope that helps'}, True),
    ("bom_prefixed", {"reply": '﻿{"ok": true}'}, True),
    ("nan_value", {"reply": '{"ok": NaN}'}, True),
]


@pytest.mark.parametrize("case,action,survives", CONTRACT, ids=[c[0] for c in CONTRACT])
def test_a_broken_json_contract_fails_the_node_once_and_does_not_retry(tmp_path, shim, case, action, survives):
    shim.set_rules([{"match": {"kind": "dag_node", "prompt_regex": "MARK-A\\b"}, "action": action}])
    graph = doc([agent("a"), merge("j", ["a"], ["a.ok"])])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", graph)
        before = len(_chain_events(tid, "engine.span.end"))
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["type"] == "run_completed", (case, events[-3:])
        assert events[-1]["ok"] is survives, (case, events[-3:])
        assert len([r for r in shim.nodes() if "MARK-A" in r["prompt"]]) == 1          # no hidden retry
        # every spawn that was opened has been closed (INV-R3), whatever the answer looked like
        assert len(_chain_events(tid, "engine.span.end")) - before == 1, case
        assert_chain_intact(tid)


def test_the_cli_reporting_another_model_than_requested_is_recorded_not_hidden(tmp_path, shim):
    """INV-M6: a CLI fallback answers on a different model. The span closes on the REPORTED model and the
    run stream shows it; the requested one stays visible in the resolution event."""
    from model_selector import tier_model
    shim.set_rules([{"match": {"kind": "dag_node", "prompt_regex": "MARK-A\\b"},
                     "action": {"reply": {"ok": True}, "report_model": "fallback-model-1"}}])
    graph = doc([agent("a", model="opus"), merge("j", ["a"], ["a.ok"])])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", graph)
        n_resolved = len(_chain_events(tid, "workflow.node_model_resolved"))
        rid, events = run_with_rid(client, "wf")
        done = node_events(events, "node_completed")["a"]
        assert done["model_id"] == "fallback-model-1"
        assert _model_of(shim.nodes()[0]["argv"]) == tier_model("opus")
        resolved = [d for d in _chain_events(tid, "workflow.node_model_resolved")[n_resolved:] if d["node_id"] == "a"]
        assert resolved and resolved[0]["model_id"] == tier_model("opus")
        assert _chain_events(tid, "engine.span.end")[-1]["model_id"] == "fallback-model-1"


# ── the operator pin outranks a node's own value, on both executors (AD-30, INV-M4) ──

def test_a_console_pin_outranks_the_node_value_and_removing_it_restores_the_node(tmp_path, shim):
    from model_selector import tier_model
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "legacy", LEGACY_MODEL_NODES)
        _make(tid, "dag", DAG_MODEL_NODES)
        shim.set_rules([{"match": {"kind": "legacy_node", "prompt_regex": "MARK-PLAIN"},
                         "action": {"reply": "alpha\nbeta"}}])
        pin = write_pin(home, tid, worker_model="claude-sonnet-5-5")
        n_resolved = len(_chain_events(tid, "workflow.node_model_resolved"))
        for wid in ("legacy", "dag"):
            events = run_with_rid(client, wid)[1]
            assert events[-1]["ok"] is True, (wid, events[-3:])
        pinned = shim.nodes()
        assert pinned and {_model_of(r["argv"]) for r in pinned} == {"claude-sonnet-5-5"}, \
            [(_model_of(r["argv"]), r["kind"]) for r in pinned]
        assert {d["source"] for d in _chain_events(tid, "workflow.node_model_resolved")[n_resolved:]} == {"pin"}
        shim.reset()
        pin.unlink()
        assert run_with_rid(client, "dag")[1][-1]["ok"] is True
        by_marker = {("MARK-WITH_MODEL" in r["prompt"]): _model_of(r["argv"]) for r in shim.nodes()}
        assert by_marker[True] == tier_model("opus") and by_marker[False] == "haiku"
        assert_chain_intact(tid)


# ── the console's own node executor runs without the host-access tools (AD-23) ──

HOST_ACCESS_TOOLS = {"Bash", "Edit", "Write", "Read", "Glob", "Grep"}


def test_every_spawn_of_the_legacy_node_executor_denies_the_host_access_tools(tmp_path, shim):
    """Found live (AD-23): `_run_node_claude` started `claude -p` with its tools, so a node whose text said
    'read <file> and copy it' read a host file into its output, which a later deliver node could send on. The
    DAG engine always denied them; this executor (agent, fan_out and the manager and workers of a
    delegation_loop) must too."""
    graph = doc([
        agent("plain"),
        {"id": "fan", "type": "fan_out", "agent": "c", "depends_on": ["plain"], "items_from": "plain.output",
         "instructions": "MARK-FAN"},
        {"id": "loop", "type": "delegation_loop", "agent": "d", "depends_on": [], "instructions": "MARK-LOOP",
         "config": {"manager": "m", "budget": {"max_loops": 1, "max_total_workers": 1}}},
    ])
    shim.set_rules([{"match": {"kind": "legacy_node", "prompt_regex": "MARK-PLAIN"}, "action": {"reply": "alpha\nbeta"}}])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", graph)
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["type"] == "run_completed", events[-3:]
        rows = shim.rows("legacy_node")
        assert len(rows) >= 4, "agent, two fan_out items and the loop's manager must all have spawned"
        for row in rows:
            argv = row["argv"]
            assert "--disallowedTools" in argv, argv
            denied = set(argv[argv.index("--disallowedTools") + 1].split())
            assert HOST_ACCESS_TOOLS <= denied, (sorted(HOST_ACCESS_TOOLS - denied), argv)


# ── cancel, concurrent runs, tenants (AD-40, AD-44, INV-R5, INV-S2) ──────────

SLOW_CHAIN = doc([agent("slow"), agent("next1", ["slow"]), agent("next2", ["next1"]),
                  merge("j", ["next2"], ["next2.ok"])])


def test_cancel_mid_run_stops_before_the_next_spawn_and_ends_exactly_once(tmp_path, shim):
    shim.set_rules([{"match": {"kind": "dag_node", "prompt_regex": "MARK-SLOW"},
                     "action": {"reply": {"ok": True}, "delay_s": 4}}])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", SLOW_CHAIN)
        holder: dict = {}
        result: dict = {}

        def _go():
            result["rid"], result["events"] = run_with_rid(client, "wf", holder=holder)

        t = threading.Thread(target=_go, daemon=True)
        t.start()
        deadline = time.time() + 20
        while time.time() < deadline and not holder.get("rid"):
            time.sleep(0.1)
        assert holder.get("rid"), "the run never started"
        # the shim writes its log row AFTER its delay: cancel while the first node is still answering
        time.sleep(1.5)
        assert shim.nodes() == [], "the first node must still be in flight when the cancel arrives"
        r = client.post(f"/v1/console/workflows/wf/runs/{holder['rid']}/cancel")
        assert r.status_code == 200, r.text
        t.join(timeout=30)
        assert not t.is_alive()
        events = result["events"]
        assert events[-1]["type"] in ("run_completed", "error"), events[-3:]
        assert events[-1].get("ok") is False, events[-3:]
        assert [r for r in shim.nodes() if "MARK-NEXT" in r["prompt"]] == []        # INV-R5
        terminals = [e for e in events if e.get("type") in ("run_completed", "error")]
        assert len(terminals) == 1                                                   # INV-D6
        assert_chain_intact(tid)


def test_two_runs_of_one_workflow_at_once_get_distinct_ids_and_both_finish(tmp_path, shim):
    shim.set_rules([{"match": {"kind": "dag_node"}, "action": {"reply": {"ok": True}, "delay_s": 1}}])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", doc([agent("a"), merge("j", ["a"], ["a.ok"])]))
        out: list = []

        def _go():
            try:
                out.append(run_with_rid(client, "wf"))
            except AssertionError as exc:               # a refusal (limit) is an acceptable answer
                out.append(("refused", str(exc)[:200]))

        threads = [threading.Thread(target=_go, daemon=True) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert len(out) == 2, out
        ran = [o for o in out if o[0] != "refused"]
        assert ran, out
        assert len({o[0] for o in ran}) == len(ran), "two runs must never share a run id"
        assert all(o[1][-1]["type"] == "run_completed" for o in ran)
        assert_chain_intact(tid)


def test_another_tenant_cannot_see_start_or_cancel_a_run_it_does_not_own(tmp_path, shim):
    with fresh_sandbox(tmp_path) as (client, home, tid):        # switches the client's session: its own console
        _make(tid, "wf", doc([agent("a"), merge("j", ["a"], ["a.ok"])]))
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["ok"] is True
        assert client.get(f"/v1/console/workflows/wf/runs/{rid}").status_code == 200
        _as_tenant(client, "tenant_b")
        for method, path in (("get", f"/v1/console/workflows/wf/runs/{rid}"),
                             ("get", "/v1/console/workflows/wf/yaml"),
                             ("post", f"/v1/console/workflows/wf/runs/{rid}/cancel"),
                             ("post", f"/v1/console/workflows/wf/runs/{rid}/resume")):
            kw = {"json": {"reply": "x"}} if path.endswith("resume") else {}
            r = getattr(client, method)(path, **kw)
            assert r.status_code in (403, 404, 422), (path, r.status_code, r.text[:200])
        n_before = shim.count("dag_node")
        r = client.post("/v1/console/workflows/wf/runs", json={"inputs": {}, "dry_run": False})
        assert r.status_code in (403, 404), r.status_code           # B has no workflow `wf`
        assert shim.count("dag_node") == n_before                    # nothing was spawned for B
