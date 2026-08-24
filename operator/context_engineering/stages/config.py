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

# The full "Context Brain" pipeline (ADR-0282/0283) used when the operator turns
# on the active pipeline (vibe_engineering_active flag) and has NOT hand-authored
# their own pipeline in tenant.corvin.yaml. It adds the LLM synthesis stage (egress
# ON — this is the deliberate opt-in) and the ToolForge + SkillForge stages so the
# worker is provisioned with forged tools/skills, not just a text brief. Every
# egress/forge stage still runs POST-gate, and the final payload is re-gated
# (Gate-2) before the spawn — see pipeline.run_full_pipeline.
ACTIVE_PIPELINE = [
    {"stage": "memory"},
    {"stage": "graph"},
    {"stage": "skill"},
    {"stage": "llm_synthesis", "config": {"egress_ok": True}},
    {"stage": "toolforge"},
    {"stage": "skillforge"},
    # Honor a user-explicitly-named EXISTING on-disk skill (session web:9gCJXQnmhy):
    # a post-gate forge stage so skills_to_bind is never populated pre-Gate-1, and
    # Gate-2 still inspects the body it adds. Namespace-gated + capped + fail-closed.
    {"stage": "explicit_skill"},
    {"stage": "blocker_id"},
]


@dataclass
class StageSpec:
    id: str
    config: dict = field(default_factory=dict)   # per-stage config (when/model/budget)


def _read_ce_config(tenant_id: str) -> dict:
    """Best-effort read of ``spec.context_engineering``. {} on any failure."""
    try:
        # Reuse the console's tenant-spec reader (already importable from the CEL
        # call sites); a shared public reader is the P-A follow-up, not a 3rd copy.
        from corvin_core import feature_flags as _ff  # noqa: PLC0415
        spec = _ff._tenant_spec(tenant_id) or {}  # type: ignore[attr-defined]
        ce = spec.get("context_engineering") or {}
        return ce if isinstance(ce, dict) else {}
    except Exception:  # noqa: BLE001 — any read failure → default-safe behaviour
        return {}


def _read_pipeline_config(tenant_id: str) -> "list | None":
    """Best-effort read of spec.context_engineering.pipeline. None ⇒ use default."""
    pipe = _read_ce_config(tenant_id).get("pipeline")
    return pipe if isinstance(pipe, list) else None


def _load_community_stages(tenant_id: str) -> None:
    """Register the community stages the operator DECLARED, so they exist by the
    time the pipeline is resolved (ADR-0289).

    Surface: ``spec.context_engineering.community_stages`` in tenant.corvin.yaml,
    a list of ``{id, path, requires?}``. The same file the pipeline itself lives
    in — no new configuration surface, and no marketplace: the operator points at
    a file they already decided to trust enough to place on the box.

    Without this the sandbox would have had no production caller at all: a
    mechanism reachable only from a Python REPL is the dead-mechanism class this
    phase's own review round exists to prevent (CONCEPT-0008). Fail-safe — a
    malformed entry is skipped, and a host without isolation registers nothing.
    """
    try:
        from .registry import get_stage, register_community_stage  # noqa: PLC0415
        spec = _read_ce_config(tenant_id)
        for e in (spec.get("community_stages") or []):
            if not isinstance(e, dict):
                continue
            sid, path = e.get("id"), e.get("path")
            if not isinstance(sid, str) or not sid or not path:
                continue
            if get_stage(sid) is not None:
                continue          # already registered (or a builtin id — never shadow)
            req = e.get("requires")
            register_community_stage(
                sid, path,
                requires=tuple(req) if isinstance(req, (list, tuple)) else ())
    except Exception:  # noqa: BLE001 — a bad declaration never breaks a turn
        return


def _default_eligible(tenant_id: str, stage_id: str) -> bool:
    """May this stage sit in a pipeline the operator did NOT author?

    Builtin → always (vetted). Otherwise it needs operator grades over the
    ADR-0285 threshold. Fail-CLOSED on any error: an unreadable grade store must
    not silently promote an unproven community stage into the default path."""
    try:
        from .grades import is_default_eligible  # noqa: PLC0415
        from .registry import builtin_ids  # noqa: PLC0415
        return is_default_eligible(tenant_id, stage_id, builtin_ids())
    except Exception:  # noqa: BLE001
        return False


def resolve_pipeline(tenant_id: str = "_default",
                     active: bool = False) -> "tuple[list, list]":
    """Return (specs, dropped_ids). Each spec is a StageSpec (id + config). Unknown
    or non-registered ids are dropped and returned separately for auditing.

    An operator-authored ``spec.context_engineering.pipeline`` always wins. Absent,
    the fallback is ACTIVE_PIPELINE when ``active`` (the full Context Brain — LLM
    synthesis + ToolForge + SkillForge) else the five-stage DEFAULT_PIPELINE."""
    # Declared community stages must be registered BEFORE the ids below are
    # resolved, or an operator's own pipeline entry would be dropped as unknown.
    _load_community_stages(tenant_id)
    raw = _read_pipeline_config(tenant_id)
    # An operator-AUTHORED pipeline is opt-in use: an ungraded community stage is
    # allowed there, because that is the only way it can ever earn its first
    # grade (ADR-0284 R1c, the henne-ei trap). A DEFAULT pipeline is different —
    # nobody chose those stages, so the grade gate applies (ADR-0285). Before
    # P-G this distinction had no subject: every registered stage was builtin and
    # therefore always eligible, which is why `is_default_eligible` sat dormant.
    authored = raw is not None
    if authored:
        entries = raw
    elif active:
        entries = ACTIVE_PIPELINE
    else:
        entries = [{"stage": s} for s in DEFAULT_PIPELINE]
    specs: list[StageSpec] = []
    dropped: list[str] = []
    for e in entries:
        sid = e.get("stage") if isinstance(e, dict) else e
        # A hostile/malformed config can make sid a list/dict (unhashable) — that
        # must be dropped+audited, not crash get_stage's dict lookup (review R2 A5).
        if not isinstance(sid, str) or not sid or get_stage(sid) is None:
            dropped.append(str(sid))
            continue
        # Grade gate on the DEFAULT path only (ADR-0285, live since ADR-0289):
        # a non-builtin stage the operator did not explicitly author needs a
        # passing mean over a minimum sample before it may run by default.
        if not authored and not _default_eligible(tenant_id, sid):
            dropped.append(sid)
            continue
        # Accept BOTH shapes (review finding #3): a nested {"stage": id, "config":
        # {...}} (what the editor writes) and flat {"stage": id, model: …} keys. A
        # present-but-non-dict config is IGNORED, not reshaped into {"config": […]}
        # (review R2 finding C8).
        if isinstance(e, dict):
            if isinstance(e.get("config"), dict):
                cfg = e["config"]
            elif "config" in e:
                cfg = {}   # malformed config → ignore
            else:
                cfg = {k: v for k, v in e.items() if k != "stage"}
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
