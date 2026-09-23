# REVIEW CHECKLISTS BY DIMENSION — Phase 1–10 Adversarial Review

**Created:** 2026-09-23, 13:00 UTC  
**Status:** ✅ COMPLETE

---

## HOW TO USE THIS DOCUMENT

Each dimension has a detailed checklist. For each item:

1. **Read the item** — Understand what to check
2. **Search the code** — Look for evidence (good or bad)
3. **Document findings** — Use the template below
4. **Report severity** — CRITICAL/HIGH/MEDIUM/LOW
5. **Assign remediation** — Add to task queue

**Template for each finding:**
```markdown
### Finding [ID]: [Title]
- **Severity:** [CRITICAL|HIGH|MEDIUM|LOW]
- **Checklist Item:** [Which item this addresses]
- **Files:** [core/audit/x.py, core/compliance/y.py, ...]
- **Description:** [What's wrong]
- **Evidence:** [Code snippet or example]
- **Impact:** [GDPR violation, security breach, etc.]
- **Remediation:** [How to fix]
```

---

# DIMENSION 1: SECURITY REVIEW CHECKLIST

## Category 1.1: Authentication & Authorization

### 1.1.1 Consent Gates Functional & Enforced
**Purpose:** GDPR Art. 6 requires affirmative consent before processing user data  
**Check:**
- [ ] `core/compliance/consent_store.py` exists and is implemented
- [ ] `ConsentStore` has methods: `grant()`, `revoke()`, `check()`, `is_active()`
- [ ] `@consent_required()` decorator exists in compliance module
- [ ] Decorator is applied to ALL routes that process user data
- [ ] Missing consent → 403 Forbidden (fail-closed)
- [ ] Consent has TTL (default 90 days), enforced via expiry check
- [ ] Revocation immediately blocks subsequent checks
- [ ] Concurrent consent checks don't race
- [ ] Tests cover: grant, revoke, expiry, tenant isolation, concurrent ops

**Files to Review:**
- `core/compliance/consent_store.py` (main implementation)
- `core/compliance/consent_audit_integration.py` (audit integration)
- `core/console/corvin_console/routes/` (all routes using decorator)
- `tests/compliance/test_consent_*.py` (test suite)

**Pass Criteria:** All routes have `@consent_required()` + audit events emitted

---

### 1.1.2 API Token Validation
**Purpose:** Prevent unauthorized API calls  
**Check:**
- [ ] All API endpoints require valid token/session
- [ ] Token validation happens BEFORE routing logic
- [ ] Invalid token → 401 Unauthorized
- [ ] Token expiry is enforced (no infinite tokens)
- [ ] Token revocation is immediate
- [ ] Rate limiting prevents brute force
- [ ] No hardcoded "bypass" tokens

**Files to Review:**
- `core/console/corvin_console/middleware/auth.py`
- `core/worker/engine_auth.py`
- `corvin_operator/bridges/shared/auth.py`

**Pass Criteria:** All endpoints require auth + rate limiting active

---

### 1.1.3 Cross-Tenant Isolation
**Purpose:** Prevent one tenant from accessing another tenant's data  
**Check:**
- [ ] Every DB query filters by `tenant_id`
- [ ] Audit trail queries filter by `tenant_id` (fail-closed if missing)
- [ ] Context/snapshot retrieval filters by `tenant_id`
- [ ] API routes validate `tenant_id` from auth context
- [ ] No fallback to default tenant if tenant_id missing
- [ ] Plugin execution includes tenant isolation
- [ ] Worker execution runs in tenant sandbox
- [ ] Tests verify cross-tenant queries fail

**Files to Review:**
- `core/compliance/audit_backend.py`
- `core/context/context_store.py`
- `core/plugins/lifecycle_loader.py`
- `core/worker/engine_integration.py`
- `tests/security/test_cross_tenant_isolation.py`

**Pass Criteria:** No cross-tenant data leaks (tested)

---

## Category 1.2: Cryptography & Secrets

### 1.2.1 Audit Chain Hash Integrity
**Purpose:** Prevent audit log tampering (GDPR Art. 32, ADR-0232)  
**Check:**
- [ ] `core/compliance/audit_atomic_transaction.py` implements hash-chaining
- [ ] Every audit event has `hash` field
- [ ] Every audit event has `prev_hash` field (links to previous)
- [ ] Hash calculation: `SHA256(prev_hash || json_dump(event))`
- [ ] Hash verification on read (catch corruption)
- [ ] Crash recovery works (atomic writes)
- [ ] No way to rewrite/delete audit events
- [ ] Boot tripwire verifies chain before any code runs (ADR-0232)
- [ ] Tests verify: single write, multi-event chain, corruption detection, recovery

**Files to Review:**
- `core/compliance/audit_atomic_transaction.py`
- `core/compliance/audit_backend.py`
- `core/compliance/tripwire.py` (boot verification)
- `tests/security/test_audit_chain_integrity.py`

**Pass Criteria:** Audit chain integrity verified + boot tripwire active

---

### 1.2.2 Data Encryption at Rest
**Purpose:** Protect data when stored on disk  
**Check:**
- [ ] Sensitive data (user context, transcripts) encrypted at rest
- [ ] Encryption key stored securely (not in code)
- [ ] Key rotation mechanism exists
- [ ] Decryption only by authorized code paths
- [ ] No plaintext secrets in error messages

**Files to Review:**
- `core/compliance/encryption.py`
- `core/security/key_manager.py`
- Search for hardcoded keys/secrets in all Python files

**Pass Criteria:** No plaintext sensitive data + encryption key rotation

---

### 1.2.3 TLS/mTLS for Networks
**Purpose:** Protect data in transit  
**Check:**
- [ ] A2A (app-to-app) communication uses mTLS
- [ ] Worker communication encrypted
- [ ] API endpoints use HTTPS
- [ ] Certificate validation enforced
- [ ] No self-signed certs in production
- [ ] Certificate expiry monitored

**Files to Review:**
- `corvin_operator/bridges/shared/tls_config.py`
- `core/bridges/a2a_bridge.py`
- `core/worker/engine_integration.py`

**Pass Criteria:** All network communication encrypted + certs validated

---

### 1.2.4 No Hardcoded Credentials
**Purpose:** Prevent secret leakage in source code  
**Check:**
- [ ] No API keys in code (all from env/vault)
- [ ] No passwords hardcoded
- [ ] No private keys in repo
- [ ] All secrets use environment variables or secure store
- [ ] CI/CD secrets masked in logs
- [ ] Code review catches hardcoded secrets

**Files to Review:**
- Search: `grep -r "password\|key\|secret" core/ | grep -v test | grep -v "def\|#\|param"`
- Check: `core/paths/env_vars.py` (all env var usage)

**Pass Criteria:** No hardcoded secrets found

---

## Category 1.3: Input Validation

### 1.3.1 SQL Injection Prevention
**Purpose:** Prevent malicious SQL from user input  
**Check:**
- [ ] All DB queries use parameterized statements
- [ ] No string concatenation in SQL queries
- [ ] Raw SQL only used where necessary (with sanitization)
- [ ] Tests verify SQL injection attempts fail

**Files to Review:**
- `core/compliance/consent_store.py`
- `core/compliance/audit_backend.py`
- Any file with `.execute()` or `.query()` calls

**Pass Criteria:** All SQL queries parameterized + injection tests pass

---

### 1.3.2 Command Injection Prevention
**Purpose:** Prevent shell command injection from user input  
**Check:**
- [ ] No `shell=True` in subprocess calls
- [ ] All command arguments passed as list (not string)
- [ ] User input sanitized before shell operations
- [ ] Tests verify injection attempts fail

**Files to Review:**
- Search: `grep -r "subprocess\|shell" core/ | grep -v test`
- Check: Any call to shell tools (ffmpeg, blender, etc.)

**Pass Criteria:** No shell injection possible

---

### 1.3.3 XSS (Cross-Site Scripting) Prevention
**Purpose:** Prevent malicious JavaScript in web UI  
**Check:**
- [ ] All user-provided content is HTML-escaped
- [ ] No `dangerouslySetInnerHTML` without sanitization
- [ ] CSP headers configured
- [ ] Input validation on all form fields
- [ ] Tests verify XSS attempts fail

**Files to Review:**
- `core/console/corvin_console/web-next/src/` (React components)
- Check for `innerHTML` or `dangerouslySetInnerHTML`

**Pass Criteria:** CSP headers set + no XSS vulnerabilities

---

## Category 1.4: Audit & Logging

### 1.4.1 All Security Events Logged
**Purpose:** Create audit trail of security-relevant events  
**Check:**
- [ ] Consent grant/revoke logged
- [ ] Auth failures logged
- [ ] Token expiry logged
- [ ] Plugin load/unload logged
- [ ] Policy violations logged
- [ ] Data access logged
- [ ] Configuration changes logged
- [ ] All logs include tenant_id + timestamp
- [ ] Logs cannot be deleted/rewritten (append-only)

**Files to Review:**
- `core/compliance/audit_backend.py`
- `core/compliance/consent_audit_integration.py`
- All compliance-related files

**Pass Criteria:** All CRITICAL events have audit entries

---

### 1.4.2 Audit Trail Hash-Chained
**Purpose:** Detect tampering with audit logs  
**Check:**
- [ ] Every event links to previous via hash
- [ ] Hash cannot be recomputed after reordering events
- [ ] Events are append-only (no deletion)
- [ ] Operator can verify chain integrity (`corvin audit verify`)
- [ ] Tests verify chain cannot be corrupted

**Files to Review:**
- `core/compliance/audit_atomic_transaction.py`
- `scripts/verify_audit_chain.py`

**Pass Criteria:** Audit chain verified + tamper detection working

---

## Category 1.5: Plugin Isolation

### 1.5.1 Plugin Sandbox Enforced
**Purpose:** Prevent plugins from accessing host system unsafely  
**Check:**
- [ ] Plugins run in isolated process (or restricted namespace)
- [ ] Plugin filesystem access restricted to plugin directory
- [ ] Plugin network access restricted to allowlist
- [ ] Plugin cannot read host env vars (except CORVIN_*)
- [ ] Plugin cannot load native code (unless signed)
- [ ] Tests verify sandbox escape attempts fail

**Files to Review:**
- `core/plugins/lifecycle_loader.py`
- `core/plugins/sandbox/` (if exists)
- Tests for plugin isolation

**Pass Criteria:** Plugin sandbox enforced + tests pass

---

### 1.5.2 Plugin Disable Enforcement
**Purpose:** Ensure disabled plugins never execute  
**Check:**
- [ ] Plugin disable flag checked BEFORE execution
- [ ] Disabled plugin not loaded into memory
- [ ] Disabled plugin routes return 404
- [ ] No fallback to default plugin if disabled
- [ ] Tests verify disabled plugins never run

**Files to Review:**
- `core/plugins/lifecycle_loader.py`
- Tests for plugin disable

**Pass Criteria:** Disabled plugins never execute

---

# DIMENSION 2: ARCHITECTURE REVIEW CHECKLIST

## Category 2.1: Layer Violations

### 2.1.1 L-Layers Properly Separated (L1–L44)
**Purpose:** Maintain clean architecture (no interdependencies)  
**Check:**
- [ ] Layer N only calls Layer N-1 or Layer 0 (no skipping)
- [ ] No backward calls (Layer N+1 calling Layer N)
- [ ] All layer boundaries clearly defined
- [ ] Import statements follow layer model
- [ ] No circular dependencies between modules

**Layers to Verify:**
- L1–L4 (Core + Compliance)
- L5–L10 (Routing + Context)
- L11–L20 (Data + Worker)
- L21–L30 (User experience)
- L31–L36 (Observability)

**Files to Review:**
- Create dependency graph: `scripts/analyze_dependencies.py`
- Check: `docs/claude-ref/layer-summary.md`

**Pass Criteria:** No layer violations + dependency graph acyclic

---

### 2.1.2 Dependency Graph Acyclic
**Purpose:** Prevent circular dependencies  
**Check:**
- [ ] Module A imports Module B
- [ ] Module B does NOT import Module A
- [ ] No A → B → C → A cycles
- [ ] Tools can detect cycles (e.g., `graphviz`)
- [ ] Tests verify no cycles

**Files to Review:**
- Run: `python3 scripts/detect_circular_dependencies.py`

**Pass Criteria:** No circular dependencies found

---

## Category 2.2: Protocol Compliance

### 2.2.1 Wire Formats Versioned
**Purpose:** Support backward compatibility  
**Check:**
- [ ] API responses include `version` field
- [ ] A2A messages include protocol version
- [ ] Plugin messages include version
- [ ] Version mismatch handled gracefully (fallback or error)
- [ ] Changelog documents protocol changes

**Files to Review:**
- `core/bridges/a2a_protocol.py`
- `core/console/corvin_console/routes/`
- Any wire format definition

**Pass Criteria:** All protocols versioned + backward compatible

---

### 2.2.2 Handshake Validation Enforced
**Purpose:** Prevent unauthorized peers from connecting  
**Check:**
- [ ] A2A connections require identity verification
- [ ] Plugin load requires signature verification
- [ ] Worker connections authenticated
- [ ] Handshake cannot be skipped
- [ ] Tests verify invalid handshakes fail

**Files to Review:**
- `corvin_operator/bridges/shared/a2a_audit.py`
- `core/plugins/lifecycle_loader.py`
- Worker authentication code

**Pass Criteria:** All handshakes validated + tests pass

---

## Category 2.3: Component Coupling

### 2.3.1 Loose Coupling Between Systems
**Purpose:** Make components independently testable  
**Check:**
- [ ] Components communicate via interfaces, not direct calls
- [ ] No hardcoded dependencies (use dependency injection)
- [ ] Mock-able components for testing
- [ ] Interfaces clearly defined (protocols/ABCs)
- [ ] No global singletons (except where documented)

**Files to Review:**
- Review major modules: audit, consent, plugins, skills
- Check for singleton patterns

**Pass Criteria:** Components loosely coupled + easily testable

---

## Category 2.4: State Management

### 2.4.1 Stateless Components Where Possible
**Purpose:** Simplify deployment and scaling  
**Check:**
- [ ] API handlers are stateless
- [ ] Worker execution stateless
- [ ] State stored in persistent backend (DB, cache)
- [ ] No mutable globals
- [ ] Tests verify idempotency

**Files to Review:**
- API route handlers
- Worker engine implementation

**Pass Criteria:** No unexpected mutable state

---

### 2.4.2 Race Condition Prevention
**Purpose:** Ensure correctness under concurrency  
**Check:**
- [ ] Audit writes are atomic (all-or-nothing)
- [ ] Consent checks are atomic
- [ ] Plugin state changes are atomic
- [ ] No time-of-check-time-of-use (TOCTOU) bugs
- [ ] Tests verify under concurrent load

**Files to Review:**
- `core/compliance/audit_atomic_transaction.py`
- `core/compliance/consent_store.py`
- Any stateful component

**Pass Criteria:** No race conditions + concurrent tests pass

---

## Category 2.5: Error Handling

### 2.5.1 Fail-Closed Mechanisms
**Purpose:** Default to denying access on error  
**Check:**
- [ ] Missing consent → 403 (not 200)
- [ ] Missing auth → 401 (not 200)
- [ ] Audit chain error → crash (not silent)
- [ ] Plugin error → isolate plugin (not crash host)
- [ ] Data classification error → block flow (not allow)
- [ ] Invalid configuration → fail at boot (not runtime)

**Files to Review:**
- All compliance enforcement points
- L44 (house-rules gate)
- L35 (egress lockdown)

**Pass Criteria:** All critical paths fail-closed

---

---

# DIMENSION 3: COMPLIANCE REVIEW CHECKLIST

## Category 3.1: GDPR Art. 5 (Principles)

### 3.1.1 Data Minimization Enforced
**Purpose:** Only collect/process data necessary for purpose  
**Check:**
- [ ] Context snapshots only include necessary fields
- [ ] No unnecessary user metadata collected
- [ ] Transcripts not stored unless explicitly needed
- [ ] Metrics are aggregated (not individual samples)
- [ ] Data retention policy enforced
- [ ] Tests verify minimal data collected

**Files to Review:**
- `core/context/context_store.py`
- `core/console/routes/` (what data is returned)
- `core/learning/event_persistence.py`

**Pass Criteria:** Only minimal necessary data collected

---

### 3.1.2 Purpose Limitation Enforced
**Purpose:** Data only used for stated purposes  
**Check:**
- [ ] Context data used only for routing/execution
- [ ] User data not shared with external services
- [ ] Audit data not used for marketing
- [ ] Tests verify data not misused

**Files to Review:**
- All data egress points (where data leaves CorvinOS)
- API integrations

**Pass Criteria:** Data only used for intended purposes

---

## Category 3.2: GDPR Art. 6 (Lawfulness)

### 3.2.1 Consent Gates Functional
**Purpose:** Require affirmative user consent  
**Check:**
- [ ] Consent store operational (tested in Dimension 1)
- [ ] Consent required BEFORE data processing
- [ ] Consent checked on every request
- [ ] Revocation immediate
- [ ] Audit logs every consent check
- [ ] GDPR-compliant consent form (specific + clear)

**Files to Review:**
- `core/compliance/consent_store.py`
- Consent UI/form in console

**Pass Criteria:** Consent gates operational (tested)

---

## Category 3.3: GDPR Art. 30 (Records of Processing)

### 3.3.1 Audit Trail Complete
**Purpose:** Document all processing activities  
**Check:**
- [ ] Every data access logged (audit trail)
- [ ] Every processing step logged
- [ ] Logs include: who, what, when, why
- [ ] Logs immutable (append-only)
- [ ] Logs can be exported for auditors
- [ ] Retention policy enforced (default 90d)

**Files to Review:**
- `core/compliance/audit_backend.py`
- `scripts/export_audit_for_auditor.py`

**Pass Criteria:** Complete audit trail maintained

---

## Category 3.4: GDPR Art. 32 (Security)

### 3.4.1 Encryption & Access Control
**Purpose:** Protect data from unauthorized access  
**Check:**
- [ ] Audit trail encrypted at rest (tested)
- [ ] User context encrypted at rest
- [ ] Network communication encrypted (TLS)
- [ ] Access to sensitive data requires auth
- [ ] Access control lists enforced
- [ ] Tests verify no unauthorized access

**Files to Review:**
- Encryption module
- Access control module
- Security tests

**Pass Criteria:** Data encrypted + access controlled

---

## Category 3.5: EU AI Act Art. 5 (Risk Mitigation)

### 3.5.1 High-Risk Uses Documented
**Purpose:** Identify and mitigate high-risk AI use cases  
**Check:**
- [ ] High-risk uses documented (e.g., automation decisions)
- [ ] Risk mitigation strategies in place
- [ ] Human oversight for critical decisions
- [ ] Documentation accessible to auditors

**Files to Review:**
- ADRs related to automation/delegation
- Delegation policy documentation

**Pass Criteria:** High-risk uses documented + mitigated

---

## Category 3.6: EU AI Act Art. 50 (Bot Disclosure)

### 3.6.1 Bot Disclosure Card Shown
**Purpose:** Transparently disclose AI nature (required)  
**Check:**
- [ ] User sees "You are interacting with an AI" on first use
- [ ] Disclosure card cannot be disabled
- [ ] Opt-out mechanism visible (`/pass`, `/leave`)
- [ ] User can exit at any time
- [ ] Audit logs disclosure display
- [ ] Tests verify disclosure shown + usable

**Files to Review:**
- Console UI (where disclosure shown)
- Routes for `/pass` and `/leave` commands
- Tests for disclosure

**Pass Criteria:** Disclosure shown + opt-out working

---

---

# DIMENSION 4: TESTING REVIEW CHECKLIST

## Category 4.1: Coverage

### 4.1.1 Unit Test Coverage (Target >85%)
**Purpose:** Test individual components in isolation  
**Check:**
- [ ] All public functions have unit tests
- [ ] Edge cases covered (empty input, None, max values)
- [ ] Error paths tested
- [ ] Mocking appropriate (not over-mocked)
- [ ] Coverage measured (>85% per module)

**Files to Review:**
- Run: `pytest --cov core/ --cov-report=term-missing`
- Identify modules <85% coverage

**Pass Criteria:** Unit test coverage >85% for all modules

---

### 4.1.2 Integration Test Coverage (Target >70%)
**Purpose:** Test component interactions  
**Check:**
- [ ] Major workflows tested end-to-end
- [ ] Plugin + host interaction tested
- [ ] Consent + audit integration tested
- [ ] Worker + routing integration tested
- [ ] Cross-layer flows tested

**Files to Review:**
- `tests/integration/`

**Pass Criteria:** Major workflows have integration tests

---

### 4.1.3 E2E Test Coverage (Target: All Entry Points)
**Purpose:** Test via real transport/interface boundaries  
**Check:**
- [ ] All API endpoints have E2E tests
- [ ] CLI commands have E2E tests
- [ ] Plugin lifecycle tested end-to-end
- [ ] Worker execution tested end-to-end
- [ ] A2A communication tested end-to-end
- [ ] Tests use real HTTP, subprocess, etc. (not mocked)

**Files to Review:**
- `tests/e2e/`
- Identify entry points without E2E tests

**Pass Criteria:** All entry points have E2E tests

---

## Category 4.2: Adversarial Testing

### 4.2.1 Malicious Input Testing
**Purpose:** Ensure system handles attacks  
**Check:**
- [ ] SQL injection attempts fail
- [ ] Command injection attempts fail
- [ ] XSS attempts fail
- [ ] Large input handled safely (no crash)
- [ ] Invalid JSON rejected
- [ ] Tests include fuzzing

**Files to Review:**
- `tests/security/test_injection_*.py`
- Search for fuzz tests

**Pass Criteria:** All injection attempts fail safely

---

### 4.2.2 Race Condition Testing
**Purpose:** Ensure correctness under concurrency  
**Check:**
- [ ] Concurrent audit writes don't corrupt
- [ ] Concurrent consent checks don't race
- [ ] Plugin loading under concurrency safe
- [ ] Tests run operations in parallel

**Files to Review:**
- `tests/concurrency/` or similar
- Tests with threading/asyncio

**Pass Criteria:** No race conditions detected

---

### 4.2.3 Resource Exhaustion Testing
**Purpose:** Ensure system doesn't crash on resource limits  
**Check:**
- [ ] Large files handled safely
- [ ] Many concurrent requests handled
- [ ] Memory doesn't leak under load
- [ ] Timeout prevents infinite loops
- [ ] Tests measure resource usage

**Files to Review:**
- `tests/performance/`

**Pass Criteria:** System handles resource exhaustion gracefully

---

## Category 4.3: Test Infrastructure

### 4.3.1 Tests Are Reproducible
**Purpose:** Tests must pass consistently  
**Check:**
- [ ] No flaky tests (pass sometimes, fail sometimes)
- [ ] Deterministic results (no randomness affecting results)
- [ ] Clean setup/teardown
- [ ] No hardcoded timestamps
- [ ] No external dependencies (no network calls)

**Files to Review:**
- Run tests multiple times: `for i in {1..5}; do pytest; done`

**Pass Criteria:** All tests pass consistently

---

### 4.3.2 Test Data Isolated
**Purpose:** Tests don't interfere with each other  
**Check:**
- [ ] Each test has its own data
- [ ] Tenant isolation enforced in tests
- [ ] Database cleaned between tests
- [ ] Mock files cleaned between tests
- [ ] No shared mutable state

**Files to Review:**
- Test fixtures and setup code

**Pass Criteria:** Tests pass in any order, results consistent

---

---

# DIMENSION 5: PRODUCTION REVIEW CHECKLIST

## Category 5.1: Operability

### 5.1.1 Deployment Process Documented
**Purpose:** Enable safe, repeatable deployments  
**Check:**
- [ ] Deployment runbook exists and is tested
- [ ] Pre-deployment checklist defined
- [ ] Deployment steps are reproducible
- [ ] Post-deployment verification defined
- [ ] Rollback procedure documented + tested

**Files to Review:**
- `docs/operations/deployment-runbook.md`
- `scripts/deploy.sh`

**Pass Criteria:** Deployment tested + runbook clear

---

### 5.1.2 Configuration Management Clear
**Purpose:** Make configuration changes safe and auditable  
**Check:**
- [ ] All config in `tenant.corvin.yaml` or env vars
- [ ] No hardcoded config in code
- [ ] Config changes logged + audited
- [ ] Config validation at startup
- [ ] Invalid config → fail at boot (fail-closed)

**Files to Review:**
- `core/paths/config.py` or similar
- Config parsing/validation code

**Pass Criteria:** All config validated + logged

---

## Category 5.2: Monitoring

### 5.2.1 Key Metrics Defined
**Purpose:** Understand system health  
**Check:**
- [ ] Latency metrics (p50, p95, p99)
- [ ] Error rate metrics
- [ ] Throughput metrics
- [ ] Resource usage (CPU, memory, disk)
- [ ] Audit trail metrics (total events, write rate)
- [ ] Consent gate metrics (requests, denials)
- [ ] Plugin metrics (load time, execute time)

**Files to Review:**
- `core/telemetry/metrics.py` or similar
- Prometheus configuration

**Pass Criteria:** 10+ key metrics defined

---

### 5.2.2 Alerting Configured
**Purpose:** Notify on-call when system degrades  
**Check:**
- [ ] Alerts for CRITICAL failures (audit chain, consent)
- [ ] Alerts for HIGH degradation (latency spike, error rate)
- [ ] Alert thresholds reasonable (avoid false alarms)
- [ ] On-call rotation defined
- [ ] Alerting tested (can trigger alert manually)

**Files to Review:**
- Prometheus alert rules
- Alertmanager configuration

**Pass Criteria:** Alerts for all CRITICAL scenarios

---

## Category 5.3: Incident Response

### 5.3.1 Runbooks Written
**Purpose:** Enable fast incident response  
**Check:**
- [ ] Runbook for "Audit chain corrupted"
- [ ] Runbook for "Consent gate not responding"
- [ ] Runbook for "Worker engine failure"
- [ ] Runbook for "Data leak detected"
- [ ] Each runbook has: symptoms, diagnosis, remediation, escalation

**Files to Review:**
- `docs/operations/runbooks/`

**Pass Criteria:** Runbooks for top 5 failure scenarios

---

### 5.3.2 Escalation Paths Clear
**Purpose:** Know who to contact for each scenario  
**Check:**
- [ ] On-call engineer defined
- [ ] Manager escalation path defined
- [ ] Security team escalation defined
- [ ] Legal team escalation for compliance incidents
- [ ] Contact list updated

**Files to Review:**
- `docs/operations/escalation-paths.md`

**Pass Criteria:** Escalation paths documented + current

---

## Category 5.4: Disaster Recovery

### 5.4.1 Backup Strategy Defined
**Purpose:** Recover from data loss  
**Check:**
- [ ] Audit trail backed up daily
- [ ] Backups encrypted
- [ ] Backups tested (restore from backup works)
- [ ] Backup retention policy (e.g., 30 days)
- [ ] Offsite backups (not just local)

**Files to Review:**
- Backup scripts
- Backup retention policy

**Pass Criteria:** Backups working + tested recovery

---

### 5.4.2 Recovery Procedures Tested
**Purpose:** Ensure DR plan actually works  
**Check:**
- [ ] RTO (Recovery Time Objective) defined (e.g., 1 hour)
- [ ] RPO (Recovery Point Objective) defined (e.g., 15 min)
- [ ] Disaster recovery drill scheduled
- [ ] Recovery procedure documented + tested
- [ ] Recovery time measured + acceptable

**Files to Review:**
- DR runbooks
- DR drill results

**Pass Criteria:** DR procedure tested + RTO/RPO met

---

## Category 5.5: SLOs & Error Budgets

### 5.5.1 SLOs Defined Per Service
**Purpose:** Make reliability explicit  
**Check:**
- [ ] Availability SLO (e.g., 99.5%)
- [ ] Latency SLO (e.g., p99 <500ms)
- [ ] Error rate SLO (e.g., <0.1%)
- [ ] SLOs documented in `docs/operations/slos.md`
- [ ] SLO compliance tracked in dashboard

**Files to Review:**
- `docs/operations/slos.md`
- SLO tracking dashboard

**Pass Criteria:** SLOs defined for all layers

---

### 5.5.2 Error Budget Tracking
**Purpose:** Use SLO budget strategically  
**Check:**
- [ ] Error budget calculated (100% - SLO%)
- [ ] Remaining budget tracked daily
- [ ] Budget burndown visible in dashboard
- [ ] High burn-rate alerts (e.g., >50% budget in 1 day)
- [ ] Budget depleted → freeze deployments (reduce risk)

**Files to Review:**
- Dashboard/monitoring for error budget

**Pass Criteria:** Error budget tracked + alerts configured

---

---

## SUMMARY: CHECKLIST USAGE

1. **Per Dimension:**
   - [ ] Security (1.1–1.5): 15 checks
   - [ ] Architecture (2.1–2.5): 12 checks
   - [ ] Compliance (3.1–3.6): 8 checks
   - [ ] Testing (4.1–4.3): 9 checks
   - [ ] Production (5.1–5.5): 12 checks
   - **Total: 56 checks**

2. **For Each Check:**
   - Read the check definition
   - Search the codebase for evidence
   - Document findings (use Finding template)
   - Assign severity (CRITICAL → LOW)

3. **Reporting:**
   - Count findings per dimension
   - Create DIMENSION_REVIEW_FINDINGS.md
   - Prioritize by severity
   - Assign to remediation streams

---

**Phase 0 Complete: Orchestration + Framework + Checklists ready for Phase 1–2 reviews**

Next: Begin DIMENSION 1 (Security) review (Week 2)

