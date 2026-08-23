# Graph Engineering Phase 3+ Roadmap

**Status:** Planning (Phase 2 complete, Phase 3 ready to start)  
**Date:** 2026-08-23  
**Scope:** Phases 3, 4, 5 (3-4 weeks)

---

## Phase 2 → Phase 3 Transition

### What Phase 2 Delivered
- ✅ Normalizer (Phase 0) — extract task type, components, severity
- ✅ Classifier (Phase 1) — confidence scoring (0.0-1.0)
- ✅ Router (graph_routing.py) — 5 independent routers (Call, Test, ADR, Layer, CodeDiff)
- ✅ Engine orchestrator — full pipeline (normalize → classify → filter → validate → enrich → delegate)
- ✅ E2E validation (Tier 4) — 9 test cases + oracle
- ✅ Security hardening — path validation, input validation, 18 security tests

### What Phase 3 Needs

**Constraint:** Current routing is static (keyword-based scores). Dynamic refinement needed:
1. Learn from operator feedback (correct/incorrect routing → model update)
2. Refine confidence gate threshold (currently ≥0.70 is hard-coded)
3. Inject memory context (ADRs, incidents, memory files)
4. Multi-agent routing (ACS carve-out, TDE delegation)

---

## Phase 3: Routing Refinement + Memory Injection

**Effort:** ~6-8 weeks (3-4 phases)  
**K_MAX:** 5 iterations per phase

### Phase 3a: Confidence Gate Tuning (2 weeks)

**Goal:** Optimize confidence threshold from hard-coded 0.70 → learned threshold

**Scope:**
1. **Metric collection:**
   - Log every routing decision + operator feedback (correct/incorrect)
   - Build dataset of (task features → routing outcome) tuples
   - Measure: precision/recall of current 0.70 threshold

2. **Threshold optimization:**
   - Run logistics regression: P(routing_correct | confidence_score)
   - Find optimal threshold (ROC curve analysis)
   - Update ≥X.XX with learned value

3. **Feedback loop:**
   - `/rate-routing [task-id] [correct|incorrect]` command in console
   - Persist feedback to learning/feedback.jsonl
   - Re-train threshold weekly

**Tests:**
- Unit: threshold calculation (k=1)
- Integration: feedback loop (k=2)
- E2E: operator feedback → threshold update → routing improves (k=3-5)

**Deliverable:** ADR-0269 (Confidence Gate Learning)

---

### Phase 3b: Memory Context Injection (2 weeks)

**Goal:** Auto-inject related ADRs, incidents, memory files into task brief

**Scope:**
1. **Memory linking:**
   - Normalizer extracts file paths + module names
   - Search memory (MEMORY.md) + ADRs (Corvin-ADR/) for matches
   - Load matched memories + ADRs

2. **Context ranking:**
   - Rank by relevance (exact match > partial > none)
   - Trim to top 5 (token budget)
   - Inject into system prompt (Tier 2 memory)

3. **Safety:**
   - Don't inject memory with secrets (implements P2 from k=5)
   - Don't inject memory older than 6 months (freshness)
   - Verify all memory files are readable

**Tests:**
- Unit: memory search (k=1)
- Integration: memory loading + ranking (k=2)
- E2E: memory-enriched task routing (k=3-5)

**Deliverable:** ADR-0270 (Memory Context Injection)

---

### Phase 3c: Multi-Agent Routing (2 weeks)

**Goal:** Route qualifying tasks to ACS/TDE instead of native

**Scope:**
1. **Carve-out rules (ADR-0217):**
   - Big-data vocabulary → ACS
   - High complexity (>0.8) → Opus or TDE
   - Normal → native (default)

2. **Cost estimation:**
   - Estimate tokens for native vs ACS (factor 2-3x cost increase)
   - Estimate tokens for TDE (5-10x cost)
   - Include in routing decision

3. **Delegation protocol:**
   - Call `DelegationRouter.route()` with confidence + complexity
   - Get back `EngineResult(target=native|acs|tde)`
   - Log decision to audit trail

**Tests:**
- Unit: carve-out logic (k=1)
- Integration: cost estimation (k=2)
- E2E: delegation routing (k=3-5)

**Deliverable:** ADR-0271 (Multi-Agent Routing Cost Model)

---

## Phase 4: Advanced Features (If Time Allows)

### Phase 4a: Call-Graph Semantic Validation

**Goal:** Verify oracle expectations in real call-graphs (not just structure)

**Scope:**
- AST-parse actual source files
- Extract actual function calls, class hierarchies
- Compare vs oracle (nodes/edges/relationships)
- Measure accuracy (% correct call relationships)

**Effort:** 1 week

---

### Phase 4b: Operator Learning

**Goal:** Learn operator's routing preferences (personalization)

**Scope:**
- Track which skills operator chooses (vs system recommendation)
- Build operator model (e.g., prefers e2e-driven-iteration for bugs)
- Personalize routing matrix for this operator

**Effort:** 1.5 weeks

---

## Phase 5: Production Deployment

### Deployment Checklist

- [ ] Phase 3a complete + green (confidence tuning)
- [ ] Phase 3b complete + green (memory injection)
- [ ] Phase 3c complete + green (multi-agent routing)
- [ ] Coverage >85% (Phase 3 E2E tests)
- [ ] Security audit passed (P1+P2+P3)
- [ ] Operator training docs complete
- [ ] Feature flag `spec.features.graph_engineering_phase3` → default true
- [ ] Rollback plan documented (disable flag → use Phase 2 only)
- [ ] Canary: 10% users for 1 week
- [ ] Ramp: 50% users for 1 week
- [ ] Full rollout: 100% users

**Timeline:** 4-5 weeks from Phase 3 start

---

## Risk & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Confidence threshold mis-tuned | Medium | Medium | Weekly re-training + dashboard |
| Memory injection causes crashes | Low | High | Add safe guards + timeout |
| ACS/TDE delegation over-aggressive | Medium | Medium | Conservative defaults, manual override |
| Operator model bias | Low | Medium | Regularly audit for bias |

---

## Success Metrics (Phase 3+)

| Metric | Target | How to Measure |
|---|---|---|
| Routing accuracy | 95%+ | Operator feedback loop |
| Confidence threshold optimized | ROC AUC >0.9 | Logistics regression |
| Memory injection success rate | 90%+ | "memory_injected" log count |
| ACS savings | 10-20% cost reduction | Delegation cost estimates |
| Operator satisfaction | NPS >7 | Quarterly survey |

---

## Open Questions

1. **Should memory injection be opt-in?** (Currently: auto)
2. **How often re-train confidence threshold?** (Weekly? Monthly?)
3. **Should operator model be per-user or global?** (Deferred to Phase 4b)
4. **When to deprecate Phase 2 static routing?** (Phase 5 release?)

---

**Next:** Phase 3 starts after Phase 2 stable (1 week bake time).
