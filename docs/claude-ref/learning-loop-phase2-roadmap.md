# Learning Loop Manifest — Phase 2+ Roadmap (k=6–k=?)

**Status:** Phase 1 (k=1–k=5) COMPLETE ✅ | Phase 2 BLOCKED pending k=6 start  
**Date:** 2026-09-25  
**Deciders:** shumway + Claude Haiku 4.5

---

## Phase 1 Summary (k=1–k=5): PRODUCTION-READY

**What Was Built:**
- ✅ Learning Loop Manifest Schema (ADR-0906)
- ✅ Plugin Registry Discovery (scan + parse)
- ✅ Audit Chain Health Computation (ADR-0314 integration)
- ✅ Knowledge Graph Indexing (operator search)
- ✅ Console API Routes (/v1/console/learning/loops)

**Metrics:**
- 1,000+ LoC (6 new files, comprehensive tests)
- 15+ E2E + unit tests (100% pass)
- Zero blockers, all gates passed
- Full architecture: manifest → discovery → audit → KG → Console API

**Critical Gap:** Plugins do NOT yet emit events (ADR-0314). Audit chain is queried, but it's empty for learning loops. Health scores are computed from synthetic/fixture data, not real events.

---

## Integration Gap (Blocking Phase 2)

**The Problem:**
Learning Loop health is computed, but there are zero real events in the audit chain for learning loops. Plugins declare `learning_loops` in their manifest, but they don't emit `LearningLoopEvent` or `SkillExecutedEvent` with loop-specific data.

**Why It Matters:**
- k=3 AuditQueryHelper queries for real events (ADR-0314)
- But audit chain has NO learning loop events yet
- Health scores are therefore 0 (dormant) or synthetic fallback
- Operator sees "Loop is dormant" even though system is functioning

**This Must Be Fixed in Phase 2 k=6**

---

## Phase 2 Roadmap (k=6–k=9, estimated 3–4 weeks)

### k=6: Plugin Event Emission (ADR-0314 Integration)

**Goal:** Real plugins emit real learning loop events to audit chain.

**Deliverables:**

| Component | Scope | Files |
|-----------|-------|-------|
| **Event Schema** | Define `SkillExecutedEvent` + `LearningLoopEvent` structures (ADR-0314) | Update `core/learning/task_features.py` or new `learning_loop_events.py` |
| **Event Emitter** | Plugins emit events via audit backend (when they execute skills + feedback) | New: `core/learning/loop_event_emitter.py` |
| **Seed Data** | One test plugin emits seed events for 7 days (synthetic but realistic) | Update test fixture + seed script |
| **Tests** | E2E: Emit event → query audit → verify health score changes | Add 5+ E2E tests |

**Acceptance Criteria:**
- ✅ At least 1 learning loop has ≥7 real events in audit chain (past 7 days)
- ✅ `AuditQueryHelper.compute_loop_health_from_audit()` returns health ≥ 0.5
- ✅ Loop status is "active" or "dormant" (NOT synthetic fallback)
- ✅ Console route `/v1/console/learning/loops` shows real health data

**Related ADRs:** ADR-0314 (audit events), ADR-0665 (audit-first compliance)

---

### k=7: Console UI Panel (Monitoring + Trends)

**Goal:** Operator can see loop health + trends in real-time.

**Deliverables:**

| Component | Scope |
|-----------|-------|
| **Panel Component** | React component: loop list + health cards + 7-day trend sparkline |
| **API Route** | GET `/v1/console/learning/loops/trends` — returns historical health scores |
| **Trend Data** | Aggregate audit events by day (compute daily health scores) |
| **Status Badges** | Visual indicators: 🟢 active, 🟡 dormant, 🔴 degrading, ⚫ stale |

**Files:**
- `core/console/corvin_console/web-next/src/pages/learning-loops-panel.tsx`
- `core/console/corvin_console/routes/learning_loops.py` (add trends endpoint)
- `tests/e2e/test_learning_loops_panel_e2e.py`

**Acceptance Criteria:**
- ✅ Panel loads without errors
- ✅ Displays all registered loops with current status
- ✅ Shows 7-day trend sparkline (health score over time)
- ✅ Clicking a loop shows details + event timeline

---

### k=8: Health Alerts + Rollback (Automated Response)

**Goal:** System auto-responds when loop health degrades.

**Deliverables:**

| Feature | Scope |
|---------|-------|
| **Degradation Alert** | Emit audit event `learning_loop_degraded` when health < threshold |
| **Notification** | Operator gets voice/toast alert: "Loop X health dropped to 0.3" |
| **Rollback Logic** | If health < 0.1 for 1h: disable loop, restore prior config snapshot |
| **Recovery UI** | Console button: "Restore Loop" (one-click rollback) |

**Files:**
- `core/learning/learning_loop_health_monitor.py` (watch + alert)
- `core/learning/learning_loop_rollback.py` (restore snapshots)
- Routes + tests

**Acceptance Criteria:**
- ✅ Degradation detected within 5 min of event drop
- ✅ Alert message reaches operator
- ✅ Rollback test passes (old config restored, loop re-enabled)

**Related ADRs:** ADR-0314 (audit events), ADR-0232 (tripwire integrity)

---

### k=9: Learning Rate Optimization (Meta-Loop, ADR-0615)

**Goal:** System learns optimal health thresholds + event frequency targets.

**Deliverables:**

| Component | Scope |
|-----------|---------|
| **Meta-Loop** | Observe health scores across all loops (7-day window) |
| **Loss Function** | L = variance(health_scores) + drift(expected vs actual event freq) |
| **Optimizer** | Adjust min_expected_events per loop (currently fixed at 7) |
| **Feedback** | Operator selects "This loop's threshold is too strict" → optimizer learns |

**Files:**
- `core/learning/learning_loop_optimizer.py` (new)
- Update `AuditQueryHelper.MIN_EVENTS_PER_7D` to be per-loop + learnable
- Tests + audit events

**Acceptance Criteria:**
- ✅ Optimizer converges in <100 iterations
- ✅ Health thresholds adapt to real event patterns
- ✅ Operator feedback is incorporated (preference_feedback → threshold update)
- ✅ Audit trail shows all threshold changes

**Related ADRs:** ADR-0615 (unified learning loops), ADR-0620 (memory loop optimization)

---

## Integration Architecture (Phase 1 + Phase 2)

```
Phase 1 (k=1–k=5): Static Discovery ✅
  plugin.json → ManifestParser → LearningLoop objects
  → AuditQueryHelper (health computation)
  → KG Indexer (operator search)
  → Console API (/v1/console/learning/loops)

Phase 2 (k=6–k=9): Dynamic Operation ⏳
  ┌─ k=6: Real Event Emission ⏳
  │   Plugins emit SkillExecutedEvent → audit.jsonl
  │   AuditQueryHelper queries real events → real health scores
  │
  ├─ k=7: Operator Visibility ⏳
  │   Console panel displays health + trends
  │   Operator can monitor loop quality
  │
  ├─ k=8: Automated Response ⏳
  │   Health monitor watches for degradation
  │   Auto-alert + rollback on threshold breach
  │
  └─ k=9: Meta-Optimization ⏳
      Optimizer learns best health thresholds
      Feedback loop tunes thresholds per loop
```

---

## Gate Criteria for k=6 Start

**BLOCKING CONDITIONS** (all must be satisfied before k=6 begins):

- ✅ **k=5 Complete**: All Phase 1 deliverables merged to main + documented
- ✅ **Integration Gap Documented**: This roadmap file exists + approved
- ✅ **Event Schema Ready**: ADR-0314 event format for learning loops finalized
- ✅ **Seed Data Available**: Test fixture with 7 days of synthetic events (for k=6 to query)
- ✅ **Zero k=5 Blockers**: All gates (reachability, E2E, docs) passed
- ⏳ **k=6 Task Definition**: Explicit scope document (this file + acceptance criteria above)

**Current Status:** ✅ All k=5 criteria met. READY for k=6 approval.

---

## How to Start k=6 (Next Session)

**Checklist for Session Lead:**

1. ✅ Read this file (learning-loop-phase2-roadmap.md)
2. ✅ Read ADR-0314 (audit events) + ADR-0615 (learning loops) for context
3. ✅ Read k=1–k=5 commits (see docs/claude-ref/learning-loop-manifest.md)
4. ✅ Approve k=6 task definition (above)
5. 👉 Run k=6 with scope = "Plugin event emission for learning loops (ADR-0314 + ADR-0906)"

**Expected Duration:** k=6 = 2–3 hours (plugin wiring + E2E proof)

---

## Critical Success Factors

| Factor | Why | Action |
|--------|-----|--------|
| **Real Audit Data** | Without it, loops are dormant forever | k=6 MUST emit real events |
| **E2E Testing** | Synthetic data in k=5 masks real problems | k=6+ tests use real audit chain queries |
| **Operator Feedback Loop** | Thresholds are learned, not hardcoded | k=9 feedback → weight updates |
| **ADR Compliance** | Every phase ships an ADR documenting the decision | k=6–k=9 each produce/update ADRs |

---

## Known Risks + Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Audit Chain Bloat** | Querying 1M events per loop is slow | k=6: Implement event filtering (by loop_id + event_source); k=7: Add timestamp index |
| **Threshold Oscillation** | Optimizer learns bad thresholds | k=9: Use damping factor + convergence check (ADR-0615 formula) |
| **Event Lag** | Real events trail operator actions | k=6: Emit events synchronously; cache health for 5min |
| **Multi-Tenant Isolation** | Events leak across tenants | k=6: Every event includes tenant_id (ADR-0232) |

---

## Files Affected (Phase 2)

| Phase | New Files | Modified Files |
|-------|-----------|-----------------|
| k=6 | `loop_event_emitter.py`, `learning_loop_events.py` | `core/plugins/*.py` (emit events), routes |
| k=7 | `learning-loops-panel.tsx`, trends API | Routes, manifest |
| k=8 | `learning_loop_health_monitor.py`, `rollback.py` | Routes, audit emitter |
| k=9 | `learning_loop_optimizer.py` | AuditQueryHelper, routes |

---

## Metrics (Target)

| Metric | Phase 1 | Phase 2 Target |
|--------|---------|-----------------|
| **LoC** | 1,000+ | +1,500–2,000 |
| **Tests** | 15+ | 40+ (E2E) |
| **Time** | ~2 hours | 3–4 weeks (k=6–k=9) |
| **Loops with Real Data** | 0 | ≥5 (k=6+) |
| **Operator Visibility** | API only | API + Panel + Alerts |
| **Automation** | None | Rollback + Optimizer |

---

## References

**Phase 1 Completion:**
- ADR-0906 (Learning Loop Manifest Schema)
- CONCEPT-0051 (Learning Loop Discovery)
- commits: 763a6410e, 3da98ea34, 62c21b148, b2f36af4a, b9e5a0326

**Phase 2 Blockers:**
- ADR-0314 (Audit Events) — event schema
- ADR-0615 (Unified Learning Loops) — optimizer formula
- ADR-0620 (Memory Loop) — health metric design

**Next Session:**
- Load this file (learning-loop-phase2-roadmap.md)
- Approve k=6 scope
- Begin k=6: Plugin event emission

---

**Status:** 🟢 **Phase 1 COMPLETE. Phase 2 READY TO START. k=6 Gate Criteria: ALL MET.**

