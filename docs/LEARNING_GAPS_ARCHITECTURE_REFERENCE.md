# Learning Gaps Architecture Reference

**Date:** 2026-08-19  
**Status:** v0.2-rc1 (Phase 6 Complete)  
**Scope:** All 7 learning integration gaps (ADR-0321-0327)

---

## System Overview

The learning system consists of 7 interdependent gaps that form a complete closed-loop learning pipeline:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Learning Loop Architecture                      │
└─────────────────────────────────────────────────────────────────────┘

Tool Execution              Aggregation              Ranking
    (Gap 1)                   (Gap 4)                (Gap 2)
      │                         │                      │
      ├─────────────────────────┼──────────────────────┤
      │                         │                      │
      v                         v                      v
   Events ───────────────> Metrics ────────────> Ranked Tools
                               │                      │
                               │                      │
                            Attribution ◄────────────┤
                             (Gap 3)    Feedback
                               │        (Gap 7)
                               │          │
                               v          v
                          Skill Scores  Operator Rating
                               │          │
                               ├──────────┤
                               │          │
                               v          v
                         Cost Learning   Context Coherence
                           (Gap 6)           (Gap 5)
                               │              │
                               └──────┬───────┘
                                      │
                                      v
                          ┌─────────────────────┐
                          │  Next Session Input │
                          └─────────────────────┘
```

---

## Gap Architecture Details

### Gap 1: Tool Execution Telemetry (ADR-0321)

**Purpose:** Capture execution metrics for every tool call

**Flow:**
```
Tool Execution
    │
    v
ToolExecutionTelemetry (frozen dataclass)
    │ (validates, sanitizes PII)
    v
LearningEvent (TOOL_EXECUTED)
    │
    v
EventEmitter → EventStore
    │
    v
Audit Trail (hash-chained)
```

**Data Model:**
```python
ToolExecutionTelemetry:
  - tenant_id, session_id, task_id, task_type
  - tool_id, tool_name, model_id
  - status (success/failure)
  - duration_ms, estimated_cost_cents
  - tokens_in, tokens_out
  - error_type, error_message (sanitized)
```

**Integration Points:**
- Input: ToolForgeSubsystem._forge_exec()
- Output: EventStore.TOOL_EXECUTED events
- Gating: `learning.gap_1_tool_execution_telemetry` flag

---

### Gap 2: Tool Ranking & Reuse Decision (ADR-0322)

**Purpose:** Rank tools by historical performance, decide whether to reuse or generate new

**Flow:**
```
Gap 4 Metrics ──────────┐
                        v
RankedTool Dataclass ◄──┤
  (immutable)           │
    │                   │
    v                   │
Scoring Formula ────────┤
  (0.3·success +        │
   0.2·latency +        │
   0.2·cost +           │
   0.1·trend -          │
   0.2·cold_start)      │
    │                   │
    v                   │
Cache (5-min TTL)       │
    │                   │
    v                   │
Tool Reuse Decision ◄───┘
  (> threshold: reuse)
  (< threshold: generate)
```

**Data Model:**
```python
RankedTool:
  - tool_id, score (0.0-1.0)
  - success_rate, total_count
  - avg_latency_ms, p95_latency_ms
  - avg_cost_cents
  - confidence (Bayesian)
  - trend (improving/stable/degrading)
  - is_cold_start (< 10 samples)
  - rank (1 = best)
```

**Integration Points:**
- Input: Gap 4 metrics, Task context
- Output: RankedTool list, Reuse decision
- Gating: `learning.gap_2_tool_ranking` flag

---

### Gap 3: Skill Attribution (ADR-0323)

**Purpose:** Fairly distribute credit between skills used in a multi-skill strategy

**Flow:**
```
Strategy Outcome
    │ (success/failure)
    v
SkillAttributionManager
    │
    ├─ EQUAL model:
    │  └─ Each skill: 50% credit
    │
    ├─ WEIGHTED model:
    │  └─ Each skill: credit ∝ execution_time
    │
    ├─ FIRST model:
    │  └─ First skill: 100% credit
    │
    └─ LAST model:
       └─ Last skill: 100% credit
    │
    v
SkillScore (per skill)
    │
    v
Skill Promotion/Demotion
```

**Data Model:**
```python
AttributionResult:
  - strategy_id
  - skills_used: [skill_name, ...]
  - attribution_model (EQUAL|WEIGHTED|FIRST|LAST)
  - scores: {skill_name: float (0.0-1.0)}
  - outcome (success/failure)
  - audit_entry (hash-chained)
```

**Integration Points:**
- Input: Gap 4 metrics (for WEIGHTED), Strategy outcome
- Output: Skill scores, Attribution events
- Gating: `learning.gap_3_skill_attribution` flag

---

### Gap 4: Performance Aggregation Pipeline (ADR-0324)

**Purpose:** Compute aggregated metrics from tool execution events (hourly job)

**Flow:**
```
EventStore
(TOOL_EXECUTED events, 7-day window)
    │
    v
Aggregator (hourly cron)
    │
    ├─ Group by tool_id
    │  └─ [30 events for tool_A, ...]
    │
    ├─ Compute per-tool metrics:
    │  ├─ Success rate (# successes / # total)
    │  ├─ Latency percentiles (p50, p95, p99)
    │  ├─ Cost average
    │  ├─ Bayesian confidence (min(1.0, N/30))
    │  └─ Trend (recent success - overall success)
    │
    v
ToolPerformanceMetrics (frozen)
    │
    v
Cache (5-min TTL, LRU eviction)
    │
    v
METRIC_AGGREGATED events (for observability)
```

**Data Model:**
```python
ToolPerformanceMetrics:
  - tool_id, success_rate, success_count, total_count
  - avg_latency_ms, p50_latency_ms, p95_latency_ms, p99_latency_ms
  - avg_cost_cents, cost_samples
  - confidence (0.0-1.0, Bayesian)
  - trend (improving|stable|degrading)
  - days_since_first_sample
  - last_updated_utc, tenant_id
```

**Integration Points:**
- Input: EventStore.TOOL_EXECUTED events
- Output: ToolPerformanceMetrics, Cache
- Called by: Gap 2 (ranking), Gap 3 (attribution)
- Gating: `learning.gap_4_performance_aggregation` flag
- Cron: Hourly job (configurable)

---

### Gap 5: Context Coherence (ADR-0325)

**Purpose:** Preserve learned strategies and preferences across sessions

**Flow:**
```
Session 1
  │ (learn: tool_X works for error_Y)
  v
SessionCheckpoint
  ├─ coherence_chain (hash-chained)
  ├─ strategy_history (tools used, outcomes)
  ├─ learned_preferences (error → tool mapping)
  └─ cost_deltas (cost multiplier updates)
    │
    v
Session 2 (resume)
  │
  v
ContextCoherenceManager.inherit_parent_context()
  │
  ├─ Validate: checkpoint age ≤ 24 hours
  ├─ Resolve: conflicts (parent vs new data)
  ├─ Merge: strategy histories
  └─ Apply: learned preferences
    │
    v
Cross-Session Learning
  (operator avoids re-learning)
```

**Data Model:**
```python
ToolCoherence:
  - parent_session_id
  - coherence_chain (hash chain for integrity)
  - strategy_history ([{tool_id, error_type, outcome}])
  - learned_preferences ({error_class: tool_id})
  - cost_deltas ({tool_id: multiplier})
  - max_age_hours (24)

SessionCheckpoint:
  - session_id, tenant_id
  - coherence: ToolCoherence
  - created_at_utc
```

**Integration Points:**
- Input: Parent SessionCheckpoint
- Output: Inherited preferences, Cost multipliers, Strategy history
- Called by: Session resumption logic
- Gating: `learning.gap_5_context_coherence` flag

---

### Gap 6: Cost Learning (ADR-0326)

**Purpose:** Learn actual tool costs and refine budget estimates

**Flow:**
```
Initial Estimate: 10 cents
    │ (operator configures)
    v
100 Tool Executions
    │ (actual output tokens: 2.5x expected)
    v
CostLearningManager
    │
    ├─ Collect: [actual_cost_1, actual_cost_2, ..., actual_cost_100]
    │
    ├─ Compute: actual_avg / estimated_avg
    │  └─ Result: multiplier = 2.5
    │
    ├─ Apply EMA: new_multiplier = 0.9·old + 0.1·sample
    │
    v
Learned Multiplier: 2.5x
    │
    v
Budget Pre-Check
    ├─ remaining_budget = 100 cents
    ├─ tool_cost_estimate = 10 cents
    ├─ adjusted_cost = 10 * 2.5 = 25 cents
    └─ sufficient? (25 < 100) → YES, proceed
```

**Data Model:**
```python
CostMultiplier:
  - tool_id
  - multiplier (float, 1.0 = accurate estimate)
  - sample_count (N for convergence)
  - confidence (similar to Bayesian)
  - updated_at_utc
  - tenant_id

CostLearningEvent:
  - tool_id, estimated_cost_cents, actual_cost_cents
  - multiplier_before, multiplier_after
  - ema_alpha (0.1 default)
```

**Integration Points:**
- Input: Actual costs from Gap 1 events
- Output: Cost multipliers for budget planning
- Called by: Budget pre-check in turn planner
- Gating: `learning.gap_6_cost_learning` flag

---

### Gap 7: Operator Feedback Loop (ADR-0327)

**Purpose:** Capture operator preferences and adjust system behavior accordingly

**Flow:**
```
Operator Action
  │ (rate skill: 5 stars, comment)
  v
POST /api/skills/{skill_id}/rating
  │
  v
OperatorFeedbackManager.submit_skill_rating()
  ├─ Validate: rating ∈ [1, 5]
  ├─ Sanitize: comment (max 1000 chars)
  └─ Create event: OPERATOR_RATED_SKILL
    │
    v
EventEmitter → EventStore
  │
    v
Audit Trail
  │
    v
SkillPromotionEngine
  ├─ High ratings (≥4) → ↑ promotion threshold
  ├─ Low ratings (≤2) → ↓ demotion threshold
  └─ Comments → adjust attribution weights
    │
    v
Observable Effect
  (operator sees: skills promoted/demoted accordingly)
```

**Data Model:**
```python
OperatorRating:
  - rating_type (tool|skill)
  - target_id (tool_id|skill_name)
  - rating (1-5)
  - operator_id
  - comment (optional, sanitized)
  - created_at_utc
  - tenant_id

OperatorRatedSkillEvent:
  - event_type: OPERATOR_RATED_SKILL
  - payload:
    {skill_name, rating, comment, operator_id}
  - timestamp_utc
  - audit_entry (hash-chained)
```

**Integration Points:**
- Input: Operator Web API (/feedback, /rating)
- Output: Rating events, Promotion/demotion triggers
- Called by: Console Settings UI
- Gating: `learning.gap_7_operator_feedback` flag

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Learning System Data Flow                          │
└─────────────────────────────────────────────────────────────────────────────┘

  Turn Execution (Claude Code)
         │
         v
  Tool Execution ◄────── (Gap 1)
         │
         ├──────────────────────────────────────────────────┐
         │                                                  │
         v                                                  v
    EventStore              SessionCheckpoint (Gap 5)
    (persisted)               (coherence data)
         │                          │
         │                          v
         │                   Parent Context
         │                   (strategies,
         │                    preferences)
         │                          │
         v                          v
    Aggregation Job ─────────> Context Coherence Manager
    (Gap 4, hourly)              (inheritance)
         │                          │
         v                          v
    Metrics                  Learned Preferences
    (cached)                 (error → tool)
         │                          │
         ├──────┬──────────────────┬────┐
         │      │                  │    │
         v      v                  v    v
    (Gap 2)  (Gap 3)            (Gap 6) │
    Ranking  Attribution        Cost Learning
         │      │                 │     │
         v      v                 v     v
    Ranked   Skill Scores    Multiplier │
    Tools    Attribution      Learning  │
         │      │                 │     │
         └──────┼─────────────────┼─────┤
                │                 │     │
                v                 v     v
         Decision Making:
         - Which tool to use (Gap 2)
         - Cost budget (Gap 6)
         - Skill selection (Gap 3)
         - Is coherent (Gap 5)
                │
                v
         Tool Execution
         (next turn)
                │
         ┌──────┘
         │
         v
    Operator Feedback ◄────── (Gap 7)
         │
         v
    EventStore
         │
    (loop continues)
```

---

## Tenant Isolation

All gaps respect tenant_id scoping:

- **Gap 1:** Events tagged with tenant_id
- **Gap 2:** Ranking queries filtered by tenant_id
- **Gap 3:** Attribution computed per-tenant
- **Gap 4:** Aggregation scoped to tenant_id
- **Gap 5:** Coherence inherited within tenant only
- **Gap 6:** Multipliers learned per tenant
- **Gap 7:** Feedback stored with tenant_id

**GDPR Compliance:**
- All reads filter by tenant_id (Art. 6)
- All events hash-chained (Art. 30, 32)
- Audit trail immutable (Art. 5)
- No cross-tenant leakage (Art. 32)

---

## Performance Characteristics

| Operation | Latency | Throughput | Notes |
|-----------|---------|-----------|-------|
| Gap 1: Emit event | <10ms | 100 events/s | Async, non-blocking |
| Gap 4: Aggregate (10k) | <1s | 10k events/hour | Hourly batch job |
| Gap 2: Rank tools | <100ms p99 | 10 rankings/s | Cached (5-min TTL) |
| Gap 3: Attribute | <50ms | 20 attributions/s | Per-strategy |
| Gap 5: Inherit context | <50ms | 20 inherits/s | On session start |
| Gap 6: Learn multiplier | <5ms | 200 updates/s | EMA incremental |
| Gap 7: Rate skill | <20ms | 50 ratings/s | Web API |

---

## Configuration & Tuning

### Feature Flags

```yaml
spec:
  learning:
    gap_1_tool_execution_telemetry: true  # Emit tool events
    gap_2_tool_ranking: true              # Use ranking for tool selection
    gap_3_skill_attribution: true         # Fair skill grading
    gap_4_performance_aggregation: true   # Hourly metrics job
    gap_5_context_coherence: true         # Cross-session inheritance
    gap_6_cost_learning: true             # Budget refinement
    gap_7_operator_feedback: true         # Operator ratings
```

### Tuning Parameters

```yaml
spec:
  learning_tuning:
    gap_4_aggregation_window_days: 7      # Event lookback window
    gap_4_aggregation_interval_seconds: 3600  # Hourly job
    gap_4_cache_ttl_seconds: 300          # 5-minute cache
    gap_2_score_threshold: 0.7            # Reuse threshold
    gap_5_max_age_hours: 24               # Coherence validity
    gap_6_ema_alpha: 0.1                  # Cost smoothing
    gap_6_convergence_samples: 100        # N for confidence
```

---

## Deployment Strategy

### Week 1-2: Canary (10% of users)
- Enable Gap 1 only (telemetry collection)
- Monitor event emission rate and latency
- Verify audit trail integrity

### Week 3: Beta (50% of users)
- Enable Gaps 4 + 2 (aggregation + ranking)
- Monitor metric accuracy
- Verify tool selection quality

### Week 4: Full Rollout (100% of users)
- Enable all 7 gaps
- Monitor learning loop closure
- Track operator feedback response time

---

## Troubleshooting

### Gap 1: No events in EventStore

**Diagnosis:**
```bash
# Check if Gap 1 flag is enabled
grep "gap_1_tool_execution_telemetry" ~/.corvin/tenants/_default/global/tenant.corvin.yaml

# Check for emission errors
tail -f logs/learning.log | grep "TOOL_EXECUTED"
```

**Fix:**
1. Verify flag is `true`
2. Check EventEmitter is initialized in ToolForgeSubsystem
3. Restart agent

### Gap 4: Aggregation metrics incorrect

**Diagnosis:**
```bash
# Check stored events
python3 -c "
from core.learning.event_store import EventStore
store = EventStore()
events = store.read_events(event_type='TOOL_EXECUTED')
print(f'Total events: {len(events)}')
print(f'Success rate: {sum(1 for e in events if e.payload[\"status\"] == \"success\") / len(events):.2%}')
"
```

**Fix:**
1. Verify aggregation job ran: `tail -f logs/aggregation.log`
2. Check cache: `/tmp/learning_cache/*`
3. Manually run aggregation: `python3 -m core.learning.performance_aggregation`

### Gap 6: Cost multiplier not converging

**Diagnosis:**
```bash
# Check multiplier learning
python3 -c "
from core.learning.tool_cost_learning import CostLearningManager
mgr = CostLearningManager()
multiplier = mgr.get_cost_multiplier('tool_id')
print(f'Multiplier: {multiplier}')
print(f'Confidence: {multiplier.confidence}')
"
```

**Fix:**
1. Ensure Gap 1 is enabled (costs come from tool events)
2. Verify cost data is populated in events
3. Check EMA alpha is reasonable (0.05-0.2)

---

## Related Documents

- [ADR-0321](../Corvin-ADR/decisions/ADR-0321-*.md): Tool Execution Learning Events
- [ADR-0322](../Corvin-ADR/decisions/ADR-0322-*.md): Tool Performance Ranking
- [ADR-0323](../Corvin-ADR/decisions/ADR-0323-*.md): Skill Attribution Model
- [ADR-0324](../Corvin-ADR/decisions/ADR-0324-*.md): Performance Aggregation
- [ADR-0325](../Corvin-ADR/decisions/ADR-0325-*.md): Context Coherence
- [ADR-0326](../Corvin-ADR/decisions/ADR-0326-*.md): Cost Learning
- [ADR-0327](../Corvin-ADR/decisions/ADR-0327-*.md): Operator Feedback
- [LEARNING_GAPS_LDD_VERIFICATION_COMPLETE.md](./LEARNING_GAPS_LDD_VERIFICATION_COMPLETE.md): Test coverage & verification

---

**Last Updated:** 2026-08-19  
**Status:** ✅ Complete (v0.2-rc1)
