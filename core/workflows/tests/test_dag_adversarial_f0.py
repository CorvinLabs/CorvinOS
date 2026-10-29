"""Adversarial DAG end-to-end, stage F0 (deterministic, no model, no network).

Plan: claude-playground/dag-e2e-adversarial-plan-2026-10-29/PLAN.md (sections 2, 3.1, 5).

* property tests of the runner against an INDEPENDENT reference oracle, with a fixed seed (the plan's
  replacement for ``hypothesis``, which is not installed);
* the validator never raises anything but ``WorkflowInvalid`` (A-10), whatever it is given;
* every hard cap with its PRODUCTION constant;
* what a lying manager can and cannot do to a ``delegation_loop`` and its workers;
* the call-site proofs: each mechanism the suite relies on has a production caller, and the
  mechanisms with none are named (negative controls);
* three findings kept as executable facts (H-DF-1 characterised, H-GW-1 and ADR-0771 as strict xfail
  or negative control) so a fix or a regression changes the suite instead of going unnoticed.
"""
from __future__ import annotations

import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "core" / "workflows"), str(_REPO / "corvin_operator" / "bridges" / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_workflows import DAGRunner, EngineCall, NODE_TYPES, WorkflowDoc, WorkflowInvalid, validate  # noqa: E402


# ── a scripted engine and builders ───────────────────────────────────────────

@dataclass
class ScriptedEngine:
    name: str = "scripted"
    script: Callable[[EngineCall], Any] = lambda call: {"ok": True}
    history: list[EngineCall] = field(default_factory=list)

    def spawn(self, call: EngineCall) -> dict[str, Any]:
        self.history.append(call)
        out = self.script(call)
        if isinstance(out, BaseException):
            raise out
        return out


def make_doc(nodes: list[dict], engine: str = "dag") -> WorkflowDoc:
    return WorkflowDoc.from_dict({
        "awp": "1.0.0", "workflow": {"name": "adv_f0", "description": "adversarial f0"},
        "orchestration": {"engine": engine, "graph": nodes}})


def agent(nid: str, deps=(), **extra) -> dict:
    return {"id": nid, "type": "agent", "agent": nid, "depends_on": list(deps), "instructions": nid, **extra}


def merge(nid: str, deps, inputs=None) -> dict:
    return {"id": nid, "type": "merge", "depends_on": list(deps), "strategy": "concat_list",
            "inputs": list(inputs if inputs is not None else [f"{d}.ok" for d in deps] or ["x"]), "output": "out"}


def run(nodes: list[dict], engine: ScriptedEngine | None = None, inputs: dict | None = None):
    doc = make_doc(nodes)
    validate(doc)
    eng = engine or ScriptedEngine()
    return DAGRunner(doc, engine=eng, tenant_id="_default").run(inputs=inputs or {}), eng


# ── property tests against an independent oracle (INV-D2, INV-D4, INV-D6) ────

def random_dag(rng: random.Random, n: int) -> list[dict]:
    """A valid random DAG of agent and merge nodes: edges only point to earlier nodes (acyclic by
    construction); merge nodes are the join points the skip rule treats specially."""
    nodes: list[dict] = []
    for i in range(n):
        pool = [f"n{j}" for j in range(i)]
        deps = sorted(rng.sample(pool, rng.randint(0, min(3, len(pool))))) if pool else []
        if deps and rng.random() < 0.3:
            nodes.append(merge(f"n{i}", deps))
        else:
            fail = rng.random() < 0.2
            nodes.append(agent(f"n{i}", deps, retry={"error_strategy": "fail_branch"}, **({"x_fail": True} if fail else {})))
    return nodes


def reference_outcome(nodes: list[dict]) -> tuple[set[str], set[str], set[str]]:
    """What the run must produce, computed WITHOUT the runner's code: (ran_ok, failed, skipped)."""
    by_id = {n["id"]: n for n in nodes}
    dead: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    for n in nodes:                      # nodes are listed in topological order by construction
        deps = n["depends_on"]
        if n["type"] == "merge":
            skip = bool(deps) and all(d in dead for d in deps)
        else:
            skip = any(d in dead for d in deps)
        if skip:
            skipped.add(n["id"])
            dead.add(n["id"])
        elif n.get("x_fail"):
            failed.add(n["id"])
            dead.add(n["id"])
    ok = {i for i in by_id} - failed - skipped
    return ok, failed, skipped


@pytest.mark.parametrize("seed", range(40))
def test_a_random_dag_runs_in_dependency_order_and_fails_exactly_where_the_reference_says(seed):
    rng = random.Random(seed)
    nodes = random_dag(rng, rng.randint(2, 14))

    def script(call: EngineCall):
        node = next(n for n in nodes if n["id"] == call.metadata["node_id"])
        return RuntimeError("injected failure") if node.get("x_fail") else {"ok": call.metadata["node_id"]}

    res, eng = run(nodes, ScriptedEngine(script=script))
    ok, failed, skipped = reference_outcome(nodes)
    got = {nid: r.status for nid, r in res.nodes.items()}
    assert {n for n, s in got.items() if s == "failed"} == failed, (seed, got)
    assert {n for n, s in got.items() if s == "skipped"} == skipped, (seed, got)
    assert {n for n, s in got.items() if s == "success"} == ok, (seed, got)
    assert res.state == "complete"                                    # fail_branch contains every failure
    # INV-D2: every executed agent node ran after all of its (executed) dependencies, and once
    started = [c.metadata["node_id"] for c in eng.history]
    assert len(started) == len(set(started)), "a node was spawned twice"
    pos = {nid: i for i, nid in enumerate(started)}
    for n in nodes:
        for d in n["depends_on"]:
            if n["id"] in pos and d in pos:
                assert pos[d] < pos[n["id"]], (seed, d, n["id"])
    terminal = [e for e in res.audit if e["event"] == "run.terminal"]
    assert len(terminal) == 1, "INV-D6: exactly one terminal record"


def test_abort_is_the_default_and_stops_the_run_at_the_first_failure():
    nodes = [agent("a"), agent("b", ["a"]), agent("c", ["b"])]
    res, eng = run(nodes, ScriptedEngine(script=lambda c: RuntimeError("x") if c.agent == "b" else {"ok": 1}))
    assert res.state == "failed" and "b" in (res.error or "")
    assert [c.agent for c in eng.history] == ["a", "b"]           # c never spawned


# ── the validator never raises anything but WorkflowInvalid (A-10) ───────────

def random_garbage_node(rng: random.Random) -> Any:
    kinds = [
        lambda: {"id": rng.choice(["a", "b", "A", "x y", "", None, 5, "n" * 100]),
                 "type": rng.choice(["agent", "merge", "code", "fan_out", "route", "nope", None, 7]),
                 "depends_on": rng.choice([[], ["a"], ["zzz"], "a", None, [1], {"a": 1}]),
                 "agent": rng.choice(["x", "", None, 3])},
        lambda: rng.choice([None, 1, "str", [], [1, 2], {}]),
        lambda: {"id": "a", "type": "route", "mode": rng.choice(["condition", "classify", "x", None])},
        lambda: {"id": "a", "type": "fan_out", "items_from": rng.choice(["a.b", "", None, 5])},
        lambda: {"id": "a", "type": "delegation_loop", "config": rng.choice([None, {}, {"manager": "m"}, "x", []])},
        lambda: {"id": "a", "type": "agent", "agent": "x", "retry": rng.choice(
            [None, {}, {"max_retries": -1}, {"max_retries": "x"}, {"retry_interval_s": float("inf")}, [], "x"])},
        lambda: {"id": "a", "type": "agent", "agent": "x", "model": rng.choice(
            ["opus", "nope", 5, [], {}, "--x", "", None])},
    ]
    return rng.choice(kinds)()


@pytest.mark.parametrize("seed", range(60))
def test_the_validator_answers_anything_with_workflow_invalid_never_another_exception(seed):
    rng = random.Random(1000 + seed)
    graph = [random_garbage_node(rng) for _ in range(rng.randint(0, 5))]
    orch = rng.choice([{"engine": "dag", "graph": graph}, {"engine": rng.choice(["chat", "x", None, 3]), "graph": graph},
                       {"graph": graph}, "not a mapping", None, {"engine": "dag", "graph": "oops"}])
    doc = WorkflowDoc(awp_version=rng.choice(["1.0.0", "x", "", None]), name=rng.choice(["ok_name", "", "Bad Name", None]),
                      description=rng.choice(["d", "", None, 5]), orchestration=orch if isinstance(orch, dict) else {},
                      raw={})
    if not isinstance(orch, dict):
        doc.orchestration = orch                     # type: ignore[assignment]
    try:
        validate(doc)
    except WorkflowInvalid as exc:
        assert exc.code.startswith("R"), exc.code
    # any other exception type fails the test on its own


def test_a_valid_random_dag_always_validates():
    for seed in range(40):
        validate(make_doc(random_dag(random.Random(seed), random.Random(seed).randint(2, 14))))


# ── hard caps, with the production constants (INV-D5) ────────────────────────

def test_fan_out_spawns_at_most_500_items_and_audits_the_clamp():
    nodes = [agent("src"), {"id": "f", "type": "fan_out", "agent": "c", "depends_on": ["src"],
                            "items_from": "src.items", "instructions": "f"}]
    eng = ScriptedEngine(script=lambda c: {"items": list(range(2000))} if c.agent == "src" else {"ok": 1})
    res, eng = run(nodes, eng)
    assert res.state == "complete"
    assert len([c for c in eng.history if c.agent == "c"]) == 500
    assert any(e["event"] == "node.fan_out_clamped" and e["requested"] == 2000 and e["cap"] == 500 for e in res.audit)


def test_delegation_loop_never_exceeds_100_loops_or_200_workers():
    spawned = {"manager": 0, "worker": 0}

    def script(call: EngineCall):
        if call.metadata.get("role") == "manager":
            spawned["manager"] += 1
            return {"decision": "DELEGATE", "workers": [{"agent": "w", "instructions": "w"}] * 10_000}
        spawned["worker"] += 1
        return {"ok": 1}

    node = {"id": "d", "type": "delegation_loop", "agent": "a", "depends_on": [],
            "config": {"manager": "m", "budget": {"max_loops": 10**9, "max_total_workers": 10**9}}}
    res, _ = run([node], ScriptedEngine(script=script))
    assert res.state == "complete"
    assert spawned["worker"] == 200 and spawned["manager"] <= 100
    assert any(e["event"] == "node.delegation_budget_clamped" for e in res.audit)


def test_retries_are_capped_at_ten_and_the_interval_at_sixty_seconds():
    from corvin_workflows.runner import _retry_config
    assert _retry_config({"retry": {"max_retries": 10**6, "retry_interval_s": 1e9}}) == (10, 60.0, "abort")
    nodes = [agent("a", retry={"max_retries": 10**6, "retry_interval_s": 0})]
    res, eng = run(nodes, ScriptedEngine(script=lambda c: RuntimeError("always")))
    assert res.nodes["a"].attempts == 11 and len(eng.history) == 11


def test_side_effecting_node_types_are_never_retried():
    from corvin_workflows.runner import _retry_config
    for t in ("deliver", "answer", "ask_human", "approval", "peer_agent"):
        assert _retry_config({"type": t, "retry": {"max_retries": 5}})[0] == 0, t


def test_a_3000_node_chain_validates_and_runs_without_recursion():
    nodes = [merge("n0", [], ["x"])] + [merge(f"n{i}", [f"n{i-1}"], [f"n{i-1}.out"]) for i in range(1, 3000)]
    res, eng = run(nodes)
    assert res.state == "complete" and len(res.nodes) == 3000 and eng.history == []   # INV-D8: no model call


# ── fan_out sources (H-FO-1, found by the live run of SC-02) ────────────────

FAN_OUT_NODE = {"id": "f", "type": "fan_out", "agent": "c", "depends_on": [], "items_from": "mails", "instructions": "f"}


@pytest.mark.xfail(strict=True, reason=(
    "H-FO-1a (open finding): fan_out resolves a bare `items_from` against the run STATE only. "
    "code_exec._resolve_selector documents the same grammar for every selector - 'a bare name checks "
    "workflow inputs first, then top-level state' - so `items_from: mails` with an input `mails` should "
    "fan out over it. Today it silently finds nothing. A fix flips this to XPASS on purpose."))
def test_fan_out_reads_a_bare_name_from_the_workflow_inputs_like_every_other_selector():
    res, eng = run([dict(FAN_OUT_NODE)], inputs={"mails": ["a", "b", "c"]})
    assert len(eng.history) == 3, f"{len(eng.history)} item(s) were processed"


@pytest.mark.xfail(strict=True, reason=(
    "H-FO-1b (open finding): a fan_out whose `items_from` names nothing that exists runs ZERO items and the "
    "run completes successfully - a typo is a silent no-op. A-01 closed the same class for selectors that "
    "name a graph node; a name that is no node at all is still accepted by the validator and by the runner."))
def test_fan_out_over_a_source_that_does_not_exist_fails_instead_of_succeeding_with_zero_items():
    res, eng = run([dict(FAN_OUT_NODE, items_from="no_such_source")])
    assert res.nodes["f"].status == "failed", (res.state, res.nodes["f"].status, res.nodes["f"].output)


def test_fan_out_over_an_empty_list_that_exists_is_a_legitimate_zero_item_success():
    """The control for H-FO-1b: an upstream node that really returned an empty list is not an error."""
    nodes = [agent("src"), {**FAN_OUT_NODE, "depends_on": ["src"], "items_from": "src.items"}]
    res, eng = run(nodes, ScriptedEngine(script=lambda c: {"items": []} if c.agent == "src" else {"ok": 1}))
    assert res.state == "complete" and res.nodes["f"].output == {"items": [], "count": 0}


# ── a lying manager, at the runner level (AD-13..AD-16, INV-M7) ──────────────

def loop_node(**cfg) -> dict:
    budget = {"max_loops": 3, "max_total_workers": 4}
    return {"id": "d", "type": "delegation_loop", "agent": "a", "depends_on": [],
            "config": {"manager": "m", "budget": budget, **cfg}}


def test_reserved_output_keys_cannot_be_overwritten_by_a_managers_result():
    result = {"_terminal": "evil", "terminal": "evil", "iterations": "evil", "workers_spawned": "evil", "extra": 1}
    res, _ = run([loop_node()], ScriptedEngine(script=lambda c: {"decision": "COMPLETE", "result": result}))
    out = res.nodes["d"].output
    assert isinstance(out["terminal"], dict) and out["terminal"]["state"] == "complete"
    assert isinstance(out["iterations"], list) and out["workers_spawned"] == 0
    assert out["extra"] == 1, "unreserved keys are lifted, by design"


def test_a_workers_model_field_never_reaches_the_engine_call():
    seen: list[EngineCall] = []

    def script(call: EngineCall):
        seen.append(call)
        if call.metadata.get("role") == "manager":
            return {"decision": "DELEGATE", "workers": [{"agent": "w", "instructions": "w", "model": "opus"}]}
        return {"ok": 1}

    run([loop_node()], ScriptedEngine(script=script))
    workers = [c for c in seen if c.metadata.get("role") == "worker"]
    assert workers and all(c.model == "" for c in workers), [c.model for c in workers]


def test_a_manager_can_override_workflow_inputs_for_its_workers__documented_behaviour():
    """FINDING F-INPUTS (informational): worker `inputs` from the manager are merged OVER the workflow
    inputs (`{**inputs, **w.inputs}`). The manager is the planner of the loop, so this is plausible by
    design; it is recorded here so a change in either direction is a decision, not an accident."""
    seen: list[EngineCall] = []

    def script(call: EngineCall):
        seen.append(call)
        if call.metadata.get("role") == "manager":
            return {"decision": "DELEGATE", "workers": [{"agent": "w", "instructions": "w", "inputs": {"topic": "evil"}}]}
        return {"ok": 1}

    run([loop_node()], ScriptedEngine(script=script), inputs={"topic": "harbour"})
    worker = next(c for c in seen if c.metadata.get("role") == "worker")
    assert worker.inputs["topic"] == "evil"


# ── H-DL-1 (fixed): what the manager of a delegation loop is told about its earlier iterations ──

def _loop_call(role: str, iterations, node_type: str = "delegation_loop") -> EngineCall:
    return EngineCall(agent="m", instructions="MARK-MGR", state={"_iterations": iterations, "x": {"k": 1}},
                      metadata={"node_id": "d", "node_type": node_type, "role": role})


ITER = [{"iteration": 1, "manager": {"decision": "DELEGATE", "workers": [{"agent": "w"}]},
         "workers": [{"finding": "CANARY-WORKER-RESULT"}]}]


def test_the_manager_prompt_carries_its_earlier_iterations_and_marks_them_as_data():
    from corvin_workflows.engines_claude import _build_prompt
    prompt = _build_prompt(_loop_call("manager", ITER))
    assert "CANARY-WORKER-RESULT" in prompt and "DELEGATE" in prompt
    assert "not instructions" in prompt, "worker results must be framed as data"
    assert prompt.index("CANARY-WORKER-RESULT") < prompt.index("Respond with ONLY"), "history comes before the contract line"


@pytest.mark.parametrize("role,node_type", [("worker", "delegation_loop"), ("manager", "agent"), ("", "agent")])
def test_only_the_manager_of_a_delegation_loop_gets_the_history(role, node_type):
    from corvin_workflows.engines_claude import _build_prompt
    prompt = _build_prompt(_loop_call(role, ITER, node_type))
    assert "CANARY-WORKER-RESULT" not in prompt and "Earlier iterations" not in prompt
    assert '"k": 1' in prompt, "the ordinary state block is unchanged"


def test_the_first_manager_call_has_no_history_block():
    from corvin_workflows.engines_claude import _build_prompt
    assert "Earlier iterations" not in _build_prompt(_loop_call("manager", []))
    assert "Earlier iterations" not in _build_prompt(_loop_call("manager", None))
    assert "Earlier iterations" not in _build_prompt(_loop_call("manager", "not a list"))


def test_the_history_is_bounded_keeps_the_newest_iterations_and_names_what_it_left_out():
    from corvin_workflows.engines_claude import _HISTORY_CAP_CHARS, _build_prompt
    many = [{"iteration": i, "manager": {"decision": "DELEGATE"}, "workers": [{"finding": f"R{i:03d}-" + "x" * 2000}]}
            for i in range(1, 201)]
    prompt = _build_prompt(_loop_call("manager", many))
    block = prompt.split("Earlier iterations", 1)[1]
    assert len(block) < _HISTORY_CAP_CHARS + 600
    assert "R200-" in prompt and "R001-" not in prompt, "the newest are kept, the oldest dropped"
    assert "older iteration(s) omitted" in prompt


def test_a_single_oversized_iteration_is_cut_not_dropped():
    from corvin_workflows.engines_claude import _HISTORY_CAP_CHARS, _build_prompt
    huge = [{"iteration": 1, "manager": {"decision": "DELEGATE"}, "workers": [{"finding": "y" * 100_000}]}]
    prompt = _build_prompt(_loop_call("manager", huge))
    assert "cut at the size limit" in prompt and len(prompt) < _HISTORY_CAP_CHARS + 2000


# ── H-DF-1: what a node's prompt state contains (characterised) ──────────────

def test_a_nodes_state_holds_every_earlier_node_output_not_only_its_dependencies__finding_h_df_1():
    """FINDING H-DF-1: `_execute_agent` gives the engine `state=dict(state)`, so a node sees the output of
    EVERY node that ran before it, also one it does not depend on (and the other node of its own level that
    sorts first). `share_output` is what limits what a node publishes. Whether prompts should be scoped to
    `depends_on` is an operator decision; this test pins today's behaviour and the knob that limits it."""
    nodes = [agent("a"), agent("b")]                                  # no edge between them
    res, eng = run(nodes, ScriptedEngine(script=lambda c: {"secret": f"CANARY-{c.agent}"}))
    b_call = next(c for c in eng.history if c.agent == "b")
    assert b_call.state.get("a") == {"secret": "CANARY-a"}            # an independent sibling's output is visible
    nodes_hidden = [agent("a", share_output=[]), agent("b")]
    res, eng = run(nodes_hidden, ScriptedEngine(script=lambda c: {"secret": f"CANARY-{c.agent}"}))
    assert next(c for c in eng.history if c.agent == "b").state.get("a") == {}      # share_output: [] hides it


# ── ATO classification (SC-13, ADR-0164) ─────────────────────────────────────

FICTIONAL_TASKS = [
    ("What is the capital of Norway?", "one_shot"),
    ("Fix the failing e2e test in the harbour report module and keep iterating until the bug is gone", "iterative_fix"),
    ("Research all files in the codebase of the Quellbach waterworks and run a parallel survey of the migration", "multi_agent"),
    ("Monitor the warehouse feed and alert me on a recurring schedule in the background", "autonomous"),
    ("Compare two architecture approaches for the mushroom farm and recommend one, weigh the trade-off", "exploration"),
]


@pytest.mark.parametrize("task,expected", FICTIONAL_TASKS, ids=[t[1] for t in FICTIONAL_TASKS])
def test_the_task_classifier_names_the_expected_execution_type(task, expected):
    from ato_classify import classify
    plan = classify(task, haiku_allowed=False)
    assert plan.task_type == expected, (task, plan)


def test_the_classifier_recommends_a_model_only_when_the_operator_allowed_it_and_only_for_one_shot():
    from ato_classify import classify
    one = "What is the capital of Norway?"
    assert classify(one, haiku_allowed=False).recommended_model is None
    assert classify(one, haiku_allowed=True).recommended_model == "haiku"
    assert classify("Fix the failing test and keep iterating", haiku_allowed=True).recommended_model is None
    assert classify("", haiku_allowed=True).recommended_model is None


# ── call-site proofs (e2e-wiring-proof, phase 1) ─────────────────────────────

def _production_sources() -> list[Path]:
    roots = [_REPO / "core", _REPO / "corvin_operator"]
    out: list[Path] = []
    for root in roots:
        for p in root.rglob("*.py"):
            parts = set(p.parts)
            if parts & {".venv", "node_modules", "tests", "__pycache__", ".claude", "worktrees"}:
                continue
            if p.name.startswith("test_") or p.name.endswith("_test.py"):
                continue
            out.append(p)
    return out


def _callers(pattern: str, *, outside: str) -> list[str]:
    rx = re.compile(pattern)
    hits = []
    for p in _production_sources():
        rel = str(p.relative_to(_REPO))
        if rel.startswith(outside):
            continue
        try:
            if rx.search(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(rel)
        except OSError:
            pass
    return hits


LIVE_MECHANISMS = [
    # name, regex of a real use, path prefix of the definition (excluded), a file that must be among the callers
    ("design-chat YAML planner", r"_generate_awp_yaml\b", "never-excluded-because-same-file", "routes/workflows.py"),
    ("node model resolution", r"model_resolution", "core/workflows/corvin_workflows/model_resolution.py", "corvin_workflows/node_types.py"),
    ("claude cli engine in console runs", r"ClaudeCliEngine", "core/workflows/corvin_workflows/engines_claude.py", "workflow_dag_run.py"),
    ("OS-turn routing record", r"route_and_record", "corvin_operator/bridges/shared/delegation_policy.py", "chat_runtime.py"),
    ("gateway intelligent router", r"IntelligentRouterBridge", "core/skills/os_skills/intelligent_router_integration.py", "dispatcher.py"),
    ("ATO classification", r"ato_classify", "corvin_operator/bridges/shared/ato_classify.py", "adapter.py"),
    ("ACS worker model resolution", r"_resolve_worker_model", "never-excluded-because-same-file", "acs_runtime.py"),
]


@pytest.mark.parametrize("name,rx,excluded,must", LIVE_MECHANISMS, ids=[m[0] for m in LIVE_MECHANISMS])
def test_every_mechanism_the_suite_relies_on_has_a_production_call_site(name, rx, excluded, must):
    hits = _callers(rx, outside=excluded)
    assert any(h.endswith(must) for h in hits), f"{name}: no production caller in {must}; callers: {hits[:6]}"


DEAD_MECHANISMS = [
    # Plan H-DEAD: no production importer. If one appears, the suite must say so: a dead mechanism
    # that became live needs its own end-to-end test and a place in the plan.
    ("core/task_engine (DAGPlanner, executor)", r"(from|import)\s+(core\.)?task_engine\b|task_engine\.(executor|dag_planner)", "core/task_engine/"),
    ("vibe_engineering VibeEngine.decompose", r"from\s+core\.vibe_engineering(\.vibe_engine)?\s+import\s+.*VibeEngine|vibe_engine import", "core/vibe_engineering/"),
    ("orchestration.workflow_engine", r"orchestration\.workflow_engine|from\s+\.workflow_engine\s+import", "core/orchestration/workflow_engine.py"),
]


@pytest.mark.parametrize("name,rx,excluded", DEAD_MECHANISMS, ids=[m[0] for m in DEAD_MECHANISMS])
def test_known_dead_mechanisms_still_have_no_production_importer(name, rx, excluded):
    hits = _callers(rx, outside=excluded)
    assert hits == [], f"{name} now has a production caller ({hits}); give it an end-to-end test and update the plan"


def test_adr_0771_worker_model_selector_is_still_not_built__negative_control():
    """ADR-0771 (proposed) names core/gateway/corvin_gateway/worker_model_selector.py and a
    `spec.worker_models.adaptive` flag. Neither exists: the idea lives on as ADR-0867's IntelligentRouter."""
    assert not (_REPO / "core/gateway/corvin_gateway/worker_model_selector.py").exists()
    assert _callers(r"worker_models\.adaptive|adaptive_worker_models", outside="never") == []


def test_every_node_type_the_planner_prompt_does_not_name_is_still_accepted_by_the_validator__finding_h_pl_2():
    """FINDING H-PL-2: the design-chat prompt offers 5 node types; the validator accepts all 11. A planner
    (or an injected task) can therefore produce a `code`, `peer_agent`, `route` or `answer` node. The
    containment tests (jail, design-time no-side-effect) are what carry that risk today."""
    src = (_REPO / "core/console/corvin_console/routes/workflows.py").read_text(encoding="utf-8")
    prompt = src.split('_AWP_YAML_PROMPT = """', 1)[1].split('"""', 1)[0]
    offered = set(re.search(r"Only these node types: ([a-z_ |]+)", prompt).group(1).replace(" ", "").split("|"))
    assert offered == {"agent", "fan_out", "delegation_loop", "deliver", "approval"}
    assert set(NODE_TYPES) - offered == {"code", "merge", "route", "answer", "ask_human", "peer_agent"}
    assert "model:" not in prompt, "H-PL-2: the planner prompt does not know the node `model:` field"


# ── H-GW-1: the gateway's prompt router against the operator's pin ───────────

def _write_pin(home: Path, **pins: str) -> None:
    d = home / "tenants" / "_default" / "global"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["spec:", "  engine_models:", "    claude_code:"] + [f"      {k}: {v}" for k, v in pins.items()]
    f = d / "tenant.corvin.yaml"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    f.chmod(0o600)


def test_the_gateway_dispatcher_honours_the_operator_pin_over_the_prompt_router(tmp_path, monkeypatch):
    """H-GW-1 (decided 2026-10-29, "pin vor router"): the dispatcher used to ask the IntelligentRouter FIRST
    and used spec.engine_models.<engine>.worker_model only when the router was unavailable or there was no
    prompt. The operator rule of 2026-10-13 says the Console selection is the model that runs."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    _write_pin(tmp_path, worker_model="claude-opus-5-5")
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    for prompt in ("Say hello", "Design a distributed analytics system with failover and cost controls. " * 80):
        chosen = RunDispatcher._resolve_worker_model("_default", "claude_code", prompt=prompt)
        assert chosen == "claude-opus-5-5", f"the router overrode the pin for {prompt[:20]!r}: {chosen!r}"


def test_a_provider_qualified_pin_is_normalised_before_it_reaches_the_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    _write_pin(tmp_path, worker_model="anthropic/claude-sonnet-5-5")
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    assert RunDispatcher._resolve_worker_model("_default", "claude_code", prompt="Say hello") == "claude-sonnet-5-5"


def test_a_pin_this_process_cannot_verify_still_passes_through_and_is_never_replaced_silently(tmp_path, monkeypatch):
    """ADR-0952: a pin is the operator's instruction. An id the process cannot verify (the model catalog is
    empty here, so availability is 'permissive') is handed on unchanged: a wrong one fails visibly at the
    CLI, dropping it would silently run a different model, which is the defect the Models console had."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    _write_pin(tmp_path, worker_model="claude-no-such-model-9")
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    chosen = RunDispatcher._resolve_worker_model("_default", "claude_code", prompt="Write a hello world function")
    assert chosen == "claude-no-such-model-9", chosen


def test_a_pin_the_catalog_knows_to_be_missing_falls_through_to_the_router(tmp_path, monkeypatch):
    """When the catalog IS known and lacks the pinned id (a retired model), the pin is skipped with the
    warning the fallback block writes, and the router answers as it did before this change."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    _write_pin(tmp_path, worker_model="claude-retired-model-1")
    import engine_models
    monkeypatch.setattr(engine_models, "model_catalog", {"claude-haiku-4-5", "claude-sonnet-5-5"}, raising=False)
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    chosen = RunDispatcher._resolve_worker_model("_default", "claude_code", prompt="Write a hello world function")
    assert chosen != "claude-retired-model-1" and "haiku" in chosen, chosen


def test_without_a_pin_the_router_still_chooses_by_prompt_weight(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    assert "haiku" in RunDispatcher._resolve_worker_model("_default", "claude_code", prompt="Say hello")


@pytest.mark.xfail(strict=True, reason=(
    "H-GW-2 (open finding): core/skills/os_skills/intelligent_router.py hardcodes claude-haiku-4-5, "
    "claude-sonnet-5 and claude-opus-5 while model_selector.tier_model() resolves the newest of each "
    "family (5-5). ADR-2090 and CLAUDE.md: never hardcode an id where tier_model() exists."))
def test_the_gateway_router_names_the_newest_model_of_the_family_it_picks(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    from model_selector import tier_model
    chosen = RunDispatcher._resolve_worker_model("_default", "claude_code", prompt="Say hello")
    assert chosen == tier_model("haiku"), chosen


def test_the_gateway_router_orders_the_families_by_prompt_weight(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    light = RunDispatcher._resolve_worker_model("_default", "claude_code", prompt="Write a hello world function")
    heavy = RunDispatcher._resolve_worker_model(
        "_default", "claude_code",
        prompt="Design a distributed analytics system with failover, schema evolution and cost controls. " * 80)
    assert "haiku" in light and "opus" in heavy, (light, heavy)


# ── ACS worker model: the six-step order (INV-M8, function level) ────────────

ACS_LAYERS = ["explicit", "env", "pin", "default", "anthropic", "none"]


@pytest.mark.parametrize("winner", ACS_LAYERS)
def test_acs_worker_model_takes_the_first_source_that_has_a_value(tmp_path, monkeypatch, winner):
    """Every lower source is set too, so a wrong order shows as the wrong winner. FUNCTION-LEVEL proof of
    the order table (plan C2); the live spawn of each step is not part of this stage."""
    import acs_runtime
    from model_selector import tier_model
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    for var in ("CORVIN_ACS_WORKER_MODEL", "ANTHROPIC_MODEL", "CORVIN_ENGINE_ID"):
        monkeypatch.delenv(var, raising=False)
    order = ACS_LAYERS
    active = set(order[order.index(winner):])
    if "env" in active:
        monkeypatch.setenv("CORVIN_ACS_WORKER_MODEL", "env-model-1")
    if "anthropic" in active:
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    cfg = ["spec:"]
    if "default" in active:
        cfg.append("  default_worker_model: claude-sonnet-5-5")
    if "pin" in active:
        cfg += ["  engine_models:", "    claude_code:", "      worker_model: claude-opus-5-5"]
    d = tmp_path / "tenants" / "_default" / "global"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "tenant.corvin.yaml"
    f.write_text("\n".join(cfg) + "\n", encoding="utf-8")
    f.chmod(0o600)
    explicit = "explicit-model-1" if "explicit" in active else None
    expected = {"explicit": "explicit-model-1", "env": "env-model-1", "pin": "claude-opus-5-5",
                "default": "claude-sonnet-5-5", "anthropic": "claude-haiku-4-5", "none": tier_model("haiku")}[winner]
    assert acs_runtime._resolve_worker_model(explicit, "_default") == expected


# ── OS-turn model: classifier tiers and the pin (SC-16, INV-M10, function level) ──

def test_the_os_turn_resolver_follows_the_complexity_policy_and_a_pin_outranks_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("CORVIN_OS_MODEL_AUTOSELECT", raising=False)
    import model_selector as ms
    work = "Please update the config value in settings.yaml to 30"
    adr = "Review ADR-0952 and write an architecture decision record about the routing trade-off"
    assert ms.resolve_os_model(None, task_input=adr) == ms.tier_model("opus")      # operator policy 2026-09-27
    assert ms.resolve_os_model(None, task_input=work) == ms.tier_model("sonnet")
    monkeypatch.setenv("CORVIN_OS_MODEL_AUTOSELECT", "off")
    assert ms.resolve_os_model(None, task_input=adr) != ms.tier_model("opus")       # the kill switch includes Tier 2.9
    monkeypatch.delenv("CORVIN_OS_MODEL_AUTOSELECT")
    _write_pin(tmp_path, os_model="claude-haiku-5-5")
    assert ms.resolve_os_model(None, task_input=adr) == "claude-haiku-5-5"          # a pin wins on every complexity


def test_the_gateway_dispatcher_falls_back_to_the_pin_when_there_is_no_prompt(tmp_path, monkeypatch):
    """The router is only consulted when a prompt exists; without one the operator's value is used."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    _write_pin(tmp_path, worker_model="claude-sonnet-5-5")
    from core.gateway.corvin_gateway.dispatcher import RunDispatcher
    assert RunDispatcher._resolve_worker_model("_default", "claude_code", prompt="") == "claude-sonnet-5-5"
