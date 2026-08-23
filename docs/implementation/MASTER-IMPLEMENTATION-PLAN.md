# Master Implementation Plan — Orchestration System (8 Weeks, 355h)

**Version:** v1.0  
**Date:** 2026-08-23  
**Status:** READY FOR PHASE 0 EXECUTION  
**Decisions Driving This:** ADR-0367 (Master Orchestration), ADR-0368 (Extensibility Contract)

---

## Adversarial Review Summary (5 Critical Assumptions)

### 1. Event-Ordering SLA: Causal Dependencies Must Be Pre-Documented
- **Risk:** Brain subsystems have implicit causal chains; misclassification → out-of-order events → Panel-Registry corruption
- **Mitigation:** Phase 0 Week 1 (2d): Brain event audit, full DAG, dependency validation
- **Gate:** Event audit complete, no conflicts

### 2. Audit-Durability 100ms SLA: Under-Tested at Scale
- **Risk:** Event throughput (50–100 evt/sec) may exceed 100ms SLA under load
- **Mitigation:** Phase 0 Week 1-2 (3d load test): baseline p50/p99 latency, adjust SLA or defer async-write
- **Gate:** Latency profile validated, alert wiring live

### 3. Panel-Registry Mutations Not Atomic; Torn Reads Risk
- **Risk:** Concurrent panel installs + UI reads → partial state visibility
- **Mitigation:** Phase 0 Week 2 (2d): Define atomicity contract (single-writer vs MVCC), test (100 installs + 100 reads)
- **Gate:** Mutation contract chosen, concurrent test green

### 4. ADR-0300 Design Review: Approval Criteria Undefined
- **Risk:** Review completes, Architect + shumway disagree → rework cascade, slip 2–3 weeks
- **Mitigation:** Phase 0 Week 1 (2d): Draft Design Review Charter (questions, acceptance criteria, tiebreaker), pre-sign-off
- **Gate:** Charter signed by both; design review has **zero ambiguity on approval criteria**

### 5. Phase 1a: No Rework Budget; Assumes Linear Waterfall
- **Risk:** Historical: ADR-0300 itself discovered issues mid-impl; current plan repeats pattern
- **Mitigation:** Phase 0 Week 1.5–2 (3d Micro-Design): design scrutiny for all 7 ADRs, conflict matrix built before coding
- **Gate:** Conflict matrix resolved, no high-risk design conflicts

---

## 5-Phase Timeline (Weeks 1–8)

### PHASE 0: PRE-FLIGHT (Weeks 1–2, 84h)

**Goal:** Resolve conflicts, pre-schedule critical reviews, define SLAs

| Week | Task | Owner | Hours | Blocker? |
|------|------|-------|-------|----------|
| 1 | ADR-0367/0368 Adversarial Review | Code Review Panel | 40 | ✅ YES |
| 1 | Brain Event-Ordering SLA Audit | Brain Maintainer | 20 | ✅ YES |
| 1 | ADR-0300 Design Review Charter | Architect | 12 | ✅ YES |
| 1 | Console P7 Scope Finalization | Console Maintainer | 12 | ⚠️ HIGH |
| 1-2 | ADRs 0296–0301 Micro-Design | shumway + Architect | 40 | ✅ YES |
| 2 | Audit-Durability Load-Test | Brain Maintainer | 20 | ⚠️ HIGH |
| 2 | Panel-Registry Atomicity Design | Console Maintainer | 12 | ⚠️ HIGH |

**Success Criteria:**
- ✅ Zero high-risk findings from ADR review
- ✅ Brain event audit complete (all events, causal deps, tiers)
- ✅ ADR-0300 charter signed off (Architect + shumway)
- ✅ Console P7 scope finalized (in-scope or deferred)
- ✅ ADRs 0296–0301 design conflict matrix resolved
- ✅ Audit latency SLA validated/adjusted + alert wiring

---

### PHASE 1a: MASTER REFACTORING ADRs 0296–0301 (Weeks 2.5–3.5, 83h)

**Goal:** Foundation security + compliance implementation

| Week | ADR | Task | Owner | Hours | Blocker? | Notes |
|------|-----|------|-------|-------|----------|-------|
| 2.5 | 0296, 0295 | Impl + Test (parallel) | shumway | 22 | – | Input Validator + File Hardener |
| 2.5–3 | 0297, 0298 | Impl + Test (parallel) | shumway | 21 | – | PII Detection + Queue Corruption |
| 3 | **0299** | Audit Durability (CRITICAL) | shumway | 15 | ✅ **BLOCKS 0300+0301** | Must complete before next block |
| 3.5 | 0300 | Dual-Gate Pipeline (post design-review) | shumway | 10 | ✅ BLOCKS 0301 | Impl after charter signed |
| 3.5 | 0301 | Pipeline Call-Site Wiring (50+ sites) | shumway | 15 | – | E2E wiring proof required |
| 3.5 | – | Adversarial Review K=1–K=5 | Code Review Panel | 20 | ✅ YES | 0 high-risk findings gate |

**Critical Path:** ADRs 0296–0299 (sequential due to dependencies) → ADR-0300 (depends on design review) → ADR-0301

**Success Criteria:**
- ✅ All 7 ADRs merged to main
- ✅ Zero test regressions
- ✅ Audit-chain verified (genesis → latest, no gaps)
- ✅ Feature flags default-OFF
- ✅ 50+ entry points verified reachable
- ✅ Adversarial review K=1–K=5 passed (0 high-risk)

---

### PHASE 1b: BRAIN EVENT-ORDERING SLA (Weeks 3.5–4, 24h)

**Goal:** Event delivery reliability (Tier-1/2/3 queues + timeouts)

| Week | Task | Owner | Hours | Blocker? |
|------|------|-------|-------|----------|
| 3.5–4 | EventBus Tier-1/2/3 + Request Timeouts | Brain Maintainer | 10 | – |
| 4 | Load-Test Event Delivery (50+ events, SLA validation) | Brain Maintainer | 8 | – |
| 4 | Integration: Brain → Console → Refactoring | Code Review Panel | 6 | – |

**Success Criteria:**
- ✅ EventBus Tier-1/2/3 shipped + tested
- ✅ Request timeouts enforced (Tier-1: 30s, Tier-2: 10s)
- ✅ 50+ events, zero loss, correct ordering
- ✅ Latency profile documented (p50/p99 per tier)
- ✅ Integration e2e: 1000 events, 100% delivery

---

### PHASE 2: CONSOLE INTEGRATION + AUDIT WIRING (Weeks 4–5, 58h)

**Goal:** Panel-Registry + Audit-Chain binding, atomicity guarantee

| Week | Task | Owner | Hours | Blocker? |
|------|------|-------|-------|----------|
| 4 | ADR-0366 Audit Binding (Panel events → audit-handler) | Console Maintainer | 8 | – |
| 4 | Panel-Registry Atomicity (mutex-based, concurrent test) | Console Maintainer | 8 | – |
| 4–5 | P7 Panel-Mount-UI (if in-scope) | Console Maintainer | 10 | ⚠️ SCOPE-DEPENDENT |
| 5 | Nav-Gating via AI-Panels | Console Maintainer | 6 | – |
| 5 | Compliance Audit Trail E2E (panel → audit verify) | Code Review Panel | 6 | – |
| 5 | Console Integration Test (Brain + Console + Refactoring) | Code Review Panel | 10 | – |

**Success Criteria:**
- ✅ Panel creation events logged in audit-chain
- ✅ Panel-Registry atomicity verified (0 torn reads under 100 installs + 100 reads)
- ✅ P7 panel-mount-UI shipped OR documented for Phase 3
- ✅ Dynamic nav from panel-registry working
- ✅ Brain → Console → Audit full loop verified, SLA met

---

### PHASE 3: E2E REVIEW + PRODUCTION GATE (Weeks 6–8, 80h)

**Goal:** System validation, security/compliance audit, GO/NO-GO decision

| Week | Task | Owner | Hours | Blocker? |
|------|------|-------|-------|----------|
| 6 | Integrated System Adversarial Review | Code Review Panel | 25 | ✅ YES |
| 6 | Performance + Latency Profiling (all subsystems) | Brain Maintainer | 10 | – |
| 7 | Security Assessment (event delivery, audit integrity, auth) | Security Lead | 15 | ✅ YES |
| 7 | Compliance Matrix (GDPR Art. 30/32, EU AI Act Art. 50) | Code Review Panel | 10 | ✅ YES |
| 7 | Documentation + Runbooks | Brain Maintainer | 8 | – |
| 8 | Production Readiness Gate (GO/NO-GO decision) | shumway + Architect | 12 | ✅ **DECISION POINT** |

**Success Criteria:**
- ✅ 0 high-risk findings from integrated system review
- ✅ Performance SLAs met (event latency p99 ≤100ms, audit write p99 ≤150ms, registry ops <10ms)
- ✅ Security checklist passed
- ✅ Compliance matrix passed (GDPR + EU AI Act)
- ✅ Documentation complete + tested
- ✅ **PRODUCTION READINESS SIGN-OFF** (shumway + Architect: GO or NO-GO with next steps)

---

## Ownership Matrix

| Role | Phase | Responsibility | Hours | Notes |
|------|-------|-----------------|-------|-------|
| **shumway** | 0 | ADR micro-design (0296–0301), design review pre-work | 40 | Pre-flight |
| **shumway** | 1a | Implement ADRs 0296–0301 | 83 | Critical path; ADR-0299 gates downstream |
| **Brain Maintainer** | 0 | Event-ordering audit, load-test audit | 40 | Pre-flight SLA definition |
| **Brain Maintainer** | 1b | EventBus Tier-1/2/3, latency profiling | 18 | Event reliability |
| **Brain Maintainer** | 3 | Performance + Latency Profiling, Runbooks | 18 | System validation |
| **Console Maintainer** | 0 | P7 scope, atomicity design | 24 | Pre-flight scoping |
| **Console Maintainer** | 2 | Audit wiring, panel-registry atomicity, P7 impl, nav-gating | 32 | Console integration |
| **Architect** | 0 | ADR-0300 design review charter | 12 | Pre-flight gating |
| **Architect** | 3 | Production readiness sign-off | 12 | GO/NO-GO decision |
| **Code Review Panel** | 0 | ADR adversarial review | 40 | Pre-flight gating |
| **Code Review Panel** | 1a | Adversarial review K=1–K=5 | 20 | Phase 1a gate |
| **Code Review Panel** | 1b | Integration e2e test | 6 | Event delivery proof |
| **Code Review Panel** | 2 | Compliance e2e, console integration test | 16 | Phase 2 validation |
| **Code Review Panel** | 3 | System review, compliance matrix | 35 | Phase 3 gates |
| **Security Lead** | 3 | Security assessment | 15 | Phase 3 gate |

**Total:** ~355 hours over 8 weeks (average 44h/week)

---

## Critical Path (Dependency Graph)

```
PHASE 0 (Weeks 1–2) — Sequential Pre-Requisites:
  ADR-0367/0368 Adversarial Review (40h)
    ↓ (findings resolved)
  Brain Event-Ordering SLA Audit (20h) + ADR-0300 Design Charter (12h) [PARALLEL]
    ↓ (SLA defined, charter approved)
  ADR Micro-Design (40h) + Audit Load-Test (20h) + Panel Atomicity Design (12h) [PARALLEL]
    ↓ (conflicts resolved, SLA validated, design reviewed)

PHASE 1a (Weeks 2.5–3.5) — Main Implementation Path:
  ADR-0296, ADR-0295 [PARALLEL, 22h]
    ↓
  ADR-0297, ADR-0298 [PARALLEL, 21h]
    ↓
  ADR-0299 [CRITICAL, 15h] ← **BLOCKS ADR-0300 + ADR-0301**
    ↓ (audit-chain verified)
  ADR-0300 [DEPENDS on design charter, 10h]
    ↓ (design approved, impl complete)
  ADR-0301 [DEPENDS on 0300, 15h]
    ↓ (50+ call-sites verified)
  Adversarial Review K=1–K=5 [GATE, 20h]
    ↓ (0 high-risk findings)

PHASE 1b (Weeks 3.5–4) — Parallel to Phase 1a end:
  EventBus Tier-1/2/3 + Request Timeouts [10h]
    ↓
  Load-Test Event Delivery [8h]
    ↓
  Integration: Brain → Console → Refactoring [6h]

PHASE 2 (Weeks 4–5, starts after Phase 1a PASS):
  ADR-0366 Audit Binding [8h] + Panel-Registry Atomicity [8h] [PARALLEL]
    ↓
  P7 Panel-Mount-UI [10h, IF IN-SCOPE]
    ↓
  Nav-Gating [6h]
    ↓
  Console Integration E2E [10h]

PHASE 3 (Weeks 6–8, starts after Phase 2 PASS):
  Integrated System Adversarial Review [25h] + Latency Profiling [10h] [PARALLEL]
    ↓ (0 high-risk findings)
  Security Assessment [15h] + Compliance Matrix [10h] [PARALLEL]
    ↓ (passed)
  Documentation [8h]
    ↓
  Production Readiness Gate [12h] ← **GO/NO-GO DECISION**
```

### Critical Path Items (Any Slip Cascades)

1. **ADR-0299 (Audit Durability)** — blocks ADR-0300/0301 (1 week slip = 2 week cascade)
2. **ADR-0300 Design Review** — must complete before coding (2 day slip = 1 week cascade)
3. **Phase 1a Adversarial Review K=1–K=5** — gates Phase 1 merge + Phase 2 start (1 week slip)
4. **Phase 3 Production Readiness Gate** — GO/NO-GO decision for canary rollout (decision point)

---

## Gate Criteria per Phase

### PHASE 0 GATE (End of Week 2)
**PASS:** All 7 success criteria met → proceed to Phase 1a  
**FAIL:** Any blocker unresolved → escalate, do not proceed

### PHASE 1a GATE (End of Week 3.5)
**PASS:** All 7 ADRs merged, adversarial review 0 high-risk → proceed to Phase 1b + Phase 2  
**FAIL:** High-risk findings or merge blockers → iterate, hold Phase 2

### PHASE 1b GATE (End of Week 4)
**PASS:** Event-ordering SLA validated, integration e2e green → proceed to Phase 2  
**FAIL:** Latency SLA violated or event loss detected → adjust SLA or defer async-write, iterate

### PHASE 2 GATE (End of Week 5)
**PASS:** Panel-Registry atomic, audit wiring verified, P7 scope clear → proceed to Phase 3  
**FAIL:** Torn reads or audit gaps → iterate, hold Phase 3

### PHASE 3 GATE (End of Week 8)
**PASS:** All security/compliance/performance gates passed + sign-off → **PRODUCTION READY**  
**FAIL:** Unresolved high-risk item → NO-GO, define next iteration

---

## Risk Mitigations (Tied to Adversarial Findings)

| Risk | Mitigation | Phase | Responsible |
|------|-----------|-------|-------------|
| Event-ordering causal deps unclear | Event audit + DAG + dependency validation | 0 | Brain Maintainer |
| Audit-durability SLA violated at scale | Load-test baseline, adjust SLA or defer async-write | 0 | Brain Maintainer |
| Panel-Registry torn reads possible | Atomicity contract (single-writer) + concurrent test | 0 | Console Maintainer |
| ADR-0300 design review deadlock | Pre-drafted charter, tiebreaker defined | 0 | Architect |
| Phase 1a hidden design conflicts | Micro-design pre-impl, conflict matrix | 0 | shumway + Architect |
| ADR implementation slips into rework | Adversarial review gates, design scrutiny | 1a | Code Review Panel |
| Event delivery under 100 evt/sec fails | Load-test, alert wiring | 1b | Brain Maintainer |
| Panel-Registry + Audit not synced | E2E compliance test (creation → audit entry) | 2 | Code Review Panel |
| System integration failures | Integrated system adversarial review | 3 | Code Review Panel |

---

## Success Definition

**System is production-ready when:**
1. ✅ All Phase 0–3 gates passed (0 high-risk findings gate)
2. ✅ ADR-0367 + ADR-0368 formally approved
3. ✅ Brain v0.2 event-ordering SLA <100ms (p99) validated under 100 evt/sec load
4. ✅ Console Panel-Registry mutations atomic (0 torn reads under concurrent load)
5. ✅ Audit-chain integrity verified (genesis → latest, no gaps, hash-chain valid)
6. ✅ Security assessment passed (no MITM/replay/auth-bypass risks)
7. ✅ Compliance matrix passed (GDPR Art. 30/32 + EU AI Act Art. 50)
8. ✅ Documentation complete + runnable examples tested
9. ✅ **Production Readiness Sign-Off** signed by shumway + Architect

---

## Next Action

**PHASE 0 STARTS NOW (Weeks 1–2).**

1. **Week 1 Parallel Kickoff:**
   - Code Review Panel: ADR-0367/0368 adversarial review (40h)
   - Brain Maintainer: Event-ordering SLA audit (20h)
   - Architect: ADR-0300 design review charter (12h)
   - Console Maintainer: P7 scope finalization (12h)

2. **Week 1.5–2 (After initial blockers resolved):**
   - shumway + Architect: ADRs 0296–0301 micro-design (40h)
   - Brain Maintainer: Audit-durability load-test (20h)
   - Console Maintainer: Panel-Registry atomicity design (12h)

3. **Week 2 Handoff to Phase 1a:**
   - Merge findings into this plan
   - Confirm Phase 0 gates all passed
   - Begin ADR implementation (0296–0301)

---

**Document Version:** v1.0  
**Last Updated:** 2026-08-23  
**Next Review:** Weekly during execution (Phase 0 kickoff)
