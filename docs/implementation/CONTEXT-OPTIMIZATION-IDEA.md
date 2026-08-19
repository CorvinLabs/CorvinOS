# IDEA: Context Engineering Optimization Initiative

**Initiative ID:** CONTEXT-OPT-001  
**Status:** PROPOSED (Design Review)  
**Target Release:** v0.3  
**Estimated Token Savings:** 25–30%  
**Effort:** 4–6 weeks (small team)  
**Dependencies:** Context Engineering v2 (ADR-0358+), Learning Infrastructure (ADR-0314)

---

## Problem Statement

CorvinOS Context Engineering v2 ships a unified execution model with full decision audit trails, but carries **structural token overhead** that degrades with task complexity:

| Cost Driver | Impact | Example |
|---|---|---|
| **Full decision history in context** | Every decision appended; grows with task length | 50-decision task: 15K context tokens |
| **Memory template recall** | Entire historical patterns merged upfront | 200 prior tasks: 25K tokens of "noise" |
| **Unbounded preview windows** | Reasoning shown for all decision options | 5 strategy choices: 8K tokens of alternatives |
| **No confidence filtering** | Low-confidence guidance persists in audit | 100 guidance events, 20% relevant: 5K waste |

**Total waste per task:** 15–30% of execution budget, rising linearly with task depth.

**Business impact:** 
- Token-constrained teams (Tier 2/3 users) can't use advanced strategies
- Cost controller blocks auto-promotion due to overhead
- Guidance system underutilized due to context bloat

---

## Vision

**CorvinOS Context Engineering v0.3** — lean, confidence-gated context that preserves all compliance guarantees while cutting execution overhead by 25–30%.

After optimization:
- Operators ship **same safety, same auditability, same learning flywheel**
- But context window reserved for execution grows from 60% → 80% of budget
- Tier 2/3 users adopt mid-task guidance; auto-promotion unlocks for all
- Guidance system confidence gate prevents low-signal noise
- Learning loop stays audit-complete; decision recovery fast (<1ms)

**End state:** Every complex task is now *faster, cheaper, and more guided* — without sacrificing audit completeness or GDPR guarantees.

---

## Core Hypothesis: Three Optimization Layers

### 1. **Confidence-Gated Memory** (10% savings)

**Problem:** Memory templates load ALL historical decision outcomes. A project with 200 tasks loads 25K tokens of patterns, including rare failures and low-confidence guidance.

**Solution:** Load only **high-confidence templates** at startup; lazy-load full history only if guidance system requests it.

- **Decision threshold:** `confidence ≥ 0.70` for template inclusion
- **Mechanism:** MemoryCoordinator pre-filters; maintains separate full-history index for recovery
- **Audit impact:** NONE — all decisions still hash-chained; filtering is ephemeral
- **Recovery:** If task fails with high error rate, guidance system can query full history (one-time cost, not pre-loaded)

**Expected gain:**
- Typical project (50 high-conf + 150 low-conf patterns) → 12K → 5K context (60% reduction)
- Cumulative: 8–12% execution budget freed per task

### 2. **Token Budget Per Stage** (10% savings)

**Problem:** Strategy generation, reasoning, and preview stages consume unbounded tokens. A "decompose" strategy shows 5 alternative breakdowns with full reasoning.

**Solution:** Allocate **per-stage token quota** within decision loop.

- **Reasoning stage:** 500 tokens max (abort decompose if exceeded)
- **Preview stage:** 800 tokens for all alternatives (truncate lowest-confidence)
- **Recovery stage:** 1000 tokens (full reasoning; this is the fallback)
- **Fallback:** If quota hit, switch to direct (non-decomposed) strategy

**Mechanism:**
- LoopEngineer measures tokens during each stage
- CostController enforces quota; triggers fallback if exceeded
- **Audit impact:** NONE — stage selection is recorded; truncation noted in decision record
- **Safety:** Fallback always available (never deadlock)

**Expected gain:**
- High-decomposition tasks (multiple rounds): 10–15% reduction
- Typical tasks: 5–8% reduction
- Cumulative: 8–12% execution budget freed per task

### 3. **Bounded Preview Window** (5–8% savings)

**Problem:** When guidance system evaluates next-step options, it previews all candidate strategies. A guidance choice generates 3K tokens of preview just to filter from 3 options to 1.

**Solution:** **Bounded preview:** show only top-2 candidates (by confidence), skip low-confidence options.

- **Candidate ranking:** Use learned success rate from memory; sort descending
- **Cutoff:** Show only top 2 (or top 1 if confidence of #2 < 0.50)
- **Fallback:** If bounded preview lacks coverage, widen to top 4 (one-time cost)
- **Audit impact:** NONE — all candidates ranked and recorded; preview truncation is ephemeral

**Mechanism:**
- GuidanceClassifier pre-ranks candidates using confidence intervals
- Bounded preview generator selects top-2; passes to reasoning
- If result fails, expand to top-4 automatically

**Expected gain:**
- Typical guidance: 3K → 1.2K preview tokens (60% reduction)
- Cumulative: 5–8% execution budget freed per task

---

## Success Criteria

### Quantitative (Shipping Gates)

| Metric | Target | Verification |
|---|---|---|
| **Context window reduction** | 25–30% | Benchmark suite; 50 real tasks |
| **Audit chain integrity** | 100% | Hash verification on all tasks |
| **Compliance: decision recovery** | <1ms | Latency SLO in ADR-0370 |
| **Learning accuracy** | ≥92% (no degradation) | Confidence interval width unchanged |
| **Guidance application rate** | ≥80% vs. v2 | No increase in guidance queue depth |
| **Auto-promotion false-positive rate** | <1% (no change) | Grade quality dashboard |

### Qualitative

- [ ] Design review sign-off (architecture, compliance)
- [ ] Operator feedback: "guidance system now responsive" (gatekeepers: Tier 2/3 users)
- [ ] Cost team confirms 25% reduction applies to customer invoices
- [ ] GDPR audit: no new data flows, compliance guarantees intact

---

## Scope

### In Scope ✅

- **MemoryCoordinator:** Confidence filtering on template load
- **LoopEngineer:** Per-stage token budget enforcement
- **GuidanceClassifier:** Bounded preview window (top-2 candidate selection)
- **CostController:** Quota enforcement and fallback logic
- **Audit trail:** Record truncation decisions (no data loss)
- **Configuration:** New knobs in `tenant.corvin.yaml` (opt-in at first)

### Out of Scope ❌

- **Hook system changes** (ADR-0142 extensions unaffected)
- **Plugin architecture** (boot layers, registries unchanged)
- **Voice integration** (separate stream; no context optimization)
- **Compliance baseline** (no new compliance logic; all existing gates preserved)
- **Skill/Tool Forge** (v0.2 algorithms intact; only feeding context shrinks)
- **Storage layer** (no change to audit.jsonl format or hash chain)

---

## Implementation Roadmap

### Phase 1: Confidence-Gated Memory (Week 1–2)
- Add `confidence_threshold` field to MemoryCoordinator config
- Implement pre-filter on template load
- Unit tests: 90% test coverage on new filter logic
- E2E test: Load memory with 200 tasks; verify <6K context

### Phase 2: Token Budget Per Stage (Week 2–3)
- Define per-stage budgets in LoopEngineer
- Add quota tracking to CostController
- Implement fallback trigger (strategy switch)
- Unit tests: Budget exhaustion, fallback correctness
- Benchmark: 20 high-complexity tasks; confirm 10% reduction

### Phase 3: Bounded Preview Window (Week 3–4)
- Ranking algorithm in GuidanceClassifier
- Preview window truncation logic
- Fallback expansion (top-2 → top-4)
- Unit tests: Ranking, truncation, fallback
- Benchmark: Guidance cycles; confirm 5% reduction

### Phase 4: Integration & SLOs (Week 4–5)
- Weld all three layers; end-to-end tests
- Audit trail verification (no data loss)
- Performance SLO validation
- Operator docs + configuration guide

### Phase 5: Canary & Measurement (Week 6)
- Deploy to 10% of users (opt-in via `context_optimization.enabled: true`)
- Collect metrics: real-task context reduction, guidance confidence
- Cost impact analysis
- Go/no-go decision for v0.3 release

---

## Risks & Mitigations

| Risk | Probability | Mitigation |
|---|---|---|
| **Audit chain breaks** | Low | Phase 4 includes hash verification suite; pre-flight gates |
| **Guidance quality drops** | Low | Confidence thresholds empirically tuned on 50-task benchmark |
| **Learning flywheel slows** | Low | Memory loading is ephemeral; full history accessible for recovery |
| **Edge case: all candidates low-confidence** | Medium | Fallback expands window; never deadlock (direct strategy always available) |
| **Operator confusion: new config knobs** | Medium | Docs + example playbook; opt-in by default (v0.2 behavior if disabled) |

---

## Success Narrative

After v0.3 ships:

> "A Tier 2 operator runs a complex code-review task. Guidance system suggests mid-task strategy switch. Context window: 12K tokens (was 18K in v2). Execution completes in 45s (was 62s). Confidence gates filter noise. Decision audit intact. Cost: 120 units (was 160). Task memory updated; next task starts faster."

---

## Approval Checklist

- [ ] **Architecture:** Confidence filtering + per-stage budget + bounded preview valid
- [ ] **Compliance:** GDPR audit, audit chain, decision recovery ≥92% confidence
- [ ] **Cost:** 25–30% reduction estimate validated
- [ ] **Operations:** Config knobs reasonable; opt-in safe default
- [ ] **Timeline:** 6 weeks feasible with current team
- [ ] **Testing:** SLO targets and E2E gate strategy documented

---

**Next:** Design Review (Week 1) → Architecture Sign-Off → Implementation Kickoff
