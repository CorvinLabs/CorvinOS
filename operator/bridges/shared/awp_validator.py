"""awp_validator.py — Phase 6 (ADR-0006): R17 output-contract validator
+ 5D-budget tracking.

Pure-Python implementation of AWP's validation rules from
``spec/versions/1.0/validation-rules.md`` (R17) and the budget
envelope from layer-04. **No imports from ``awp.runtime.*``** — this
is a re-implementation against the spec, not a runtime delegation.

R17 contract:
  Every worker output MUST be a dict containing at minimum::
      {
        "<worker_id>": {
          "confidence": <float 0.0..1.0>,
          ...
        }
      }
  Additional top-level keys are allowed; the worker's result lives
  under its own id.

Budget envelope (5D):
  - tokens (sum of input + output across all nodes)
  - time_s (wall clock)
  - max_loops (re-dispatch count in delegation pattern)
  - max_workers (distinct worker-instance count)
  - max_tool_calls (sum of tool_use events across all nodes)

Each axis: zero means "unbounded for this axis" (operator-friendly
default). Validator returns (ok, detail) — caller decides whether to
abort the walk or just log the breach.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BudgetTracker:
    """Mutable accumulator for a walk. Caller chargs each node's spend."""
    tokens_spent: int = 0
    time_s_spent: float = 0.0
    loops_spent: int = 0
    workers_spent: int = 0
    tool_calls_spent: int = 0

    def charge(self, *, tokens: int = 0, time_s: float = 0.0,
               loops: int = 0, workers: int = 0, tool_calls: int = 0) -> None:
        self.tokens_spent += int(tokens)
        self.time_s_spent += float(time_s)
        self.loops_spent += int(loops)
        self.workers_spent += int(workers)
        self.tool_calls_spent += int(tool_calls)


def validate_node_output(result: Any, worker_id: str) -> tuple[bool, str]:
    """R17 check: result MUST be a dict containing
    ``result[worker_id]['confidence'] in [0.0, 1.0]`` (numeric).

    Returns (ok, detail). On ok=True, detail is empty. On ok=False,
    detail names the first violation.
    """
    if not isinstance(result, dict):
        return False, f"R17: result must be dict, got {type(result).__name__}"
    if worker_id not in result:
        return False, f"R17: result missing key {worker_id!r}"
    payload = result[worker_id]
    if not isinstance(payload, dict):
        return False, f"R17: result[{worker_id!r}] must be dict, got {type(payload).__name__}"
    if "confidence" not in payload:
        return False, f"R17: result[{worker_id!r}].confidence missing"
    conf = payload["confidence"]
    try:
        conf_f = float(conf)
    except (TypeError, ValueError):
        return False, f"R17: result[{worker_id!r}].confidence must be numeric, got {conf!r}"
    if not (0.0 <= conf_f <= 1.0):
        return False, f"R17: result[{worker_id!r}].confidence={conf_f} outside [0.0, 1.0]"
    return True, ""


def validate_budget(tracker: BudgetTracker, budget) -> tuple[bool, list[str]]:
    """Check tracker against the DAG's budget envelope. Zero = unbounded
    for that axis. Returns (ok, breaches) where breaches is a list of
    human-readable axis-level violations."""
    breaches: list[str] = []
    if budget.tokens > 0 and tracker.tokens_spent > budget.tokens:
        breaches.append(f"tokens: {tracker.tokens_spent} > {budget.tokens}")
    if budget.time_s > 0 and tracker.time_s_spent > budget.time_s:
        breaches.append(f"time_s: {tracker.time_s_spent:.1f} > {budget.time_s}")
    if budget.max_loops > 0 and tracker.loops_spent > budget.max_loops:
        breaches.append(f"loops: {tracker.loops_spent} > {budget.max_loops}")
    if budget.max_workers > 0 and tracker.workers_spent > budget.max_workers:
        breaches.append(f"workers: {tracker.workers_spent} > {budget.max_workers}")
    if budget.max_tool_calls > 0 and tracker.tool_calls_spent > budget.max_tool_calls:
        breaches.append(f"tool_calls: {tracker.tool_calls_spent} > {budget.max_tool_calls}")
    return (not breaches), breaches


def extract_text(result: dict, worker_id: str) -> str:
    """Convenience: pull the first user-facing string out of an R17
    result dict. Order: result[worker_id][k] for k in (output, text,
    answer, summary, result). Falls back to JSON dump."""
    if not isinstance(result, dict):
        return str(result)
    payload = result.get(worker_id)
    if not isinstance(payload, dict):
        return str(result)
    for k in ("output", "text", "answer", "summary", "result"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    import json
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False)
