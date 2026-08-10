"""Vibe Engineering — consolidated Context Engineering pipeline (P-1, ADR-0275).

``build_brief()`` is the SINGLE run-all-or-none boundary for the CEL: it runs the
memory → graph → skill stages in ONE place (today they are scattered across
``memory_lookup.enrich_task`` + ``task_analysis/engine.py`` Phase 5.5), so the
license gate (ADR-0276) and the trace (P1) have exactly ONE call site. Called
from the live turn (chat_runtime) behind the ships-dark ``vibe_engineering`` flag,
BEFORE the pre-spawn compliance gates — invariant I1: CE builds the brief, the
gates inspect the spawn; build_brief never admits a spawn the gates would deny.

Fail-safe throughout: a stage that raises is recorded as ``failed`` in the trace
and never breaks the turn (mirrors engine.py Phase 5.5's ``except`` guard).
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from .memory_lookup import MemoryLookup
from .graph_traversal import GraphTraversal
from .skill_injection import SkillInjection


def _confidence_tier(score: float) -> str:
    """Quantize a continuous confidence float into a tier. The 0.7 boundary is
    the one real threshold in the CEL (confidence_scorer: >=0.7 normal routing);
    below 0.4 = low."""
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _task_adapter(task: str) -> Any:
    """The CEL stages read ``.normalized.summary`` / ``.key_terms`` off an
    EnrichedTask-shaped object — but the real EnrichedTask has NEITHER, so the
    engine.py path finds nothing (verified GOTCHA). Feed a minimal adapter that
    actually carries the task text + keywords so memory/graph/skill search. Keeps
    P-1 self-contained (no dependency on the enrichment pipeline)."""
    words = [w.strip(".,!?;:()[]\"'").lower() for w in task.split() if len(w) > 3]
    return SimpleNamespace(
        normalized=SimpleNamespace(summary=task),
        key_terms=words[:12],
        validated=SimpleNamespace(task=task),
        task_complexity="moderate",
    )


def _avg(scores: list) -> float:
    return sum(scores) / len(scores) if scores else 0.0


# Blocker ID is DETERMINISTIC (no LLM call per turn): it surfaces the documented
# constraint / "must not" / fail-closed signals the gathered context already
# carries, so the agent sees the tripwires before it acts. The agent itself does
# the real synthesis; approach_synthesis only points at the densest signal.
_BLOCKER_SIGNALS = (
    "must not", "must-not", "fail-closed", "fail closed", "load-bearing",
    "load bearing", "blocker", "constraint", "deprecated", "do not", "don't",
    "never ", "irreversible", "locked", "blocked", "breaking change",
)


def _scan_blockers(brief: Any) -> list:
    """Sources whose title/preview carries a constraint signal. Deterministic."""
    out: list[str] = []
    mc = getattr(brief, "memory_context", None)
    for m in (getattr(mc, "matches", []) if mc else []):
        hay = f"{getattr(m, 'title', '')} {getattr(m, 'content_preview', '')}".lower()
        if any(sig in hay for sig in _BLOCKER_SIGNALS):
            out.append(getattr(m, "title", None) or getattr(m, "filename", "?"))
    for d in (getattr(brief, "related_decisions", None) or []):
        if any(sig in (getattr(d, "title", "") or "").lower() for sig in _BLOCKER_SIGNALS):
            out.append(getattr(d, "decision_id", "?"))
    # de-dup preserving order
    seen: set = set()
    return [x for x in out if not (x in seen or seen.add(x))][:5]


def build_brief(task: str, tenant: str = "_default", session: Any = None,
                meter: bool = True) -> "tuple[Any, dict]":
    """Run the full CEL (memory → graph → skill) in ONE place; return
    ``(brief, trace)``. ``brief`` is a RichTaskBrief (or None if the license gate
    degrades this turn to plain context, or if memory itself fails). ``trace``
    carries per-stage {stage, status, duration_ms, confidence_tier, sources,
    tokens_in/out}, plus ``degraded`` when the turn ran on plain context.

    License gate (ADR-0276): this is the SINGLE metering boundary — it charges
    exactly one context-engineering unit per turn. Over budget / license
    unavailable → degrade to plain context (no stages run), never a block (I2).
    ``meter=False`` bypasses the meter (tests / internal reuse).
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

    task_obj = _task_adapter(task)

    # Stage 1 — Memory (constructs the RichTaskBrief).
    try:
        brief = MemoryLookup().enrich_task(task_obj)
        mc = getattr(brief, "memory_context", None)
        matches = getattr(mc, "matches", []) if mc else []
        trace["stages"].append({
            "stage": "memory", "status": "ok",
            "duration_ms": getattr(mc, "search_duration_ms", None),
            "confidence_tier": _confidence_tier(getattr(mc, "confidence", 0.0) or 0.0),
            # id = basename only (never source_file — an abs path leaks the home dir);
            # score = the raw per-source relevance, the causal "why this source" (ADR-0278).
            "sources": [{"id": getattr(m, "filename", None) or "?",
                         "score": round(getattr(m, "relevance_score", 0.0) or 0.0, 3)}
                        for m in matches][:8],
            "tokens_in": len(task) // 4, "tokens_out": None,
        })
    except Exception as e:  # noqa: BLE001 — no brief → nothing downstream can attach
        trace["stages"].append({"stage": "memory", "status": "failed",
                                "error": str(e)[:120]})
        return None, trace

    # Stage 2 — Graph (related decisions; attached to the brief).
    try:
        gr = GraphTraversal().find_related_decisions(task_obj)
        brief.related_decisions = gr.related_decisions
        scores = [getattr(d, "relevance_score", 0.0) for d in gr.related_decisions]
        trace["stages"].append({
            "stage": "graph", "status": "ok",
            "duration_ms": getattr(gr, "search_duration_ms", None),
            "confidence_tier": _confidence_tier(_avg(scores)),
            "sources": [{"id": getattr(d, "decision_id", "?"),
                         "score": round(getattr(d, "relevance_score", 0.0) or 0.0, 3)}
                        for d in gr.related_decisions][:8],
            "tokens_in": None, "tokens_out": None,
        })
    except Exception as e:  # noqa: BLE001
        trace["stages"].append({"stage": "graph", "status": "failed",
                                "error": str(e)[:120]})

    # Stage 3 — Skill (consumes graph's decisions).
    try:
        sr = SkillInjection(tenant_id=tenant).recommend_skills(
            task_obj, getattr(brief, "related_decisions", None))
        brief.recommended_skills = sr.recommended_skills
        scores = [getattr(s, "relevance_score", 0.0) for s in sr.recommended_skills]
        trace["stages"].append({
            "stage": "skill", "status": "ok",
            "duration_ms": getattr(sr, "search_duration_ms", None),
            "confidence_tier": _confidence_tier(_avg(scores)),
            "sources": [{"id": getattr(s, "skill_id", "?"),
                         "score": round(getattr(s, "relevance_score", 0.0) or 0.0, 3)}
                        for s in sr.recommended_skills][:8],
            "tokens_in": None, "tokens_out": None,
        })
    except Exception as e:  # noqa: BLE001
        trace["stages"].append({"stage": "skill", "status": "failed",
                                "error": str(e)[:120]})

    # Stage 4 — Approach Synthesis (deterministic: point at the densest signal
    # across the three stages; the agent does the real synthesis from there).
    try:
        mc = getattr(brief, "memory_context", None)
        top_mem = next(iter(getattr(mc, "matches", []) if mc else []), None)
        top_adr = next(iter(getattr(brief, "related_decisions", None) or []), None)
        top_skill = next(iter(getattr(brief, "recommended_skills", None) or []), None)
        anchors: list[str] = []
        if top_mem:
            anchors.append(getattr(top_mem, "title", None) or getattr(top_mem, "filename", "?"))
        if top_adr:
            anchors.append(getattr(top_adr, "decision_id", "?"))
        if top_skill:
            anchors.append(getattr(top_skill, "title", None) or getattr(top_skill, "skill_id", "?"))
        brief.approach = anchors
        trace["stages"].append({
            "stage": "approach_synthesis", "status": "ok",
            "confidence_tier": "high" if len(anchors) >= 2 else "low",
            "sources": [{"id": a, "score": 1.0} for a in anchors],
        })
    except Exception as e:  # noqa: BLE001
        trace["stages"].append({"stage": "approach_synthesis", "status": "failed",
                                "error": str(e)[:120]})

    # Stage 5 — Blocker ID (deterministic constraint scan over the gathered context).
    try:
        blockers = _scan_blockers(brief)
        brief.blockers = blockers
        trace["stages"].append({
            "stage": "blocker_id", "status": "ok",  # ran; empty is a valid result
            "confidence_tier": "high" if blockers else "low",
            "sources": [{"id": b, "score": 1.0} for b in blockers],
        })
    except Exception as e:  # noqa: BLE001
        trace["stages"].append({"stage": "blocker_id", "status": "failed",
                                "error": str(e)[:120]})

    return brief, trace


def render_brief_to_text(brief: Any) -> str:
    """Format the brief into a compact system-prompt block. Empty string when
    there is nothing useful, so the prompt block collapses to nothing (I5: off is
    a quiet path)."""
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
