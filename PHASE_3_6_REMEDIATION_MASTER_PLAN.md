# PHASE 3–6 REMEDIATION MASTER PLAN — Fix All 18 CRITICAL Findings

**Status:** 🟢 PHASE 3 STARTED  
**Timeline:** Sep 24 – Oct 13, 2026 (3 weeks)  
**Target:** 18/18 CRITICAL findings fixed + verified

---

## EXECUTIVE SUMMARY

**Parallel Remediation Strategy:**
- 5 simultaneous streams (Security, Architecture, Compliance, Testing, Production)
- Each fixes assigned CRITICAL findings
- Each adds unit tests + re-audit verification
- Coordinated weekly commits (Fridays: Sep 30, Oct 7, Oct 14)

**Outcome:** By Oct 13, all 18 CRITICAL findings fixed + re-audited, zero regressions

---

## REMEDIATION STREAM ASSIGNMENTS

### STREAM 1: SECURITY (5 CRITICAL Findings)

**Assigned Issues:**
1. **S-002: Consent Decorator Gaps** (50+ routes missing decorator)
   - **Work:** Audit all routes, apply `@consent_required()` to missing ones
   - **Tests:** Verify decorator present + consent checks emit events
   - **Timeline:** Sep 24–27
   - **Commits:** Add decorators + tests + CI/CD gate

2. **S-004: Cross-Tenant Audit Leakage** (queries don't filter by tenant_id)
   - **Work:** Audit all `audit_backend.query()` calls, add tenant_id filters
   - **Tests:** test_cross_tenant_audit_isolation_enforced()
   - **Timeline:** Sep 28–Oct 1
   - **Commits:** Fix queries + add tests + CI/CD gate

3. **S-005: No Rate Limiting on Auth** (brute force risk)
   - **Work:** Add rate limiting middleware (10 attempts/min per IP)
   - **Tests:** test_auth_rate_limiting_enforced()
   - **Timeline:** Oct 1–3
   - **Commits:** Add middleware + tests

**Already Fixed:**
- S-001: NameError in is_active() ✅ (Sep 23, commit 91e076de)
- S-003: Missing scope validation ✅ (Sep 23, commit 91e076de)

**Stream Lead:** Security Lead (TBD)

---

### STREAM 2: ARCHITECTURE (4 CRITICAL Findings)

**Assigned Issues:**

1. **A-002: Circular Dependency (Audit ↔ Consent)**
   - **Work:** Create `audit_events.py` (neutral event defs), break cycle
   - **Tests:** Verify no circular imports
   - **Timeline:** Sep 24–Oct 1
   - **Approach:** 
     - Extract event definitions to separate module
     - audit_backend imports from audit_events (not consent)
     - consent_store imports from audit_events (not audit)
     - Both emit independently

2. **A-001: Layer Violation (Plugin → Console)**
   - **Work:** Block backwards imports, create plugin API interface
   - **Tests:** CI/CD gate to prevent plugin→console imports
   - **Timeline:** Oct 1–4
   - **Approach:**
     - Create `PluginAPI` ABC (safe operations only)
     - Routes call DOWN to plugins via API
     - No imports UP to console

3. **A-003: No Protocol Versioning (A2A)**
   - **Work:** Add protocol_version field to all A2A messages
   - **Tests:** test_a2a_version_mismatch_handled()
   - **Timeline:** Oct 4–7
   - **Approach:**
     - Add version field to message schema
     - Receiver checks version (fail-closed on mismatch)
     - Document upgrade path

4. **A-004: No Plugin Lifecycle Interface**
   - **Work:** Define PluginLifecycle ABC, validate all plugins conform
   - **Tests:** test_plugin_implements_lifecycle_interface()
   - **Timeline:** Oct 7–10
   - **Approach:**
     - Create PluginLifecycle ABC with required methods
     - Loader validates conformance at boot
     - Enforce via CI/CD test

**Stream Lead:** Architecture Lead (TBD)

---

### STREAM 3: COMPLIANCE (3 CRITICAL Findings)

**Assigned Issues:**

1. **C-001: Audit Trail Not Mandatory**
   - **Work:** Add assertions to critical code paths (fail-closed without audit)
   - **Tests:** test_audit_event_emitted_on_consent()
   - **Timeline:** Sep 24–29
   - **Approach:**
     - Identify critical paths (auth, consent, plugins, data access)
     - Add `assert_audit_logged()` calls
     - Fail-closed if audit write fails

2. **C-002: No GDPR Art. 17 Erasure Workflow**
   - **Work:** Implement user data erasure + audit trail cleanup
   - **Tests:** test_user_erasure_complete(), test_audit_trail_cleaned()
   - **Timeline:** Sep 29–Oct 6
   - **Approach:**
     - Create `user_erasure.py` module
     - Erase: consent records, context, audit trail
     - Log erasure event (immutable proof)
     - GDPR Art. 17 compliance

3. **C-003: No Bot Disclosure (EU AI Act)**
   - **Work:** Show disclosure card on first use, enforce opt-out
   - **Tests:** test_bot_disclosure_shown_on_first_login()
   - **Timeline:** Oct 6–10
   - **Approach:**
     - Add disclosure modal to auth flow
     - Non-dismissible on first login
     - `/pass` and `/leave` commands functional
     - Audit every disclosure display

**Stream Lead:** Compliance Officer (TBD)

---

### STREAM 4: TESTING (4 CRITICAL Findings)

**Assigned Issues:**

1. **T-001: Console Module 40% Tested**
   - **Work:** Write 100+ new unit tests for console routes
   - **Target:** Coverage 40% → 80%+
   - **Timeline:** Sep 26–Oct 8 (ongoing, parallelizable)
   - **Approach:**
     - Identify untested routes (grep for 100% coverage)
     - Write test for each: happy path + error cases
     - Measure coverage incrementally

2. **T-002: No E2E Consent Gate Test**
   - **Work:** Write E2E test: request without consent → 403
   - **Tests:** test_consent_gate_blocks_request_e2e()
   - **Timeline:** Sep 26–29
   - **Approach:**
     - HTTP POST to route without consent
     - Verify 403 Forbidden response
     - Verify audit event logged

3. **T-003: Plugin Lifecycle Untested**
   - **Work:** Write E2E test for plugin load→execute→cleanup
   - **Tests:** test_plugin_lifecycle_end_to_end()
   - **Timeline:** Sep 29–Oct 3
   - **Approach:**
     - Load test plugin
     - Execute plugin
     - Cleanup (verify no residual state)
     - Verify audit events at each step

4. **T-004: A2A Message Handling Untested**
   - **Work:** Write E2E test for A2A request→process→response
   - **Tests:** test_a2a_end_to_end_message_handling()
   - **Timeline:** Oct 3–8
   - **Approach:**
     - Send A2A message (actual network)
     - Verify processing
     - Verify response received
     - Verify audit trail

**Stream Lead:** QA Lead (TBD)

---

### STREAM 5: PRODUCTION (2 CRITICAL Findings)

**Assigned Issues:**

1. **P-001: No Deployment Runbook**
   - **Work:** Write deployment procedure + test it
   - **Tests:** test_deploy_procedure_works_end_to_end()
   - **Timeline:** Sep 26–Oct 1
   - **Deliverable:** `docs/operations/DEPLOYMENT_RUNBOOK.md`
   - **Includes:** Pre-deploy checklist, deploy steps, post-deploy verification, rollback

2. **P-002: Monitoring Missing**
   - **Work:** Deploy monitoring stack (Prometheus + Grafana)
   - **Tests:** test_metrics_collected(), test_alerts_working()
   - **Timeline:** Oct 1–8
   - **Deliverable:** Monitoring dashboard + alert rules
   - **Metrics:** Latency, errors, throughput, resource usage, audit trail health

**Stream Lead:** Ops Lead (TBD)

---

## PHASE 3 WEEKLY MILESTONES

### Week 1 (Sep 24–29): Foundation
- **Friday Sep 30:** 1st Weekly Report
- **Targets:**
  - Security: S-002 fixed (consent decorators)
  - Architecture: A-002 started (circular dep analysis)
  - Compliance: C-001 started (audit assertions)
  - Testing: T-001 + T-002 tests written (100+ tests)
  - Production: P-001 runbook drafted

**Expected Progress:** 25% of CRITICAL fixes complete (4.5/18)

### Week 2 (Sep 30–Oct 6): Mid-Course
- **Friday Oct 7:** 2nd Weekly Report
- **Targets:**
  - Security: S-002 complete + S-004 in progress
  - Architecture: A-002 complete + A-001 in progress
  - Compliance: C-001 complete + C-002 in progress
  - Testing: T-001, T-002, T-003 tests added (50+ new tests)
  - Production: P-001 tested, P-002 started

**Expected Progress:** 60% of CRITICAL fixes complete (10.8/18)

### Week 3 (Oct 7–13): Completion
- **Friday Oct 14:** 3rd Weekly Report + Re-audit Results
- **Targets:**
  - Security: All 3 fixes (S-002, S-004, S-005) complete
  - Architecture: All 4 fixes (A-001–004) complete
  - Compliance: All 3 fixes (C-001–003) complete
  - Testing: T-001–004 complete (100+ new tests, coverage >80%)
  - Production: P-001 + P-002 complete

**Expected Progress:** 100% of CRITICAL fixes complete (18/18)

---

## QUALITY GATES (Per Fix)

**Every CRITICAL fix must pass:**

1. ✅ **Code Review:** Peer review + architecture consistency
2. ✅ **Unit Tests:** New tests for the fix (min 2 per fix)
3. ✅ **Integration Tests:** Verify fix works with other systems
4. ✅ **E2E Tests:** Real transport/interface boundary (if applicable)
5. ✅ **Regression Tests:** No existing tests broken
6. ✅ **Audit Verification:** Audit events logged correctly
7. ✅ **CI/CD Pass:** All automation passes

**Fail any gate → fix not merged**

---

## PARALLEL EXECUTION MODEL

**5 streams work independently, weekly sync points:**

```
Sep 24                Oct 1                Oct 8               Oct 13
|                     |                    |                   |
Stream 1 ────────────────────────────────────────────────────── DONE
Stream 2 ─────────────────────────────────────────────────────── DONE
Stream 3 ────────────────────────────────────────────────────── DONE
Stream 4 ──────────────────────────────────────────────────────── DONE
Stream 5 ──────────────────────────────────────────────────────── DONE
         |            |                    |                   |
         Sync         Sync                 Sync                Final
        (Sep 30)     (Oct 7)              (Oct 14)            (Oct 13)
```

**Weekly Sync (Every Friday):**
- Compare progress across streams
- Identify blockers + help
- Consolidate commits
- Report to coordinator

---

## SUCCESS CRITERIA

**By Oct 13, 2026:**

- ✅ 18/18 CRITICAL findings fixed
- ✅ 100+ new unit tests added
- ✅ Code coverage improved (65% → >80%)
- ✅ All tests passing (0 failures)
- ✅ Zero regressions (existing tests still pass)
- ✅ All re-audit gates passed (Week 7–9)
- ✅ Audit trail verified (no corruption)

**Failure Criteria (STOP → escalate):**
- Any CRITICAL fix breaks existing tests
- Coverage doesn't improve ≥10%
- Re-audit gates fail (regressions detected)

---

## CONTINGENCY PLANNING

**If a fix takes longer than planned:**

1. **Prioritize:** Focus on security + compliance first
2. **Parallelize:** More team members join stream
3. **Defer:** Move low-risk HIGH findings to Phase 11
4. **Extend:** Adjust timeline (goal still Nov 30)

---

## NEXT PHASE (WEEK 4)

**Oct 14: Phase 7–9 Re-Audit Begins**

Each dimension gets re-audited:
- Gate 1 (Security): Verify S-001–005 fixed
- Gate 2 (Architecture): Verify A-001–004 fixed
- Gate 3 (Compliance): Verify C-001–003 fixed
- Gate 4 (Testing): Verify T-001–004 fixed + coverage >80%
- Gate 5 (Production): Verify P-001, P-002 working

Success = 0 regressions, 0 CRITICAL remaining

---

**Remediation Team Leads:** To be assigned (can proceed solo, parallel preferred)  
**Coordinator:** Weekly report reviews (Fridays)  
**Timeline:** 3 weeks to 0 CRITICAL, then 4 weeks re-audit, then 2 weeks final verdict

**Target Completion:** Nov 30, 2026 ✅

