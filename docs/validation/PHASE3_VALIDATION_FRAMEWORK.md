# Phase 3 Validation Framework (Week 6 Go/No-Go Gate)

**Status:** Phase 3.A IMPLEMENTATION COMPLETE  
**Timeline:** Week 1-2 (core validators), Week 3-4 (dashboard/automation, deferred)  
**Gate Decision:** Manual (Week 2), Automated (Week 4, if stable)  

---

## Mission Summary

Build a **comprehensive validation framework** that proves Phase 2 multi-tenant tests work correctly and gates the Week 6 Go/No-Go decision. Phase 3 has two tiers:

| Tier | Focus | Scope | Timeline | Status |
|---|---|---|---|---|
| **3.A (Core)** | Validators + measurement | E2E wiring proof, reproducibility <2% flake, coverage impact | Weeks 1-2 | COMPLETE ✅ |
| **3.B (Dashboard)** | Automation + observability | Grafana, auto-gate logic, alerting | Weeks 3-4 | Deferred (if 3.A stable) |

**Key Principle:** Fail-closed. Any FAIL/BROKEN status blocks the gate until resolved.

---

## Phase 3.A Core Validators (COMPLETE)

### 1. E2E Wiring Proof Validator

**File:** `validation/e2e_wiring_proof.py`

Proves that code is reachable and functional:

**Two-phase validation:**
1. **Phase 1 (Reachability):** Find ≥1 real call site outside definition + test files
2. **Phase 2 (Functional):** E2E test through real transport boundary (HTTP/CLI/MCP/plugin-registry/etc)

**Result:** `E2EWiringProofResult`
- `reachability_status`: PASS | FAIL | SKIP
- `call_sites`: All found call sites (including from tests)
- `real_call_sites`: Non-test call sites (proof of reachability)
- `functional_test_passed`: True | False (Phase 2)
- `failure_reason`: Human-readable diagnosis

**Usage:**
```python
from validation.e2e_wiring_proof import E2EWiringProofValidator

validator = E2EWiringProofValidator(repo_root="/path/to/CorvinOS")
result = validator.validate("core/console/app.py::my_endpoint")

if result.reachability_status.value != "pass":
    print(f"BLOCK: {result.failure_reason}")
```

**Tests:** 18 unit tests in `tests/test_validation_phase3_wiring.py`

---

### 2. Reproducibility Checker

**File:** `validation/reproducibility_checker.py`

Detects flaky tests by running them 3 consecutive times:

**Measurements:**
- Pass rate (target: 100%, warn if <98%)
- Latency variance (coefficient of variation, target: <10% CV)
- Output consistency (warn if output differs across runs)

**Result:** `ReproducibilityResult`
- `pass_rate`: 0.0 to 1.0
- `flake_status`: STABLE (3/3) | FLAKY (1-2/3) | BROKEN (0/3)
- `mean_latency`: Average run time
- `latency_cv`: Coefficient of variation (0-1 scale)
- `output_consistent`: True if all runs produce identical output

**Usage:**
```python
from validation.reproducibility_checker import ReproducibilityChecker

checker = ReproducibilityChecker(repo_root="/path/to/CorvinOS", num_runs=3)
result = checker.check_test("tests/test_phase2_multi_tenant_isolation.py::TestCRUDIsolation::test_insert_as_tenant_a")

if result.flake_status.value == "broken":
    print(f"BLOCK: {result.test_name} is broken (0/3 passes)")
```

**Tests:** 20 unit tests in `tests/test_validation_phase3_reproducibility.py`

---

### 3. Coverage Impact Measurement

**File:** `validation/coverage_impact.py`

Measures code coverage before/after changes:

**Workflow:**
1. `baseline = measure.capture_coverage(label="baseline")` — snapshot before changes
2. Make changes
3. `current = measure.capture_coverage(label="current")` — snapshot after changes
4. `report = measure.compare(baseline, current)` — analyze delta

**Result:** `CoverageImpactReport`
- `delta_coverage_percent`: Negative = regression, positive = improvement
- `new_files`: Files added in current (must meet min_coverage target)
- `improved_files`: Files where coverage increased
- `regressed_files`: Files where coverage decreased
- `untested_functions`: Functions with 0% coverage
- `risk_level`: LOW | MEDIUM | HIGH

**Decision Rule:**
- delta > -2% → LOW
- delta -2% to -5% → MEDIUM
- delta < -5% → HIGH (blocks gate)

**Usage:**
```python
from validation.coverage_impact import CoverageImpactMeasurement

measure = CoverageImpactMeasurement(repo_root="/path/to/CorvinOS", min_coverage=80.0)
baseline = measure.capture_coverage(label="baseline")
# ... make changes ...
current = measure.capture_coverage(label="current")
report = measure.compare(baseline, current)

if report.risk_level == "HIGH":
    print(f"BLOCK: Coverage regression {report.delta_coverage_percent:+.1f}%")
```

**Tests:** 14 unit tests in `tests/test_validation_phase3_coverage.py`

---

### 4. Measurement Report & Gate Logic

**File:** `validation/measurement_report.py`

Aggregates all validations and makes Go/No-Go decision:

**Gate Metrics (configurable):**
```python
GateMetrics(
    pass_rate_threshold=0.80,              # ≥80% tests pass
    flake_threshold=0.98,                  # ≥98% reproducibility
    coverage_regression_threshold=-5.0,    # No more than -5% regression
    min_e2e_coverage=0.80                  # ≥80% entry points reachable
)
```

**Decision Logic (fail-closed):**
- Any E2E FAIL → NO_GO
- Reproducibility: broken (0/3) → NO_GO, flaky (1-2/3) → WARN
- Coverage regression > -5% → NO_GO, HIGH risk → WARN
- Otherwise → GO

**Result:** `MeasurementReport`
- `decision`: GO | NO_GO | HOLD
- `blocking_issues`: Failures that block the gate
- `warnings`: Non-blocking issues (flakes, high-risk coverage)
- `decision_rationale`: Human-readable explanation

**Usage:**
```python
from validation.measurement_report import MeasurementReportBuilder

builder = MeasurementReportBuilder(repo_root="/path/to/CorvinOS")
builder.add_e2e_results(e2e_results)
builder.add_reproducibility_results(repro_results)
builder.add_coverage_impact(coverage_result)

decision = builder.compute_gate_decision()
print(builder.summary())
builder.save_to_file(Path("validation_report.json"))
```

**Tests:** 18 unit tests in `tests/test_validation_phase3_reporting.py`

---

## Test Suite (Phase 3.A)

| Test File | Tests | Focus |
|---|---|---|
| `test_validation_phase3_wiring.py` | 18 | E2E reachability + functional proof |
| `test_validation_phase3_reproducibility.py` | 20 | Flake detection (3-run methodology) |
| `test_validation_phase3_coverage.py` | 14 | Coverage measurement + risk assessment |
| `test_validation_phase3_reporting.py` | 18 | Gate logic + decision automation |
| **Total** | **70** | All core validators |

**Run all tests:**
```bash
cd /home/shumway/projects/CorvinOS
python3 -m pytest tests/test_validation_phase3_*.py -xvs
```

---

## Measurement Report Output

**JSON Format:**
```json
{
  "timestamp": "2026-08-29T10:00:00",
  "phase": "3A",
  "decision": "go",
  "total_e2e_checks": 15,
  "total_tests_for_flake": 60,
  "e2e_wiring_results": {
    "endpoint_1": {
      "reachability_status": "pass",
      "real_call_sites": 2,
      "functional_test_passed": true
    }
  },
  "reproducibility_results": {
    "test_phase2_isolation": {
      "flake_status": "stable",
      "pass_rate": 1.0,
      "latency_cv": 0.05
    }
  },
  "coverage_impact_result": {
    "delta_coverage_percent": 2.5,
    "risk_level": "LOW",
    "new_files": 0,
    "regressed_files": []
  },
  "blocking_issues": [],
  "warnings": [],
  "decision_rationale": ["All metrics pass"]
}
```

---

## Week-by-Week Execution Plan

### Week 1: Core Validator Implementation

**Deliverables:**
- ✅ `validation/e2e_wiring_proof.py`
- ✅ `validation/reproducibility_checker.py`
- ✅ `validation/coverage_impact.py`
- ✅ `validation/measurement_report.py`
- ✅ 70 unit tests (Tier-1/2)

**Acceptance Criteria:**
- All modules compile (Tier-1 lint/type)
- All unit tests pass (Tier-2, mock-based)
- Zero external dependencies (isolated)

### Week 2: Validator Integration & Dry Run

**Deliverables:**
- Run Phase 3.A validators against Phase 2 test suite
- Generate first measurement report
- Identify any measurement gaps
- Manual gate decision (expert review)

**Acceptance Criteria:**
- All 60+ Phase 2 tests measured
- E2E wiring proof completes without timeout
- Reproducibility check < 2 hours for full suite
- Coverage report generated

### Week 3-4: Dashboard & Automation (DEFERRED)

Only proceed if Phase 3.A is stable. Deliverables:
- Grafana dashboard config
- Automated gate logic (no manual review needed)
- Alert rules (threshold breaches)
- CI/CD integration

---

## Integration with Phase 2 Tests

Phase 3 validates Phase 2:

| Phase 2 Test Class | Phase 3 Validator | Gate Requirement |
|---|---|---|
| `TestCRUDIsolation` | E2E wiring proof + reproducibility | STABLE (3/3 passes) |
| `TestCrossTenantQueryIsolation` | Reproducibility + coverage | <2% flake rate |
| `TestRBACEnforcement` | E2E (real API) + reproducibility | STABLE |
| `TestLoadThroughputSLO` | Coverage impact + reproducibility | No regression |

---

## Fail-Closed Design

**Core principle:** Any check that fails → gate blocks until remediated.

| Failure Mode | Result | Recovery |
|---|---|---|
| E2E wiring: zero call sites | NO_GO | Add real call site / prove via E2E test |
| Reproducibility: broken (0/3) | NO_GO | Fix underlying test or flake |
| Reproducibility: flaky (1-2/3) | WARN → GO (with tracking) | Investigate + re-run |
| Coverage regression > -5% | NO_GO | Add tests or refactor |
| Coverage risk HIGH | WARN → GO (with tracking) | Monitor + plan fixes |

---

## Manual Validation Checklist

20 generated test scenarios (Week 1 manual review):

- [ ] E2E wiring: HTTP endpoint detected
- [ ] E2E wiring: CLI command detected
- [ ] E2E wiring: MCP tool detected
- [ ] E2E wiring: Plugin registry entry detected
- [ ] Reproducibility: Stable test (3/3 passes)
- [ ] Reproducibility: Flaky test (2/3 passes)
- [ ] Reproducibility: Broken test (0/3 passes)
- [ ] Reproducibility: Low latency variance (CV < 5%)
- [ ] Reproducibility: High latency variance (CV > 20%)
- [ ] Coverage: Improvement (+5%)
- [ ] Coverage: Regression (-3%, MEDIUM risk)
- [ ] Coverage: Regression (-10%, HIGH risk)
- [ ] Coverage: New file meets threshold
- [ ] Coverage: New file below threshold
- [ ] Gate: All pass → GO decision
- [ ] Gate: One E2E fail → NO_GO decision
- [ ] Gate: Multiple flaky → WARN decision
- [ ] Gate: Coverage regression → NO_GO decision
- [ ] Report: JSON export valid schema
- [ ] Report: Summary human-readable

---

## Success Metrics (Week 2)

| Metric | Target | Evidence |
|---|---|---|
| Test coverage | 70 unit tests | All passing, Tier-1/2 |
| Validator stability | 100% | No timeouts, no crashes |
| Measurement accuracy | ≤10% error | Spot-check vs manual run |
| Report completeness | 100% | All fields populated |
| Decision accuracy | 100% | Manual review agrees with auto-decision |

---

## Future Work (Phase 3.B, Weeks 3-4)

If Phase 3.A is stable:

1. **Grafana Dashboard** — visualize measurements over time
2. **Auto-Gate Logic** — gate decision without manual review (if stable)
3. **Alert Rules** — Slack/email on threshold breach
4. **CI/CD Integration** — automatic gate run on PR
5. **Trend Analysis** — track flake rate, coverage over weeks

---

## Related Documentation

- **Phase 2:** [PHASE2_MULTI_TENANT_VALIDATION_FRAMEWORK.md](PHASE2_MULTI_TENANT_VALIDATION_FRAMEWORK.md)
- **E2E Wiring:** [e2e-wiring-proof-standard.md](../../docs/claude-ref/e2e-wiring-proof-standard.md)
- **LDD Methodology:** [loop-driven-development CLAUDE.md](../../CLAUDE.md)

---

## Sign-Off

**Framework Status:** ✅ Phase 3.A COMPLETE  
**Test Count:** 70 unit tests (Tier-1/2 validated)  
**Readiness:** Ready for Week 2 integration testing  
**Next:** Run Phase 3.A validators against Phase 2 tests, generate measurement report  
