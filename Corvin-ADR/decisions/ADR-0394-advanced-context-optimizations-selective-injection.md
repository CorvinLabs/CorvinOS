---
id: ADR-0394
status: accepted
supersedes: []
depends_on: [ADR-0387, ADR-0388, ADR-0391]
related: [ADR-0392, ADR-0393]
commits: []
paths:
  - operator/context_engineering/selective_injection.py
  - operator/context_engineering/memory_pruning.py
  - operator/context_engineering/adr_reranking.py
  - operator/context_engineering/stages/selective_injection_stage.py
  - operator/context_engineering/stages/memory_pruning_stage.py
  - operator/context_engineering/stages/adr_reranking_stage.py
docs:
  - docs/optimization/advanced-context-selection.md
---

# ADR-0394 — Advanced Context Optimizations: Selective Injection, Pruning, Reranking

**Date:** 2026-08-19  
**Deciders:** shumway (Claude)  
**Status:** Accepted

## Context

Phases 1-3 reduce context by 40-50%. Further optimization requires intelligent selection and ordering of context items. Three independent strategies can reduce context by an additional 15-25%:

1. **Selective Injection:** Skip irrelevant items entirely (query-aware filtering)
2. **Memory Pruning:** Remove expired/low-confidence memories (retention policies)
3. **ADR Reranking:** Put most relevant ADRs first (status + recency + relevance)

## Decision

Implement three orthogonal pipeline stages for advanced context optimization:

1. **SelectiveInjector** — Query-aware filtering by relevance
   - Cosine similarity between query + item embeddings
   - Skip items below threshold (0.7 default, configurable)
   - Expected savings: 10-15% of context

2. **MemoryPruner** — Multi-rule memory retention
   - Rule 1: Drop if confidence < 0.3 (quality floor)
   - Rule 2: Drop if age > 30 days (retention policy)
   - Rule 3: Keep top-5 per tenant (per-user quota)
   - Non-destructive: items remain in audit trail
   - Expected savings: 5-10% of context

3. **ADRRanker** — Intelligent ADR ordering
   - Score by: recency (recent first), relevance (similarity), status (ACCEPTED > PROPOSED)
   - Handle supersession chains (hide if superseding ADR present)
   - Keep top-3 ADRs by score (tunable)
   - Expected savings: 5-10% of context

All three stages are:
- **Pure:** Read-only, no side effects
- **Feature-flagged:** Independently toggleable
- **Fail-safe:** Stage failure doesn't break the turn
- **Non-destructive:** Audit trail unchanged

## Rationale

- **Selective Injection:** Context should match query; irrelevant items just add noise
- **Memory Pruning:** Stale memories are less useful than recent ones; low-confidence matches hurt
- **ADR Reranking:** Status + recency are better ordering signals than random order
- **Orthogonality:** Three stages can be enabled/disabled independently for A/B testing

## Constraints

- Relevance threshold (0.7) is hardcoded; operator can configure via tenant.corvin.yaml
- Memory pruning rules apply to ALL memories (not per-query); reconsider if temporal specificity needed
- ADR reranking assumes embeddings available (uses same embedding layer as Phase 1)
- Supersession chain handling requires ADR graph traversal (O(n) but n is small <100 ADRs)

## Compliance

✅ No PII impact (only changes rendering, not storage)  
✅ Audit trail unchanged (non-destructive)  
✅ Operator visibility: pruning stats logged per turn  

## Files

| File | LoC | Purpose |
|------|-----|---------|
| selective_injection.py | 179 | Query-aware filtering |
| memory_pruning.py | 162 | Confidence + age + quota rules |
| adr_reranking.py | 292 | Status + recency + relevance scoring |
| selective_injection_stage.py | - | Pipeline stage wrapper |
| memory_pruning_stage.py | - | Pipeline stage wrapper |
| adr_reranking_stage.py | - | Pipeline stage wrapper |
| test_advanced_optimizations_adr0394.py | 570 | 16+ comprehensive tests |
| advanced-context-selection.md | 643 | Configuration + deployment guide |

**Total: 2,347 LoC, 16+ tests, 0 breaking changes**

## Combined Impact (Phase 1-5)

| Phase | Savings |
|-------|---------|
| Phase 1-3 | 40-50% |
| Phase 4 | +5-10% (ML accuracy) |
| Phase 5 | +15-25% (selective + pruning + reranking) |
| **TOTAL** | **65-82%** |

From 4000 tokens → 720-1400 tokens (2600-3280 T/turn savings)

## Timeline

- Week 1-2: Stages deployed with feature flags OFF
- Week 3: Enable for 10% canary, measure quality
- Week 4+: Gradual rollout with per-operator toggles
