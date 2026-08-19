---
id: ADR-0387
status: PROPOSED
depends_on:
  - ADR-0314
  - ADR-0315
relates_to:
  - ADR-0024
  - ADR-0133
paths:
  - core/learning/memory_lookup.py
  - core/console/corvin_core/feature_flags.py
  - core/learning/confidence.py
docs:
  - docs/implementation/SKILL-VERIFICATION-STRATEGY.md
  - docs/claude-ref/ldd-mandatory.md
---

# ADR-0387: Confidence-Gated Memory Retrieval

## Problem

SkillForge and the Learning Engine emit confidence scores for every decision (skill injection, tool selection, style preference) — these scores live in the EventStore and are surfaced during memory lookups. Today, memory retrieval returns **all matches ordered by recency**, regardless of confidence. This creates three waste streams:

1. **Token overhead:** A marginal skill match (confidence 0.25) gets embedded in the prompt context at the same priority as a proven match (0.85). Both incur token cost; only one has signal.
2. **Attention dilution:** The LLM reads 20 skills of mixed quality and must expend reasoning to weight them. Operator decision-making in the Settings → Skills dashboard is cluttered.
3. **Scoring divergence:** Phase 3.2+ (ADR-0315, Confidence Intervals) will route high-confidence decisions through a fast path, but memory retrieval has no equivalent gating — same-event skill gets fast-path scoring but low-confidence retrieval.

**Quantification (Phase 2 data):** ~15% of injected skills carried confidence <0.4; if filtered, this reduces average context length by ~8% and improves auto-grading precision by 3 percentage points (weak correlation, but real). Estimated token savings: ~50 tokens/decision on Tier 1 at current skill-set size.

## Options

### (a) No Threshold — Baseline (Status Quo)
Return all matches, rank by (recency, then confidence). **Pros:** simplest, no feature flag, no risk of type I errors (false negatives). **Cons:** wastes tokens, dilutes attention, leaves scoring divergence unfixed.

### (b) Hard Threshold (0.5)
Filter memory lookups to `confidence >= 0.5` before returning. **Pros:** clean cutoff, eliminates obvious noise, stateless (no per-user tuning). **Cons:** rigid — a 0.49 match may have real signal in context; affects Tier 1 users who should see everything available.

### (c) Dynamic Threshold
Threshold = `max(0.3, percentile_75_confidence - 0.25)` — adaptive per user/session. **Pros:** personalizes the gate, learns from user feedback over time. **Cons:** adds complexity (stats per user, statefulness), delays initial rollout, harder to debug.

### (d) Operator-Configurable Threshold
Expose `spec.learning.memory_confidence_gate` (default 0.0, meaning no filter) as a per-tenant setting in Console → Settings → Learning. **Pros:** maximum flexibility, operators can tune for their workflow (research teams like marginal hits; production teams prefer high-confidence). **Cons:** adds config surface, UX burden, requires operator education.

## Decision

**Implement (b) with immediate upgrade path to (d).**

**Rationale:**
- **Phase 1 quick-win:** A hard 0.5 threshold is sufficient to capture the token savings and attention benefits *today*, with <2 days of implementation. Confidence scoring (ADR-0315) stabilizes in Week 2; by then we have real production data to tune the cutoff.
- **No type I risk in SkillForge context:** Skills are user-addable, high-churn, and self-rated initially. Low-confidence skill injection is *designed* — the gate protects LLM context, not gate-keeper decisions. Missing a 0.3-confidence skill is acceptable; the user can re-run or adjust their skill library.
- **Tier 1 fallback:** Operator can immediately bump the threshold to 0.0 (no filter) via a feature flag (`memory_confidence_gate_enabled: false`) if a customer pushes back on missing marginal matches. This is maintainable week-by-week.
- **Path to operator control:** Once (d) is live, operators own the dial; default stays 0.5 (we ship with the learned position), but each tenant can override. This unblocks both the researcher and the production team without a second code path.

## Consequences

### Immediate (Week 1–2)
- **Token savings:** ~8% reduction in memory-lookup context length (50–80 tokens/decision, depending on skill-set size). Cumulative: ~2M tokens/week saved across 10-user canary.
- **Implementation:** Add `confidence_threshold` parameter to `memory_lookup.recall()` (default 0.5); wire into SkillSystemIntegration event handler.
- **Testing:** Extend `test_learning_integration.py` with before/after comparisons (same user session, measure context length and auto-grade precision).
- **Feature flag:** `memory_confidence_gate_enabled` (default `true`). If false, behaves like option (a).

### Medium term (Week 3–4)
- **Threshold tuning:** Collect production confidence distributions; if P75 confidence is 0.6, lower threshold to 0.45 (retains ~70% of matches, keeps noise down).
- **Scoring divergence closure:** When ADR-0315 (Confidence Intervals) lands, align fast-path gate with memory-lookup gate.
- **Dashboard noise reduction:** Skills panel in Console shows only `>= 0.5` confidence matches by default (with a toggle "show marginal" for exploration).

### Risks
- **Missed signal:** A 0.48-confidence match that *is* relevant gets filtered. Mitigation: keep the toggle always available; user can re-run with `--include-marginal-memory` CLI flag (not shipping Week 1, but reserved).
- **Inconsistency during rollout:** If (d) lands before canary graduates to 50%, some users see configurable threshold, others see hard 0.5. Mitigation: keep feature flag, roll out (d) to same cohort as canary scales.
- **Audit trail:** Memory lookups are not audit-logged today; filtering decision disappears. Mitigation: low priority (audit traces *events*, not query filters); if required for compliance, add optional audit point in later ADR.

## Implementation Notes

1. **Confidence source:** Use `EventStore.query(event_type='confidence')` with `score` field (ADR-0315 defines this). Fallback to 1.0 if no confidence event found (graceful degrade for pre-0315 data).
2. **Threshold constant:** `MEMORY_CONFIDENCE_THRESHOLD = 0.5` in `core/learning/confidence.py` (centralize for easy tuning).
3. **Feature flag location:** `spec.learning.memory_confidence_gate_enabled` in `tenant.corvin.yaml`; Console → Settings → Learning (UI implementation in Phase 4).
4. **Fallback logic:** If `memory_confidence_gate_enabled: false`, bypass filter (option a behavior).

---

## Frontmatter Resolution

- **Depends on:** ADR-0314 (EventStore exists), ADR-0315 (confidence scores defined)
- **Relates to:** ADR-0024 (memory interface design), ADR-0133 (skill injection flow)
- **Paths touched:** Two files (memory_lookup logic, feature flag registry)
- **Docs affected:** Learning strategy guide + LDD mandatory doc (confirms scoring consistency)

---

**Decision:** Approved for implementation. Ship Week 1 as hard threshold (option b) with feature flag fallback. Evaluate (d) upgrade after Week 2 production telemetry.
