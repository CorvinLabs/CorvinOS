"""Approach Synthesis stage (ADR-0280) — DETERMINISTIC: points at the densest
signal across memory/graph/skill. `requires=("memory","graph","skill")`. The LLM
synthesis stage (P-C) is a separate, gated stage; this one never calls out."""
from __future__ import annotations

from .base import StageTelemetry
from .registry import register_stage


class ApproachStage:
    id = "approach_synthesis"
    requires: tuple = ("memory", "graph", "skill")
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        brief = bundle.brief
        mc = getattr(brief, "memory_context", None)
        top_mem = next(iter(getattr(mc, "matches", []) if mc else []), None)
        top_adr = next(iter(getattr(brief, "related_decisions", None) or []), None)
        top_skill = next(iter(getattr(brief, "recommended_skills", None) or []), None)
        anchors: list = []
        if top_mem:
            anchors.append(getattr(top_mem, "title", None) or getattr(top_mem, "filename", "?"))
        if top_adr:
            anchors.append(getattr(top_adr, "decision_id", "?"))
        if top_skill:
            anchors.append(getattr(top_skill, "title", None) or getattr(top_skill, "skill_id", "?"))
        brief.approach = anchors
        tel = StageTelemetry(
            stage="approach_synthesis", status="ok",
            confidence_tier="high" if len(anchors) >= 2 else "low",
            sources=[{"id": a, "score": 1.0} for a in anchors])
        return bundle, tel


register_stage(ApproachStage())
