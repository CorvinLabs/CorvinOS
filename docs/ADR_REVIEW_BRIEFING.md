# ADR Review Briefing: Learning Integration (ADR-0321–0327)

**Date:** 2026-08-19  
**Prepared for:** Architecture Team  
**Objective:** Enable informed discussion and approval of 7 ADRs for learning system integration

---

## Executive Summary

**What:** 7 ADRs that wire learning infrastructure (ADR-0314) into live CorvinOS systems
**Scope:** Tool execution capture → ranking → skill grading → cost learning → operator feedback  
**Effort:** ~50 person-days, 6 engineers, 10-week critical path  
**Risk Level:** LOW (all ADRs are incremental; ADR-0314 foundation already approved)  
**MVP Readiness:** Gap 1 (telemetry) + Gap 2 (ranking) + Gap 4 (aggregation) = ~15 days for 80% value

---

## Architecture Overview

### Three-Layer Learning System

```
┌─────────────────────────────────────────────────────────┐
│ LAYER 3: Feedback & Optimization (Gaps 5–7)            │
│  • Context Coherence (cross-session learning)           │
│  • Cost Learning (budget refinement)                     │
│  • Operator Feedback (human signal)                      │
└─────────────────────────────────────────────────────────┘
                           ↑
                  (depends on Layer 2)
                           ↑
┌─────────────────────────────────────────────────────────┐
│ LAYER 2: Ranking & Attribution (Gaps 2–4)              │
│  • Tool Performance Ranking (data-driven selection)      │
│  • Skill Attribution (fair grading)                      │
│  • Performance Aggregation (metrics pipeline)            │
└─────────────────────────────────────────────────────────┘
                           ↑
                  (depends on Layer 1)
                           ↑
┌─────────────────────────────────────────────────────────┐
│ LAYER 1: Telemetry (Gap 1)                              │
│  • Tool Execution Events (every execution captured)      │
└─────────────────────────────────────────────────────────┘
                           ↑
                  (depends on ADR-0314)
                           ↑
┌─────────────────────────────────────────────────────────┐
│ ADR-0314: Learning Infrastructure                       │
│  • EventEmitter, EventStore, event schema               │
│  • Already APPROVED (✅)                                 │
└─────────────────────────────────────────────────────────┘
```

### Dependency Graph

```
ADR-0321 (Gap 1: Telemetry) ──┬─→ ADR-0322 (Gap 2: Ranking)
                              ├─→ ADR-0323 (Gap 3: Attribution)
                              ├─→ ADR-0324 (Gap 4: Aggregation)
                              ├─→ ADR-0325 (Gap 5: Coherence)
                              ├─→ ADR-0326 (Gap 6: Cost)
                              └─→ ADR-0327 (Gap 7: Feedback)

ADR-0324 (Gap 4) ─────┬─→ ADR-0322 (Gap 2) [ranking queries metrics]
                      └─→ ADR-0323 (Gap 3) [attribution uses success rates]

ADR-0322 (Gap 2) ─────────→ ADR-0325 (Gap 5) [coherence blends rankings]

ADR-0322 (Gap 2) ─────────→ ADR-0326 (Gap 6) [cost learning uses ranking data]

ADR-0323 (Gap 3) ─────────→ ADR-0327 (Gap 7) [feedback adjusts grades]
```

---

## Decision Matrix: Conceptual Decisions by ADR

| ADR | Conceptual Decision | Structural Impact | Risk | Review Priority | Status |
|-----|-------------------|------------------|------|-----------------|--------|
| 0321 | Capture tool execution as learning signals | EventEmitter integration + ToolForgeSubsystem wiring | LOW | **BLOCKER** (others depend) | PROPOSED |
| 0322 | Rank tools by success rate (weighted formula) | ToolRankingManager subsystem + ranking cache | MEDIUM | **CORE** (enables convergence) | PROPOSED |
| 0323 | Attribute strategy outcomes fairly (EQUAL model) | SkillAttributionEngine + event subscription | LOW | **DEFERRABLE** (MVP=EQUAL) | PROPOSED |
| 0324 | Aggregate metrics hourly (batch pipeline) | PerformanceAggregator + AggregationScheduler | MEDIUM | **CORE** (unblocks 2, 3) | PROPOSED |
| 0325 | Carry context across sessions (parent blending) | ContextCoherenceManager + ranking boost | LOW | NICE-TO-HAVE (post-MVP) | PROPOSED |
| 0326 | Learn cost multipliers via EMA | CostLearner subsystem + CostController integration | LOW | OPTIMIZATION (post-MVP) | PROPOSED |
| 0327 | Integrate operator ratings (1-5 stars) | OperatorFeedbackHandler + FeedbackAdjuster | LOW | **DEFERRABLE** (UI deferred) | PROPOSED |

---

## Critical Decision Points (For Each ADR)

### ADR-0321: Tool Execution Learning Events

**Foundational decision:** Capture telemetry synchronously or asynchronously?

| Aspect | Decision | Rationale | Risk |
|--------|----------|-----------|------|
| **Emission** | Async queue, non-blocking | Tool execution latency unaffected | Queue fills → events dropped (mitigated: alert + max size) |
| **Error messages** | Regex sanitization + _assert_safe() | PII risk with stack traces | Accidental leakage (mitigated: validation function) |
| **Operator ratings** | Optional, retroactive (Gap 7) | No blocking latency | User doesn't rate → missing signal (mitigated: recorded anyway) |

**Question 1:** Do we accept async emission in critical path?  
→ **Recommendation:** YES. Tool execution should never block on telemetry.

**Question 2:** How do we validate PII removal?  
→ **Recommendation:** _assert_safe() fail-closed function + code review finding.

**Question 3:** What if EventEmitter queue fills?  
→ **Recommendation:** Drop oldest (logged) but don't block; surface as telemetry metric.

---

### ADR-0322: Tool Performance Ranking

**Foundational decision:** What scoring formula?

| Factor | Weight | Justification | Tuning |
|--------|--------|----------------|--------|
| Success rate | 0.3 | Primary (does tool work?) | If reuse too aggressive → raise threshold 0.7→0.75 |
| Latency | 0.2 | Secondary (is it fast?) | Set alert if p95 > 2s |
| Cost | 0.2 | Tertiary (is it cheap?) | Weight adjustable via Gap 6 |
| Trend | 0.1 | Bonus (improving?) | Future: replace with time-series (Gap 4) |
| Cold-start | -0.2 | Penalty (too few samples?) | Conservative (safe) |
| Base | 0.5 | Neutral start | Symmetric ±0.3 |

**Question 1:** Are these weights justified?  
→ **Recommendation:** YES, rationale in ADR section "Scoring Formula Justification." Can A/B test after 2 weeks.

**Question 2:** When do we reuse vs generate?  
→ **Recommendation:** Reuse if score > 0.7 (high confidence). Threshold tunable per operator.

**Question 3:** How do we prevent stale tool reuse?  
→ **Recommendation:** Max tool age 30 days; refresh if older.

---

### ADR-0323: Skill Attribution

**Foundational decision:** Which attribution model for MVP?

| Model | MVP? | Rationale | Future |
|-------|------|-----------|--------|
| EQUAL | ✅ YES | Fair, no external data | Default for v0.2.1 |
| WEIGHTED | ❌ NO | Requires Gap 4 metrics | Enabled in v0.3 |
| FIRST | ❌ NO | Penalizes improvements | Reference only |
| LAST | ❌ NO | Penalizes foundation | Reference only |

**Question 1:** Should we implement WEIGHTED now or defer?  
→ **Recommendation:** DEFER to Gap 4 (after aggregation stable). EQUAL is safe MVP.

**Question 2:** What if strategy has only 1 skill?  
→ **Recommendation:** That skill gets credit=1.0 (test case: test_attribution_single_skill).

**Question 3:** How do we wire the event subscription?  
→ **Recommendation:** KEY FIX in ADR: SkillForgeSubsystem.startup() → hub.subscribe("strategy.outcome", self.on_strategy_outcome).

---

### ADR-0324: Performance Aggregation

**Foundational decision:** On-demand or background aggregation?

| Approach | Decision | Rationale | Trade-off |
|----------|----------|-----------|-----------|
| **Background** | ✅ CHOSEN | Hourly batch + cache | Stale data (1h max) |
| On-demand | ❌ REJECTED | O(n) query too expensive | Would block ranking |

**Question 1:** Is 1-hour staleness acceptable?  
→ **Recommendation:** YES. Learning is low-velocity (tool ranking won't change much in 1h).

**Question 2:** How do we handle cold-start (few samples)?  
→ **Recommendation:** Bayesian Beta-Binomial with prior Beta(2,2); confidence intervals handle uncertainty.

**Question 3:** Should we aggregate per (tool_id, task_type)?  
→ **Recommendation:** YES. Fine-grained buckets enable finer ranking (e.g., "code" vs "research" tasks).

---

### ADR-0325: Context Coherence

**Foundational decision:** Should learned context carry across sessions?

| Aspect | Decision | Rationale | Risk |
|--------|----------|-----------|------|
| **Parent session** | Match by task_type + age < 24h | Contextual similarity | Wrong parent matched (mitigated: tunable age) |
| **Inheritance** | Tool/skill IDs from parent | Tools worked before → try again | Stale parent (mitigated: max_age_hours=24) |
| **Blending** | parent_weight=0.3 (30% boost) | Conservative; current data dominates | Tool good in session 1, bad in session 2 (mitigated: tunable weight) |

**Question 1:** Should context carry forward indefinitely?  
→ **Recommendation:** NO. Limit to 24 hours (conservative). Operators can tune.

**Question 2:** How do we resolve conflicts (parent recommends A, current data suggests B)?  
→ **Recommendation:** Blend: if A in parent, boost score by 0.2 (30% weight). Current data still dominant.

**Question 3:** Is this MVP or post-MVP?  
→ **Recommendation:** POST-MVP (Gap 5). Blocks nothing; nice-to-have optimization.

---

### ADR-0326: Cost Learning

**Foundational decision:** Learn cost multipliers via EMA?

| Aspect | Decision | Rationale | Risk |
|--------|----------|-----------|------|
| **Learning rate** | EMA α=0.1 (10% weight per sample) | Conservative; large sample history | Slow adaptation (mitigated: can tune α) |
| **Aggregation** | Median (robust) not mean | Outliers handled | Median less reactive (mitigated: intended) |
| **Granularity** | Per (tool_id, model_id) | Fine-grained tracking | Fragmentation (mitigated: groups by model) |

**Question 1:** Is EMA the right approach?  
→ **Recommendation:** YES. Simple, proven for cost forecasting. Alternatives (ARIMA, Kalman) overkill for MVP.

**Question 2:** How do we detect outliers (e.g., tool cost 10x estimate)?  
→ **Recommendation:** Flag if actual > 2x estimated; alert operator; log in telemetry.

**Question 3:** Is this MVP or post-MVP?  
→ **Recommendation:** POST-MVP (Gap 6). Blocks nothing; pure optimization.

---

### ADR-0327: Operator Feedback

**Foundational decision:** Synchronous or asynchronous feedback processing?

| Approach | Decision | Rationale | Trade-off |
|----------|----------|-----------|-----------|
| **Async** | ✅ CHOSEN | Non-blocking; feedback processed within minutes | Operator doesn't see instant effect |
| Sync | ❌ REJECTED | Would block rating UI | Latency on operator (bad UX) |

**Question 1:** How do we weight operator feedback vs auto-grades?  
→ **Recommendation:** Equal weight initially (both are signals). Can adjust based on operator expertise (future work).

**Question 2:** What if operator gives 1 rating that contradicts 100 auto-grades?  
→ **Recommendation:** Implement sample size threshold (ignore feedback if < 10 total grades). Audit trail visible.

**Question 3:** Is the UI in scope for this gap?  
→ **Recommendation:** NO. Gap 7 covers backend (feedback handler + API); UI deferred to Gap 7b.

---

## Risk Summary

| Category | Risk | Mitigation | Owner |
|----------|------|-----------|-------|
| **Data loss** | EventEmitter queue fills → events dropped | Max queue size + alert + drop metric | Learning Team |
| **PII leakage** | Error messages contain paths/schema | Regex sanitization + _assert_safe() | Learning Team |
| **Stale tools** | Tool remains in service despite failures | Max age 30 days + refresh trigger | Tool Forge Team |
| **Unfair grading** | New skills penalized despite high success | EQUAL model (fair by default) + WEIGHTED future | Skill Forge Team |
| **Cold-start** | New tools/skills with few samples underrated | Bayesian smoothing (Beta-Binomial prior) | Learning Team |
| **Tenant isolation** | Cross-tenant data leakage | All queries filter by tenant_id; test coverage | Learning Team |
| **Event latency** | Telemetry capture slows tool execution | Async emission (non-blocking) | Learning Team |
| **Stale metrics** | Aggregation cache 1h old; decisions use outdated data | Eventual consistency model (acceptable for learning) | Learning Team |
| **Scoring formula** | Magic numbers arbitrary; hard to justify | Published rationale + tuning strategy | Tool Forge Team |
| **Operator feedback abuse** | Malicious downrating; adversarial input | Audit trail visible; sample threshold (future: moderation) | Operator Team |

---

## Implementation Blockers

**Critical path (must be approved first):**

1. ✅ **ADR-0314** — Learning Infrastructure (already approved)
2. ⏳ **ADR-0321** — Tool Execution Events (blocks all others)
3. ⏳ **ADR-0324** — Performance Aggregation (blocks ranking & attribution)

**Parallel (no inter-dependencies):**
- ⏳ **ADR-0322** — Tool Ranking (after 0321 + 0324)
- ⏳ **ADR-0323** — Skill Attribution (after 0321 + 0324)
- ⏳ **ADR-0325** — Context Coherence (after 0321 + 0322)
- ⏳ **ADR-0326** — Cost Learning (after 0321 + 0324)
- ⏳ **ADR-0327** — Operator Feedback (independent)

---

## Recommended Review Timeline

| Week | Focus | ADRs | Decision |
|------|-------|------|----------|
| **Week 1** | Foundation + Telemetry | 0321 | APPROVE / REQUEST_CHANGES |
| **Week 2** | Aggregation (enables ranking) | 0324 | APPROVE / REQUEST_CHANGES |
| **Week 3** | Ranking + Attribution (apply metrics) | 0322, 0323 | APPROVE / REQUEST_CHANGES |
| **Week 4** | Optimization + Feedback | 0325, 0326, 0327 | APPROVE / REQUEST_CHANGES |

**Post-review:** All ADRs → CODE_REVIEW findings (5 → 7 per ADR) → Implementation starts

---

## MVP Scope (Weeks 1–2, Minimum Viable Product)

| Gap | Scope | Effort | Value | Include? |
|-----|-------|--------|-------|----------|
| 1 | TOOL_EXECUTED events + audit trail | 7 days | 100% (foundational) | ✅ YES |
| 2 | Tool ranking + reuse decision | 6 days | 80% (primary ROI) | ✅ YES |
| 4 | Aggregation + confidence intervals | 5 days | 70% (enables ranking) | ✅ YES |
| 3 | Skill attribution (EQUAL model) | 5 days | 60% (fair grading) | ⚠️ MAYBE |
| 5–7 | Coherence, Cost Learning, Feedback | 27 days | 40% (optimizations) | ❌ POST-MVP |

**MVP value proposition:** "Tools are ranked by success rate; system learns which tools work; operators see improvement over 2 weeks."

---

## Open Questions for Architecture Team

1. **Async emission priority:** Is non-blocking emission acceptable in critical path, even if queue drops events under load? (We recommend: YES, with alerting.)

2. **Scoring formula ownership:** Who owns tuning scoring weights over time? Should ops be a toggleable knob in Settings → Features? (We recommend: tunable, default published weights.)

3. **Skill attribution fairness:** Is EQUAL model sufficient for MVP, or does business require WEIGHTED attribution day 1? (We recommend: EQUAL MVP, WEIGHTED in v0.3.)

4. **Cold-start handling:** Should new tools/skills with high success rate but <5 samples be penalized (-0.2)? Or should we fully trust small sample sets? (We recommend: -0.2 penalty is conservative/safe.)

5. **Operator feedback moderation:** Should we implement feedback moderation (flag controversial ratings) now or defer? (We recommend: DEFER; sample threshold sufficient for MVP.)

6. **Cost learning baseline:** What if cost multipliers are wrong (e.g., model pricing changes)? Should we auto-reset? (We recommend: Manual reset; version multipliers with model_id.)

7. **Tenant isolation rigor:** Should we add E2E test for cross-tenant queries? (We recommend: YES; critical for compliance.)

8. **Feature flag architecture:** Should all 7 gaps be independently toggleable, or should we require Gap 1 ON for others to work? (We recommend: Independent toggles; Gap 1 off → others silent no-op.)

9. **Audit trail volume:** Will learning events 10–100 MB/day per tenant overwhelm audit storage? (We recommend: Yes, acceptable; plan for retention policy in ADR-0319.)

10. **Measurement instrumentation:** Who owns weekly dashboards measuring tool reuse rate, skill improvement speed, cost accuracy? (We recommend: Learning Team owns instrumentation; Ops owns dashboards.)

---

## Approval Checklist Template

**For each ADR, Architecture Team should complete:**

- [ ] Conceptual level decision is clear (What principle?)
- [ ] Structural level constraints documented (What APIs/events?)
- [ ] Implementation level code/types specified (What frozen dataclasses?)
- [ ] Consequences listed (positive + negative)
- [ ] Alternatives considered (3–5 options)
- [ ] Risk assessment complete + mitigations sound
- [ ] Backwards compatible (no breaking changes)
- [ ] GDPR-compliant (no PII, audit trail, consent)
- [ ] Tenant isolation enforced (queries filter by tenant_id)
- [ ] Integration points clear (which subsystems?)
- [ ] Effort estimate realistic (days + person allocation)
- [ ] Testing strategy sound (unit + E2E + reachability)
- [ ] Rollout plan clear (feature flags, canary %)
- [ ] Dependencies documented (blockers + unblocks)

---

## Success Criteria (Phase 1–4, 10 weeks)

| Phase | Objective | Success Metric |
|-------|-----------|-----------------|
| **Phase 1** (Weeks 1–2) | Telemetry flowing | ✅ TOOL_EXECUTED events emitted at 100%, <50ms latency overhead |
| **Phase 2** (Weeks 3–4) | Tool ranking working | ✅ Tools reused in 20%+ of decisions, cost savings 20–30% |
| **Phase 3** (Weeks 5–8) | Skill grading fair | ✅ Attribution audit trail 100%, WEIGHTED model ready (v0.3) |
| **Phase 4** (Weeks 9–10) | System learning | ✅ Tool convergence (Top 3 tools used in 60%+ of similar tasks), 8–15x ROI vs independent systems |

---

## References

- **ADR-0314:** Learning Infrastructure (already approved)
- **ADR-0321 through 0327:** Full decision records (see /Corvin-ADR/decisions/)
- **DETAILED_DESIGN_ALL_INTEGRATIONS.md:** Implementation details per gap
- **CODE_REVIEW_INTEGRATION_GAPS.md:** Code review findings (5–7 per ADR)
- **COMPLIANCE_BASELINE.md:** GDPR requirements (Art. 5, 6, 30, 32)

---

**Prepared by:** Claude Code  
**Review Due:** 2026-08-26 (1 week)  
**Next Step:** Architecture Team approval → Code review findings → Implementation kickoff
