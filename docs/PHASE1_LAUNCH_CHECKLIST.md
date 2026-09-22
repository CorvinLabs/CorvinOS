# Phase 1 Launch Checklist: Go/No-Go Gates

**Status:** 🟢 **READY FOR EXECUTION**  
**Date:** 2026-09-22  
**Go/No-Go Decision Date:** Friday 2026-10-18, 15:00 UTC  
**Checklist Owner:** Tech Lead + SRE Lead + Product Lead (3-signature go-live)

---

## Executive Summary

This document defines three sequential checklists that must be completed before Phase 1 launch:

1. **Code Quality Checklist** (due: end of Week 3, Oct 13)
2. **Operations Checklist** (due: end of Week 4, Oct 18)
3. **Launch Go/No-Go Gates** (due: Friday Oct 18, 15:00 UTC)

Each checklist is owned by a specific role. Final go/no-go decision requires **ALL 3 ROLES to sign off** (unanimous decision required).

---

## 1. Code Quality Checklist (Due: End of Week 3, Oct 13)

**Owner:** Tech Lead  
**Scope:** Code reviews, test coverage, performance, security, compliance

### Code Review & Merge Requirements

- [ ] **All code merged to main (2-reviewer rule)**
  - Backend #1 marketplace code: 2 reviews → merged
  - Backend #2 learning code: 2 reviews → merged
  - Frontend console panels: 2 reviews → merged
  - Total: 800+ LoC approved + merged
  - Measurement: git log --oneline main | head -50 shows all PRs merged

- [ ] **Code quality gates passed**
  - Linting: mypy, pylint all pass (0 violations) → `mypy . --strict | head` = empty
  - Formatting: black, eslint all pass → `black --check . | wc -l` = 0 errors
  - No hardcoded secrets (grep -r "api_key" src/ → 0 results)
  - Measurement: CI/CD pipeline shows all quality gates ✅

- [ ] **No blockers from security review**
  - OWASP top 10 assessed (injection, auth, secrets, XXE, SSRF, etc.)
  - Auth/authz correct (skill installation requires consent, no privilege escalation)
  - Secrets management: no API keys in code, config only
  - Measurement: Security review sign-off document, <5 findings

### Test Coverage & Pass Rates

- [ ] **Unit tests >85% coverage, all passing**
  - Coverage report: `pytest --cov=. --cov-report=term-report`
  - Target: ≥85% coverage (currently tracking ~86%)
  - All tests green: `pytest . -v --tb=short | tail -1 | grep "passed"`
  - Measurement: Coverage badge in README shows 86%+ | All tests passing

- [ ] **E2E tests >95% passing (38+ tests)**
  - Marketplace E2E: 12/12 passing (discovery, install, enable, upgrade, dependencies, versions)
  - Feedback E2E: 12/12 passing (collect, quality gate, persistence, audit)
  - Cost E2E: 8/8 passing (dashboard, breakdown, recommendations, anomalies)
  - Integration E2E: 6/6 passing (end-to-end workflows)
  - Measurement: `pytest tests/e2e/ -v | grep -c "PASSED"` ≥ 38

- [ ] **Adversarial tests all passing (15+ scenarios)**
  - Security tests: 5/5 passing (injection, privilege escalation, secret leakage)
  - Isolation tests: 4/4 passing (tenant boundary, cross-tenant data check)
  - Compliance tests: 6/6 passing (all 6 gates enforcing, zero bypasses)
  - Measurement: `pytest tests/adversarial/ -v | tail -1 | grep "passed"`

### Performance Validation

- [ ] **p99 latency <500ms (baseline: 247ms, SLA: 500ms)**
  - Marketplace API: p99 ≤150ms (GET /index, GET /skills/{id})
  - Feedback API: p99 ≤80ms (POST /feedback)
  - Cost API: p99 ≤200ms (GET /costs, GET /breakdown)
  - Dashboard load: p99 ≤300ms (all 4 panels)
  - Measurement: `corvin stats perf --percentile=99` shows all <500ms

- [ ] **Error rate <0.1% (baseline: 0.08%, SLA: <0.1%)**
  - Measured over 1 week of production traffic
  - Errors breakdown: P0 (critical): 0, P1 (high): 0, P2 (medium): <1 in 1000
  - Measurement: Error dashboard shows <0.1% error rate

- [ ] **No memory leaks (sustained <2 GB resident memory)**
  - 1-hour load test at 100 req/sec
  - Memory footprint stable (no growth over time)
  - Measurement: `ps aux | grep corvin-webui | awk '{print $6}'` = constant ±10%

### Compliance Gate Verification

- [ ] **All 6 compliance gates still enforcing (zero bypasses)**
  - L18 Bot Disclosure: shown to every user ✅
  - L16 Consent Gate: GDPR Art. 6,7 enforced ✅
  - L44 House Rules: always-on, never disabled ✅
  - L34 Data Flow Guard: classification active, PII blocked ✅
  - L10 Path Gate: FS writes protected ✅
  - L23 Voice Audit: metadata-only logging ✅
  - Measurement: Compliance test suite (`pytest tests/compliance/ -v`) all pass

- [ ] **Audit trail hash-chain verified (zero tampering)**
  - Daily verification runs: 19/19 days passed ✅
  - Chain height: 142,857+ events, all hashes linked
  - Boot tripwire: verified (boot refused on broken chain test)
  - Measurement: `corvin audit verify-chain --tenant=_default` = ✅ VERIFIED

- [ ] **Zero compliance violations in Phase 1 code**
  - Code review gate: verified no gates weakened
  - New audit events: all Phase 1 events ADR-0264 compliant (id, status, depends_on, paths, docs)
  - Measurement: Compliance audit report, zero violations

### ADR & Documentation

- [ ] **All Phase 1 ADRs migrated to Corvin-ADR**
  - ADR-2030 (Marketplace): migrated, frontmatter complete ✅
  - ADR-2031 (Learning): migrated, frontmatter complete ✅
  - ADR-2032 (Cost): migrated, frontmatter complete ✅
  - ADR-2033 (Onboarding): migrated, frontmatter complete ✅
  - Measurement: `ls /home/shumway/projects/Corvin-ADR/decisions/ | grep "ADR-203[0-3]"` = 4 files

- [ ] **Code comments on complex logic (>20 LOC per function)**
  - Optimizer algorithm: documented (why thresholds work)
  - Cost calculation: documented (formula, assumptions)
  - Feedback quality gate: documented (contradiction detection)
  - Measurement: Code review checklist shows comments present

### Tech Lead Sign-Off

**Code Quality Assessment:**

```
All of the following must be TRUE:

[ ] Code merged (2 reviewers per PR)
[ ] Tests >95% passing (38+ E2E)
[ ] Adversarial tests all pass (15 scenarios)
[ ] p99 latency <500ms (247ms baseline)
[ ] Error rate <0.1% (0.08% baseline)
[ ] All 6 compliance gates enforcing
[ ] ADRs migrated to Corvin-ADR
[ ] Zero security/compliance violations

TECH LEAD ASSESSMENT:

☐ Code Quality: ✅ GO (all criteria met)
☐ Code Quality: 🟡 CONDITIONAL (1+ criterion at risk, but mitigable)
☐ Code Quality: ❌ NO-GO (1+ blocker, cannot launch)

SIGNATURE: _____________________ DATE: ________

If CONDITIONAL or NO-GO, document blockers here:
[Blocker 1: ...]
[Blocker 2: ...]
[Mitigation: ...]
```

---

## 2. Operations Checklist (Due: End of Week 4, Oct 18)

**Owner:** SRE Lead  
**Scope:** Monitoring, alerting, runbook, team readiness, disaster recovery

### Monitoring & Alerting Setup

- [ ] **Monitoring dashboard live (all Phase 1 metrics)**
  - Dashboard URL: http://monitoring.corvin.local/phase1
  - Metrics tracked:
    - Cost: daily burn rate, anomalies (cost spike >20% flagged)
    - Optimizer: hourly runs, config updates, convergence score
    - Skills: load time, execution latency, errors
    - Compliance: gate violations (target: 0)
  - Refreshes: every 5 minutes
  - Measurement: Dashboard accessible, all metrics populated

- [ ] **Alerting rules configured & tested (5+ rules)**
  - Cost spike alert: fires if cost >20% above 7-day avg
  - Error rate alert: fires if errors >0.1% for >5 min
  - Latency alert: fires if p99 >500ms for >5 min
  - Compliance alert: fires if any gate violation detected
  - Skill error alert: fires if skill execution errors >1/min
  - Measurement: Alert test: simulate condition, verify alert fires

- [ ] **Health checks implemented & passing (10+ checks)**
  - Liveness probe: /health (responds 200)
  - Readiness probe: /ready (all dependencies up)
  - Compliance probe: all 6 gates enforcing
  - Database probe: audit chain reachable
  - Learning probe: EventStore writable
  - Cost API probe: Anthropic API reachable
  - Measurement: `curl http://localhost:8765/health` = 200 OK

### Runbook & Operational Procedures

- [ ] **Runbook complete & reviewed (7 sections, <20 pages)**
  - 1. Daily checklist (5 min, 5 items)
  - 2. Weekly review (30 min, 3 sections)
  - 3. Incident response (P0/P1/P2 procedures)
  - 4. Disaster recovery (backup restore, rollback)
  - 5. Escalation chain (who to contact, decision authority)
  - 6. Known issues & workarounds (from beta testing)
  - 7. Performance tuning (if latency increases)
  - Measurement: Runbook reviewed by Tech Lead + Manager, clarity >4/5

- [ ] **Daily checklist established & tested**
  - 09:00 UTC daily: run checklist (5 min)
  - Checklist items:
    - Health check: all 10 probes green?
    - Cost: reasonable daily burn?
    - Errors: any spikes overnight?
    - Alerts: any firing? (if yes, investigate)
    - Backup: completed successfully yesterday?
  - Measurement: Checklist run for 5 consecutive days, all green

- [ ] **On-call schedule published & trained**
  - Schedule format: Google Calendar (shared with team)
  - Rotation: 1 backend + 1 SRE on-call (24h rotations)
  - Escalation chain: On-call → Tech Lead (if needed) → Manager
  - Training: 2 incident response drills completed
  - Measurement: Schedule published, >90% of team confirmed available

### Disaster Recovery

- [ ] **Backup tested: restore time <5 minutes**
  - Test date: [date]
  - Restore procedure: [steps]
  - Time to restore: [minutes]
  - Data verified: [yes/no]
  - Measurement: Test report signed by SRE, restore time <5 min

- [ ] **Rollback procedure documented & tested**
  - Rollback method: git revert to prior commit, redeploy
  - Time to rollback: target <15 min
  - Success criteria: all systems green after rollback
  - Measurement: Test report, rollback time measured

- [ ] **3 backup copies maintained**
  - Location 1 (local): same day backup
  - Location 2 (offsite): daily backup (different physical location)
  - Location 3 (cold): weekly backup (archived, long-term)
  - Retention: 30 days (local) + 90 days (offsite)
  - Measurement: Backup inventory (3 copies present, all verified)

### Team Readiness

- [ ] **Team training completed (1h, delivered Thu Oct 17)**
  - Attendees: all engineers + on-call rotation
  - Topics:
    - Phase 1 feature overview (15 min)
    - How to read dashboards + alerts (15 min)
    - Incident response (15 min)
    - Hands-on Q&A (15 min)
  - Material: slides + lab environment + Q&A doc
  - Measurement: Attendance sheet >90%, post-training survey >4/5

- [ ] **Incident drills completed (2 drills, pass all)**
  - Drill 1: Cost spike alert fires → team investigates + clears alert
  - Drill 2: P1 error detected → team pages on-call + fixes
  - Success: team responds in <10 min, follows runbook
  - Measurement: Drill report, both pass

- [ ] **On-call procedures tested**
  - Escalation chain: Page on-call → contact Tech Lead if needed → contact Manager if needed
  - Paging system: works (PagerDuty or equivalent)
  - Contact info: all on-call members have current phone/email
  - Measurement: Escalation test (page someone, verify message received)

### SRE Lead Sign-Off

**Operations Readiness Assessment:**

```
All of the following must be TRUE:

[ ] Monitoring dashboard live
[ ] 5+ alert rules tested & working
[ ] 10+ health checks passing
[ ] Runbook complete & reviewed
[ ] Daily checklist tested (5 days)
[ ] On-call schedule published
[ ] 2 incident drills completed (both pass)
[ ] Backup restore tested (<5 min)
[ ] Rollback procedure tested (<15 min)
[ ] Team trained (>90% attendance, >4/5 satisfaction)

SRE ASSESSMENT:

☐ Operations: ✅ GO (all criteria met)
☐ Operations: 🟡 CONDITIONAL (1+ criterion at risk, but mitigable)
☐ Operations: ❌ NO-GO (1+ blocker, cannot launch)

SIGNATURE: _____________________ DATE: ________

If CONDITIONAL or NO-GO, document blockers here:
[Blocker 1: ...]
[Mitigation: ...]
```

---

## 3. Launch Go/No-Go Gates (Due: Friday Oct 18, 15:00 UTC)

**Owners:** Tech Lead + SRE Lead + Product Lead  
**Decision Authority:** Unanimous (all 3 must agree)

### Pre-Launch System Verification (Friday 09:00 UTC)

24 hours before go/no-go decision, verify all systems ready:

- [ ] **All systems health check green**
  - Marketplace API: responding
  - Learning optimizer: running hourly
  - Cost dashboard: data fresh
  - Compliance gates: all enforcing
  - Audit chain: verified
  - Measurement: `corvin health --all` shows ✅ all green

- [ ] **Code freeze applied (no commits after 12:00 UTC Friday)**
  - Only critical hotfixes allowed (P0 only)
  - All non-critical code merged by EOD Thursday
  - Measurement: git log Friday shows only hotfixes (if any)

- [ ] **Final smoke test passing (5 critical scenarios)**
  - Scenario 1: User discovers skill → installs → enables
  - Scenario 2: User provides feedback → optimizer runs → convergence updates
  - Scenario 3: Cost dashboard loads, shows accurate costs
  - Scenario 4: Compliance gates enforce (try disabling a gate, fails)
  - Scenario 5: Audit chain verified (end-to-end hash-chain intact)
  - Measurement: All 5 scenarios pass (run manually, document)

### Final Assessment (Friday 15:00 UTC)

Three roles assess readiness independently, then convene for final decision:

#### **Gate 1: Feature Complete ✅**

| Criterion | Status | Evidence |
|-----------|--------|----------|
| **Stream 1: Marketplace** | ✅ or ❌ | All US-1-1 through US-1-7 DONE + tested |
| **Stream 2: Learning Loop** | ✅ or ❌ | All US-2-1 through US-2-6 DONE + tested |
| **Stream 3: Cost Insights** | ✅ or ❌ | All US-3-1 through US-3-4 DONE + tested |
| **Stream 4: Onboarding** | ✅ or ❌ | All US-4-1 through US-4-4 DONE + tested |
| **Overall** | ✅ GO | All 4 streams DONE |

**Owner:** Tech Lead

---

#### **Gate 2: Quality Complete ✅**

| Criterion | Status | Evidence |
|-----------|--------|----------|
| **Code coverage** | ✅ or ❌ | >85% (currently 86%) |
| **E2E tests passing** | ✅ or ❌ | 38+ tests, >95% passing |
| **Adversarial tests passing** | ✅ or ❌ | 15+ scenarios, all pass |
| **Security review** | ✅ or ❌ | <5 findings (all addressed or accepted) |
| **Compliance gates** | ✅ or ❌ | All 6 gates enforcing, zero bypasses |
| **Performance SLA** | ✅ or ❌ | p99 latency <500ms (247ms), errors <0.1% (0.08%) |
| **Overall** | ✅ GO | All criteria met |

**Owner:** Tech Lead + QA

---

#### **Gate 3: Operations Ready ✅**

| Criterion | Status | Evidence |
|-----------|--------|----------|
| **Monitoring live** | ✅ or ❌ | Dashboard accessible, all metrics populated |
| **Alerting active** | ✅ or ❌ | 5+ rules tested, alerts fire correctly |
| **Runbook complete** | ✅ or ❌ | 7 sections, reviewed, >4/5 clarity |
| **On-call schedule** | ✅ or ❌ | Published, team confirmed, >90% availability |
| **Team trained** | ✅ or ❌ | >90% attendance, >4/5 satisfaction |
| **Backup restore tested** | ✅ or ❌ | <5 minutes, data verified |
| **Disaster recovery** | ✅ or ❌ | Rollback <15 min, 3 backup copies |
| **Overall** | ✅ GO | All criteria met |

**Owner:** SRE Lead

---

#### **Gate 4: Compliance Ready ✅**

| Criterion | Status | Evidence |
|-----------|--------|----------|
| **All 6 gates enforcing** | ✅ or ❌ | Zero gate bypasses in Phase 1 code |
| **Audit trail intact** | ✅ or ❌ | Hash-chain verified, boot tripwire active |
| **ADRs migrated** | ✅ or ❌ | ADR-2030–2033 in Corvin-ADR, ADR-0264 compliant |
| **No security violations** | ✅ or ❌ | <5 findings, all addressed or accepted-risk |
| **Overall** | ✅ GO | All criteria met |

**Owner:** Tech Lead + Security (if available)

---

#### **Gate 5: Documentation Ready ✅**

| Criterion | Status | Evidence |
|-----------|--------|----------|
| **Getting Started guide** | ✅ or ❌ | Published, covers all platforms, <30 min end-to-end |
| **FAQ complete** | ✅ or ❌ | ≥20 Q&As, searchable, covers all features |
| **Video tutorials** | ✅ or ❌ | 3–5 videos × 3–5 min each, captions present |
| **Release notes** | ✅ or ❌ | Features, known issues, upgrade path documented |
| **Overall** | ✅ GO | All criteria met |

**Owner:** Product Lead

---

### Final Decision Vote (Friday 15:00 UTC)

Each role votes independently, then convenes for discussion:

```
========================================
PHASE 1 GO/NO-GO DECISION
Friday October 18, 2026, 15:00 UTC
========================================

GATE 1: Feature Complete
  Tech Lead:  ☐ GO  ☐ CONDITIONAL  ☐ NO-GO

GATE 2: Quality Complete
  Tech Lead:  ☐ GO  ☐ CONDITIONAL  ☐ NO-GO

GATE 3: Operations Ready
  SRE Lead:   ☐ GO  ☐ CONDITIONAL  ☐ NO-GO

GATE 4: Compliance Ready
  Tech Lead:  ☐ GO  ☐ CONDITIONAL  ☐ NO-GO

GATE 5: Documentation Ready
  Product:    ☐ GO  ☐ CONDITIONAL  ☐ NO-GO

========================================
FINAL DECISION
========================================

If all 5 gates are GO:
  → OVERALL: ✅ GO FOR LAUNCH

If 4 gates are GO + 1 is CONDITIONAL:
  → OVERALL: 🟡 CONDITIONAL GO (requires risk acceptance)

If any gate is NO-GO:
  → OVERALL: ❌ NO-GO (document blockers, plan Phase 1.1)

========================================
SIGNATURES (REQUIRED)
========================================

TECH LEAD:   ___________________  DATE: ________

SRE LEAD:    ___________________  DATE: ________

PRODUCT:     ___________________  DATE: ________

MANAGER:     ___________________  DATE: ________ (if needed)

========================================
```

### If CONDITIONAL GO or NO-GO

**Blocker Documentation (required if not straight GO):**

```
GATE [1-5]: [NAME]
STATUS: ☐ CONDITIONAL  ☐ NO-GO

Blocker:
  [Specific criterion not met]

Evidence:
  [Why blocker exists]

Mitigation:
  ☐ Defer to Phase 1.1 (scope change, not critical)
  ☐ Fix before launch (add 2–3 days to timeline)
  ☐ Accept risk (mitigations in place, proceed)

Timeline impact:
  [If defer: launch delayed to date X]
  [If fix: Phase 1.1 shortened, launch on time]
  [If accept: launch on time, Phase 1.1 monitors]

Owner:
  [Who owns the mitigation]

Sign-off:
  ___________________  (owning role)
  ___________________  (manager/escalation)
```

---

## Launch Timeline from Go/No-Go Decision

### If ✅ GO (Friday 15:00 UTC)

**Launch window opens:** Sunday 23:00 UTC (4 hours before Monday 00:00 UTC)

| Time | Activity |
|------|----------|
| Sunday 23:00 UTC | Pre-launch checklist (all green) |
| Monday 00:00 UTC | Deploy Phase 1 to production |
| Monday 02:00 UTC | Final smoke tests (5 critical scenarios) |
| Monday 02:00–12:00 UTC | First 10 hours monitoring (every 30 min checks) |
| Tuesday–Friday | Post-launch monitoring (daily check-ins) |
| Friday 15:00 UTC | Launch success declaration (if all metrics green for 5+ days) |

### If 🟡 CONDITIONAL GO (Friday 15:00 UTC)

**Proceed with risk acceptance, launch as planned** (but risk documented + sign-off required from 3 roles)

**Phase 1.1 sprint (1 week post-launch):** Fix conditional blockers, integrate into Phase 1.5 planning

### If ❌ NO-GO (Friday 15:00 UTC)

**Launch delayed.** Start Phase 1.1 sprint immediately:

| Task | Timeline |
|------|----------|
| Document all blockers | Friday 16:00–17:00 UTC |
| Team meeting on fixes | Monday 10:00 UTC (next week) |
| Execute Phase 1.1 (1 week) | Oct 21–27 (fix blockers) |
| Re-assess go/no-go | Friday Oct 25, 15:00 UTC |
| New launch window (if approved) | Sunday Oct 27, 23:00 UTC |

---

## Post-Launch Success Metrics

### 24-Hour Post-Launch Check (Monday evening)

All of the following must be TRUE:
- [ ] Error rate <0.1% (sustained >4h)
- [ ] Latency p99 <500ms (sustained >4h)
- [ ] Zero critical bugs or P0 incidents
- [ ] Users successfully onboarded (>50 on Day 1)
- [ ] Cost dashboard data accurate (within 1% vs billing)
- [ ] Learning loop running (optimizer executed hourly)
- [ ] Feedback collected (>10 submissions)
- [ ] All compliance gates still enforcing (zero bypasses)

**5-Day Post-Launch Success Criteria**

All metrics sustained for 5 consecutive days (Mon–Fri):
- [ ] Error rate <0.1% (daily average)
- [ ] Latency p99 <500ms (daily average)
- [ ] Zero critical bugs post-launch
- [ ] Users active (growth trending)
- [ ] Feedback >50/day (quality gate detecting noise)
- [ ] Learning converging (confidence ↑)
- [ ] Compliance perfect (0 gate violations)

**Friday Oct 25, 15:00 UTC: Launch Declared Successful ✅**

If all metrics met → Phase 1 GA is live, Phase 1.5 planning begins

---

**END OF PHASE 1 LAUNCH CHECKLIST**

**Checklist Owner:** Tech Lead + SRE Lead + Product Lead  
**Next:** PHASE1_KICKOFF_AGENDA.md (4-hour kickoff meeting)
