"""Workflow readiness report: "ready" is measured, not claimed (ADR-2275).

`check_readiness()` takes the workflow YAML text, an optional stored acceptance case and the
designer-repair count, and returns a `ReadinessReport`. It writes nothing; the only state it
reads besides its arguments is the tenant's `worker_model` pin and the workflow-node site default,
for the informational `node_model` hint (ADR-2308).
It validates the draft (R1..R11), runs it ONCE through the real `DAGRunner` against a
deterministic `StubEngine`, and evaluates the acceptance case on that run.

The run is synthetic and says so (`ReadinessReport.synthetic`). It proves structure
and the acceptance case, never real execution: prompts, credentials, rate limits and
the channels behind `deliver`/`answer`/`ask_human`/`approval` are NOT exercised. Those
five node types (with `peer_agent`: nothing is sent to a peer) are replaced for this run by recording stubs (their config is already
validated by R10), so the check never writes an outbox envelope and never pauses.
`code` nodes keep their own sandbox (they are deterministic by contract).

Acceptance case (stored by the console under `meta["acceptance"]`)::

    {"inputs": {...},
     "stub_responses": {"<agent or node id>": {...}},
     "expect": {"state": "complete",
                "nodes_reached": ["<node id>", ...],
                "final_contains": {"<node.field.path>": "<substring>"}}}

A `stub_responses` entry keyed by an agent name is that agent's canned reply; one keyed by
the id of an `ask_human`/`approval` node picks its answer (`{"approved": false}` makes the
approval reject). `{"__raise__": true}` makes an engine call fail, to exercise a
`fail_branch` on purpose.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .engines import EngineCall, StubEngine
from .node_types import ApprovalRejected, _stringify_for_chat
from .runner import DAGRunner, RunResult
from .storage import WorkflowDoc, _parse_yaml
from .validator import WorkflowInvalid, validate

ACCEPTANCE_MAX_BYTES = 64 * 1024
DEFAULT_MAX_REPAIRS = 3
_DETAIL_MAX = 200
_EXPECT_STATES = ("complete", "failed")
_ACCEPTANCE_KEYS = {"inputs", "stub_responses", "expect"}
_EXPECT_KEYS = {"state", "nodes_reached", "final_contains"}

SIGNAL_NAMES = ("validator", "reachability", "acceptance", "external_exposure", "repairs", "node_model")


@dataclass(frozen=True)
class Signal:
    name: str  # one of SIGNAL_NAMES
    status: str  # "pass" | "fail" | "unmeasured" | "info"
    detail: str  # short; never YAML text, never user text
    value: float | int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "value": self.value}


@dataclass(frozen=True)
class NodeOutcome:
    """What the synthetic run did with one node, in execution order (feeds the console's
    "Synthetic check" event stream). `error` is the exception class name only."""

    node_id: str
    node_type: str
    status: str  # "success" | "failed" | "skipped"
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "node_type": self.node_type, "status": self.status, "error": self.error}


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool  # True only when no signal is "fail" or "unmeasured" ("info" is ignored)
    synthetic: bool  # always True in v1, and the UI says so
    signals: tuple[Signal, ...]
    run_state: str | None  # RunResult.state of the synthetic run; None when it did not start
    elapsed_ms: int
    nodes: tuple[NodeOutcome, ...] = ()

    def first_blocker(self) -> Signal | None:
        for s in self.signals:
            if s.status in ("fail", "unmeasured"):
                return s
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "synthetic": self.synthetic,
            "run_state": self.run_state,
            "elapsed_ms": self.elapsed_ms,
            "signals": [s.as_dict() for s in self.signals],
            "nodes": [n.as_dict() for n in self.nodes],
        }


def _clip(text: str) -> str:
    return text if len(text) <= _DETAIL_MAX else text[: _DETAIL_MAX - 1] + "…"


# ---------------------------------------------------------------------------
# Acceptance case schema
# ---------------------------------------------------------------------------


def validate_acceptance(case: Any) -> str | None:
    """Return None when `case` is a well-formed acceptance case, else a short reason."""
    if not isinstance(case, dict):
        return "acceptance must be an object"
    extra = set(case) - _ACCEPTANCE_KEYS
    if extra:
        return f"unknown acceptance key: {sorted(extra)[0]}"
    inputs = case.get("inputs", {})
    if not isinstance(inputs, dict):
        return "inputs must be an object"
    stubs = case.get("stub_responses", {})
    if not isinstance(stubs, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in stubs.items()):
        return "stub_responses must map a name to an object"
    expect = case.get("expect")
    if not isinstance(expect, dict) or not expect:
        return "expect is required and must be a non-empty object"
    extra = set(expect) - _EXPECT_KEYS
    if extra:
        return f"unknown expect key: {sorted(extra)[0]}"
    if "state" in expect and expect["state"] not in _EXPECT_STATES:
        return f"expect.state must be one of {list(_EXPECT_STATES)}"
    reached = expect.get("nodes_reached", [])
    if not isinstance(reached, list) or not all(isinstance(x, str) for x in reached):
        return "expect.nodes_reached must be a list of node ids"
    contains = expect.get("final_contains", {})
    if not isinstance(contains, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in contains.items()):
        return "expect.final_contains must map a path to a substring"
    return None


# ---------------------------------------------------------------------------
# Synthetic engine + recording shims
# ---------------------------------------------------------------------------


class _SyntheticEngine(StubEngine):
    """StubEngine that also honours `{"__raise__": true}` in a canned response."""

    def spawn(self, call: EngineCall) -> dict[str, Any]:
        out = super().spawn(call)
        if isinstance(out, dict) and out.get("__raise__"):
            raise RuntimeError("synthetic failure requested by the acceptance case")
        return out


def _default_reply(call: EngineCall) -> dict[str, Any]:
    md = call.metadata or {}
    if md.get("node_type") == "route" and md.get("mode") == "classify":
        classes = md.get("classes") or []
        return {"class": classes[0] if classes else ""}
    if md.get("node_type") == "delegation_loop" and md.get("role") == "manager":
        return {"decision": "COMPLETE", "result": {"synthetic": True}, "confidence": 1.0}
    text = f"synthetic response from {call.agent}"
    return {"output": text, "text": text, "summary": text, "items": []}


def _shims(stubs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Recording replacements for the node types that touch a channel or pause for a human."""

    def deliver(*, node, engine, state, inputs, audit):
        audit("synthetic.deliver", node_id=node["id"])
        return {"delivered": True, "channel": (node.get("config") or {}).get("channel"), "synthetic": True}

    def answer(*, node, engine, state, inputs, audit):
        from .code_exec import _resolve_selector

        text = node.get("text")
        if not text and node.get("text_from"):
            text = _stringify_for_chat(_resolve_selector(node["text_from"], state=state, inputs=inputs))
        audit("synthetic.answer", node_id=node["id"])
        return {"sent": True, "text": text or "", "channel": node.get("channel"), "synthetic": True}

    def ask_human(*, node, engine, state, inputs, audit):
        audit("synthetic.ask_human", node_id=node["id"])
        override = stubs.get(node["id"])
        if override is not None:
            return dict(override)
        expect = node.get("expect")
        if expect:
            value: Any = True if expect.get("type", "string") == "boolean" else "synthetic reply"
            return {expect["field"]: value, "raw_reply": "synthetic reply"}
        return {"reply": "synthetic reply"}

    def approval(*, node, engine, state, inputs, audit):
        audit("synthetic.approval", node_id=node["id"])
        override = stubs.get(node["id"]) or {}
        if override.get("approved") is False:
            raise ApprovalRejected(f"approval {node['id']!r} rejected (synthetic)")
        return {"approved": True, "output": "approved", "comment": "synthetic"}

    def peer_agent(*, node, engine, state, inputs, audit):
        # Nothing leaves the installation: the node is validated (R10), a pinned endpoint must be a
        # paired one, and the answer is canned. The real executor is never called here.
        from . import node_types as _nt
        from .peer_gate import endpoint_is_paired

        audit("synthetic.peer_agent", node_id=node["id"])
        override = stubs.get(node["id"]) or {}
        if override.get("__raise__"):
            raise RuntimeError("synthetic failure requested by the acceptance case")
        endpoint_id = node.get("endpoint_id")
        if endpoint_id is not None:
            sender = _nt._PEER_SENDER_FACTORY() if _nt._PEER_SENDER_FACTORY is not None else None
            if not endpoint_is_paired(endpoint_id, sender):
                raise RuntimeError("peer endpoint is not paired on this installation")
        out = {"status": "ok", "ok": True, "result": "synthetic peer answer", "untrusted": True,
               "synthetic": True, "peer_instance_id": "", "agent_address": None, "task_id": "",
               "our_chain_tail": "", "peer_chain_tail": ""}
        out.update({k: v for k, v in override.items() if k != "__raise__"})
        return out

    return {"deliver": deliver, "answer": answer, "ask_human": ask_human, "approval": approval,
            "peer_agent": peer_agent}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def _legit_failure(node_spec: dict[str, Any], stubs: dict[str, Any]) -> bool:
    """A failed node is expected only when its branch is contained (`fail_branch`) AND the
    acceptance case provoked it on purpose (a canned response for its agent or id)."""
    strategy = (node_spec.get("retry") or {}).get("error_strategy", "abort")
    if strategy != "fail_branch":
        return False
    return node_spec.get("id") in stubs or node_spec.get("agent") in stubs


def _reachability(doc: WorkflowDoc, run: RunResult | None, timed_out: bool, stubs: dict[str, Any]) -> Signal:
    if timed_out:
        return Signal("reachability", "fail", "timeout")
    if run is None:
        return Signal("reachability", "fail", "the synthetic run did not finish")
    specs = {n["id"]: n for n in doc.graph}
    if run.state != "complete":
        failed = [nid for nid, r in run.nodes.items() if r.status == "failed"]
        who = f" at node {failed[0]!r} ({(run.nodes[failed[0]].error or '').split(':', 1)[0]})" if failed else ""
        return Signal("reachability", "fail", _clip(f"run ended {run.state}{who}"))
    bad: list[str] = []
    for nid, spec in specs.items():
        res = run.nodes.get(nid)
        if res is None:
            bad.append(nid)
        elif res.status == "failed" and not _legit_failure(spec, stubs):
            bad.append(nid)
    if bad:
        return Signal("reachability", "fail", _clip(f"node never reached or failed: {', '.join(repr(b) for b in bad[:5])}"), len(bad))
    done = sum(1 for r in run.nodes.values() if r.status == "success")
    skipped = sum(1 for r in run.nodes.values() if r.status == "skipped")
    return Signal("reachability", "pass", f"{done} node(s) ran, {skipped} skipped by a branch", done)


def _resolve_path(path: str, state: dict[str, Any]) -> Any:
    value: Any = state
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _acceptance(acceptance: dict | None, run: RunResult | None, timed_out: bool) -> Signal:
    if acceptance is None:
        return Signal("acceptance", "unmeasured", "no acceptance case stored")
    problem = validate_acceptance(acceptance)
    if problem:
        return Signal("acceptance", "fail", _clip(f"malformed acceptance case: {problem}"))
    if run is None or timed_out:
        return Signal("acceptance", "unmeasured", "the synthetic run did not finish")
    expect = acceptance["expect"]
    want_state = expect.get("state", "complete")
    if run.state != want_state:
        return Signal("acceptance", "fail", f"expected state {want_state}, run ended {run.state}")
    for nid in expect.get("nodes_reached", []):
        res = run.nodes.get(nid)
        if res is None or res.status != "success":
            return Signal("acceptance", "fail", _clip(f"expected node {nid!r} to run"))
    for path, needle in expect.get("final_contains", {}).items():
        value = _resolve_path(path, run.final_state)
        if value is None or needle not in str(value):
            return Signal("acceptance", "fail", _clip(f"{path!r} does not contain the expected text"))
    checks = len(expect.get("nodes_reached", [])) + len(expect.get("final_contains", {})) + ("state" in expect)
    return Signal("acceptance", "pass", f"{checks} expectation(s) hold", checks)


def _exposure(doc: WorkflowDoc) -> Signal:
    counts: dict[str, int] = {}
    engine_nodes = 0
    from .node_types import node_requires_engine

    for n in doc.graph:
        t = n.get("type", "agent")
        counts[t] = counts.get(t, 0) + 1
        if node_requires_engine(n):
            engine_nodes += 1
    stubbed = sum(counts.get(t, 0) for t in ("deliver", "answer", "ask_human", "approval", "peer_agent"))
    return Signal(
        "external_exposure",
        "info",
        f"{engine_nodes} engine node(s) and {stubbed} channel/human node(s) are stubbed, not executed",
        engine_nodes + stubbed,
    )


def _node_models(doc: WorkflowDoc, tenant_id: str | None = None) -> Signal:
    """ADR-2308: how many model-calling nodes carry no ``model:``. Such a node runs on the path
    default (the DAG engine's alias, or the CLI's own default on the console's legacy executor,
    which can be the most expensive model). Informational: a missing model is a cost hint, never
    a defect, so it never blocks "ready". Counts only, no node ids (ids are workflow text)."""
    from .model_resolution import in_scope, operator_pin, site_default

    scoped = [n for n in doc.graph if isinstance(n, dict) and in_scope(n)]
    missing = sum(1 for n in scoped if not str(n.get("model") or "").strip())
    if not scoped:
        return Signal("node_model", "info", "no node calls a model", 0)
    if tenant_id and operator_pin(tenant_id):
        # the pin outranks every node value (ADR-2308 decision 1): saying otherwise would mislead
        return Signal("node_model", "info",
                      f"an operator pin on the Models page sets the model of all {len(scoped)} model node(s)", 0)
    if not missing:
        return Signal("node_model", "info", f"all {len(scoped)} model node(s) set 'model:'", 0)
    where = ("the site default (CORVIN_HELPER_MODEL_WORKFLOW_NODE)" if site_default() else
             "the path default (DAG engine: haiku; console executor: the CLI's own default)")
    return Signal(
        "node_model",
        "info",
        f"{missing} of {len(scoped)} model node(s) have no 'model:' and run on {where}",
        missing,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check_readiness(
    yaml_text: str,
    *,
    acceptance: dict | None,
    repairs: int,
    max_repairs: int = DEFAULT_MAX_REPAIRS,
    wall_limit_s: float = 10.0,
    tenant_id: str | None = None,
) -> ReadinessReport:
    t0 = time.perf_counter()

    def finish(signals: list[Signal], run_state: str | None,
               nodes: tuple[NodeOutcome, ...] = ()) -> ReadinessReport:
        ready = not any(s.status in ("fail", "unmeasured") for s in signals)
        return ReadinessReport(
            ready=ready, synthetic=True, signals=tuple(signals), run_state=run_state,
            elapsed_ms=int((time.perf_counter() - t0) * 1000), nodes=nodes,
        )

    repairs_signal = (
        Signal("repairs", "pass", f"{repairs} designer repair(s), ceiling {max_repairs}", repairs)
        if repairs <= max_repairs
        else Signal("repairs", "fail", f"{repairs} designer repair(s) exceed the ceiling of {max_repairs}", repairs)
    )

    # -- validator ---------------------------------------------------------
    doc: WorkflowDoc | None = None
    try:
        doc = WorkflowDoc.from_dict(_parse_yaml(yaml_text))
        validate(doc)
        validator = Signal("validator", "pass", "R1-R11 hold")
    except WorkflowInvalid as e:
        validator = Signal("validator", "fail", _clip(f"{e.code} violated"))
    except Exception as e:  # noqa: BLE001 - unparseable YAML / wrong shape
        validator = Signal("validator", "fail", f"workflow does not parse ({type(e).__name__})")
    if validator.status != "pass" or doc is None:
        return finish(
            [
                validator,
                Signal("reachability", "unmeasured", "validator failed, no synthetic run"),
                Signal("acceptance", "unmeasured", "validator failed, no synthetic run")
                if acceptance is None or validate_acceptance(acceptance) is None
                else Signal("acceptance", "fail", "malformed acceptance case"),
                Signal("external_exposure", "info", "not measured"),
                repairs_signal,
                Signal("node_model", "info", "not measured"),
            ],
            None,
        )

    # -- synthetic run -----------------------------------------------------
    case = acceptance if acceptance is not None and validate_acceptance(acceptance) is None else None
    stubs: dict[str, dict[str, Any]] = dict(case.get("stub_responses", {})) if case else {}
    inputs = dict(case.get("inputs", {})) if case else {}
    engine = _SyntheticEngine(name="synthetic-stub", responses=stubs, default=_default_reply)
    cancel = threading.Event()
    runner = DAGRunner(doc, engine=engine, console_run=True, cancel_event=cancel,
                       executor_overrides=_shims(stubs))
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["run"] = runner.run(inputs)
        except BaseException as e:  # noqa: BLE001 - never lets the thread die silently
            box["error"] = type(e).__name__

    th = threading.Thread(target=work, name="workflow-readiness", daemon=True)
    th.start()
    th.join(max(0.1, float(wall_limit_s)))
    timed_out = th.is_alive()
    if timed_out:
        cancel.set()
    run: RunResult | None = box.get("run")

    reach = _reachability(doc, run, timed_out, stubs)
    if "error" in box and run is None:
        reach = Signal("reachability", "fail", f"the synthetic run crashed ({box['error']})")
    acc = _acceptance(acceptance, run, timed_out)
    outcomes = tuple(
        NodeOutcome(r.node_id, r.node_type, r.status, (r.error or "").split(":", 1)[0] or None)
        for r in (run.nodes.values() if run is not None else ())
    )
    return finish(
        [validator, reach, acc, _exposure(doc), repairs_signal, _node_models(doc, tenant_id)],
        run.state if run is not None else None,
        outcomes,
    )
