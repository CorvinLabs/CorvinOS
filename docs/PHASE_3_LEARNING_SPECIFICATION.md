# Phase 3: Learning Infrastructure (8 ADRs, 75h)

**Status:** PRE-PLANNING (for BATCH 2 agents)  
**Scope:** ADRs 0315–0321 (ADR-0314 already implemented via Vibe Engineering)  
**Parallel Execution:** 4 agents (2 ADRs per agent, staggered)  
**Total Tests:** 100+ tests  
**Success Criteria:** All 8 ADRs merged, 0 regressions, adversarial review K=1–K=5 gates pass

---

## ADR BREAKDOWN

### ADR-0315: Confidence Intervals (Relevance & Reliability Scoring)
**Effort:** 10h | **Risk:** MEDIUM | **Blocker:** ADR-0314 complete (✅)

**Goal:** Quantify confidence in recommendations/decisions

**Deliverables:**
- Confidence scoring model (0.0–1.0 scale)
- Relevance scorer (TF-IDF + semantic similarity)
- Reliability scorer (historical accuracy tracking)
- Calibration tests (MAE ≤ 0.15 on holdout set)

**Files:**
- `core/learning/confidence.py` (100 LoC)
- `core/learning/tests/test_confidence_intervals.py` (15 tests)

**Dependencies:** ADR-0314 (Event schema + EventStore)

---

### ADR-0316: Decision History (User Choice Tracking)
**Effort:** 9h | **Risk:** LOW | **Blocker:** ADR-0314

**Goal:** Persistent record of user decisions for learning

**Deliverables:**
- Decision event schema (choice, outcome, feedback)
- History store (time-series DB-backed)
- Query interface (filter by task/date/type)
- Compliance (GDPR retention policy)

**Files:**
- `core/learning/decision_history.py` (80 LoC)
- `core/learning/tests/test_decision_history.py` (12 tests)

**Dependencies:** ADR-0314, L34 (Data Classification)

---

### ADR-0317: Outcome Feedback (Closed-Loop Learning)
**Effort:** 11h | **Risk:** HIGH | **Blocker:** ADR-0314, ADR-0316

**Goal:** Collect outcome data to train models (offline)

**Deliverables:**
- Outcome event schema (success/failure, metrics, user rating)
- Feedback loop (async, non-blocking)
- Training data export (format: CSV/Parquet for ML)
- Backprop rules (how feedback affects confidence scoring)

**Files:**
- `core/learning/outcome_feedback.py` (120 LoC)
- `core/learning/tests/test_outcome_feedback.py` (14 tests)

**Dependencies:** ADR-0315, ADR-0316

---

### ADR-0318: Style Preferences (User Model)
**Effort:** 8h | **Risk:** LOW | **Blocker:** ADR-0314

**Goal:** Learn user style preferences (tone, detail level, pacing)

**Deliverables:**
- Preference schema (4D model: verbosity, pace, tone, technical_depth)
- Preference learner (from user feedback + message patterns)
- Preference storage (per-user profile, tenant-scoped)
- Integration hook (context pipeline uses prefs)

**Files:**
- `core/learning/user_preferences.py` (100 LoC)
- `core/learning/tests/test_user_preferences.py` (13 tests)

**Dependencies:** ADR-0314

---

### ADR-0319: Attention Budget (Finite Attention Constraint)
**Effort:** 10h | **Risk:** MEDIUM | **Blocker:** ADR-0315, ADR-0316

**Goal:** Respect token budget while maximizing value

**Deliverables:**
- Token budget model (per-session, per-phase)
- Attention allocation (confidence-weighted, returns-first)
- Budget monitoring + alerts
- Adaptive truncation (when budget threatened)

**Files:**
- `core/learning/attention_budget.py` (110 LoC)
- `core/learning/tests/test_attention_budget.py` (16 tests)

**Dependencies:** ADR-0315 (confidence), ADR-0320 (metrics)

---

### ADR-0320: Metric Collection (Aggregation Pipeline)
**Effort:** 9h | **Risk:** MEDIUM | **Blocker:** ADR-0317, ADR-0319

**Goal:** Real-time aggregation of learning metrics

**Deliverables:**
- Metric schema (per-task, per-user, per-phase)
- Aggregation pipeline (sum, mean, percentile)
- Time-series storage (partitioned by date)
- Query interface (slice by dims, time range)

**Files:**
- `core/learning/metrics.py` (100 LoC)
- `core/learning/tests/test_metrics.py` (14 tests)

**Dependencies:** ADR-0314, ADR-0317

---

### ADR-0321: Reporting Dashboard (Observability UI)
**Effort:** 9h | **Risk:** LOW | **Blocker:** ADR-0320

**Goal:** Operator-visible learning metrics + system health

**Deliverables:**
- React dashboard: `core/console/corvin_console/web-next/src/components/LearningMetrics.tsx`
- Flask routes: `core/console/corvin_console/routes/learning_metrics.py`
- Displays:
  * Confidence distribution (histogram)
  * Decision history (timeline)
  * User preference patterns (radar chart)
  * Attention budget usage (gauge)
  * Outcome feedback loop health (green/yellow/red)
- Dark mode + responsive (mobile/tablet/desktop)

**Files:**
- `core/console/corvin_console/web-next/src/components/LearningMetrics.tsx` (300 LoC)
- `core/console/corvin_console/routes/learning_metrics.py` (80 LoC)
- `core/learning/tests/test_learning_dashboard.py` (10 tests)

**Dependencies:** ADR-0320, ADR-0315, ADR-0316, ADR-0317, ADR-0318, ADR-0319

---

## DEPENDENCY GRAPH

```
ADR-0314 (Event Schema)  ← [DONE via Vibe]
  │
  ├→ ADR-0315 (Confidence Intervals)
  │   └→ ADR-0319 (Attention Budget) ← uses confidence scoring
  │       └→ ADR-0320 (Metrics) ← aggregates attention usage
  │
  ├→ ADR-0316 (Decision History)
  │   └→ ADR-0317 (Outcome Feedback) ← trains on history
  │       └→ ADR-0320 (Metrics) ← aggregates outcomes
  │
  ├→ ADR-0318 (User Preferences)
  │   └→ ADR-0321 (Dashboard) ← visualizes preferences
  │
  └→ ADR-0320 (Metrics) → ADR-0321 (Dashboard) ← visualizes metrics
```

**Blocking Order:**
```
1. ADR-0314 ✅ (already done)
2. ADR-0315, ADR-0316, ADR-0318 (parallel — no cross-deps)
3. ADR-0317 (depends on 0316)
4. ADR-0319 (depends on 0315)
5. ADR-0320 (depends on 0317, 0319)
6. ADR-0321 (depends on 0320)
```

---

## PARALLEL AGENT ASSIGNMENTS (4 agents)

| Agent | ADRs | Effort | Blocking |
|---|---|---|---|
| Agent A | 0315, 0318 | 18h | None |
| Agent B | 0316, 0317 | 20h | 0317 → 0316 |
| Agent C | 0319, 0320 | 19h | 0320 → 0319 |
| Agent D | 0321 | 9h | 0321 → 0320 |

**Execution:**
- Agents A, B, C start in parallel (no blockers)
- Agent D starts after Agent C completes (ADR-0320)

---

## SUCCESS CRITERIA

✅ All 8 ADRs implemented + merged  
✅ 100+ tests written + passing  
✅ Adversarial Review K=1–K=5 gates pass  
✅ Learning loop functional (Event → Metric → Dashboard)  
✅ Zero critical blockers  

**Test Targets:**
- Confidence: 15 tests (calibration, edge cases)
- Decision History: 12 tests (CRUD, filtering)
- Outcome Feedback: 14 tests (backprop, export)
- User Preferences: 13 tests (learning, integration)
- Attention Budget: 16 tests (allocation, alerts)
- Metrics: 14 tests (aggregation, time-series)
- Dashboard: 10 tests (React component, API, responsive)

---

**Prepared by:** Autonomous workflow  
**For:** BATCH 2 execution (after BATCH 1 gates pass)  
**Next:** Phase 4 (Consolidation) specification
