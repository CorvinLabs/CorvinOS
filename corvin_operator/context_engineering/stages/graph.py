"""Graph Traversal stage (ADR-0280). Consumes the brief memory built; attaches
related decisions. `requires=("memory",)`."""
from __future__ import annotations

from .base import StageTelemetry
from .registry import register_stage
from ._util import confidence_tier, avg


class GraphStage:
    id = "graph"
    requires: tuple = ("memory",)
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        from ..graph_traversal import GraphTraversal  # noqa: PLC0415
        gr = GraphTraversal().find_related_decisions(ctx.task_obj)
        bundle.brief.related_decisions = gr.related_decisions
        bundle.scratch["related_decisions"] = gr.related_decisions
        scores = [getattr(d, "relevance_score", 0.0) for d in gr.related_decisions]
        tel = StageTelemetry(
            stage="graph", status="ok",
            confidence_tier=confidence_tier(avg(scores)),
            duration_ms=getattr(gr, "search_duration_ms", None),
            sources=[{"id": getattr(d, "decision_id", "?"),
                      "score": round(getattr(d, "relevance_score", 0.0) or 0.0, 3)}
                     for d in gr.related_decisions][:8])
        return bundle, tel


register_stage(GraphStage())
