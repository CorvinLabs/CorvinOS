# Implementation Roadmap: Learning Integration (v0.2.1)

**Date:** 2026-08-19  
**Scope:** ADR-0321 through ADR-0327 (7 gaps)  
**Duration:** 10 weeks (50 person-days)  
**Team Size:** 6 engineers (parallel)  
**Target Release:** v0.2.1 (post-v1.0 cycle)

---

## Timeline Overview

```
Phase 1: Foundation (Weeks 1–2)
├─ Gap 1: Tool Execution Learning Events (BLOCKER)
└─ Duration: 7 days
   Status: Gap 1 complete → Unblocks all others

Phase 2: Application (Weeks 3–4)
├─ Parallel: Gap 2 (Tool Ranking) + Gap 4 (Aggregation)
├─ Sequential: Gap 3 (Skill Attribution) after Gap 4 stabilizes
└─ Duration: 14 days

Phase 3: Optimization (Weeks 5–6)
├─ Parallel: Gap 5 (Context Coherence) + Gap 6 (Cost Learning)
└─ Duration: 8 days

Phase 4: Feedback & Integration (Weeks 7–8)
├─ Gap 7: Operator Feedback Loop
├─ E2E Testing, Performance Benchmarking
└─ Duration: 8 days

Phase 5: Rollout & Monitoring (Weeks 9–10)
├─ Feature flag staging (10% → 50% → 100%)
├─ Operator education
├─ Monitoring dashboard
└─ Duration: 10 days

Total Critical Path: 47 days (7 + 14 + 8 + 8 + 10)
Parallel efficiency: ~50 person-days for 6 engineers
```

---

## Phase 1: Foundation (Weeks 1–2, Days 1–7)

### Goal
Complete Gap 1 (Tool Execution Events). This is the blocker; all downstream work depends on it.

### Deliverables
- ✅ TOOL_EXECUTED events flowing at 100% rate
- ✅ ToolExecutionTelemetry dataclass validated
- ✅ Audit trail entry per execution
- ✅ PII sanitization verified
- ✅ Feature flag wired

### Sprint 1A: Days 1–2 (Data Structures & Validation)

| Day | Task | Owner | Status | Notes |
|-----|------|-------|--------|-------|
| 1 | Implement ToolExecutionTelemetry dataclass | Eng A | | Frozen, immutable, __post_init__ validation |
| 1 | Implement _sanitize_error_message() function | Eng A | | Regex removes paths, schema, traces |
| 2 | Implement _validate_tokens() helper | Eng A | | Assertion: subsystem_tokens ⊆ total tokens |
| 2 | Unit tests: validation, field ranges, edge cases | Eng A | | 4 test cases: happy, error, rating, tokens |

### Sprint 1B: Days 3–5 (ToolForgeSubsystem Integration)

| Day | Task | Owner | Status | Notes |
|-----|------|--------|-------|-------|
| 3 | Add _handle_tool_execute() method | Eng A | | Captures telemetry, wraps in LearningEvent |
| 3 | Add _emit_tool_executed_event() method | Eng A | | Non-blocking queue emission |
| 4 | Add _handle_tool_rate() method (operator ratings) | Eng A | | Retroactive rating attachment |
| 4 | Wire event subscription (audit trail) | Eng A | | Every emission audited |
| 5 | Integration tests (event emission, error handling) | Eng A | | 4 test cases |

### Sprint 1C: Days 6–7 (Event Schema & Documentation)

| Day | Task | Owner | Status | Notes |
|-----|------|--------|-------|-------|
| 6 | Add TOOL_EXECUTED to LearningEventType enum | Eng A | | Update event schema (ADR-0314 extension) |
| 6 | Add ToolExecutedPayload dataclass | Eng A | | Immutable, type-safe |
| 7 | Feature flag: learning_gap_1_tool_telemetry | Eng A | | Default: false |
| 7 | Code review + merge → main | Eng A + Lead | | All findings addressed |

### Success Criteria

- [ ] `test_tool_execution_emits_learning_event` ✅
- [ ] `test_operator_rating_event_emitted` ✅
- [ ] Audit trail contains ≥1 tool.executed entry per execution
- [ ] Error message sanitization validated (no PII)
- [ ] EventEmitter queue drop rate < 0.1%
- [ ] Latency overhead < 50ms (p99)
- [ ] PR merged; ADR-0321 → ACCEPTED

**Blocking for Phase 2:** Yes. All other gaps depend on this data flowing.

---

## Phase 2: Application (Weeks 3–4, Days 8–21)

### Goal
Complete Gaps 2, 3, 4 in sequence. Gap 4 must precede Gaps 2 & 3 because both need aggregated metrics.

### Timeline (Sequential then Parallel)

```
Days 8–13: Parallel (Gap 2 + Gap 4)
├─ Eng C: Gap 2 (Tool Ranking) — 6 days
├─ Eng B: Gap 4 (Performance Aggregation) — 5 days
└─ Result: Ranking API ready + metrics flowing

Day 14–19: Gap 3 (Skill Attribution) — 6 days
├─ Eng D: Uses Gap 4 metrics for WEIGHTED model (future)
└─ Result: Fair skill grading operational

Days 20–21: Integration + Testing — 2 days
├─ All: E2E scenarios, performance benchmarks
└─ Result: All 3 gaps integrated + working together
```

### Sprint 2A: Days 8–13 (Parallel: Gap 2 + Gap 4)

#### Gap 2: Tool Performance Ranking (Eng C, 6 days)

| Day | Task | Status | Notes |
|-----|------|--------|-------|
| 8 | Implement ToolPerformanceMetrics dataclass | | Success count, latency percentiles, cost |
| 8 | Implement ToolRankingManager (base class) | | Takes EventStore, computes rankings |
| 9 | Implement scoring formula (_score_tool) | | 7-factor: success, latency, cost, trend, cold-start, base |
| 9 | Implement cache with TTL (KEY FIX from review) | | 5-min cache; cache_key = f"{tenant}:{task_type}:{error}" |
| 10 | Implement pagination for queries (KEY FIX) | | limit=10000; prevents memory exhaustion |
| 10 | Implement audit trail logging (KEY FIX) | | Event: tool.ranking_computed |
| 11 | Unit tests (12 cases): ranking, filtering, cold-start | | All edge cases covered |
| 11 | Integration tests: decision rule (score > 0.7 → reuse) | | Fallback to generate if low score |
| 12 | ToolForgeSubsystem._handle_select_tool() | | Wires ranking into tool selection |
| 12 | Feature flag: learning_gap_2_tool_ranking | | Default: false |
| 13 | Code review + merge | | KEY FIX findings addressed |

#### Gap 4: Performance Aggregation (Eng B, 5 days)

| Day | Task | Status | Notes |
|-----|------|--------|-------|
| 8 | Implement ConfidenceIntervalCalculator (Bayesian) | | Beta-Binomial; scipy.stats.beta |
| 9 | Implement PerformanceAggregator (batch queries) | | Queries EventStore for TOOL_EXECUTED events |
| 9 | Implement aggregation logic (success rates, percentiles) | | Groups by tool_id, computes P50/P95/P99 |
| 10 | Implement AggregationScheduler (hourly) | | Background loop; runs every 60 minutes |
| 11 | Wire into SubsystemHub (scheduler startup) | | Emits PERFORMANCE_METRICS_COMPUTED events |
| 11 | Unit tests (15 cases): intervals, aggregation, edge cases | | Cold-start, zero samples, outliers |
| 12 | Integration: PerformanceAggregator lives in Brain hub | | Accessible to Gap 2 & 3 |
| 12 | Feature flag: learning_gap_4_aggregation | | Default: false |
| 13 | Code review + merge | | Ready for use by Gap 2 & 3 |

### Sprint 2B: Days 14–19 (Sequential: Gap 3 after Gap 4)

#### Gap 3: Skill Attribution (Eng D, 6 days)

| Day | Task | Status | Notes |
|-----|------|--------|-------|
| 14 | Implement AttributionModel enum (4 models) | | EQUAL, WEIGHTED, FIRST, LAST |
| 14 | Implement SkillAttributionEngine (EQUAL model) | | Default for MVP; fair by design |
| 15 | Implement EQUAL attribution logic | | credit_per_skill = 1.0 / num_skills |
| 15 | Implement FIRST and LAST (reference only) | | Not recommended; included for completeness |
| 16 | Stub WEIGHTED with comment "see Gap 4" | | Will be fully implemented later |
| 16 | Unit tests (10 cases): all models, edge cases | | Single skill, empty list, strategy outcomes |
| 17 | Wire event subscription in SkillForgeSubsystem.startup() | | KEY FIX: hub.subscribe("strategy.outcome", ...) |
| 17 | Implement _grade_skill() (full implementation) | | KEY FIX: Calls registry.update_skill() |
| 18 | Implement audit trail per attribution (KEY FIX) | | Event: skill.attribution with credit, model, outcome |
| 18 | Integration tests (5 cases): event handling, grading | | Verify skills graded with correct credit |
| 19 | Feature flag: learning_gap_3_skill_attribution | | Default: false |
| 19 | Code review + merge | | All KEY FIX findings addressed |

### Sprint 2C: Days 20–21 (Integration & E2E)

| Day | Task | Owner | Status | Notes |
|-----|------|--------|-------|-------|
| 20 | E2E test: Gap 1 → Gap 4 → Gap 2 → tool selected | Eng G | | Execute tool → event emitted → ranked → selected |
| 20 | E2E test: Gap 1 → Gap 4 → Gap 3 → skill graded | Eng G | | Strategy outcome → skills attributed fairly → graded |
| 21 | Performance benchmark (all 3 gaps combined) | Eng G | | Measure latency per component |
| 21 | Code review + merge (integration branch) | Lead | | Phase 2 complete |

### Success Criteria

- [ ] Tool ranking decisioning: score > 0.7 → reuse, else generate ✅
- [ ] Aggregation completes in < 30 minutes ✅
- [ ] Cache hit rate > 80% (repeated ranking queries) ✅
- [ ] Skill attribution: EQUAL model fair (each skill = 1/N credit) ✅
- [ ] All audit trails flowing: tool.ranking_computed + skill.attribution ✅
- [ ] E2E: Tools reused in 5+ decision cycles (test validation) ✅

---

## Phase 3: Optimization (Weeks 5–6, Days 22–31)

### Goal
Complete Gaps 5 & 6 in parallel. Both are optimizations; neither blocks critical path.

### Sprint 3A: Days 22–27 (Parallel: Gap 5 + Gap 6)

#### Gap 5: Context Coherence (Eng E, 5 days)

| Day | Task | Status | Notes |
|-----|------|--------|-------|
| 22 | Implement SessionContext dataclass | | Parent session, inheritance, freshness |
| 22 | Implement ContextCoherenceManager (base) | | Finds parent sessions, blends rankings |
| 23 | Implement find_parent_session() | | Query for SESSION_CREATED events; max_age_hours=24 |
| 23 | Implement get_inherited_tools/skills() | | Top-N tools/skills from parent session |
| 24 | Implement blend_tool_rankings() | | Boost parent tools by +0.2 (30% weight) |
| 24 | Wire into ToolRankingManager (optional boost) | | If parent context available, apply blending |
| 25 | Unit tests (8 cases): parent finding, blending, age | | Edge cases: no parent, stale parent, multiple parents |
| 25 | Feature flag: learning_gap_5_context_coherence | | Default: false |
| 26 | Code review + merge | | |

#### Gap 6: Cost Learning (Eng F, 5 days)

| Day | Task | Status | Notes |
|-----|------|--------|-------|
| 22 | Implement CostLearner (EMA updates) | | EMA α=0.1; learning rate tunable |
| 22 | Implement observe_execution() | | Tracks estimated vs actual; updates multiplier |
| 23 | Implement get_cost_estimate() (corrected) | | Returns base_cost * multiplier |
| 23 | Implement aggregate_multipliers() | | Computes median estimated/actual per (tool, model) |
| 24 | Wire into CostController | | Observations flow on every tool execution |
| 24 | Outlier detection (actual > 2x estimated) | | Flag for alert; log in telemetry |
| 25 | Unit tests (10 cases): EMA updates, aggregation | | Cold-start, outliers, model changes |
| 25 | Feature flag: learning_gap_6_cost_learning | | Default: false |
| 26 | Code review + merge | | |

### Sprint 3B: Days 28–31 (Testing & Documentation)

| Day | Task | Owner | Status | Notes |
|-----|------|--------|-------|-------|
| 28 | E2E test: Gap 5 (parent context inherited) | Eng G | | Create session 1 with tool X; session 2 should prefer X |
| 28 | E2E test: Gap 6 (cost multiplier learned) | Eng G | | Execute tool N times; cost estimate should converge |
| 29 | Update DETAILED_DESIGN_ALL_INTEGRATIONS.md | Eng Lead | | Incorporate fixes from all gaps |
| 29 | Operator guides (ranking, attribution, coherence, cost) | Eng A | | 4 docs explaining each feature |
| 30 | Performance benchmarking (all 6 gaps) | Eng G | | Measure end-to-end latency |
| 31 | Code review + merge (Phase 3 integration) | Lead | | Phase 3 complete |

### Success Criteria

- [ ] Parent session found for similar task_type ✅
- [ ] Tool rankings blended with parent preference ✅
- [ ] Cost multiplier converges (within 10% after 20 samples) ✅
- [ ] All gaps integrated without regressions ✅

---

## Phase 4: Feedback & Integration (Weeks 7–8, Days 32–39)

### Goal
Complete Gap 7 (Operator Feedback). Integrate all gaps; ensure E2E systems test.

### Sprint 4A: Days 32–36 (Gap 7 Implementation)

#### Gap 7: Operator Feedback (Eng D, 5 days)

| Day | Task | Status | Notes |
|-----|------|--------|-------|
| 32 | Implement OperatorFeedbackHandler | | rate_tool(), rate_skill() methods |
| 32 | Implement FeedbackAdjuster | | Adjusts skill grades on feedback |
| 33 | Console API endpoints (/api/feedback/*) | | POST /api/feedback/rate-tool, /api/feedback/rate-skill |
| 33 | Wire into SubsystemHub (event subscription) | | hub.subscribe(OPERATOR_RATED_SKILL, ...) |
| 34 | Unit tests (10 cases): feedback emission, grade adjustment | | Rating mapping (5→+0.5, 1→-0.5, etc) |
| 35 | Integration tests: feedback → grade update | | Verify skill grade adjusts after operator rates |
| 35 | Feature flag: learning_gap_7_operator_feedback | | Default: false |
| 36 | Code review + merge | | |

### Sprint 4B: Days 37–39 (System Testing)

| Day | Task | Owner | Status | Notes |
|-----|------|--------|-------|-------|
| 37 | E2E: Full learning loop (Gap 1 → 7) | Eng G | | Execute tool → rank → attribute skill → operator rates → grade adjusts |
| 37 | E2E: Multi-session coherence | Eng G | | Session 1 ranks tool X; Session 2 inherits + boosts X |
| 38 | Stress test (1000 events/min through EventStore) | Eng G | | Verify no drops, latency < 200ms |
| 38 | Data consistency test (cross-tenant isolation) | Eng G | | Verify Tenant A events don't appear in Tenant B queries |
| 39 | Cleanup & documentation | Lead | | Phase 4 complete; ready for canary |

### Success Criteria

- [ ] OPERATOR_RATED_SKILL events emitted and processed ✅
- [ ] Skill grades adjusted (+0.5 for 5 stars, -0.5 for 1 star) ✅
- [ ] E2E learning loop functional (all 7 gaps) ✅
- [ ] No data loss; audit trail 100% complete ✅

---

## Phase 5: Rollout & Monitoring (Weeks 9–10, Days 40–50)

### Goal
Stage learning system to production. Monitor key metrics. Prepare for full release.

### Sprint 5A: Days 40–44 (Feature Flag Staging)

| Day | Task | Owner | Status | Notes |
|-----|------|--------|-------|-------|
| 40 | Create monitoring dashboard (Prometheus metrics) | Ops | | Track event latency, queue drops, aggregation time |
| 40 | Canary deployment (10% internal tenants) | Ops | | All 7 flags ON for 10% |
| 41 | Monitor: Event latency, error rates | Eng + Ops | | Alert if p99 > 100ms or drop rate > 0.1% |
| 41 | Verify audit trail integrity | Eng | | Hash-chain continuous check (audit verify) |
| 42 | Tool reuse metrics (% of decisions using ranked tools) | Eng G | | Expected: 20%+ day 1, 40%+ by day 3 |
| 42 | Skill promotion metrics (% reaching auto-promote threshold) | Eng G | | Expected: 10%+ by day 2 |
| 43 | Go/No-Go decision: Canary → Beta (50%) | Lead + Ops | | If all metrics green, promote to 50% |
| 44 | Beta deployment (50% customer tenants opt-in) | Ops | | Roll to voluntary customers |

### Sprint 5B: Days 45–50 (Full Rollout)

| Day | Task | Owner | Status | Notes |
|-----|------|--------|-------|-------|
| 45 | Monitor Beta (48h): Alert on regressions | Eng + Ops | | Latency, cost accuracy, tenant isolation |
| 46 | Operator education (webinar, docs, support) | Eng A + Ops | | "How to understand tool ranking" guide |
| 47 | Go/No-Go decision: Beta → Full (100%) | Lead + Ops | | If no regressions, enable 100% (opt-out available) |
| 48 | Full rollout (all tenants, default ON, disableable) | Ops | | Flags ship default-true for new installs |
| 49 | Monitoring dashboard live + alerting | Ops | | PagerDuty integration for critical issues |
| 50 | v0.2.1 release notes + operator changelog | Eng Lead | | Summarize all 7 gaps, highlight value, docs links |

### Success Criteria

- [ ] Event latency p99 < 100ms ✅
- [ ] EventEmitter queue drop rate < 0.1% ✅
- [ ] Tool reuse 20%+ of decisions ✅
- [ ] Skill promotion 10%+ reaching threshold ✅
- [ ] Zero tenant isolation breaches ✅
- [ ] Audit trail 100% complete + hash-chain verified ✅
- [ ] v0.2.1 released to all users ✅

---

## Team Assignments & Allocation

### Staffing Model

**6 engineers, 50 person-days total, 10-week duration:**

```
Learning Team (2 engineers)
├─ Eng A: Gap 1 lead + operator guides
├─ Eng B: Gap 4 lead (aggregation)
└─ 10 days each (20 total)

Tool Forge Team (1 engineer)
├─ Eng C: Gap 2 lead (ranking)
└─ 6 days

Skill Forge Team (1 engineer)
├─ Eng D: Gap 3 lead (attribution) + Gap 7 (feedback)
└─ 11 days

Brain/Orchestration Team (2 engineers)
├─ Eng E: Gap 5 lead (context coherence)
├─ Eng F: Gap 6 lead (cost learning)
└─ 5 days each (10 total)

QA Team (1 engineer)
├─ Eng G: E2E testing, benchmarking, monitoring
└─ 13 days

Leads (Shared)
├─ Eng Lead (Scrum Master): Coordination, code review, merge gate
├─ Ops Lead: Deployment, monitoring, rollout
└─ Part-time allocation
```

### Per-Phase Allocation

```
Phase 1 (Days 1–7):
├─ Eng A: 7 days (100%)
├─ Eng Lead: 2 days (code review, merge)
└─ Ops: 1 day (feature flag setup)

Phase 2 (Days 8–21):
├─ Eng B: 5 days (Gap 4)
├─ Eng C: 6 days (Gap 2)
├─ Eng D: 6 days (Gap 3)
├─ Eng G: 2 days (E2E)
└─ Eng Lead: 3 days (code review, merge)

Phase 3 (Days 22–31):
├─ Eng E: 5 days (Gap 5)
├─ Eng F: 5 days (Gap 6)
├─ Eng A: 2 days (documentation)
├─ Eng G: 4 days (E2E, benchmarking)
└─ Eng Lead: 2 days (code review)

Phase 4 (Days 32–39):
├─ Eng D: 5 days (Gap 7)
├─ Eng G: 4 days (system E2E)
└─ Eng Lead: 2 days (code review)

Phase 5 (Days 40–50):
├─ Ops: 8 days (deployment, monitoring)
├─ Eng G: 3 days (performance testing)
├─ Eng A: 2 days (operator education)
├─ Eng Lead: 2 days (release notes)
└─ Entire team: 1 day (go/no-go decisions)
```

---

## Critical Path Dependencies

```
ADR-0314 (approved)
    ↓
Day 1: ADR-0321 (Gap 1) ← BLOCKER
    ↓ (unblocks all)
    ├─→ Day 8: ADR-0324 (Gap 4) ← CRITICAL (feeds 2, 3)
    │       ├─→ Day 8: ADR-0322 (Gap 2) → Tool ranking
    │       ├─→ Day 14: ADR-0323 (Gap 3) → Skill attribution
    │       ├─→ Day 22: ADR-0325 (Gap 5) → Context coherence
    │       └─→ Day 22: ADR-0326 (Gap 6) → Cost learning
    │
    └─→ Day 32: ADR-0327 (Gap 7) → Operator feedback

Critical Path Length: Day 1 + 7 (Gap 1) + 5 (Gap 4) + 6 (Gap 2) = 18 days
                    With parallel Phase 2: 13 days (8–21)
                    Plus Phase 3 serial: 8 days (22–31)
                    Plus Phase 4: 8 days (32–39)
                    Plus Phase 5: 11 days (40–50)
                    = 47 days actual
```

**Safe schedule:** 50 days (7 working weeks) = Weeks 1–10 with Friday buffers

---

## Milestone Map

| Milestone | Date (Target) | Criteria | Owner |
|-----------|---------------|----------|-------|
| **M1: Gap 1 Complete** | End Week 2 | TOOL_EXECUTED events flowing; PR merged | Eng A |
| **M2: Aggregation Ready** | End Week 4 (Day 13) | Metrics computed hourly; cache working | Eng B |
| **M3: Ranking + Attribution** | End Week 4 (Day 21) | Tools ranked, skills fairly graded | Eng C, D |
| **M4: Optimizations** | End Week 6 (Day 31) | Context coherence + cost learning | Eng E, F |
| **M5: Feedback Loop** | End Week 8 (Day 39) | Operator ratings integrated; E2E system test | Eng D, G |
| **M6: Canary Live** | End Week 9 (Day 44) | 10% tenants; metrics baseline | Ops |
| **M7: Full Release** | End Week 10 (Day 50) | v0.2.1 shipped; all users able to opt-in | Ops, Lead |

---

## Success Metrics (By Phase)

### Phase 1 Metrics
- TOOL_EXECUTED event emission rate: 100%
- Telemetry latency: <50ms p99
- PII sanitization: 100% (no failures)
- Event loss rate: <0.1%

### Phase 2 Metrics
- Tool ranking decisioning: 100% reuse/generate decision
- Aggregation latency: <30 min for 100K events
- Cache hit rate: >80%
- Skill attribution fairness: EQUAL model verified

### Phase 3 Metrics
- Context coherence: Parent session found in 90%+ similar tasks
- Cost multiplier convergence: Within 10% after 20 samples
- Zero regressions: All Phase 1–2 metrics maintained

### Phase 4 Metrics
- Operator feedback events: Emitted on demand
- Skill grade adjustments: Applied within 5 minutes
- E2E loop time: <5 seconds total

### Phase 5 Metrics
- Tool reuse rate: 20%+ of decisions (vs. 0% pre-Phase 1)
- Skill promotion rate: 10%+ reaching threshold (vs. 0%)
- Cost accuracy: ±10% of forecast (vs. ±50% pre-learning)
- User satisfaction: 8+ NPS improvement (post-release survey)

---

## Risk Management

### Technical Risks

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|-----------|-------|
| EventEmitter queue fills | MEDIUM | High (event loss) | Max queue size + alert | Eng A |
| Aggregation too slow | MEDIUM | High (ranking blocks) | Cache + incremental updates | Eng B |
| Tenant isolation breach | LOW | CRITICAL | E2E test + audit review | Eng G |
| Cold-start skills penalized | LOW | Medium (UX issue) | Bayesian prior + documentation | Eng D |
| Feedback loop abuse | LOW | Low (audit visible) | Sample threshold + moderation (future) | Lead |

### Schedule Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| Code review delays | MEDIUM | 3–5 days | Parallel review threads |
| Unforeseen dependencies | MEDIUM | 2–3 days | Phase kickoff discovery meeting |
| Team interruptions | MEDIUM | 2–4 days | Dedicated team; no context switches |
| Env issues (CI/CD) | LOW | 1–2 days | Pre-Phase 1 env audit |

### Staffing Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| Engineer unavailable | LOW | 2–3 days | Pair programming; cross-training |
| New to codebase | LOW | 3–5 days | 1-week onboarding pre-Phase 1 |
| Skill gaps (Bayesian stats) | LOW | 2 days | Pre-implement review by statistics expert |

---

## Success Plan (Go/No-Go Gates)

### Phase 1 Go/No-Go (End of Week 2)

**Approve if:**
- [ ] Gap 1 PR merged to main
- [ ] Event emission rate 99.5%+ (2–3 hours canary data)
- [ ] Latency overhead <50ms p99
- [ ] PII sanitization validated (code + test review)
- [ ] Audit trail integrity verified

**Escalate if:**
- Event loss > 1%
- Latency > 100ms p99
- PII in any event (fail-closed validation catches)

**Decision:** Continue to Phase 2, or pause for fixes (max 2 days)

---

### Phase 2 Go/No-Go (End of Week 4)

**Approve if:**
- [ ] Gaps 2, 3, 4 PRs merged
- [ ] E2E: Tool ranked and selected successfully (5+ cycles)
- [ ] E2E: Skill graded fairly (attribution verified)
- [ ] No Phase 1 regressions
- [ ] All gaps stable (no new issues in 24h canary)

**Escalate if:**
- Tool ranking accuracy <80%
- Skill attribution unfair (test failure)
- New PII leaks or audit trail breaks

**Decision:** Continue to Phase 3, or pause for fixes (max 3 days)

---

### Phase 3 Go/No-Go (End of Week 6)

**Approve if:**
- [ ] Gaps 5, 6 PRs merged
- [ ] E2E: Context coherence working (parent → child boost)
- [ ] E2E: Cost multiplier converging
- [ ] No Phase 1–2 regressions

**Decision:** Continue to Phase 4 (feedback can proceed in parallel)

---

### Phase 4 Go/No-Go (End of Week 8)

**Approve if:**
- [ ] Gap 7 PR merged
- [ ] E2E: Operator feedback → grade adjustment
- [ ] Full system test: Day 1 → Day 7 complete
- [ ] All 7 gaps stable (48h canary)
- [ ] Monitoring dashboard live

**Decision:** Proceed to Phase 5 (staging rollout)

---

### Phase 5 Go/No-Go (End of Week 10)

**Approve if:**
- [ ] Canary metrics green (10% tenants, 48h)
- [ ] Tool reuse 20%+
- [ ] Cost accuracy within ±10%
- [ ] Zero tenant isolation breaches
- [ ] Operator education complete
- [ ] Support team trained

**Decision:** Release v0.2.1 to all users (all flags enabled by default)

---

## Communication Plan

### Weekly Status (Friday 4pm PT)

**Participants:** All engineers + Eng Lead + Ops Lead + Architecture point

**Format:** 15 min sync
- Gap completion status (% done)
- Blockers (if any)
- Metric trend (latency, error rate)
- Next week plan

### Bi-weekly Review (Every other Wednesday 2pm PT)

**Participants:** All engineers + Leads + Architecture Team

**Format:** 30 min
- Phase milestone review
- Go/No-Go assessment
- Risk updates
- Budget/schedule review

### Post-Phase Retrospective

**Timing:** Friday after go/no-go decision

**Format:** 1 hour, all participants
- What went well?
- What could improve?
- Lessons for next phase

---

## Documentation Deliverables

### Per-Phase
- [ ] **Phase 1:** DETAILED_DESIGN_ALL_INTEGRATIONS_FIXED.md (Gap 1 section)
- [ ] **Phase 2:** Gap 2, 3, 4 sections + LEARNING_EVENT_SCHEMA.md updates
- [ ] **Phase 3:** Gap 5, 6 sections + operator guides (3 docs)
- [ ] **Phase 4:** Gap 7 section + OPERATOR_FEEDBACK_GUIDE.md
- [ ] **Phase 5:** Release notes + v0.2.1 changelog + operator quickstart

### Operator Docs
- [ ] "Understanding Tool Ranking" (Gap 2)
- [ ] "Understanding Skill Attribution" (Gap 3)
- [ ] "Understanding Cost Learning" (Gap 6)
- [ ] "Operator Feedback: Rate Tools & Skills" (Gap 7)
- [ ] "Release Notes: v0.2.1 Learning Integration"

### Architecture Docs
- [ ] Event schema extension (ADR-0314 + 7 gaps)
- [ ] Data flow diagrams (Gaps 1–7)
- [ ] API reference (ranking, feedback endpoints)

---

## Budget & Resource Summary

```
Total Effort: 50 person-days

Breakdown by gap:
  Gap 1: 7 days (1 engineer)
  Gap 2: 6 days (1 engineer)
  Gap 3: 6 days (1 engineer)
  Gap 4: 5 days (1 engineer)
  Gap 5: 5 days (1 engineer)
  Gap 6: 5 days (1 engineer)
  Testing/Monitoring: 13 days (1 engineer)
  Leads/Reviews: 8 days (shared)
  ─────────────
  Total: 55 days

Adjusted for parallel: ~50 person-days (6 engineers, 10 weeks)

Cost (est.):
  Sr. Engineer × 5: $750k (50k/day × 3 days avg)
  Jr. Engineer × 1: $150k (30k/day × 5 days)
  ─────────────
  Total: ~$900k dev + ops overhead

Timeline:
  Phase 1: 7 days (Week 1–2)
  Phase 2: 14 days (Week 3–4)
  Phase 3: 8 days (Week 5–6)
  Phase 4: 8 days (Week 7–8)
  Phase 5: 11 days (Week 9–10)
  ─────────────
  Total: 48 days = 10 weeks (6 days/week, 1 day Friday buffer)
```

---

## Next Steps

1. **Week 0 (Pre-Phase 1):**
   - [ ] Architecture Team approval (ADRs 0321–0327)
   - [ ] Team kickoff meeting (all engineers + leads)
   - [ ] Environment setup (CI/CD, monitoring, feature flags)
   - [ ] Onboarding (ADR deep-dives, codebase walkthrough)

2. **Week 1 (Phase 1 Start):**
   - [ ] Eng A begins Gap 1 implementation
   - [ ] Code review queue open
   - [ ] Daily standup (15 min)

3. **Week 2 (Phase 1 Completion):**
   - [ ] Phase 1 go/no-go decision
   - [ ] Phase 2 kickoff (Eng B, C, D)

4. **Weeks 3–10:**
   - [ ] Phases 2–5 execution per schedule
   - [ ] Weekly status + bi-weekly deep dives
   - [ ] Metrics tracking (dashboards live Week 9)

---

**Prepared by:** Claude Code  
**Approved by:** [Architecture Lead]  
**Released:** 2026-08-19  
**Target Start:** 2026-08-26 (Week 1)
