"""Semantic Context Retriever — BM25 baseline (ADR-0598, on the ADR-0599 seam).

This is the first *plugin* that consumes the ADR-0599 ``context_retriever``
provider seam. It is a swappable strategy for CHOOSING which context is put in
front of the model at the surfaces that do it badly today — the CEL memory stage
(lexical substring) and, later, TDE step assembly (blind truncation).

**It only ever NARROWS or REORDERS. It never ADDS.** ``select`` is handed a list
of already-produced (and, at the CEL surface, already PII/consent-gated —
ADR-0297) ``candidates`` and returns a subset of them in a better order. Every
returned item is one of the input objects (identity-preserved), so the fail-open
seam in ``stages/memory.py`` accepts it; anything else it rejects and keeps the
raw matches.

Ranking is **BM25 Okapi**, lexical and torch-free. This is the baseline the ADR
says an embedding stack must *beat* before it is justified — for a ~180-memory
corpus a compact pure-Python BM25 is more than fast enough and pulls in no heavy
dependency. If ``rank_bm25`` happens to be installed it is used; otherwise the
in-module implementation runs (identical scoring, no numpy required).

``select`` MUST NOT raise (ADR-0599). Any internal error degrades to returning
``candidates`` unchanged, which the seam treats as "no selection".
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, List, Optional

from corvin_plugins.protocol import HealthStatus, PluginContext

_log = logging.getLogger("corvin.plugins.semantic_context_retriever")

# BM25 Okapi hyperparameters — the standard defaults.
_K1 = 1.5
_B = 0.75

# Tokeniser: lowercase, split on any run of non-alphanumeric characters.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _candidate_text(candidate: Any) -> str:
    """Best-effort text view of a candidate for ranking.

    Tolerant of shape: MemoryMatch exposes ``title`` + ``content_preview``; other
    candidate kinds may carry ``body`` / ``content`` / ``text``. Falls back to
    ``str(candidate)`` so an unknown shape still ranks rather than crashing.
    """
    parts: List[str] = []
    for attr in ("title", "content_preview", "body", "content", "text", "filename"):
        val = getattr(candidate, attr, None)
        if isinstance(val, str) and val:
            parts.append(val)
    if parts:
        return " ".join(parts)
    if isinstance(candidate, str):
        return candidate
    return str(candidate)


def _bm25_scores(query_tokens: List[str], docs_tokens: List[List[str]]) -> List[float]:
    """Return a BM25-Okapi score per doc for ``query_tokens``.

    Pure-Python; no numpy. Used when ``rank_bm25`` is not importable (the common
    case in this repo's venv). Scoring matches ``rank_bm25.BM25Okapi`` with the
    same k1/b, so swapping backends does not change the ranking.
    """
    n_docs = len(docs_tokens)
    if n_docs == 0:
        return []
    doc_lens = [len(d) for d in docs_tokens]
    avgdl = (sum(doc_lens) / n_docs) if n_docs else 0.0

    # Document frequency of each term.
    df: dict[str, int] = {}
    for tokens in docs_tokens:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1

    # idf, same form rank_bm25 uses (with the +1 that keeps it non-negative).
    idf: dict[str, float] = {}
    for term in set(query_tokens):
        n_q = df.get(term, 0)
        idf[term] = math.log(1 + (n_docs - n_q + 0.5) / (n_q + 0.5))

    scores: List[float] = []
    for tokens, dl in zip(docs_tokens, doc_lens):
        # Term frequencies in this doc.
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        denom_len = _K1 * (1 - _B + _B * (dl / avgdl if avgdl else 0.0))
        score = 0.0
        for term in query_tokens:
            f = tf.get(term, 0)
            if f == 0:
                continue
            score += idf.get(term, 0.0) * (f * (_K1 + 1)) / (f + denom_len)
        scores.append(score)
    return scores


class SemanticContextRetriever:
    """A ``context_retriever`` provider that BM25-ranks candidates (ADR-0598).

    Implements both the CorvinPlugin lifecycle (on_load/on_unload/health_check)
    and the ADR-0599 ``ContextRetriever.select`` capability. ``top_n`` is an
    optional hard cap on how many candidates survive when the caller passes no
    ``budget``; ``None`` means reorder-only (no narrowing by default).
    """

    plugin_id = "semantic-context-retriever"
    plugin_type = "context_retriever"
    version = "0.1.0"
    display_name = "Semantic Context Retriever"

    def __init__(self, *, top_n: Optional[int] = None) -> None:
        self._top_n = top_n
        # Prefer rank_bm25 if present; otherwise the in-module scorer. Detected
        # once at construction so `select` stays allocation-light.
        try:
            import rank_bm25  # noqa: F401

            self._has_rank_bm25 = True
        except Exception:  # noqa: BLE001 — any import failure → use the fallback
            self._has_rank_bm25 = False

    # ── ADR-0599 capability ─────────────────────────────────────────────────
    def select(
        self,
        query: str,
        candidates: list,
        *,
        budget: int | None = None,
        tenant_id: str | None = None,
    ) -> list:
        """Return a BM25-reordered/narrowed subset of ``candidates``.

        Never additive: every returned element is one of ``candidates``. Never
        raises: on any error it returns ``candidates`` unchanged, which the
        fail-open seam reads as "no selection". ``budget`` (if a positive int)
        caps the returned count; otherwise ``top_n`` applies, else all are kept
        in ranked order. PII/consent gating happened upstream (ADR-0297) — this
        only chooses among candidates that already passed it.
        """
        try:
            if not candidates:
                return candidates
            query_tokens = _tokenize(query or "")
            if not query_tokens:
                # No usable query terms — nothing to rank on. Passthrough.
                return candidates

            docs_tokens = [_tokenize(_candidate_text(c)) for c in candidates]
            if self._has_rank_bm25:
                scores = self._scores_rank_bm25(query_tokens, docs_tokens)
            else:
                scores = _bm25_scores(query_tokens, docs_tokens)

            # Stable sort by descending score; ties keep original order (the
            # index in the key breaks ties deterministically).
            order = sorted(
                range(len(candidates)),
                key=lambda i: (-scores[i], i),
            )
            ranked = [candidates[i] for i in order]

            limit = self._resolve_limit(budget)
            if limit is not None and limit < len(ranked):
                ranked = ranked[:limit]
            return ranked
        except Exception as exc:  # noqa: BLE001 — must not raise (ADR-0599)
            _log.debug(
                "select degraded (%s) — returning candidates unchanged",
                type(exc).__name__,
            )
            return candidates

    def _scores_rank_bm25(
        self, query_tokens: List[str], docs_tokens: List[List[str]]
    ) -> List[float]:
        from rank_bm25 import BM25Okapi

        # rank_bm25 needs at least one non-empty doc; guard defensively.
        safe_docs = [d if d else [""] for d in docs_tokens]
        bm25 = BM25Okapi(safe_docs, k1=_K1, b=_B)
        return list(bm25.get_scores(query_tokens))

    def _resolve_limit(self, budget: int | None) -> Optional[int]:
        if isinstance(budget, int) and budget > 0:
            return budget
        if isinstance(self._top_n, int) and self._top_n > 0:
            return self._top_n
        return None

    # ── CorvinPlugin lifecycle ──────────────────────────────────────────────
    def on_load(self, ctx: PluginContext) -> None:
        """Install self as the active context retriever (ADR-0599 seam)."""
        reg = getattr(ctx, "context_retriever_registry", None)
        if reg is not None:
            reg.set_active(self)
        else:  # pragma: no cover — subsystem present but handle unpassed
            _log.warning(
                "context_retriever_registry not passed; %s loaded but not active",
                self.plugin_id,
            )

    def on_unload(self) -> None:
        """Release the retriever slot this plugin took, restoring passthrough."""
        try:
            from corvin_plugins.providers import context_retriever

            # Identity-based release: only clears the slot if THIS instance is
            # installed, and by owning plugin_id if the slot holds a helper.
            if not context_retriever.clear_if_active(self):
                context_retriever.release_owned_by(self.plugin_id)
        except Exception as exc:  # noqa: BLE001 — unload must not raise
            _log.debug("on_unload degraded (%s)", type(exc).__name__)

    def health_check(self) -> HealthStatus:
        return HealthStatus(
            ok=True,
            message="BM25 retriever ready",
            details={"backend": "rank_bm25" if self._has_rank_bm25 else "builtin-bm25"},
        )


__all__ = ["SemanticContextRetriever"]
