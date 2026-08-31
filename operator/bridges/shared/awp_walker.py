"""awp_walker.py — Phase 6 (ADR-0006): engine-driven DAG executor.

Reads a parsed ``DAG`` (from ``awp_dag_parser``), walks each node in
topological order, and executes the work via the **engine layer**
(``ClaudeCodeEngine`` / ``CodexCliEngine`` / future Gemini / Ollama —
through ``engine_registry.get_engine``). Every step's output is
validated against R17 (``awp_validator.validate_node_output``) before
landing in the shared state dict. Budget breaches abort the walk with
a structured failure record.

**No imports from ``awp.runtime.*``** — this is the Phase-6 standards-
only execution path mandated by ADR-0005. AWP's YAML schema is the
*input contract*; engines do the actual execution.

Walker contract:

    walk(dag, *, engine_factory, on_node_start=None, on_node_complete=None,
         http_caller=None, audit_writer=None) -> WalkResult

Each node maps to one engine spawn (or, for ``execution_kind="http"``,
one ``http_caller(prompt, model)`` call — the hybrid escape hatch from
ADR-0005 § Hybrid execution). The walker never imports an HTTP client
itself; the caller injects ``http_caller`` if HTTP-direct steps are
desired. Default behaviour: every node uses the engine layer.

State mutation rules:
  * Each node reads from the accumulating ``state`` dict.
  * The node's prompt is rendered with simple ``{state.foo}``
    substitution before being handed to the engine.
  * The node's R17-validated result lands at ``state[node.output_key]``
    (default = node.id).
  * The shared ``state["meta"]`` block receives ``{node_id, engine_id,
    elapsed_s, tokens_in, tokens_out}`` per executed node.
  * ``state["worker_engine_factory"]`` is *injected* per ADR-0005
    convention so any forge / sub-tool that reads it gets the
    walker's factory — but the walker itself never reads back from
    AWP runtime.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# Ensure shared/ (same dir as this file) is in sys.path so engine_span.py
# is always importable, regardless of how awp_walker is imported.
_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# ADR-0171 — universal engine span (role=worker) for the CorvinFlow node executor.
try:
    import engine_span as _espan  # type: ignore
except Exception:  # noqa: BLE001
    _espan = None  # type: ignore[assignment]


def _emit_awp_engine_span(kind: str, *, span_id: str, engine_id: str,
                          status: str = "ok", duration_ms: int = 0) -> None:
    """Best-effort engine.span.start/end on the OS chain (audit_event). The flow
    walker has no L16 chain of its own; this keeps every flow-node engine
    invocation auditable. Metadata-only; never raises."""
    if _espan is None:
        return
    try:
        from audit import audit_event  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    try:
        if kind == "start":
            _espan.emit_start(audit_event, span_id=span_id, role="worker",
                              engine_id=engine_id)
        else:
            _espan.emit_end(audit_event, span_id=span_id, role="worker",
                            engine_id=engine_id, status=status,
                            duration_ms=int(duration_ms))
    except Exception:  # noqa: BLE001
        pass

try:
    from . import awp_dag_parser as _dp  # type: ignore
    from . import awp_validator as _val  # type: ignore
except ImportError:
    import awp_dag_parser as _dp  # type: ignore
    import awp_validator as _val  # type: ignore


@dataclass
class NodeRecord:
    """Per-node execution trace returned in WalkResult."""
    node_id: str
    engine_id: str
    execution_kind: str
    ok: bool
    elapsed_s: float
    error: str = ""
    confidence: float = 0.0


@dataclass
class WalkResult:
    """Outcome of one DAG walk."""
    workflow_id: str
    final_state: dict
    final_text: str = ""
    nodes: list[NodeRecord] = field(default_factory=list)
    budget_breaches: list[str] = field(default_factory=list)
    aborted_at: str = ""
    ok: bool = True


# ----- helpers ------------------------------------------------------------

def _render_prompt(template: str, state: dict) -> str:
    """Cheap ``{state.foo}`` substitution. Missing keys → literal
    ``{state.foo}`` left in place (no crash on missing). Operators
    who need full Jinja2 can wrap the walker."""
    out = template
    # Walk a snapshot of state keys; we only resolve top-level keys here.
    for key, value in state.items():
        marker = "{state." + key + "}"
        if marker in out:
            try:
                out = out.replace(marker, str(value))
            except Exception:  # noqa: BLE001
                pass
    return out


def _exec_engine(node: _dp.DAGNode, prompt: str, *,
                 engine_factory: Callable[..., Any]) -> tuple[Any, dict]:
    """Run one node through the engine layer. Returns (raw_text, telemetry).

    The engine instances expose either ``.spawn(prompt=...)`` (sync) or
    ``.spawn(prompt=...)`` returning an iterator of stream events. The
    walker is intentionally minimal — for the Phase-6 skeleton we treat
    the engine as a callable: ``engine.spawn(prompt) -> str``. Real
    engines have richer signatures; the test suite uses stub engines
    that match this minimal contract.
    """
    engine = engine_factory(node.engine_id)
    if engine is None:
        raise RuntimeError(f"engine_factory returned None for {node.engine_id!r}")

    # The minimal contract: callable with a prompt kwarg returning a
    # dict (R17-shaped) OR a tuple (text, telemetry_dict). Real engine
    # adapters can wrap themselves to match.
    started = time.monotonic()
    _span_id = f"spn-awp-{node.id}"
    _emit_awp_engine_span("start", span_id=_span_id, engine_id=node.engine_id)
    try:
        raw = engine.spawn(prompt=prompt)  # type: ignore[arg-type]
    except BaseException:
        _emit_awp_engine_span("end", span_id=_span_id, engine_id=node.engine_id,
                              status="error",
                              duration_ms=int((time.monotonic() - started) * 1000))
        raise
    elapsed = time.monotonic() - started
    _emit_awp_engine_span("end", span_id=_span_id, engine_id=node.engine_id,
                          status="ok", duration_ms=int(elapsed * 1000))
    telemetry = {"elapsed_s": round(elapsed, 3),
                 "engine_id": node.engine_id,
                 "execution_kind": "engine"}
    return raw, telemetry


def _exec_http(node: _dp.DAGNode, prompt: str, *,
               http_caller: Callable[..., Any]) -> tuple[Any, dict]:
    """HTTP-direct path (hybrid). Caller injects an http_caller that
    speaks the OpenAI chat-completions protocol or similar. Walker
    doesn't care which provider — that's the http_caller's job.

    Contract: ``http_caller(prompt: str, model: str) -> dict`` — must
    return an R17-shaped dict.
    """
    started = time.monotonic()
    raw = http_caller(prompt=prompt, model=node.model)
    elapsed = time.monotonic() - started
    telemetry = {"elapsed_s": round(elapsed, 3),
                 "model": node.model,
                 "execution_kind": "http"}
    return raw, telemetry


# ----- main walker --------------------------------------------------------

def walk(dag: _dp.DAG,
         *,
         engine_factory: Callable[..., Any],
         http_caller: Optional[Callable[..., Any]] = None,
         on_node_start: Optional[Callable[[_dp.DAGNode], None]] = None,
         on_node_complete: Optional[Callable[[_dp.DAGNode, NodeRecord], None]] = None,
         audit_writer: Optional[Callable[[str, str, dict], None]] = None,
         strict_r17: bool = True) -> WalkResult:
    """Execute a parsed DAG in topological order.

    Args:
      dag             : parsed DAG (from awp_dag_parser).
      engine_factory  : callable ``factory(engine_id: str) -> WorkerEngine``.
                        Returns None for unknown engine_ids; walker aborts.
      http_caller     : optional callable for ``execution_kind="http"``
                        nodes. Required iff any node uses http; otherwise
                        such nodes abort with "no http_caller".
      on_node_start   : optional callback fired before each node executes.
      on_node_complete: optional callback fired after each node, with
                        the NodeRecord.
      audit_writer    : optional ``(event_type, severity, details) -> None``
                        for hash-chain emission. Walker never imports the
                        audit module itself.
      strict_r17      : when True (default), an R17 violation aborts the
                        walk; when False, the walk continues with a
                        warning record.

    Returns a WalkResult — never raises into the caller. Failures are
    captured in ``aborted_at`` + ``ok=False``.
    """
    state = dict(dag.initial_state)
    state["meta"] = state.get("meta") or {}
    state["meta"]["workflow_id"] = dag.workflow_id
    state["meta"]["dag_started"] = time.time()
    # ADR-0005 carry-along: any sub-process / forge that knows the
    # convention can read state["worker_engine_factory"]. Walker itself
    # uses engine_factory directly.
    state["worker_engine_factory"] = engine_factory

    tracker = _val.BudgetTracker()
    records: list[NodeRecord] = []

    try:
        ordered = _dp.topological_order(dag)
    except ValueError as e:
        return WalkResult(workflow_id=dag.workflow_id,
                          final_state=state, ok=False,
                          aborted_at=f"topo_sort: {e}")

    for node in ordered:
        if on_node_start is not None:
            try:
                on_node_start(node)
            except Exception:  # noqa: BLE001
                pass

        prompt = _render_prompt(node.prompt, state)
        record = NodeRecord(node_id=node.id, engine_id=node.engine_id,
                            execution_kind=node.execution_kind,
                            ok=False, elapsed_s=0.0)

        try:
            if node.execution_kind == "http":
                if http_caller is None:
                    raise RuntimeError("execution_kind='http' but no http_caller provided")
                raw, telem = _exec_http(node, prompt, http_caller=http_caller)
            else:
                raw, telem = _exec_engine(node, prompt, engine_factory=engine_factory)
        except Exception as e:  # noqa: BLE001
            record.error = f"{type(e).__name__}: {e}"
            records.append(record)
            if audit_writer:
                try:
                    audit_writer("walker.node_failed", "ERROR",
                                 {"node_id": node.id,
                                  "engine_id": node.engine_id,
                                  "error": record.error[:300]})
                except Exception:  # noqa: BLE001
                    pass
            return WalkResult(
                workflow_id=dag.workflow_id, final_state=state,
                nodes=records, ok=False,
                aborted_at=f"node {node.id}: {record.error}",
            )

        record.elapsed_s = telem.get("elapsed_s", 0.0)
        tracker.charge(time_s=record.elapsed_s, workers=1)

        # R17 validation
        ok, detail = _val.validate_node_output(raw, node.id)
        if ok:
            record.ok = True
            record.confidence = float(raw[node.id].get("confidence", 0.0))
        else:
            record.error = detail
            if strict_r17:
                records.append(record)
                if audit_writer:
                    try:
                        audit_writer("walker.r17_violation", "ERROR",
                                     {"node_id": node.id,
                                      "engine_id": node.engine_id,
                                      "violation": detail})
                    except Exception:  # noqa: BLE001
                        pass
                return WalkResult(
                    workflow_id=dag.workflow_id, final_state=state,
                    nodes=records, ok=False,
                    aborted_at=f"node {node.id}: {detail}",
                )

        # Land result in state under output_key
        state[node.output_key] = raw
        state["meta"][node.id] = telem

        records.append(record)
        if audit_writer:
            try:
                audit_writer("walker.node_complete", "INFO",
                             {"node_id": node.id,
                              "engine_id": node.engine_id,
                              "elapsed_s": record.elapsed_s,
                              "confidence": record.confidence,
                              "execution_kind": node.execution_kind})
            except Exception:  # noqa: BLE001
                pass
        if on_node_complete is not None:
            try:
                on_node_complete(node, record)
            except Exception:  # noqa: BLE001
                pass

        # Budget check after every node
        ok_b, breaches = _val.validate_budget(tracker, dag.budget)
        if not ok_b:
            if audit_writer:
                try:
                    audit_writer("walker.budget_exceeded", "WARNING",
                                 {"breaches": breaches})
                except Exception:  # noqa: BLE001
                    pass
            return WalkResult(
                workflow_id=dag.workflow_id, final_state=state,
                nodes=records, ok=False,
                aborted_at=f"budget exceeded: {';'.join(breaches)}",
                budget_breaches=breaches,
            )

    # Pick final text from the designated output node (or last node).
    final_text = ""
    output_id = dag.output_node or (ordered[-1].id if ordered else "")
    if output_id and output_id in state:
        final_text = _val.extract_text(state[output_id], output_id)

    state["meta"]["dag_completed"] = time.time()
    return WalkResult(
        workflow_id=dag.workflow_id, final_state=state,
        final_text=final_text, nodes=records, ok=True,
    )
