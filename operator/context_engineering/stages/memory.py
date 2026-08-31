"""Memory Lookup stage (ADR-0280). The ROOT: it constructs the RichTaskBrief;
everything downstream consumes it, so `requires=()` and it is non-removable."""
from __future__ import annotations

from .base import StageTelemetry
from .registry import register_stage
from ._util import confidence_tier
from ..memory_lookup import MemoryLookup


class MemoryStage:
    id = "memory"
    requires: tuple = ()
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        brief = MemoryLookup().enrich_task(ctx.task_obj)
        bundle.brief = brief
        mc = getattr(brief, "memory_context", None)
        matches = getattr(mc, "matches", []) if mc else []
        bundle.scratch["memory_matches"] = matches
        tel = StageTelemetry(
            stage="memory", status="ok",
            confidence_tier=confidence_tier(getattr(mc, "confidence", 0.0) or 0.0),
            duration_ms=getattr(mc, "search_duration_ms", None),
            sources=[{"id": getattr(m, "filename", None) or "?",
                      "score": round(getattr(m, "relevance_score", 0.0) or 0.0, 3)}
                     for m in matches][:8])
        return bundle, tel


register_stage(MemoryStage())
