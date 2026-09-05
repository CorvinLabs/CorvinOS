"""Memory Lookup stage (ADR-0280). The ROOT: it constructs the RichTaskBrief;
everything downstream consumes it, so `requires=()` and it is non-removable."""
from __future__ import annotations

import logging

from .base import StageTelemetry
from .registry import register_stage
from ._util import confidence_tier
from ..memory_lookup import MemoryLookup

_log = logging.getLogger("corvin.cel.memory")


def _apply_context_retriever(brief, ctx):
    """Fail-open ADR-0599 seam: let an active ``context_retriever`` narrow/reorder
    the memory matches ``MemoryLookup`` produced.

    ``MemoryLookup`` is NOT replaced or monkeypatched — the provider only ever
    SELECTS from the candidates it already produced, and never adds. With no
    provider active (the bundled ``PassthroughContextRetriever``) the retriever
    returns the SAME list object and this is a no-op, so behaviour is byte-
    identical to before this seam. Any error, a non-list return, or a return that
    grows the set is rejected and the original matches are kept. The retriever
    sits BEHIND MemoryLookup's own gating (ADR-0297): it selects from candidates
    that are already produced/gated, never widening them.
    """
    try:
        # Import shape matters: `from ... providers import context_retriever` +
        # a `context_retriever.get_active()` call is what surface_map's
        # consumed-by test resolves as a real consumer of the registry.
        from corvin_plugins.providers import context_retriever
    except ImportError:
        # Plugin subsystem absent (stripped/headless) — never break the turn.
        return brief
    try:
        mc = getattr(brief, "memory_context", None)
        candidates = getattr(mc, "matches", None) if mc is not None else None
        if not candidates:
            return brief
        query = getattr(brief, "raw_input", "") or ""
        selected = context_retriever.get_active().select(
            query,
            candidates,
            budget=None,
            tenant_id=getattr(ctx, "tenant_id", None),
        )
        if selected is candidates:
            # Passthrough default (or a provider that chose no change): no-op.
            return brief
        # Reject anything that is not a NARROWING/REORDERING of the candidates:
        # not a list, or grows the set, or introduces an item that was not a
        # candidate (identity-checked; MemoryMatch is unhashable). Fail-open.
        if not isinstance(selected, list) or len(selected) > len(candidates):
            return brief
        if not all(any(s is c for c in candidates) for s in selected):
            return brief
        mc.matches = selected
    except Exception as exc:  # noqa: BLE001 — a stage that raises must not break the turn
        _log.debug("context_retriever seam degraded (%s) — keeping raw matches",
                   type(exc).__name__)
        return brief
    return brief


class MemoryStage:
    id = "memory"
    requires: tuple = ()
    effect = "pure"
    trust = "builtin"

    def run(self, bundle, ctx):
        brief = MemoryLookup().enrich_task(ctx.task_obj)
        brief = _apply_context_retriever(brief, ctx)
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
