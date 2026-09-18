# 🚀 PHASE D AUTONOMOUS EXECUTION MASTER PLAN

**Goal:** Complete Phase D (Autonomous Loop Enablement, ADR-0472) to "wirklich done" status  
**Timeline:** 2026-09-25 to 2026-10-01 (7 days, 28h effort)  
**Owner:** Autonomous agents (no operator intervention)  
**Exit Gate:** Phase E unblocked (SessionAutoStarter fully wired, all LDD gates pass)

---

## 📊 CURRENT STATUS (2026-09-18)

### Implemented (90% complete)
- ✅ **SessionAutoStarter** (core/session_manager/auto_starter.py) — 90% done
  - Task monitoring, split detection, checkpoint creation, retry engine ✅
  - CRITICAL-003 (TaskStateLock), CRITICAL-005 (RetryEngine), CRITICAL-009 (Goal Drift) all fixed ✅
  - Commits: f63510b4 (Phase 1), 534f79ed (10 CRITICAL fixes)

- ✅ **SessionLifecycleManager** — split decision logic ✅
- ✅ **CheckpointManager** — checkpoint persistence ✅
- ✅ **TaskExecutor integration** — on_task_progress() hook ✅

### Missing (10% remaining — THE GAP)
- ❌ **Session Init API** (Line 269 TODO: "Call actual session init API")
  - `init_session_from_checkpoint()` method not implemented
  - Checkpoint state injection not wired
  - Session bootstrap from checkpoint needs completion

- ❌ **VIBE Gate Integration**
  - Audit-first validation (ADR-0232)
  - Fail-closed on goal drift (ADR-0472 spec)
  - Compliance hardening (ADR-0232)

- ❌ **E2E Tests**
  - Long task split + resume scenario
  - Checkpoint injection verification
  - Audit trail validation

- ❌ **ADR-0472 → ACCEPTED**
  - Status still PROPOSED
  - Needs implementation validation

---

## 🎯 EXECUTION STRATEGY (5 Iterations, K_MAX = 5)

### k=1: Session Init API (8h)

**Owner:** Autonomous LDD Agent  
**Gate:** Can TaskExecutor call `init_from_checkpoint()` and restore state?

**Deliverables:**
1. Implement `SessionAutoStarter.init_session_from_checkpoint()` method
   - Create new session context from checkpoint
   - Load goal, context, audit trail
   - Return session_id + handle

2. Implement `SessionAutoStarter._inject_checkpoint_state()` method
   - Restore task state in new session
   - Verify goal continuity (fail-closed)
   - Emit audit event (session_resumed, checkpoint_injected)

3. Implement `SessionAutoStarter._emit_audit_event()` helper
   - Audit-first: write event before state change
   - Hash-chain to previous audit event (ADR-0232)
   - Tenant-scoped, immutable

**Code Changes:**
- File: core/session_manager/auto_starter.py
  - Add ~80 LoC for init_from_checkpoint()
  - Add ~60 LoC for _inject_checkpoint_state()
  - Add ~40 LoC for _emit_audit_event()
  - Replace Line 269 TODO with real call

**Exit Criteria (LDD k=1):**
- [ ] Tier-1 (syntax/lint) green
- [ ] Tier-2 (unit tests) green — 10+ unit tests for init paths
- [ ] Tier-3 (integration) green — checkpoint → session → state restored
- [ ] E2E proof: mock task → split → new session resumes (basic)

---

### k=2: VIBE Gate Hardening (6h)

**Owner:** Autonomous LDD Agent  
**Gate:** Does init_from_checkpoint() enforce audit_first + fail_closed?

**Deliverables:**
1. Add `@audit_first` decorator to SessionAutoStarter methods
   - Write audit event BEFORE state change
   - On audit failure → raise exception (fail-closed)
   - No silent state changes

2. Implement goal drift detection enhancement
   - Compare new goal against checkpoint goal
   - Raise RuntimeError if mismatch (fail-closed)
   - Log drift event (CRITICAL audit signal)

3. Add `@fail_closed` validation
   - Checkpoint integrity check (hash verification)
   - Session init failure → rollback (no partial state)
   - Timeout handling (30min wedge detection)

**Code Changes:**
- File: core/session_manager/auto_starter.py
  - Enhance on_task_progress() goal validation (CRITICAL-009 already done ✅)
  - Add @audit_first wrapper for init_from_checkpoint()
  - Add @fail_closed error handling

**Exit Criteria (LDD k=2):**
- [ ] Tier-2 (unit tests) green — 15+ tests for fail-closed scenarios
- [ ] Tier-3 (integration) green — goal drift rejected, audit trail complete
- [ ] Audit trail validation: every split logged + hash-chained ✅

---

### k=3: Checkpoint Injection Wiring (6h)

**Owner:** Autonomous LDD Agent  
**Gate:** Can TaskExecutor receive new_session_id and continue seamlessly?

**Deliverables:**
1. Wire TaskExecutor.on_task_progress() callback
   - Call SessionAutoStarter.on_task_progress() per iteration
   - Receive new_session_id on split (or None on continue)
   - Update task.current_session_id + audit
   - Continue task loop in new session (if split)

2. Implement session boundary crossing
   - Clean shutdown of old session (flush pending audit events)
   - Bootstrap new session with checkpoint
   - Resume task loop without operator intervention
   - Audit trail spans both sessions (hash-chained)

3. Add context continuity verification
   - Old session checkpoint hash matches new session injected checkpoint
   - Goal matches across boundary
   - Iteration counter continuous (no skips/repeats)

**Code Changes:**
- File: core/task_engine/executor.py
  - Enhance on_task_progress() call site (L76-91, currently basic)
  - Add session_id update logic
  - Add loop-resume logic post-split

**Exit Criteria (LDD k=3):**
- [ ] Tier-3 (integration) green — TaskExecutor + SessionAutoStarter wired end-to-end
- [ ] Audit trail spans session boundary ✅
- [ ] No context loss or duplication

---

### k=4: E2E Test Suite (6h)

**Owner:** Autonomous LDD Agent  
**Gate:** Can a real long task split, resume, and complete correctly?

**Deliverables:**
1. E2E test: Long task with forced split (timeout + context limit)
   - Task goal: "Process 100 items in sequence"
   - Force split at item #50 (via mock context usage)
   - Verify new session continues at item #51
   - Verify completion at item #100
   - Audit trail shows: task_started → split (item 50) → task_resumed → task_completed

2. E2E test: Checkpoint integrity validation
   - Corrupt checkpoint, try to resume → RuntimeError ✅
   - Stale checkpoint, try to resume → RuntimeError ✅
   - Valid checkpoint, resume succeeds ✅

3. E2E test: Goal drift protection
   - Task goal: "Audit codebase"
   - Split triggered, goal changed to "Deploy service" → RuntimeError + audit ✅
   - Goal unchanged → split proceeds ✅

4. E2E test: Audit trail completeness
   - Capture all events across session boundary
   - Verify hash-chain integrity (no gaps)
   - Verify tenant isolation (cross-tenant read forbidden)

**Code Changes:**
- File: tests/e2e/test_phase_d_session_split_e2e.py (NEW, ~300 LoC)
  - 4 E2E scenarios (long task split, integrity, drift, audit)
  - Mock TaskExecutor + SessionAutoStarter
  - Real audit trail verification

**Exit Criteria (LDD k=4):**
- [ ] Tier-4 (E2E) green — all 4 scenarios pass
- [ ] Real session split + resume cycle proven
- [ ] Audit trail validated end-to-end ✅
- [ ] Operator never needed to intervene

---

### k=5: Docs + ADR-0472 Promotion (4h)

**Owner:** Autonomous LDD Agent  
**Gate:** Is Phase D "wirklich done" — all LDD gates passed, ADR accepted, docs current?

**Deliverables:**
1. Update ADR-0472
   - Status: PROPOSED → ACCEPTED
   - Add `commits:` field (all Phase D commits)
   - Update `paths:` with implementation locations
   - Add `audit_events:` list (all audit event types logged)
   - Amendment: Implementation complete, all CRITICAL fixes validated

2. Docs updates
   - docs/claude-ref/autonomy.md (new or update)
     - SessionAutoStarter architecture + phases
     - Checkpoint injection flow
     - Fail-closed guarantees
     - GDPR compliance notes
   - docs/claude-ref/compliance-baseline.md
     - Reference ADR-0472 for autonomous session compliance
   - PHASE_D_COMPLETION_REPORT.md (new)
     - Executive summary: SessionAutoStarter fully wired
     - Metrics: 28h planned, actual hours, speedup
     - LDD gates: all k=1-5 passed
     - E2E tests: 4/4 scenarios green
     - Audit trail: 100% hash-chained

3. Update Memory
   - Session 6 status → Phase D COMPLETE
   - ADR-0472 ACCEPTED link
   - Phase E ready-to-kickoff marker

**Exit Criteria (LDD k=5):**
- [ ] ADR-0472 ACCEPTED + committed
- [ ] docs/ synchronized with implementation
- [ ] Memory.md updated
- [ ] PHASE_D_COMPLETION_REPORT.md written
- [ ] All LDD gates k=1-5 verified ✅

---

## 🎯 SUCCESS METRICS

| Metric | Target | Acceptance Criteria |
|--------|--------|-------------------|
| **SessionAutoStarter wiring** | 100% | init_from_checkpoint() + injection working end-to-end |
| **Checkpoint injection** | 100% | state restored in new session, no loss, no duplication |
| **VIBE gates** | 100% | audit_first + fail_closed enforced, goal drift detected |
| **E2E test coverage** | 4/4 scenarios | long task split, integrity check, drift protection, audit trail |
| **LDD gates** | k=1-5 all green | all tiers passing, no K_MAX violations |
| **Audit trail** | 100% hash-chained | every event audited, tenant-scoped, immutable |
| **ADR-0472 status** | ACCEPTED | decision documented, implementation validated |
| **Phase E gate** | UNBLOCKED | SessionAutoStarter + checkpoint injection ready for E |

---

## 📋 BLOCKERS & MITIGATIONS

| Blocker | Risk | Mitigation |
|---------|------|-----------|
| Session Init API design ambiguity | LOW | ADR-0472 spec clear, SessionLifecycleManager API exists as template |
| Checkpoint state size (large contexts) | MEDIUM | CheckpointManager has compression; test with >50MB context |
| Goal drift detection false positives | LOW | ADR-0472 spec: exact hash match required; covered by CRITICAL-009 fix |
| Audit chain performance | LOW | Hash-chain is one SHA256 per event; ~1ms overhead per split |
| Cross-session state consistency | MEDIUM | Snapshot + hash verification; E2E test validates |

---

## 🚀 NEXT STEPS (After Phase D Complete)

### Phase E Kickoff (2026-10-01)
- **What:** OS-Skills Marketplace (18 initiatives, 4 tiers)
- **Why:** Autonomous sessions enable long-running skill optimization loops
- **Gate:** SessionAutoStarter live + E2E tested + Phase D metrics green

### Operator Tasks (Phase D→E transition)
- [ ] Approve ADR-0472 ACCEPTED status (review metrics)
- [ ] Approve Phase E kickoff (review marketplace design)
- [ ] Monitor audit trail (weekly compliance check)

---

## 📊 BUDGET TRACKING

| Phase | Effort | Timeline | Status |
|-------|--------|----------|--------|
| k=1 (Session Init API) | 8h | 2026-09-25→26 | ⏳ QUEUED |
| k=2 (VIBE Gates) | 6h | 2026-09-26→27 | ⏳ QUEUED |
| k=3 (Checkpoint Wiring) | 6h | 2026-09-27→28 | ⏳ QUEUED |
| k=4 (E2E Tests) | 6h | 2026-09-28→29 | ⏳ QUEUED |
| k=5 (Docs + ADR) | 4h | 2026-09-29→30 | ⏳ QUEUED |
| **TOTAL** | **28h** | **2026-09-25→10-01** | **🟢 READY** |

---

**Phase D Status:** 🟢 **READY FOR AUTONOMOUS EXECUTION**  
**Starting:** 2026-09-25 (immediately after Phase C → 100%)  
**Completion Target:** 2026-10-01 (7 days, all LDD gates green)  
**Phase E Unblocked:** ✅ SessionAutoStarter + checkpoint injection production-ready

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
