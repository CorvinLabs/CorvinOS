# Phase 3: Learning Loop Activation — ADR-0316 to ADR-0321 Roadmap

**Date:** 2026-09-04  
**Status:** Phase 3.1 (ADR-0315) Complete ✅; Phases 3.2–3.7 Ready for Autonomous Execution  
**Total Timeline:** 6–8 weeks (5 days/week)

---

## Phase Overview

Phase 3 enables **self-learning Skills** through closed-loop feedback. ADR-0315 (Confidence Intervals) established decision scoring. Remaining ADRs build:

1. **0316:** Decision History (track user choices → learn patterns)
2. **0317:** Outcome Feedback (closed-loop: was decision right?)
3. **0318:** Style Preferences (learn user communication style)
4. **0319:** Attention Budget (finite attention constraint)
5. **0320:** Metric Collection (aggregate latency/cost/errors)
6. **0321:** Reporting Dashboard (observability UI)

---

## ADR-0316: Decision History (Phase 3.2)

**Timeline:** Week 2–3 (10 days)  
**Scope:** Track which Skill decisions user accepted/rejected

### Design

```
DecisionHistoryEvent (frozen dataclass):
  - decision_id: str (links to ConfidenceEvent)
  - skill_id: str (which Skill decided?)
  - user_action: enum ("accept" | "reject" | "modify" | "ignore")
  - timestamp: ISO8601
  - tenant_id: str (GDPR Art. 32)
  - context_snapshot: dict (what was the task context?)
```

### Implementation

**File:** `core/learning/decision_history.py`

- DecisionHistoryEvent (frozen schema, like ConfidenceEvent)
- DecisionHistory (query interface with tenant isolation)
- Integration: EventStore persistence, audit trail

**Tests (Tier 1–3):**
- Event creation + serialization
- Tenant isolation validation
- Query by skill_id / time range
- EventStore round-trip

**GDPR:** Art. 30 (audit), Art. 32 (tenant scope, no context leakage)

### Success Criteria

✅ 100 decisions stored + retrieved without leakage  
✅ Query by skill_id + time range  
✅ Audit chain verified  
✅ Tier-3 integration green

---

## ADR-0317: Outcome Feedback (Phase 3.3)

**Timeline:** Week 4–5 (10 days)  
**Scope:** Closed-loop learning signal (was the decision correct?)

### Design

```
OutcomeFeedbackEvent (frozen dataclass):
  - decision_id: str (links to ConfidenceEvent + DecisionHistoryEvent)
  - outcome: enum ("correct" | "incorrect" | "partial" | "unknown")
  - feedback_source: enum ("user" | "system_metric" | "inference")
  - confidence_delta: float (-1.0 to +1.0, how much should confidence change?)
  - timestamp: ISO8601
  - tenant_id: str (GDPR Art. 32)
  - reasoning: str (why this outcome?)
```

### Implementation

**File:** `core/learning/outcome_feedback.py`

- OutcomeFeedbackEvent (frozen schema)
- OutcomeFeedbackCollector (integrate with Skill execution loop)
- Feedback → Confidence adjustment (0.4 * relevance_delta + 0.6 * reliability_delta)
- Integration: EventStore persistence

**Tests:**
- Feedback event creation
- Confidence adjustment calculation
- EventStore persistence
- Tenant isolation

**Optimizer Integration:**
- OutcomeEvent + ConfidenceEvent → compute optimal Skill config delta
- Update Skill parameters (thresholds, weights) based on feedback pattern

**GDPR:** Art. 30 (decision audit), Art. 6(1)(f) legitimate interest (learning improvement)

### Success Criteria

✅ Feedback → Confidence delta mapping correct  
✅ Optimizer computes config adjustments  
✅ Skill parameters update based on feedback  
✅ No cross-tenant feedback leakage

---

## ADR-0318: Style Preferences (Phase 3.4)

**Timeline:** Week 6–7 (10 days)  
**Scope:** Learn user communication style, tone, format preferences

### Design

```
StylePreferenceEvent (frozen dataclass):
  - dimension: enum ("tone", "format", "length", "technical_level")
  - preference: str (e.g., "concise" for tone, "markdown" for format)
  - confidence: float (0.0-1.0, how sure are we?)
  - evidence_count: int (how many interactions support this?)
  - timestamp: ISO8601
  - tenant_id: str (GDPR Art. 32)
```

### Implementation

**File:** `core/learning/style_preferences.py`

- StylePreferenceEvent (frozen schema)
- StyleLearner (infer preferences from OutcomeFeedback patterns)
- Integration: ContextAdapterSkill uses style preferences to adapt output

**Inference:**
- User corrects "too technical" → tone preference = "simplify"
- User says "TL;DR" → length preference = "concise"
- User reformats as JSON → format preference = "structured"

**Tests:**
- Preference inference from feedback
- Confidence scoring for preferences
- EventStore persistence
- ContextAdapter integration

**GDPR:** Art. 30 (audit of learned preferences), Art. 32 (tenant-scoped)

### Success Criteria

✅ Preferences inferred from feedback patterns  
✅ ContextAdapter uses preferences in output generation  
✅ Confidence scores for preferences  
✅ No user-identifying information in preferences

---

## ADR-0319: Attention Budget (Phase 3.5)

**Timeline:** Week 8+ (15 days)  
**Scope:** Finite attention constraint (user can't process infinite feedback)

### Design

```
AttentionBudgetEvent (frozen dataclass):
  - period: enum ("daily" | "weekly" | "session")
  - total_tokens: int (max LLM tokens for feedback loop)
  - used_tokens: int (spent so far)
  - remaining_tokens: int (budget left)
  - timestamp: ISO8601
  - tenant_id: str (GDPR Art. 32)
```

### Implementation

**File:** `core/learning/attention_budget.py`

- AttentionBudgetManager (track token consumption)
- BudgetGate (before emitting feedback, check if budget remaining)
- Prioritization: high-confidence decisions get feedback first

**Budget Enforcement:**
- Default: 1000 tokens/day for feedback loop
- Once budget exhausted: only emit critical feedback (confidence drops below 0.3)
- Reset: daily/weekly/session boundary

**Tests:**
- Budget tracking accuracy
- Gate prevents over-spending
- Priority ordering works
- Tenant isolation

**GDPR:** Art. 6(1)(f) legitimate interest (managing communication burden), Art. 32 (tenant-scoped)

### Success Criteria

✅ Budget tracked per user/tenant  
✅ Critical feedback always emitted (budget bypass)  
✅ Non-critical feedback queued/dropped when budget exhausted  
✅ No budget leakage across tenants

---

## ADR-0320: Metric Collection (Phase 3.6)

**Timeline:** Week 9–10 (10 days)  
**Scope:** Aggregate latency, cost, error rates across Skills

### Design

```
MetricEvent (frozen dataclass):
  - metric_type: enum ("latency_ms", "cost_tokens", "error_count", "cache_hit_rate")
  - skill_id: str
  - value: float
  - timestamp: ISO8601
  - tenant_id: str (GDPR Art. 32)
  - aggregation_window: str (e.g., "5m", "1h", "1d")
```

### Implementation

**File:** `core/learning/metrics.py`

- MetricCollector (hook into Skill execution, capture latency/cost/errors)
- MetricAggregator (5m / 1h / 1d rolling windows)
- Dashboard datasource (feed Vibe dashboard)

**Integration:**
- Skill.execute() → emit MetricEvent (latency_ms, cost_tokens)
- OutcomeFeedback → emit MetricEvent (error_count if outcome="incorrect")
- CacheHit → emit MetricEvent (cache_hit_rate)

**Tests:**
- Metric collection accuracy
- Aggregation windowing
- EventStore persistence
- Tenant isolation

**GDPR:** Art. 30 (audit of metrics), Art. 32 (tenant-scoped)

### Success Criteria

✅ Metrics collected on all Skill executions  
✅ Aggregation windows computed accurately  
✅ Dashboard can query metrics per skill/tenant  
✅ No cross-tenant metric leakage

---

## ADR-0321: Reporting Dashboard (Phase 3.7)

**Timeline:** Week 11–12 (15 days)  
**Scope:** Vibe Engineering dashboard for operator visibility

### Features

**Dashboard Panels:**

1. **Skill Confidence Heatmap**
   - Grid: Skills (rows) × time (columns)
   - Color: confidence score (red=low, green=high)
   - Hover: relevance + reliability breakdown

2. **Decision History Timeline**
   - User choices over time
   - Skill decisions + outcomes
   - Pattern detection (e.g., "user rejects routing decisions on Fridays")

3. **Feedback Health**
   - Feedback reception rate (how much does user engage?)
   - Outcome signal quality (can we detect correct vs incorrect?)
   - Style preference confidence (how sure are we?)

4. **Metrics Dashboard**
   - Latency per skill (p50, p95, p99)
   - Cost trends (tokens/decision)
   - Error rate (%)
   - Cache hit rate (%)

5. **Attention Budget Status**
   - Daily/weekly budget consumption
   - Critical feedback emitted vs queued
   - Budget forecast (will user hit limit?)

### Implementation

**File:** `core/console/corvin_console/routes/vibe_learning.py`

- Backend routes: `/vibe/confidence`, `/vibe/decisions`, `/vibe/metrics`, `/vibe/budget`
- Frontend panels: React components using Recharts for charts
- Real-time updates: WebSocket feed from Learning layer

**Tests:**
- API endpoint tests (data correctness)
- Chart data formatting
- E2E dashboard navigation
- Permission checks (operator-only access)

**GDPR:** Art. 30 (audit of reported metrics), Art. 32 (tenant-scoped dashboard)

### Success Criteria

✅ All 5 panels load + render correctly  
✅ Data refreshes every 5s (near real-time)  
✅ Operator can drill down (skill → decisions → outcomes)  
✅ No cross-tenant data visible  
✅ Permission checks enforced

---

## Execution Timeline (Autonomous)

| Phase | ADR | Focus | Effort | Timeline | Status |
|-------|-----|-------|--------|----------|--------|
| 3.1 | 0315 | Confidence Intervals | 5h | Week 1 | ✅ DONE |
| 3.2 | 0316 | Decision History | 10h | Week 2–3 | ⏳ READY |
| 3.3 | 0317 | Outcome Feedback | 10h | Week 4–5 | ⏳ READY |
| 3.4 | 0318 | Style Preferences | 10h | Week 6–7 | ⏳ READY |
| 3.5 | 0319 | Attention Budget | 15h | Week 8–9 | ⏳ READY |
| 3.6 | 0320 | Metric Collection | 10h | Week 10–11 | ⏳ READY |
| 3.7 | 0321 | Dashboard | 15h | Week 12–13 | ⏳ READY |

**Total Phase 3:** 75 hours (8–10 weeks, 5 days/week)

---

## Quality Gates (All Phases)

| Gate | Requirement | LDD Method |
|------|-----------|-----------|
| **Tier 1** | Syntax + imports pass | `python3 -m py_compile` |
| **Tier 2** | Unit tests green | Validation tests (no pytest needed) |
| **Tier 3** | Integration + EventStore | Round-trip serialization tests |
| **Tier 4** | E2E (dashboard) | Real WebSocket + UI tests |
| **Adversarial** | 0 findings | Ultra depth (7+ dimensions) |

---

## GDPR Compliance Checklist (All Phases)

- ✅ Art. 30 (Record-keeping): All events audit-logged, hash-chained
- ✅ Art. 32 (Integrity): Immutable events, no silent data loss
- ✅ Art. 32 (Confidentiality): Tenant-scoped, no cross-tenant leakage
- ✅ Art. 5 (Fairness): No PII in preference/style events
- ✅ Art. 6(1)(f) (Legitimate interest): Learning improves Skill quality for user

---

## Risk Mitigation

| Risk | Mitigation | Owner |
|------|-----------|-------|
| Scope creep (more ADRs) | Time-box each phase at budget | Shumway |
| Regression in Phase 2 | Regression test suite (always-on) | Tests |
| Cross-tenant data leakage | Tenant isolation at all layers | Code review |
| Token budget exhaustion | Compact implementation (k=1-2 per ADR) | LDD discipline |
| Operator misinterpretation | Dashboard documentation + tooltips | UX |

---

## Success Criteria (Phase 3 Complete)

✅ All 7 ADRs implemented  
✅ All 7 features working end-to-end  
✅ Tier-1/2/3 gates green for all  
✅ Operator can observe Skill confidence + feedback loop  
✅ Learning loop closes (feedback → confidence → Skill optimization)  
✅ GDPR compliance verified (0 Art. 30/32 violations)

---

**Report Status:** ✅ PHASE 3 ROADMAP READY  
**Next:** Phase 3.2 Autonomous Execution (ADR-0316 Decision History)  
**Timeline:** 6–8 weeks to Phase 3 completion
