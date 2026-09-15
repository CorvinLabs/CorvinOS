"""Selective Injection Stage (Phase 5, ADR-0394).

Filters memory matches and ADRs by relevance to the task query, reducing
context size by 10-15% while preserving signal.

Stage: selective_injection
Requires: memory (to have memory_context with matches)
Effect: pure (read-only, transforms brief data)
"""
from __future__ import annotations

import logging
from .base import StageTelemetry
from .registry import register_stage
from ..selective_injection import SelectiveInjector

logger = logging.getLogger(__name__)


class SelectiveInjectionStage:
    """Filter context items by relevance."""

    id = "selective_injection"
    requires = ("memory",)  # Must run after memory to have matches
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        """Filter brief contents by relevance to task.

        Modifies bundle.brief.memory_context.matches (memory stage output) by
        keeping only items with relevance >= threshold. Non-destructive: filtered
        items are not rendered but remain in audit trail.
        """
        tel = StageTelemetry(stage=self.id, status="ok")
        start_items_count = 0
        end_items_count = 0

        try:
            # Get config
            config = getattr(ctx, "config", {}) or {}
            threshold = float(config.get("relevance_threshold", 0.7))
            enabled = config.get("enabled", True)

            if not enabled:
                tel.status = "skipped"
                tel.reason = "disabled_by_config"
                return bundle, tel

            # Get memory matches from brief
            brief = getattr(bundle, "brief", None)
            if brief is None:
                tel.status = "skipped"
                tel.reason = "no_brief"
                return bundle, tel

            mc = getattr(brief, "memory_context", None)
            if mc is None:
                tel.status = "skipped"
                tel.reason = "no_memory_context"
                return bundle, tel

            matches = getattr(mc, "matches", None)
            if not matches:
                tel.status = "ok"
                tel.reason = "no_matches_to_filter"
                return bundle, tel

            start_items_count = len(matches)

            # Filter by relevance
            injector = SelectiveInjector(threshold=threshold)
            task_text = getattr(ctx.task_obj, "query", "") or getattr(ctx.task_obj, "brief", "") or ""
            filtered_matches, injection_tel = injector.filter_by_relevance(
                matches, task_text, threshold=threshold
            )

            # Update brief with filtered matches
            mc.matches = filtered_matches
            end_items_count = len(filtered_matches)

            # Record telemetry
            tel.status = "ok"
            tel.sources = [
                {"id": "dropped_count", "score": injection_tel.get("dropped_count", 0)},
                {"id": "threshold", "score": threshold},
            ]

            logger.info(
                f"SelectiveInjectionStage: {start_items_count} → {end_items_count} matches "
                f"(threshold={threshold}, dropped={injection_tel.get('dropped_count', 0)})"
            )

        except Exception as e:
            tel.status = "failed"
            tel.error = str(e)[:120]
            logger.exception(f"SelectiveInjectionStage failed: {e}")

        return bundle, tel


register_stage(SelectiveInjectionStage())
