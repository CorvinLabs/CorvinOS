"""ADR Reranking Stage (Phase 5, ADR-0394).

Reranks ADRs by recency, relevance, and status to surface the most relevant
ones first. Keeps the top-3 ADRs by composite score.

Stage: adr_reranking
Requires: graph (to have related_decisions ADRs)
Effect: pure (read-only, transforms brief data)
"""
from __future__ import annotations

import logging
from datetime import datetime
from .base import StageTelemetry
from .registry import register_stage
from ..adr_reranking import ADRRanker

logger = logging.getLogger(__name__)


class ADRRerangkingStage:
    """Rerank ADRs by recency, relevance, and status."""

    id = "adr_reranking"
    requires = ("graph",)  # Must run after graph to have related_decisions
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        """Rerank brief.related_decisions by recency, relevance, and status.

        Non-destructive: reranked ADRs are sorted in place; lowest-scoring ones
        are dropped from the context but remain in audit trail.
        """
        tel = StageTelemetry(stage=self.id, status="ok")

        try:
            # Get config
            config = getattr(ctx, "config", {}) or {}
            keep_top_k = int(config.get("keep_top_k", 3))
            recency_weight = float(config.get("recency_weight", 0.3))
            relevance_weight = float(config.get("relevance_weight", 0.4))
            status_weight = float(config.get("status_weight", 0.3))
            enabled = config.get("enabled", True)

            if not enabled:
                tel.status = "skipped"
                tel.reason = "disabled_by_config"
                return bundle, tel

            # Get ADRs from brief
            brief = getattr(bundle, "brief", None)
            if brief is None:
                tel.status = "skipped"
                tel.reason = "no_brief"
                return bundle, tel

            related_decisions = getattr(brief, "related_decisions", None)
            if not related_decisions:
                tel.status = "ok"
                tel.reason = "no_related_decisions_to_rerank"
                return bundle, tel

            # Rerank
            ranker = ADRRanker(
                keep_top_k=keep_top_k,
                recency_weight=recency_weight,
                relevance_weight=relevance_weight,
                status_weight=status_weight,
            )
            task_text = getattr(ctx.task_obj, "query", "") or getattr(ctx.task_obj, "brief", "") or ""
            reranked_adrs, rerank_tel = ranker.rerank(related_decisions, query=task_text)

            # Update brief with reranked ADRs
            brief.related_decisions = reranked_adrs

            # Record telemetry
            tel.status = "ok"
            tel.sources = [
                {"id": "dropped_count", "score": rerank_tel.get("dropped_count", 0)},
                {"id": "keep_top_k", "score": keep_top_k},
            ]

            logger.info(
                f"ADRRerangkingStage: {rerank_tel.get('adrs_before', 0)} → {rerank_tel.get('adrs_after', 0)} "
                f"(keep_top_k={keep_top_k}, dropped={rerank_tel.get('dropped_count', 0)})"
            )

        except Exception as e:
            tel.status = "failed"
            tel.error = str(e)[:120]
            logger.exception(f"ADRRerangkingStage failed: {e}")

        return bundle, tel


register_stage(ADRRerangkingStage())
