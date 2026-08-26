# Phase 1 Sprint Execution Plan: Audit + Auth Plugins — SUPERSEDED

**Status:** **Superseded** by [`PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md`](PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md)
and ADR-0233 (see Corvin-ADR repo).

Two reasons:

1. **Staffing model.** It assigns four named engineers (A/B/C/D) with a merge monopoly,
   k8s/Grafana ops handoff and sprint ceremonies. This is a solo-maintainer repo; the
   replacement plan is sequenced in sessions with K_MAX = 5.
2. **Phase-1 framing.** "Extract Audit logging from L16 into pluggable
   `AuditBackendPlugin`" conflicts with the compliance baseline (ADR-0232): the audit
   chain, house-rules gate and consent gate are mandatory core and cannot move behind
   a plugin. The replacement makes backends **additive** — core keeps writing its own
   chain, a backend receives a copy, and `core/compliance/tripwire.py` fails the boot
   closed if the core writer is unreachable.

**Retained for:** the adversarial review checklist (§ Adversarial Code Review Process)
and the veto conditions, which carry over unchanged as the per-phase review gate.
LDAP/OIDC backend scaffolds are dropped — speculative work with no requester.

## Original content (historical)

**Duration:** 16 weeks (Sprint 0-7)
**Team:** 3-4 engineers (A, B, C, D)
**Budget:** 50 engineer-weeks, K_MAX = 5 iterations per sprint
**Gate:** Adversarial code review is MANDATORY before any merge  

---

## Team Role Matrix (Ownership + Expertise)

| Engineer | Role | Sprints | Primary | Secondary |
|----------|------|---------|---------|-----------|
| **A** | Lead + Audit Specialist | 0-8 | L16 Audit Backend | Protocol, Wiring |
| **B** | Auth Specialist | 0-8 | L18-21 User Backend | LDAP, OIDC |
| **C** | Testing + Integration | 0-8 | E2E Tests + CI | Adversarial Review Lead |
| **D** | Infrastructure + Circuit Breaker | 1-8 | Circuit Breaker, Core Wiring | Ops Support |

**Adversarial Review Lead:** Engineer C owns the code review process. Reviews all PRs. Has veto power on security/compliance concerns.

---

## Parallel Work Streams (Minimize Serialization)

### Stream 1: Protocols (Sprint 0-1, Parallel)
- **Engineer A:** Design `AuditBackend` protocol (sync with C)
- **Engineer B:** Design `UserBackend` protocol (sync with C)
- **Engineer C:** Create test templates for both

**Dependency:** Both protocols done before Stream 2 starts  
**Gate:** Protocols pass schema validation + 10 unit tests each

### Stream 2: Built-in Implementations (Sprint 2-5, Parallel)
- **Engineer A:** Default audit backend (file-based)
- **Engineer B:** Local + LDAP user backends
- **Engineer D:** Circuit breaker + wrapper

**Dependency:** Protocols complete, no cross-blocking  
**Gate:** Each implementation passes integration tests

### Stream 3: Core Integration (Sprint 4-6, Dependent)
- **Engineer D:** Wire plugins into core boot (needs A + B done)
- **Engineer A:** Update registry + lifecycle hooks
- **Engineer C:** E2E tests across all plugins

**Dependency:** Implementations must be complete  
**Gate:** Full E2E test suite passes (20+ tests)

### Stream 4: Documentation + Rollout (Sprint 6-8, Parallel)
- **Engineer A:** Operator migration guide
- **Engineer B:** Plugin author guide
- **Engineer D:** Deployment + monitoring setup

**Dependency:** Code complete, tests passing  
**Gate:** All docs reviewed + deployed to staging

---

## Sprint-by-Sprint Breakdown

### Sprint 0: Protocol Design (Week 1-2)
**Theme:** Frozen interfaces, no code changes yet

**Deliverables:**
```
├─ AuditBackend Protocol (Engineer A)
│  ├─ log_event(event_type, details, **kwargs) → None
│  ├─ verify_chain() → bool
│  ├─ enforce_retention(max_age_days) → dict
│  └─ 5+ unit tests
│
├─ UserBackend Protocol (Engineer B)
│  ├─ authenticate(credentials) → dict | None
│  ├─ get_user(user_id) → dict | None
│  ├─ enforce_quota(user_id, resource) → None
│  ├─ list_users() → list[dict]
│  └─ 7+ unit tests
│
└─ Test Templates (Engineer C)
   ├─ Mock audit backend
   ├─ Mock user backend
   └─ Test fixtures
```

**Test Gates (Tier 1-2):**
- [ ] `ruff` — no style violations
- [ ] `mypy --strict` — full type coverage
- [ ] Protocol docs render (sphinx)
- [ ] 12+ unit tests (protocols, fixtures)

**Adversarial Review (Engineer C):**
- [ ] Is protocol complete (no missing methods)?
- [ ] Can it be implemented multiple ways (not over-specified)?
- [ ] Error handling clear (None vs Exception)?
- [ ] Backwards compatible with existing code?

**Risk Score:** LOW (design-only, no runtime risk)

**Escalation:** If protocol can't be implemented 3 ways → redesign

---

### Sprint 1: Protocol Templates (Week 3-4)
**Theme:** Example implementations, test infrastructure

**Deliverables:**
```
├─ Audit Backend Template (Engineer A)
│  ├─ templates/audit_backend_plugin.py (50 LOC)
│  ├─ Example: file-based backend stub
│  └─ 3 unit tests
│
├─ User Backend Template (Engineer B)
│  ├─ templates/user_backend_plugin.py (60 LOC)
│  ├─ Example: local auth stub
│  └─ 4 unit tests
│
└─ CI/Testing Harness (Engineer C)
   ├─ pytest fixtures (mock plugins, in-memory DB)
   ├─ Test matrix: unit + integration
   └─ Pre-commit hooks (ruff, mypy, linting)
```

**Test Gates (Tier 1-3):**
- [ ] `ruff` — all templates pass linting
- [ ] `mypy --strict` — full type coverage
- [ ] Unit tests: 7+ tests (templates)
- [ ] Integration tests: mock plugins work together

**Adversarial Review (Engineer C):**
- [ ] Can an external dev implement this template?
- [ ] Error messages clear and actionable?
- [ ] Testing is realistic (not over-mocked)?
- [ ] No hardcoded assumptions (tenant_id, paths, etc.)?

**Risk Score:** LOW-MEDIUM (templates are scaffolding, not production)

**Escalation:** If template doesn't match protocol → fix protocol or template

---

### Sprint 2: Default Audit Backend (Week 5-6)
**Theme:** Production audit storage (file-based, immutable)

**Deliverables (Engineer A):**
```
├─ core/plugins/corvin_plugins/providers/audit_backend.py (200 LOC)
│  ├─ DefaultAuditBackendPlugin class
│  ├─ log_event() → writes JSON + hash-chain to audit.jsonl
│  ├─ verify_chain() → reads file, checks hash-chain
│  ├─ enforce_retention() → delete events older than N days
│  ├─ fsync() to ensure disk write
│  └─ Error handling (file permissions, disk full, etc.)
│
├─ core/audit/hash_chain.py (extraction from L16)
│  ├─ Hash-chain computation logic
│  └─ Verification logic
│
└─ Tests (Engineer C collaborates)
   ├─ test_audit_backend.py (15 unit tests)
   │  ├─ Write event → verify file
   │  ├─ Hash-chain correct
   │  ├─ Retention deletes old events
   │  ├─ fsync guarantees
   │  ├─ File permission errors handled
   │  └─ Concurrent writes (thread-safe)
   │
   └─ test_audit_integration.py (5 integration tests)
      ├─ Plugin registers with registry
      ├─ Audit events flow through E2E
      └─ Failure modes (disk full, etc.)
```

**Test Gates (Tier 1-4):**
- [ ] Tier 1: `ruff`, `mypy --strict`
- [ ] Tier 2: Unit tests (15+, all pass)
- [ ] Tier 3: Integration tests (5+, audit trail works)
- [ ] Tier 4: E2E (mock HTTP request → audit event logged)

**Adversarial Review (Engineer C):**
- [ ] Hash-chain algorithm correct (use library, don't implement)?
- [ ] fsync() actually guarantees disk write (check OS behavior)?
- [ ] No PII in logged details (audit-cleaner script needed)?
- [ ] Retention is GDPR-compliant (what about compliance holds)?
- [ ] Thread-safe for concurrent writes?
- [ ] What if audit file is deleted mid-operation? (graceful recovery)

**Risk Score:** MEDIUM (audit is critical path, data loss is unacceptable)

**Escalation:** If hash-chain verification fails → escalate to cryptographer review

---

### Sprint 3: User Backend Implementations (Week 7-8)
**Theme:** Multi-auth support (local + LDAP + OIDC scaffolds)

**Deliverables (Engineer B):**
```
├─ Local Auth Backend (250 LOC)
│  ├─ core/plugins/corvin_plugins/providers/user_backend_local.py
│  ├─ authenticate(credentials) → check password hash
│  ├─ get_user(user_id) → return user from local DB
│  ├─ enforce_quota(user_id, resource) → check limits
│  ├─ list_users() → admin endpoint
│  └─ Tests (12 unit + integration)
│
├─ LDAP Backend Scaffold (150 LOC)
│  ├─ core/plugins/corvin_plugins/providers/user_backend_ldap.py
│  ├─ authenticate() → LDAP bind attempt
│  ├─ get_user() → LDAP lookup
│  ├─ Mock LDAP server for testing
│  └─ Tests (8 unit tests)
│
├─ OIDC Backend Scaffold (150 LOC)
│  ├─ core/plugins/corvin_plugins/providers/user_backend_oidc.py
│  ├─ authenticate() → OIDC token exchange
│  ├─ get_user() → userinfo endpoint
│  ├─ Mock OIDC server for testing
│  └─ Tests (8 unit tests)
│
└─ Integration Tests (Engineer C)
   ├─ test_user_backends_integration.py (10+ tests)
   │  ├─ All 3 backends work together
   │  ├─ Tenant isolation (Tenant A uses LDAP, B uses local)
   │  ├─ Quota enforcement works
   │  └─ Guest mode (optional)
```

**Test Gates (Tier 1-4):**
- [ ] Tier 1: `ruff`, `mypy --strict`
- [ ] Tier 2: Unit tests (28+, all pass)
- [ ] Tier 3: Integration (10+, multi-backend scenarios)
- [ ] Tier 4: E2E (full auth flow end-to-end)

**Adversarial Review (Engineer C):**
- [ ] Password hashing uses bcrypt (not MD5)?
- [ ] LDAP connection pooling (not creating new connection per auth)?
- [ ] OIDC token validation (signature, expiry, nonce)?
- [ ] Are secrets (LDAP password, OIDC client secret) loaded safely?
- [ ] Quota enforcement: what if quota check itself fails?
- [ ] Thread-safety for all backends?
- [ ] Rate limiting on failed auth attempts (brute-force protection)?

**Risk Score:** HIGH (auth failures can lock users out, auth bypass is security incident)

**Escalation:** If LDAP password not hashed → must fix before merge

---

### Sprint 4: Circuit Breaker + Core Wiring (Week 9-10)
**Theme:** Glue code, fault isolation, plugin lifecycle

**Deliverables:**

**Engineer D: Circuit Breaker (150 LOC)**
```
├─ core/circuitbreaker/circuit_breaker.py
│  ├─ CircuitBreaker class (closed → open → half_open states)
│  ├─ Trip on timeout (30s per plugin)
│  ├─ Trip on N consecutive failures
│  ├─ Auto-recover (half-open state)
│  ├─ Fallback behavior (what to return when open)
│  └─ Tests (10 unit tests)
│
└─ core/logging/context.py (100 LOC, Engineer A)
   ├─ Thread-local correlation ID
   ├─ Tenant ID injection
   ├─ Request context manager
   └─ Tests (5 unit tests)
```

**Engineer D: Core Boot Wiring (200 LOC)**
```
├─ core/boot/plugin_bootstrap.py
│  ├─ Register AuditBackendPlugin
│  ├─ Register UserBackendPlugin (config-based selection)
│  ├─ Inject into FastAPI context
│  ├─ Verify all mandatory plugins loaded (tripwire)
│  └─ Tests (6 integration tests)
│
└─ core/api/middleware.py (80 LOC, Engineer A)
   ├─ Correlation ID injection
   ├─ Tenant ID extraction
   ├─ Request logging hook
   └─ Tests (3 integration tests)
```

**Engineer C: Full Integration Tests (250 LOC)**
```
└─ tests/integration/test_plugin_bootstrap.py (15 tests)
   ├─ Boot sequence completes
   ├─ All plugins registered
   ├─ Audit backend callable
   ├─ User backend callable
   ├─ Circuit breaker works
   ├─ Correlation IDs flow through
   ├─ Multi-tenant isolation
   └─ Graceful degradation (plugin disable → fallback)
```

**Test Gates (Tier 1-4):**
- [ ] Tier 1: `ruff`, `mypy --strict`
- [ ] Tier 2: Unit tests (24+, all pass)
- [ ] Tier 3: Integration (21+, boot + plugin interaction)
- [ ] Tier 4: E2E (full request flow: boot → plugin call → response)

**Adversarial Review (Engineer C):**
- [ ] Circuit breaker timeout realistic (not too short, not too long)?
- [ ] Circuit breaker state transitions correct (closed → open → half-open)?
- [ ] Correlation ID survives async context switches?
- [ ] Tenant isolation enforced (no cross-tenant ID leakage)?
- [ ] Boot fails if ANY mandatory plugin missing (tripwire works)?
- [ ] Fallback behaviors safe (don't expose internals)?
- [ ] FastAPI middleware ordering correct (runs before/after auth)?

**Risk Score:** CRITICAL (core wiring, if wrong, everything breaks)

**Escalation:** If tripwire doesn't fire → fix immediately, no exceptions

---

### Sprint 5: Structured Logging System (Week 11-12)
**Theme:** Observability, correlation, no PII

**Deliverables:**

**Engineer A: CorvinLogger Class (150 LOC)**
```
├─ core/logging/structured_logger.py
│  ├─ CorvinLogger class (init, error, warn, info, debug)
│  ├─ JSON output format (timestamp, level, component, tenant_id, correlation_id)
│  ├─ PII scrubbing (regex checks: email, SSN, credit card)
│  ├─ Context injection (correlation_id, tenant_id via thread-local)
│  └─ Tests (10 unit tests)
│
└─ core/logging/pii_checker.py (80 LOC)
   ├─ Scan for email addresses
   ├─ Scan for SSN patterns
   ├─ Scan for credit card patterns
   ├─ Raise on detection
   └─ Tests (5 unit tests)
```

**Engineer A: Plugin Logging Integration (100 LOC)**
```
├─ core/plugins/corvin_plugins/registry.py (updated)
│  ├─ Auto-log plugin.loaded event
│  ├─ Auto-log plugin.unloaded event
│  ├─ Auto-log plugin.health_check failures
│  └─ Tests (5 integration tests)
```

**Engineer C: Logging Tests + Metrics (200 LOC)**
```
├─ tests/unit/test_structured_logger.py (12 tests)
│  ├─ JSON output correct format
│  ├─ Correlation IDs preserved
│  ├─ PII scrubbing works
│  ├─ No stack traces in logs
│  └─ Concurrent logging thread-safe
│
├─ core/telemetry/metrics.py (100 LOC)
│  ├─ Prometheus counters (plugin_load_total, etc.)
│  ├─ Prometheus histograms (plugin_latency_seconds, etc.)
│  └─ Tests (5 unit tests)
│
└─ tests/integration/test_logging_e2e.py (8 tests)
   ├─ Full request logged with correlation ID
   ├─ Audit events logged
   ├─ Metrics collected
   └─ No PII in any log
```

**Test Gates (Tier 1-4):**
- [ ] Tier 1: `ruff`, `mypy --strict`
- [ ] Tier 2: Unit tests (32+, all pass)
- [ ] Tier 3: Integration (13+, logging flows through plugins)
- [ ] Tier 4: E2E (request → full log trail)

**Adversarial Review (Engineer C):**
- [ ] PII scrubbing catches all patterns (test with real examples)?
- [ ] Scrubbing doesn't have false positives (block legitimate text)?
- [ ] Correlation IDs survive thread switches (verify with async tests)?
- [ ] Prometheus metrics parseable (run scraper against endpoint)?
- [ ] Overhead <5% (measure: log with/without)?
- [ ] JSON format queryable by grep/jq (no escaping surprises)?
- [ ] Logging doesn't lose events (async queue, no silent drops)?

**Risk Score:** MEDIUM (observability, not critical path, but GDPR-relevant)

**Escalation:** If PII slip through scrubber → improve regex, re-test, no merge

---

### Sprint 6: Chaos + Performance Testing (Week 13-14)
**Theme:** Stress-test, adversarial scenarios, SLA validation

**Deliverables (Engineer C leads):**

**Chaos Tests (100 LOC)**
```
├─ tests/chaos/test_audit_failures.py
│  ├─ Audit backend crashes → core continues
│  ├─ Audit slow (5s latency) → circuit breaker kicks in
│  ├─ Audit file missing → graceful recovery
│  ├─ Hash-chain corrupted → detected + logged
│  └─ 8 chaos scenarios
│
├─ tests/chaos/test_auth_failures.py
│  ├─ Auth backend crashes → fail gracefully
│  ├─ Auth slow → timeout + circuit breaker
│  ├─ LDAP server down → fallback to cache
│  ├─ Quota check fails → deny request
│  └─ 8 chaos scenarios
│
└─ tests/chaos/test_multi_tenant.py
   ├─ Tenant A's plugin restart doesn't affect Tenant B
   ├─ Tenant A's quota doesn't leak to Tenant B
   ├─ Tenant A's logs don't appear in Tenant B's stream
   └─ 5 multi-tenant scenarios
```

**Performance Tests (50 LOC)**
```
├─ tests/performance/test_latency_sla.py
│  ├─ Audit event: <10ms (p99)
│  ├─ Auth check: <100ms (p99)
│  ├─ Logging overhead: <5% of request time
│  └─ 5 latency benchmarks
│
└─ tests/performance/test_throughput.py
   ├─ 1000 concurrent requests + plugins handle it
   ├─ Circuit breaker doesn't cascade failures
   └─ 3 throughput tests
```

**Load Tests (50 LOC)**
```
└─ tests/load/test_24h_stability.py
   ├─ Run 10k requests over simulated 24h
   ├─ No memory leaks (audit file stays small)
   ├─ Hash-chain integrity maintained
   ├─ Correlation IDs don't accumulate
   └─ 1 long-running test (60+ min runtime)
```

**Test Gates (Tier 3-5):**
- [ ] Tier 3: Integration (16 chaos tests pass)
- [ ] Tier 4: Performance (8 benchmarks pass, SLAs met)
- [ ] Tier 5: Chaos (all 16 scenarios recover gracefully)

**Adversarial Review (Engineer C):**
- [ ] Chaos tests realistic (actual failure modes, not contrived)?
- [ ] SLAs achievable (not optimistic)?
- [ ] Recovery is automatic (not "manual intervention required")?
- [ ] Load test runs long enough (24h simulated, minimum)?
- [ ] Memory profiling shows no leaks (use memory_profiler)?
- [ ] Are we testing the RIGHT things (not just "doesn't crash")?

**Risk Score:** CRITICAL (if chaos tests fail, code not ready for production)

**Escalation:** If SLA missed → optimize or adjust SLA, explicit decision

---

### Sprint 7: Documentation + Beta Rollout (Week 15-16)
**Theme:** Handoff to ops, rollback plan, production readiness

**Deliverables:**

**Engineer A: Operator Docs (150 LOC markdown)**
```
├─ docs/operators/PLUGIN_MIGRATION_GUIDE.md
│  ├─ "Migrating to Plugin System"
│  ├─ Config format (tenant.corvin.yaml)
│  ├─ Backwards compat (env vars → plugin config)
│  ├─ Troubleshooting (audit plugin slow, auth timeout, etc.)
│  └─ Rollback procedure
│
└─ docs/operators/PLUGIN_ARCHITECTURE.md
   ├─ How plugins work (lifecycle, registry, health checks)
   ├─ Per-plugin SLAs
   ├─ Monitoring (Prometheus queries)
   └─ Alerting rules
```

**Engineer B: Plugin Author Docs (150 LOC markdown)**
```
├─ docs/authors/AUDIT_PLUGIN_GUIDE.md
│  ├─ How to implement AuditBackend
│  ├─ Hash-chain requirements
│  ├─ Testing checklist
│  └─ Example: S3-based audit backend
│
└─ docs/authors/USER_PLUGIN_GUIDE.md
   ├─ How to implement UserBackend
   ├─ Multi-auth patterns
   ├─ Quota enforcement
   └─ Example: Okta OIDC integration
```

**Engineer D: Deployment Ops (100 LOC)**
```
├─ ops/k8s/plugin-system-monitoring.yaml
│  ├─ Prometheus scrape config
│  ├─ Grafana dashboards (JSON)
│  ├─ Alert rules (circuit breaker trips, latency SLA breach, etc.)
│  └─ Health check endpoints
│
└─ ops/rollback_plan.md
   ├─ If plugins fail: revert to single-plugin mode
   ├─ If audit fails: fall back to stderr logging
   ├─ If auth fails: allow guest mode temporarily
   └─ Communication script for ops
```

**Engineer C: Release Testing (100 LOC)**
```
├─ tests/release/test_beta_readiness.py (10 tests)
│  ├─ All 56+ tests pass
│  ├─ No PII in logs
│  ├─ Hash-chain verifiable
│  ├─ Plugins load in order
│  ├─ Fallbacks work
│  ├─ Metrics exported
│  ├─ Docs render without errors
│  └─ Code review checklist green
│
└─ RELEASE_NOTES.md
   ├─ What changed (plugins, logging, circuit breaker)
   ├─ Migration steps
   ├─ New config options
   ├─ Known limitations (OIDC backend is scaffold, not prod-ready)
   └─ How to report issues
```

**Test Gates (Tier 2-4):**
- [ ] Tier 2: All docs render, no broken links
- [ ] Tier 3: Monitoring setup works (scrape endpoint, dashboards load)
- [ ] Tier 4: Beta rollout to staging environment

**Adversarial Review (Engineer C):**
- [ ] Are rollback instructions actually tested (not theoretical)?
- [ ] Do ops know how to disable plugins (kill switch exists)?
- [ ] Are monitoring alerts actionable (can ops fix the alert)?
- [ ] Docs match code (check for stale examples)?
- [ ] Is release procedure clear (30 steps or 3)?
- [ ] Known limitations honest (don't hide tech debt)?

**Risk Score:** MEDIUM (documentation quality affects production support)

**Escalation:** If docs incomplete → complete them before staging rollout

---

## Adversarial Code Review Process

### Review Checklist (Every PR)

**Security (Engineer C):**
- [ ] No hardcoded credentials (check for "password", "token", "secret")
- [ ] No `eval()` / `exec()` / dynamic code execution
- [ ] Input validation present (sanitize before using)
- [ ] No PII in logs/errors (check error messages, test data)
- [ ] SQL injection impossible (use parameterized queries)
- [ ] GDPR compliance (retention, erasure, consent)

**Correctness (Engineer C + Domain Expert):**
- [ ] Code matches PR description (no scope creep)
- [ ] Happy path works (read the test)
- [ ] Error handling complete (what if X fails?)
- [ ] Edge cases covered (empty input, null, boundary values)
- [ ] Backwards compatible (old config still works?)
- [ ] Async/concurrency safe (no race conditions?)

**Testing (Engineer C):**
- [ ] All Tier 1-2 gates passing (ruff, mypy)
- [ ] Unit tests cover happy + sad paths
- [ ] Integration tests cover plugin interaction
- [ ] Chaos tests validate failure recovery
- [ ] Performance tests pass SLA
- [ ] Code coverage >90% (report in CI)

**Quality (Engineer C):**
- [ ] Readability (could someone else maintain this code?)
- [ ] Naming (functions/variables self-explanatory)
- [ ] Duplication minimized (don't repeat logic)
- [ ] Comments only for "why", not "what" (code should be obvious)
- [ ] Tech debt tracked (TODOs reference issues)

### Review Veto Conditions (Immediate Reject, No Exceptions)

```
❌ VETO: Security concern (hardcoded secrets, SQL injection, etc.)
   → Re-review after fix. No expedited approval.

❌ VETO: Tests missing or failing
   → Complete tests. No "we'll add them in follow-up."

❌ VETO: Audit trail integrity compromised
   → Audit is load-bearing. Zero tolerance.

❌ VETO: PII in code, logs, or tests
   → Review for similar leaks. Test anti-patterns.

❌ VETO: Backwards compatibility broken (old config won't work)
   → Provide migration path or revert.

❌ VETO: Chaos tests not included (stability-critical code)
   → Add chaos tests. No "happy path only."
```

### Approval Criteria (All Must Pass)

```
✅ ALL security checks green
✅ ALL tests passing (Tier 1-4, relevant to change)
✅ Adversarial reviewer found NO veto conditions
✅ Code coverage >= 90%
✅ Docs updated (if behavior change)
✅ Tech debt tracked (if any shortcuts taken)
```

### Merge Authority

**Only Engineer C (Adversarial Lead) can merge to main.**

```
PR created by: Engineer A, B, or D
Review by: Engineer C (adversarial lens)
Approval by: Engineer C
Merge by: Engineer C (or run `git merge` locally)

If Engineer C has conflict of interest (reviewed own code):
  → Swap with another engineer for that PR only
```

---

## Risk Scoring + Escalation

### Per-Sprint Risk Score

| Sprint | Score | Reason | Escalation |
|--------|-------|--------|-----------|
| **0** | 🟢 LOW | Design-only, no runtime | None |
| **1** | 🟢 LOW | Templates, scaffolding | None |
| **2** | 🟠 MEDIUM | Audit backend (critical path) | Crypto review if concerns |
| **3** | 🔴 HIGH | Auth (security-critical) | Security team co-review |
| **4** | 🔴 CRITICAL | Core wiring (everything depends on it) | Tech lead sign-off |
| **5** | 🟠 MEDIUM | Logging (GDPR-relevant but non-critical) | Legal review of PII scrubbing |
| **6** | 🟠 MEDIUM | Chaos tests (validate resilience) | None |
| **7** | 🟠 MEDIUM | Rollout (ops impact) | Ops lead sign-off |

### Escalation Paths

**If Sprint Blocks (K_MAX = 5 iterations hit):**

1. **Root-cause-by-layer:** What layer failed? (code? design? integration? infra?)
2. **Diagnosis:** Why did 5 iterations not converge?
3. **Remedy:**
   - Code issue? → Swap engineer, fresh eyes
   - Design issue? → Call emergency design review (2h)
   - Infra issue? → Engage ops, extend timeline
   - External blocker? → Escalate to stakeholders

**Example:**
- Sprint 4, iteration 3: Circuit breaker state machine deadlocked under high concurrency
- Root cause (Layer 4): Logic error in half_open → closed transition
- Remedy: Engineer D + Engineer A pair on fix (2 engineers, 1 iteration)
- Result: Converges in iteration 4

---

## Timeline: Week-by-Week

```
Week 1-2   (Sprint 0)  Protocol Design             [A + B design, C tests]
Week 3-4   (Sprint 1)  Templates                   [A + B templates, C harness]
Week 5-6   (Sprint 2)  Audit Backend               [A impl, C tests, D support]
Week 7-8   (Sprint 3)  User Backends               [B impl, C tests, D support]
Week 9-10  (Sprint 4)  Circuit Breaker + Wiring   [D impl, C tests, A logging]
Week 11-12 (Sprint 5)  Structured Logging         [A logging, C metrics]
Week 13-14 (Sprint 6)  Chaos + Perf               [C testing, all engineers chaos review]
Week 15-16 (Sprint 7)  Docs + Staging Rollout    [A ops, B auth docs, D deploy, C review]

TOTAL: 16 weeks, 3-4 engineers, 50 engineer-weeks
```

---

## Critical Path (What Blocks What)

```
Sprint 0 (Protocols)
    ↓
Sprint 1 (Templates)
    ↓
Sprint 2-3 (Implementations, Parallel: Audit || Auth)
    ↓
Sprint 4 (Core Wiring, depends on both implementations)
    ↓
Sprint 5 (Logging, depends on Wiring)
    ↓
Sprint 6 (Chaos, depends on Logging)
    ↓
Sprint 7 (Rollout, depends on all above)
```

**Critical Path:** Sprints 0 → 1 → 2 (or 3) → 4 → 5 → 6 → 7  
**Float:** Sprint 3 can happen in parallel with Sprint 2 (no dependency)

---

## Deliverables Checklist

- [ ] 2 protocols (Audit + User) + 20+ unit tests
- [ ] 3 backend implementations (Audit, Local, LDAP, OIDC scaffolds) + 40+ unit tests
- [ ] Circuit breaker + core wiring + 15+ integration tests
- [ ] Structured logging + PII scrubbing + 20+ unit tests
- [ ] 21 chaos/performance/load tests (all pass)
- [ ] 56+ total tests (all passing, >90% coverage)
- [ ] 400+ lines operator documentation
- [ ] 400+ lines plugin author documentation
- [ ] Monitoring + alerting (Prometheus + Grafana)
- [ ] Release notes + migration guide
- [ ] Staging rollout (successful, 24h stability test)

---

## Definition of Done (Per Sprint)

✅ **Code:** All Tier 1-4 tests passing  
✅ **Review:** Adversarial code review approved (no veto conditions)  
✅ **Docs:** Behavior changes documented  
✅ **Coverage:** >90% code coverage  
✅ **Commit:** Merged to main, tagged with sprint number  
✅ **Communication:** Team aware of changes, no surprises

---

## No-Go Criteria (Sprint is Blocked)

❌ Security vulnerability found (must fix before progressing)  
❌ Audit trail integrity compromised  
❌ Chaos tests failing (stability is non-negotiable)  
❌ Performance SLA missed (optimize or adjust SLA, explicit decision)  
❌ Code review veto condition (must resolve)  

---

## Next Steps (Starting This Week)

### Day 1-2 (Today + Tomorrow)
- [ ] Team reads this plan
- [ ] Approve staffing (A, B, C, D committed)
- [ ] Set up Git branches (sprint/0-protocols, sprint/1-templates, etc.)
- [ ] Configure CI (ruff, mypy, pytest gates)

### Day 3-5 (This Week, Friday)
- [ ] Sprint 0 kickoff meeting (2h design discussion)
- [ ] Engineer A starts AuditBackend protocol
- [ ] Engineer B starts UserBackend protocol
- [ ] Engineer C sets up test infrastructure

### Week 2 (Sprint 0 Continues)
- [ ] Protocol design reviews (C-led, adversarial)
- [ ] Protocols finalized + merged
- [ ] Sprint 1 planning

---

**This is ready to execute. No more planning. Start coding tomorrow.** ⚓

