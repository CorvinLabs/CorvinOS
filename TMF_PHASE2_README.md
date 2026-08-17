# Token Measurement Framework (TMF) — Phase 2 Complete Design

**Status:** Ready for Implementation  
**Last Updated:** 2026-08-17  
**Audience:** Developers implementing Phase 2 (K=1 through K=4)

---

## Overview

Phase 1 built the measurement infrastructure (instrumentation, storage, baseline, aggregation, tests). **Phase 2 wires it into the runtime, makes it observable via a React dashboard, and replaces the in-memory cache with persistent database storage.**

| Phase | K | Focus | Status |
|-------|---|-------|--------|
| **Phase 1** | K=1-K=5 | Instrumentation, Storage, Baseline, Aggregation, Tests | ✅ COMPLETE |
| **Phase 2** | K=1 | WorkerEngine Integration | 🚀 START HERE |
| **Phase 2** | K=2 | EventStore ↔ Database Backend | → After K=1 |
| **Phase 2** | K=3 | Console Panel (React VibeMetrics) | → After K=2 |
| **Phase 2** | K=4 | API Endpoints | → After K=3 |

---

## Three Documents (Use Alongside Each Other)

### 1. **TMF_PHASE2_DESIGN.md** — The Master Plan
   - **What:** Comprehensive design for all 4 Ks
   - **When:** First read; reference for overall vision
   - **Contents:**
     - Executive summary + Phase 1 recap
     - Detailed K=1–K=4 goals, files, changes, testing
     - High-level checklist
     - FAQ + troubleshooting

   **Start here to understand the big picture.**

### 2. **TMF_PHASE2_QUICK_REF.md** — Implementation Tasks
   - **What:** Step-by-step tasks with skeleton code
   - **When:** While coding; reference for exact line numbers
   - **Contents:**
     - K=1: Discovery + instrumentation tasks (with grep commands)
     - K=2: DB classes + schema (with SQL + Python boilerplate)
     - K=3: React component structure (with React/TypeScript templates)
     - K=4: API routes (with FastAPI endpoints)
     - Dependency tree + file locations (master list)
     - Quick start commands

   **Refer to this when writing code; follow the pseudocode skeletons.**

### 3. **TMF_PHASE2_ARCHITECTURE.md** — Visual Reference
   - **What:** Diagrams, data flow, deployment architecture
   - **When:** To understand how components interact
   - **Contents:**
     - End-to-end data flow (WorkerEngine → DB → API → UI)
     - Console-to-DB query path
     - File dependency graph
     - DB schema (visual + SQL)
     - Deployment diagram
     - K-wise completion criteria

   **Refer to this to visualize the architecture; useful for design reviews.**

---

## Quick Start: Which Doc Do I Read?

```
Am I starting Phase 2?
├─ YES → Read TMF_PHASE2_DESIGN.md (Executive Summary section)
│        Then TMF_PHASE2_ARCHITECTURE.md (Data Flow diagrams)
│        Then start K=1 in TMF_PHASE2_QUICK_REF.md
│
Are you coding K=1 (WorkerEngine)?
├─ YES → Jump to TMF_PHASE2_QUICK_REF.md § K=1
│        ├─ Task 1A: Find WorkerEngine (grep command)
│        ├─ Task 1B: Instrument WorkerEngine (pseudocode)
│        ├─ Task 1C: Inject TokenMetricsStore
│        └─ Task 1D: Create tests
│
Are you coding K=2 (DB)?
├─ YES → Jump to TMF_PHASE2_QUICK_REF.md § K=2
│        ├─ Task 2A: Create TokenMetricsDB (skeleton code)
│        ├─ Task 2B: Create DB factory
│        ├─ Task 2C: Upgrade TokenMetricsStore
│        ├─ Task 2D: Update app bootstrap
│        └─ Task 2E: Create tests
│
Are you building the React panel (K=3)?
├─ YES → Jump to TMF_PHASE2_QUICK_REF.md § K=3
│        ├─ Task 3A: Create pages & components (skeleton TSX)
│        ├─ Task 3B: Register panel
│        ├─ Task 3C: Export from lazy-pages
│        └─ Task 3D: Create tests (Playwright)
│
Are you building the API (K=4)?
└─ YES → Jump to TMF_PHASE2_QUICK_REF.md § K=4
         ├─ Task 4A: Create metrics routes (skeleton FastAPI)
         ├─ Task 4B: Add DI helper
         ├─ Task 4C: Register routes in app.py
         └─ Task 4D: Create tests
```

---

## The 4 Ks at a Glance

### K=1: WorkerEngine Integration (Week 1)
**Goal:** Instrument the runtime so every turn measures tokens automatically.

**Key Files:**
- (TBD) `core/compute/worker.py` or similar → **FIND THIS FIRST**
- `core/console/corvin_console/app.py` → Add DI

**What You'll Add:**
```python
# In WorkerEngine.run():
counter = TokenInstrumentationHooks.on_worker_engine_start(turn_id, engine, tier)
TokenInstrumentationHooks.on_llm_response(counter, input_tokens, output_tokens)
TokenInstrumentationHooks.on_worker_engine_end(counter, quality, followup)
await metrics_store.write_token_metrics(counter, ...)
```

**Success:** Real turn → metrics recorded → appear in storage

---

### K=2: EventStore ↔ Database Backend (Week 2)
**Goal:** Replace in-memory cache with persistent SQLite/PostgreSQL.

**Key Files:**
- `core/learning/token_metrics_db.py` (NEW) → DB abstraction
- `core/learning/token_metrics_db_factory.py` (NEW) → Factory
- `core/learning/token_metrics_store.py` → Upgrade to DB-backed
- `core/console/corvin_console/app.py` → Initialize DB on startup

**What You'll Build:**
- `TokenMetricsDB` abstract base class
- `SqliteMetricsDB` implementation (11 columns, 3 indexes)
- DB schema: token_metrics table
- Query methods: insert, query_by_session, query_by_timespan, aggregate_*

**Success:** Metrics persist to SQLite; queries are fast (<100ms)

---

### K=3: Console Panel (Week 3)
**Goal:** Build real-time React dashboard showing token metrics.

**Key Files:**
- `core/console/corvin_console/web-next/src/pages/vibe-metrics.tsx` (NEW)
- `core/console/corvin_console/web-next/src/components/VibeMetrics/` (NEW folder, 6 files)
- `core/console/corvin_console/web-next/src/panels/registry.tsx` → Register panel

**What You'll Build:**
- React components: Dashboard, TokenChart, SavingsCard, SubsystemBreakdown, TaskTypeTable
- `useLiveMetrics.ts` hook (fetches from `/api/metrics/*` every 5s)
- Dark mode styling + responsive layout

**Success:** Navigate to `/console/vibe-metrics`; see live data; charts update every 5 seconds

---

### K=4: API Endpoints (Week 4)
**Goal:** Expose metrics via REST API for the console panel.

**Key Files:**
- `core/console/corvin_console/routes/metrics.py` (NEW) → 5 GET endpoints
- `core/console/corvin_console/deps.py` → Add `get_metrics_store()` DI
- `core/console/corvin_console/app.py` → Register routes

**What You'll Build:**
- `GET /v1/console/metrics/session/{id}/summary` → Summary stats
- `GET /v1/console/metrics/session/{id}/turns` → Individual turn metrics
- `GET /v1/console/metrics/session/{id}/by-task-type` → Breakdown by task type
- `GET /v1/console/metrics/session/{id}/by-subsystem` → Breakdown by subsystem
- `GET /v1/console/metrics/stats` → Global stats (admin-only)

**Success:** Console panel fetches data; API returns 200 OK; tenant isolation verified

---

## File Checklist (Master List)

### Phase 1 Existing (Reference Only)
```
core/learning/
├── token_instrumentation.py       ✓ Phase 1
├── token_baseline.py               ✓ Phase 1
├── token_metrics_store.py          ⚠ Upgrade in K=2
├── token_metrics_aggregator.py     ✓ Phase 1
├── event_schema.py                 ✓ Phase 1
├── event_persistence.py            ✓ Phase 1
└── event_emitter.py                ✓ Phase 1
```

### K=1 Files
```
(TBD) WorkerEngine                   ↔ MODIFY (4 hook calls)
core/console/corvin_console/app.py   ↔ MODIFY (DI setup)
tests/unit/test_token_instrumentation_k1_live.py  ← NEW
```

### K=2 Files
```
core/learning/token_metrics_db.py    ← NEW (400 LOC)
core/learning/token_metrics_db_factory.py ← NEW (80 LOC)
core/learning/token_metrics_store.py ↔ MODIFY (50 LOC)
core/console/corvin_console/app.py   ↔ MODIFY (40 LOC)
tests/unit/test_token_metrics_db_k2.py ← NEW (200 LOC)
```

### K=3 Files
```
core/console/corvin_console/web-next/src/
├── pages/vibe-metrics.tsx           ← NEW (20 LOC)
├── components/VibeMetrics/
│   ├── Dashboard.tsx                ← NEW (150 LOC)
│   ├── TokenChart.tsx               ← NEW (80 LOC)
│   ├── SavingsCard.tsx              ← NEW (60 LOC)
│   ├── SubsystemBreakdown.tsx       ← NEW (80 LOC)
│   ├── TaskTypeTable.tsx            ← NEW (100 LOC)
│   └── useLiveMetrics.ts            ← NEW (60 LOC)
├── panels/registry.tsx              ↔ MODIFY (3 lines)
└── lazy-pages/index.ts              ↔ MODIFY (1 line)
tests/e2e/console-vibe-metrics.spec.ts ← NEW (120 LOC)
```

### K=4 Files
```
core/console/corvin_console/
├── routes/metrics.py                ← NEW (300 LOC)
├── deps.py                          ↔ MODIFY (8 lines)
└── app.py                           ↔ MODIFY (20 lines)
tests/unit/test_metrics_api_k4.py   ← NEW (180 LOC)
```

**Total:** ~25 files (15 new, 10 modified); ~2,200 LOC added

---

## Success Criteria Summary

### K=1 ✓ When:
- [ ] WorkerEngine found (exact file + line number)
- [ ] 4 hooks inserted and firing on every turn
- [ ] TokenMetricsStore injected into WorkerEngine
- [ ] E2E: Real turn → metrics recorded → appear in storage
- [ ] All Phase 1 tests still pass

### K=2 ✓ When:
- [ ] SQLite DB created at `~/.corvin/tenants/_default/global/metrics.db`
- [ ] Schema created: token_metrics table (11 columns + 3 indexes)
- [ ] insert_token_metrics() and query_by_session() working
- [ ] DB persists across app restart
- [ ] Audit chain still fires for every write
- [ ] All K=1 + Phase 1 tests still pass

### K=3 ✓ When:
- [ ] Panel loads at `/console/vibe-metrics`
- [ ] Dashboard renders 4 stat cards + 2 charts + 1 table
- [ ] Data polls from API every 5 seconds
- [ ] Dark mode styling applied
- [ ] Charts display real data
- [ ] All K=1 + K=2 + Phase 1 tests still pass

### K=4 ✓ When:
- [ ] 5 GET endpoints live (`/v1/console/metrics/*`)
- [ ] Auth checks prevent unauthorized access
- [ ] Tenant isolation verified (WHERE tenant_id = ?)
- [ ] Console panel fetches data successfully
- [ ] API returns <100ms response time
- [ ] All K=1–K=3 + Phase 1 tests still pass

---

## Common Questions

**Q: Where do I start?**  
A: Read `TMF_PHASE2_DESIGN.md` § "Executive Summary" (5 min read). Then jump to `TMF_PHASE2_QUICK_REF.md` § "K=1: Find WorkerEngine" and follow the grep command to locate it.

**Q: What if I can't find WorkerEngine?**  
A: Run the grep command from K=1 Task 1A in QUICK_REF. If it returns nothing, search in:
   - `core/gateway/corvin_gateway/` (main LLM invocation)
   - `core/compute/` (compute worker)
   - Search for "async def run" or "def process_turn"

**Q: How long does each K take?**  
A: Estimate ~1 week per K if you're working full-time. K=1 is discovery + wiring (3–4 days); K=2 is DB backend (2–3 days); K=3 is React components (2–3 days); K=4 is API routes (1–2 days).

**Q: Can I skip K=2 and go straight to K=3?**  
A: No. K=3 (React panel) depends on K=2 (DB queries). The panel fetches from the API, which queries the DB. Without K=2, you'd have no persistent storage to query.

**Q: What if the DB backend already exists?**  
A: Excellent! Adapt K=2 to use it. The key is that `TokenMetricsStore` must have `query_by_session()`, `query_by_timespan()`, and `summary()` methods that return data from a DB (not in-memory only).

**Q: Do I need to backfill old metrics into the DB?**  
A: No. Phase 2 K=2 creates the DB from scratch. Existing Phase 1 metrics stay in disk (JSONL). New turns (after K=2 deployment) write to both audit chain AND DB. Optional: add a backfill script later if you want historical data searchable via DB.

**Q: How do I test each K without deploying?**  
A: Each K has a test file:
   - K=1: `pytest tests/unit/test_token_instrumentation_k1_live.py`
   - K=2: `pytest tests/unit/test_token_metrics_db_k2.py`
   - K=3: Playwright E2E test (requires running console UI)
   - K=4: `pytest tests/unit/test_metrics_api_k4.py`

**Q: What do I do if a Phase 1 test breaks?**  
A: Stop; don't merge. Revert the last change and review the test error. Phase 1 is stable and must remain passing.

---

## Recommended Reading Order

1. **This file (README)** — You are here. (5 min)
2. **TMF_PHASE2_DESIGN.md § Executive Summary** — Understand Phase 2 goals. (10 min)
3. **TMF_PHASE2_ARCHITECTURE.md § Data Flow** — Visualize the architecture. (10 min)
4. **TMF_PHASE2_QUICK_REF.md § K=1 Tasks** — Find WorkerEngine + start coding. (30 min)
5. **Code along** — K=1 implementation (3–4 days)
6. **Repeat 4–5 for K=2, K=3, K=4** — Each K follows same pattern

---

## Support / Escalation

**If you get stuck:**

1. **Grep / Find commands not working?**  
   → Check file paths in `TMF_PHASE2_QUICK_REF.md` § "Phase 1 Existing Files"

2. **WorkerEngine not found after exhaustive search?**  
   → May be in a plugin or a different pattern. Search for "llm_api.complete" or "claude_api" instead.

3. **DB schema questions?**  
   → See `TMF_PHASE2_ARCHITECTURE.md` § "Database Schema (SQLite)"

4. **React component not rendering?**  
   → Check console browser dev tools. Verify API is returning data at `/v1/console/metrics/session/{id}/summary`.

5. **API returns 403 Forbidden?**  
   → Verify `rec.session_id == session_id` check in `routes/metrics.py`. Also check `rec.tenant_id` is set correctly.

6. **Phase 1 tests broken?**  
   → Run `pytest tests/unit/test_token_metrics_phase1_complete.py -v` to identify which test failed. Likely issue: modified a Phase 1 file accidentally.

---

## Related Files (In This Repo)

- **Phase 1 Completion Report:** `docs/Phase1_TMF_Completion.md` (if exists)
- **Token Instrumentation Hooks:** `core/learning/token_instrumentation.py` (reference)
- **Event Schema:** `core/learning/event_schema.py` (reference)
- **Console App Bootstrap:** `core/console/corvin_console/app.py` (modify here)
- **Existing Panels:** `core/console/corvin_console/web-next/src/panels/registry.tsx` (see pattern)

---

## Timeline

```
Week 1 (K=1): WorkerEngine Integration
├─ Day 1–2: Find WorkerEngine, understand its structure
├─ Day 3: Insert 4 hooks, test on one turn
├─ Day 4: Run full E2E, verify metrics in storage
└─ Day 5: Code review + Phase 1 test verification

Week 2 (K=2): DB Backend
├─ Day 1–2: Implement TokenMetricsDB + schema
├─ Day 3: Factory + TokenMetricsStore upgrade
├─ Day 4: App bootstrap + persistence tests
└─ Day 5: Code review + performance testing

Week 3 (K=3): Console Panel
├─ Day 1–2: Create React components (Dashboard + children)
├─ Day 3: useLiveMetrics hook + polling setup
├─ Day 4: Panel registration + styling
└─ Day 5: E2E tests + browser verification

Week 4 (K=4): API Endpoints
├─ Day 1–2: Create metrics.py routes
├─ Day 3: Auth checks + DI integration
├─ Day 4: Register routes + app bootstrap
└─ Day 5: API tests + integration verification

Week 5: Ship + Monitor
├─ Code review + merge to main
├─ Canary rollout (10% users)
└─ Monitor: latency, accuracy, errors
```

---

## Phase 2 Is Done When

- ✅ All 4 Ks are complete
- ✅ No Phase 1 tests broken
- ✅ All new K=1–K=4 tests pass
- ✅ E2E: Navigate to `/console/vibe-metrics` → see live data
- ✅ API: Curl `/v1/console/metrics/session/{id}/summary` → valid JSON
- ✅ DB: Query `~/.corvin/tenants/_default/global/metrics.db` → metrics table has data
- ✅ Docs: Phase 2 completion report written
- ✅ Code review passed

---

## What's Next (Phase 3)

Once Phase 2 ships, Phase 3 will add:

- **K=5:** Advanced Baselines (segment by engine, model, persona)
- **K=6:** Closed-Loop Learning (user feedback → confidence scoring)
- **K=7:** Cost Attribution (tokens → dollars, subsystem costs)
- **K=8:** Anomaly Detection (2σ outliers, token spike alerts)

All built on Phase 2's solid DB + API foundation.

---

**Happy coding! Questions? Start with the grep command in QUICK_REF.md § K=1 Task 1A.**
