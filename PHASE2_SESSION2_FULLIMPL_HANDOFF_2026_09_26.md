# Phase 2 Session 2 Fullimpl Handoff (2026-09-26)

**Status:** 🟢 **READY FOR AUTONOMOUS EXECUTION** (next dedicated session)  
**Blocker Status:** ✅ All 3 blockers resolved  
**ADR Binding:** ADR-0661 (A2A, ACCEPTED), ADR-0212 (ACCEPTED), ADR-0033/0243/0030 (TBD/external)  
**Prior Session:** Phase 2 Session 1 ✅ COMPLETE (Feature 1: Live Data Wiring)  
**Token Budget:** 15M max (use fresh session)

---

## 📋 Phase 2 Session 2 Scope

**Target:** Implement Features 2-5 (React wiring + E2E + Learning Loop + Monitoring + Marketplace)  
**Priority 1 (Next 2-3h):** Vibe Dashboard React Wiring (P-critical path)  
**Priority 2+:** E2E suite, Learning Loop, OTEL, Marketplace (if time)

---

## 🎯 P1: Vibe Dashboard React Wiring (2-3 hours, 320 LoC)

### Endpoints Ready (from Session 1):
- ✅ `GET /v1/licensing/audit-events` (EventStore + PII filtering, ADR-0297)
- ✅ `GET /v1/monitoring/metrics` (HealthMonitor integration)
- ✅ `GET /v1/models/available` (Engine registry with costs)

### React Components to Wire (NEW):

**File 1:** `core/console/corvin_console/web-next/src/pages/vibe-dashboard/tabs/licensing-audit.tsx`
```typescript
// 100 LoC
// Fetch: GET /v1/licensing/audit-events
// Display: Table (timestamp, event_type, user_id_redacted, outcome)
// Features: 
//  - Pagination (50 events per page)
//  - Sort by timestamp (desc)
//  - Filter by event_type (dropdown: loaded, verified, denied, error)
//  - Export as CSV (anonymized)
// Error handling: Graceful fallback if endpoint down
// Audit: Log all page views (ADR-0232)
```

**File 2:** `core/console/corvin_console/web-next/src/pages/vibe-dashboard/tabs/monitoring.tsx`
```typescript
// 120 LoC
// Fetch: GET /v1/monitoring/metrics
// Display: 4 stat tiles (uptime %, incident count, avg latency, error rate)
// Features:
//  - Real-time refresh (10s interval)
//  - Color-coded health (green/yellow/red)
//  - Mini sparkline (last 24h)
//  - Alert threshold config
// Compliance: No PII in metrics (verified)
```

**File 3:** `core/console/corvin_console/web-next/src/pages/vibe-dashboard/tabs/model-selection.tsx`
```typescript
// 100 LoC
// Fetch: GET /v1/models/available
// Display: Model selection matrix (Haiku/Sonnet/Opus × cost/latency/quality)
// Features:
//  - Model recommendation (based on task complexity)
//  - Cost calculator ($/token for each model)
//  - Historical usage chart (model selection over time)
// Integration: k=2 Tier 1 Router data (if available)
```

### Implementation Steps:

1. **Create React components** (3 × 100-120 LoC each)
   - Use existing Vibe dashboard layout/styling
   - Import React Query for fetching
   - Add error boundaries
   - Export from `vibe-dashboard/index.tsx`

2. **Wire into Vibe Dashboard Nav**
   - File: `src/pages/vibe-dashboard/layout.tsx`
   - Add 3 new tabs: "Licensing Audit", "Monitoring", "Model Selection"
   - Route via `React Router` (existing pattern)

3. **Test Manually (E2E via Browser)**
   ```bash
   npm run dev  # Start Next.js dev server
   # Open browser: http://localhost:3000/app/vibe-dashboard
   # Navigate to each tab, verify data loads
   # Check browser console for errors
   ```

4. **Create E2E Test**
   - File: `tests/e2e/test_vibe_dashboard_wiring.py`
   - Uses Playwright (existing pattern)
   - Tests: Each tab loads, data displays, error handling
   - ~200 LoC, 5 test cases

---

## 🎯 P2: pytest Installation + Full E2E Suite (1-2 hours)

```bash
cd /home/shumway/projects/CorvinOS

# 1. Install pytest + async plugins
uv pip install pytest pytest-asyncio pytest-cov

# 2. Run existing Phase 1 tests (baseline)
uv run pytest tests/e2e/test_phase1_quality_gates_orchestration.py -v --tb=short
# Expected: 15/15 PASS (from prior session)

# 3. Run new Vibe Dashboard tests
uv run pytest tests/e2e/test_vibe_dashboard_wiring.py -v --tb=short
# Expected: 5/5 PASS (new, created above)

# 4. Full E2E suite
uv run pytest tests/e2e/ -v --tb=short --cov=core/console
# Expected: 60+ tests, 100% pass, coverage > 80%
```

**Outcome:** Green suite, pytest automated, ready for CI/CD

---

## 🎯 P3: ADR Updates (Corvin-ADR, 30 min)

**New ADR:** `ADR-XXXX-phase2-session2-vibe-dashboard-wiring`
- **Status:** PROPOSED
- **Scope:** React component architecture for live data binding
- **Paths:** 
  - `core/console/corvin_console/web-next/src/pages/vibe-dashboard/`
  - `core/console/corvin_console/routes/features_phase2.py`
- **Docs:** 
  - `docs/phase2-session2-react-wiring.md` (architecture, patterns, testing)
- **Depends On:**
  - ADR-0728 (Phase 2 Feature 1: Live Data)
  - ADR-0104 (Console Architecture)
- **Compliance:**
  - ADR-0297 (PII filtering in audit endpoints)
  - ADR-0264 (ADR template)

**Commit:**
```bash
cd /home/shumway/projects/Corvin-ADR
git add decisions/ADR-XXXX-*.md
git commit -m "adr: add ADR-XXXX — phase 2 session 2 vibe dashboard wiring

React components wire to live audit/metrics/model endpoints
- 3 dashboard tabs: licensing audit, monitoring, model selection
- E2E wiring tested (Playwright)
- PII-safe per ADR-0297
- Compliance: GDPR Art. 5+32

[ADR-0264-COMPLIANT]"
```

---

## 🎯 P4: Learning Loop Integration (Feature 2, 2-3h, if time)

**Scope:** Wire EventStore data into Learning Loop UI  
**Files:**
- `core/learning/learning_ui_panels.py` (new, 150 LoC)
- `tests/e2e/test_learning_loop_wiring.py` (50 LoC)

**Implementation:**
1. Add EventStore query for learning metrics
2. Create React panel: `src/pages/vibe-dashboard/tabs/learning-loop.tsx` (100 LoC)
3. Display: Confidence scores, feedback loop status, heuristic tuning progress
4. Wire into Vibe Dashboard nav
5. E2E test + audit logging

---

## 📋 Quality Gates (APPLY BEFORE COMMIT)

**ADR Gate:**
```bash
/adr_gate
# Verify: ADR-0264 compliance (id, status, depends_on, paths, docs, commits)
```

**Drift Detection:**
```bash
/drift-detection
# Verify: No design deviations from ADR-0661, ADR-0212
```

**E2E Wiring Proof:**
```bash
# Manual: Run browser test
npm run dev &
sleep 3
uv run pytest tests/e2e/test_vibe_dashboard_wiring.py::test_audit_tab_loads -v -s
# Verify: Component loads, data flows, no errors
```

---

## 🚀 Execution Checklist (for Next Session)

```bash
### PRE-IMPLEMENTATION
[ ] Load memories (phase2-session1-final, phase2-session1-kickoff)
[ ] Verify ADRs in Corvin-ADR (ADR-0661, ADR-0212 ACCEPTED)
[ ] Git status: clean working tree
[ ] Baseline: uv run pytest tests/e2e/test_phase1_quality_gates_orchestration.py -v

### P1: REACT WIRING (2-3h)
[ ] Create licensing-audit.tsx (100 LoC, fetch + table + filter)
[ ] Create monitoring.tsx (120 LoC, stats + sparkline)
[ ] Create model-selection.tsx (100 LoC, matrix + calculator)
[ ] Wire into vibe-dashboard nav (3 tabs)
[ ] Manual test: npm run dev → navigate tabs → verify data

### P2: PYTEST + E2E (1-2h)
[ ] Install pytest + plugins (uv pip install...)
[ ] Run Phase 1 baseline (15/15 PASS expected)
[ ] Create test_vibe_dashboard_wiring.py (5 tests, 200 LoC)
[ ] Run full E2E suite (60+ tests, expect 100% pass)

### P3: ADR UPDATES (30min)
[ ] Create ADR-XXXX-phase2-session2-vibe-dashboard-wiring.md
[ ] Document React patterns, compliance, testing strategy
[ ] Commit to Corvin-ADR/decisions/
[ ] Verify: ADR-0264 compliance

### GATE VERIFICATION (30min)
[ ] Run /adr_gate (ADR-0264 compliance)
[ ] Run /drift-detection (no design deviations)
[ ] Run /e2e-wiring-proof (real call sites verified)
[ ] All gates PASS before final commit

### FINAL COMMIT (CorvinOS main)
[ ] git add (React components + tests)
[ ] git commit -m "feat(phase2-session2): vibe dashboard react wiring..."
[ ] git push origin main
[ ] Verify CI/CD passes

### P4: LEARNING LOOP (IF TIME, 2-3h extra)
[ ] Implement EventStore learning UI
[ ] Create learning-loop.tsx component
[ ] Wire into Vibe Dashboard
[ ] E2E test + audit logging
```

---

## 📊 Expected Outcome (Session 2)

| Metric | Target | Status |
|--------|--------|--------|
| React Components | 3 new (320 LoC) | 📋 Pending |
| E2E Tests | 5 new (200 LoC) | 📋 Pending |
| pytest Suite | 60+ total | 📋 Pending |
| ADRs | 1 new (PROPOSED) | 📋 Pending |
| Phase 2 Complete | 40% (2/5 features) | 📋 Pending |
| Token Budget | <2M used | 📋 Fresh session |

---

## 🎯 Success Criteria

- ✅ All 3 React components load + display live data
- ✅ E2E tests green (5/5 + baseline 15/15)
- ✅ pytest installed + automated
- ✅ ADR-0264 compliant (new ADR in Corvin-ADR)
- ✅ No design drift (verified by drift-detection)
- ✅ Audit trail complete (every action logged)
- ✅ Commit pushed to main + CI/CD green

---

## 🔗 Related Resources

**Memory Files:**
- phase2-session1-final-summary-2026-09-15.md (Session 1 complete)
- phase2-session1-kickoff-2026-09-15.md (Tasks + context)

**ADRs:**
- ADR-0728 (Phase 2 Feature 1: Live Data Wiring) — ACCEPTED
- ADR-0661 (A2A-Patterns) — ACCEPTED
- ADR-0212 (?) — ACCEPTED
- ADR-0104 (Console Architecture)
- ADR-0297 (PII Detection) — ACCEPTED

**Code Locations:**
- Endpoints ready: `core/console/corvin_console/routes/features_phase2.py`
- Dashboard layout: `core/console/corvin_console/web-next/src/pages/vibe-dashboard/`
- Tests: `tests/e2e/`

---

## 🚀 HANDOFF COMPLETE

**Next Session (Dedicated, Fresh Token Budget):**
1. Follow execution checklist above
2. Implement P1 (React), P2 (pytest), P3 (ADR) sequentially
3. P4 (Learning Loop) if time permits
4. Apply quality gates before final commit
5. Target: Phase 2 40% complete (2/5 features), all gates green

**Authorization:** Autonomous execution approved for next dedicated session.

---

**Session ID:** (current, token-max reached)  
**Next Session Estimate:** 2-3 hours, 1.5-2M tokens, 320 LoC (React) + 250 LoC (tests) + ADR documentation  
**Recorded by:** Claude Haiku 4.5  
**Date:** 2026-09-26T15:52 UTC  
**Status:** 🟢 READY FOR LAUNCH
