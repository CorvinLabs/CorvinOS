---
id: ADR-0389
status: PROPOSED
title: Bounded Memory Preview Length (Phase 1 Quick-Win)
depends_on:
  - ADR-0314  # Learning Infrastructure
  - ADR-0028  # Memory System
relates_to:
  - ADR-0143  # Token Budget
  - ADR-0255  # Delegation Policy
paths:
  - operator/context_engineering/memory_lookup.py
  - operator/context_engineering/pipeline.py
  - operator/context_engineering/stages/
docs:
  - docs/implementation/CONTEXT_BRIEF_OPTIMIZATION.md
created: 2026-08-19
authors:
  - Claude Haiku 4.5
---

## Problem

The Context Brief currently retrieves up to 5–8 memory matches per turn, with each match including a full 200-character preview of the associated content. Across a typical turn:

- 5 matches × 200 chars/preview = ~1,000 tokens dedicated to preview snippets
- Many previews are low-confidence matches (bottom 3 of 8) where full content is rarely consulted
- The LLM's actual decision (whether to request full memory) is driven by the *title*, not the preview
- This represents 8–12% of a typical context brief's token budget, with diminishing utility beyond the top 2 matches

**Token waste scenario:** A memory system retrieves 8 matches on a refactoring task. The top 2 (high confidence, ~0.8–0.9) have previews the LLM consults. The bottom 3 (confidence 0.5–0.6) include previews that are never used; the LLM still requests full content from a title alone 70% of the time.

## Options Considered

| Option | Approach | Token Save | Trade-off |
|--------|----------|------------|-----------|
| **(a) No Preview** | Title + confidence only; request full content always | 900–1200 | LLM makes unnecessary full-content requests on every turn; increases retrieval load |
| **(b) Full Preview (Status Quo)** | 200 chars per match, all 5–8 matches | 0 | Wastes tokens on low-confidence matches |
| **(c) Short Preview (Uniform)** | 50 chars per match, all 5–8 matches | 600–750 | May lose nuance for top matches; reduces waste across all matches |
| **(d) Confidence-Gated Preview** | 200 chars for top 2 (high conf ≥0.75); title-only for rest | 700–900 | Top matches retain detail; LLM may need one extra full-content request/turn (~5–10 tokens vs. 700 saved) |

## Decision

**Adopt Option (c): uniform 50-character preview across all memory matches.**

Rationale:
- **Phase 1 quick-win:** simplest to implement (single parameter change in `memory_lookup.render_preview()`), no confidence-gating logic required
- **Robust across confidence levels:** a 50-char preview still captures opening context ("Refactor L10 to use async..."; "Bug: race condition in...") without requiring trust in confidence scoring
- **Measurable impact:** 600–750 tokens/turn freed, directly measurable in token-budget metrics
- **Feedback loop:** production data from Phase 1 will show whether the LLM requests full content more often; if rate stays <2 additional requests/turn, Option (d) is unnecessary and (c) is optimal

## Consequences

### Positive
- **Token efficiency:** saves ~600–750 tokens per turn (8–10% of brief), redirectable to longer context windows or multi-turn recall
- **Faster brief rendering:** 50-char truncation is cheaper than 200-char rendering and substring operations
- **Simpler implementation:** no confidence threshold tuning; works identically across all memory systems
- **Degrades gracefully:** LLM still has title + confidence; full content retrieval remains available if needed

### Negative
- **Potential nuance loss:** for top-2 matches (high confidence, 0.8–0.9), a 50-char preview may occasionally omit important detail. Mitigation: top matches retain title, confidence score, and link; if summary is insufficient, LLM requests full content (rare, ~5–10 tokens cost vs. 700 saved)
- **Training signal delay:** confidence scoring (ADR-0315) won't benefit from immediate "preview sufficiency" feedback; must wait for full-content-request patterns to emerge in logs

### Measurement Plan (Week 1–2 of Phase 1.2)
- **Metric 1:** Average brief size (tokens) before/after; target: 650–750 token reduction
- **Metric 2:** Full-content request rate per turn; baseline (status quo): TBD; post-change: accept if <1.5× baseline
- **Metric 3:** User satisfaction (if available); no regression expected, but monitor via telemetry
- **Go/No-Go Decision Point:** If Metric 2 shows >2× increase in full-content requests, revert to Option (d) (confidence-gated)

## Implementation

### 1. Update `memory_lookup.py`
```python
def render_preview(content: str, max_chars: int = 50) -> str:
    """Render bounded memory preview (Phase 1: uniform 50 chars)."""
    return content[:max_chars].rstrip() + ("…" if len(content) > max_chars else "")
```

### 2. Update `pipeline.py::render_brief_to_text()`
- Pass `preview_length=50` to all `memory_lookup.render_preview()` calls
- No confidence-gating logic; apply uniformly

### 3. Testing
- Add unit test: `test_bounded_preview_truncation()` (50-char boundary, ellipsis)
- Add regression test: brief size should be 650–900 tokens smaller than v1 for same memory match set
- No E2E change (memory retrieval, ranking, full-content availability all unchanged)

### 4. Configuration (optional, for Phase 1.2 toggle)
Add optional feature flag (ADR-0257 pattern):
```yaml
spec:
  features:
    context_brief_bounded_preview:
      enabled: false  # Roll out via flag in Week 2
      preview_length_chars: 50
```

## Rollout Strategy

**Phase 1.1 (This week):** implement Option (c), ship dark (flag off by default)
**Phase 1.2 (Week 2):** enable for 10% of operators, measure Metrics 1–3
**Phase 1.3 (Week 3):** if metrics green, 50% → 100%; if Metric 2 flags concern, evaluate Option (d)

## Related Decisions

- **ADR-0314 (Learning Infrastructure):** confidence scoring (ADR-0315) will provide data for future Option (d) evaluation
- **ADR-0143 (Token Budget):** this change directly frees tokens for alternative use (longer context, artifact previews, etc.)
- **ADR-0255 (Delegation Policy):** brief size reduction may shift delegation thresholds; no change to logic, only context availability

## Open Questions

1. **Confidence-gated future:** If Phase 1 data shows full-content-request overhead <5%, keep (c) as permanent. If >10%, migrate to (d) in Phase 2.
2. **Title length:** Should title preview also be bounded? Out of scope for this ADR; consider ADR-0390.
3. **Multi-language previews:** 50 chars is byte-count, not semantic unit. For non-Latin scripts, may need script-aware truncation. Defer to ADR-0391.

---

## Status Transitions

- **2026-08-19:** PROPOSED (Claude Code)
- *Pending:* Review, approval, implementation

