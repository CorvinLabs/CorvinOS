# Layer Forge E2E Test Plan (30+ Tests, ADR-2222 Style)

**Status:** Planning Phase  
**Target Completion:** Integration into CI/CD workflow  
**Scope:** Validate → Test → Enforce → Create → Promote lifecycle with audit-first design  
**Constraints:** E2E only (subprocess CLI, HTTP routes, not unit tests); chain integrity verified; tenant isolation enforced

---

## Executive Summary

Layer Forge produces versioned **configuration artifacts** (Layer-Definitions) with quality gates and enforcement rules. This plan covers:

- **30+ concrete E2E tests** organized by lifecycle phase
- **Test matrix:** CLI vs Console vs Orchestrator
- **Fixture strategy:** Sandboxed CORVIN_HOME + tenant-scoped audit chain
- **Compliance checklist:** GDPR Art. 5/30/32, EU AI Act Art. 50, tenant isolation, PII containment
- **Blockers & dependencies:** Enforcement rule execution, LLM planning phase, frontend panel
- **Coverage gaps:** Error recovery, race conditions, cross-tenant leakage, edge cases

---

## Test Categories & Count

### Phase 1: Validation (4 tests)

1. **test_schema_valid_manifest_accepted** — Well-formed manifest passes all schema checks
2. **test_schema_invalid_id_pattern_rejected** — Manifest with `id` violating regex pattern is rejected
3. **test_schema_invalid_version_semver_rejected** — Non-semver `version` is rejected
4. **test_schema_dependency_dag_cycle_detected** — Circular dependencies are rejected before any state change

**Criticality:** CRITICAL — all paths downstream depend on valid schema

---

### Phase 2: Quality Gate Testing (7 tests)

5. **test_gate_pytest_passes_verdict_pass** — Gate runs pytest subprocess, captures PASS
6. **test_gate_pytest_fails_verdict_fail** — Gate captures pytest FAIL, orchestrator rejects
7. **test_gate_pytest_timeout_verdict_error** — pytest exceeds 60s timeout → ERROR status
8. **test_gate_pytest_exit_2_no_tests_collected_verdict_error** — pytest exit 2 (no tests) → ERROR, never PASS
9. **test_gate_pytest_not_installed_verdict_error** — Missing pytest in interpreter → ERROR
10. **test_gate_unsafe_test_path_traversal_rejected** — test_path with `..` traversal rejected pre-subprocess
11. **test_gate_multiple_gates_all_run_fail_on_first_failure** — Multiple gates, fail-closed on first failure

**Criticality:** CRITICAL — gates are the primary validation lever

---

### Phase 3: Enforcement Checking (5 tests)

12. **test_enforcement_host_awareness_skip_no_runtime_install** — No `/opt/corvin` → `SKIPPED` status (not silent PASS)
13. **test_enforcement_host_awareness_sha256_match_success** — Source & runtime hashes match → `PASS`
14. **test_enforcement_host_awareness_sha256_mismatch_fail** — Hash mismatch → FAIL, reject definition
15. **test_enforcement_compile_time_rule_recorded_not_executed** — Compile-time rules stored, NOT run (deferred)
16. **test_enforcement_boot_time_rule_recorded_not_executed** — Boot-time rules stored, NOT run (deferred)

**Criticality:** HIGH — enforcement framework in place; execution deferred per ADR-2222 Consequences

---

### Phase 4: Create (Audit-First) (6 tests)

17. **test_create_audit_record_written_before_registry_entry** — Audit event commits, then registry write (or abort)
18. **test_create_audit_chain_write_fails_operation_aborted** — Chain write fails → LayerForgeAuditError, no registry entry
19. **test_create_rejected_manifest_writes_definition_rejected_event** — Rejection at any phase → `layer_forge.definition_rejected` audit event
20. **test_create_success_writes_definition_proposed_event** — Success → `layer_forge.definition_proposed` with counts (gates, rules, layers)
21. **test_create_version_already_exists_409_conflict** — Duplicate (id, version) → 409 HTTP or error response
22. **test_create_lock_file_prevents_concurrent_writes** — Two create() calls on same id → second waits for lock

**Criticality:** CRITICAL — audit-first is load-bearing for compliance

---

### Phase 5: Promotion (Status Transitions) (6 tests)

23. **test_promote_proposed_to_accepted_success** — `proposed → accepted` transition allowed, recorded
24. **test_promote_accepted_to_deployed_success** — `accepted → deployed` transition allowed, recorded
25. **test_promote_deployed_to_superseded_success** — `deployed → superseded` terminal transition
26. **test_promote_superseded_rejects_any_transition** — Superseded entries locked to terminal state
27. **test_promote_accepted_back_to_proposed_allowed** — `accepted → proposed` downgrade allowed (revert capability)
28. **test_promote_illegal_transition_rejected_409** — e.g., `proposed → deployed` (skip accepted) → 409

**Criticality:** CRITICAL — lifecycle model enforced

---

### Phase 6: CLI E2E (4 tests)

29. **test_cli_create_manifest_success_exit_0** — `layer_forge_cli.py create manifest.json` → exit 0, stdout JSON
30. **test_cli_list_returns_all_registered_entries** — `layer_forge_cli.py list` → JSON array of all entries across versions
31. **test_cli_get_entry_by_id_and_version** — `layer_forge_cli.py get <id> --version <v>` returns entry details
32. **test_cli_promote_transitions_status** — `layer_forge_cli.py promote <id> <v> <status>` transitions and audits

**Criticality:** HIGH — CLI is primary operator interface

---

### Phase 7: Console Routes E2E (5 tests)

33. **test_console_get_definitions_list_empty_or_populated** — `GET /v1/console/layer-forge/definitions` → {items, count}
34. **test_console_get_definition_by_id_404_if_unknown** — `GET /v1/console/layer-forge/definitions/{id}` returns entry or 404
35. **test_console_post_create_manifest_200_or_422** — `POST /v1/console/layer-forge/definitions` → 200 SUCCESS or 422 (phase named)
36. **test_console_post_transition_status_200_or_409** — `POST .../transition {to_status}` → 200 or 409 (illegal transition)
37. **test_console_csrf_protection_enforced** — Missing or invalid X-CSRF-Token → 403 on mutations

**Criticality:** CRITICAL — console is primary UI entry point

---

### Phase 8: Audit Chain Integrity (5 tests)

38. **test_audit_events_written_to_tenant_audit_chain** — All layer_forge.* events land in tenant's hash-chained audit.jsonl
39. **test_audit_chain_hash_links_unbroken** — Run `verify_chain` on audit.jsonl after each operation
40. **test_audit_definition_rejected_event_emitted_only_on_failure** — Rejection at validate/test/enforce → event with phase named
41. **test_audit_quality_gate_evaluated_one_event_per_gate** — Multiple gates → N events, one per gate (status captured)
42. **test_audit_allowed_fields_only_no_secret_leakage** — Event payloads strictly match ALLOWED_FIELDS; no manifest bodies, exception messages

**Criticality:** CRITICAL — audit trail is GDPR/compliance foundation

---

### Phase 9: Tenant Isolation (4 tests)

43. **test_tenant_isolation_registry_separate_per_tenant** — Tenant A's entries never visible to tenant B
44. **test_tenant_isolation_audit_chain_separate_per_tenant** — Tenant A's chain never mixed with tenant B's
45. **test_tenant_isolation_cross_tenant_lookup_returns_empty** — `get(id, version, tenant=B)` returns 404 even if id exists in tenant A
46. **test_tenant_isolation_environment_fallback_corvin_tenant_id** — `$CORVIN_TENANT_ID` env var selects tenant; default is `_default`

**Criticality:** CRITICAL — GDPR Art. 5 / Data Isolation

---

### Phase 10: Error Handling & Edge Cases (6 tests)

47. **test_manifest_invalid_json_rejected_gracefully** — Malformed JSON → 422 with "schema" phase, no crash
48. **test_manifest_missing_required_field_rejected** — Manifest missing "id" or "version" → validation error
49. **test_test_path_outside_tests_directory_rejected** — `test_path: "/etc/passwd"` → rejected, symlink resolution checked
50. **test_gate_crash_doesnt_crash_orchestrator** — pytest subprocess crash (segfault, etc.) → ERROR verdict, orchestrator continues
51. **test_large_manifest_handles_many_gates_and_rules** — 50+ gates + 30+ rules: no timeout, all tested
52. **test_registry_corruption_detected_and_reported** — Malformed registry JSON → error on read, error on write returns appropriate message

**Criticality:** HIGH — robustness under failure

---

### Phase 11: Concurrency & Locking (3 tests)

53. **test_concurrent_creates_same_id_second_waits_for_lock** — Two processes create same id → second waits, gets lock after first commits
54. **test_concurrent_promotes_same_entry_serialized** — Two threads promote same entry → transitions applied serially per lock
55. **test_lock_file_is_removed_after_operation** — Lock file in `layer_forge/locks/` is cleaned up after operation

**Criticality:** HIGH — prevents registry corruption

---

### Phase 12: Compliance & Security (5 tests)

56. **test_pii_never_in_audit_events** — Scrub check: no user_id, email, phone, PII patterns in event payloads
57. **test_actor_field_distinguishes_cli_vs_console** — CLI events have `actor: "cli"`, console have `actor: "console"` (audit attribution)
58. **test_audit_field_allowlist_mismatch_prevents_write** — Event with extra field outside ALLOWED_FIELDS → LayerForgeAuditError
59. **test_enforcement_rules_not_executed_until_explicitly_enabled** — Rules stored but not run; no silent activation
60. **test_definition_manifest_not_stored_in_audit** — Audit events include only metadata (id, version, counts), never full manifest

**Criticality:** CRITICAL — compliance-enforcing

---

### Phase 13: Fixtures & Test Infrastructure (implicit, tested via all above)

| Fixture | Purpose | Scope |
|---------|---------|-------|
| `tmp_corvin_home` | Sandboxed `CORVIN_HOME` per test | Test isolation |
| `tenant_audit_chain` | Verify hash-chain integrity after each test | Compliance |
| `layer_forge_manifest` | Factory for valid/invalid manifests | Parameterized tests |
| `cli_subprocess` | Run layer_forge_cli.py as subprocess | E2E CLI testing |
| `console_http_client` | FastAPI TestClient w/ CSRF + session | E2E console testing |
| `registry_state` | Inspect registry files on disk | Verification |

---

## Test Matrix: CLI vs Console vs Orchestrator

| Operation | CLI | Console | Orchestrator | Audit |
|---|---|---|---|---|
| **Create** | `layer_forge_cli.py create` | `POST /v1/console/definitions` | `orch.create_layer_definition()` | `layer_forge.definition_proposed` |
| **Get** | `layer_forge_cli.py get` | `GET /v1/console/definitions/{id}` | `orch.get(id, version)` | None (read-only) |
| **List** | `layer_forge_cli.py list` | `GET /v1/console/definitions` | `orch.list_definitions()` | None (read-only) |
| **Promote** | `layer_forge_cli.py promote` | `POST .../transition` | `orch.promote()` | `layer_forge.definition_transitioned` |

**Coverage Strategy:**
- 3–4 tests per operation (CLI + console + orchestrator direct)
- Console tests MUST verify CSRF, session auth, audit events
- CLI tests MUST verify exit codes + stdout/stderr formatting
- Orchestrator tests are internal (unit-style but called via subprocess to prove wiring)

---

## Fixtures & Test Infrastructure

### Fixture: `tmp_corvin_home`
```python
@pytest.fixture
def tmp_corvin_home(tmp_path):
    home = tmp_path / "corvin_home"
    tid = "_default"
    (home / "tenants" / tid / "global" / "layer_forge" / "registry").mkdir(parents=True)
    (home / "tenants" / tid / "global" / "layer_forge" / "locks").mkdir(parents=True)
    (home / "tenants" / tid / "global" / "forge").mkdir(parents=True)
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tid
    yield home
    # Cleanup in conftest via `tmp_path` teardown
```

### Fixture: `tenant_audit_chain`
```python
@pytest.fixture
def verify_chain(tmp_corvin_home):
    """Verify hash-chain integrity after test."""
    def _verify():
        chain = tmp_corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        if not chain.exists():
            return []
        events = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        # Verify prev_hash chain
        for i, ev in enumerate(events[1:], 1):
            expected_prev = events[i-1].get("hash")
            actual_prev = ev.get("prev_hash")
            assert actual_prev == expected_prev, f"Chain break at event {i}"
        return events
    yield _verify
    _verify()  # Verify at teardown
```

### Fixture: `layer_forge_manifest_factory`
```python
@pytest.fixture
def layer_forge_manifest():
    """Factory for manifests with overridable fields."""
    def _make(id="test.entry", version="0.1.0", gates=None, enforce=None, **kw):
        m = {
            "id": id,
            "version": version,
            "targets": [{"layer_id": "L34"}],
            "quality_gates": gates or [{"gate_id": "schema", "test_path": "tests/layer_forge/test_schema.py"}],
            "enforcement_rules": enforce or [],
        }
        m.update(kw)
        return m
    return _make
```

### Fixture: `cli_runner`
```python
@pytest.fixture
def cli_runner(tmp_corvin_home, monkeypatch):
    """Run layer_forge_cli.py as subprocess."""
    repo_root = Path(__file__).parents[3]
    def _run(args, tenant="_default"):
        env = os.environ.copy()
        env["CORVIN_HOME"] = str(tmp_corvin_home)
        env["CORVIN_TENANT_ID"] = tenant
        result = subprocess.run(
            [sys.executable, str(repo_root / "scripts" / "layer_forge_cli.py")] + args,
            capture_output=True, text=True, env=env
        )
        return result
    return _run
```

### Fixture: `console_client`
```python
@pytest.fixture
def console_client(tmp_corvin_home):
    """FastAPI TestClient w/ CSRF + session."""
    os.environ["CORVIN_HOME"] = str(tmp_corvin_home)
    _reset_modules()
    from corvin_console import auth
    from corvin_console.app import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    rec = auth.create_session(tenant_id="_default", token_fingerprint="test")
    csrf = auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    
    yield client, csrf
    _reset_modules()
```

---

## Test Implementation Strategy

### Phase 1: Foundation (15 tests, Weeks 1–2)

**Tests 1–4 (Validation):**
- `test_schema_valid_manifest_accepted` → via `orch.create_layer_definition()`
- `test_schema_invalid_id_pattern_rejected` → manifest w/ id = "INVALID"
- `test_schema_invalid_version_semver_rejected` → version = "0.1"
- `test_schema_dependency_dag_cycle_detected` → dep: A→B, B→A

**Tests 5–11 (Quality Gates):**
- `test_gate_pytest_passes_verdict_pass` → gate runs passing test
- `test_gate_pytest_fails_verdict_fail` → gate runs failing test
- `test_gate_pytest_timeout_verdict_error` → timeout_s=1, slow test
- `test_gate_pytest_exit_2_no_tests_collected_verdict_error` → empty test file
- `test_gate_pytest_not_installed_verdict_error` → mock missing pytest
- `test_gate_unsafe_test_path_traversal_rejected` → test_path = "../../../etc/passwd"
- `test_gate_multiple_gates_all_run_fail_on_first_failure` → 3 gates, 2nd fails

**Tests 12–15 (Enforcement):**
- `test_enforcement_host_awareness_skip_no_runtime_install` → check SKIPPED status
- `test_enforcement_host_awareness_sha256_match_success` → hash match → PASS
- `test_enforcement_host_awareness_sha256_mismatch_fail` → mismatch → FAIL
- `test_enforcement_compile_time_rule_recorded_not_executed` → rule stored, not run

**Dependencies:** None (foundation); can run in parallel

---

### Phase 2: Audit & Create (10 tests, Weeks 2–3)

**Tests 17–22 (Audit-First Create):**
- `test_create_audit_record_written_before_registry_entry` → audit event, then registry
- `test_create_audit_chain_write_fails_operation_aborted` → mock chain failure → abort
- `test_create_rejected_manifest_writes_definition_rejected_event` → rejection event
- `test_create_success_writes_definition_proposed_event` → success event + counts
- `test_create_version_already_exists_409_conflict` → duplicate version
- `test_create_lock_file_prevents_concurrent_writes` → threading.Thread + lock

**Tests 38–42 (Audit Verification):**
- `test_audit_events_written_to_tenant_audit_chain` → check audit.jsonl
- `test_audit_chain_hash_links_unbroken` → verify hash chain
- `test_audit_definition_rejected_event_emitted_only_on_failure` → check event
- `test_audit_quality_gate_evaluated_one_event_per_gate` → N gates → N events
- `test_audit_allowed_fields_only_no_secret_leakage` → check ALLOWED_FIELDS

**Dependencies:** Phase 1; need working QualityGateRunner

---

### Phase 3: Promotion & Lifecycle (9 tests, Weeks 3–4)

**Tests 23–28 (Status Transitions):**
- `test_promote_proposed_to_accepted_success` → transition + event
- `test_promote_accepted_to_deployed_success` → transition + event
- `test_promote_deployed_to_superseded_success` → terminal transition
- `test_promote_superseded_rejects_any_transition` → locked state
- `test_promote_accepted_back_to_proposed_allowed` → downgrade allowed
- `test_promote_illegal_transition_rejected_409` → proposed → deployed (skip)

**Tests 43–45 (Tenant Isolation):**
- `test_tenant_isolation_registry_separate_per_tenant` → A/B separate
- `test_tenant_isolation_audit_chain_separate_per_tenant` → chains separate
- `test_tenant_isolation_cross_tenant_lookup_returns_empty` → B doesn't see A

**Tests 56–60 (Compliance):**
- `test_pii_never_in_audit_events` → scrub check
- `test_actor_field_distinguishes_cli_vs_console` → audit attribution
- `test_audit_field_allowlist_mismatch_prevents_write` → extra field rejected
- `test_enforcement_rules_not_executed_until_explicitly_enabled` → rules deferred
- `test_definition_manifest_not_stored_in_audit` → no manifest in events

**Dependencies:** Phase 2; need working audit

---

### Phase 4: CLI & Console (9 tests, Weeks 4–5)

**Tests 29–32 (CLI):**
- `test_cli_create_manifest_success_exit_0` → subprocess CLI
- `test_cli_list_returns_all_registered_entries` → list output
- `test_cli_get_entry_by_id_and_version` → get output
- `test_cli_promote_transitions_status` → promote + status change

**Tests 33–37 (Console):**
- `test_console_get_definitions_list_empty_or_populated` → GET list
- `test_console_get_definition_by_id_404_if_unknown` → GET + 404
- `test_console_post_create_manifest_200_or_422` → POST create
- `test_console_post_transition_status_200_or_409` → POST transition
- `test_console_csrf_protection_enforced` → CSRF gate

**Dependencies:** Phase 2–3; console/CLI wiring

---

### Phase 5: Edge Cases & Robustness (6 tests, Weeks 5–6)

**Tests 47–52 (Error Handling):**
- `test_manifest_invalid_json_rejected_gracefully` → JSON parse error
- `test_manifest_missing_required_field_rejected` → schema validation
- `test_test_path_outside_tests_directory_rejected` → path traversal guard
- `test_gate_crash_doesnt_crash_orchestrator` → pytest crash handling
- `test_large_manifest_handles_many_gates_and_rules` → 50+ gates
- `test_registry_corruption_detected_and_reported` → malformed registry

**Tests 53–55 (Concurrency):**
- `test_concurrent_creates_same_id_second_waits_for_lock` → locking
- `test_concurrent_promotes_same_entry_serialized` → serialization
- `test_lock_file_is_removed_after_operation` → cleanup

**Tests 46, 52 (Environment):**
- `test_tenant_isolation_environment_fallback_corvin_tenant_id` → env fallback
- `test_registry_corruption_detected_and_reported` → error reporting

**Dependencies:** Phase 4; full stack

---

## Compliance Checklist

### GDPR Article 5 (Accountability)
- [x] Every action audited (`layer_forge.*` events)
- [x] Audit trail preserved per tenant
- [x] No silent operations
- [ ] Right-to-erasure workflow (deferred, GAP)

**Test:** `test_audit_events_written_to_tenant_audit_chain` ✅

### GDPR Article 30 (Records of Processing)
- [x] Immutable audit trail
- [x] Hash-chained events
- [x] Timestamps on all events
- [x] Actor attribution (cli / console)

**Test:** `test_audit_chain_hash_links_unbroken` ✅

### GDPR Article 32 (Security)
- [x] Hash-chain integrity verification
- [x] Fail-closed on chain write failure
- [x] No silent data loss
- [x] PII scrubbing (`_assert_safe` backstop, TBD)

**Test:** `test_create_audit_chain_write_fails_operation_aborted` ✅

### EU AI Act Article 50 (Transparency)
- [x] Bot disclosure (handled by Layer 44 gate, out of scope for Layer Forge)
- [x] Decision attribution (actor field in audit)
- [x] Audit trail for operator inspection
- [ ] Dashboard for decision visibility (deferred, GAP)

**Test:** `test_actor_field_distinguishes_cli_vs_console` ✅

### Tenant Isolation (ADR-0007)
- [x] Separate registry per tenant
- [x] Separate audit chain per tenant
- [x] No cross-tenant leakage
- [x] Environment fallback to `_default`

**Test:** `test_tenant_isolation_*` (4 tests) ✅

### PII Containment
- [x] Audit event allowlist (no secrets, no user data)
- [x] Manifest bodies never logged
- [x] Exception messages not logged
- [x] Scrubbing at write time (fail-closed)

**Test:** `test_pii_never_in_audit_events` ✅ + `test_audit_allowed_fields_only_no_secret_leakage` ✅

---

## Coverage Gaps Identified

| Gap | Severity | Description | When Fixed |
|-----|----------|-------------|-----------|
| **Enforcement Rule Execution** | HIGH | Compile-time & boot-time rules stored but not executed (deferred per ADR-2222 Consequences) | ADR-2222 Phase 2 (ADR-2270 TBD) |
| **LLM Planning Phase** | HIGH | PLAN phase is deterministic (caller supplies manifest); LLM planning deferred | ADR-2222 Phase 2 (no ADR yet) |
| **Adversarial Review Phase** | HIGH | REVIEW phase (challenge decisions, surface risks) not built | ADR-2222 Phase 3 (no ADR yet) |
| **Console Frontend Panel** | MEDIUM | Routes are API-only; no UI in console yet | ADR-2222 Phase 2 (no ADR yet) |
| **Right-to-Erasure (Art. 17)** | MEDIUM | No workflow to GDPR-erase an entry from registry + chain | L36 (later, cross-system) |
| **Retention Policy** | LOW | No automatic cleanup of superseded entries (operator-manual today) | ADR-0319 / Retention policy TBD |

---

## Test Execution & CI/CD Integration

### Running All 60 Tests
```bash
# Local
pytest tests/layer_forge/ -v --tb=short

# With audit verification
pytest tests/layer_forge/ -v --tb=short --assert=verify_chain

# Concurrent E2E (8 workers, separate tmp homes)
pytest tests/layer_forge/ -v -n 8 --tb=short

# Filtered by phase
pytest tests/layer_forge/ -k "validate" -v
pytest tests/layer_forge/ -k "gate" -v
pytest tests/layer_forge/ -k "audit" -v
```

### CI/CD Gate (`.github/workflows/layer-forge-e2e.yml`)
```yaml
jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with: { python-version: "3.11" }
      - run: pip install -e . pytest pytest-xdist
      - run: pytest tests/layer_forge/ -v -n 4 --tb=short
      - if: failure()
        run: |
          # On failure, capture audit chain for debugging
          find $CORVIN_HOME/tenants/_default/global/forge -name audit.jsonl -exec cat {} \;
```

### Success Criteria
- All 60 tests pass
- Chain integrity verified on every test
- No cross-tenant leakage
- Latency: < 5s per test (< 300s total on 8 cores)
- Coverage: 100% of orchestrator.py, registry.py, audit.py paths

---

## Blockers & Dependencies

| Blocker | Status | ADR | Impact |
|---------|--------|-----|--------|
| **QualityGateRunner** | ✅ DONE | ADR-2222 D1 | Tests 5–11 depend on this |
| **EnforcementChecker** | ✅ DONE | ADR-2222 D1 | Tests 12–16 depend on this |
| **LayerPrimitive lock** | ✅ DONE | ADR-2222 D4 | Tests 53–55 depend on this |
| **Audit chain integration** | ✅ DONE | ADR-2222 D6 | Tests 38–42, all compliance tests depend on this |
| **Console routes** | ✅ DONE | ADR-2222 D5 | Tests 33–37 depend on this |
| **Enforcement rule execution** | ❌ NOT DONE | ADR-2270 (TBD) | Tests 15–16 marked FAIL if execution attempted; blocking skip until ADR-2270 |
| **LLM PLAN phase** | ❌ NOT DONE | ADR-2300 (TBD) | Deferred; manifest schema allows LLM output, no test yet |
| **Adversarial REVIEW phase** | ❌ NOT DONE | ADR-2301 (TBD) | Deferred; audit event schema ready, no orchestrator path yet |
| **Console frontend panel** | ❌ NOT DONE | ADR-2222 Phase 2 | API tests pass; UI tests deferred |
| **GDPR Art. 17 erasure flow** | ❌ NOT DONE | L36 (later) | Compliance test blocked; skipped with reason |

---

## Success Metrics

| Metric | Target | Status |
|--------|--------|--------|
| **Test Count** | 60 concrete E2E tests | In planning |
| **Phase Coverage** | 100% of validate→test→enforce→create→promote | Planned |
| **Audit Verification** | Chain integrity checked after every test | Designed |
| **Tenant Isolation** | 4 dedicated tests + implicit in all tests | Planned |
| **Compliance** | GDPR Art. 5/30/32 + EU AI Act 50 verified | Checklist created |
| **CLI/Console/Orch Parity** | All operations tested via all 3 entry points | Planned |
| **Error Path Coverage** | Happy + sad path for every major operation | 6 dedicated tests |
| **Concurrency Safety** | Locking & serialization verified | 3 dedicated tests |
| **Latency** | < 5s per test, < 300s total on 8 cores | Target TBD (measure locally first) |

---

## Implementation Order (Recommended)

1. **Week 1–2:** Foundation (validation, gates, enforcement, initial fixtures)
   - Tests 1–15 (foundation)
   - Create `conftest.py` w/ `tmp_corvin_home`, `cli_runner`, fixtures

2. **Week 2–3:** Audit & Create
   - Tests 17–22, 38–42 (audit-first create, verification)
   - Prove chain integrity on every test

3. **Week 3–4:** Promotion & Compliance
   - Tests 23–28 (lifecycle)
   - Tests 43–45 (tenant isolation)
   - Tests 56–60 (compliance)

4. **Week 4–5:** CLI & Console
   - Tests 29–32 (CLI)
   - Tests 33–37 (console routes)
   - Verify CSRF, auth, audit attribution

5. **Week 5–6:** Edge Cases & Robustness
   - Tests 47–52 (error handling)
   - Tests 53–55 (concurrency)
   - Final audits & edge cases

6. **Week 6+:** CI/CD Integration & Documentation
   - Add GitHub Actions workflow
   - Document test running, coverage report
   - Add to CONTRIBUTING.md

---

## Test File Structure

```
tests/layer_forge/
├── conftest.py                           # Fixtures (tmp_corvin_home, cli_runner, etc.)
├── test_schema_validation.py             # Tests 1–4 (validation phase)
├── test_quality_gates.py                 # Tests 5–11 (gate phase)
├── test_enforcement_checking.py          # Tests 12–16 (enforcement phase)
├── test_create_audit_first.py            # Tests 17–22 (create + audit)
├── test_status_transitions.py            # Tests 23–28 (promotion phase)
├── test_cli_e2e.py                       # Tests 29–32 (CLI subprocess)
├── test_console_routes_e2e.py            # Tests 33–37 (HTTP routes) [already exists]
├── test_audit_chain_integrity.py         # Tests 38–42 (chain verification)
├── test_tenant_isolation.py              # Tests 43–45 (multi-tenant)
├── test_error_handling.py                # Tests 47–52 (edge cases)
├── test_concurrency_safety.py            # Tests 53–55 (locking)
├── test_compliance_and_security.py       # Tests 56–60 (GDPR/compliance)
└── fixtures/
    └── manifests.json                    # Sample manifests for parameterized tests
```

---

## Appendix: Test Template

```python
"""
Layer Forge E2E test — validate audit-first lifecycle.

Phase: CREATE (audit)
Criticality: CRITICAL
Dependencies: QualityGateRunner, tenant_audit_chain fixture
"""
import json
from pathlib import Path

def test_create_audit_record_written_before_registry_entry(tmp_corvin_home, cli_runner, verify_chain):
    """Audit event is written to chain BEFORE registry entry is created.
    
    If chain write fails, no registry entry exists. Proves audit-first.
    """
    manifest = {"id": "test.audit", "version": "0.1.0", "targets": [{"layer_id": "L34"}]}
    result = cli_runner(["create", "-"])  # stdin: manifest JSON
    
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    body = json.loads(result.stdout)
    assert body["status"] == "SUCCESS"
    
    # Check audit event
    events = verify_chain()
    proposed = [e for e in events if e["event_type"] == "layer_forge.definition_proposed"]
    assert len(proposed) == 1
    assert proposed[0]["details"]["entry_id"] == "test.audit"
    assert proposed[0]["details"]["actor"] == "cli"
    
    # Check registry entry exists
    registry = tmp_corvin_home / "tenants" / "_default" / "global" / "layer_forge" / "registry"
    assert (registry / "test.audit@0.1.0.json").exists()
```

---

## Summary

**60 E2E tests organized across 13 phases**, covering:
- ✅ **Validation:** 4 tests (schema, DAG, version)
- ✅ **Quality Gates:** 7 tests (pass/fail/error, timeout, traversal)
- ✅ **Enforcement:** 5 tests (host-awareness, rule storage)
- ✅ **Create (Audit-First):** 6 tests (audit precedence, chain failure, lock)
- ✅ **Promotion:** 6 tests (transitions, terminal state, downgrade)
- ✅ **CLI:** 4 tests (subprocess, exit codes, JSON output)
- ✅ **Console:** 5 tests (routes, CSRF, auth)
- ✅ **Audit Integrity:** 5 tests (chain links, events, allowlist)
- ✅ **Tenant Isolation:** 4 tests (separate registry, chains, environment)
- ✅ **Error Handling:** 6 tests (validation, crashes, corruption)
- ✅ **Concurrency:** 3 tests (locking, serialization)
- ✅ **Compliance:** 5 tests (PII, attribution, field validation)

**Compliance baseline:** GDPR Art. 5/30/32, EU AI Act Art. 50, tenant isolation  
**Blockers:** Enforcement execution, LLM planning, review phase (all deferred per ADR-2222)  
**Ready for implementation:** Week 1–6 roadmap, fixtures designed, success criteria defined
