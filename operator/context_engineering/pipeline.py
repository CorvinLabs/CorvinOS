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
                  meter: bool = True, active: bool = False) -> "tuple[Any, dict]":
    """Run the config-resolved stage pipeline; return ``(bundle, trace)``.

    ``bundle`` is a ContextBundle (or None if the license gate degrades this turn,
    the pipeline has a cycle, or memory fails). ``trace`` carries per-stage
    telemetry + ``degraded`` when the turn ran on plain context. ``meter=False``
    bypasses the license gate (tests / internal reuse). ``active=True`` falls back
    to ACTIVE_PIPELINE (egress/forge) when the operator authored no pipeline.
    """
    # Coerce a non-str task before task_adapter().split() (review R3 finding C4):
    # this runs BEFORE the per-stage try, so a bad input would otherwise raise out
    # of the whole pipeline rather than degrade to a recorded trace.
    if not isinstance(task, str):
        task = str(task or "")
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

    specs, dropped = resolve_pipeline(tenant, active=active)
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


# ── Boundary façades (review R6) ────────────────────────────────────────────
# The live spawn boundaries (bridge adapter, console chat_runtime, ACS runtime)
# load this package by FILE PATH and reach it through the top-level module object,
# so the P-B binding helpers need a stable entry point here. They stay LAZY — the
# `.stages` package registers every first-party stage at import, and pulling that
# into `__init__` would make a plain `import context_engineering` do the stage
# import work even for a caller that only wants `build_brief`.

def apply_tool_bindings(bundle: Any, persona_patterns, allowed_tools, mcp_config,
                        persona_caps=None) -> "tuple[list, dict, list]":
    """See stages.binding.apply_tool_bindings — merge a bundle's tool bindings into
    a turn's (allowed_tools, mcp_config) after capability-class re-validation."""
    from .stages.binding import apply_tool_bindings as _impl  # noqa: PLC0415
    return _impl(bundle, persona_patterns, allowed_tools, mcp_config, persona_caps)


def render_skill_bindings(bundle: Any) -> str:
    """See stages.binding.render_skill_bindings — the skill-injection channel."""
    from .stages.binding import render_skill_bindings as _impl  # noqa: PLC0415
    return _impl(bundle)


def strip_for_remote(bundle: Any) -> bool:
    """See stages.binding.strip_for_remote — ADR-0279 reach boundary."""
    from .stages.binding import strip_for_remote as _impl  # noqa: PLC0415
    return _impl(bundle)


def _safe_gate(gate, text: str) -> "tuple[bool, str]":
    """Invoke the compliance gate; a gate that RAISES must DENY, never fail-open
    (review R2 finding A3 — an un-wrapped gate exception propagated out of the
    enforcer, and a caller catching it could then spawn ungated)."""
    try:
        ok, reason = gate(text)
        # Only an EXACT True allows (review R2 finding B3): a gate that returns a
        # truthy non-bool first element — e.g. ("allow", …) or (reason_string, …)
        # from a shape-confused refactor — must DENY, not be coerced to allow.
        return (ok is True), str(reason or "")
    except Exception as e:  # noqa: BLE001 — gate error → deny (fail-closed)
        return False, f"gate_error:{str(e)[:80]}"


def _tenant_of(bundle: Any) -> str:
    ctx = bundle.scratch.get("_ctx")
    return getattr(ctx, "tenant_id", "_default") if ctx is not None else "_default"


def _rollback_forged(bundle: Any, trace: dict, *, tool_names=None,
                     skill_names=None) -> None:
    """Un-create forged tools/skills a gate/re-validation rejected (review R2 A4:
    the artifact is written to disk PRE-Gate-2, so a denial must roll it back or a
    forge_enabled persona could invoke the denied tool by name). Best-effort."""
    tenant = _tenant_of(bundle)
    if tool_names:
        from .stages.toolforge import uncreate_tools  # noqa: PLC0415
        uncreate_tools(tenant, tool_names)
    if skill_names:
        from .stages.skillforge import uncreate_skills  # noqa: PLC0415
        uncreate_skills(tenant, skill_names)
    if tool_names or skill_names:
        rb = trace.setdefault("forged_rolled_back", {"tools": [], "skills": []})
        rb["tools"].extend(tool_names or [])
        rb["skills"].extend(skill_names or [])


def _gate1(bundle: Any, trace: dict, gate, task: str) -> bool:
    """Gate-1 on the raw task. Returns True to proceed to the egress/forge stages,
    False when the gate denied (nothing side-effecting then runs)."""
    ok, reason = _safe_gate(gate, task)
    if not ok:
        trace["gate1_denied"] = reason[:120]
        bundle.scratch["_deferred"] = []
    return ok


def _gate2_and_bind(bundle: Any, trace: dict, gate, persona_patterns,
                    persona_caps=None) -> Any:
    """Gate-2 on the FINAL assembled payload + class-based tool re-validation
    (bind ≠ authorise). Shared by the sync and async entry points so the enforcer
    logic lives in exactly one place.

    The payload Gate-2 inspects is EVERYTHING that reaches the worker: the
    synthesised prompt, the bound tool names, AND the forged-skill ids + bodies
    (review R2 finding A1 — skills reach the worker via the injection channel, so
    a skill body was previously un-gated). If Gate-2 denies, every forged artifact
    is rolled back (A4). Re-validation ALWAYS runs (A2): None/empty patterns drop
    all forged tools (fail-closed); the bridge passes ["*"] for an all-allowed
    persona so an all-allowed persona is not wrongly narrowed."""
    tool_names = " ".join(getattr(t, "name", "") for t in (bundle.tools_to_bind or []))
    # An mcp_config (server URL/command) reaches the worker via apply_tool_bindings
    # → Gate-2 must see it too (review R2 finding A3, defense-in-depth: forge tools
    # carry None today, but a future producer could set it).
    mcp_cfgs = " ".join(str(getattr(t, "mcp_config", "") or "")
                        for t in (bundle.tools_to_bind or []))
    skill_ids = " ".join(getattr(s, "skill_id", "") for s in (bundle.skills_to_bind or []))
    skill_bodies = " ".join(getattr(s, "body", "") for s in (bundle.skills_to_bind or []))
    # The DETERMINISTIC brief is part of the final payload too (review R6): when the
    # synthesis stage degrades (over budget, egress denied, timeout) both boundaries
    # inject `render_brief_to_text(bundle.brief)` instead — retrieved memory
    # passages and ADR/skill titles that Gate-1 never saw, because Gate-1 inspects
    # the RAW TASK. Gate-2's contract is "the spawn the worker gets is always what
    # Gate-2 inspected", so it must cover the fallback channel, not only the
    # synthesised one.
    rendered = "" if bundle.synthesised_prompt else (
        render_brief_to_text(getattr(bundle, "brief", None)) or "")
    final_payload = " ".join(
        x for x in (bundle.synthesised_prompt or "", rendered, tool_names, mcp_cfgs,
                    skill_ids, skill_bodies) if x).strip()
    if final_payload:
        ok2, reason2 = _safe_gate(gate, final_payload)
        if not ok2:
            trace["gate2_denied"] = reason2[:120]
            _rollback_forged(
                bundle, trace,
                tool_names=[f["name"] for f in bundle.scratch.get("_forged_tools", [])],
                skill_names=list(bundle.scratch.get("_forged_skills", [])))
            bundle.synthesised_prompt = None      # drop the un-approved synthesis
            bundle.tools_to_bind = []
            bundle.skills_to_bind = []
            return bundle
    if bundle.tools_to_bind:
        # A ToolRef carrying its OWN mcp_config is refused HERE, in the enforcer,
        # not at each boundary (review R7 — the first cut put it in the bridge, so
        # the console would silently have ignored it: the exact boundary-divergence
        # class this review exists to close; project memory "fix the primitive, not
        # the call site"). No producer sets it today, and neither boundary can plumb
        # an extra MCP server into an already-written config file, so binding the
        # NAME would hand the worker a tool it cannot call. Refusing here means the
        # forged artifact is rolled back with everything else below.
        _needs_srv = [t for t in bundle.tools_to_bind
                      if getattr(t, "mcp_config", None)]
        if _needs_srv:
            trace["mcp_config_unsupported"] = len(_needs_srv)
            bundle.tools_to_bind = [t for t in bundle.tools_to_bind
                                    if not getattr(t, "mcp_config", None)]
            dropped_names = {getattr(t, "name", "") for t in _needs_srv}
            _rollback_forged(
                bundle, trace,
                tool_names=[f["name"] for f in bundle.scratch.get("_forged_tools", [])
                            if f["ref"] in dropped_names])
    if bundle.tools_to_bind:
        from .stages.binding import revalidate_tools  # noqa: PLC0415
        kept, dropped = revalidate_tools(bundle.tools_to_bind, persona_patterns,
                                         persona_caps)
        bundle.tools_to_bind = kept
        if dropped:
            trace["tools_dropped"] = [getattr(t, "name", "?") for t in dropped]
            dropped_refs = {getattr(t, "name", "") for t in dropped}
            forged = bundle.scratch.get("_forged_tools", [])
            _rollback_forged(
                bundle, trace,
                tool_names=[f["name"] for f in forged if f["ref"] in dropped_refs])
    return bundle


def run_full_pipeline(task: str, tenant: str = "_default", session: Any = None,
                      meter: bool = True, *, gate_fn=None,
                      persona_patterns=None, persona_caps=None) -> "tuple[Any, dict]":
    """The FULL two-gate pipeline (ADR-0280 R2 / CONCEPT-0006 §9), SYNC, with EVERY
    enforcer in ONE place (the review's key demand — not delegated to a caller):

      build_context (pure, pre-gate)
        → Gate-1 on the raw task
        → deferred egress/forge stages (blocking; the caller runs in a worker
          context, not the event loop — the bridge spawn hook is sync)
        → Gate-2 on the FINAL payload (synthesised prompt + bound tool names)
        → class-based tool re-validation (bind ≠ authorise)

    ``gate_fn(text) -> (ok: bool, reason: str)`` is the caller's compliance gate
    (L44/L34/L35); default allow-all for tests. ``persona_patterns`` are the
    persona's allowed tool globs. Returns ``(bundle, trace)``; fail-safe throughout
    — a denial degrades (drops the un-approved egress/forge output), never blocks
    the turn. An event-loop caller would use ``run_full_pipeline_async`` (the
    console chat_runtime is NOT yet wired to the active brain — it still calls the
    deterministic ``build_brief``; the active brain is bridge-adapter-only today).
    """
    bundle, trace = build_context(task, tenant, session, meter, active=True)
    if bundle is None:
        return None, trace
    gate = gate_fn or (lambda _text: (True, ""))
    if not _gate1(bundle, trace, gate, task):
        return bundle, trace
    bundle = _run_deferred_sync(bundle, trace)
    return _gate2_and_bind(bundle, trace, gate, persona_patterns, persona_caps), trace


async def run_full_pipeline_async(task: str, tenant: str = "_default",
                                  session: Any = None, meter: bool = True, *,
                                  gate_fn=None, persona_patterns=None,
                                  persona_caps=None) -> "tuple[Any, dict]":
    """Async twin of :func:`run_full_pipeline` for the event-loop callers (console
    chat_runtime): the deferred stages run via ``asyncio.to_thread`` so a
    ``claude -p`` subprocess never blocks the loop. Same two-gate enforcer path."""
    bundle, trace = build_context(task, tenant, session, meter, active=True)
    if bundle is None:
        return None, trace
    gate = gate_fn or (lambda _text: (True, ""))
    if not _gate1(bundle, trace, gate, task):
        return bundle, trace
    bundle = await build_context_post_gate(bundle, trace)
    return _gate2_and_bind(bundle, trace, gate, persona_patterns, persona_caps), trace


def _run_deferred_sync(bundle: Any, trace: dict) -> Any:
    """Run the deferred egress/forge stages BLOCKING (no event loop). Fail-safe:
    a stage that raises is recorded failed and the pipeline continues."""
    from .stages import get_stage  # noqa: PLC0415
    if bundle is None:
        return None
    ctx = bundle.scratch.get("_ctx")
    for spec in bundle.scratch.get("_deferred", []):
        stage = get_stage(spec.id)
        if stage is None:
            continue
        if ctx is not None:
            ctx.config = spec.config
        try:
            bundle, tel = stage.run(bundle, ctx)
            trace["stages"].append(tel.to_trace() if hasattr(tel, "to_trace") else tel)
        except Exception as e:  # noqa: BLE001
            trace["stages"].append({"stage": spec.id, "status": "failed",
                                    "error": str(e)[:120]})
    return bundle


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


def _memory_body(match: Any, cap: int = 800) -> str:
    """Clean body of a memory match's source file (frontmatter + HTML comment stripped, capped).
    ``content_preview`` is only ~50 chars of frontmatter, so it never carries the actual fact —
    read the source file. Used only by the content-injecting render path (EXP-001 Entry 17)."""
    try:
        from pathlib import Path as _P  # noqa: PLC0415
        sf = getattr(match, "source_file", "") or ""
        if not sf or not _P(sf).is_file():
            return ""
        raw = _P(sf).read_text(encoding="utf-8")
        body = raw.split("-->")[-1] if "-->" in raw else raw
        if body.lstrip().startswith("---"):          # strip a leading YAML frontmatter block
            parts = body.split("---")
            body = parts[2] if len(parts) >= 3 else body
        return " ".join(body.split())[:cap].strip()
    except Exception:  # noqa: BLE001 — content injection is best-effort, never break the brief
        return ""


def render_brief_to_text(brief: Any, *, include_content: bool = False) -> str:
    """Format the brief into a compact system-prompt block. Empty string when
    there is nothing useful (I5: off is a quiet path). Unchanged from pre-P-A for
    parity; the LLM synthesis stage (P-C) may set a ``synthesised_prompt`` that
    the boundary prefers over this rendering.

    ``include_content`` (EXP-001 Entry 17, default False = ship-dark): when True, each relevant
    memory renders its actual BODY, not just its title. The tool-disabled measurement showed the
    title-only brief carries no answerable fact (cel=0.00) while injected content answers (oracle
    =0.944); this path closes that gap. Default False keeps every existing caller byte-identical."""
    if brief is None:
        return ""
    lines: list[str] = []
    mc = getattr(brief, "memory_context", None)
    matches = getattr(mc, "matches", []) if mc else []
    if matches:
        lines.append("Relevant past memory:")
        for m in matches[:5]:
            _title = getattr(m, 'title', None) or getattr(m, 'filename', '?')
            _body = _memory_body(m) if include_content else ""
            if _body:
                lines.append(f"  - {_title}: {_body}")
            else:
                lines.append(f"  - {_title}")
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
