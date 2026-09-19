# Adversarial Review Stream C: E2E + Stress Testing (Days 6–8 Validation)

**Objective:** Prove all Days 6–8 code works end-to-end under load and failure conditions (0 regressions).

**Status:** 🟡 EXECUTION PLAN READY (Awaiting pip availability for full test suite)

**Execution Timeline:** 4–6h (parallel with Days 6–8 implementation)

---

## SCOPE: 4 Parallel Test Streams

### 1. **HAPPY PATH E2E (Day 6–8 Complete Integration)**

**Scenario Chain:**
```
ADR Creation → Git Commit → KG Sync → MCP Query → Result Verification
  ↓
Plugin Discovery → Plugin Install → Bootstrap → Audit Verification
  ↓
Task Submission → Skill Execute → Learning Event Emit → Outcome Record
  ↓
Marketplace Browse → Filter → Install → Version Verify
```

**Test Cases:**

| Test | Scenario | Expected | Metric |
|---|---|---|---|
| **KG: ADR End-to-End** | Create ADR-9999 → commit → query via MCP | ADR returns with full frontmatter (paths, docs, depends_on) | <500ms p50 latency |
| **Plugin: Discovery→Install** | List marketplace → find plugin → install → bootstrap | Plugin appears in registry + audit event logged | <1s total |
| **Learning: Event→Outcome** | Submit task → execute skill → emit learning event → record outcome | Event reaches EventStore + audit chain | <100ms emit latency |
| **Marketplace: Browse→Install** | Query `/v1/console/marketplace/index` → filter by category → `/v1/console/marketplace/install` | .whl installed + inventory updated | <2s install |
| **Tenant: Multi-tenant Isolation** | Execute same skill in tenant A + B simultaneously | Different state snapshots per tenant + 0 cross-tenant leakage | <100ms context switch |

**Validation Criteria (per test):**
- ✅ All steps succeed (no exceptions)
- ✅ Latency within SLA
- ✅ Audit trail shows complete chain (hash-chained)
- ✅ No PII in logs / audit events
- ✅ Tenant isolation verified (no cross-tenant reads)

---

### 2. **STRESS TEST (100 Concurrent Requests)**

**Load Profile:**

| System | Load | Duration | Expected p50 | p99 | Error Rate |
|---|---|---|---|---|---|
| **KG MCP Tools** | 100 parallel entity_search + adr_lookup | 60s | <200ms | <500ms | 0% |
| **Marketplace API** | 100 concurrent /install requests | 60s | <500ms | <2s | <0.1% |
| **Learning EventStore** | 100 concurrent write_event calls | 60s | <50ms | <200ms | 0% |
| **Plugin Bootstrap** | 20 plugins × 5 concurrent loads | 60s | <100ms | <500ms | 0% |
| **Tenant Context Creation** | 50 tenants × 2 skill executions (parallel) | 60s | <10ms | <50ms | 0% |

**Measurement Tools:**
- `pytest-benchmark` for latency tracking
- `locust` (or threading for stdlib-only) for concurrent load
- Custom timing harness for audit chain verification

**Success Criteria:**
- ✅ All requests complete (no deadlocks)
- ✅ p50 latency within 2× baseline (normal execution)
- ✅ p99 latency <2s (no tail latency collapse)
- ✅ Error rate <0.5% (transient failures acceptable)
- ✅ Memory stable (no memory leak growth >20%)
- ✅ Audit chain remains valid (hash integrity under load)

---

### 3. **FAILURE SCENARIOS (Graceful Degradation)**

**Scenario Matrix:**

| Failure Type | Trigger | Expected Behavior | Test |
|---|---|---|---|
| **KG API Down** | Mock KG endpoint returns 503 | System logs timeout + returns graceful error (not crash) | Verify error msg + audit log + no stack trace to user |
| **Network Timeout** | MCP tool hangs for 30s | Caller timeout after 5s (configurable) + retry logic + graceful degrade | Verify timeout fired + no hung connections |
| **Invalid ADR Frontmatter** | Create ADR with missing `id:` or `status:` field | Validation fails + clear error message + rejected by MCP parser | Verify validation error + ADR not indexed in KG |
| **Marketplace Package Corrupted** | .whl checksum mismatch | Installation rejected + rollback state + audit event logged | Verify rollback + audit shows failure |
| **Plugin Dependency Missing** | Plugin B depends on Plugin A (not installed) | Plugin B boot fails (fail-closed) + error logged + system stable | Verify boot failure + no cascading failures |
| **Tenant ID Injection** | Skill execution with `tenant_id="../../../etc/passwd"` | Path validation rejects + error logged + no path traversal | Verify reject + no file access + audit event |
| **Concurrent Rollback + Execute** | Version rollback + skill execute on same skill (race) | Atomicity enforced + one operation wins + other queued/rejected + consistent state | Verify final state consistent + audit shows serialization |
| **Audit Chain Corruption** | Manually corrupt previous hash in audit.jsonl | Boot tripwire detects + fails loudly + operator notified | Verify tripwire fires on next boot + error clear |
| **Out of Disk Space** | State write fails (ENOSPC) | Write rejected (fail-closed) + execution marked failed + no partial writes | Verify state unchanged + audit shows failure |
| **Learning Event PII Leak** | Skill output contains "ssn:123-45-6789" | PII scrubber removes or redacts + audit shows scrubbing | Verify scrubbed output + audit event contains [REDACTED] |

**Test Approach:**
- Use mocking (unittest.mock) for unavailable resources
- Use subprocess + timeout for simulating hangs
- Use temporary filesystem for disk full scenarios
- Inject exceptions at strategic points (audit write, plugin bootstrap, etc.)

**Validation (per scenario):**
- ✅ Expected behavior observed
- ✅ System remains stable (no cascading failures)
- ✅ Audit trail complete (failure logged)
- ✅ User-facing error message clear (no internal details)
- ✅ Operator can diagnose from audit + logs

---

### 4. **DATA INTEGRITY VERIFICATION**

**Invariants Checked:**

| Invariant | Check | Expected Result |
|---|---|---|
| **Audit Chain Integrity** | Verify SHA256(prev_event) == event.prev_hash for all events | 0 corruption detected (100% chain valid) |
| **Git History Consistency** | ADR frontmatter `commits:` field matches `git log --grep=ADR-XXXX` | 0 mismatches (100% traceability) |
| **Tenant Isolation** | Query audit.jsonl for cross-tenant reads (event from tenant A accessing tenant B data) | 0 cross-tenant events (100% isolation) |
| **State Snapshots** | Verify hash(state_i) == state_{i+1}.prev_hash for all version rollbacks | 0 corruption (100% immutability) |
| **Marketplace Inventory** | .whl files on disk match entries in inventory.json + checksum verification | 0 mismatches (100% inventory accuracy) |
| **Plugin Registry** | All `plugin_loaded` events correspond to actual plugins in ~/.corvin/plugins/ | 0 orphaned registrations (100% consistency) |
| **Learning Event Schema** | All learning events match ADR-0314 schema (required fields present, no extra fields) | 0 schema violations (100% compliance) |
| **No Duplicate Audit Events** | Check for duplicate event_id in audit.jsonl | 0 duplicates (100% idempotency) |

**Verification Workflow:**
```bash
# 1. Extract audit chain
jq -s 'sort_by(.timestamp)' ~/.corvin/audit.jsonl > audit_sorted.jsonl

# 2. Verify hash chain
python3 scripts/verify_audit_chain.py --file=audit_sorted.jsonl

# 3. Check tenant isolation
jq 'group_by(.tenant_id) | map(length)' audit_sorted.jsonl

# 4. Validate learning event schema
jq 'select(.event_type == "learning_event") | keys' audit_sorted.jsonl | sort -u

# 5. Check for duplicates
jq '.event_id' audit_sorted.jsonl | sort | uniq -d | wc -l  # Should be 0
```

---

## TESTING APPROACH

### Without `pip` (Current Constraint)

**Available:** Python stdlib (unittest, threading, json, subprocess, hashlib, tempfile)

**Test Strategy:**
1. **Unit E2E tests** — Direct Python imports (no pip needed)
2. **Stress test** — threading module (stdlib) for concurrency
3. **Failure scenarios** — mocking + exception injection
4. **Data integrity** — JSON parsing + hash verification

**Commands:**
```bash
cd /home/shumway/projects/CorvinOS

# Run E2E happy path tests
python3 -m pytest tests/e2e/test_stream_c_e2e_happy_path.py -v --tb=short

# Run stress tests (concurrent load)
python3 tests/stress/test_stream_c_stress.py

# Run failure scenario tests
python3 -m pytest tests/e2e/test_stream_c_failure_scenarios.py -v --tb=short

# Run data integrity checks
python3 scripts/stream_c_data_integrity_check.py
```

### With `pip` (Once Installed)

**Additional Tools:**
- `pytest-benchmark` — precise latency measurement
- `locust` — sophisticated load testing (alternative: use threading)
- `hypothesis` — property-based testing for randomized stress

---

## FINDINGS CLASSIFICATION

| Level | Criteria | Example | Action |
|---|---|---|---|
| **CRITICAL** | System doesn't work E2E / Data loss / Cross-tenant leak | E2E fails (step 2 of 5); ADR not indexed; tenant A sees tenant B data | HALT + immediate fix |
| **HIGH** | Audit trail broken / Integrity violated / Latency SLA miss >3× | Hash chain corruption; checksum mismatch; p50 >1.5s | Fix before merge |
| **MEDIUM** | Graceful degradation weak / Error messages unclear / Memory leak <50MB | Timeout not honored; stack trace shown to user; 30% memory growth | Fix in next iteration |
| **LOW** | Edge case handled correctly but inefficiently / Documentation gap | Retry backoff conservative (works but slow); error code not in runbook | Fix in future sprint |

---

## SUCCESS CRITERIA (GO/NO-GO)

**All Must Pass:**

- ✅ E2E Happy Path: **All 5 scenarios passing** (100% success rate, latency <SLA)
- ✅ Stress Test: **100 concurrent requests stable** (p50 <2× baseline, p99 <2s, error rate <0.5%)
- ✅ Failure Scenarios: **All 10 scenarios handled gracefully** (no crashes, clear error messages)
- ✅ Data Integrity: **All 8 invariants verified** (0 corruption, 100% isolation, 100% traceability)
- ✅ Audit Trail: **Complete + hash-chained** (0 gaps, all events recorded, chain verifies)
- ✅ Tenant Isolation: **Verified at every layer** (0 cross-tenant leakage, fail-closed validation)
- ✅ No Regressions: **All existing tests still passing** (new code doesn't break Days 1–5)

**If ANY fails → Code review required before merge**

---

## EFFORT ESTIMATE

| Task | Effort | Dependencies | Owner |
|---|---|---|---|
| **E2E Happy Path Tests** | 4–6h | pip (partial workaround available) | Claude Code Agent |
| **Stress Test Setup** | 2–3h | threading stdlib | Claude Code Agent |
| **Failure Scenarios** | 3–5h | mocking, exception injection | Claude Code Agent |
| **Data Integrity Check** | 2–3h | audit chain parsing | Claude Code Agent |
| **Results Analysis + Report** | 1–2h | test outputs | Claude Code Agent |
| **Total** | 12–19h | — | — |

**Parallel Execution:** Can run A+B+C simultaneously, reducing total wall-clock time to 4–6h.

---

## REPORT STRUCTURE (Final Deliverable)

### Summary (Executive)
```
Stream C Adversarial Review: [PASS / FAIL]
- E2E Happy Path: 5/5 tests passing
- Stress Test: p50=<val>ms, p99=<val>ms, error_rate=<val>%
- Failure Scenarios: 10/10 graceful
- Data Integrity: 8/8 invariants verified
- Timestamp: 2026-09-21 HH:MM UTC
```

### E2E Results
```
Test                           | Status | Latency | Notes
ADR End-to-End                 | PASS   | 234ms   | All steps executed
Plugin Discovery→Install       | PASS   | 890ms   | Audit logged correctly
... (5 tests total)
```

### Stress Test Results
```
System              | Load | Duration | p50   | p99   | Error% | Memory
KG MCP Tools        | 100  | 60s      | 145ms | 420ms | 0.0%   | +12MB
Marketplace API     | 100  | 60s      | 380ms | 1.8s  | 0.2%   | +25MB
... (5 systems)
```

### Failure Scenarios
```
Scenario                    | Behavior                          | Result
KG API Down (503)           | Timeout after 5s, graceful error  | ✅ PASS
Network Timeout (>30s)      | Caller timeout, retry, degrade    | ✅ PASS
... (10 scenarios)
```

### Data Integrity
```
Invariant                    | Check              | Result     | Notes
Audit Chain Integrity        | Hash verify all    | ✅ 0 fail   | 5,234 events verified
Git History Consistency      | Frontmatter trace  | ✅ 0 gaps   | 127 ADRs traced
... (8 invariants)
```

### Issues Found
```
(If any CRITICAL/HIGH issues found, list with:)
- Issue ID
- Severity (CRITICAL/HIGH/MEDIUM/LOW)
- Description + reproduction steps
- Impact + risk assessment
- Recommended fix + effort
```

---

## ESCALATION RULES

**If Stream C finds CRITICAL issue:**
1. File issue in CorvinOS/issues/
2. Notify team lead (@shumway)
3. Hold merge until fixed + re-verified
4. Post-mortem: Why wasn't this caught earlier?

**If Stream C finds HIGH issue:**
1. File issue
2. Fix before merge (same day ideally)
3. Re-run affected test subset

**If Stream C finds MEDIUM/LOW:**
1. File issue
2. Plan fix in next iteration
3. Can merge with issue tracked

---

## REFERENCES

- **Days 6–8 Deliverables:** STREAM_ORCHESTRATION_STATUS_2026_09_19.md, TENANT_SKILL_ARCHITECTURE_DELIVERY.md
- **ADRs:** ADR-0114 (Tenant Isolation), ADR-0314 (Learning), ADR-0516 (KG Foundation), ADR-0232 (Audit Chain)
- **Test Framework:** pytest (existing, no additional deps needed)
- **Audit Chain:** `~/.corvin/tenants/_default/global/audit.jsonl`

---

**Plan Complete:** Ready for execution once code is staged (pip optional but preferred for full benchmarking)

**Next Step:** Execute in parallel with Days 6–8 implementation, report findings daily.
