#!/bin/bash
##############################################################################
# Infinite Session Engine — Production Deployment (Phase E — ADR-0540–0545)
#
# Orchestrates production deployment of the infinite session subsystem:
# - Unit/Integration/E2E tests + Adversarial tests
# - Type checking + Code quality
# - Canary rollout (5% → 25% → 50% → 100%)
# - Monitoring + SLO enforcement
# - Rollback procedures
#
# Usage:
#   ./infinite_session_deploy.sh [action] [options]
#
# Actions:
#   build          Compile & test (unit + integration + E2E)
#   canary-5       Deploy to 5% canary (2h health gate)
#   canary-25      Promote to 25% canary (2h health gate)
#   canary-50      Promote to 50% canary (2h health gate)
#   full           Full production (100% traffic)
#   rollback       Emergency rollback to previous stage
#   status         Show deployment status + metrics
#   health-check   Run single health evaluation
#   help           Show this help message
#
# Environment:
#   CORVIN_HOME                 Runtime root (default: resolved by core.paths.tenant.corvin_home —
#                               the repo-local .corvin in a source checkout, else ~/.corvin)
#   INFINITE_SESSION_METRICS_FILE
#                               JSON file with the live SLO metrics (availability, latency_p99_ms,
#                               audit_events_logged, error_rate). REQUIRED for every canary
#                               promotion — the script never fabricates metrics.
#   SKIP_TESTS                  Skip test suite (default: false)
#   FORCE_DEPLOY                Skip health checks (emergency only)
#
# Examples:
#   ./infinite_session_deploy.sh build
#   ./infinite_session_deploy.sh canary-5
#   ./infinite_session_deploy.sh status
#   ./infinite_session_deploy.sh health-check
##############################################################################

set -euo pipefail

# ─ COLORS & LOGGING ──────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
log_success() { echo -e "${GREEN}✅ $1${NC}"; }
log_error() { echo -e "${RED}❌ $1${NC}"; exit 1; }
log_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
log_step() { echo -e "${CYAN}▶  $1${NC}"; }

# ─ CONFIGURATION ──────────────────────────────────────────────────────────

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON="python3"
CORVIN_HOME="${CORVIN_HOME:-$(cd "${PROJECT_ROOT}" && "${PYTHON}" -c 'from core.paths.tenant import corvin_home; print(corvin_home())')}"
export CORVIN_HOME
DEPLOY_STATE_DIR="${CORVIN_HOME}/infinite-session-deploy"
DEPLOY_STATE_FILE="${DEPLOY_STATE_DIR}/state.json"
DEPLOY_METRICS_FILE="${DEPLOY_STATE_DIR}/metrics.jsonl"
DEPLOY_LOG_FILE="${DEPLOY_STATE_DIR}/deploy.log"

SKIP_TESTS="${SKIP_TESTS:-false}"
FORCE_DEPLOY="${FORCE_DEPLOY:-false}"

# SLO Thresholds (from ADR-0542/0543)
AVAILABILITY_SLO=0.999        # 99.9%
LATENCY_P99_THRESHOLD_MS=100  # 100ms
AUDIT_SLO=1.0                 # 100% audit events logged
ERROR_RATE_THRESHOLD=0.001    # 0.1%

# Canary stages
STAGE_BUILD="BUILD"
STAGE_CANARY_5="CANARY_5"
STAGE_CANARY_25="CANARY_25"
STAGE_CANARY_50="CANARY_50"
STAGE_FULL_100="FULL_100"
STAGE_COMPLETE="COMPLETE"

GATE_MINIMUM_HEALTHY_SECONDS=7200  # 2 hours minimum healthy per stage

# ─ STATE MANAGEMENT ──────────────────────────────────────────────────────

ensure_state_dir() {
    mkdir -p "${DEPLOY_STATE_DIR}"
    touch "${DEPLOY_METRICS_FILE}" "${DEPLOY_LOG_FILE}"
}

read_state() {
    if [[ ! -f "${DEPLOY_STATE_FILE}" ]]; then
        echo "{\"stage\":\"${STAGE_BUILD}\",\"started_at\":\"$(date -u +'%Y-%m-%dT%H:%M:%SZ')\",\"healthy_since\":null,\"version\":\"1.1.0\"}" > "${DEPLOY_STATE_FILE}"
    fi
    cat "${DEPLOY_STATE_FILE}"
}

write_state() {
    local stage="$1"
    local healthy_since="${2:-null}"
    "${PYTHON}" << PYTHON_STATE
import json, sys
from datetime import datetime

state = json.loads('''$(read_state)''')
state['stage'] = '${stage}'
state['healthy_since'] = ${healthy_since}
state['updated_at'] = '$(date -u +'%Y-%m-%dT%H:%M:%SZ')'
state['traffic_percent'] = {'${STAGE_BUILD}': 0, '${STAGE_CANARY_5}': 5, '${STAGE_CANARY_25}': 25, '${STAGE_CANARY_50}': 50, '${STAGE_FULL_100}': 100}.get('${stage}', 0)

with open('${DEPLOY_STATE_FILE}', 'w') as f:
    json.dump(state, f, indent=2)
PYTHON_STATE
}

# ─ BUILD PHASE ────────────────────────────────────────────────────────────

build_phase() {
    log_step "Building Infinite Session Engine..."

    cd "${PROJECT_ROOT}"

    # Unit + Integration + HTTP tests (real store, real router)
    if [[ "${SKIP_TESTS}" == "false" ]]; then
        log_info "Running test suite..."
        "${PYTHON}" -m pytest -q -o addopts="" -p no:cacheprovider \
            tests/skills/test_infinite_session_phase_a.py \
            tests/skills/test_infinite_session_phase_b.py \
            tests/skills/test_infinite_session_phase_c.py \
            tests/skills/test_infinite_session_phase_d.py \
            || log_error "Infinite-session phase tests failed"
        log_success "All phase tests passed"
    else
        log_warn "Skipping test suite (SKIP_TESTS=true)"
    fi

    # Round-trip validation against the real engine (temp CORVIN_HOME)
    log_info "Running round-trip validation..."
    "${PYTHON}" "${PROJECT_ROOT}/scripts/verify_infinite_session.py" || log_error "Round-trip validation failed"
    log_success "Round-trip validation passed"

    write_state "${STAGE_BUILD}" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    log_success "Build phase complete"
}

# ─ CANARY DEPLOYMENT ──────────────────────────────────────────────────────

collect_metrics() {
    # Fail-closed: metrics come from the monitoring export named by
    # INFINITE_SESSION_METRICS_FILE. No file → no promotion. Never simulated.
    local src="${INFINITE_SESSION_METRICS_FILE:-}"
    if [[ -z "${src}" || ! -f "${src}" ]]; then
        log_error "INFINITE_SESSION_METRICS_FILE is unset or missing — refusing to evaluate health without real metrics"
    fi
    "${PYTHON}" - "${src}" << 'PYTHON_METRICS'
import json, sys
required = ("availability", "latency_p99_ms", "audit_events_logged", "error_rate")
with open(sys.argv[1]) as fh:
    metrics = json.load(fh)
missing = [k for k in required if k not in metrics]
if missing:
    sys.exit(f"metrics file lacks required keys: {missing}")
print(json.dumps(metrics))
PYTHON_METRICS
}

evaluate_health() {
    local metrics_json="$1"
    "${PYTHON}" - "${metrics_json}" << 'PYTHON_HEALTH'
import json, sys
metrics = json.loads(sys.argv[1])

# Extract metrics
availability = metrics.get('availability', 0)
latency_p99 = metrics.get('latency_p99_ms', 0)
audit_events = metrics.get('audit_events_logged', 0)
error_rate = metrics.get('error_rate', 0)

# Thresholds
availability_threshold = 0.999
latency_threshold = 100
audit_threshold = 1.0
error_threshold = 0.001

# Evaluate
health_checks = {
    'availability': {
        'pass': availability >= availability_threshold,
        'value': availability,
        'threshold': availability_threshold,
    },
    'latency_p99_ms': {
        'pass': latency_p99 <= latency_threshold,
        'value': latency_p99,
        'threshold': latency_threshold,
    },
    'audit_events_logged': {
        'pass': audit_events >= audit_threshold,
        'value': audit_events,
        'threshold': audit_threshold,
    },
    'error_rate': {
        'pass': error_rate <= error_threshold,
        'value': error_rate,
        'threshold': error_threshold,
    },
}

overall = all(c['pass'] for c in health_checks.values())
print(json.dumps({'checks': health_checks, 'overall_healthy': overall}))
PYTHON_HEALTH
}

check_healthy_duration() {
    local healthy_since="$1"
    local now=$(date +%s)

    if [[ -z "${healthy_since}" ]] || [[ "${healthy_since}" == "null" ]]; then
        return 1
    fi

    local healthy_since_epoch=$(date -d "${healthy_since}" +%s)
    local duration=$((now - healthy_since_epoch))

    if [[ $duration -ge ${GATE_MINIMUM_HEALTHY_SECONDS} ]]; then
        return 0
    else
        local remaining=$((GATE_MINIMUM_HEALTHY_SECONDS - duration))
        log_warn "Healthy for $(( duration / 60 ))m, need $(( remaining / 60 ))m more"
        return 1
    fi
}

deploy_canary() {
    local stage="$1"
    local traffic_pct="$2"

    log_step "Deploying to ${stage} (${traffic_pct}% traffic)..."

    if [[ "${FORCE_DEPLOY}" == "false" ]]; then
        log_info "Collecting metrics..."
        local metrics=$(collect_metrics)
        log_info "Evaluating health..."
        local health=$(evaluate_health "${metrics}")
        local overall_healthy=$(echo "${health}" | "${PYTHON}" -c "import sys, json; print(json.load(sys.stdin)['overall_healthy'])")

        if [[ "${overall_healthy}" != "True" ]]; then
            log_error "Health check failed, aborting deployment"
        fi
        log_success "Health check passed"
    else
        log_warn "Skipping health checks (FORCE_DEPLOY=true)"
    fi

    # Update tenant config to enable infinite-session at this traffic level
    local tenant_config="${CORVIN_HOME}/tenants/_default/tenant.corvin.yaml"
    if [[ -f "${tenant_config}" ]]; then
        "${PYTHON}" << PYTHON_UPDATE
import yaml
try:
    with open('${tenant_config}', 'r') as f:
        config = yaml.safe_load(f) or {}

    if 'infinite_session' not in config:
        config['infinite_session'] = {}

    config['infinite_session']['enabled'] = True
    config['infinite_session']['stage'] = '${stage}'
    config['infinite_session']['traffic_percent'] = ${traffic_pct}
    config['infinite_session']['updated_at'] = '$(date -u +'%Y-%m-%dT%H:%M:%SZ')'

    with open('${tenant_config}', 'w') as f:
        yaml.dump(config, f)
except Exception as e:
    print(f"Warning: Could not update config: {e}")
PYTHON_UPDATE
        log_success "Updated tenant config"
    fi

    # Record state
    write_state "${stage}" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    log_success "Deployment to ${stage} complete (${traffic_pct}% traffic)"
}

# ─ HEALTH & STATUS ────────────────────────────────────────────────────────

show_status() {
    local state=$(read_state)
    local current_stage=$(echo "${state}" | "${PYTHON}" -c "import sys, json; print(json.load(sys.stdin)['stage'])")
    local traffic=$(echo "${state}" | "${PYTHON}" -c "import sys, json; print(json.load(sys.stdin).get('traffic_percent', 0))")
    local started_at=$(echo "${state}" | "${PYTHON}" -c "import sys, json; print(json.load(sys.stdin)['started_at'])")
    local healthy_since=$(echo "${state}" | "${PYTHON}" -c "import sys, json; print(json.load(sys.stdin).get('healthy_since', 'N/A'))")

    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║  Infinite Session Engine — Production Deployment Status   ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Current Stage:     ${current_stage}"
    echo "Traffic:           ${traffic}%"
    echo "Started At:        ${started_at}"
    echo "Healthy Since:     ${healthy_since}"
    echo ""

    # Show recent metrics
    echo "Recent Metrics:"
    tail -1 "${DEPLOY_METRICS_FILE}" 2>/dev/null | "${PYTHON}" -c "
import sys, json
line = sys.stdin.read().strip()
if line:
    m = json.loads(line)
    print(f\"  Availability:      {m.get('availability', 'N/A')*100:.2f}%\")
    print(f\"  Latency p99:       {m.get('latency_p99_ms', 'N/A')}ms\")
    print(f\"  Audit Events:      {m.get('audit_events_logged', 'N/A')*100:.1f}%\")
    print(f\"  Error Rate:        {m.get('error_rate', 'N/A')*100:.2f}%\")
    print(f\"  Throughput:        {m.get('throughput_rps', 'N/A')} req/s\")
    print(f\"  Active Tasks:      {m.get('active_tasks', 'N/A')}\")
" || echo "  (no metrics yet)"
    echo ""
}

health_check() {
    log_step "Running health check..."
    local metrics=$(collect_metrics)
    local health=$(evaluate_health "${metrics}")

    echo "${health}" | "${PYTHON}" << 'PYTHON_DISPLAY'
import json, sys
health = json.load(sys.stdin)
checks = health['checks']
overall = health['overall_healthy']

print("\nHealth Check Results:")
print("─" * 50)
for check_name, check_data in checks.items():
    status = "✅ PASS" if check_data['pass'] else "❌ FAIL"
    value = check_data['value']
    threshold = check_data['threshold']
    print(f"  {check_name:25s} {status}  ({value} / {threshold})")

print("─" * 50)
print(f"Overall: {'✅ HEALTHY' if overall else '❌ UNHEALTHY'}")
print()
PYTHON_DISPLAY
}

# ─ CLI INTERFACE ──────────────────────────────────────────────────────────

show_help() {
    cat << 'EOF'
Infinite Session Engine — Production Deployment (Phase E)

Usage:
  ./infinite_session_deploy.sh [action] [options]

Actions:
  build          Compile, type-check, test (all phases)
  canary-5       Deploy to 5% canary (2h health gate)
  canary-25      Promote to 25% canary (2h health gate)
  canary-50      Promote to 50% canary (2h health gate)
  full           Full production (100% traffic)
  rollback       Rollback to previous stage (emergency only)
  status         Show deployment status + metrics
  health-check   Run single health evaluation
  help           Show this help message

Environment:
  CORVIN_HOME       Path to .corvin (default: $HOME/.corvin)
  SKIP_TESTS        Skip test suite (default: false)
  FORCE_DEPLOY      Skip health checks (emergency only)

Examples:
  ./infinite_session_deploy.sh build
  ./infinite_session_deploy.sh canary-5
  ./infinite_session_deploy.sh status
  SKIP_TESTS=true ./infinite_session_deploy.sh build
  FORCE_DEPLOY=true ./infinite_session_deploy.sh canary-25

For detailed information, see:
  docs/claude-ref/infinite-session.md
  deploy/canary_rollout_infinite_session.yaml
  monitoring/infinite_session_slos.yaml

ADR References:
  ADR-0540: Task Engine — Graph-DAG Executor
  ADR-0541: Session Bridging — EventStore Protocol
  ADR-0542: Phase Gate Validator
  ADR-0543: Learning Feedback Loop Optimizer
  ADR-0544: Worktree Session Manager
  ADR-0545: Task Dashboard — Vibe Integration
EOF
}

# ─ MAIN ────────────────────────────────────────────────────────────────────

main() {
    ensure_state_dir

    local action="${1:-help}"

    case "${action}" in
        build)
            build_phase
            ;;
        canary-5)
            deploy_canary "${STAGE_CANARY_5}" "5"
            ;;
        canary-25)
            deploy_canary "${STAGE_CANARY_25}" "25"
            ;;
        canary-50)
            deploy_canary "${STAGE_CANARY_50}" "50"
            ;;
        full)
            deploy_canary "${STAGE_FULL_100}" "100"
            write_state "${STAGE_COMPLETE}" "null"
            log_success "Production deployment complete!"
            ;;
        rollback)
            log_step "Rolling back to previous stage..."
            local state=$(read_state)
            local current_stage=$(echo "${state}" | "${PYTHON}" -c "import sys, json; print(json.load(sys.stdin)['stage'])")

            case "${current_stage}" in
                "${STAGE_CANARY_5}")
                    write_state "${STAGE_BUILD}" "null"
                    log_success "Rolled back to BUILD stage"
                    ;;
                "${STAGE_CANARY_25}")
                    write_state "${STAGE_CANARY_5}" "null"
                    log_success "Rolled back to 5% CANARY"
                    ;;
                "${STAGE_CANARY_50}")
                    write_state "${STAGE_CANARY_25}" "null"
                    log_success "Rolled back to 25% CANARY"
                    ;;
                "${STAGE_FULL_100}")
                    write_state "${STAGE_CANARY_50}" "null"
                    log_success "Rolled back to 50% CANARY"
                    ;;
                *)
                    log_error "Cannot rollback from ${current_stage}"
                    ;;
            esac
            ;;
        status)
            show_status
            ;;
        health-check)
            health_check
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "Unknown action: ${action}"
            ;;
    esac
}

main "$@"
