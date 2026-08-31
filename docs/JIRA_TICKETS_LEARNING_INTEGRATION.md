# JIRA Ticket Specifications: Learning Integration (ADR-0321–0327)

**Date:** 2026-08-19  
**Project:** CorvinOS  
**Epic:** Learning Integration v0.2.1  
**Assignees:** See per-ticket  
**Total Effort:** 50 person-days

---

## EPIC: Learning Integration v0.2.1

```
Title: Learning Integration v0.2.1 — Tool Ranking, Skill Grading, Cost Optimization
Description:
  Wire learning infrastructure (ADR-0314) into live systems.
  7 gaps implement telemetry capture → ranking → attribution → cost learning → feedback loop.
  MVP (Gaps 1-4): 20+ person-days; Full (Gaps 1-7): 50 person-days.
  
Priority: HIGH (enables all downstream learning)
Status: PROPOSED (awaiting Architecture approval)
Start Date: 2026-08-26
Target Release: v0.2.1 (post-v1.0)
Labels: learning, architecture, adr-0321, adr-0327
```

---

## TICKET 1: Gap 1 — Tool Execution Learning Events

```
Title: [Learning] Gap 1 — Tool Execution Telemetry Capture (ADR-0321)
Story Points: 8
Sprint: Sprint 1 (Weeks 1–2)
Epic: Learning Integration v0.2.1
Assignee: Eng A (Learning Team)
Status: READY FOR DEV
Priority: BLOCKER (all other gaps depend)

Description:
Implement tool execution telemetry capture. Emit TOOL_EXECUTED learning events 
to EventStore on every tool execution. This is the foundational data layer for 
all downstream learning (tool ranking, skill grading, cost learning).

Acceptance Criteria:
  AC1: ToolExecutionTelemetry dataclass implemented (frozen, immutable)
  AC2: TOOL_EXECUTED events emitted at 100% rate (zero drops)
  AC3: Event latency overhead <50ms (p99)
  AC4: Error messages sanitized (no paths, schema, stack traces)
  AC5: Audit trail entry per execution (hash-chained)
  AC6: Operator rating retroactively attached (optional, async)
  AC7: Feature flag learning_gap_1_tool_telemetry wired (default: false)
  AC8: All code review findings addressed

Definition of Done:
  [ ] ToolExecutionTelemetry dataclass + validation (frozen)
  [ ] _sanitize_error_message() + _validate_tokens() helpers
  [ ] ToolForgeSubsystem._handle_tool_execute() integration
  [ ] ToolForgeSubsystem._emit_tool_executed_event() method
  [ ] EventEmitter non-blocking queue emission
  [ ] Audit trail integration (LearningEvent.write_event)
  [ ] TOOL_EXECUTED + OPERATOR_RATED_TOOL event types added
  [ ] 8 unit tests passing (validation, emission, errors, ratings)
  [ ] 4 integration tests (event flow, audit, rating attachment)
  [ ] Code review ✅ and merge to main
  [ ] ADR-0321 status changed to ACCEPTED
  
Implementation Notes:
  • Use frozen=True dataclass with __post_init__ validation
  • Event emission async (non-blocking queue, queue-full logs drop)
  • Operator ratings optional; retroactively attached via Gap 7
  • Sanitization: Remove absolute paths, schema names, internal service names
  • Validation: _assert_safe() fail-closed before emission
  • Compliance: GDPR Art. 5, 6, 30 checked (see ADR-0321)
  • Testing: Cross-tenant isolation test (Tenant A events ≠ Tenant B)

Key Files:
  - core/learning/tool_execution.py (new)
  - core/learning/event_schema.py (extension)
  - core/orchestration/subsystems/tool_forge_subsystem.py (integration)
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md (update Gap 1)
  - tests/learning/test_tool_execution.py (new)

Blockers: None
Unblocks: Gaps 2–7 (all depend on this data)

Links:
  ADR: ADR-0321 (Tool Execution Learning Events)
  Design: docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md#gap-1
  Code Review: docs/CODE_REVIEW_INTEGRATION_GAPS.md#gap-1-findings
```

---

## TICKET 2: Gap 2 — Tool Performance Ranking and Reuse

```
Title: [Learning] Gap 2 — Tool Performance Ranking & Reuse Selection (ADR-0322)
Story Points: 6
Sprint: Sprint 2 (Weeks 3–4, Days 8–13)
Epic: Learning Integration v0.2.1
Assignee: Eng C (Tool Forge Team)
Depends On: Ticket 1 (Gap 1)
Status: READY FOR DEV (after Gap 1 complete)
Priority: HIGH (core ROI)

Description:
Implement tool ranking system. Query TOOL_EXECUTED events (Gap 1), aggregate 
success rates, compute composite score, rank tools by reuse potential. Integrate 
into ToolForgeSubsystem to make reuse/generate decisions data-driven.

Acceptance Criteria:
  AC1: ToolPerformanceMetrics dataclass + aggregation
  AC2: RankedTool dataclass + ranking list
  AC3: ToolRankingManager subsystem (queries EventStore, ranks, caches)
  AC4: Scoring formula (7-factor: success, latency, cost, trend, cold-start)
  AC5: Reuse decision: score > 0.7 → reuse; else generate
  AC6: Cache with TTL (5-min, cache key = f"{tenant}:{task_type}:{error}")
  AC7: Pagination (limit=10000 prevents memory exhaustion)
  AC8: Audit trail per ranking decision (tool.ranking_computed event)
  AC9: Feature flag learning_gap_2_tool_ranking (default: false)
  AC10: All code review findings addressed

Definition of Done:
  [ ] ToolPerformanceMetrics dataclass (success count, latency P50/P95/P99, cost)
  [ ] RankedTool dataclass (score 0.0–1.0, reason, metrics)
  [ ] ToolRankingManager class (aggregate, score, cache, query)
  [ ] Scoring formula: base(0.5) + success(±0.3) + latency(±0.2) + cost(±0.2) + trend(±0.1) - cold_start(0.2)
  [ ] _score_tool() implementation (all 7 factors)
  [ ] Cache with TTL check (datetime.utcnow() > expiry → recompute)
  [ ] Pagination in EventStore.query_events() (limit=10000)
  [ ] Audit backend integration (write_event on ranking computed)
  [ ] ToolForgeSubsystem._handle_select_tool() integration
  [ ] 12 unit tests (ranking, filtering, cold-start, cost-aware, caching)
  [ ] 4 integration tests (decision rule, fallback, audit trail)
  [ ] Code review ✅ and merge to main
  [ ] ADR-0322 status changed to ACCEPTED

Implementation Notes:
  • Scoring rationale documented (success > latency > cost)
  • Cold-start penalty (-0.2) conservative; discourages but doesn't block
  • Trend: success_trend = recent_rate - overall_rate (not true time-series yet)
  • Threshold tunable: score > 0.7 (can raise to 0.75 if too aggressive)
  • Percentile-based (robust to outliers): P50, P95, P99 for latency/cost
  • Tenant isolation: cache key includes tenant_id; query filters by tenant_id
  • Performance: query latency target <100ms (p95)

Key Files:
  - core/learning/tool_ranking.py (new)
  - core/learning/tool_performance.py (new)
  - core/orchestration/subsystems/tool_forge_subsystem.py (integration)
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md (update Gap 2)
  - tests/learning/test_tool_ranking.py (new)

Blockers: Ticket 1 (Gap 1) must be complete
Unblocks: Ticket 4 (Gap 4 provides aggregated metrics)

Links:
  ADR: ADR-0322 (Tool Performance Ranking)
  Design: docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md#gap-2
  Scoring Justification: ADR-0322 § Scoring Formula Justification
  Code Review: docs/CODE_REVIEW_INTEGRATION_GAPS.md#gap-2-findings
```

---

## TICKET 3: Gap 3 — Skill Attribution Model

```
Title: [Learning] Gap 3 — Fair Skill Attribution & Grading (ADR-0323)
Story Points: 6
Sprint: Sprint 2 (Weeks 3–4, Days 14–19)
Epic: Learning Integration v0.2.1
Assignee: Eng D (Skill Forge Team)
Depends On: Ticket 1 (Gap 1), Ticket 4 (Gap 4 for metrics)
Status: READY FOR DEV (after Gap 4)
Priority: MEDIUM (fair grading)

Description:
Implement skill attribution engine. When a strategy succeeds/fails, attribute 
outcomes fairly to individual skills. Default: EQUAL (each skill = 1/N credit). 
Future: WEIGHTED (by success rate). Wire into SkillForgeSubsystem for grading.

Acceptance Criteria:
  AC1: AttributionModel enum (EQUAL, WEIGHTED, FIRST, LAST)
  AC2: SkillAttributionEngine class (4 models, async)
  AC3: EQUAL model default + fully implemented (MVP)
  AC4: WEIGHTED model defined but stubbed (future Gap 4)
  AC5: SkillAttributionResult dataclass (credit 0.0–1.0, reasoning)
  AC6: Event subscription wired (strategy.outcome → on_strategy_outcome)
  AC7: _grade_skill() full implementation (KEY FIX: updates registry)
  AC8: Audit trail per attribution (skill.attribution event)
  AC9: Single-skill strategy handled (credit=1.0)
  AC10: Feature flag learning_gap_3_skill_attribution (default: false)
  AC11: All code review findings addressed

Definition of Done:
  [ ] AttributionModel enum (EQUAL, WEIGHTED, FIRST, LAST)
  [ ] SkillAttributionEngine class (base + 4 model implementations)
  [ ] _attribute_equal() method (1/N credit split)
  [ ] _attribute_first() + _attribute_last() (reference only; not recommended)
  [ ] _attribute_weighted() stubbed with comment "see Gap 4"
  [ ] SkillAttributionResult frozen dataclass
  [ ] SkillForgeSubsystem.startup() → hub.subscribe("strategy.outcome", ...)
  [ ] on_strategy_outcome() handler (KEY FIX: full implementation, not stub)
  [ ] _grade_skill() implementation (call registry.update_skill(), log, audit)
  [ ] Audit backend integration (skill.attribution events)
  [ ] 10 unit tests (all models, single skill, empty list, edge cases)
  [ ] 5 integration tests (event handling, grading, audit trail)
  [ ] Code review ✅ and merge to main
  [ ] ADR-0323 status changed to ACCEPTED

Implementation Notes:
  • EQUAL model: credit_per_skill = 1.0 / len(skill_ids)
  • Success outcome: full credit; failure outcome: -credit * 0.5
  • WEIGHTED requires Gap 4 metrics (success rates per skill)
  • Event handler must be subscribed (KEY FIX: was missing in original stub)
  • Grade update: new_score = skill.score + score_delta
  • Audit trail critical (GDPR Art. 30): log strategy_id, skill_id, credit, model, outcome
  • Tenant isolation: query filters by tenant_id; audit events include tenant_id

Key Files:
  - core/learning/skill_attribution.py (new)
  - core/orchestration/subsystems/skill_forge_subsystem.py (integration)
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md (update Gap 3)
  - tests/learning/test_skill_attribution.py (new)

Blockers: Ticket 1 (Gap 1), Ticket 4 (Gap 4 for metrics)
Unblocks: Ticket 7 (Gap 7 integrates feedback into grading)

Links:
  ADR: ADR-0323 (Skill Attribution Model)
  Design: docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md#gap-3
  Code Review: docs/CODE_REVIEW_INTEGRATION_GAPS.md#gap-3-findings
```

---

## TICKET 4: Gap 4 — Performance Aggregation Pipeline

```
Title: [Learning] Gap 4 — Performance Aggregation & Confidence Intervals (ADR-0324)
Story Points: 5
Sprint: Sprint 2 (Weeks 3–4, Days 8–13, parallel with Gap 2)
Epic: Learning Integration v0.2.1
Assignee: Eng B (Learning Team)
Depends On: Ticket 1 (Gap 1)
Status: READY FOR DEV (after Gap 1)
Priority: CRITICAL (feeds Gaps 2, 3, 5, 6)

Description:
Implement background aggregation pipeline. Batch query TOOL_EXECUTED and SKILL_USED 
events hourly. Compute success rates, latency/cost percentiles, confidence intervals 
(Bayesian Beta-Binomial). Cache results with 60-min TTL. Queries (Gap 2, 3) use 
cached metrics instead of expensive O(n) aggregations.

Acceptance Criteria:
  AC1: ConfidenceIntervalCalculator (Bayesian Beta-Binomial)
  AC2: PerformanceAggregator (batch queries, aggregation)
  AC3: AggregationScheduler (hourly background job)
  AC4: MetricsCache with TTL (60-min default, configurable)
  AC5: Aggregation for tools (success count, latency P50/P95/P99, cost)
  AC6: Aggregation for skills (success rate, outcome history)
  AC7: Temporal windows (7-day, 30-day, all-time)
  AC8: Pagination (limit=100000 prevents memory exhaustion)
  AC9: PERFORMANCE_METRICS_COMPUTED events emitted hourly
  AC10: Feature flag learning_gap_4_aggregation (default: false)
  AC11: All code review findings addressed

Definition of Done:
  [ ] ConfidenceInterval frozen dataclass (lower, mean, upper, samples)
  [ ] ConfidenceIntervalCalculator.compute_interval() (Beta-Binomial)
  [ ] PerformanceAggregator class (event_store, cache, aggregation)
  [ ] aggregate_tool_metrics() method (groups by tool_id, computes metrics)
  [ ] aggregate_skill_metrics() method (groups by skill_id)
  [ ] _percentile() helper (P50, P95, P99 computation)
  [ ] _match_tool_filter() time window + attribute filtering
  [ ] AggregationScheduler class (interval_minutes=60)
  [ ] start() coroutine (background loop)
  [ ] run_aggregation() method (7-day + 30-day windows)
  [ ] Cache TTL check (datetime.utcnow() > last_aggregation + TTL)
  [ ] MetricsCache with automatic invalidation
  [ ] Monitoring: aggregation latency + event count metrics
  [ ] 15 unit tests (intervals, aggregation, percentiles, edge cases)
  [ ] Integration: scheduler wired into SubsystemHub
  [ ] Code review ✅ and merge to main
  [ ] ADR-0324 status changed to ACCEPTED

Implementation Notes:
  • Bayesian prior: Beta(2, 2) regularizes cold-start (few samples)
  • Confidence interval: 95% credible level (α=0.05)
  • Cold-start smoothing: Posterior = Beta(successes + prior, failures + prior)
  • Cache key: f"{tenant_id}:{metric_type}:{time_window}" (e.g., "acme:tool:7d")
  • Aggregation time target: <30 min for 100K events
  • Performance: cache hit rate >95% (repeated queries)
  • Eventual consistency model: 1-hour staleness acceptable for learning
  • Tenant isolation: all queries filter by tenant_id

Key Files:
  - core/learning/performance_aggregator.py (new)
  - core/learning/confidence_intervals.py (new)
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md (update Gap 4)
  - tests/learning/test_performance_aggregator.py (new)

Blockers: Ticket 1 (Gap 1)
Unblocks: Tickets 2, 3, 5, 6 (all use aggregated metrics)

Links:
  ADR: ADR-0324 (Performance Aggregation Pipeline)
  Design: docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md#gap-4
  Bayesian Smoothing: https://en.wikipedia.org/wiki/Beta-binomial_distribution
  Code Review: docs/CODE_REVIEW_INTEGRATION_GAPS.md#gap-4-findings
```

---

## TICKET 5: Gap 5 — Context Coherence Across Sessions

```
Title: [Learning] Gap 5 — Context Coherence & Cross-Session Learning (ADR-0325)
Story Points: 5
Sprint: Sprint 3 (Weeks 5–6, Days 22–27, parallel with Gap 6)
Epic: Learning Integration v0.2.1
Assignee: Eng E (Brain/Orchestration Team)
Depends On: Ticket 1 (Gap 1), Ticket 2 (Gap 2)
Status: READY FOR DEV (after Phase 2)
Priority: LOW (nice-to-have optimization, post-MVP)

Description:
Implement context coherence manager. Find parent sessions with similar task_type. 
Inherit top tools/skills from parent. Blend parent recommendations with current 
ranking (parent_weight=0.3, conservative). Carry learning across sessions.

Acceptance Criteria:
  AC1: SessionContext dataclass (parent_session_id, inherited tools/skills)
  AC2: ContextCoherenceManager class (find parent, inherit, blend)
  AC3: find_parent_session() (matches task_type, max_age_hours=24)
  AC4: get_inherited_tools/skills() (top-N from parent)
  AC5: blend_tool_rankings() (parent boost +0.2 to score)
  AC6: Age filtering (parent >24h deprioritized)
  AC7: Conflict resolution (score-weighted blend if conflict)
  AC8: Integration with ToolRankingManager (optional boost)
  AC9: Feature flag learning_gap_5_context_coherence (default: false)
  AC10: All code review findings addressed

Definition of Done:
  [ ] SessionContext frozen dataclass
  [ ] ContextCoherenceManager class
  [ ] find_parent_session() implementation
  [ ] get_inherited_tools() implementation
  [ ] get_inherited_skills() implementation
  [ ] blend_tool_rankings() implementation (parent_weight=0.3)
  [ ] Age filtering (max_age_hours=24)
  [ ] 8 unit tests (parent finding, blending, age filtering)
  [ ] Integration: ToolRankingManager accepts blended rankings
  [ ] Code review ✅ and merge to main
  [ ] ADR-0325 status changed to ACCEPTED

Implementation Notes:
  • Parent matching: exact task_type, most recent, <24 hours old
  • Inheritance: propagate tool_id list from parent ranking
  • Blending formula: if tool_id in inherited → score += (1 - parent_weight) * 0.2
  • parent_weight=0.3 means parent contributes 30% boost (conservative)
  • Conflict: if parent recommends tool X but current data suggests Y, both compete
  • Tunable: parent_weight parameter (can adjust per operator)
  • Tenant isolation: query filters by tenant_id

Key Files:
  - core/learning/context_coherence.py (new)
  - core/orchestration/subsystems/context_bridge.py (integration)
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md (update Gap 5)
  - tests/learning/test_context_coherence.py (new)

Blockers: Ticket 1 (Gap 1), Ticket 2 (Gap 2)
Unblocks: (none; end of optimization chain)

Links:
  ADR: ADR-0325 (Context Coherence)
  Design: docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md#gap-5
  Code Review: docs/CODE_REVIEW_INTEGRATION_GAPS.md#gap-5-findings
```

---

## TICKET 6: Gap 6 — Cost Learning & Budget Refinement

```
Title: [Learning] Gap 6 — Cost Learning & EMA Multiplier Updates (ADR-0326)
Story Points: 5
Sprint: Sprint 3 (Weeks 5–6, Days 22–27, parallel with Gap 5)
Epic: Learning Integration v0.2.1
Assignee: Eng F (Brain/Orchestration Team)
Depends On: Ticket 1 (Gap 1), Ticket 4 (Gap 4)
Status: READY FOR DEV (after Phase 2)
Priority: LOW (optimization, post-MVP)

Description:
Implement cost learner. Observe (estimated, actual) cost pairs per tool + model. 
Compute multiplier deltas. Apply exponential moving average (EMA, α=0.1) to 
refine multipliers. Update CostController to use learned estimates.

Acceptance Criteria:
  AC1: CostLearnerMetrics dataclass (estimated, actual, multiplier, samples)
  AC2: CostLearner class (observe, aggregate, learn)
  AC3: observe_execution() (EMA update on each tool run)
  AC4: get_cost_estimate() (returns corrected estimate)
  AC5: aggregate_multipliers() (per tool + model)
  AC6: EMA learning rate tunable (default α=0.1)
  AC7: Outlier detection (actual > 2x estimated flagged)
  AC8: Median-based aggregation (robust to outliers)
  AC9: Per-tool aggregation (fine-grained tracking)
  AC10: Integration with CostController (observe on execution)
  AC11: Feature flag learning_gap_6_cost_learning (default: false)
  AC12: All code review findings addressed

Definition of Done:
  [ ] CostLearnerMetrics frozen dataclass
  [ ] CostLearner class (event_store, multipliers dict)
  [ ] observe_execution() (EMA α=0.1 update)
  [ ] get_cost_estimate() (returns base * multiplier)
  [ ] aggregate_multipliers() (query TOOL_EXECUTED, group by tool+model)
  [ ] Median computation (_percentile helper)
  [ ] Outlier flagging (2x threshold) + logging
  [ ] CostController integration (observe() call after each execution)
  [ ] 10 unit tests (EMA updates, aggregation, outliers, cold-start)
  [ ] Integration: multipliers flow through cost prediction
  [ ] Code review ✅ and merge to main
  [ ] ADR-0326 status changed to ACCEPTED

Implementation Notes:
  • EMA formula: new_multiplier = 0.9 * current + 0.1 * actual
  • Learning rate α=0.1 is conservative (slow adaptation)
  • Can be tuned: α=0.2 for faster, α=0.05 for slower
  • Outlier handling: flag (log) but don't apply to multiplier
  • Recommendation: reset multipliers on model pricing change
  • Granularity: per (tool_id, model_id) pair
  • Future: per (tool_id, model_id, task_type) for finer control

Key Files:
  - core/learning/cost_learner.py (new)
  - core/orchestration/subsystems/cost_controller.py (integration)
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md (update Gap 6)
  - tests/learning/test_cost_learner.py (new)

Blockers: Ticket 1 (Gap 1), Ticket 4 (Gap 4)
Unblocks: (none; end of optimization chain)

Links:
  ADR: ADR-0326 (Cost Learning)
  Design: docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md#gap-6
  EMA Tutorial: https://en.wikipedia.org/wiki/Exponential_moving_average
  Code Review: docs/CODE_REVIEW_INTEGRATION_GAPS.md#gap-6-findings
```

---

## TICKET 7: Gap 7 — Operator Feedback Loop Integration

```
Title: [Learning] Gap 7 — Operator Feedback & Auto-Grade Adjustment (ADR-0327)
Story Points: 5
Sprint: Sprint 4 (Weeks 7–8, Days 32–36)
Epic: Learning Integration v0.2.1
Assignee: Eng D (Skill Forge Team)
Depends On: Ticket 1 (Gap 1), Ticket 3 (Gap 3)
Status: READY FOR DEV (after Phase 3)
Priority: MEDIUM (feedback loop closure, deferred UI)

Description:
Implement operator feedback handler. Collect 1–5 star ratings on tools/skills. 
Emit OPERATOR_RATED_TOOL/SKILL events. Wire FeedbackAdjuster to adjust skill 
grades based on ratings (+0.5 for 5-star, -0.5 for 1-star). Integrate Console 
API endpoints.

Acceptance Criteria:
  AC1: OperatorFeedbackHandler class (rate_tool, rate_skill methods)
  AC2: FeedbackAdjuster class (adjusts grades on feedback)
  AC3: Rating mapping (5→+0.5, 4→+0.25, 3→0.0, 2→-0.25, 1→-0.5)
  AC4: Event emission (OPERATOR_RATED_TOOL, OPERATOR_RATED_SKILL)
  AC5: Console API endpoints (/api/feedback/rate-tool, rate-skill)
  AC6: Event handler subscription (OPERATOR_RATED_SKILL → on_operator_rated_skill)
  AC7: Skill grade adjustment async (processed within 5 min)
  AC8: Audit trail per rating (operator_rated event)
  AC9: Sample size threshold (ignore if <10 total grades, future work)
  AC10: Feature flag learning_gap_7_operator_feedback (default: false)
  AC11: All code review findings addressed

Definition of Done:
  [ ] OperatorFeedbackHandler class
  [ ] rate_tool(tool_id, rating 1-5, feedback optional) method
  [ ] rate_skill(skill_id, rating 1-5, feedback optional) method
  [ ] FeedbackAdjuster class
  [ ] Rating-to-grade-delta mapping (frozen dict)
  [ ] on_operator_rated_tool() handler (logs feedback)
  [ ] on_operator_rated_skill() handler (adjusts skill grade)
  [ ] Console API POST /api/feedback/rate-tool
  [ ] Console API POST /api/feedback/rate-skill
  [ ] Event handler subscription wiring
  [ ] Audit backend integration (operator_feedback events)
  [ ] 10 unit tests (feedback emission, grade adjustment, mapping)
  [ ] 2 E2E tests (rate skill → grade adjusts, rate tool → logged)
  [ ] Code review ✅ and merge to main
  [ ] ADR-0327 status changed to ACCEPTED

Implementation Notes:
  • Rating range: 1–5 (validated in handler)
  • Grade adjustment: asymmetric (success +0.5, failure -0.25)
  • Failure outcome: -0.5 for 1-star, -0.25 for 2-star (half penalty)
  • Feedback optional but recommended
  • Processing: async, non-blocking (skill grade adjusted within 5 min)
  • Sample threshold: (future work) ignore feedback if <10 total grades
  • UI: deferred to Gap 7b (feedback modal/form in Console)
  • Audit trail: operator_feedback event includes rating, feedback, skill_id

Key Files:
  - core/learning/operator_feedback.py (new)
  - core/console/routes/feedback.py (new)
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md (update Gap 7)
  - tests/learning/test_operator_feedback.py (new)

Blockers: Ticket 1 (Gap 1), Ticket 3 (Gap 3)
Unblocks: (none; final component of feedback loop)

Deferred:
  [ ] UI components (star rating, feedback form) — Gap 7b
  [ ] Bridge integration (Slack/Discord rating prompts) — Gap 7c
  [ ] Feedback moderation dashboard — Gap 7d
  [ ] Feedback analytics — Gap 7e

Links:
  ADR: ADR-0327 (Operator Feedback Loop)
  Design: docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md#gap-7
  Code Review: docs/CODE_REVIEW_INTEGRATION_GAPS.md#gap-7-findings
```

---

## Testing Tickets (Cross-Gap E2E)

### TICKET 8: E2E System Test — Full Learning Loop

```
Title: [Testing] E2E System Test — Full Learning Loop (Gap 1–7)
Story Points: 5
Sprint: Sprint 4 (Weeks 7–8, Days 37–39)
Epic: Learning Integration v0.2.1
Assignee: Eng G (QA Team)
Depends On: All Tickets 1–7
Status: READY FOR DEV (after all gaps implemented)
Priority: HIGH (system validation)

Description:
End-to-end test: Tool execution (Gap 1) → ranking (Gap 2) → skill attribution 
(Gap 3) → aggregation (Gap 4) → context coherence (Gap 5) → cost learning (Gap 6) 
→ operator feedback (Gap 7). Verify complete data flow.

Test Scenarios:
  1. Single-session loop: Execute tool → detect success → rank → select on next call
  2. Multi-session coherence: Session 1 ranks tool X → Session 2 inherits boost
  3. Cost convergence: Execute tool 20x → cost multiplier converges to actual
  4. Skill grading: Strategy with 2 skills succeeds → each graded fairly (0.5 credit)
  5. Operator feedback: Operator rates skill 5 stars → grade adjusts +0.5
  6. Tenant isolation: Tenant A's events don't appear in Tenant B queries

Definition of Done:
  [ ] test_e2e_tool_execution_to_ranking() — tool exec → ranked → reused
  [ ] test_e2e_multi_session_coherence() — parent context inherited
  [ ] test_e2e_cost_multiplier_convergence() — 20 executions, multiplier within ±10%
  [ ] test_e2e_skill_attribution_fair() — 2-skill strategy, equal credit (0.5 each)
  [ ] test_e2e_operator_feedback_to_grade() — rating 5 stars → +0.5 adjustment
  [ ] test_e2e_tenant_isolation() — cross-tenant queries fail
  [ ] Stress test: 1000 events/min through EventStore
  [ ] Latency benchmark: Each component <100ms p95
  [ ] All tests passing + code reviewed

Key Files:
  - tests/learning/test_e2e_full_loop.py (new)
  - tests/learning/conftest.py (shared fixtures)
```

---

## Rollout Tickets (Phase 5)

### TICKET 9: Feature Flag Staging — Canary Release

```
Title: [Release] Feature Flag Staging — Canary (10% Tenants)
Story Points: 3
Sprint: Sprint 5 (Weeks 9–10, Days 40–44)
Epic: Learning Integration v0.2.1
Assignee: Ops Lead
Depends On: All Tickets 1–8 complete
Status: READY FOR DEV (after Phase 4)
Priority: HIGH (prerequisite for full release)

Description:
Deploy learning system to 10% of internal tenants. All feature flags ON. 
Monitor metrics baseline: event latency, error rates, tool reuse %, cost accuracy.

Acceptance Criteria:
  AC1: Feature flags live in tenant.corvin.yaml (all 7 gaps toggleable)
  AC2: 10% of internal tenants have all 7 flags enabled
  AC3: Monitoring dashboard deployed (Prometheus + Grafana)
  AC4: Event latency p99 <100ms (baseline)
  AC5: Event drop rate <0.1%
  AC6: Tool reuse 20%+ (success rate metric)
  AC7: No tenant isolation breaches
  AC8: Audit trail 100% complete (hash-chain verification)
  AC9: 48-hour canary stable (no new errors after hour 6)
  AC10: Go/No-Go decision documented

Definition of Done:
  [ ] All 7 feature flags wired to tenant.corvin.yaml
  [ ] Canary deployment to 10% (select internal tenants)
  [ ] Monitoring dashboard live (latency, drops, metrics)
  [ ] Baseline metrics recorded (day 1, hour 6, hour 24)
  [ ] Alerting configured (page on drop rate >1%, latency >200ms)
  [ ] Support team briefed
  [ ] Go/No-Go decision (continue to beta or pause for fixes)
```

---

### TICKET 10: Feature Flag Staging — Beta Release

```
Title: [Release] Feature Flag Staging — Beta (50% Opt-In)
Story Points: 2
Sprint: Sprint 5 (Weeks 9–10, Days 44–48)
Epic: Learning Integration v0.2.1
Assignee: Ops Lead
Depends On: Ticket 9 (Canary green)
Status: READY FOR DEV (after canary go/no-go)
Priority: HIGH

Description:
Deploy to 50% of customer tenants (opt-in early adopters). Continue monitoring. 
Measure tool reuse convergence, cost accuracy, skill promotion speed.

Acceptance Criteria:
  AC1: 50% of customer tenants offered opt-in
  AC2: Tool reuse 40%+ (double baseline)
  AC3: Cost accuracy ±10% of forecasted budget
  AC4: Skill promotion rate 10%+ reaching threshold
  AC5: No regressions from canary metrics
  AC6: User feedback collected (1-week survey)
  AC7: Go/No-Go decision documented

Definition of Done:
  [ ] Beta deployment to 50% (opt-in messaging)
  [ ] Metrics dashboard updated with 50% cohort
  [ ] Weekly status (tool reuse %, cost accuracy, skill promotions)
  [ ] User feedback survey (NPS, feature requests)
  [ ] Support ticket analysis (any systemic issues?)
  [ ] Go/No-Go decision (full rollout or pause)
```

---

### TICKET 11: Feature Flag Rollout — General Availability

```
Title: [Release] Feature Flag Rollout — GA (100%, Opt-Out Available)
Story Points: 2
Sprint: Sprint 5 (Weeks 9–10, Days 48–50)
Epic: Learning Integration v0.2.1
Assignee: Ops Lead
Depends On: Ticket 10 (Beta green)
Status: READY FOR DEV (after beta go/no-go)
Priority: HIGH

Description:
Release learning system to all users. All 7 feature flags enabled by default 
(disableable per tenant). Monitoring continues. Prepare v0.2.1 release notes.

Acceptance Criteria:
  AC1: 100% of tenants have access (all flags default ON)
  AC2: Each tenant can disable flags (spec.features.learning_gap_* = false)
  AC3: Monitoring dashboard full coverage
  AC4: Operator education complete (webinar, docs, support training)
  AC5: v0.2.1 release notes published
  AC6: No new critical issues (48-hour post-launch observation)
  AC7: Full release announced (changelog, blog post optional)

Definition of Done:
  [ ] All 7 feature flags default-true in new installs
  [ ] Upgrade path tested (existing installs keep false, operator opt-in)
  [ ] Operator education complete (webinar recording, docs, FAQ)
  [ ] Release notes published (changelog, highlights, links)
  [ ] Support team staffed (escalation path for issues)
  [ ] Monitoring 24/7 (on-call rotation established)
  [ ] Post-launch metrics (48h): all green
```

---

## Documentation Tickets

### TICKET 12: Documentation — Integration Design Updates

```
Title: [Docs] Update Integration Design Docs (All Gaps)
Story Points: 3
Sprint: Ongoing (throughout Phases 1–5)
Epic: Learning Integration v0.2.1
Assignee: Eng Lead
Depends On: Code implementation per gap
Status: READY FOR DEV

Description:
Update docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md with 
implementation details and code review findings for each gap.

Deliverables:
  [ ] Gap 1 section: telemetry schema, event structure, PII sanitization
  [ ] Gap 2 section: ranking formula, scoring weights, cache architecture
  [ ] Gap 3 section: attribution models, grading logic, event subscription
  [ ] Gap 4 section: aggregation pipeline, Bayesian confidence intervals
  [ ] Gap 5 section: context coherence, parent matching, blending
  [ ] Gap 6 section: cost learning, EMA formula, multiplier updates
  [ ] Gap 7 section: feedback handler, API endpoints, grade adjustment
  [ ] Data flow diagrams (Mermaid or SVG)
  [ ] API reference (all new endpoints)
```

---

### TICKET 13: Documentation — Operator Guides

```
Title: [Docs] Create Operator Guides (Features & Learning)
Story Points: 2
Sprint: Phase 5 (weeks 9–10)
Epic: Learning Integration v0.2.1
Assignee: Eng A
Depends On: All gaps complete
Status: READY FOR DEV

Description:
Create operator-friendly guides explaining learning features.

Deliverables:
  [ ] "Understanding Tool Ranking" — how tools are ranked, enabling reuse
  [ ] "Understanding Skill Attribution" — how skills get graded fairly
  [ ] "Understanding Cost Learning" — how budget estimates improve
  [ ] "Rating Tools & Skills" — how to provide feedback (once UI available)
  [ ] "FAQs: Learning System" — common questions, troubleshooting
  [ ] Release notes v0.2.1 — feature summary, links to docs
```

---

## Summary Table

| Ticket | Gap | Title | SP | Owner | Status |
|--------|-----|-------|----|----|--------|
| 1 | 1 | Tool Execution Telemetry | 8 | Eng A | READY |
| 2 | 2 | Tool Performance Ranking | 6 | Eng C | READY |
| 3 | 3 | Skill Attribution | 6 | Eng D | READY |
| 4 | 4 | Performance Aggregation | 5 | Eng B | READY |
| 5 | 5 | Context Coherence | 5 | Eng E | READY |
| 6 | 6 | Cost Learning | 5 | Eng F | READY |
| 7 | 7 | Operator Feedback | 5 | Eng D | READY |
| 8 | E2E | Full Learning Loop Test | 5 | Eng G | READY |
| 9 | Ops | Canary Deployment | 3 | Ops | READY |
| 10 | Ops | Beta Deployment | 2 | Ops | READY |
| 11 | Ops | GA Release | 2 | Ops | READY |
| 12 | Docs | Integration Design Updates | 3 | Lead | READY |
| 13 | Docs | Operator Guides | 2 | Eng A | READY |
| **Total** | | | **57 SP** | | |

---

## Import Instructions

1. **Create Epic** (JIRA):
   ```
   Project: CorvinOS
   Epic Name: Learning Integration v0.2.1
   Description: See EPIC section above
   ```

2. **Create Tickets** (order matters):
   - Create all 13 tickets using fields above
   - Set dependencies (Ticket N Depends On Ticket M)
   - Assign to team members

3. **Create Sprint(s)**:
   - Sprint 1 (Weeks 1–2): Ticket 1
   - Sprint 2 (Weeks 3–4): Tickets 2, 3, 4
   - Sprint 3 (Weeks 5–6): Tickets 5, 6
   - Sprint 4 (Weeks 7–8): Tickets 7, 8
   - Sprint 5 (Weeks 9–10): Tickets 9, 10, 11, 12, 13

4. **Set Labels**:
   - `learning` (all)
   - `architecture` (all)
   - `adr-0321` through `adr-0327` (respective tickets)
   - `testing` (Tickets 8–11)
   - `documentation` (Tickets 12–13)

5. **Configure Alerts**:
   - Ticket cycle time target: 5–7 days
   - Blocked alert if dependencies not met
   - Escalation if ticket age > sprint length

---

**Prepared by:** Claude Code  
**Date:** 2026-08-19  
**Status:** READY FOR IMPORT  
**JIRA Project Key:** CORVIN  
**Epic Key:** CORVIN-TBD (assigned on creation)
