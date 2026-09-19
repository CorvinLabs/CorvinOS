# Phase 3a: E2E Integration Test Matrix — Execution Guide

**Status:** ✅ Test suite created (10 tests), ready for execution with real Anthropic API

**Location:** `tests/skills/test_plugin_builder_e2e_matrix.py`

---

## Overview

9 comprehensive End-to-End Integration Tests covering:
- **Scenario 1:** Simple Plugin (Haiku only, <30s)
- **Scenario 2:** Medium Plugin (Haiku + Opus, <60s)
- **Scenario 3:** Complex Plugin (Opus Ideas-mode, <120s)
- **Supporting tests:** Tenant isolation, cost tracking, audit integrity, real LLM verification, wheel validity, scaffold validity, report generation

**All tests use REAL Anthropic API calls (no mocks).**

---

## Prerequisites

### 1. Environment Setup

```bash
cd /home/shumway/projects/CorvinOS

# Activate virtual environment
source .venv/bin/activate

# Verify pytest installed
python -m pytest --version
# Expected: pytest 9.1.1+ (already installed)
```

### 2. Anthropic API Key

Set your API key before running tests:

```bash
export ANTHROPIC_API_KEY="sk-ant-..." # Your actual key
# OR
export ANTHROPIC_API_KEY=$(cat ~/.anthropic_api_key)
```

**Verify:**
```bash
echo $ANTHROPIC_API_KEY | head -c 20  # Should show "sk-ant-" prefix
```

---

## Test Execution

### Run All 9 Tests

```bash
# Full test suite (all 9 tests + 1 report test = 10 total)
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py -v -m real_llm --tb=short

# Expected output:
# ✅ test_e2e_simple_plugin_haiku_only PASSED
# ✅ test_e2e_medium_plugin_opus_checkpoint PASSED
# ✅ test_e2e_complex_plugin_ideas_mode PASSED
# ✅ test_e2e_parallel_tenants_isolation PASSED
# ✅ test_e2e_cost_estimation_accuracy PASSED
# ✅ test_e2e_audit_trail_integrity PASSED
# ✅ test_e2e_real_llm_not_mocked PASSED
# ✅ test_e2e_wheel_validity PASSED
# ✅ test_e2e_scaffold_validity PASSED
# ✅ test_e2e_report_generation PASSED
# 
# ========================= 10 passed in 4m30s =========================
```

### Run Individual Scenarios

```bash
# Scenario 1: Simple (Haiku only, ~30s)
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py::test_e2e_simple_plugin_haiku_only -v

# Scenario 2: Medium (Haiku + Opus, ~60s)
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py::test_e2e_medium_plugin_opus_checkpoint -v

# Scenario 3: Complex (Opus Ideas-mode, ~120s)
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py::test_e2e_complex_plugin_ideas_mode -v

# Audit Integrity (hash-chain verification)
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py::test_e2e_audit_trail_integrity -v

# Real LLM Verification (non-mocked)
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py::test_e2e_real_llm_not_mocked -v
```

### Run with Coverage

```bash
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py \
  --cov=core.plugins.plugin_builder \
  --cov-report=html \
  -v -m real_llm
```

---

## Cost Estimation

| Test | LLM | Calls | Cost/Call | Total |
|---|---|---|---|---|
| Simple | Haiku | 1 | $0.001 | $0.001 |
| Medium | Haiku + Opus | 2 | $0.001 + $0.05 | $0.051 |
| Complex | Opus | 3 | $0.05 | $0.15 |
| Cost Accuracy | Haiku | 3 | $0.001 | $0.003 |
| Audit Integrity | Haiku | 5 | $0.001 | $0.005 |
| Real LLM Check | Haiku | 1 | $0.001 | $0.001 |
| Others (wheel, scaffold, report) | None | 0 | $0 | $0 |
| **TOTAL** | | 15 | | **~$0.22** |

**Expected Execution Time:** 3–5 minutes (including real LLM latency)

---

## Expected Outputs

### 1. Test Results (stdout)

```
============================= test session starts ==============================
platform linux -- Python 3.12, pytest-9.1.1
collected 10 items

test_plugin_builder_e2e_matrix.py::test_e2e_simple_plugin_haiku_only PASSED      [ 10%]
test_plugin_builder_e2e_matrix.py::test_e2e_medium_plugin_opus_checkpoint PASSED [ 20%]
test_plugin_builder_e2e_matrix.py::test_e2e_complex_plugin_ideas_mode PASSED     [ 30%]
test_plugin_builder_e2e_matrix.py::test_e2e_parallel_tenants_isolation PASSED    [ 40%]
test_plugin_builder_e2e_matrix.py::test_e2e_cost_estimation_accuracy PASSED      [ 50%]
test_plugin_builder_e2e_matrix.py::test_e2e_audit_trail_integrity PASSED         [ 60%]
test_plugin_builder_e2e_matrix.py::test_e2e_real_llm_not_mocked PASSED           [ 70%]
test_plugin_builder_e2e_matrix.py::test_e2e_wheel_validity PASSED                [ 80%]
test_plugin_builder_e2e_matrix.py::test_e2e_scaffold_validity PASSED             [ 90%]
test_plugin_builder_e2e_matrix.py::test_e2e_report_generation PASSED             [100%]

========================= 10 passed in 4m30s ========================
```

### 2. Audit Trail (audit.jsonl)

Located at: `~/.corvin/tenants/_default/global/forge/audit.jsonl`

Example events:
```jsonl
{"event_type":"development_started","timestamp":"2026-09-19T14:23:45.123Z","tenant_id":"_default","development_id":"uuid-1","plugin_id":"test.simple_connector","hash":"sha256-abc...","prev_hash":null}
{"event_type":"scaffold_generated","timestamp":"2026-09-19T14:23:46.456Z","tenant_id":"_default","development_id":"uuid-1","hash":"sha256-def...","prev_hash":"sha256-abc..."}
{"event_type":"build_completed","timestamp":"2026-09-19T14:23:50.789Z","tenant_id":"_default","development_id":"uuid-1","hash":"sha256-ghi...","prev_hash":"sha256-def..."}
...
```

### 3. Test Matrix Results JSON

```json
{
  "test_run_id": "1726759425.123",
  "timestamp": "2026-09-19T14:23:45.123456Z",
  "total_tests": 10,
  "passed": 10,
  "failed": 0,
  "skipped": 0,
  "total_duration_seconds": 270,
  "cost_tracking": {
    "haiku_calls": 10,
    "opus_calls": 5,
    "total_cost": 0.255,
    "cost_estimate_accuracy": 0.98
  },
  "scenarios": {
    "simple": {
      "test_name": "test_e2e_simple_plugin_haiku_only",
      "model": "haiku",
      "latency_ms": 2500,
      "latency_target_ms": 30000,
      "cost": 0.001,
      "status": "PASSED",
      "assertions": {
        "plugin_developed_successfully": true,
        "scaffold_directory_created": true,
        "wheel_built": true,
        "audit_events_present": true,
        "tenant_isolation": true,
        "e2e_wiring_proof": true
      }
    },
    "medium": {
      "test_name": "test_e2e_medium_plugin_opus_checkpoint",
      "models": ["haiku", "opus"],
      "latency_ms": 5800,
      "latency_target_ms": 60000,
      "cost": 0.051,
      "status": "PASSED",
      "assertions": {
        "haiku_classification": true,
        "opus_analysis": true,
        "risks_identified": 4,
        "test_cases_generated": 5,
        "wheel_valid": true,
        "audit_trail_complete": true
      }
    },
    "complex": {
      "test_name": "test_e2e_complex_plugin_ideas_mode",
      "model": "opus",
      "latency_ms": 8200,
      "latency_target_ms": 120000,
      "cost": 0.15,
      "status": "PASSED",
      "assertions": {
        "ideas_mode_interaction": true,
        "learning_hooks_present": true,
        "wheel_packaging_complete": true,
        "e2e_tests_generated": true
      }
    }
  },
  "supporting_tests": {
    "tenant_isolation": {
      "parallel_tenants": 2,
      "status": "PASSED",
      "cross_contamination_detected": false
    },
    "cost_accuracy": {
      "estimate_vs_actual_error_percent": 2.5,
      "threshold_percent": 15,
      "status": "PASSED"
    },
    "audit_integrity": {
      "total_events": 5,
      "hash_chain_valid": true,
      "no_collisions": true,
      "status": "PASSED"
    },
    "real_llm_verification": {
      "mock_called": false,
      "real_api_called": true,
      "response_valid": true,
      "status": "PASSED"
    },
    "wheel_validity": {
      "format": "valid_zipfile",
      "contains_dist_info": true,
      "contains_wheel_metadata": true,
      "status": "PASSED"
    },
    "scaffold_validity": {
      "python_syntax_valid": true,
      "json_valid": true,
      "directory_structure_complete": true,
      "status": "PASSED"
    }
  },
  "e2e_wiring_proof": {
    "phase_1_reachability": {
      "real_llm_calls_found": 15,
      "all_components_reachable": true,
      "status": "PASSED"
    },
    "phase_2_functional": {
      "real_api_responses_valid": true,
      "all_assertions_passed": true,
      "no_mocks_used": true,
      "status": "PASSED"
    }
  }
}
```

---

## Troubleshooting

### Issue: "ANTHROPIC_API_KEY environment variable not set"

**Solution:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py -v -m real_llm
```

### Issue: "APIError: Invalid API key"

**Solution:**
```bash
# Verify key is correct
echo $ANTHROPIC_API_KEY | head -c 20
# Should show: sk-ant-

# Check for trailing whitespace
export ANTHROPIC_API_KEY=$(echo $ANTHROPIC_API_KEY | xargs)
```

### Issue: "Test timed out"

**Solution:**
- Haiku classification: expected ~1–2 seconds
- Opus checkpoint: expected ~3–5 seconds
- If slower, check network connectivity and API rate limits

### Issue: "Audit chain integrity failed"

**Solution:**
- Verify tenant_id is consistent throughout
- Check that all events have hash + prev_hash fields
- Inspect audit.jsonl for malformed records

---

## Verification Checklist

After running all tests, verify:

- [ ] All 10 tests PASSED
- [ ] Total cost < $0.30 (cost_estimate accurate within 15%)
- [ ] Execution time < 5 minutes (real LLM latency included)
- [ ] Audit trail (audit.jsonl) contains ≥20 events
- [ ] Hash-chain integrity verified (no broken links)
- [ ] Tenant isolation maintained (_default tenant in all events)
- [ ] No mocks used (real Anthropic API calls only)
- [ ] E2E wiring proof complete (reachability + functional)
- [ ] Wheel files valid (tarfile check passes)
- [ ] Scaffold Python syntax valid
- [ ] Results JSON generated with full metrics

---

## Integration with CI/CD

To run in CI/CD pipeline:

```yaml
# .github/workflows/e2e-matrix.yml
name: E2E Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: |
          python -m pip install -e .
          python -m pytest tests/skills/test_plugin_builder_e2e_matrix.py \
            -v -m real_llm \
            --tb=short \
            --timeout=600 \
            --json-report \
            --json-report-file=e2e_results.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: actions/upload-artifact@v3
        with:
          name: e2e-results
          path: e2e_results.json
```

---

## Next Steps (Post-Execution)

1. **Commit test results:**
   ```bash
   git add tests/skills/test_plugin_builder_e2e_matrix.py
   git commit -m "feat(plugin-builder): Phase 3a E2E Integration Test Matrix [ADR-0262][ADR-0613]"
   ```

2. **Archive results:**
   ```bash
   mkdir -p /home/shumway/projects/Corvin-ADR/archive/2026-09-19
   cp e2e_test_matrix_results.json /home/shumway/projects/Corvin-ADR/archive/2026-09-19/
   cp e2e_audit_trail_export.jsonl /home/shumway/projects/Corvin-ADR/archive/2026-09-19/
   ```

3. **Update ADR status:**
   - ADR-0262: `status: ACCEPTED`
   - ADR-0613: `status: ACCEPTED`
   - ADR-e2e-wiring-proof: `status: ACCEPTED`

---

## References

- **Test file:** `tests/skills/test_plugin_builder_e2e_matrix.py`
- **Fixtures:** `core/plugins/plugin_builder/testing_framework/base_fixtures.py`
- **ADR-0262:** Plugin-Builder Architecture (ADR repo)
- **ADR-0613:** Learning Loop Closure (ADR repo)
- **E2E Wiring Proof Standard:** `docs/claude-ref/e2e-wiring-proof-standard.md`

