"""Vibe Engineering — consolidated Context Engineering pipeline (ADR-0275/0280).

As of P-A (ADR-0280) `build_brief` is a **config-driven runner** over a registry
of `ContextStage`s (operator/context_engineering/stages/), not five hard-coded
calls. Behaviour is preserved for the default pipeline (parity-tested): the five
first-party stages run in dependency order (memory → graph → skill →
approach_synthesis → blocker_id), memory is the non-removable root.

Single license-meter call at the boundary (ADR-0276); a stage that raises is
recorded `failed` and never breaks the turn; memory failing → no brief (nothing
downstream can attach). Invariant I1: build_brief only shapes the prompt; the
pre-spawn gates still inspect the task (P-A stages are all `pure`, pre-gate).
"""
from __future__ import annotations

from typing import Any

# Backward-compat re-exports — the helpers moved to stages/_util.py (some tests +
# the bridge import these names from pipeline).
from .stages._util import (  # noqa: F401
    confidence_tier as _confidence_tier,
    avg as _avg,
    scan_blockers as _scan_blockers,
    task_adapter as _task_adapter,
)


def build_brief(task: str, tenant: str = "_default", session: Any = None,
                meter: bool = True) -> "tuple[Any, dict]":
    """Backward-compatible façade: run the pipeline, return ``(brief, trace)``.
    The bridge/console call this. ``build_context`` (below) exposes the full
    ContextBundle (incl. the P-B binding channels) for callers that provision the
    worker with tools/skills, not just text."""
    bundle, trace = build_context(task, tenant, session, meter)
    return (bundle.brief if bundle is not None else None), trace


def build_context(task: str, tenant: str = "_default", session: Any = None,
                  meter: bool = True) -> "tuple[Any, dict]":
    """Run the config-resolved stage pipeline; return ``(bundle, trace)``.

    ``bundle`` is a ContextBundle (or None if the license gate degrades this turn,
    the pipeline has a cycle, or memory fails). ``trace`` carries per-stage
    telemetry + ``degraded`` when the turn ran on plain context. ``meter=False``
    bypasses the license gate (tests / internal reuse).
    """
    trace: dict[str, Any] = {"task_preview": task[:120], "stages": []}

    if meter:
        try:
            from .license_gate import enforce_ce_quota  # noqa: PLC0415
            if not enforce_ce_quota(tenant):
                trace["degraded"] = "ce_budget_or_license"
                return None, trace   # plain context — zero stages run
        except Exception:  # noqa: BLE001 — a broken gate must never break the turn
            pass

    from .stages import (  # noqa: PLC0415 — lazy: registers stages on first use
        ContextBundle, StageCtx, resolve_pipeline, topo_order, get_stage,
    )
    from .stages._util import task_adapter  # noqa: PLC0415

    bundle = ContextBundle(task=task)
    ctx = StageCtx(tenant_id=tenant, task_obj=task_adapter(task),
                   session_id=getattr(session, "sid", "") or "")

    specs, dropped = resolve_pipeline(tenant)
    for d in dropped:  # unknown/unregistered stage ids — audited, never a crash
        trace["stages"].append({"stage": d, "status": "not_run",
                                "reason": "unknown_stage"})
    try:
        ordered = topo_order(specs)
    except ValueError:
        trace["degraded"] = "pipeline_cycle"
        return None, trace

    # Gate-split (ADR-0280 R2 / ADR-0282): PURE stages run here, pre-gate. Stages
    # with side effects (effect=egress/forge — LLM synthesis, ToolForge) are
    # DEFERRED; the caller runs them via build_context_post_gate AFTER Gate-1
    # approved the task, then re-gates the final payload (Gate-2) before the spawn.
    deferred: list = []
    for spec in ordered:
        stage = get_stage(spec.id)
        if stage is None:
            continue
        if getattr(stage, "effect", "pure") != "pure":
            deferred.append(spec)
            trace["stages"].append({"stage": spec.id, "status": "deferred",
                                    "reason": "post_gate"})
            continue
        ctx.config = spec.config
        try:
            bundle, tel = stage.run(bundle, ctx)
            trace["stages"].append(tel.to_trace())
        except Exception as e:  # noqa: BLE001 — one stage never breaks the turn
            trace["stages"].append({"stage": spec.id, "status": "failed",
                                    "error": str(e)[:120]})
            if spec.id == "memory" or bundle.brief is None:
                return None, trace   # no brief → nothing downstream can attach

    bundle.scratch["_deferred"] = deferred
    bundle.scratch["_ctx"] = ctx
    return bundle, trace


async def build_context_post_gate(bundle: Any, trace: dict) -> Any:
    """Run the deferred egress/forge stages AFTER the caller's Gate-1 approved the
    task (ADR-0282). Blocking stages run via ``asyncio.to_thread`` so a `claude -p`
    subprocess never blocks the event loop. The caller MUST re-gate the final
    assembled payload (Gate-2) before spawning the worker. Fail-safe: a stage that
    raises is recorded failed and the pipeline continues (degrade, never block)."""
    import asyncio  # noqa: PLC0415
    from .stages import get_stage  # noqa: PLC0415

    if bundle is None:  # degraded pre-gate (meter deny / cycle / memory fail)
        return None
    ctx = bundle.scratch.get("_ctx")
    for spec in bundle.scratch.get("_deferred", []):
        stage = get_stage(spec.id)
        if stage is None:
            continue
        if ctx is not None:
            ctx.config = spec.config
        try:
            bundle, tel = await asyncio.to_thread(stage.run, bundle, ctx)
            trace["stages"].append(tel.to_trace() if hasattr(tel, "to_trace") else tel)
        except Exception as e:  # noqa: BLE001
            trace["stages"].append({"stage": spec.id, "status": "failed",
                                    "error": str(e)[:120]})
    return bundle


def render_brief_to_text(brief: Any) -> str:
    """Format the brief into a compact system-prompt block. Empty string when
    there is nothing useful (I5: off is a quiet path). Unchanged from pre-P-A for
    parity; the LLM synthesis stage (P-C) may set a ``synthesised_prompt`` that
    the boundary prefers over this rendering."""
    if brief is None:
        return ""
    lines: list[str] = []
    mc = getattr(brief, "memory_context", None)
    matches = getattr(mc, "matches", []) if mc else []
    if matches:
        lines.append("Relevant past memory:")
        for m in matches[:5]:
            lines.append(f"  - {getattr(m, 'title', None) or getattr(m, 'filename', '?')}")
    rel = getattr(brief, "related_decisions", None) or []
    if rel:
        lines.append("Related decisions (ADRs):")
        for d in rel[:5]:
            lines.append(f"  - {getattr(d, 'decision_id', '?')}: {getattr(d, 'title', '')}")
    sk = getattr(brief, "recommended_skills", None) or []
    if sk:
        lines.append("Recommended skills:")
        for s in sk[:5]:
            lines.append(f"  - {getattr(s, 'title', None) or getattr(s, 'skill_id', '?')}")
    ap = getattr(brief, "approach", None) or []
    if ap:
        lines.append("Suggested focus (densest signal — synthesise from here):")
        lines.append("  - " + " · ".join(str(a) for a in ap))
    bl = getattr(brief, "blockers", None) or []
    if bl:
        lines.append("Constraints / blockers the context flags — respect these:")
        for b in bl[:5]:
            lines.append(f"  - {b}")
    if not lines:
        return ""
    return "## Context brief (Vibe Engineering)\n" + "\n".join(lines)
