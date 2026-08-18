# CorvinOS v0.6 Design Index

**Release:** Operator Modeling  
**Timeline:** 8 weeks (2026-09-15)  
**Status:** Design Phase

---

## Quick Navigation

### Core Documents

| Document | Purpose | Status |
|---|---|---|
| **[V0.6_IDEAS.md](V0.6_IDEAS.md)** | Vision & core ideas (5 ideas, metrics, phases) | ✓ Complete |
| **[V0.6_IMPLEMENTATION_PLAN.md](V0.6_IMPLEMENTATION_PLAN.md)** | Detailed implementation (algorithms, APIs, tests) | ✓ Complete |
| **[UPGRADE_v0.5_to_v0.6.md](../UPGRADE_v0.5_to_v0.6.md)** | Migration guide | Pending (Week 8) |

### Architectural Decisions (ADRs)

| ADR | Title | Focus | Status |
|---|---|---|---|
| **[ADR-0383](../../../Corvin-ADR/decisions/ADR-0383-operator-fingerprint-data-model.md)** | Operator Fingerprint Data Model | 4D style model (risk, speed, communication, task affinity) | ✓ Complete |
| **[ADR-0384](../../../Corvin-ADR/decisions/ADR-0384-task-affinity-measurement.md)** | Task Affinity Measurement | Per-task-type success rate + Bayesian update | ✓ Complete |
| **[ADR-0385](../../../Corvin-ADR/decisions/ADR-0385-predictive-guidance-engine.md)** | Predictive Guidance Engine | ARIMA task predictor + suggestions | ✓ Complete |
| **[ADR-0386](../../../Corvin-ADR/decisions/ADR-0386-what-if-replay-architecture.md)** | What-If Replay Architecture | Deterministic snapshots + counterfactual replay | ✓ Complete |

### Concepts (Reusable Methodologies)

| Concept | Title | Methodology | Status |
|---|---|---|---|
| **[CONCEPT-0020](../../../Corvin-ADR/concepts/CONCEPT-0020-operator-style-fingerprinting.md)** | Operator Style Fingerprinting | Measurement algorithms, stability testing, privacy | ✓ Complete |
| **[CONCEPT-0021](../../../Corvin-ADR/concepts/CONCEPT-0021-task-affinity-learning.md)** | Task Affinity Learning | Per-task success measurement, strength tiers | ✓ Complete |
| **[CONCEPT-0022](../../../Corvin-ADR/concepts/CONCEPT-0022-predictive-task-suggestion.md)** | Predictive Task Suggestion | ARIMA modeling, triggering rules, acceptance tracking | ✓ Complete |

---

## Implementation Roadmap

### Phase 1: Data Collection (Week 1–2)

**Deliverables:**
- Decision audit data structure
- Operator annotation UI
- Audit trail integration
- 20+ tests

**Success criteria:** >95% decision capture rate, <5ms latency

---

### Phase 2: Fingerprinting (Week 3–4)

**Deliverables:**
- Fingerprint inference engine (4 dimensions)
- Task affinity Bayesian update
- Console API: GET /operator-fingerprint
- 50+ tests

**Success criteria:** Fingerprint stable ±0.1 after 50 decisions, MAE <0.15

---

### Phase 3: Predictive Guidance (Week 5–7)

**Deliverables:**
- ARIMA task predictor
- Suggestion API + filtering
- What-If replay engine
- Console UI (suggestions + replay)
- 30+ tests

**Success criteria:** Suggestion acceptance >60%, Replay latency <500ms

---

### Phase 4: Optimization & Polish (Week 8)

**Deliverables:**
- Model hyperparameter tuning
- Privacy controls (Settings)
- Telemetry (anonymized)
- Performance optimization
- Migration guide
- 20+ additional tests

**Success criteria:** <100ms fingerprinting, <150ms suggestion, zero PII leaks

---

## Key Metrics & Success Criteria

### Operator Modeling v0.6 "Done" Checklist

✅ **Fingerprint Quality**
- [ ] Stable within ±0.1 after 50 decisions per dimension
- [ ] Task affinity MAE < 0.15 vs. actual success rates

✅ **Suggestion Effectiveness**
- [ ] Acceptance rate >60% (operator starts suggested task within 1 turn)
- [ ] Zero false positives (no suggestions that lead to failure)

✅ **What-If Replay**
- [ ] Latency <500ms p99
- [ ] Determinism verified (same input = same output)
- [ ] Snapshot isolation proven (no cross-decision contamination)

✅ **Privacy & Security**
- [ ] Zero PII in exports (`_assert_safe` validation)
- [ ] Encryption at rest (AES-256)
- [ ] Hash-chain audit trail complete
- [ ] Right to erasure working (deletion purges all data)

✅ **Performance**
- [ ] Fingerprinting: <100ms p99
- [ ] Task prediction: <50ms p99
- [ ] Suggestion API: <50ms p99
- [ ] What-If replay: <500ms p99

✅ **Test Coverage**
- [ ] 165+ tests (100 unit, 50 integration, 15 E2E)
- [ ] All green, no skips

✅ **Documentation**
- [ ] V0.6_IDEAS.md complete
- [ ] ADRs 0383-0386 complete
- [ ] Concepts 0020-0022 complete
- [ ] Implementation plan detailed
- [ ] Upgrade guide ready

✅ **Backwards Compatibility**
- [ ] v0.5 operators upgrade seamlessly
- [ ] No behavior change until opt-in (flags OFF by default)
- [ ] Decision audit is invisible to operator until enabled

✅ **Operator Controls**
- [ ] Privacy settings in Console (enable/disable per feature)
- [ ] Can view own fingerprint
- [ ] Can request data export
- [ ] Can request data deletion (right to erasure)

---

## File Structure

```
CorvinOS/
├── Corvin-ADR/
│   ├── decisions/
│   │   ├── ADR-0383-operator-fingerprint-data-model.md
│   │   ├── ADR-0384-task-affinity-measurement.md
│   │   ├── ADR-0385-predictive-guidance-engine.md
│   │   └── ADR-0386-what-if-replay-architecture.md
│   └── concepts/
│       ├── CONCEPT-0020-operator-style-fingerprinting.md
│       ├── CONCEPT-0021-task-affinity-learning.md
│       └── CONCEPT-0022-predictive-task-suggestion.md
├── core/
│   ├── learning/
│   │   ├── decision_audit.py (Phase 1)
│   │   ├── operator_fingerprint.py (Phase 2)
│   │   ├── affinity_model.py (Phase 2)
│   │   ├── task_predictor.py (Phase 3)
│   │   ├── replay_engine.py (Phase 3)
│   │   ├── snapshot.py (Phase 3)
│   │   └── audit_integration.py (Phase 1)
│   ├── console/
│   │   ├── routes/
│   │   │   ├── learning.py (Phase 2)
│   │   │   ├── suggestions.py (Phase 3)
│   │   │   └── replay.py (Phase 3)
│   │   └── web-next/src/
│   │       ├── components/
│   │       │   ├── DecisionAnnotationPanel.tsx (Phase 1)
│   │       │   ├── SuggestionPanel.tsx (Phase 3)
│       │   │   └── WhatIfReplayPanel.tsx (Phase 3)
│   │       └── pages/
│   │           └── learning.tsx (Phase 2/3)
├── docs/
│   ├── v0.6-design/
│   │   ├── INDEX.md (this file)
│   │   ├── V0.6_IDEAS.md
│   │   ├── V0.6_IMPLEMENTATION_PLAN.md
│   │   └── UPGRADE_v0.5_to_v0.6.md
│   └── implementation/
│       └── LEARNING_TELEMETRY_STRATEGY.md
└── tests/
    └── learning/
        ├── test_decision_audit.py (Phase 1, 15 tests)
        ├── test_fingerprint.py (Phase 2, 25 tests)
        ├── test_affinity.py (Phase 2, 15 tests)
        ├── test_task_predictor.py (Phase 3, 12 tests)
        ├── test_replay_engine.py (Phase 3, 12 tests)
        └── test_learning_api.py (Integration, 50 tests)
```

---

## Dependency Chain

```
v0.5 (baseline)
  ↓
v0.6 (Operator Modeling)
  ├─→ ADR-0314 (Learning Infrastructure) [already exists]
  ├─→ ADR-0359 (Decision History) [already exists]
  ├─→ ADR-0383 (Fingerprint Model)
  ├─→ ADR-0384 (Task Affinity)
  ├─→ ADR-0385 (Predictive Guidance)
  └─→ ADR-0386 (What-If Replay)
      ↓
  v0.7 (Plugin Ecosystem) — affinity model guides recommendations
  v0.8 (Offline Mode) — fingerprint informs degradation
  v0.9 (Dashboard) — shows operator model live
  v1.0 (Polish & Release) — stable, documented, hardened
```

---

## GDPR Compliance Checklist

✅ **Art. 5 (Lawfulness)**
- [ ] Operator model inferred from operator's own decisions only
- [ ] No third-party data enrichment
- [ ] No sensitive characteristic profiling (race, religion, etc.)

✅ **Art. 6 (Legal Basis)**
- [ ] Art. 6(1)(b): Contract fulfillment (necessary for personalization)
- [ ] Art. 6(1)(a): Explicit consent (bot disclosure + Settings opt-out)

✅ **Art. 30/32 (Records & Security)**
- [ ] Hash-chained audit trail (every fingerprint update logged)
- [ ] Encrypted at rest (AES-256)
- [ ] Access controls (0600 permissions, operator-isolated)
- [ ] Audit events immutable

✅ **Art. 17 (Right to Erasure)**
- [ ] Operator can request deletion
- [ ] Purges fingerprint + decision history
- [ ] Immediate effect, verified with operator
- [ ] No backups retained (or purged per retention policy)

✅ **Data Minimization**
- [ ] Collect only: decision choices, outcomes, annotations, aggregates
- [ ] Never: prompt content, response content, conversation history, PII

---

## Quality Gates (Before Release)

✅ **Architecture Review**
- [ ] ADRs 0383-0386 approved by maintainer
- [ ] Concepts 0020-0022 approved by team

✅ **Code Review**
- [ ] All Phase 1-4 implementations reviewed
- [ ] Tests green, coverage >90%
- [ ] No regressions in v0.5 baseline

✅ **Security Review**
- [ ] Adversarial testing (can fingerprint be gamed?)
- [ ] Privacy audit (`_assert_safe` on exports)
- [ ] Encryption validated
- [ ] No PII leaks

✅ **Performance Review**
- [ ] Latency targets met (<100ms fingerprinting, <150ms suggestion)
- [ ] No regressions in existing turn latency
- [ ] Memory usage stable (<10MB for 1000 operators)

✅ **Compliance Review**
- [ ] GDPR Art. 5/6/30/32/17 verified
- [ ] Audit trail integration complete
- [ ] Right to erasure tested
- [ ] Data minimization enforced

✅ **Documentation Review**
- [ ] V0.6_IDEAS.md complete
- [ ] ADRs have proper frontmatter (ADR-0264)
- [ ] Concepts have operator notes section
- [ ] Implementation plan has rollback procedure

---

## References

- **v0.5 Baseline:** See CHANGELOG_v0.2_to_v0.3.md (shipped Aug 2026)
- **Learning Infrastructure:** ADR-0314 (already implemented)
- **Decision History:** ADR-0359 (already implemented)
- **Audit Trail:** Layer 16 (core/compliance/audit/)
- **Privacy Framework:** GDPR Art. 5/6/30/32/17 baseline
- **Quality Standards:** LDD (Loop-Driven Engineering), ADR-0264 frontmatter

---

## Glossary

| Term | Definition |
|---|---|
| **Fingerprint** | 4-dimensional operator style model (risk, speed, communication, task affinity) |
| **Risk Tolerance** | [0.0..1.0] measure of willingness to choose bold options |
| **Speed Preference** | [0.0..1.0] measure of preference for quick decisions vs. thorough analysis |
| **Communication Style** | [0.0..1.0] formality of operator's annotations (formal=1.0, casual=0.0) |
| **Task Affinity** | Per-task-type success rate with confidence interval |
| **Affinity Tier** | Categorization: "strong" (≥75%), "neutral" (45-75%), "weak" (<45%) |
| **Suggestion** | Task type prediction offered to operator (opt-in) |
| **What-If Replay** | Counterfactual analysis (simulate different decision) |
| **Snapshot** | Immutable decision-point state for replay (expires 30 days) |
| **Confidence** | [0.0..1.0] certainty in model estimate (saturates at n=30 samples) |

---

**Maintained by:** Claude Code  
**Last Updated:** 2026-08-18  
**Next Review:** Week 9 (post-canary rollout)

