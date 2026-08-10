"""Skill Injection stage (ADR-0280). Consumes graph's decisions; recommends
skills. `requires=("graph",)`."""
from __future__ import annotations

from .base import StageTelemetry
from .registry import register_stage
from ._util import confidence_tier, avg


class SkillStage:
    id = "skill"
    requires: tuple = ("graph",)
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        from ..skill_injection import SkillInjection  # noqa: PLC0415
        sr = SkillInjection(tenant_id=ctx.tenant_id).recommend_skills(
            ctx.task_obj, getattr(bundle.brief, "related_decisions", None))
        bundle.brief.recommended_skills = sr.recommended_skills
        bundle.scratch["recommended_skills"] = sr.recommended_skills
        scores = [getattr(s, "relevance_score", 0.0) for s in sr.recommended_skills]
        tel = StageTelemetry(
            stage="skill", status="ok",
            confidence_tier=confidence_tier(avg(scores)),
            duration_ms=getattr(sr, "search_duration_ms", None),
            sources=[{"id": getattr(s, "skill_id", "?"),
                      "score": round(getattr(s, "relevance_score", 0.0) or 0.0, 3)}
                     for s in sr.recommended_skills][:8])
        return bundle, tel


register_stage(SkillStage())
