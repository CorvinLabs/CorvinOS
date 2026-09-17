# Console Infrastructure — Unified Implementation Plan (16–22 Days)

**Master Plan:** ADR-0800 + ADR-0801–0808 (8 Systems)  
**Timeline:** Week 1–3  
**Commits:** 25–35  
**LOC:** ~7,500 code + ~2,000 tests

---

## PHASE 1: TIER 1 FOUNDATION (Days 1–5, Week 1)

### Day 1–2: Foundation Systems (3 tracks parallel)

**Track A: Vibe + DataHub (ADR-0801, ADR-0803)**
- Vibe 9D: Wire to live_measurements (300 LOC)
- DataHub: Core Skill + GraphQL API (800 LOC)
- **Commits:** 2 (one per system)
- **Tests:** 25+ (E2E: API queries, dashboard rendering)

**Track B: Skill Forge + Model Selection (ADR-0802, ADR-0804)**
- Skill Forge v2.0: Skeleton + manifest schema (500 LOC)
- Model Selection: Console UI + static config (600 LOC)
- **Commits:** 2
- **Tests:** 30+ (generation, classification)

**Track C: Foundation (ADR-0806, ADR-0808)**
- OTEL: Collector wiring (400 LOC)
- Licensing: Schema + audit events (400 LOC)
- **Commits:** 2
- **Tests:** 20+ (metric ingestion, tier gates)

**Day 1–2 Sync Point:** Verify no circular dependencies (DAG check passes)

### Day 3–4: Video Producer + Integration Prep

**Track D: Video Producer (ADR-0805)**
- Orchestrator skeleton (500 LOC)
- AssetAnalyzer worker (400 LOC)
- **Commits:** 1
- **Tests:** 35+ (composition, asset detection)

**Track E: DoD Verifier + Cross-System Wiring**
- DoD Verifier Skill (700 LOC)
- Cross-system event types (200 LOC)
- **Commits:** 2
- **Tests:** 30+ (gate functionality, event schema)

**Day 3–4 Output:** All core systems + E2E test suite complete

### Day 5: Integration & Adversarial Review Prep

**Integration:**
- Wire all 8 systems into console panels (200 LOC)
- Dependency graph validation (automated DAG check)
- **Commits:** 1

**Adversarial Review Preparation:**
- Collect all 8 ADRs
- Run 6D adversarial review (Security, Compliance, Performance, Integration, Scalability, Observability)
- Target: 3×0 findings per system
- Document all findings + mitigations

---

## PHASE 2: TIER 2 INTEGRATION (Days 6–10, Week 2)

### Day 6–7: Cross-System Wiring

**System 1: Learning Loop Integration (ADR-0314)**
- Wire feedback events from Model Selection → Bayesian update (300 LOC)
- Wire quality feedback from Video Producer → optimizer (200 LOC)
- **Tests:** 20+ (feedback propagation)

**System 2: Skill Marketplace Hub (ADR-0678)**
- Skill Forge → DataHub indexing (300 LOC)
- DataHub → Marketplace UI (200 LOC)
- **Tests:** 25+ (discovery, filtering)

**System 3: Audit Chain (ADR-0232/0233)**
- All Skill audit events → central chain (200 LOC)
- Hash-chain verification (150 LOC)
- **Tests:** 30+ (audit integrity)

**Day 6–7 Output:** Full cross-system integration, learning loop active

### Day 8–9: E2E Tests + Adversarial Fixes

**E2E Test Suite:**
- Real load test (550 concurrent users, 415ms p95) (800 LOC tests)
- Cross-system workflow test (create skill → index → route → execute) (400 LOC tests)
- Failure mode test (fallback, rollback scenarios) (300 LOC tests)
- **Total:** 1,500 LOC test code

**Adversarial Findings Fixes:**
- Security: Close any auth gaps, PII leakage (100–200 LOC per finding)
- Compliance: Ensure GDPR/EU AI Act gates (50–100 LOC per finding)
- Performance: Optimize hot paths (cache, batching) (200–300 LOC per finding)
- Integration: Fix any broken wiring (50 LOC per finding)

**Expected:** 10–15 findings → 8–12 days to fix (parallel tracks)

### Day 10: Adversarial Review Sign-Off

**Final Checks:**
- Run full adversarial review again (3×0 verification)
- All findings documented + fixed
- All tests pass
- Performance SLA met (P99 < 2s)
- Audit trail clean (zero gaps)

---

## PHASE 3: TIER 3 POLISH + CANARY (Days 11–16, Week 3)

### Day 11–12: Performance Tuning

**Optimization:**
- Profile hot paths (Skill execution, DataHub queries, telemetry ingestion)
- Implement caching (200 LOC optimization)
- Batch telemetry writes (150 LOC)
- **Gate:** P99 latency < 2s verified in load test

**Monitoring Setup:**
- OTEL dashboard wiring (300 LOC)
- Alert rules (CPU > 80%, Error rate > 1%, Latency > 3s) (100 LOC)

### Day 13–14: Canary Deployment Prep

**Infrastructure:**
- Feature flags for each system (disable independently) (100 LOC)
- Canary traffic routing (5% → control, 95% → baseline) (150 LOC)
- Monitoring dashboard (Grafana/Datadog) (200 LOC)

**Validation:**
- Dry-run canary in staging (all 8 systems)
- Verify rollback procedure (each system independently)
- Alert testing (simulate failures, verify alerts fire)

### Day 15: CANARY 5% DEPLOYMENT

**Go Live:**
1. Deploy all 8 systems to production (5% traffic via feature flag)
2. Monitor for 4 hours:
   - P99 latency < 2s? ✅
   - Error rate < 0.1%? ✅
   - Audit trail clean? ✅
   - No PII leaks? ✅
3. If OK → expand to 10%

**Rollback Trigger:**
- Error rate > 1.0%
- P99 latency > 3.0s
- Audit chain breaks
- **Action:** Feature flag → 0% traffic, revert deployment

### Day 16: GRADUAL ROLLOUT

**Phases:**
- 10% traffic (monitor 2h)
- 25% traffic (monitor 2h)
- 50% traffic (monitor 2h)
- 100% traffic (live, monitor 24h)

**Each Phase Gates:**
- P99 latency stable
- Error rate < 0.5%
- Audit trail valid
- **Decision:** Proceed or rollback

---

## SUCCESS METRICS

| Metric | Target | Verification |
|--------|--------|---|
| **ADRs Complete** | 9 ADRs (1 master + 8 systems) | All in Corvin-ADR repo |
| **Adversarial Review** | 3×0 findings per system | Independent verification |
| **E2E Tests** | 100+ tests, 100% pass | CI/CD gate |
| **Performance** | P99 latency < 2s | Real load test (550 concurrent) |
| **Error Rate** | < 0.1% (canary) | Production monitoring |
| **Audit Trail** | Zero gaps, hash-chain verified | Manual inspection |
| **Code Quality** | Ruff/mypy clean | Linting gate |
| **Canary Stability** | 24h, no regressions | Automated alerting |

---

## COMMIT STRUCTURE (25–35 Total)

**Week 1 (Days 1–5):** 8 commits (1 per system core)
**Week 2 (Days 6–10):** 12–15 commits (integration + fixes)
**Week 3 (Days 11–16):** 5–6 commits (canary + monitoring)

**Example Commit Messages:**
```
feat(console): Vibe 9D Maturity wired to live_measurements
feat(skills): Skill Forge v2.0 skeleton + manifest schema
feat(datahub): DataHub Skill + GraphQL API
feat(routing): Model Selection console UI + static config
feat(video): Video Producer orchestrator + AssetAnalyzer
feat(observability): OTEL collector wiring
feat(licensing): Licensing 1.0.0 schema + audit events
feat(dod): DoD Verifier Skill + quality gate
integration: Cross-system wiring + learning loop
test: E2E test suite (1,500 LOC)
fix(security): Auth gate validation
fix(compliance): GDPR Art. 5/6 verification
perf: Cache DataHub queries
deploy: Feature flags + canary routing
```

---

## DEPENDENCIES & BLOCKERS

### Critical Dependencies
- ✅ ADR-0314 (Learning Infrastructure) — must be live
- ✅ ADR-0232/0233 (Audit Chain) — must be live
- ✅ ADR-0535 (Skill Composition) — must be live
- ✅ ADR-0678 (Marketplace Hub) — must be live

### Known Blockers (Resolvable in Week 1)
- 🟡 CRITICAL: operator/ stdlib shadowing (rename to corvin_operator/) — 3–4h
- 🟡 CRITICAL: L10 Entry Point wiring (os.context_adapter) — 2–3h
- 🟡 MEDIUM: mermaid build error (orthogonal, Phase 2) — defer

---

## Ownership & Teams

| System | Owner | Effort | Days |
|--------|-------|--------|------|
| Vibe 9D | Frontend | 300 LOC | 2 |
| Skill Forge v2.0 | Generative | 500 LOC | 3 |
| DataHub | Data | 800 LOC | 3 |
| Model Selection | Routing | 600 LOC | 3 |
| Video Producer | Orchestration | 900 LOC | 3 |
| OTEL | Observability | 400 LOC | 2 |
| DoD Verifier | Quality | 700 LOC | 2 |
| Licensing | Compliance | 400 LOC | 1 |
| **Integration & Tests** | **All** | **1,500+ LOC** | **5–6** |

---

## Risk Mitigation Summary

| Risk | Severity | Mitigation | Owner |
|------|----------|-----------|-------|
| Circular dependency | 🔴 CRITICAL | Automated DAG validation (Day 5) | All |
| Audit trail breaks | 🔴 CRITICAL | Hash-chain verification (Day 10) | Observability |
| Performance regression | 🟡 HIGH | Load testing + monitoring (Day 12) | Perf |
| PII leakage | 🟡 HIGH | Telemetry scrubbing (Day 6) | Security |
| Integration failure | 🟡 HIGH | E2E tests (Day 9) | All |
| Canary disaster | 🟡 HIGH | Rollback procedure (Day 14) | DevOps |

