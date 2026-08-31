# Graph Engineering Security Hardening (k=5)

**Status:** Phase 2 k=5 Complete (ADR-0268 + Security audit)  
**Date:** 2026-08-23  
**Scope:** Security validation for graph routing and task analysis

---

## Security Audit Results

### OWASP Top 10 Assessment

| Risk | Category | Status | Mitigation |
|---|---|---|---|
| **Path Traversal** | Injection | 🟢 FIXED | validate_path() — blocks `../`, absolute paths |
| **Command Injection** | Injection | 🟢 MITIGATED | Path validation before subprocess |
| **Secret Exposure** | Data | 🟡 DESIGN GUARD | Don't inject memory files with secrets |
| **DoS (Long Input)** | Availability | 🟢 FIXED | validate_task_input() — max 10K chars |
| **XSS** | Web | 🟢 N/A | Backend-only, no browser rendering |
| **Authentication** | Access | 🟢 N/A | Confidence gate is control, not auth |
| **XML Injection** | Parsing | 🟢 N/A | No XML parsing in graph engineering |
| **Deserialization** | Data | 🟢 N/A | Dataclasses + JSON only |
| **Known Vulns** | Deps | 🟢 SAFE | Stdlib only (ast, importlib, pathlib) |
| **Insufficient Logging** | Ops | 🟡 DESIGN | audit.jsonl logs routing, monitoring TBD |

### Priority 1 Fixes (Implemented in k=5)

1. **Path Validation** ✅
   - Function: `validate_path(file_path, repo_root) → bool`
   - Checks: no absolute paths, no `..` traversal, must resolve within repo
   - Applied: CallGraphRouter.route() validates all components
   - Test coverage: TestPathValidation (7 test cases)

2. **Input Validation** ✅
   - Function: `validate_task_input(task_description) → bool`
   - Checks: max 10K chars, no control characters (except newline/tab)
   - Test coverage: TestInputValidation (8 test cases)

3. **Subprocess Safety** ✅
   - Import: `shlex` (for future quote escaping)
   - Applied: CallGraphRouter validates paths before grep
   - Defense-in-depth: subprocess.run() uses list args (not shell=True)

### Priority 2 Guards (Design)

1. **Secret Detection** (Deferred to Phase 3)
   - Don't auto-inject memory files containing API keys, passwords
   - Implement secret detector (AWS/Azure key regex) in normalizer.py

2. **CEL Validation** (Documented)
   - Context Engineering Layer (Phase 5.5) must validate memory context
   - Document requirement in ADR-0268

### Priority 3 (Phase 3+)

1. **Anomaly Detection** — Monitor for consistently low confidence scores
2. **Monitoring Dashboard** — Visualize audit trail for security events
3. **Rate Limiting** — Protect against high-frequency task submission

---

## Security Test Coverage

**File:** `operator/task_analysis/tests/test_security_validation.py` (250+ LoC)

### Test Classes

| Class | Tests | Coverage |
|---|---|---|
| TestPathValidation | 7 | All validation paths |
| TestInputValidation | 8 | Length + control char checks |
| TestSecurityEdgeCases | 3 | Unicode, symlinks, filename tricks |

### Test Matrix

```
Path Validation:
  ✅ Valid: core/voice/renderer.py
  ✅ Valid: operator/task_analysis/normalizer.py
  ❌ Absolute: /etc/passwd
  ❌ Traversal: ../../../etc/passwd
  ❌ Current dir: ./core/voice.py
  ❌ Empty string
  ❌ Non-string

Input Validation:
  ✅ Valid: "Fix bug in voice module" (normal)
  ✅ Valid: "A" * 10000 (boundary)
  ✅ Valid: multiline with \n\t
  ❌ Too long: "A" * 10001 (>10K)
  ❌ Control char: \x00, \x07, \x1f
  ❌ Empty string
  ❌ Non-string
```

---

## Production Readiness Checklist

### Code Quality

- [x] Tier 0: Context read (ADR-0267, ADR-0267-MVP, CLAUDE.md)
- [x] Tier 1: Lint/syntax (py_compile passed)
- [x] Tier 2: Unit tests (291 existing + 18 security tests)
- [x] Tier 3: Integration tests (test_engine.py covers full pipeline)
- [x] Tier 4: E2E tests (test_e2e_graph_validation.py, 9 cases)

### Security

- [x] P1 fixes applied (path validation, input validation)
- [x] P1 tests added (test_security_validation.py, 18 cases)
- [x] P2 mitigations designed (CEL validation, secret detection)
- [x] Security audit documented (OWASP checklist)

### Documentation

- [x] ADR-0268 written (E2E validation + performance SLO)
- [x] Security guide written (this document)
- [x] Inline code comments (validate_path, validate_task_input)
- [x] Test docstrings (Tier 2 security tests)

### Performance

- [x] Latency SLO: <500ms per task (measured in k=4)
- [x] Memory SLO: <100MB per task (baseline)
- [x] Determinism: same input → same output (verified)
- [x] Error handling: graceful degradation (no crashes on syntax error)

### Operations

- [x] Logging: structured (audit.jsonl), phase context (engine.py)
- [x] Monitoring: metrics available (TaskMetrics, phase timing)
- [x] Feature flag: `spec.features.task_analysis_phase0_1` (defaults false)
- [x] Rollback: disable flag → uses Tier 2+3 only (no data loss)

---

## Known Limitations & Future Work

| Item | Status | Timeline |
|---|---|---|
| **Secret Detection** | Designed, not implemented | Phase 3 |
| **Anomaly Detection** | Designed, not implemented | Phase 3 |
| **Large-file Optimization** | Tested (<1MB), not optimized | Phase 4 |
| **Parallel Call-Graph** | Sequential only | Phase 4 |
| **Real CorvinOS E2E** | Tested on synthetic files | Phase 3+ |

---

## Security Boundary

**In Scope (Graph Engineering):**
- Task description parsing (normalized → type/components/severity)
- Component → file path routing (with validation)
- Call-graph routing (with error handling)
- Confidence scoring (deterministic)

**Out of Scope (other layers):**
- User authentication (delegated to L18+)
- Secrets management (delegated to vault)
- Audit chain integrity (delegated to L16)
- Memory injection (delegated to CEL, Phase 5.5)

---

## Deployment Notes

### Pre-Deployment Verification

1. Run security tests:
   ```bash
   pytest operator/task_analysis/tests/test_security_validation.py -v
   ```

2. Check coverage:
   ```bash
   pytest operator/task_analysis/tests/ --cov=operator/task_analysis --cov-report=term
   ```

3. Verify audit logging:
   ```bash
   tail -f ~/.corvin/audit.jsonl | grep "task_analysis"
   ```

### Production Monitoring

**Metrics to watch:**
- Task analysis latency (should stay <500ms)
- Confidence score distribution (should favor 0.0-1.0 range)
- Error rate (should be <1%)
- Path validation rejections (security signal)

**Alert thresholds:**
- Latency spike: >1s (investigate subprocess timeout)
- Confidence clustering: all 0.0 or all 1.0 (gate malfunction)
- Validation rejections: >10/hour (possible attack)

---

## Compliance

**GDPR:** Task analysis metadata only (no PII/prompts stored)  
**EU AI Act:** Confidence gate documented + audit-logged (Art. 50, 52)  
**Apache-2.0:** All code licensed, CLA signed

---

**Next Phase:** Phase 2 k=5 complete. Phase 3 begins (Routing Refinement + Memory Injection + ACS Delegation).
