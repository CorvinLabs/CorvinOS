"""NODE_TYPES registry — pluggable node executors.

Each entry binds a node-type string (the YAML `type:` field) to:
- a validator (called by R10)
- an executor (called by DAGRunner)

Operators can register additional types without forking by calling
`register_node_type(name, validator=..., executor=...)`.

Shipped types:

  agent             — static single-shot engine call (default)
  fan_out           — same agent over N items from a state field, sequential
  delegation_loop   — manager-LLM iterates DELEGATE / COMPLETE until budget
  deliver           — fire-and-forget push of upstream output to a bridge outbox
  code              — deterministic, sandboxed Python; never calls the engine (ADR-0188 M1)
  merge             — deterministic fan-in (concat_list/first_non_empty/dict_union) (ADR-0188 M2)
  route             — engine-native branching: condition (structured, no eval) or classify (LLM) (ADR-0188 M3)
"""
from __future__ import annotations

from typing import Any, Callable

# Forward references — runner imports node_types, node_types imports nothing.
_Executor = Callable[..., Any]
_Validator = Callable[[dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Agent node — the simple case
# ---------------------------------------------------------------------------


def _validate_agent(node: dict[str, Any]) -> None:
    agent = node.get("agent")
    if not isinstance(agent, str) or not agent:
        raise ValueError("agent node requires non-empty 'agent' field")


def _execute_agent(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    """Single engine.spawn call. The agent reads from `state` + `inputs`."""
    from .engines import EngineCall

    call = EngineCall(
        agent=node["agent"],
        instructions=node.get("instructions", ""),
        inputs=dict(inputs),
        state=dict(state),
        iteration=0,
        metadata={"node_id": node["id"], "node_type": "agent"},
    )
    audit("node.engine_call", node_id=node["id"], agent=call.agent, iteration=0)
    return engine.spawn(call)


# ---------------------------------------------------------------------------
# Fan-out node — same agent over many items
# ---------------------------------------------------------------------------


def _validate_fan_out(node: dict[str, Any]) -> None:
    agent = node.get("agent")
    if not isinstance(agent, str) or not agent:
        raise ValueError("fan_out node requires 'agent'")
    items_from = node.get("items_from")
    if not isinstance(items_from, str) or not items_from:
        raise ValueError("fan_out node requires 'items_from' (state field name)")


def _execute_fan_out(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    from .engines import EngineCall

    field = node["items_from"]
    # items_from accepts "node_id.field" or just "field" (top-level state)
    if "." in field:
        nid, subfield = field.split(".", 1)
        items = (state.get(nid) or {}).get(subfield, [])
    else:
        items = state.get(field, [])
    if not isinstance(items, list):
        raise ValueError(f"fan_out: items_from {field!r} did not resolve to a list")

    results = []
    for i, item in enumerate(items):
        call = EngineCall(
            agent=node["agent"],
            instructions=node.get("instructions", ""),
            inputs={**inputs, "item": item, "item_index": i},
            state=dict(state),
            iteration=i,
            metadata={"node_id": node["id"], "node_type": "fan_out", "item_index": i},
        )
        audit("node.engine_call", node_id=node["id"], agent=call.agent, iteration=i)
        results.append(engine.spawn(call))
    return {"items": results, "count": len(results)}


# ---------------------------------------------------------------------------
# Delegation-loop node — the composed Pattern-4 inner step
# ---------------------------------------------------------------------------


def _validate_delegation_loop(node: dict[str, Any]) -> None:
    cfg = node.get("config") or {}
    manager = cfg.get("manager")
    if not isinstance(manager, str) or not manager:
        raise ValueError("delegation_loop node requires config.manager")
    budget = cfg.get("budget") or {}
    if not isinstance(budget.get("max_loops"), int) or budget["max_loops"] < 1:
        raise ValueError("delegation_loop node requires config.budget.max_loops >= 1")
    if not isinstance(budget.get("max_total_workers"), int) or budget["max_total_workers"] < 1:
        raise ValueError("delegation_loop node requires config.budget.max_total_workers >= 1")


def _execute_delegation_loop(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    """Manager-worker loop, bounded by budget.

    Manager protocol — returns a dict with one of:
      {"decision": "DELEGATE", "workers": [{agent, instructions, inputs?}, ...]}
      {"decision": "COMPLETE", "result": {...}, "confidence": 0.0..1.0}
      {"decision": "FAIL", "reason": "..."}

    Workers return free-form dicts; the runtime stitches them into
    `state["_delegation"][node_id]["iterations"]` so the next manager call
    sees the full history.
    """
    from .engines import EngineCall

    cfg = node["config"]
    manager_name = cfg["manager"]
    budget = cfg["budget"]
    max_loops = int(budget["max_loops"])
    max_total_workers = int(budget["max_total_workers"])

    iterations: list[dict[str, Any]] = []
    workers_spawned = 0
    terminal = None  # set by COMPLETE / FAIL / budget-hit

    for it in range(1, max_loops + 1):
        manager_call = EngineCall(
            agent=manager_name,
            instructions=node.get("instructions", ""),
            inputs=dict(inputs),
            state={**state, "_iterations": iterations},
            iteration=it,
            metadata={
                "node_id": node["id"],
                "node_type": "delegation_loop",
                "role": "manager",
                "workers_spawned": workers_spawned,
                "max_total_workers": max_total_workers,
            },
        )
        audit(
            "node.delegation_iteration",
            node_id=node["id"],
            iteration=it,
            workers_spawned=workers_spawned,
        )
        decision = engine.spawn(manager_call)
        kind = decision.get("decision")

        if kind == "COMPLETE":
            terminal = {
                "state": "complete",
                "iteration": it,
                "result": decision.get("result", {}),
                "confidence": float(decision.get("confidence", 1.0)),
            }
            iterations.append({"iteration": it, "manager": decision, "workers": []})
            break

        if kind == "FAIL":
            terminal = {
                "state": "failed",
                "iteration": it,
                "reason": decision.get("reason", "manager said FAIL"),
            }
            iterations.append({"iteration": it, "manager": decision, "workers": []})
            break

        if kind != "DELEGATE":
            terminal = {
                "state": "failed",
                "iteration": it,
                "reason": f"unknown manager decision: {kind!r}",
            }
            iterations.append({"iteration": it, "manager": decision, "workers": []})
            break

        workers_spec = list(decision.get("workers") or [])
        # Trim to budget so we never overshoot max_total_workers
        remaining = max_total_workers - workers_spawned
        if len(workers_spec) > remaining:
            workers_spec = workers_spec[:remaining]
        worker_results: list[dict[str, Any]] = []
        for wi, w in enumerate(workers_spec):
            wcall = EngineCall(
                agent=w["agent"],
                instructions=w.get("instructions", ""),
                inputs={**inputs, **(w.get("inputs") or {})},
                state={**state, "_iterations": iterations},
                iteration=it,
                metadata={
                    "node_id": node["id"],
                    "node_type": "delegation_loop",
                    "role": "worker",
                    "loop_iter": it,
                    "worker_index": wi,
                },
            )
            audit(
                "node.engine_call",
                node_id=node["id"],
                agent=wcall.agent,
                iteration=it,
            )
            worker_results.append(engine.spawn(wcall))
            workers_spawned += 1

        iterations.append({"iteration": it, "manager": decision, "workers": worker_results})

        if workers_spawned >= max_total_workers:
            # Budget exhausted but manager didn't COMPLETE; mark partial.
            terminal = {
                "state": "partial",
                "iteration": it,
                "reason": f"max_total_workers reached ({max_total_workers})",
            }
            break

    if terminal is None:
        terminal = {
            "state": "partial",
            "iteration": max_loops,
            "reason": f"max_loops reached ({max_loops})",
        }

    # Lift the manager's `result` dict to the top level so `share_output:
    # [score, top_quotes, ...]` projects naturally without forcing
    # downstream nodes to know about delegation-loop internals. Metadata
    # (terminal, iterations, workers_spawned) stays under reserved keys.
    output: dict[str, Any] = {
        "_terminal": terminal,
        "_iterations": iterations,
        "_workers_spawned": workers_spawned,
        # Back-compat / inspection keys (tests still read these)
        "terminal": terminal,
        "iterations": iterations,
        "workers_spawned": workers_spawned,
        "result": terminal.get("result", None),
    }
    result_payload = terminal.get("result") or {}
    if isinstance(result_payload, dict):
        for k, v in result_payload.items():
            if k not in output:
                output[k] = v
    return output


# ---------------------------------------------------------------------------
# Registry + extension point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deliver node — write upstream output to a bridge outbox
# ---------------------------------------------------------------------------

_DELIVER_CHANNELS = {"discord", "telegram", "slack", "whatsapp", "email", "signal", "teams"}
_DELIVER_FORMATS = {"text", "markdown"}


def _validate_deliver(node: dict[str, Any]) -> None:
    cfg = node.get("config") or {}
    channel = cfg.get("channel", "")
    if channel not in _DELIVER_CHANNELS:
        raise ValueError(
            f"deliver node requires config.channel in {sorted(_DELIVER_CHANNELS)}, got {channel!r}"
        )
    chat_id = cfg.get("chat_id", "")
    if not chat_id:
        raise ValueError("deliver node requires config.chat_id (channel ID, group ID, or 'auto')")
    fmt = cfg.get("format", "markdown")
    if fmt not in _DELIVER_FORMATS:
        raise ValueError(f"deliver node config.format must be one of {_DELIVER_FORMATS}, got {fmt!r}")
    # voice is optional boolean
    voice = cfg.get("voice")
    if voice is not None and not isinstance(voice, bool):
        raise ValueError("deliver node config.voice must be true or false")


def _execute_deliver(*, node: dict[str, Any], engine: Any, state: dict[str, Any], inputs: dict[str, Any], audit: Any) -> dict[str, Any]:
    """Write upstream output to the bridge outbox so the messenger daemon delivers it."""
    import json as _json
    import os as _os
    import secrets as _secrets
    import time as _time
    from pathlib import Path as _Path

    cfg = node.get("config") or {}
    channel = cfg["channel"]
    chat_id = str(cfg.get("chat_id", "auto"))
    fmt = cfg.get("format", "markdown")

    # Collect text from upstream nodes via state
    deps = node.get("depends_on") or []
    text_parts: list[str] = []
    for dep in deps:
        dep_val = state.get(dep)
        if isinstance(dep_val, dict):
            text_parts.append(str(dep_val.get("output", dep_val.get("text", ""))))
        elif dep_val is not None:
            text_parts.append(str(dep_val))
    text = "\n\n".join(p for p in text_parts if p).strip()

    if not text:
        audit("deliver.skipped", node_id=node["id"], reason="no upstream output")
        return {"delivered": False, "reason": "no upstream output"}

    # Resolve chat_id="auto" from state (set by the workflow runner on triggered runs)
    if chat_id == "auto":
        chat_id = str(state.get("__trigger_chat_id__", ""))
        if not chat_id:
            audit("deliver.skipped", node_id=node["id"], reason="chat_id=auto but no trigger context")
            return {"delivered": False, "reason": "chat_id=auto — no trigger context available"}

    # Locate the shared outbox directory (same path Discord/Telegram daemons poll)
    _here = _Path(__file__).resolve()
    _repo = _here.parents[3]  # workflows/corvin_workflows/ → core/ → repo root
    outbox_dir = _repo / "operator" / "bridges" / "shared" / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    envelope = {
        "channel": channel,
        "chat_id": chat_id,
        "text": text[:4000],  # Discord hard limit is 2000; daemon chunks longer messages
        "_workflow_deliver": True,
        "ts": int(_time.time() * 1000),
    }

    fname = f"wf_deliver_{_secrets.token_hex(6)}.json"
    fpath = outbox_dir / fname
    fpath.write_text(_json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")

    audit("deliver.sent", node_id=node["id"], channel=channel, chat_id=chat_id, chars=len(text))
    return {"delivered": True, "channel": channel, "chat_id": chat_id, "chars": len(text)}


# ---------------------------------------------------------------------------
# Code node — deterministic, sandboxed Python (ADR-0188 M1)
# ---------------------------------------------------------------------------

_CODE_LANGUAGES = {"python3"}


def _validate_code(node: dict[str, Any]) -> None:
    lang = node.get("language")
    if lang not in _CODE_LANGUAGES:
        raise ValueError(f"code node requires language in {sorted(_CODE_LANGUAGES)}, got {lang!r}")
    source = node.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("code node requires non-empty 'source'")
    if "def main(" not in source:
        raise ValueError("code node 'source' must define a top-level 'def main(...)' entry point")
    outputs = node.get("outputs")
    if not isinstance(outputs, list) or not outputs or not all(isinstance(o, str) for o in outputs):
        raise ValueError("code node requires non-empty 'outputs' list of field names")
    node_inputs = node.get("inputs") or {}
    if not isinstance(node_inputs, dict) or not all(isinstance(v, str) for v in node_inputs.values()):
        raise ValueError("code node 'inputs' must be a mapping of param name -> selector string")


def _execute_code(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    """Deterministic node: never calls engine.spawn(). Runs `source` in the
    same bwrap sandbox Forge tools use (see code_exec.py). Enforces AWP-1.0
    spec rule R33 ("deterministic node must not invoke an LLM") by
    construction — there is no code path here that reaches the engine.
    """
    from .code_exec import CodeExecutionError, _resolve_selector, run_sandboxed_python

    node_inputs = node.get("inputs") or {}
    resolved_args = {
        param: _resolve_selector(selector, state=state, inputs=inputs)
        for param, selector in node_inputs.items()
    }
    audit("node.code_exec", node_id=node["id"], params=sorted(resolved_args.keys()))
    try:
        result = run_sandboxed_python(node["source"], resolved_args)
    except CodeExecutionError as e:
        raise RuntimeError(f"code node {node['id']!r} execution failed: {e}") from e

    outputs = node["outputs"]
    missing = [o for o in outputs if o not in result]
    if missing:
        raise RuntimeError(
            f"code node {node['id']!r}: main() return dict missing declared outputs {missing} "
            f"(got keys: {sorted(result.keys())})"
        )
    return {k: result[k] for k in outputs}


# ---------------------------------------------------------------------------
# Merge node — deterministic fan-in (ADR-0188 M2)
# ---------------------------------------------------------------------------

_MERGE_STRATEGIES = {"concat_list", "first_non_empty", "dict_union"}


def _validate_merge(node: dict[str, Any]) -> None:
    strategy = node.get("strategy")
    if strategy not in _MERGE_STRATEGIES:
        raise ValueError(f"merge node requires strategy in {sorted(_MERGE_STRATEGIES)}, got {strategy!r}")
    node_inputs = node.get("inputs")
    if not isinstance(node_inputs, list) or not node_inputs or not all(isinstance(s, str) for s in node_inputs):
        raise ValueError("merge node requires non-empty 'inputs' list of dotted selector strings")
    output = node.get("output")
    if not isinstance(output, str) or not output:
        raise ValueError("merge node requires non-empty 'output' field name")


def _execute_merge(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    """Deterministic fan-in — no LLM. Mirrors Dify's variable-aggregator:
    combine upstream branch outputs by a fixed strategy instead of asking an
    LLM to merge them in a prompt (AWP's previous only option via `agent`)."""
    from .code_exec import _resolve_selector

    strategy = node["strategy"]
    values = [_resolve_selector(sel, state=state, inputs=inputs) for sel in node["inputs"]]
    audit("node.merge", node_id=node["id"], strategy=strategy, n=len(values))

    if strategy == "concat_list":
        merged: Any = []
        for v in values:
            if isinstance(v, list):
                merged.extend(v)
            elif v is not None:
                merged.append(v)
    elif strategy == "first_non_empty":
        merged = next((v for v in values if v), None)
    elif strategy == "dict_union":
        merged = {}
        for v in values:
            if isinstance(v, dict):
                merged.update(v)
    else:  # pragma: no cover — guarded by validator
        raise ValueError(f"unknown merge strategy {strategy!r}")

    return {node["output"]: merged}


# ---------------------------------------------------------------------------
# Route node — engine-native branching (ADR-0188 M3)
# ---------------------------------------------------------------------------

_ROUTE_MODES = {"condition", "classify"}
_ROUTE_OPS = {"==", "!=", ">", ">=", "<", "<=", "contains", "in"}


def _validate_route(node: dict[str, Any]) -> None:
    mode = node.get("mode")
    if mode not in _ROUTE_MODES:
        raise ValueError(f"route node requires mode in {sorted(_ROUTE_MODES)}, got {mode!r}")

    if mode == "condition":
        cases = node.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("route(mode=condition) requires a non-empty 'cases' list")
        seen_ids: set[str] = set()
        has_default = False
        for c in cases:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"]:
                raise ValueError("route condition case requires a non-empty 'id'")
            if c["id"] in seen_ids:
                raise ValueError(f"route condition: duplicate case id {c['id']!r}")
            seen_ids.add(c["id"])
            when = c.get("when")
            if when == "default":
                has_default = True
                continue
            if not isinstance(when, dict):
                raise ValueError(
                    f"route condition case {c['id']!r}: 'when' must be 'default' or a "
                    f"structured {{selector, op, value}} mapping — no free-form eval by design"
                )
            if not isinstance(when.get("selector"), str) or not when["selector"]:
                raise ValueError(f"route condition case {c['id']!r}: 'when.selector' required")
            if when.get("op") not in _ROUTE_OPS:
                raise ValueError(
                    f"route condition case {c['id']!r}: 'when.op' must be one of {sorted(_ROUTE_OPS)}"
                )
            if "value" not in when:
                raise ValueError(f"route condition case {c['id']!r}: 'when.value' required")
        if not has_default:
            raise ValueError(
                "route(mode=condition) requires exactly one case with when: 'default' "
                "as the guaranteed fallback branch"
            )
    else:  # classify
        agent = node.get("agent")
        if not isinstance(agent, str) or not agent:
            raise ValueError("route(mode=classify) requires 'agent'")
        classes = node.get("classes")
        if not isinstance(classes, list) or not classes or not all(isinstance(c, str) and c for c in classes):
            raise ValueError("route(mode=classify) requires a non-empty list of string 'classes'")
        input_sel = node.get("input")
        if not isinstance(input_sel, str) or not input_sel:
            raise ValueError("route(mode=classify) requires a non-empty 'input' selector")


def _apply_op(op: str, actual: Any, expected: Any) -> bool:
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == ">":
        return actual is not None and actual > expected
    if op == ">=":
        return actual is not None and actual >= expected
    if op == "<":
        return actual is not None and actual < expected
    if op == "<=":
        return actual is not None and actual <= expected
    if op == "contains":
        return actual is not None and expected in actual
    if op == "in":
        return actual in (expected or [])
    raise ValueError(f"unknown route op {op!r}")  # pragma: no cover — guarded by validator


def _execute_route_condition(*, node, state, inputs, audit) -> dict[str, Any]:
    from .code_exec import _resolve_selector

    default_case = None
    for case in node["cases"]:
        if case.get("when") == "default":
            default_case = case["id"]
            continue
        when = case["when"]
        actual = _resolve_selector(when["selector"], state=state, inputs=inputs)
        if _apply_op(when["op"], actual, when["value"]):
            audit("node.route_matched", node_id=node["id"], case=case["id"], mode="condition")
            return {"case": case["id"], "mode": "condition"}
    audit("node.route_matched", node_id=node["id"], case=default_case, mode="condition", fallback=True)
    return {"case": default_case, "mode": "condition", "fallback": True}


def _execute_route_classify(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    from .code_exec import _resolve_selector
    from .engines import EngineCall

    classes = node["classes"]
    query = _resolve_selector(node["input"], state=state, inputs=inputs)
    instructions = (
        f"{node.get('instructions', '')}\n\n"
        f"Classify the input below into EXACTLY ONE of these classes: {', '.join(classes)}.\n"
        f'Respond with a JSON object of the exact shape {{"class": "<one of the classes>"}} '
        f"and nothing else.\n\nInput:\n{query}"
    ).strip()
    call = EngineCall(
        agent=node["agent"],
        instructions=instructions,
        inputs=dict(inputs),
        state=dict(state),
        iteration=0,
        metadata={"node_id": node["id"], "node_type": "route", "mode": "classify", "classes": classes},
    )
    audit("node.engine_call", node_id=node["id"], agent=call.agent, iteration=0)
    decision = engine.spawn(call)
    chosen = decision.get("class")
    if chosen not in classes:
        raise RuntimeError(
            f"route(mode=classify) node {node['id']!r}: engine returned class {chosen!r}, "
            f"not one of the declared classes {classes}"
        )
    audit("node.route_matched", node_id=node["id"], case=chosen, mode="classify")
    return {"case": chosen, "mode": "classify"}


def _execute_route(*, node, engine, state, inputs, audit) -> dict[str, Any]:
    if node["mode"] == "condition":
        return _execute_route_condition(node=node, state=state, inputs=inputs, audit=audit)
    return _execute_route_classify(node=node, engine=engine, state=state, inputs=inputs, audit=audit)


# ---------------------------------------------------------------------------
# Registry + extension point
# ---------------------------------------------------------------------------

NODE_TYPES: dict[str, dict[str, Any]] = {
    "agent": {"validate": _validate_agent, "execute": _execute_agent},
    "fan_out": {"validate": _validate_fan_out, "execute": _execute_fan_out},
    "delegation_loop": {
        "validate": _validate_delegation_loop,
        "execute": _execute_delegation_loop,
    },
    "deliver": {"validate": _validate_deliver, "execute": _execute_deliver},
    "code": {"validate": _validate_code, "execute": _execute_code},
    "merge": {"validate": _validate_merge, "execute": _execute_merge},
    "route": {"validate": _validate_route, "execute": _execute_route},
}


def register_node_type(
    name: str,
    *,
    validate: _Validator | None,
    execute: _Executor,
) -> None:
    """Operator-facing extension point. Adds a node type without forking."""
    if name in NODE_TYPES:
        raise ValueError(f"node type {name!r} already registered")
    NODE_TYPES[name] = {"validate": validate, "execute": execute}
