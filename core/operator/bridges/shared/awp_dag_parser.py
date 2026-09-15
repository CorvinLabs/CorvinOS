"""awp_dag_parser.py — Phase 6 (ADR-0006): AWP-DAG schema parser.

Reads AWP-style ``workflow.awp.yaml`` (or an inline dict from a calling
test), validates the schema, and returns a structured ``DAG`` ready for
the engine-driven walker. **Imports nothing from ``awp.runtime``** —
this is pure standards consumption per ADR-0005.

Schema (subset of AWP 1.0 spec, focused on what the walker needs):

    workflow:
      id:      <str, required>
      version: <str, optional>

    state:
      initial: { ... }       # optional dict
      output_node: <str>     # optional — which node's output is the
                             # workflow's final answer

    budget:
      tokens:    <int>       # optional
      time_s:    <int>       # optional
      max_loops: <int>       # optional
      max_workers: <int>     # optional
      max_tool_calls: <int>  # optional

    dag:
      nodes:
        - id:             <str, required, unique>
          engine_id:      <str, required>  # claude_code | codex_cli | ...
          prompt:         <str, required>  # template; {state.foo} is allowed
          depends_on:     [<id>, ...]      # optional list of node ids
          execution_kind: "engine"|"http"  # optional, default "engine"
          output_key:     <str, optional>  # where in state to write result;
                                           # defaults to node.id
          tools:          [<tool_id>, ...] # optional, advisory
          model:          <str, optional>  # advisory; engine may use

YAML support is optional: when ``pyyaml`` isn't installed, ``parse_dag_file``
gracefully reports the missing dep; ``parse_dag_dict`` always works
(callers can do their own JSON load).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DAGNode:
    """One worker node in an AWP-style DAG."""
    id: str
    engine_id: str
    prompt: str
    depends_on: tuple[str, ...] = ()
    execution_kind: str = "engine"  # "engine" | "http"
    output_key: str = ""
    tools: tuple[str, ...] = ()
    model: str = ""


@dataclass(frozen=True)
class DAGBudget:
    """5D budget envelope (subset). Zero means unbounded for that axis."""
    tokens: int = 0
    time_s: int = 0
    max_loops: int = 0
    max_workers: int = 0
    max_tool_calls: int = 0


@dataclass(frozen=True)
class DAG:
    workflow_id: str
    version: str
    nodes: tuple[DAGNode, ...]
    initial_state: dict = field(default_factory=dict)
    budget: DAGBudget = field(default_factory=DAGBudget)
    output_node: str = ""

    def node_by_id(self, node_id: str) -> DAGNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None


# ----- public API ---------------------------------------------------------

def parse_dag_dict(data: dict[str, Any]) -> DAG:
    """Validate + return DAG. Raises ValueError on schema problems."""
    if not isinstance(data, dict):
        raise ValueError("AWP-DAG: top-level must be a dict")

    wf = data.get("workflow") or {}
    if not isinstance(wf, dict):
        raise ValueError("AWP-DAG: 'workflow' must be a dict")
    wf_id = wf.get("id")
    if not isinstance(wf_id, str) or not wf_id:
        raise ValueError("AWP-DAG: 'workflow.id' is required (non-empty str)")
    version = wf.get("version") or "1.0"
    if not isinstance(version, str):
        raise ValueError("AWP-DAG: 'workflow.version' must be string")

    state = data.get("state") or {}
    if not isinstance(state, dict):
        raise ValueError("AWP-DAG: 'state' must be a dict")
    initial = state.get("initial") or {}
    if not isinstance(initial, dict):
        raise ValueError("AWP-DAG: 'state.initial' must be a dict")
    output_node = state.get("output_node") or ""
    if output_node and not isinstance(output_node, str):
        raise ValueError("AWP-DAG: 'state.output_node' must be string")

    budget_raw = data.get("budget") or {}
    if not isinstance(budget_raw, dict):
        raise ValueError("AWP-DAG: 'budget' must be a dict")
    budget = DAGBudget(
        tokens=int(budget_raw.get("tokens") or 0),
        time_s=int(budget_raw.get("time_s") or 0),
        max_loops=int(budget_raw.get("max_loops") or 0),
        max_workers=int(budget_raw.get("max_workers") or 0),
        max_tool_calls=int(budget_raw.get("max_tool_calls") or 0),
    )

    dag = data.get("dag") or {}
    if not isinstance(dag, dict):
        raise ValueError("AWP-DAG: 'dag' must be a dict")
    nodes_raw = dag.get("nodes") or []
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ValueError("AWP-DAG: 'dag.nodes' must be a non-empty list")

    seen_ids: set[str] = set()
    nodes: list[DAGNode] = []
    for i, n in enumerate(nodes_raw):
        if not isinstance(n, dict):
            raise ValueError(f"AWP-DAG: dag.nodes[{i}] must be a dict")
        nid = n.get("id")
        if not isinstance(nid, str) or not nid:
            raise ValueError(f"AWP-DAG: dag.nodes[{i}].id required (non-empty str)")
        if nid in seen_ids:
            raise ValueError(f"AWP-DAG: duplicate node id {nid!r}")
        seen_ids.add(nid)

        engine_id = n.get("engine_id")
        if not isinstance(engine_id, str) or not engine_id:
            raise ValueError(f"AWP-DAG: node {nid!r}: 'engine_id' required")
        prompt = n.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"AWP-DAG: node {nid!r}: 'prompt' required (non-empty str)")
        deps_raw = n.get("depends_on") or []
        if not isinstance(deps_raw, list) or not all(isinstance(x, str) for x in deps_raw):
            raise ValueError(f"AWP-DAG: node {nid!r}: 'depends_on' must be list[str]")
        ekind = n.get("execution_kind") or "engine"
        if ekind not in ("engine", "http"):
            raise ValueError(f"AWP-DAG: node {nid!r}: 'execution_kind' must be 'engine' or 'http'")
        out_key = n.get("output_key") or nid
        if not isinstance(out_key, str):
            raise ValueError(f"AWP-DAG: node {nid!r}: 'output_key' must be string")
        tools_raw = n.get("tools") or []
        if not isinstance(tools_raw, list) or not all(isinstance(x, str) for x in tools_raw):
            raise ValueError(f"AWP-DAG: node {nid!r}: 'tools' must be list[str]")
        model = n.get("model") or ""
        if not isinstance(model, str):
            raise ValueError(f"AWP-DAG: node {nid!r}: 'model' must be string")

        nodes.append(DAGNode(
            id=nid, engine_id=engine_id, prompt=prompt,
            depends_on=tuple(deps_raw), execution_kind=ekind,
            output_key=out_key, tools=tuple(tools_raw), model=model,
        ))

    # Validate dependency targets exist
    for n in nodes:
        for dep in n.depends_on:
            if dep not in seen_ids:
                raise ValueError(
                    f"AWP-DAG: node {n.id!r} depends_on unknown id {dep!r}"
                )
    if output_node and output_node not in seen_ids:
        raise ValueError(
            f"AWP-DAG: state.output_node={output_node!r} is not a known node"
        )

    # Detect cycles via DFS — would otherwise infinite-loop in topo_sort.
    _detect_cycles(nodes)

    return DAG(
        workflow_id=wf_id, version=version,
        nodes=tuple(nodes), initial_state=initial,
        budget=budget, output_node=output_node,
    )


def parse_dag_file(path: str | Path) -> DAG | None:
    """Load YAML/JSON workflow file. Returns None when file is missing.
    Raises ValueError on schema problems or missing pyyaml dep."""
    p = Path(path)
    if not p.is_file():
        return None
    text = p.read_text("utf-8")
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as e:
            raise ValueError(f"AWP-DAG: pyyaml missing for {p}: {e}") from e
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"AWP-DAG: malformed YAML at {p}: {e}") from e
    else:
        # default: JSON
        import json
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"AWP-DAG: malformed JSON at {p}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"AWP-DAG: {p} must contain a top-level dict")
    return parse_dag_dict(data)


def topological_order(dag: DAG) -> list[DAGNode]:
    """Return DAG nodes in dependency order (Kahn's algorithm).
    Within a level, order is preserved from the source list — operators
    can rely on it for tie-breaking when fan-out parallelism is added.
    """
    by_id = {n.id: n for n in dag.nodes}
    incoming: dict[str, set[str]] = {n.id: set(n.depends_on) for n in dag.nodes}

    ordered: list[DAGNode] = []
    # Kahn: repeat until empty: pick all roots (in original order),
    # remove them, continue.
    remaining = list(dag.nodes)  # preserves source order
    while remaining:
        roots = [n for n in remaining if not incoming[n.id]]
        if not roots:
            # cycle (should have been caught by parse, but defensive).
            ids = [n.id for n in remaining]
            raise ValueError(f"AWP-DAG: cycle detected, remaining={ids}")
        for r in roots:
            ordered.append(r)
            remaining.remove(r)
            for n in remaining:
                incoming[n.id].discard(r.id)
    return ordered


# ----- helpers ------------------------------------------------------------

def _detect_cycles(nodes: list[DAGNode]) -> None:
    """Throw on any cycle in the dependency graph. Standard DFS with
    BLACK/GRAY colouring."""
    by_id = {n.id: n for n in nodes}
    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n.id: WHITE for n in nodes}

    def visit(nid: str, stack: list[str]) -> None:
        if colour[nid] == BLACK:
            return
        if colour[nid] == GRAY:
            cycle = " → ".join(stack[stack.index(nid):] + [nid])
            raise ValueError(f"AWP-DAG: cycle detected: {cycle}")
        colour[nid] = GRAY
        for dep in by_id[nid].depends_on:
            visit(dep, stack + [nid])
        colour[nid] = BLACK

    for n in nodes:
        visit(n.id, [])
