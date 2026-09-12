#!/bin/bash
# Pre-flight checklist for Quality Gates deployment (Phase 3.3)
# Usage: bash scripts/quality-gates-preflight.sh
# Exit codes: 0=all checks passed, 1=one or more checks failed

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
CHECKS_TOTAL=0
CHECKS_PASSED=0
CHECKS_FAILED=0

# Logging functions
log_header() {
  echo -e "${BLUE}=== $1 ===${NC}"
  echo
}

log_check() {
  CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
  echo -ne "$1 ... "
}

log_pass() {
  CHECKS_PASSED=$((CHECKS_PASSED + 1))
  echo -e "${GREEN}✅ PASS${NC}"
}

log_fail() {
  CHECKS_FAILED=$((CHECKS_FAILED + 1))
  echo -e "${RED}❌ FAIL${NC}"
  echo -e "  ${RED}Error: $1${NC}"
}

log_warn() {
  echo -e "${YELLOW}⚠️  $1${NC}"
}

log_summary() {
  echo
  echo -e "${BLUE}=== SUMMARY ===${NC}"
  echo "Total checks: $CHECKS_TOTAL"
  echo -e "Passed: ${GREEN}$CHECKS_PASSED${NC}"
  if [ $CHECKS_FAILED -gt 0 ]; then
    echo -e "Failed: ${RED}$CHECKS_FAILED${NC}"
  fi
  echo
}

# Main preflight checks
main() {
  log_header "Quality Gates Pre-Flight Checklist (Phase 3.3)"

  # Check 1: Phase 1 tests
  log_check "Phase 1 validators (ADR-0688)"
  if pytest core/quality_gates/tests/test_phase1_validators.py -v --tb=short -q > /tmp/phase1_test.log 2>&1; then
    log_pass
  else
    log_fail "Phase 1 validator tests failed"
    tail -20 /tmp/phase1_test.log
    return 1
  fi

  # Check 2: Phase 2 audit integration tests
  log_check "Phase 2 audit integration (ADR-0689)"
  if pytest core/quality_gates/tests/test_phase2_audit_integration.py -v --tb=short -q > /tmp/phase2_test.log 2>&1; then
    log_pass
  else
    log_fail "Phase 2 audit integration tests failed"
    tail -20 /tmp/phase2_test.log
    return 1
  fi

  # Check 3: Phase 3 E2E wiring proof
  log_check "Phase 3 E2E wiring proof (ADR-0690)"
  if pytest core/quality_gates/tests/test_phase3_e2e_wiring.py -v --tb=short -q > /tmp/phase3_test.log 2>&1; then
    log_pass
  else
    log_fail "Phase 3 E2E wiring tests failed"
    tail -20 /tmp/phase3_test.log
    return 1
  fi

  # Check 4: Adversarial tests (all attack vectors)
  log_check "Adversarial review (5 attack vectors)"
  ADVERSARIAL_PASS=true
  for adv_test in core/quality_gates/tests/test_adversarial_*.py; do
    if ! pytest "$adv_test" -v --tb=short -q > /tmp/adversarial_test.log 2>&1; then
      ADVERSARIAL_PASS=false
      log_fail "Adversarial test failed: $adv_test"
      tail -20 /tmp/adversarial_test.log
      return 1
    fi
  done
  if [ "$ADVERSARIAL_PASS" = true ]; then
    log_pass
  fi

  # Check 5: Audit chain verification
  log_check "Audit chain integrity verification"
  if python3 << 'PYEOF' > /tmp/audit_verify.log 2>&1
from core.quality_gates.audit import verify_audit_chain
try:
  verify_audit_chain()
  print("Audit chain verification passed")
except Exception as e:
  print(f"Audit chain verification failed: {e}")
  exit(1)
PYEOF
  then
    log_pass
  else
    log_fail "Audit chain verification failed"
    cat /tmp/audit_verify.log
    return 1
  fi

  # Check 6: E2E wiring proof tests
  log_check "E2E wiring proof (console routes)"
  if pytest core/quality_gates/tests/test_e2e_wiring_proof.py -v --tb=short -q > /tmp/e2e_wiring.log 2>&1; then
    log_pass
  else
    log_fail "E2E wiring proof tests failed"
    tail -20 /tmp/e2e_wiring.log
    return 1
  fi

  # Check 7: Performance SLO
  log_check "Performance SLO (P99 < 500ms)"
  if pytest core/quality_gates/tests/test_phase3_performance_slo.py -v --tb=short -q > /tmp/slo_test.log 2>&1; then
    log_pass
    # Extract and show SLO metrics
    if grep -q "P99" /tmp/slo_test.log; then
      echo "    Metrics:"
      grep "P99\|latency\|outlier" /tmp/slo_test.log | sed 's/^/    /'
    fi
  else
    log_fail "Performance SLO test failed (P99 > 500ms)"
    tail -20 /tmp/slo_test.log
    return 1
  fi

  # Check 8: Import validation
  log_check "Quality gates module import"
  if python3 -c "from core.quality_gates import api; print('Import successful')" > /tmp/import_test.log 2>&1; then
    log_pass
  else
    log_fail "Import failed"
    cat /tmp/import_test.log
    return 1
  fi

  # Check 9: Config file validation
  log_check "Quality gates config file (YAML validity)"
  if [ -f "core/quality_gates/config/tenant.corvin.yaml" ]; then
    if python3 -c "import yaml; yaml.safe_load(open('core/quality_gates/config/tenant.corvin.yaml'))" > /tmp/yaml_test.log 2>&1; then
      log_pass
    else
      log_fail "Config YAML invalid"
      cat /tmp/yaml_test.log
      return 1
    fi
  else
    log_warn "Config file not found (will be created at deployment)"
  fi

  # Check 10: Backup directory exists
  log_check "Backup directory availability"
  if mkdir -p backups/quality-gates; then
    log_pass
  else
    log_fail "Cannot create backup directory"
    return 1
  fi

  # Print summary
  log_summary

  # Final status
  if [ $CHECKS_FAILED -eq 0 ]; then
    echo -e "${GREEN}=== ALL CHECKS PASSED ===${NC}"
    echo "✅ Ready for production deployment"
    echo
    echo "Next steps:"
    echo "  1. Run: bash scripts/quality-gates-deploy.sh"
    echo "  2. Monitor: curl -s http://127.0.0.1:8765/v1/console/quality/gate/status | jq '.'"
    echo "  3. Verify: tail -f ~/.corvin/tenants/_default/logs/quality-gates.log"
    return 0
  else
    echo -e "${RED}=== SOME CHECKS FAILED ===${NC}"
    echo "❌ NOT ready for deployment"
    echo "  Fix the failures above and re-run this script"
    return 1
  fi
}

# Run main
main
exit $?
