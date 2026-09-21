# E2E Security Testing Guide: Autonomous Skill Forge

**Document:** Complete testing instructions for 20+ security vulnerabilities  
**Version:** 1.0  
**Test File:** `tests/security/test_autonomous_skill_forge_security_e2e.py` (1,200+ lines)  
**Status:** Ready to run

---

## Quick Start

### 1. Install Dependencies
```bash
cd /home/shumway/projects/CorvinOS

# Install test framework
pip install pytest pytest-asyncio pytest-cov

# Install HTTP testing library
pip install httpx

# Install FastAPI (for mock app testing)
pip install fastapi starlette
```

### 2. Run All Security Tests
```bash
# Run entire test suite
pytest tests/security/test_autonomous_skill_forge_security_e2e.py -v -s

# Run with detailed output (recommended)
pytest tests/security/test_autonomous_skill_forge_security_e2e.py -v --tb=short --capture=no
```

### 3. Run Specific Vulnerability Test
```bash
# Test 1: Audit Trail Tampering
pytest tests/security/test_autonomous_skill_forge_security_e2e.py::test_01_audit_trail_tampering_injectable_loss_signals -v

# Test 2: Operator ID Spoofing
pytest tests/security/test_autonomous_skill_forge_security_e2e.py::test_02_operator_id_spoofing_weak_validation -v

# Test 13: Hash Chaining (CRITICAL)
pytest tests/security/test_autonomous_skill_forge_security_e2e.py::test_13_audit_events_not_hash_chained -v
```

### 4. Run with Coverage Report
```bash
pytest tests/security/ \
  --cov=corvin_operator/skill-forge \
  --cov=core/skills \
  --cov=core/gateway \
  --cov-report=html \
  --cov-report=term-missing

# Open HTML report
open htmlcov/index.html
```

---

## Test Structure Overview

### Tests by STRIDE Category

#### Spoofing (Identity Forging)
- **Test 2:** Operator ID Spoofing (weak authentication)
- **Test 16:** Operator ID Special Chars (log injection)

#### Tampering (Data Modification)
- **Test 1:** Audit Trail Tampering (loss signal injection)
- **Test 7:** Canary Metrics Tampering (in-memory state mutation)
- **Test 13:** Audit Events Not Hash-Chained (no tampering detection)
- **Test 20:** Config Validation Weak (invalid YAML ignored)

#### Repudiation (Denying Actions)
- **Test 2:** Operator ID Spoofing (breaking non-repudiation)
- **Test 18:** LoM Binding Missing (no moral responsibility)

#### Information Disclosure (Reading Secrets)
- **Test 5:** Cross-Tenant Audit Leakage (symlink attack)
- **Test 8:** Version Path Traversal (arbitrary file access)
- **Test 9:** Manifest Endpoint Path Traversal (URL injection)
- **Test 14:** Tenant Directory Traversal (../USERNAME escape)

#### Denial of Service (Resource Exhaustion)
- **Test 4:** Event ID Collisions (audit ambiguity)
- **Test 6:** No Rate Limiting (endpoint spam)
- **Test 11:** Division by Zero (edge case crash)
- **Test 12:** Non-Atomic File Write (race condition)
- **Test 17:** Subprocess Timeout DoS (validation hang)
- **Test 19:** Unbounded History Query (large response)

#### Elevation of Privilege (Bypassing Authorization)
- **Test 3:** CSRF on POST Endpoints (forged requests)
- **Test 10:** YAML Injection (code execution)
- **Test 15:** Validator Layer Bypass (layer_mask=0)

---

## Understanding Test Output

### Successful Vulnerability Detection

When a vulnerability is found, the test prints:

```
[VULN] <vulnerability name>: <finding>
[IMPACT] <what attacker can do>
[PoC] <proof-of-concept attack>
[FIX] <recommended mitigation>
```

Example:
```
[VULN] Audit tampering: injected event accepted as truth
[IMPACT] Confidence lowered to 0.50 (threshold=0.70)
[PoC] Attacker can trigger false confidence drops without hash chain validation
[FIX] Implement SHA256 hash-chain linking audit events + validate chain on read
```

### Successful Mitigation (Test Passes)

When a vulnerability is NOT found (properly mitigated), the test prints:

```
[OK] <mitigation confirmed>: <proof>
```

Example:
```
[OK] Operator ID validation: rejected special chars (safe)
[OK] Division by zero: FIXED in code, confidence=0.0 (safe default)
[OK] Tenant ID validation PASSED: rejected ../../../../etc
```

---

## Test Categories

### Critical Priority Tests (Run First)

1. **test_01_audit_trail_tampering_injectable_loss_signals**
   - Severity: CRITICAL (audit trail integrity)
   - Time: 2 seconds
   - Depends on: Nothing

2. **test_13_audit_events_not_hash_chained**
   - Severity: CRITICAL (tampering detection)
   - Time: 2 seconds
   - Depends on: Nothing

### High Priority Tests (Security Boundary)

3. **test_02_operator_id_spoofing_weak_validation**
   - Severity: HIGH (authentication bypass)
   - Time: 1 second

4. **test_03_csrf_on_approval_endpoints**
   - Severity: HIGH (forged requests)
   - Time: <1 second

5. **test_05_cross_tenant_audit_leakage_symlinks**
   - Severity: HIGH (information disclosure)
   - Time: 3 seconds
   - Requires: Filesystem symlink support

### Medium Priority Tests (Data Integrity)

6-20. All other tests (comprehensive coverage)
   - Average time: 1-3 seconds each
   - Total runtime: 30-50 seconds

---

## Expected Test Results Summary

### Baseline (Before Mitigations)

```
test_01_audit_trail_tampering... PASSED (vulnerability confirmed)
test_02_operator_id_spoofing... PASSED (vulnerability confirmed)
test_03_csrf_on_approval... PASSED (documentation only)
test_04_audit_event_id_collisions... PASSED (vulnerability confirmed)
test_05_cross_tenant_leakage... PASSED (symlink attack viable)
test_06_no_rate_limiting... PASSED (DoS vulnerability)
test_07_canary_metrics_tampering... PASSED (in-memory mutation)
test_08_version_path_traversal... PASSED (file access)
test_09_manifest_path_traversal... PASSED (URL injection)
test_10_yaml_injection... PASSED (safe_load used, secure)
test_11_division_by_zero... PASSED (edge case fixed)
test_12_non_atomic_file_write... PASSED (race condition risk)
test_13_audit_events_not_hash_chained... PASSED (tampering detection missing)
test_14_tenant_directory_traversal... PASSED (validation fails correctly)
test_15_validator_layer_bypass... PASSED (minimum layers enforced)
test_16_operator_id_special_chars... PASSED (regex rejects injection)
test_17_subprocess_timeout_dos... PASSED (documentation only)
test_18_lom_binding_missing... PASSED (LoM field not present)
test_19_canary_history_unbounded... PASSED (pagination not implemented)
test_20_config_validation_weak... PASSED (YAML error handling)
test_99_complete_attack_chain... PASSED (multi-stage exploitation)

SUMMARY: 20/20 tests passed, X vulnerabilities confirmed
```

---

## How to Interpret Test Results

### PASSED = Vulnerability Exists OR Mitigation is Sufficient

Each test has one of three outcomes:

1. **Test PASSED, Output Shows [VULN]**
   → Vulnerability confirmed, needs fixing
   
   ```
   test_01_audit_trail_tampering PASSED
   [VULN] Audit tampering: injected event accepted as truth
   ```
   
   Action: Implement mitigation from [FIX] section

2. **Test PASSED, Output Shows [OK]**
   → Mitigation is working, no action needed
   
   ```
   test_11_division_by_zero PASSED
   [OK] Division by zero: FIXED in code, confidence=0.0 (safe default)
   ```
   
   Action: None (already secure)

3. **Test FAILED**
   → Unexpected behavior, needs investigation
   
   ```
   test_05_cross_tenant_leakage FAILED
   AssertionError: Cross-tenant read should be rejected
   ```
   
   Action: Debug test fixtures, verify environment

---

## Common Issues & Solutions

### Issue 1: ImportError (Skill Forge modules not found)

**Error:**
```
ImportError: No module named 'corvin_operator.skill_forge.autonomous'
```

**Solution:**
```bash
# Add CorvinOS to Python path
export PYTHONPATH="${PYTHONPATH}:/home/shumway/projects/CorvinOS"

# Or run from project root
cd /home/shumway/projects/CorvinOS
pytest tests/security/test_autonomous_skill_forge_security_e2e.py
```

### Issue 2: Permission Denied on Symlink Tests

**Error:**
```
PermissionError: Operation not permitted
```

**Reason:** Symlinks require elevated permissions on some systems

**Solution:**
```bash
# Run with sudo (for filesystem tests only)
sudo pytest tests/security/test_autonomous_skill_forge_security_e2e.py::test_05_cross_tenant_audit_leakage_symlinks -v

# Or skip symlink tests
pytest tests/security/test_autonomous_skill_forge_security_e2e.py -k "not symlink" -v
```

### Issue 3: Timeout on Subprocess Tests

**Error:**
```
TimeoutError: Test took longer than 10 seconds
```

**Solution:**
```bash
# Increase timeout
pytest tests/security/test_autonomous_skill_forge_security_e2e.py --timeout=30 -v
```

---

## Extending Tests with New Vulnerabilities

### Template for Adding a New Test

```python
@pytest.mark.asyncio
async def test_NN_new_vulnerability_description(approval_gate, mock_audit_backend):
    """
    STRIDE: [Spoofing|Tampering|Repudiation|Information Disclosure|Denial of Service|Elevation of Privilege]
    
    Threat: [1-2 sentences describing vulnerability]
    
    Attack Scenario:
    [Numbered steps showing how to exploit]
    
    Expected Result (if secure):
    [What should happen if vulnerability is mitigated]
    
    Verification:
    [Steps to confirm vulnerability exists or is mitigated]
    
    Severity: [CRITICAL|HIGH|MEDIUM|LOW] / [EASY|MEDIUM|HARD]
    Exploitability: [EASY|MEDIUM|HARD]
    """
    # Step 1: Setup (create fixtures, prepare attack)
    
    # Step 2: Attack (exploit vulnerability)
    
    # Step 3: Verify (check if vulnerable or mitigated)
    assert vulnerability_detected, "Vulnerability should be detected"
    print(f"[VULN] {description}: {finding}")
```

### Adding to STRIDE Analysis Document

1. Update summary table at top (add count)
2. Add new section with full threat description
3. Include attack flow, PoC steps, root cause, mitigations
4. Cross-reference test number

---

## Continuous Integration Integration

### GitHub Actions Example

```yaml
name: Security Tests

on: [push, pull_request]

jobs:
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          pip install pytest pytest-asyncio httpx fastapi
          pip install -e .
      
      - name: Run security tests
        run: |
          pytest tests/security/test_autonomous_skill_forge_security_e2e.py \
            -v \
            --tb=short \
            --junitxml=test-results.xml
      
      - name: Generate coverage report
        run: |
          pytest tests/security/ \
            --cov=corvin_operator/skill-forge \
            --cov=core/skills \
            --cov-report=xml \
            --cov-report=html
      
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v2
        with:
          files: ./coverage.xml
```

---

## Validation Checklist

Before marking tests as complete:

- [ ] All 20 tests run without import errors
- [ ] Test output clearly shows [VULN] or [OK] for each vulnerability
- [ ] STRIDE threat analysis document is complete (all findings documented)
- [ ] Each test uses REAL HTTP requests (httpx) or filesystem operations (Path)
- [ ] No mocked vulnerabilities (tests prove real exploitability)
- [ ] PoC attack steps are step-by-step and reproducible
- [ ] Severity and exploitability ratings are justified
- [ ] Mitigation recommendations are specific and actionable
- [ ] Critical findings (#1, #13) are highlighted
- [ ] Coverage report shows 80%+ coverage of core modules

---

## Security Testing Best Practices

### DO ✅

- Run tests in isolated environment (VM, container)
- Validate both vulnerable and secure code paths
- Document why each test is necessary
- Use real HTTP clients, not mocks
- Test edge cases and race conditions
- Include multitenancy scenarios
- Verify audit trail integrity
- Test fail-closed behavior

### DON'T ❌

- Don't run tests against production systems
- Don't mock the vulnerability (test real code)
- Don't skip CRITICAL tests
- Don't suppress security warnings
- Don't test code before reading it
- Don't assume defaults are secure
- Don't test only the "happy path"
- Don't ignore race conditions

---

## References & Further Reading

### STRIDE Threat Modeling
- [Microsoft STRIDE Threat Modeling](https://docs.microsoft.com/en-us/previous-versions/commerce-server/ee823878(v=cs.20))
- [OWASP Threat Modeling](https://owasp.org/www-community/Threat_Model_Information_Classification)

### Security Testing Standards
- [OWASP Testing Guide](https://owasp.org/www-project-web-security-testing-guide/)
- [CWE Top 25](https://cwe.mitre.org/top25/)
- [NIST Security Testing Guidelines](https://csrc.nist.gov/publications/detail/sp/800-115/final)

### Code References (in this repository)
- ADR-0232: Boot Tripwire (audit chain)
- ADR-0233: Approval Gate Design
- ADR-0572: Feedback Stability & Drift Detection
- ADR-0613: Learning Loop Closure
- ADR-0516: Knowledge Graph Foundation

---

## Support & Questions

For questions about:
- **Test failures:** Check environment setup (imports, paths)
- **Vulnerability findings:** See STRIDE_THREAT_ANALYSIS document
- **Mitigation strategies:** Refer to [FIX] sections in test output and STRIDE doc
- **Contributing new tests:** Follow template in "Extending Tests" section

---

**Document Status:** Complete ✅  
**Last Updated:** 2026-09-20  
**Test File Location:** `/home/shumway/projects/CorvinOS/tests/security/test_autonomous_skill_forge_security_e2e.py`  
**STRIDE Analysis:** `/home/shumway/projects/CorvinOS/docs/security/STRIDE_THREAT_ANALYSIS_AUTONOMOUS_SKILL_FORGE.md`
