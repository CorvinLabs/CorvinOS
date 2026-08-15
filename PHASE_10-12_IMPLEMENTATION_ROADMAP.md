# Phases 10-12 Implementation Roadmap

**Status:** Phase 11 complete (bugs fixed, tests green), Phases 10-12 planned  
**Date:** 2026-08-15  
**Quality:** Phase 11 K=1 ✓, Phases 10-12 ready for implementation

---

## Phase 11: Dual-Gate Pipeline (ADR-0300) — COMPLETE ✅

**Bugs Fixed (K=1):**
1. `AuditEntry.__init__()` missing `tenant_id` parameter
   - Impact: All 34 unit tests failed with TypeError
   - Fix: Pass `tenant_id` to AuditEntry constructor (line 420)
   - Commit: f487ecb

2. PII detection feature flag hardcoded to `True`
   - Impact: Feature flag `dual_gate_pii_detection_enabled` was ignored
   - Fix: Check feature_flags dict like validation/queue gates (line 155)
   - Commit: f487ecb

**Tests:** 64/64 green (34 unit + 14 E2E + 16 entry point wiring)  
**Quality:** Tier-2 ✓, K=1 ✓

---

## Phase 10: Input Validation Integration (ADR-0297 + ADR-0296)

### Scope
Wire Phase-9 validators (ADR-0296 ValidatorFactory) into real Flask routes, CLI commands, async handlers.

### Modules (4 total)
- `core/validation/route_validators.py` (250 lines)
  - `@validate_input` Flask decorator
  - Route parameter validation
  - Auto-rejects invalid input → 400 Bad Request, audited
  
- `core/validation/cli_validators.py` (180 lines)
  - `@click_validate` Click decorator for CLI commands
  - Argument validation before execution
  - Auto-rejects invalid args → exit code 1, audited

- `core/validation/async_validators.py` (200 lines)
  - Async task input validation
  - Compatible with asyncio.create_task()
  - Fail-closed: rejects before task submission

- `core/validation/integration.py` (150 lines)
  - Middleware registration (Flask + asyncio)
  - Test utilities for E2E
  - Tenant-scoped error responses (403, 400, 422)

### Tests (48 total)
| Category | Count | Examples |
|----------|-------|----------|
| Unit: Route validators | 15 | valid input, invalid input, tenant scope, error formatting |
| Unit: CLI validators | 12 | valid args, type coercion, missing required, error messages |
| Unit: Async validators | 8 | valid payload, invalid payload, tenant isolation |
| Unit: Middleware | 5 | error response, audit trail, feature flag gating |
| E2E: Flask routes | 5 | GET, POST, PUT, DELETE with real Flask TestClient |
| E2E: CLI commands | 3 | Real Click command execution, captured output |

### Entry Points to Wire
**Flask routes (10+):**
- `GET /api/users/<user_id>` — path parameter validation
- `POST /api/users` — JSON body validation
- `GET /features/<flag_id>` — query parameter validation
- etc.

**CLI commands (8+):**
- `corvin config set <key> <value>` — key/value validation
- `corvin sync-config` — config structure validation
- etc.

**Async tasks (5+):**
- Background sync operations
- Bulk imports
- etc.

### Integration Points
- DualGatePipeline.execute_guarded() — validation happens in Gate 2a
- CapabilityRegistry — used for capability checks (Gate 1)
- PIIDetector — used for PII checks (Gate 2b)
- AuditChain — failures logged (Gate 3)

### Timeline
- Implementation: 90 min
- Tests (tier 1-3): 45 min
- E2E tests: 15 min
- K=1 adversarial review: 20 min
- Total K-cycle: 2-3 iterations, converge to K=0

---

## Phase 12: Infrastructure Hardening (ADR-0334-0341)

### Scope
Implement 7 infrastructure protection layers with fail-closed contracts.

### ADRs to Create (7 total)
| ADR | Title | Scope | Effort |
|-----|-------|-------|--------|
| 0334 | Boot Verification | Audit tripwire on startup | 1h |
| 0335 | Data Classification | L34 fail-closed gate | 1h |
| 0336 | Compartmentalization | 3-tier boundary enforcement | 1.5h |
| 0337 | Module Contracts | Validation on load | 1h |
| 0338 | Self-Healing | Non-blocking fire-and-forget recovery | 1.5h |
| 0339 | Subprocess Isolation | Enforcement boundaries | 1h |
| 0341 | Operator Dashboard | Read-only health monitoring | 1h |

### Modules (7 total, 1 per ADR)
| Module | Lines | Key Classes | Tests |
|--------|-------|------------|-------|
| `core/infrastructure/boot_verification.py` | 200 | BootVerifier, BootState | 10 unit + 2 E2E |
| `core/infrastructure/data_classification.py` | 250 | DataClassifier, ClassificationLevel | 12 unit + 2 E2E |
| `core/infrastructure/compartmentalization.py` | 300 | CompartmentBoundary, TierValidator | 14 unit + 2 E2E |
| `core/infrastructure/module_contracts.py` | 200 | ModuleContract, ContractValidator | 10 unit + 2 E2E |
| `core/infrastructure/self_healing.py` | 280 | SelfHealingLoop, RecoveryStrategy | 12 unit + 2 E2E |
| `core/infrastructure/subprocess_isolation.py` | 220 | SubprocessBoundary, IsolationPolicy | 10 unit + 2 E2E |
| `core/infrastructure/operator_dashboard.py` | 180 | OperatorDashboard, HealthWidget | 12 unit + 0 E2E |

### Tests (72 total)
| Category | Count | Examples |
|----------|-------|----------|
| Boot verification unit | 10 | tripwire fires, chain verifies, graceful shutdown |
| Data classification unit | 12 | levels assigned, data flows tracked, leaks blocked |
| Compartmentalization unit | 14 | 3 tiers enforced, cross-tier calls blocked, audit trail |
| Module contracts unit | 10 | contracts validated, load-time checks, invalid modules rejected |
| Self-healing unit | 12 | recovery triggered, state restored, idempotent |
| Subprocess isolation unit | 10 | boundaries enforced, IPC restricted, crashes isolated |
| Dashboard unit | 12 | widgets rendered, health calculated, tenant scoped |
| E2E: Boot sequence | 3 | real boot, chain verification, operator notification |
| E2E: Data flows | 3 | sensitive data tracked, leaks blocked, audit trail |
| E2E: Subprocess | 3 | real subprocess, isolation boundary, resource limits |
| E2E: Recovery | 3 | self-healing triggered, service recovery, no data loss |

### Fail-Closed Contracts
All 7 layers enforce immutable fail-closed semantics:
- **Boot:** Audit chain verification before accepting requests
- **Data:** Classification errors → request rejection (403 Forbidden)
- **Compartmentalization:** Cross-tier calls → immediate rejection (403)
- **Contracts:** Invalid module → load failure (crash on boot)
- **Self-Healing:** Recovery fails → incident reporting (never silent)
- **Subprocess:** Isolation violation → termination (no fallback)
- **Dashboard:** Data error → read-only null response (never propagate)

### Compliance Binding
- **GDPR Art. 32:** Data protection by design (all 7 layers)
- **GDPR Art. 30:** Immutable audit trail (boot + all decisions logged)
- **EU AI Act Art. 50:** Disclosure integrity (dashboard shows real state)

### Timeline
- ADR design: 60 min
- Implementation: 120 min (each module ~17 min avg)
- Tests (tier 1-4): 60 min
- K=1 adversarial review: 30 min
- Total K-cycle: 4-5 iterations, converge to K=0

### E2E Integration
- Real boot sequence (not mocked)
- Real data flows (not mocked classifiers)
- Real subprocess isolation (not mocked process manager)
- Audit trail verification (hash-chain integrity)

---

## Quality Gates (All Phases)

### Per-Phase Convergence
| Gate | Phase 11 | Phase 10 | Phase 12 |
|------|----------|----------|----------|
| Tier 1: Lint + type | ✅ | 🔲 | 🔲 |
| Tier 2: Unit tests | ✅ | 🔲 | 🔲 |
| Tier 3: Integration | ✅ | 🔲 | 🔲 |
| Tier 4: E2E | ✅ | 🔲 | 🔲 |
| K=1 adversarial | ✅ | 🔲 | 🔲 |
| K=2-5 convergence | ✅ | 🔲 | 🔲 |

### Cross-Phase Review (After all 3 complete)
1. Full adversarial review of 15 modules (K=1)
2. All findings fixed (K=2-K=5)
3. docs-as-definition-of-done (update ADRs + diagrams)
4. One commit per phase + final cross-phase commit

---

## Implementation Checklist (Next Agent/Session)

### Phase 10 (2 hours)
- [ ] Create `core/validation/` package
- [ ] Implement `route_validators.py` + 15 unit tests
- [ ] Implement `cli_validators.py` + 12 unit tests
- [ ] Implement `async_validators.py` + 8 unit tests
- [ ] Implement `integration.py` + 5 unit tests
- [ ] Create E2E tests (Flask + CLI): 8 tests
- [ ] Lint + type check (Tier 1) ✓
- [ ] Run unit tests (Tier 2) ✓
- [ ] Run integration tests (Tier 3) ✓
- [ ] Run E2E tests (Tier 4) ✓
- [ ] K=1 adversarial review
- [ ] Fix findings, K=2-5 convergence
- [ ] Commit with message "feat(phase10): input validation integration"

### Phase 12 (3 hours)
- [ ] Create ADR-0334-0341 (7 ADRs)
- [ ] Create `core/infrastructure/` package
- [ ] Implement boot_verification.py + 10 unit + 2 E2E tests
- [ ] Implement data_classification.py + 12 unit + 2 E2E tests
- [ ] Implement compartmentalization.py + 14 unit + 2 E2E tests
- [ ] Implement module_contracts.py + 10 unit + 2 E2E tests
- [ ] Implement self_healing.py + 12 unit + 2 E2E tests
- [ ] Implement subprocess_isolation.py + 10 unit + 2 E2E tests
- [ ] Implement operator_dashboard.py + 12 unit tests
- [ ] All tests tier 1-4 ✓
- [ ] K=1 adversarial review (all 7 modules)
- [ ] Fix findings, K=2-5 convergence
- [ ] Commit with message "feat(phase12): infrastructure hardening (ADR-0334-0341)"

### Cross-Phase (1 hour)
- [ ] Full adversarial review of all 15 modules (Phase 10 + 11 + 12)
- [ ] docs-as-definition-of-done: update all ADRs + diagrams
- [ ] Final cross-phase commit
- [ ] Mark all phases complete in MEMORY.md

---

## Success Criteria

**Phase 10 Complete:**
- 48 tests green (40 unit + 8 E2E)
- All validators wired into real entry points
- K=0 findings after adversarial review
- ADR-0297 implementation summary updated

**Phase 12 Complete:**
- 7 ADRs in Corvin-ADR with full frontmatter
- 7 modules, 72 tests green (60 unit + 12 E2E)
- All fail-closed contracts enforced
- K=0 findings after adversarial review
- GDPR + EU AI Act compliance verified

**All Phases (15 modules, 175+ tests):**
- 100% test pass rate
- K=0 findings (5-round adversarial review complete)
- Committed to main
- Ready for 0.12.x release candidate

---

**Prepared by:** Claude Code Agent (Haiku 4.5)  
**Date:** 2026-08-15  
**Next Steps:** Proceed with Phase 10 implementation when bandwidth available
