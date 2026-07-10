"""DAG runner — topological execution with node-type dispatch.

The runner is single-process and synchronous. Same-level nodes execute
sequentially in this MVP; parallelism is a Phase-2 concern (ThreadPoolExecutor
analog to L25-compute's ParallelDriver).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable

from .node_types import NODE_TYPES
from .storage import WorkflowDoc


@dataclass
class NodeResult:
    node_id: str
    node_type: str
    started_at: float
    finished_at: float
    output: dict[str, Any]
    error: str | None = None
    status: str = "success"  # "success" | "failed" | "skipped"
    attempts: int = 1

    @property
    def wall_s(self) -> float:
        return self.finished_at - self.started_at


@dataclass
class RunResult:
    workflow: str
    state: str  # "complete" | "failed"
    inputs: dict[str, Any]
    nodes: dict[str, NodeResult] = field(default_factory=dict)
    final_state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    audit: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_wall_s(self) -> float:
        if not self.nodes:
            return 0.0
        return sum(n.wall_s for n in self.nodes.values())


def _topo_levels(graph: list[dict[str, Any]]) -> list[list[str]]:
    """Kahn's algorithm — returns a list of execution levels."""
    incoming: dict[str, set[str]] = {n["id"]: set(n.get("depends_on") or []) for n in graph}
    levels: list[list[str]] = []
    remaining = dict(incoming)
    while remaining:
        ready = sorted([nid for nid, deps in remaining.items() if not deps])
        if not ready:
            raise RuntimeError("topo_levels: cycle detected (validator should have caught this)")
        levels.append(ready)
        for r in ready:
            del remaining[r]
        for deps in remaining.values():
            deps.difference_update(ready)
    return levels


def _direct_dependents(graph: list[dict[str, Any]]) -> dict[str, list[str]]:
    """node_id -> [ids of nodes whose depends_on includes node_id]."""
    out: dict[str, list[str]] = {n["id"]: [] for n in graph}
    for n in graph:
        for dep in n.get("depends_on") or []:
            out.setdefault(dep, []).append(n["id"])
    return out


def _transitive_dependents(dependents: dict[str, list[str]], node_id: str) -> set[str]:
    """All nodes reachable downstream from node_id (ADR-0188 M3/M4: used to
    propagate 'skipped' — an unmatched route branch or a fail_branch retry
    exhaustion — to everything that would otherwise run on dead data)."""
    seen: set[str] = set()
    stack = list(dependents.get(node_id, []))
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(dependents.get(nid, []))
    return seen


_DEFAULT_ERROR_STRATEGY = "abort"
_ERROR_STRATEGIES = {"abort", "fail_branch"}


def _retry_config(node_spec: dict[str, Any]) -> tuple[int, float, str]:
    """Parse the optional `retry:` block (ADR-0188 M4). Absent -> unchanged
    legacy behavior: 1 attempt, abort the whole run on failure."""
    cfg = node_spec.get("retry") or {}
    max_retries = int(cfg.get("max_retries", 0))
    interval_s = float(cfg.get("retry_interval_s", 0))
    strategy = cfg.get("error_strategy", _DEFAULT_ERROR_STRATEGY)
    if strategy not in _ERROR_STRATEGIES:
        raise ValueError(
            f"retry.error_strategy must be one of {sorted(_ERROR_STRATEGIES)}, got {strategy!r}"
        )
    return max_retries, interval_s, strategy


def _share_output(node_spec: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Apply the YAML `share_output: [field1, field2]` projection.

    If `share_output` is unset, the whole output is published; otherwise only
    the listed fields. Mirrors AWP's DAG semantics.
    """
    share = node_spec.get("share_output")
    if share is None:
        return dict(output)
    if not isinstance(share, list):
        raise ValueError("share_output must be a list of field names")
    return {k: output.get(k) for k in share}


class DAGRunner:
    """Synchronous DAG runner.

    Construction:
        runner = DAGRunner(doc, engine=stub_engine)

    Each run:
        result = runner.run(inputs={...})
    """

    def __init__(
        self,
        doc: WorkflowDoc,
        *,
        engine: Any,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.doc = doc
        self.engine = engine
        self._audit_sink = audit_sink
        self._audit_buffer: list[dict[str, Any]] = []

    def _audit(self, event: str, **details: Any) -> None:
        rec = {"event": event, **details}
        self._audit_buffer.append(rec)
        if self._audit_sink is not None:
            self._audit_sink(rec)

    def run(self, inputs: dict[str, Any] | None = None) -> RunResult:
        import time as _time

        inputs = dict(inputs or {})
        graph = self.doc.graph
        node_specs = {n["id"]: n for n in graph}
        levels = _topo_levels(graph)
        dependents = _direct_dependents(graph)

        run = RunResult(
            workflow=self.doc.name,
            state="complete",
            inputs=inputs,
            audit=self._audit_buffer,
        )
        state: dict[str, Any] = {}
        skipped: set[str] = set()

        self._audit(
            "run.started",
            workflow=self.doc.name,
            input_keys=sorted(inputs.keys()),
            levels=len(levels),
            nodes=len(graph),
        )

        for level_index, level in enumerate(levels):
            self._audit("run.level", level=level_index, nodes=list(level))
            for nid in level:
                node = node_specs[nid]
                ntype = node.get("type", "agent")

                if nid in skipped:
                    t_now = perf_counter()
                    run.nodes[nid] = NodeResult(
                        node_id=nid, node_type=ntype,
                        started_at=t_now, finished_at=t_now,
                        output={}, status="skipped",
                    )
                    self._audit("node.skipped", node_id=nid, node_type=ntype)
                    continue

                executor = NODE_TYPES[ntype]["execute"]
                max_retries, interval_s, error_strategy = _retry_config(node)

                t0 = perf_counter()
                self._audit("node.started", node_id=nid, node_type=ntype)
                output: dict[str, Any] | None = None
                last_error: Exception | None = None
                attempts = 0
                for attempts in range(1, max_retries + 2):  # first try + N retries
                    try:
                        output = executor(
                            node=node, engine=self.engine, state=state,
                            inputs=inputs, audit=self._audit,
                        )
                        last_error = None
                        break
                    except Exception as e:  # noqa: BLE001 — every node failure is reported
                        last_error = e
                        self._audit(
                            "node.attempt_failed", node_id=nid, node_type=ntype,
                            attempt=attempts, error=f"{type(e).__name__}: {e}",
                        )
                        if attempts <= max_retries:
                            if interval_s > 0:
                                _time.sleep(interval_s)
                            continue

                t1 = perf_counter()

                if last_error is not None:
                    err_text = f"{type(last_error).__name__}: {last_error}"
                    self._audit(
                        "node.failed", node_id=nid, node_type=ntype,
                        error=err_text, attempts=attempts, error_strategy=error_strategy,
                    )
                    run.nodes[nid] = NodeResult(
                        node_id=nid, node_type=ntype,
                        started_at=t0, finished_at=t1,
                        output={}, error=err_text, status="failed", attempts=attempts,
                    )
                    if error_strategy == "fail_branch":
                        # Contain the failure to this branch: skip everything
                        # downstream of it, keep running the rest of the graph.
                        newly_skipped = _transitive_dependents(dependents, nid)
                        skipped |= newly_skipped
                        self._audit(
                            "node.branch_failed", node_id=nid,
                            skipped=sorted(newly_skipped),
                        )
                        continue
                    # Default "abort" strategy — unchanged legacy behavior.
                    run.state = "failed"
                    run.error = f"node {nid!r}: {err_text}"
                    run.final_state = state
                    self._audit("run.terminal", state="failed", node_id=nid)
                    return run

                # Success — publish projection into state.
                assert output is not None
                state[nid] = _share_output(node, output)
                run.nodes[nid] = NodeResult(
                    node_id=nid, node_type=ntype,
                    started_at=t0, finished_at=t1,
                    output=output, attempts=attempts,
                )
                self._audit(
                    "node.completed", node_id=nid, node_type=ntype,
                    wall_ms=int((t1 - t0) * 1000), attempts=attempts,
                )

                # route (ADR-0188 M3): skip every directly-dependent node
                # tagged `branch: <case>` that does not match the chosen case.
                if ntype == "route":
                    chosen = output.get("case")
                    for dep_id in dependents.get(nid, []):
                        dep_branch = node_specs[dep_id].get("branch")
                        if dep_branch is not None and dep_branch != chosen:
                            newly_skipped = {dep_id} | _transitive_dependents(dependents, dep_id)
                            skipped |= newly_skipped
                            self._audit(
                                "node.branch_skipped", node_id=nid, chosen=chosen,
                                skipped=sorted(newly_skipped),
                            )

        run.final_state = state
        failed_ct = sum(1 for n in run.nodes.values() if n.status == "failed")
        skipped_ct = sum(1 for n in run.nodes.values() if n.status == "skipped")
        self._audit(
            "run.terminal", state="complete", nodes=len(run.nodes),
            failed_branches=failed_ct, skipped=skipped_ct,
        )
        return run
