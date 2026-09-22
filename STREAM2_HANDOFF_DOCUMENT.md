# STREAM 2 HANDOFF — Security Orchestrator Skill (ADR-2031)

**For:** Stream 2 Lead  
**Date:** 2026-09-26 (Kickoff)  
**Timeline:** 8-12 weeks (Oct 3 – Dec 5)  
**Deliverable:** 2,100 LoC production-ready Skill

---

## Executive Summary

Stream 2 implements **Security Orchestrator**, an OS-level Skill that detects threat patterns from the audit trail and automatically tightens security gates. This is NOT a policy framework—it is a **dynamic response engine** that reacts to observed threats by tightening existing gates (auth failures, overrides, data flows, rate limits), never creating new gates or disabling compliance mechanisms.

**Core Principle:** All policy changes are:
- ✅ **Audited** — Every change logged in immutable, hash-chained audit events
- ✅ **Reversible** — TTL-based auto-revert after 1h (prevents permanent lockdown)
- ✅ **Constrained** — Never bypass house-rules (L44) or consent gates (L16)
- ✅ **Learned** — Integrated with ADR-0314 learning loops for confidence tuning

**Success Criteria:** 99 tests passing (30 unit + 35 E2E + 34 adversarial), threat model 20+ scenarios, MTTR < 5 min, false positive rate < 5%, 0 critical security findings.

---

## Architecture Overview

### Five Core Modules (2,100 LoC Total)

| Module | LoC | Purpose | Status |
|---|---|---|---|
| `security_orchestrator.py` | 250 | Main Skill class; orchestrates detect→respond→record flow | ✅ Week 1 |
| `threat_detection.py` | 450 | Pattern matching; 5 threat types (brute force, priv esc, data exfil, distributed, anomaly) | ✅ Week 1 |
| `policy_engine.py` | 400 | Dynamic state machine; tightens gates, checks TTL, reverts | ✅ Week 1 |
| `audit_events.py` | 150 | Immutable event schema (ADR-0232/0233, ADR-0537); tenant isolation | ✅ Week 1 |
| `routes/security_orchestrator.py` | 350 | HTTP endpoints (threats, policies, feedback, override) | Week 2 |
| **Tests** | 700 | 99 tests (unit/E2E/adversarial); threat model 20+ scenarios | Weeks 2-6 |
| **Docs + UI** | 350 | Console dashboard, Grafana dashboards, runbooks | Weeks 6-8 |

**Foundation (Week 1):** All core modules implemented + test skeleton + weekly status template.

---

## Data Flow

```
Audit Trail Events
    ↓
ThreatDetector.analyze_*() ← detects patterns (5 types)
    ↓ (if confidence >= 0.75)
ThreatSignal (pattern, confidence, severity, affected_users/ips)
    ↓
SecurityOrchestratorSkill.respond_to_threat()
    ↓
PolicyEngine.tighten_policy() ← increases security
    ↓
SecurityAuditEvent (immutable, hash-chained)
    ↓ (via audit_backend.write_event)
Audit Trail (GDPR Art. 30, 32)
    ↓
LearningBackend.record_skill_execution() ← feedback loop (ADR-0314)

TTL Check (every minute)
    ↓ (if threat clears OR TTL expires)
PolicyEngine.check_ttl_and_revert()
    ↓
restore gate to baseline + emit "security_policy_reverted" event
```

---

## Threat Patterns (5 Implemented)

| Pattern | Detection Rule | Response | TTL | Audit Event |
|---|---|---|---|---|
| **Brute Force** | >N failed auth in T minutes | Reduce auth_max_failures | 1h | `security_threat_detected`, `security_policy_tightened` |
| **Priv Escalation** | >N unauthorized overrides | Disable overrides (set to 0) | 1h | `security_threat_detected`, `security_policy_tightened` |
| **Data Exfiltration** | >N high-risk flows to external | Reduce data_high_risk_flow_limit | 1h | `security_threat_detected`, `security_policy_tightened` |
| **Distributed Attack** | >N unique IPs targeting one resource | Reduce rate_limit_requests_per_minute | 1h | `security_threat_detected`, `security_policy_tightened` |
| **Anomalous Behavior** | User/system deviation from baseline | Context-dependent tightening | 1h | `security_threat_detected` (no auto-response; logs for review) |

---

## Key Design Decisions (Committed)

### 1. Confidence-Gated Threshold: 0.75

- Threats < 0.75 confidence: logged but **no auto-tightening**
- Threats >= 0.75 confidence: **auto-tighten** (fail-closed, err on side of security)
- Rationale: False positives (0.75) < False negatives (0.25)

### 2. TTL-Based Auto-Revert: 3,600 seconds

- Every tightening has TTL (default 1h)
- After TTL, if threat clears, policy **auto-reverts** to baseline
- Rationale: Prevents "emergency becomes permanent"; maintains availability
- Monitoring: Check TTL every minute (can be tuned)

### 3. Audit-First: Every Change Logged

- **Zero silent operations.** Every tightening + revert = immutable audit event
- Hash-chained to previous event (ADR-0232/0233)
- Includes LoM (Line of Moral Responsibility) cryptographic binding (ADR-0537)
- Rationale: GDPR Art. 30/32 compliance + operator visibility

### 4. Never Bypass Compliance Mechanisms

- Policy engine CANNOT disable L44 house-rules (acceptable-use policy)
- Policy engine CANNOT disable L16 consent gates
- Policy engine CANNOT disable audit chain
- Rationale: Load-bearing security constraints (non-negotiable)

### 5. Tenant Isolation (Every Event)

- All audit events tagged with `tenant_id`
- All threat detection per-tenant
- All policy state per-tenant
- Rationale: GDPR Art. 5 (data minimization), Art. 32 (security)

---

## Interfaces

### 1. SecurityOrchestratorSkill (Main Entry Point)

```python
skill = SecurityOrchestratorSkill(
    tenant_id="acme-corp",
    audit_backend=audit_service,
    learning_backend=learning_service,
    window_minutes=5,  # ThreatDetector sliding window
    brute_force_threshold=5,  # >5 failed attempts
    # ... other thresholds
)

# Detect threats
threat_signal = skill.detect_threats(auth_events, "brute_force")

# Auto-respond
if threat_signal:
    result = skill.respond_to_threat(threat_signal)
    skill.record_response(result)  # → learning backend

# Revert expired tightenings
skill.check_and_revert_ttl()

# Current state
posture = skill.get_security_posture()  # → console dashboard
```

### 2. ThreatDetector (Pattern Matching)

```python
detector = ThreatDetector(
    window_minutes=5,
    brute_force_threshold=5,
    priv_esc_threshold=3,
    data_exfil_threshold=10,
    distributed_threshold=20,
)

# Returns ThreatSignal or None
threat = detector.analyze_auth_events(auth_events)
threat = detector.analyze_privilege_escalation_events(override_events)
threat = detector.analyze_data_exfiltration_events(data_flows)
threat = detector.analyze_distributed_attack(request_events)

# Check if actionable
if threat and threat.is_actionable(threshold=0.75):
    # Tighten policy
```

### 3. PolicyEngine (Dynamic State Machine)

```python
engine = PolicyEngine()

# Tighten in response to threat
result = engine.tighten_policy(
    threat_signal={"pattern": "brute_force_auth", "confidence": 0.92, ...},
    audit_backend=audit_service,
    tenant_id="acme-corp",
)

# Check TTL and revert
reverts = engine.check_ttl_and_revert(
    audit_backend=audit_service,
    tenant_id="acme-corp",
)

# Current state
policy = engine.get_current_policy()  # Returns SecurityPolicy
active = engine.get_active_tightenings()  # Dict[tightening_id, PolicyTightening]
```

### 4. Console Routes (Week 2)

```
GET /v1/console/security/threats
  → Returns: [ThreatSignal, ...]
  → Used by: Dashboard, alerts

POST /v1/console/security/feedback
  → Input: {"threat_id": str, "feedback": "accurate|false_alarm|partial", "reasoning": str}
  → Output: {"success": bool, "feedback_id": str}
  → Used by: Learning loop (ADR-0314)

GET /v1/console/security/policy
  → Returns: current SecurityPolicy + active_tightenings + MTTR
  → Used by: Dashboard, observability

POST /v1/console/security/override
  → Input: {"gate": "auth_gate", "action": "tighten|loosen", "ttl_seconds": int}
  → Output: {"success": bool, "new_value": Any, "audit_event_id": str}
  → Used by: Operator manual override (audit-logged)
```

---

## Week-by-Week Roadmap

### Week 1 (Sep 26–Oct 2): Kickoff + Foundation ✅ **DONE**
- ✅ ADR-2031 ACCEPTED
- ✅ Core modules: security_orchestrator.py, threat_detection.py, policy_engine.py, audit_events.py
- ✅ Test skeleton: 99 tests documented (30 unit + 35 E2E + 34 adversarial)
- ✅ Weekly status template ready

### Week 2 (Oct 3–9): Unit Tests + API Routes
- [ ] 30 unit tests passing
- [ ] Console routes implemented (4 endpoints)
- [ ] Learning backend wired
- [ ] Local test environment + CI/CD

### Week 3 (Oct 10–16): E2E Tests + Threat Model
- [ ] 35 E2E tests passing
- [ ] Threat model: 20+ attack scenarios documented
- [ ] Console dashboard panel (React)
- [ ] Grafana dashboards

### Weeks 4–6 (Oct 17 – Oct 30): Adversarial Tests + Hardening
- [ ] 34 adversarial tests passing (all 99 total)
- [ ] False positive tuning: <5% rate
- [ ] Load test: 10K events/sec
- [ ] **Gate 3 (Week 6):** All tests ✅, 0 critical findings, ready for staging

### Weeks 7–8 (Nov 7–20): Integration + Hardening
- [ ] Integrate with L16 (consent) + L34 (data flow guard)
- [ ] Staging deployment + 7-day soak test
- [ ] Threat response latency < 5 min

### Weeks 9–10 (Nov 21 – Dec 4): Production Canary + Final Gate
- [ ] **Gate 4 (Week 10):** All 99 tests ✅, security review ✅, staging soak ✅
- [ ] Production canary: 5% traffic, 3 days stable
- [ ] MTTR < 5 min verified

### Weeks 11–12 (Dec 5–15): Release + Handoff
- [ ] Production rollout: 100% traffic
- [ ] Incident response runbooks live
- [ ] Team handoff to Operations

---

## Testing Strategy (99 Tests)

### Unit Tests (30)
- ThreatDetector: 10 tests (pattern matching, confidence scoring)
- PolicyEngine: 10 tests (gate tightening, TTL, restoration)
- AuditEvents: 5 tests (immutability, schema, isolation)
- SecurityOrchestratorSkill: 5 tests (orchestration)

### E2E Tests (35)
- Brute force: 5 tests (detect → tighten → block → clear → revert)
- Priv escalation: 5 tests
- Data exfiltration: 5 tests
- Distributed attack: 5 tests
- Learning integration: 5 tests (feedback → tuning → confidence scores)
- TTL & auto-revert: 5 tests

### Adversarial Tests (34)
- False positives: 8 tests (batch jobs, CDN, monitoring tools, etc.)
- Security constraints: 12 tests (cannot bypass L44, L16, audit chain, etc.)
- Load & stress: 8 tests (10K events/sec, 100 concurrent users, multi-tenant isolation)
- Edge cases: 6 tests (malformed timestamps, zero confidence, missing backends, etc.)

**All tests use real interfaces** (no mocking the entry point). E2E tests verify:
1. Threat detected
2. Policy tightened
3. Audit event emitted + hash-chain verified
4. Attack blocked
5. TTL reverts policy

---

## Compliance Requirements (Non-Negotiable)

| Requirement | ADR | Enforcement | Testing |
|---|---|---|---|
| **Immutable Audit Trail** | ADR-0232/0233 | SecurityAuditEvent frozen; no deletes/rewrites | test_audit_event_immutability |
| **Hash-Chained Events** | ADR-0232/0233 | prev_hash links every event; verify chain integrity | test_audit_chain_intact (E2E) |
| **Tenant Isolation** | ADR-0007 | All events tagged tenant_id; queries filter by tenant | test_e2e_cross_tenant_isolation |
| **Consent Respect** | L16 (ADR-0016) | Policy tightening respects consent gates | test_adv_cannot_disable_consent_gates_l16 |
| **House-Rules Never Bypass** | L44 (ADR-0005) | Cannot disable acceptable-use policy | test_adv_cannot_disable_house_rules_l44 |
| **LoM Binding** | ADR-0537 | Every audit event includes lom_hash (SHA256) | test_audit_event_lom_binding |
| **TTL-Based Revert** | ADR-2031 | No permanent policy changes; all reversible | test_e2e_threat_clears_policy_reverts |
| **GDPR Art. 30/32** | GDPR | Every decision logged + auditable; no PII in audit | test_adv_cannot_inject_pii_into_audit_logs |

---

## Infrastructure Dependencies (Week 1 Ready)

| Component | Status | Owner | Wiring |
|---|---|---|---|
| **Audit Backend** | ✅ Ready | Phase 9 | `skill.audit_backend.write_event(event)` |
| **Learning Backend** | ✅ Ready (ADR-0314) | Phase 3 | `skill.learning_backend.record_skill_execution(...)` |
| **L16 Consent Gates** | ✅ Ready (Phase 9) | Phase 9 | Check consent in policy_engine.tighten_policy() |
| **L34 Data Flow Guard** | Parallel (Stream 3) | Stream 3 | Hook into data classification thresholds (Week 7) |
| **Console Web-Next** | ✅ Ready | Web | `/v1/console/security/*` routes |
| **Grafana** | ✅ Ready | Observability | Threat metrics dashboard |

---

## Key Code Locations

```
/home/shumway/projects/CorvinOS/core/skills/os_skills/security_orchestrator/
├── __init__.py                   # Module exports
├── security_orchestrator.py      # Main Skill class (250 LoC)
├── threat_detection.py           # Pattern matching (450 LoC)
├── policy_engine.py              # State machine (400 LoC)
├── audit_events.py               # Immutable schemas (150 LoC)
├── routes/
│   └── security_orchestrator.py  # Console endpoints (350 LoC) — Week 2
├── tests/
│   ├── __init__.py
│   ├── test_skeleton.py          # Test plan (99 tests documented)
│   ├── test_threat_detection.py  # Unit + E2E + adversarial tests — Week 2
│   ├── test_policy_engine.py     # Unit + E2E + adversarial tests — Week 2
│   ├── test_audit_integration.py # Unit + E2E + adversarial tests — Week 2
│   └── test_security_orchestrator.py # Integration tests — Week 2
└── docs/
    └── THREAT_MODEL.md           # 20+ attack scenarios — Week 3

Console Dashboard (React):
/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/
├── pages/security-orchestrator.tsx      # Main panel — Week 3
├── components/SecurityDashboard.tsx     # Threats + policy state — Week 3
└── components/ThreatTimeline.tsx        # MTTR visualization — Week 3

ADR (Canonical Location):
/home/shumway/projects/Corvin-ADR/decisions/ADR-2031-security-orchestrator-skill.md
```

---

## Success Criteria (Hard Stop at Week 12)

| Criterion | Target | Verification |
|---|---|---|
| **LoC** | 2,100 | wc -l core/skills/os_skills/security_orchestrator/*.py |
| **Tests** | 99 passing | pytest core/skills/os_skills/security_orchestrator/tests/ -v |
| **Coverage** | >95% | pytest --cov=core.skills.os_skills.security_orchestrator --cov-report=html |
| **Threat Model** | 20+ scenarios | Documented in THREAT_MODEL.md |
| **MTTR** | <5 minutes | Load test + timing verification |
| **False Positive Rate** | <5% | Adversarial tests + baseline workload tuning |
| **Security Findings** | 0 critical, ≤2 high | Security review sign-off |
| **ADR-2031** | ACCEPTED | Frontmatter status field |
| **Staging Soak** | 7 days, 0 critical | Monitoring report |
| **Production Canary** | 5% traffic, 3 days | Canary metrics + alerts |

---

## Known Risks & Mitigations

| Risk | Severity | Mitigation | Owned By |
|---|---|---|---|
| False positives cause denial of service | High | Adversarial test suite (Week 4), tuning phase (Week 4-6) | Stream 2 Lead |
| Learning loop doesn't converge | Medium | Feedback validation + optimizer tuning (Week 7) | Stream 2 Lead + Learning Team |
| Load test reveals scalability issue | Medium | Async queue for audit backend; throttle if needed (Week 5) | Stream 2 Lead |
| Cross-tenant data leak in audit trail | Critical | Unit test + E2E test for isolation (Week 2-3) | Stream 2 Lead + Security |
| TTL revert breaks permanent security requirement | High | Design review + compliance check (Week 1) | Integration Lead |

---

## Handoff Checklist for Stream 2 Lead

Before Kickoff (Sep 26):
- [ ] Read this document + ADR-2031
- [ ] Review STREAM2_WEEKLY_STATUS.md template
- [ ] Understand audit backend interface (how to write events)
- [ ] Understand learning backend interface (feedback recording)
- [ ] Clone repo + run existing tests in core/
- [ ] Set up local development environment

At Kickoff (Sep 26, 10:00 AM UTC):
- [ ] Confirm team members assigned (dev + QA + security)
- [ ] Confirm access to: Corvin-ADR, GitHub, Jira, Slack
- [ ] Review resource allocation (1.2 FTE + shared)
- [ ] Confirm weekly sync schedule (Mondays 10:00 AM UTC)
- [ ] Confirm gate criteria + decision-makers

Week 1 (Sep 27–Oct 1):
- [ ] Set up CI/CD pipeline (GitHub Actions)
- [ ] Plan Week 2 unit tests (detail from test_skeleton.py)
- [ ] Plan console routes (API contract)
- [ ] Kick off threat model (identify 20+ scenarios)
- [ ] EOD Friday: Submit STREAM2_WEEKLY_STATUS.md Week 1 update

---

## Questions for Clarification (Before Starting)

1. **Audit Backend Integration:** How do I import + use the audit backend? Example call?
2. **Learning Backend Interface:** Same question — how do I record skill execution?
3. **Console Routes:** Which framework/patterns to follow for HTTP endpoints?
4. **Threat Model Scope:** Should anomalous behavior pattern be auto-response or audit-only?
5. **TTL Tuning:** Is 3,600 seconds (1h) the right default, or should it be configurable per-threat?
6. **False Positive Budget:** <5% — measured how? Over baseline production workload, or test suite?
7. **MTTR Definition:** <5 min from threat detection to blocked attack (end-to-end latency)?
8. **Release Timeline:** Is Dec 15 a hard deadline, or is Dec 31 acceptable if quality risk remains?

---

**Status:** 🟢 **READY FOR KICKOFF**  
**Last Updated:** 2026-09-22  
**Next Review:** 2026-09-26 (Post-kickoff, refine based on team feedback)

