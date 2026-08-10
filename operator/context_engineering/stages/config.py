"""Config-driven pipeline resolution + topological ordering (ADR-0280).

The pipeline is DATA: `spec.context_engineering.pipeline` in tenant.corvin.yaml,
a list of `{stage: id, ...per-stage config}`. Absent → the default-safe pipeline
(the five vetted first-party stages = today's behaviour). Unknown ids are dropped
(the runner audits them). The runner topo-sorts by each stage's `requires` so a
config reorder can never break the dependency chain (memory is the root).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import get_stage

DEFAULT_PIPELINE = ["memory", "graph", "skill", "approach_synthesis", "blocker_id"]


@dataclass
class StageSpec:
    id: str
    config: dict = field(default_factory=dict)   # per-stage config (when/model/budget)


def _read_pipeline_config(tenant_id: str) -> "list | None":
    """Best-effort read of spec.context_engineering.pipeline. None ⇒ use default."""
    try:
        # Reuse the console's tenant-spec reader (already importable from the CEL
        # call sites); a shared public reader is the P-A follow-up, not a 3rd copy.
        from corvin_console import feature_flags as _ff  # noqa: PLC0415
        spec = _ff._tenant_spec(tenant_id) or {}  # type: ignore[attr-defined]
        ce = spec.get("context_engineering") or {}
        pipe = ce.get("pipeline")
        return pipe if isinstance(pipe, list) else None
    except Exception:  # noqa: BLE001 — any read failure → default-safe pipeline
        return None


def resolve_pipeline(tenant_id: str = "_default") -> "tuple[list, list]":
    """Return (specs, dropped_ids). Each spec is a StageSpec (id + config). Unknown
    or non-registered ids are dropped and returned separately for auditing."""
    raw = _read_pipeline_config(tenant_id)
    entries = raw if raw is not None else [{"stage": s} for s in DEFAULT_PIPELINE]
    specs: list[StageSpec] = []
    dropped: list[str] = []
    for e in entries:
        sid = e.get("stage") if isinstance(e, dict) else e
        if not sid or get_stage(sid) is None:
            dropped.append(str(sid))
            continue
        # Accept BOTH shapes (review finding #3): a nested {"stage": id, "config":
        # {...}} (what the editor writes) and flat {"stage": id, model: …} keys.
        if isinstance(e, dict):
            cfg = e["config"] if isinstance(e.get("config"), dict) else {
                k: v for k, v in e.items() if k != "stage"}
        else:
            cfg = {}
        specs.append(StageSpec(id=sid, config=cfg))
    return specs, dropped


def topo_order(specs: list) -> list:
    """Order specs so every stage's `requires` (that are present) come first.
    Stable within free degrees (keeps the operator's order where deps allow).
    Raises ValueError on a cycle. Ids not in the spec set are ignored as edges."""
    present = {s.id for s in specs}
    by_id = {s.id: s for s in specs}
    ordered: list = []
    done: set = set()
    visiting: set = set()

    def visit(sid: str) -> None:
        if sid in done:
            return
        if sid in visiting:
            raise ValueError(f"cycle in pipeline requires at {sid!r}")
        visiting.add(sid)
        stage = get_stage(sid)
        for dep in (getattr(stage, "requires", ()) if stage else ()):
            if dep in present:
                visit(dep)
        visiting.discard(sid)
        done.add(sid)
        ordered.append(by_id[sid])

    for s in specs:  # preserve operator order as the outer iteration
        visit(s.id)
    return ordered
