# Security Orchestrator Skill (ADR-2031)

**Phase 10 Stream 2** — 8-12 weeks (Oct 3 – Dec 5, 2026)  
**Deliverable:** 2,100 LoC production-ready Skill  
**Status:** Week 1 scaffold complete; ready for Stream 2 lead handoff

---

## What Is It?

Security Orchestrator is an **OS-level Skill** that detects threat patterns from the audit trail and automatically tightens security gates. It is **NOT** a policy framework—it is a **dynamic response engine** that reacts to observed threats by tightening existing gates (auth failures, overrides, data flows, rate limits), never creating new gates or disabling compliance mechanisms.

**Core Principle:** All policy changes are **audited, reversible (TTL), and constrained** (never bypass L44 house-rules or L16 consent gates).

---

## Key Features

### Five Threat Detection Patterns
1. **Brute Force:** >N failed auth attempts in T minutes → tighten auth gate
2. **Privilege Escalation:** >N unauthorized overrides → disable overrides
3. **Data Exfiltration:** >N high-risk flows to external → reduce flow limit
4. **Distributed Attack:** >N unique IPs targeting one resource → reduce rate limit
5. **Anomalous Behavior:** User behavior deviation from baseline → audit + alert (manual review)

### Automatic Policy Response
- Confidence >= 0.75 → **auto-tighten** (fail-closed)
- Confidence <0.75 → **audit logged, no auto-action**
- All tightenings have TTL (default 1h); auto-revert if threat clears

### Compliance-First Design
- ✅ **Every change audited** — Immutable, hash-chained SecurityAuditEvent
- ✅ **Tenant isolated** — All events tagged tenant_id; no cross-tenant leakage
- ✅ **Never bypass compliance** — Cannot disable L44 house-rules or L16 consent gates
- ✅ **Reversible** — TTL-based auto-revert prevents permanent lockdown
- ✅ **Learning-integrated** — Feedback loop (ADR-0314) tunes confidence thresholds

---

## Directory Structure

```
core/skills/os_skills/security_orchestrator/
├── __init__.py                          # Module exports
├── security_orchestrator.py             # Main Skill class (250 LoC)
├── threat_detection.py                  # Pattern matching (450 LoC)
├── policy_engine.py                     # State machine (400 LoC)
├── audit_events.py                      # Immutable schemas (150 LoC)
├── routes/
│   └── security_orchestrator.py         # Console endpoints (350 LoC) — Week 2
├── tests/
│   ├── __init__.py
│   ├── test_skeleton.py                 # Test plan (99 tests documented)
│   ├── test_threat_detection.py         # Unit + E2E + adversarial
│   ├── test_policy_engine.py            # Unit + E2E + adversarial
│   ├── test_audit_integration.py        # Unit + E2E + adversarial
│   └── test_security_orchestrator.py    # Integration tests
└── docs/
    └── THREAT_MODEL.md                  # 20+ attack scenarios
```

---

## Core Modules

### SecurityOrchestratorSkill
Main entry point. Orchestrates: detect → respond → record flow.

```python
skill = SecurityOrchestratorSkill(
    tenant_id="acme-corp",
    audit_backend=audit_service,
    learning_backend=learning_service,
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

### ThreatDetector
Pattern matching from audit events. Returns ThreatSignal with confidence (0.0–1.0).

```python
detector = ThreatDetector(
    window_minutes=5,
    brute_force_threshold=5,
    priv_esc_threshold=3,
    data_exfil_threshold=10,
    distributed_threshold=20,
)

threat = detector.analyze_auth_events(auth_events)
if threat and threat.is_actionable(threshold=0.75):
    # Tighten policy
```

### PolicyEngine
Dynamic state machine. Tightens gates, checks TTL, reverts.

```python
engine = PolicyEngine()

# Tighten in response to threat
result = engine.tighten_policy(
    threat_signal={...},
    audit_backend=audit_service,
    tenant_id="acme-corp",
)

# Check TTL and revert
reverts = engine.check_ttl_and_revert(
    audit_backend=audit_service,
    tenant_id="acme-corp",
)
```

### SecurityAuditEvent
Immutable audit event schema (ADR-0232/0233, ADR-0537). Frozen dataclass; never modified after creation.

```python
event = SecurityAuditEvent(
    tenant_id="acme-corp",
    timestamp="2026-09-22T12:34:56Z",
    event_type="security_policy_tightened",
    threat_type=ThreatType.BRUTE_FORCE,
    threat_confidence=0.92,
    policy_gate=PolicyGate.AUTH_GATE,
    old_threshold=3,
    new_threshold=1,
    lom="os.security_orchestrator.policy_engine:123",
    lom_hash="sha256(...)",
    hash="sha256(...)",
    prev_hash="sha256(...)",
)
```

---

## Testing (99 Tests)

### Unit Tests (30)
- ThreatDetector: 10 tests
- PolicyEngine: 10 tests
- AuditEvents: 5 tests
- SecurityOrchestratorSkill: 5 tests

### E2E Tests (35)
- Brute force: 5 tests
- Priv escalation: 5 tests
- Data exfiltration: 5 tests
- Distributed attack: 5 tests
- Learning integration: 5 tests
- TTL & auto-revert: 5 tests

### Adversarial Tests (34)
- False positives: 8 tests
- Security constraints: 12 tests
- Load & stress: 8 tests
- Edge cases: 6 tests

**All tests use real interfaces** (no mocking the entry point). E2E tests verify:
1. Threat detected
2. Policy tightened
3. Audit event emitted + hash-chain verified
4. Attack blocked
5. TTL reverts policy

---

## Console Routes (Week 2)

```
GET /v1/console/security/threats
  → [ThreatSignal, ...]
  → Used by: Dashboard, alerts

POST /v1/console/security/feedback
  → {"threat_id": str, "feedback": "accurate|false_alarm|partial", "reasoning": str}
  → Used by: Learning loop (ADR-0314)

GET /v1/console/security/policy
  → current SecurityPolicy + active_tightenings + MTTR
  → Used by: Dashboard, observability

POST /v1/console/security/override
  → {"gate": "auth_gate", "action": "tighten|loosen", "ttl_seconds": int}
  → Used by: Operator manual override (audit-logged)
```

---

## Week-by-Week Timeline

| Week | Phase | Deliverables | Status |
|---|---|---|---|
| 1 | Kickoff + Foundation | Core modules (650 LoC), test skeleton, docs | ✅ **DONE** |
| 2 | Unit Tests + Routes | 30 unit tests, 4 console endpoints, CI/CD | Week 2–9 |
| 3 | E2E + Threat Model | 35 E2E tests, threat model (20+ scenarios), dashboard | Week 10–16 |
| 4–6 | Adversarial + Hardening | 34 adversarial tests, false positive tuning, load test, code review | Week 17–30 |
| 7–8 | Integration + Hardening | L16/L34 integration, staging soak test (7 days) | Week 31–45 |
| 9–10 | Production Canary + Gate 4 | Canary rollout (5%→100%), final gate criteria | Week 46–60 |
| 11–12 | Release + Handoff | Production deployment, incident runbooks, team handoff | Week 61–75 |

---

## Success Criteria (Hard Stop at Week 12)

| Criterion | Target | Verification |
|---|---|---|
| **LoC** | 2,100 | wc -l *.py |
| **Tests** | 99 passing | pytest -v |
| **Coverage** | >95% | pytest --cov |
| **Threat Model** | 20+ scenarios | THREAT_MODEL.md |
| **MTTR** | <5 minutes | Load test |
| **False Positive Rate** | <5% | Baseline tuning |
| **Security Findings** | 0 critical, ≤2 high | Security review |
| **ADR-2031** | ACCEPTED | Frontmatter |
| **Staging Soak** | 7 days, 0 critical | Monitoring report |
| **Production Canary** | 5% traffic, 3 days | Canary metrics |

---

## Key Design Decisions (Committed)

1. **Confidence Threshold: 0.75** — Auto-tighten >= 0.75; audit-only < 0.75
2. **TTL: 3,600 seconds** — Auto-revert after 1h if threat clears
3. **Audit-First** — Every change logged; zero silent operations
4. **Never Bypass Compliance** — Cannot disable L44 house-rules or L16 consent
5. **Tenant Isolation** — All events tagged tenant_id; no cross-tenant leakage

---

## References

- **ADR-2031:** Security Orchestrator Skill (main architecture document)
- **ADR-0232/0233:** Audit Chain (immutable trail, hash-chaining)
- **ADR-0314:** Learning Infrastructure (feedback loop)
- **ADR-0532:** Skills 2.0 Architecture (skill framework)
- **ADR-0537:** LoM Cryptographic Binding (Line of Moral Responsibility)
- **L44:** House-Rules Enforcement (acceptable-use policy, non-disableable)
- **L16:** Consent Gates (user consent, non-negotiable)

---

## For Stream 2 Lead

1. **Read first:** ADR-2031, STREAM2_HANDOFF_DOCUMENT.md, THREAT_MODEL.md
2. **Week 1 Status:** See STREAM2_WEEKLY_STATUS.md (template)
3. **Test Plan:** See test_skeleton.py (99 tests documented)
4. **Questions?** See STREAM2_HANDOFF_DOCUMENT.md § Questions for Clarification

---

**Status:** Week 1 scaffold ✅  
**Next:** Week 2 handoff to Stream 2 lead (kickoff Sep 26)  
**Created:** 2026-09-22 (Claude Haiku 4.5)

