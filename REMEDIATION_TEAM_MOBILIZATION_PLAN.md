# Remediation Team Mobilization Plan

**Sprint:** Phase 10 Emergency Remediation  
**Duration:** 7–10 days (Sep 25, 2026 – Oct 3, 2026)  
**Mission:** Fix all 30 CRITICAL findings → Phase 10 approved for Oct 3–10 kickoff  
**Status:** 🔴 READY FOR ACTIVATION (awaiting exec approval)

---

## TEAM COMPOSITION (7–10 People)

### Stream 1: Consent + Audit (3 people)
**Owner:** Backend Lead 1 (TBD at kickoff)  
**Goal:** Implement ConsentStore + wire audit_backend for all consent/audit operations

#### Role 1A: ConsentStore Implementation + Wiring
- **Person:** [Backend Engineer 1]
- **Tasks:**
  1. Implement `ConsentStore` class (persistent state, SQLite backend) — 4h
     - Interface: `get_consent(user_id, scope)` → bool
     - Interface: `grant_consent(user_id, scope, ttl=90d)` → event
     - Interface: `revoke_consent(user_id, scope)` → event
     - Persist to `~/.corvin/consent_store.db`
     - Tenant-isolated: all queries filtered by `tenant_id`
  2. Wire to `@consent_required` decorator — 1h
     - Replace stub "always allow" with real ConsentStore check
     - File: `core/compliance/consent.py:68-102`
  3. Emit audit events on consent grant/revoke — 2h
     - `consent_granted` event (with scope, TTL)
     - `consent_revoked` event (with scope)
     - Wire to `audit_backend.log_event()`
  4. Create consent validation test — 2h
     - Test consent granted → route allowed
     - Test consent denied → 403 Forbidden
     - Test consent expiry + revoke
     - Test cross-tenant isolation
- **Effort:** 9 hours total
- **Deliverable:** ConsentStore fully operational + 20 tests passing
- **Done Criteria:** `consent_store.py` committed + `test_consent_store.py` ✅

#### Role 1B: Audit Atomicity + Backend Integration
- **Person:** [Backend Engineer 2]
- **Tasks:**
  1. Implement atomic audit transaction framework — 8h
     - Use SQLite transactions or file journal pattern
     - Ensure atomic: write + hash + chain in single transaction
     - Recovery on crash: verify chain integrity on boot
     - File: `core/compliance/audit_atomic_transaction.py`
  2. Wire audit_backend to consent + skill operations — 6h
     - Emit audit event on every consent change (grant/revoke/check)
     - Emit audit event on every skill execution
     - Emit audit event on every feedback signal
     - All events hash-chained
  3. Create comprehensive audit test suite — 12h
     - Unit tests: 25 tests (event creation, hashing, chaining)
     - Integration tests: 30 tests (multi-event chains, concurrent writes)
     - E2E tests: 15 tests (real audit output, recovery scenarios)
     - Coverage target: ≥95% on audit module (1,746 LoC)
- **Effort:** 26 hours total
- **Deliverable:** Atomic audit framework + 70 tests ✅
- **Done Criteria:** All tests pass + `pytest tests/audit/ -v` ✅

#### Role 1C: QA + Validation
- **Person:** [QA Engineer]
- **Tasks:**
  1. Test ConsentStore functionality — 4h
     - Real consent grant/revoke scenarios
     - TTL expiry verification
     - Cross-tenant isolation verification
  2. Validate audit chain integrity — 6h
     - Run `verify_audit_chain.py` after each fix
     - Confirm 0 gaps, 0 corrupted hashes
     - Verify hash-chain over 1000+ sequential events
  3. Regression testing — 4h
     - Ensure no existing tests broken
     - Run full test suite: `pytest tests/ -x`
- **Effort:** 14 hours total
- **Deliverable:** QA sign-off + regression report ✅

---

### Stream 2: SecurityOrchestratorSkill (2 people)
**Owner:** Skills Lead (TBD at kickoff)  
**Goal:** Implement threat detection + policy engine (previously stubbed)

#### Role 2A: ThreatDetector + PolicyEngine Implementation
- **Person:** [Skills Engineer 1]
- **Tasks:**
  1. Implement ThreatDetector class — 8h
     - Pattern: Brute force (N failed logins in M minutes)
     - Pattern: Privilege escalation (sudden role elevation)
     - Pattern: Data exfiltration (bulk export + external access)
     - Pattern: Cross-tenant access (policy violation)
     - Return: List[Threat(type, severity, confidence)]
     - File: `core/skills/os_skills/security_orchestrator.py`
  2. Implement PolicyEngine class — 6h
     - Tighten on threat detection (increase auth timeout, rate limits)
     - Revert on threat TTL expiry (restore baseline config)
     - Emit policy change audit events
     - Store policy history for audit trail
  3. Wire to audit_backend — 2h
     - `threat_detected` event (with threat details + confidence)
     - `policy_tightened` event (with delta from baseline)
     - `policy_reverted` event
  4. Integration tests — 8h
     - Test brute force detection + tightening
     - Test data exfiltration + response
     - Test policy revert after threat TTL
     - Test audit events emitted correctly
- **Effort:** 24 hours total
- **Deliverable:** SecurityOrchestratorSkill fully functional + 30 tests ✅

#### Role 2B: Tests + Validation
- **Person:** [Backend Engineer 3]
- **Tasks:**
  1. Create 50+ unit + E2E tests — 12h
     - Unit: threat detection patterns (brute force, escalation, exfil, cross-tenant)
     - Unit: policy tightening/revert logic
     - E2E: real audit trail verification
     - E2E: policy change triggers + effects
  2. Validate threat detection accuracy — 4h
     - Replay historical audit events
     - Verify pattern detection works on real data
     - Confirm no false positives
  3. Cross-stream validation (with Stream 1) — 2h
     - Verify SecurityOrchestratorSkill emits audit events correctly
     - Verify events are hash-chained
     - Verify tenant isolation
- **Effort:** 18 hours total
- **Deliverable:** 50+ tests passing + validation report ✅

---

### Stream 3: Console Integration (2 people)
**Owner:** Frontend Lead (TBD at kickoff)  
**Goal:** Build HTTP routes + UI panels for Phase 10 Skills (unreachable today)

#### Role 3A: HTTP Routes + Backend Integration
- **Person:** [Backend Engineer — API specialist]
- **Tasks:**
  1. Create HTTP routes for Phase 10 Skills — 3h
     ```
     POST /v1/console/skills/workflow-optimizer/execute
     GET /v1/console/skills/workflow-optimizer/status
     POST /v1/console/skills/security-orchestrator/threats
     GET /v1/console/skills/security-orchestrator/status
     GET /v1/console/skills/flow-guard/flows
     POST /v1/console/skills/flow-guard/policy
     GET /v1/console/skills/learning/feedback
     POST /v1/console/skills/learning/feedback
     # + 4 more routes for skill management
     ```
  2. Implement request validation + tenant isolation — 2h
     - All routes require authentication + session
     - All requests filtered by `tenant_id` from session
     - Fail-closed: missing tenant_id → 403
  3. Wire routes to skill execution backend — 3h
     - POST routes call skill executors
     - GET routes return skill status/metrics
     - All responses include audit trail info
  4. Create route tests — 3h
     - Test auth required on all routes
     - Test tenant isolation
     - Test request validation
- **Effort:** 11 hours total
- **Deliverable:** 12+ HTTP routes, fully tested ✅

#### Role 3B: Frontend UI + Real-time Updates
- **Person:** [Frontend Engineer]
- **Tasks:**
  1. Create React components (3 panels) — 10h
     - `/app/skills/workflow-optimizer` (status, controls, logs)
     - `/app/skills/security-orchestrator` (threat detection, policy status)
     - `/app/skills/flow-guard` (flow visualization, controls)
  2. Implement real-time updates via WebSocket — 3h
     - Skill status → auto-update on change
     - Threat alerts → real-time notifications
     - Audit log → live stream
  3. Wire to console navigation — 2h
     - Add panels to sidebar (`NAV_GROUPS`)
     - Add panel routes to registry (`PANELS`)
     - Confirm no shadow file conflicts
  4. Screenshot proof + deployment — 2h
     - Run `scripts/console-deploy.sh --marker 'Phase10Skills'`
     - Verify new bundle loads + panels appear
     - Hard-refresh + validate in browser
- **Effort:** 17 hours total
- **Deliverable:** 3 UI panels + routes, fully integrated ✅

---

### Stream 4: Verification + Re-Audit (2 people)
**Owner:** Security Lead (TBD at kickoff)  
**Goal:** Re-audit all fixes + compliance verification + staging soak test

#### Role 4A: Re-Audit + Compliance Verification
- **Person:** [Security Engineer]
- **Tasks:**
  1. Re-run adversarial review after each fix — continuous (5–10h/day)
     - Dimension 1 (Security): 4h → report after each fix
     - Dimension 2 (Architecture): 3h → report after each fix
     - Dimension 3 (Compliance): 2h → report after each fix
     - Dimension 4 (Testing): 4h → report mid-week
     - Dimension 5 (Production): 3h → report end-of-week
  2. Verify GDPR Art. 5/6/7/30/32 gaps closed — 4h
     - Consent gates operational (real store checked)
     - Audit trail complete (all operations logged)
     - Data loss impossible (atomic writes verified)
     - Audit events immutable (hash-chain verified)
  3. Verify EU AI Act Art. 5/50 compliance — 2h
     - Risk management (threat detection) operational
     - Bot-disclosure verified in code
  4. Produce final compliance report — 3h
     - GDPR certification: ✅ Art. 5/6/7/30/32 compliant
     - EU AI Act certification: ✅ Art. 5/50 compliant
     - Go/No-Go recommendation
- **Effort:** 30+ hours total (distributed across week)
- **Deliverable:** Daily re-audit reports + final compliance cert ✅

#### Role 4B: Staging + DevOps
- **Person:** [DevOps Engineer]
- **Tasks:**
  1. Deploy to staging after Stream 1–3 complete — 2h
     - Checkout fixed commits
     - Run migrations (consent_store, audit)
     - Deploy Phase 10 Skills to staging
  2. Execute 72-hour staging soak test (Oct 1–3) — continuous
     - Monitor for crashes, errors, audit corruption
     - Simulate load: 100 concurrent users
     - Inject faults (crash, network partition) → verify recovery
  3. Produce soak test report — 2h
     - Uptime: target 99.99% (max 4 seconds downtime)
     - Audit chain: 0 corrupted hashes
     - No data loss observed
     - Go/No-Go recommendation
- **Effort:** 8+ hours (distributed across week)
- **Deliverable:** Staging deployment + soak test report ✅

---

## DAILY STANDUPS & SYNC

### Standup Schedule

**Start Date:** Sep 25, 2026 (if exec approval granted Sep 22)

**Cadence:** 2x daily
- **Morning Standup:** 07:00 AM UTC (30 min)
- **Evening Standup:** 19:00 UTC (15 min)

**Attendees:**
- All 7–10 team members (mandatory)
- Stream leads (rotating facilitators)
- Integration lead (notes + blockers tracker)

**Format (Morning — 30 min):**
1. **Stream 1 (Consent + Audit):** What done yesterday → what today → blockers (5 min)
2. **Stream 2 (SecurityOrchestrator):** Same (5 min)
3. **Stream 3 (Console):** Same (5 min)
4. **Stream 4 (Re-Audit + DevOps):** Same (5 min)
5. **Cross-stream sync:** Dependencies, blockers, escalations (5 min)

**Format (Evening — 15 min):**
- Daily summary: % complete per stream
- Any blockers → escalation queue
- Next day priorities

### Blocker Escalation

**Blocker Severity Levels:**
- 🔴 **CRITICAL** — Blocks multiple streams → escalate to Integration Lead immediately
- 🟠 **HIGH** — Blocks one stream → solve within 4h or escalate
- 🟡 **MEDIUM** — Solves in <4h within stream

**Escalation Path:**
1. Stream lead identifies blocker (standup or ad-hoc)
2. Integration lead contacted immediately
3. If unresolved in 2h → CTO escalation (decision required)

---

## SUCCESS CRITERIA (Per Stream)

### Stream 1: Consent + Audit (Done by Oct 1)
- ✅ ConsentStore implemented + 20 tests passing
- ✅ `@consent_required` decorator wired + working
- ✅ Consent audit events emitted + hash-chained
- ✅ Atomic audit framework implemented + 70 tests passing
- ✅ Audit module coverage ≥95%
- ✅ Re-audit: 0 CRITICAL findings (Blockers 1–4 closed)

### Stream 2: SecurityOrchestratorSkill (Done by Oct 1)
- ✅ ThreatDetector + PolicyEngine fully implemented
- ✅ 50+ tests passing (unit + E2E)
- ✅ Real threat detection working on historical data
- ✅ Audit events emitted correctly
- ✅ Re-audit: 0 CRITICAL findings (Blocker 5 closed)

### Stream 3: Console Integration (Done by Oct 2)
- ✅ 12+ HTTP routes implemented + tested
- ✅ 3 UI panels fully functional
- ✅ Real-time WebSocket updates working
- ✅ Console deploys successfully (`console-deploy.sh` passes)
- ✅ All 3 panels visible + interactive in browser
- ✅ Re-audit: 0 CRITICAL findings (Blocker 6 closed)

### Stream 4: Verification (Done by Oct 3)
- ✅ Daily re-audit reports (no regressions)
- ✅ GDPR certification: Art. 5/6/7/30/32 ✅
- ✅ EU AI Act certification: Art. 5/50 ✅
- ✅ 72-hour staging soak test passed (0 critical incidents)
- ✅ Audit chain integrity verified (0 gaps, 0 corruption)
- ✅ Final compliance report produced

---

## GIT WORKFLOW & COMMITS

### Commit Strategy
- **Frequent commits** — don't wait until EOD
- **Atomic commits** — one feature per commit (not one per file)
- **Descriptive messages:**
  ```
  feat(consent): Implement ConsentStore with TTL + audit events [Blocker 1]
  fix(audit): Implement atomic transaction framework [Blocker 4]
  feat(skills): Implement SecurityOrchestratorSkill threat detection [Blocker 5]
  feat(console): Add HTTP routes for Phase 10 Skills [Blocker 6]
  ```

### Commit Checks
- [ ] All tests pass locally (`pytest tests/ -x`)
- [ ] No regressions in existing tests
- [ ] Audit events emitted correctly (grep for event type in git log)
- [ ] Tenant isolation maintained (no cross-tenant leakage)
- [ ] No hardcoded secrets/PII in code

### Pull Requests (Optional, but Recommended)
- Create PRs for each stream (not required during sprint)
- Link to Jira epic (Phase 10 Remediation Sprint)
- Assign 1 reviewer per PR (async review, 2h turnaround)
- Merge after 1 approval + all tests green

---

## TESTING STRATEGY

### Test Execution (Daily)

**Local Testing (Before commit):**
```bash
# Run all tests
pytest tests/ -v --tb=short

# Run only changed tests
pytest tests/test_consent_store.py tests/test_audit_*.py -v

# Check coverage
pytest --cov=core/compliance --cov-report=term-missing
```

**CI/CD (After commit):**
- GitHub Actions runs full suite (~20 min)
- Blocks merge if ≥1 test fails

### Re-Audit as Testing (Continuous)

After each Stream fix:
1. Security Engineer runs adversarial review on changed files
2. Files changed in commit → re-audit those 2–3 specific areas
3. If 0 new findings → commit marked "audit-clean"
4. If findings: either fix or document as deferred

---

## RISK MITIGATION

### Known Risks & Responses

| Risk | Probability | Mitigation |
|---|---|---|
| **ConsentStore migration fails** | 20% | Have SQLite + file backup implementations ready; roll back to simplest one if needed |
| **Audit atomic write complexity** | 30% | Pair programming: pair 2 engineers; design on whiteboard first |
| **SecurityOrchestratorSkill detection misses attacks** | 15% | Replay historical events; adjust thresholds based on false positives |
| **Console UI panels don't load** | 10% | Cache clearing workflow (`rm -rf node_modules/.vite dist/`) part of checklist |
| **Staging soak test reveals new bugs** | 40% | Plan for 2-day fix window (Oct 1–2); gate dates stay fixed |
| **Team member gets sick** | 15% | Each role has 1 backup identified; knowledge transfer via pair prog |
| **Executive approval delayed** | 5% | Have contingency sprint plan (Oct 10 start) ready |

---

## DAILY TRACKING & METRICS

### Metrics to Track (Daily)

1. **% Complete Per Stream:**
   - Stream 1 (Consent + Audit): Target 100% by Oct 1
   - Stream 2 (SecurityOrchestrator): Target 100% by Oct 1
   - Stream 3 (Console): Target 100% by Oct 2
   - Stream 4 (Re-Audit): Continuous (target 100% by Oct 3)

2. **Test Pass Rate:** Target 100% (0 failing tests)

3. **Re-Audit Findings:** Track as they appear
   - Target: 0 CRITICAL remaining (30 → 0 by Oct 3)
   - Acceptable: ≤2 HIGH by Oct 3

4. **Blocker Count:** Track open blockers
   - Target: 0 open by EOD each day
   - If 2+ blockers open → escalate immediately

5. **Code Review Turnaround:** <2 hours per PR

### Daily Dashboard (Created each morning)

```
REMEDIATION SPRINT — Daily Status (Oct 1, 2026)

🟢 Stream 1 (Consent + Audit): 85% complete
   - ConsentStore: ✅ Done (committed)
   - Audit atomicity: 🟡 In progress (6h left)
   - Tests: 65/70 passing
   - Blocker: None

🟡 Stream 2 (SecurityOrchestrator): 60% complete
   - ThreatDetector: 🟡 In progress (8h left)
   - PolicyEngine: ❌ Not started
   - Tests: 20/50 written
   - Blocker: None

🟢 Stream 3 (Console): 40% complete
   - HTTP routes: ✅ Done (committed)
   - UI panels: 🟡 In progress (12h left)
   - Blocker: None

🟢 Stream 4 (Re-Audit): Continuous
   - Daily re-audit: 2 reports filed (no new CRITICAL)
   - Staging: 🟡 Deployed, soak test running
   - Blocker: None

📊 Overall: 60% complete | 0 CRITICAL blockers | 1 HIGH escalation (unrelated)
```

---

## GO/NO-GO GATE (Oct 3, 10:00 AM UTC)

### Decision Criteria

**GO if:**
- ✅ 0 CRITICAL findings remain (re-audit confirmed)
- ✅ ≤2 HIGH findings (approved risk list)
- ✅ All GDPR Art. 5/6/7/30/32 gaps closed
- ✅ All EU AI Act Art. 5/50 gaps closed
- ✅ 72-hour staging soak test: 0 critical incidents
- ✅ Audit chain integrity: 0 gaps, 0 corruption

**NO-GO if:**
- ❌ Any CRITICAL findings remain unfixed
- ❌ ≥3 HIGH findings unresolved
- ❌ Staging soak test failures (crashes, data loss)
- ❌ Audit chain corruption detected

### Decision Makers
- CTO (technical sign-off)
- Chief Compliance Officer (legal sign-off)
- Security Engineer Lead (security sign-off)
- Integration Lead (readiness confirmation)

---

## POST-SPRINT (Oct 3–10)

### If GO (Phase 10 Approved)

1. **Kickoff Planning (Oct 3–4):**
   - Team briefing on Phase 10 roadmap
   - Stream lead assignments
   - Risk review for Phase 10 (post-remediation baseline)

2. **Phase 10 Streams Launch (Oct 10):**
   - 4 parallel Skill development streams
   - Weekly syncs with exec leadership
   - Production deployment schedule: Nov 15

### If NO-GO (Remediation Extended)

1. **Determine Cause:** What blockers remain?
   - Is it more code fixing?
   - Is it staging test failures requiring deeper investigation?
   - Is it staffing/resource issue?

2. **Revised Timeline:** Push phase 10 kickoff to Oct 10 or Oct 17
   - Extend remediation sprint by 3–7 days
   - Maintain Nov 15 production target (achievable with extended runway)

3. **Escalate to Exec:** Re-brief on new timeline + recovery plan

---

**NEXT STEP:** Await executive approval (Sep 22). If approved, team mobilization begins Sep 25, 07:00 AM UTC.

**Prepared by:** Claude Haiku 4.5 (CorvinOS Architect)  
**Date:** 2026-09-22, 19:15 UTC
