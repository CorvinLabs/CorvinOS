#!/usr/bin/env python3
"""Per-subtask E2E for the Phase-6 standards-only DAG-Walker
(``awp_dag_parser`` + ``awp_validator`` + ``awp_walker``).

Asserts the contract — no AWP-runtime, engines own execution:

  Parser:
    * happy-path dict + workflow_id / version / state.initial / budget /
      nodes propagate
    * unknown engine_id at parse time is allowed (registry check is
      runtime concern)
    * missing required fields raise ValueError
    * cyclic DAG raises ValueError on parse + on topological_order
    * topological_order respects depends_on, preserves source order on
      ties

  Validator:
    * R17 happy path: dict[worker_id]={confidence: 0..1, ...}
    * R17 fails: not a dict, missing key, missing confidence,
      out-of-range, non-numeric
    * BudgetTracker.charge accumulates correctly
    * validate_budget breaches per axis, zero=unbounded

  Walker:
    * 3-node sequential DAG with stub engines runs through, final_text
      extracted from output_node
    * fan-out (2 parallel-eligible nodes) runs both, results land in
      state under output_key
    * node failure (stub raises) aborts walk, partial results in
      records
    * R17 violation aborts walk by default
    * budget breach (time_s) aborts walk after the offending node
    * http execution_kind path uses injected http_caller, not the
      engine_factory
    * audit_writer is called on every node_complete / node_failed
    * worker_engine_factory IS injected into state (per ADR-0005
      convention) but never read back from awp.runtime

  Standards integrity:
    * No import of awp.* from any of the three modules

Run: python3 operator/bridges/shared/test_awp_walker.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import awp_dag_parser as dp  # type: ignore  # noqa: E402
import awp_validator as val  # type: ignore  # noqa: E402
import awp_walker as walker  # type: ignore  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


# ── Stub engines + http_caller ────────────────────────────────────────

class _EchoEngine:
    """Returns a static R17-shaped dict echoing the prompt under
    state[<id>]={confidence, output, ...}."""

    def __init__(self, node_id: str, confidence: float = 0.9):
        self._node_id = node_id
        self._confidence = confidence

    def spawn(self, *, prompt: str):
        return {self._node_id: {
            "confidence": self._confidence,
            "output": f"echo[{self._node_id}]: {prompt}",
        }}


class _FailingEngine:
    def spawn(self, *, prompt: str):
        raise RuntimeError("kaboom")


class _SlowEngine:
    """Idle for 0.2s — used to test budget time_s breach."""

    def __init__(self, node_id: str):
        self._node_id = node_id

    def spawn(self, *, prompt: str):
        import time as _t
        _t.sleep(0.2)
        return {self._node_id: {"confidence": 0.9, "output": "ok"}}


class _BadShapeEngine:
    """Returns a dict but missing the worker-id key — R17 violation."""

    def spawn(self, *, prompt: str):
        return {"wrong_key": {"confidence": 0.9}}


def _make_factory(node_to_engine: dict):
    def factory(engine_id: str | None = None):
        return node_to_engine.get(engine_id)
    return factory


# ── helpers for assertion ─────────────────────────────────────────────

class _AuditRecorder:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def __call__(self, event_type, severity, details):
        self.events.append((event_type, severity, details))


def main() -> int:
    # ── PARSER block ────────────────────────────────────────────────
    print("\n── parser: happy path ──────────────────────────────────")
    dag = dp.parse_dag_dict({
        "workflow": {"id": "weekly-report", "version": "1.0"},
        "state": {"initial": {"date": "2026-05-10"}, "output_node": "write"},
        "budget": {"tokens": 10000, "max_workers": 5},
        "dag": {
            "nodes": [
                {"id": "fetch",  "engine_id": "claude_code",
                 "prompt": "fetch data for {state.date}"},
                {"id": "analyse","engine_id": "claude_code",
                 "prompt": "analyse {state.fetch}",
                 "depends_on": ["fetch"]},
                {"id": "write",  "engine_id": "claude_code",
                 "prompt": "write report from {state.analyse}",
                 "depends_on": ["analyse"]},
            ],
        },
    })
    expect(dag.workflow_id == "weekly-report", "workflow_id parsed")
    expect(dag.version == "1.0", "version parsed")
    expect(dag.initial_state == {"date": "2026-05-10"},
           "initial_state propagates")
    expect(dag.budget.tokens == 10000, "budget tokens parsed")
    expect(len(dag.nodes) == 3, "3 nodes")
    expect(dag.output_node == "write", "output_node propagates")
    expect(dag.node_by_id("fetch").engine_id == "claude_code",
           "node_by_id works")

    # ── parser: schema violations ───────────────────────────────────
    print("\n── parser: schema violations raise ─────────────────────")
    bad_cases = [
        ({}, "missing workflow"),
        ({"workflow": {"id": ""}}, "empty workflow.id"),
        ({"workflow": {"id": "x"}, "dag": {"nodes": []}}, "empty nodes list"),
        ({"workflow": {"id": "x"}, "dag": {"nodes": [{"id": "n"}]}}, "node missing engine_id"),
        ({"workflow": {"id": "x"},
          "dag": {"nodes": [{"id": "n", "engine_id": "x", "prompt": ""}]}},
         "empty prompt"),
        ({"workflow": {"id": "x"},
          "dag": {"nodes": [{"id": "n", "engine_id": "x", "prompt": "p",
                             "execution_kind": "magic"}]}},
         "invalid execution_kind"),
        ({"workflow": {"id": "x"},
          "dag": {"nodes": [{"id": "a", "engine_id": "x", "prompt": "p",
                             "depends_on": ["b"]}]}},
         "depends_on unknown id"),
        ({"workflow": {"id": "x"},
          "state": {"output_node": "ghost"},
          "dag": {"nodes": [{"id": "a", "engine_id": "x", "prompt": "p"}]}},
         "output_node not in nodes"),
    ]
    for bad, label in bad_cases:
        raised = False
        try:
            dp.parse_dag_dict(bad)
        except ValueError:
            raised = True
        expect(raised, f"raises ValueError: {label}")

    # ── parser: cycle detection ─────────────────────────────────────
    raised = False
    try:
        dp.parse_dag_dict({
            "workflow": {"id": "cycle"},
            "dag": {"nodes": [
                {"id": "a", "engine_id": "x", "prompt": "p", "depends_on": ["b"]},
                {"id": "b", "engine_id": "x", "prompt": "p", "depends_on": ["a"]},
            ]},
        })
    except ValueError as e:
        raised = "cycle" in str(e).lower()
    expect(raised, "cycle detected at parse time")

    # ── parser: topological order ──────────────────────────────────
    print("\n── parser: topological order ───────────────────────────")
    fan_dag = dp.parse_dag_dict({
        "workflow": {"id": "fan"},
        "dag": {"nodes": [
            {"id": "root",  "engine_id": "x", "prompt": "p"},
            {"id": "left",  "engine_id": "x", "prompt": "p", "depends_on": ["root"]},
            {"id": "right", "engine_id": "x", "prompt": "p", "depends_on": ["root"]},
            {"id": "merge", "engine_id": "x", "prompt": "p",
             "depends_on": ["left", "right"]},
        ]},
    })
    order = [n.id for n in dp.topological_order(fan_dag)]
    expect(order[0] == "root", "topo: root first")
    expect(order[-1] == "merge", "topo: merge last")
    expect(order.index("left") < order.index("merge"), "topo: left before merge")
    expect(order.index("right") < order.index("merge"), "topo: right before merge")

    # ── VALIDATOR block ─────────────────────────────────────────────
    print("\n── validator: R17 happy + violations ───────────────────")
    ok, _ = val.validate_node_output({"foo": {"confidence": 0.9}}, "foo")
    expect(ok, "R17 happy: dict[id].confidence in range")
    ok, _ = val.validate_node_output({"foo": {"confidence": 0}}, "foo")
    expect(ok, "R17: confidence=0.0 ok")
    ok, _ = val.validate_node_output({"foo": {"confidence": 1.0}}, "foo")
    expect(ok, "R17: confidence=1.0 ok")
    ok, d = val.validate_node_output("not a dict", "foo")
    expect(not ok and "must be dict" in d, "R17 violation: non-dict")
    ok, d = val.validate_node_output({"bar": {"confidence": 0.5}}, "foo")
    expect(not ok and "missing key" in d, "R17 violation: wrong key")
    ok, d = val.validate_node_output({"foo": {}}, "foo")
    expect(not ok and "confidence missing" in d, "R17 violation: no confidence")
    ok, d = val.validate_node_output({"foo": {"confidence": 1.5}}, "foo")
    expect(not ok and "outside" in d, "R17 violation: out of range")
    ok, d = val.validate_node_output({"foo": {"confidence": "high"}}, "foo")
    expect(not ok and "numeric" in d, "R17 violation: non-numeric")

    # ── validator: budget tracking ──────────────────────────────────
    print("\n── validator: budget tracker ───────────────────────────")
    t = val.BudgetTracker()
    t.charge(tokens=100, time_s=0.5, workers=1)
    t.charge(tokens=200, time_s=1.0, workers=1)
    expect(t.tokens_spent == 300, "tokens accumulate")
    expect(abs(t.time_s_spent - 1.5) < 0.01, "time_s accumulate")
    expect(t.workers_spent == 2, "workers accumulate")

    b = dp.DAGBudget(tokens=250, time_s=0, max_workers=10)
    ok, breaches = val.validate_budget(t, b)
    expect(not ok, "budget: tokens 300>250 → breach")
    expect(any("tokens" in br for br in breaches), "breach mentions tokens")
    b2 = dp.DAGBudget()  # all zero = unbounded
    ok2, _ = val.validate_budget(t, b2)
    expect(ok2, "budget: all-zero envelope is unbounded")

    # ── WALKER block ────────────────────────────────────────────────
    print("\n── walker: 3-node sequential DAG ───────────────────────")
    seq_dag = dp.parse_dag_dict({
        "workflow": {"id": "seq"},
        "state": {"initial": {"topic": "AWP"}, "output_node": "write"},
        "dag": {"nodes": [
            {"id": "fetch", "engine_id": "claude_code", "prompt": "fetch {state.topic}"},
            {"id": "analyse", "engine_id": "claude_code", "prompt": "analyse {state.fetch}",
             "depends_on": ["fetch"]},
            {"id": "write", "engine_id": "claude_code", "prompt": "write report",
             "depends_on": ["analyse"]},
        ]},
    })
    factory = _make_factory({"claude_code": _EchoEngine("__")})
    # the stub engine was created with id "__" — but each node expects
    # its own id key. Build per-node factory:
    factory = lambda eid: _EchoEngine(_current_node_id["v"])
    _current_node_id = {"v": ""}

    def on_start(node):
        _current_node_id["v"] = node.id

    audit = _AuditRecorder()
    out = walker.walk(seq_dag, engine_factory=factory,
                      on_node_start=on_start, audit_writer=audit)
    expect(out.ok, "seq DAG: ok=True")
    expect(len(out.nodes) == 3, "seq DAG: 3 node records")
    expect(all(r.ok for r in out.nodes), "seq DAG: all node records ok")
    expect("echo[write]" in out.final_text,
           "seq DAG: final_text from output_node",
           f"got {out.final_text!r}")
    # State was carried through and prompts substituted:
    expect("fetch" in out.final_state, "fetch result in state")
    expect("analyse" in out.final_state, "analyse result in state")
    expect("write" in out.final_state, "write result in state")
    expect(out.final_state["worker_engine_factory"] is factory,
           "factory injected into state per ADR-0005 convention")
    expect(any(e[0] == "walker.node_complete" for e in audit.events),
           "audit: node_complete emitted")

    # ── walker: fan-out ────────────────────────────────────────────
    print("\n── walker: fan-out DAG ─────────────────────────────────")
    factory_fan = lambda eid: _EchoEngine(_current_node_id["v"])
    audit_fan = _AuditRecorder()
    out = walker.walk(fan_dag, engine_factory=factory_fan,
                      on_node_start=on_start, audit_writer=audit_fan)
    expect(out.ok, "fan DAG: ok=True")
    expect(len(out.nodes) == 4, "fan DAG: 4 records")
    expect(all(n in out.final_state for n in ("root", "left", "right", "merge")),
           "fan DAG: all node outputs in state")

    # ── walker: failing engine ──────────────────────────────────────
    print("\n── walker: failing engine aborts ───────────────────────")
    fail_factory = lambda eid: _FailingEngine()
    audit_fail = _AuditRecorder()
    out = walker.walk(seq_dag, engine_factory=fail_factory,
                      audit_writer=audit_fail)
    expect(not out.ok, "failing engine: ok=False")
    expect("kaboom" in out.aborted_at, "failure message recorded")
    expect(any(e[0] == "walker.node_failed" for e in audit_fail.events),
           "audit: node_failed emitted")

    # ── walker: R17 violation aborts ────────────────────────────────
    print("\n── walker: R17 violation aborts ────────────────────────")
    bad_factory = lambda eid: _BadShapeEngine()
    audit_bad = _AuditRecorder()
    out = walker.walk(seq_dag, engine_factory=bad_factory,
                      audit_writer=audit_bad)
    expect(not out.ok, "R17 violation: ok=False")
    expect("R17" in out.aborted_at, "R17 violation in aborted_at")
    expect(any(e[0] == "walker.r17_violation" for e in audit_bad.events),
           "audit: r17_violation emitted")

    # ── walker: budget time_s breach ────────────────────────────────
    print("\n── walker: budget time_s breach aborts ─────────────────")
    slow_dag = dp.parse_dag_dict({
        "workflow": {"id": "slow"},
        "budget": {"time_s": 1},  # 1 second budget
        "dag": {"nodes": [
            {"id": "n1", "engine_id": "x", "prompt": "p"},
            {"id": "n2", "engine_id": "x", "prompt": "p", "depends_on": ["n1"]},
            {"id": "n3", "engine_id": "x", "prompt": "p", "depends_on": ["n2"]},
            {"id": "n4", "engine_id": "x", "prompt": "p", "depends_on": ["n3"]},
            {"id": "n5", "engine_id": "x", "prompt": "p", "depends_on": ["n4"]},
            {"id": "n6", "engine_id": "x", "prompt": "p", "depends_on": ["n5"]},
        ]},
    })
    slow_factory = lambda eid: _SlowEngine(_current_node_id["v"])
    out = walker.walk(slow_dag, engine_factory=slow_factory,
                      on_node_start=on_start)
    expect(not out.ok, "budget breach: ok=False")
    expect(any("time_s" in br for br in out.budget_breaches),
           "breach detail mentions time_s",
           f"breaches={out.budget_breaches}")

    # ── walker: HTTP execution_kind ─────────────────────────────────
    print("\n── walker: http execution_kind ─────────────────────────")
    http_dag = dp.parse_dag_dict({
        "workflow": {"id": "http"},
        "dag": {"nodes": [
            {"id": "h1", "engine_id": "ignored",
             "execution_kind": "http", "model": "openai/gpt-4o-mini",
             "prompt": "summarise X"},
        ]},
    })
    http_calls: list[tuple[str, str]] = []
    def http_caller(*, prompt, model):
        http_calls.append((prompt, model))
        return {"h1": {"confidence": 0.85, "output": f"http[{model}]: {prompt}"}}
    out = walker.walk(http_dag,
                      engine_factory=lambda eid: None,  # would crash if called
                      http_caller=http_caller)
    expect(out.ok, "http path: ok=True")
    expect(len(http_calls) == 1, "http_caller invoked once")
    expect(http_calls[0][1] == "openai/gpt-4o-mini",
           "http_caller received model from node")

    # ── walker: missing http_caller for http node aborts ────────────
    out = walker.walk(http_dag,
                      engine_factory=lambda eid: None)
    expect(not out.ok, "http kind without http_caller: aborts")
    expect("http_caller" in out.aborted_at, "abort reason names http_caller")

    # ── INTEGRITY: no awp.runtime imports ───────────────────────────
    print("\n── integrity: no awp.runtime imports ──────────────────")
    src_files = [
        HERE / "awp_dag_parser.py",
        HERE / "awp_validator.py",
        HERE / "awp_walker.py",
    ]
    for f in src_files:
        text = f.read_text("utf-8")
        for forbidden in ("from awp.runtime", "import awp.runtime",
                          "from awp ", "import awp\n", "awp.AWPAgent"):
            expect(forbidden not in text,
                   f"{f.name} contains no '{forbidden.strip()}'",
                   f"found in {f.name}")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
