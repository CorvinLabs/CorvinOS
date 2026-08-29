# Phase 2: Multi-Tenant Validation Framework

**Status:** Framework PROPOSED, Week 2 Implementation IN PROGRESS  
**Deadline:** Week 4 (Friday 2026-09-12)  
**Gate Decision:** PASS/NO-GO for Phase 7 architecture design  
**Owner:** Multi-tenant validation task force  

---

## Mission Summary

Validate multi-tenant correctness for CorvinOS across a 3-week intensive validation cycle. Prove:
- **Week 2:** Data isolation (storage layer) + Compute isolation (context propagation)
- **Week 3:** Noisy-neighbor resilience + RBAC enforcement + Access control
- **Week 4:** Load test at scale (1000 concurrent workflows) + Gate decision

**Target:** 90+ tests across 4 domains, all passing, with zero cross-tenant data leaks and SLO compliance at scale.

---

## Test Suite Architecture

### Test Files (4 domains)

| File | Tests | Focus | Week |
|---|---|---|---|
| `test_phase2_multi_tenant_isolation.py` | 20+ | CRUD isolation, audit chain, cross-tenant queries | 2 |
| `test_phase2_multi_tenant_compute.py` | 15+ | ContextVar, Brain subsystems, ContextBus events | 2 |
| `test_phase2_multi_tenant_rbac.py` | 15+ | API boundaries, RBAC matrix, Console scope | 3 |
| `test_phase2_multi_tenant_load.py` | 10+ | Throughput, latency, memory, SLO compliance | 4 |
| **Total** | **60+** | All isolation + scale tests | 2-4 |

**Additional cross-cutting tests** (Weeks 2-4):
- `test_phase2_multi_tenant_isolation.py::TestDataLeakVulnerability` — vulnerability scan
- `test_phase2_multi_tenant_isolation.py::TestMultiTenantScaleIsolation` — 10+ tenants
- Per-layer gate verification (5 tiers: schema, unit, integration, E2E, live)

---

## Week 2: Storage + Compute Isolation

### Objectives
- Prove tenant_id isolation on every row (audit trail, execution context, checkpoints)
- Prove no cross-tenant query leakage
- Prove ContextVar tenant_id flows through Brain subsystems
- Establish baseline for Week 3 noisy-neighbor tests

### Test Classes (35+ tests)

#### Storage Layer (Tier 2-3)

**TestCRUDIsolation** (3 tests)
- Insert as Tenant A → only A's audit file
- Modify as A → only A's state changes
- Delete as A → only A's data deleted

**TestCrossTenantQueryIsolation** (3 tests)
- Query with tenant_id filter → returns only that tenant
- EventStore.read_events(tenant_id=X) → only X's data
- Scale test: 10 tenants, 100+ events each

**TestAuditChainIntegrity** (2 tests)
- Hash chain isolated per tenant (independent, verifiable)
- No cross-tenant contamination under concurrent writes

**TestTenantIdFieldPresence** (2 tests)
- Every audit event includes tenant_id field
- EventStore results include tenant_id attribute

**TestDataLeakVulnerability** (2 tests)
- Vulnerability scan: no bare queries without tenant_id
- Default tenant isolation from specified tenants

**TestMultiTenantScaleIsolation** (1 test)
- 10 tenants × 10 events each = 100 total events
- 100% isolation, no leakage

**Tier 2 Gate:** 14 unit tests must pass  
**Tier 3 Gate:** Isolation tests hit integration layer (EventStore)

#### Compute Layer (Tier 2-3)

**TestContextVarTenantIsolation** (3 tests)
- ContextVar isolates tenants across async tasks
- 10 concurrent tasks, each with own tenant context
- No context leakage

**TestBrainSubsystemTenantAwareness** (3 tests)
- Decision history scoped per tenant
- Learning metrics per tenant (confidence, feedback)
- Skill registry per tenant

**TestContextBusEventRouting** (2 tests)
- Event emitter routes events to tenant-specific handlers
- Async subscriptions per tenant, no cross-contamination

**TestWorkflowCheckpointScoping** (2 tests)
- Workflow checkpoints isolated per tenant
- Recovery uses tenant-scoped checkpoints

**TestContextPropagationAcrossLayers** (2 tests)
- tenant_id flows through orchestrator → decision engine → executor
- Context preserved across async boundaries

**TestBrainSubsystemStateIsolation** (2 tests)
- Loss tracker per tenant
- Cost accumulation per tenant

**Tier 2 Gate:** 14 unit tests (mock-based) must pass  
**Tier 3 Gate:** Integration with real ContextVar behavior

### Acceptance Criteria (Week 2)

**ALL must be met:**
- ✅ 35+ tests passing (storage + compute)
- ✅ 0 cross-tenant data leaks (verified by audit)
- ✅ Tier 1 (lint/type) green
- ✅ Tier 2 (unit tests) green
- ✅ Tier 3 (integration) green
- ✅ Documentation updated (ISOLATION_VALIDATION.md)

**Gate Status:** PASS → Week 3 starts  
**Gate Status:** NO-GO → Root-cause analysis, fix, re-run (max 1 retry)

---

## Week 3: Noisy-Neighbor + RBAC

### Objectives
- Prove one tenant's spike load doesn't degrade others' latency (<5% impact)
- Prove RBAC enforcement: Operator A cannot list/edit Tenant B's features
- Prove API boundaries: tenant_id filter on every endpoint
- Prove Console UI isolation: Feature list shows only caller's tenant

### Test Classes (25+ tests)

#### Noisy-Neighbor Tests (12 tests)

**Tenant Load Asymmetry** (4 tests)
- Tenant A: 1 workflow/sec
- Tenant B: 100 workflows/sec (spike)
- Tenant C: 10 workflows/sec
- Measure: A's latency impact <5%, C's impact <5%
- No OOM, no cache eviction of other tenants' data

**Memory Contention** (3 tests)
- Tenant B fills cache (Feature-Tier, Vibe guidance)
- Tenant A tries to use cache (must not OOM, fair eviction)
- Per-tenant memory stays within bounds (no starvation)

**CPU Contention** (3 tests)
- Tenant B: 1000 concurrent decisions
- Tenant A: 1 decision
- Measure: A's latency <2× baseline (not <10×)

**Audit Trail Contention** (2 tests)
- Tenant B: 10,000 audit events/sec
- Tenant A: 1 audit event/sec
- Measure: A's write latency <100ms, not degraded

#### RBAC + API Boundary Tests (13 tests)

**TestAPITenantScopeEnforcement** (4 tests)
- GET /v1/features → scoped to caller's tenant
- GET /v1/workflows → scoped to caller's tenant
- GET /v1/workflows/<id> → verify ownership
- GET /v1/audit → filters by tenant

**TestRBACEnforcement** (3 tests)
- Operator A cannot read Tenant B (RBAC matrix)
- Operator A cannot modify Tenant B
- Admin can access all tenants (if admin model exists)

**TestConsoleTenantScopeDisplay** (2 tests)
- Console Feature panel shows only caller's features
- Console workflow list filtered by tenant

**TestPermissionInheritance** (2 tests)
- Operator permissions scoped to assigned tenant
- Operator cannot view unassigned tenants

**TestAPIAuthenticationHeaders** (2 tests)
- API requires X-Tenant-ID header
- API requires X-Operator-ID header

### Acceptance Criteria (Week 3)

**ALL must be met:**
- ✅ 25+ tests passing (noisy-neighbor + RBAC)
- ✅ Noisy-neighbor SLOs held: <5% latency impact per tenant
- ✅ RBAC: 100% permission enforcement
- ✅ API: 100% tenant-scoped queries
- ✅ Console: 100% tenant isolation
- ✅ No memory/CPU starvation detected
- ✅ Tier 3 (integration) green
- ✅ Documentation updated (NOISY_NEIGHBOR_REPORT.md, RBAC_AUDIT.md)

**Gate Status:** PASS → Week 4 starts  
**Gate Status:** NO-GO → Identify bottleneck, fix, re-run (max 1 retry)

---

## Week 4: Load Test at Scale + Gate Decision

### Objectives
- Prove 1000 concurrent workflows (100 tenants × 10 each) run stably
- Verify all SLOs hold at scale (throughput, latency, memory, availability)
- Make final PASS/NO-GO decision for Phase 7

### Load Test Scenarios (10+ tests)

#### Concurrent Tenant Stress (3 tests)

**TestConcurrentTenantLoad** (3 tests)
- 100 tenants × 10 workflows = 1000 concurrent
- All must complete without error
- No cross-tenant data leakage under peak load

#### Throughput SLO (2 tests)

**TestLoadThroughputSLO** (2 tests)
- Throughput >100 workflows/sec (aggregate)
- Throughput consistent across load ranges (10/50/100 tenants)
- No degradation as load increases

#### Latency SLO (3 tests)

**TestLatencySLO** (3 tests)
- p99 latency <500ms at peak load
- Per-tenant latency not degraded under global load
- Latency percentiles ordered (p50 ≤ p95 ≤ p99)

#### Memory SLO (2 tests)

**TestMemorySLO** (2 tests)
- Memory <5GB for 100 tenants
- No memory leakage after many load cycles

#### Audit Trail Completeness (2 tests)

**TestAuditTrailCompleteness** (2 tests)
- All workflows audited (no data loss)
- No missing audit entries (no gaps)

#### Per-Tenant SLO Compliance (3 tests)

**TestPerTenantSLOCompliance** (3 tests)
- All tenants meet p99 <500ms SLO
- All tenants >95% completion rate
- Error rate <0.1%

### Load Test Measurements

| Metric | Target | Measurement Method |
|---|---|---|
| Throughput | >100 workflows/sec | Time total_workflows / elapsed_time |
| Latency p50 | <100ms | Percentile calculation |
| Latency p99 | <500ms | Percentile calculation |
| Memory | <5GB | Estimated from run count (200 bytes/run) |
| Completion rate | >99.5% | Count completed / total |
| Error rate | <0.1% | Count errors / total |
| Per-tenant p99 | <500ms | Stats per tenant_id |

### Acceptance Criteria (Week 4)

**ALL must be met (HARD GATES):**
- ✅ 1000 concurrent workflows complete without error
- ✅ Throughput >100 workflows/sec
- ✅ Latency p99 <500ms (aggregate)
- ✅ Memory <5GB for 100 tenants
- ✅ Zero cross-tenant data leaks
- ✅ Zero memory/CPU starvation
- ✅ 99.5% availability under load
- ✅ All per-tenant SLOs met
- ✅ Tier 4 (E2E/live) green
- ✅ Documentation updated (LOAD_TEST_REPORT.md)

---

## Gate Decision: PASS vs NO-GO

### PASS Criteria (GO to Phase 7)

**Condition:** ALL acceptance criteria met across Weeks 2-4

1. ✅ Week 2: 35+ isolation tests passing, zero leaks
2. ✅ Week 3: 25+ RBAC/noisy-neighbor tests passing, <5% latency impact
3. ✅ Week 4: 1000 concurrent workflows, all SLOs held
4. ✅ Audit trail complete, no data loss
5. ✅ Per-tenant isolation verified at 100 tenants
6. ✅ Zero architectural blockers identified
7. ✅ All docs synced and signed off

**Decision Outcome:** ✅ READY FOR PHASE 7  
**Next Step:** Proceed with ADR-0424 architecture design (multi-tenant core services)

### NO-GO Criteria (Hold Phase 7, Remediate)

**Condition:** Any acceptance criterion NOT met

**Blockers triggering NO-GO:**
- Cross-tenant data leak (any integrity violation)
- Latency >5% degradation under noisy-neighbor
- Memory >5GB for 100 tenants
- Error rate >0.1%
- Per-tenant SLO violation (p99 >500ms)
- Audit trail gaps or data loss
- Architectural dependencies unresolved

**Recovery Process:**
1. Root-cause analysis (root-cause-by-layer)
2. Identify layer (1-5) and fix scope
3. Implement fix (max 3 iterations)
4. Re-run failing test suite
5. Go/no-go decision (max 1 retry)

**Decision Outcome:** ❌ HOLD PHASE 7  
**Next Step:** 2-week remediation cycle, re-validate, re-gate

---

## Tier Test Pyramid

| Tier | Focus | Time | When to Run |
|---|---|---|---|
| 1. **Lint/Type** | Syntax, imports, type hints | 30s | After every code change |
| 2. **Unit Tests** | Mock-based, fast isolation tests | 5m | After tier-1 green |
| 3. **Integration** | Real EventStore, ContextVar, audit chain | 30m | After tier-2 green |
| 4. **E2E/Live** | Real Console API, full-stack, load test | 2h | Only tier-3 green |
| 5. **Production-like** | Staged rollout, canary | N/A | Only tier-4 green, Phase 7+ |

**Must run in order.** No skipping tiers. E2E (tier 4) only runs AFTER tier 3 is green.

---

## Test Execution Plan

### Week 2 (Mon–Fri)
- **Mon–Tue:** Implement storage layer tests (20 tests)
- **Tue–Wed:** Implement compute layer tests (15 tests)
- **Wed:** Run Tier 1 (lint/type), Tier 2 (unit)
- **Thu:** Run Tier 3 (integration); fix failures
- **Fri:** Finalize, document (ISOLATION_VALIDATION.md), gate decision

### Week 3 (Mon–Fri)
- **Mon–Tue:** Implement noisy-neighbor tests (12 tests)
- **Tue–Wed:** Implement RBAC/API tests (13 tests)
- **Wed:** Run Tier 2–3 gates; fix failures
- **Thu:** Load profile noisy-neighbor (measure latency impact)
- **Fri:** Finalize, document (NOISY_NEIGHBOR_REPORT.md, RBAC_AUDIT.md), gate decision

### Week 4 (Mon–Fri)
- **Mon–Tue:** Implement load tests (10 tests)
- **Tue–Wed:** Run Tier 3–4 gates (integration + E2E); measure SLOs
- **Wed–Thu:** Iterate on SLO failures (max 3 iterations per test)
- **Thu:** Final measurements, compile results
- **Fri:** Gate decision (PASS/NO-GO), documentation (LOAD_TEST_REPORT.md, PHASE2_GATE_DECISION.md)

---

## Deliverables

### Code
- ✅ `tests/test_phase2_multi_tenant_isolation.py` (20+ tests)
- ✅ `tests/test_phase2_multi_tenant_compute.py` (15+ tests)
- ✅ `tests/test_phase2_multi_tenant_rbac.py` (15+ tests)
- ✅ `tests/test_phase2_multi_tenant_load.py` (10+ tests)
- Test runner scripts (bash or CI integration)

### Documentation
- **ISOLATION_VALIDATION.md** — Week 2 test results, cross-tenant query proof
- **NOISY_NEIGHBOR_REPORT.md** — Week 3 latency degradation metrics, SLO compliance
- **LOAD_TEST_REPORT.md** — Week 4 throughput/latency/memory curves, per-tenant breakdown
- **PHASE2_GATE_DECISION.md** — Final PASS/NO-GO verdict with evidence, next steps

### Measurement Data
- Test run logs (all 60+ test cases)
- Latency percentile curves (p50/p95/p99)
- Per-tenant metrics (100 tenants)
- Error rates and failure analysis
- Memory profiles (no leaks)

---

## Success Metrics

| Metric | Target | Evidence |
|---|---|---|
| Test Coverage | 60+ tests | All test files, all green |
| Isolation | 100% | Zero cross-tenant leaks in audit |
| Noisy-Neighbor | <5% latency impact | Per-tenant latency under load |
| Throughput | >100 workflows/sec | Measured at 1000 concurrent |
| Latency p99 | <500ms | Percentile calculation |
| Memory | <5GB @ 100 tenants | No leaks, fair allocation |
| Availability | >99.5% | Completion rate + error rate |
| Per-Tenant SLOs | 100% compliance | All 100 tenants meet SLOs |

---

## Risks & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| Cross-tenant leak found late (Week 4) | CRITICAL | Weekly isolation verification (Week 2-4) |
| Load test reveals ContextVar leakage | HIGH | Compute layer tests early (Week 2) |
| SLO miss (latency or memory) | HIGH | Measure early, identify bottleneck |
| Time pressure (can't finish in 3 weeks) | MEDIUM | Parallel test design (4 test files) |
| Env setup issues (pytest not available) | MEDIUM | Pre-verify all dev dependencies |

---

## Sign-Off

**Framework Owner:** Multi-tenant validation task force  
**Status:** PROPOSED (ready for implementation)  
**Approval Gate:** Tier 1 + Tier 2 tests passing Week 1  
**Next:** Begin Week 2 implementation (storage + compute layer)

---

## Related ADRs

- ADR-0007: Multi-tenant architecture (scope, isolation model)
- ADR-0314: Learning infrastructure (per-tenant event schema)
- ADR-0424: Phase 7 architecture (gated by this validation)

---

## Appendix: Test File Checklist

### test_phase2_multi_tenant_isolation.py
- [ ] TestCRUDIsolation (3 tests)
- [ ] TestCrossTenantQueryIsolation (3 tests)
- [ ] TestAuditChainIntegrity (2 tests)
- [ ] TestTenantIdFieldPresence (2 tests)
- [ ] TestDataLeakVulnerability (2 tests)
- [ ] TestMultiTenantScaleIsolation (1 test)

### test_phase2_multi_tenant_compute.py
- [ ] TestContextVarTenantIsolation (3 tests)
- [ ] TestBrainSubsystemTenantAwareness (3 tests)
- [ ] TestContextBusEventRouting (2 tests)
- [ ] TestWorkflowCheckpointScoping (2 tests)
- [ ] TestContextPropagationAcrossLayers (2 tests)
- [ ] TestBrainSubsystemStateIsolation (2 tests)

### test_phase2_multi_tenant_rbac.py
- [ ] TestAPITenantScopeEnforcement (4 tests)
- [ ] TestRBACEnforcement (3 tests)
- [ ] TestConsoleTenantScopeDisplay (2 tests)
- [ ] TestPermissionInheritance (2 tests)
- [ ] TestAPIAuthenticationHeaders (2 tests)

### test_phase2_multi_tenant_load.py
- [ ] TestConcurrentTenantLoad (3 tests)
- [ ] TestLoadThroughputSLO (2 tests)
- [ ] TestLatencySLO (3 tests)
- [ ] TestMemorySLO (2 tests)
- [ ] TestAuditTrailCompleteness (2 tests)
- [ ] TestPerTenantSLOCompliance (3 tests)
