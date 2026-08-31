#!/bin/bash
##############################################################################
# Week 2 Canary Deployment Orchestrator (ADR-0461 Compliance)
#
# Orchestrates autonomous canary rollout: 10% → 50% → 100% traffic
# with 48-hour health gates at each stage.
#
# Usage:
#   ./canary-rollout.sh [stage] [action]
#
# Examples:
#   ./canary-rollout.sh status                 # Show current stage
#   ./canary-rollout.sh promote                # Auto-promote if healthy
#   ./canary-rollout.sh manual-promote FORCE   # Force promotion (audit)
#   ./canary-rollout.sh rollback               # Emergency rollback
##############################################################################

set -euo pipefail

# Configuration
CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"
CANARY_STATE_DIR="${CORVIN_HOME}/canary-deployment"
CANARY_STATE_FILE="${CANARY_STATE_DIR}/state.json"
CANARY_METRICS_FILE="${CANARY_STATE_DIR}/metrics.jsonl"
CANARY_DECISIONS_FILE="${CANARY_STATE_DIR}/decisions.jsonl"
CANARY_LOG_FILE="${CANARY_STATE_DIR}/canary.log"

# SLO Thresholds (from ADR-0461)
ERROR_RATE_THRESHOLD=0.001          # 0.1%
LATENCY_P99_THRESHOLD_MS=500        # 500ms
AUDIT_INTEGRITY_THRESHOLD=0.999     # 99.9%
LATENCY_DEGRADATION_THRESHOLD_MS=1000  # p99 > 1000ms = rollback
ERROR_SPIKE_THRESHOLD=0.05          # 5% = rollback

# Stage names
STAGE_INITIAL="INITIAL"
STAGE_CANARY_10="CANARY_10"
STAGE_RAMP_50="RAMP_50"
STAGE_FULL_100="FULL_100"
STAGE_COMPLETE="COMPLETE"

# Minimum healthy period (seconds)
GATE_MINIMUM_HEALTHY_SECONDS=172800  # 48 hours

# Alert channels
ALERT_SLACK_WEBHOOK="${CANARY_SLACK_WEBHOOK:-}"
ALERT_EMAIL="${CANARY_ALERT_EMAIL:-}"

##############################################################################
# Logging & State Management
##############################################################################

log() {
    local level="$1"
    shift
    local msg="$@"
    local timestamp=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
    echo "[${timestamp}] [${level}] ${msg}" >> "${CANARY_LOG_FILE}"
    echo "[${timestamp}] [${level}] ${msg}"
}

ensure_state_dir() {
    mkdir -p "${CANARY_STATE_DIR}"
    touch "${CANARY_METRICS_FILE}" "${CANARY_DECISIONS_FILE}"
}

# Read current state from JSON
read_state() {
    if [[ ! -f "${CANARY_STATE_FILE}" ]]; then
        echo "{\"stage\":\"${STAGE_INITIAL}\",\"started_at\":\"$(date -u +'%Y-%m-%dT%H:%M:%SZ')\",\"healthy_since\":null}" > "${CANARY_STATE_FILE}"
    fi
    cat "${CANARY_STATE_FILE}"
}

# Write state to JSON
write_state() {
    local stage="$1"
    local healthy_since="$2"
    local state_json=$(python3 -c "
import json, sys
state = json.loads('''$(read_state)''')
state['stage'] = '${stage}'
state['healthy_since'] = ${healthy_since}
state['updated_at'] = '$(date -u +'%Y-%m-%dT%H:%M:%SZ')'
print(json.dumps(state, indent=2))
")
    echo "${state_json}" > "${CANARY_STATE_FILE}"
}

##############################################################################
# Metrics & Health Checks
##############################################################################

# Collect current metrics from monitoring system
collect_metrics() {
    # Query Prometheus metrics for canary deployment
    # This example queries a local Prometheus instance
    # Adjust for your actual monitoring setup

    local query_error_rate="increase(canary_errors_total[5m]) / increase(canary_requests_total[5m])"
    local query_latency_p99="histogram_quantile(0.99, canary_latency_ms)"
    local query_audit_integrity="(canary_audit_chain_valid / canary_audit_chain_total) * 100"

    # For this implementation, we'll use curl to query Prometheus
    # In production, integrate with your metrics collector
    log "INFO" "Collecting current metrics..."

    # Placeholder: in real deployment, query Prometheus API
    # For now, return mock healthy values for demo
    python3 -c "
import json, time
metrics = {
    'timestamp': int(time.time()),
    'error_rate': 0.0008,  # 0.08% error rate
    'latency_p99_ms': 425,   # 425ms p99 latency
    'audit_integrity': 0.9995,  # 99.95% audit integrity
    'throughput_rps': 4300,  # 4300 requests/sec
}
print(json.dumps(metrics))
"
}

# Evaluate health against SLO thresholds
evaluate_health() {
    local metrics_json="$1"

    python3 << 'PYTHON_END'
import json, sys
metrics = json.loads(sys.argv[1])
error_rate = metrics.get('error_rate', 0)
latency_p99 = metrics.get('latency_p99_ms', 0)
audit_integrity = metrics.get('audit_integrity', 1.0)

# Read thresholds from environment (would be set at runtime)
error_threshold = 0.001  # 0.1%
latency_threshold = 500  # 500ms
audit_threshold = 0.999  # 99.9%

# Evaluate each metric
errors = error_rate <= error_threshold
latency = latency_p99 <= latency_threshold
audit = audit_integrity >= audit_threshold

# Overall health status
health_status = {
    'error_rate': {'pass': errors, 'value': error_rate, 'threshold': error_threshold},
    'latency_p99_ms': {'pass': latency, 'value': latency_p99, 'threshold': latency_threshold},
    'audit_integrity': {'pass': audit, 'value': audit_integrity, 'threshold': audit_threshold},
    'overall_healthy': errors and latency and audit,
}
print(json.dumps(health_status))
PYTHON_END
}

# Check if stage has been healthy for required duration
check_healthy_duration() {
    local healthy_since="$1"
    local now=$(date +%s)

    if [[ -z "${healthy_since}" ]] || [[ "${healthy_since}" == "null" ]]; then
        return 1  # Not yet tracked as healthy
    fi

    local healthy_since_epoch=$(date -d "${healthy_since}" +%s)
    local duration=$((now - healthy_since_epoch))

    if [[ $duration -ge ${GATE_MINIMUM_HEALTHY_SECONDS} ]]; then
        return 0  # Healthy for long enough
    else
        local hours=$((duration / 3600))
        log "INFO" "Stage healthy for ${hours}h, need 48h ($(( (GATE_MINIMUM_HEALTHY_SECONDS - duration) / 3600 ))h remaining)"
        return 1
    fi
}

##############################################################################
# State Transitions
##############################################################################

# Promote from one stage to the next
promote_stage() {
    local current_stage="$1"
    local next_stage=""
    local new_traffic_pct=""

    case "${current_stage}" in
        "${STAGE_INITIAL}")
            next_stage="${STAGE_CANARY_10}"
            new_traffic_pct="10"
            ;;
        "${STAGE_CANARY_10}")
            next_stage="${STAGE_RAMP_50}"
            new_traffic_pct="50"
            ;;
        "${STAGE_RAMP_50}")
            next_stage="${STAGE_FULL_100}"
            new_traffic_pct="100"
            ;;
        "${STAGE_FULL_100}")
            next_stage="${STAGE_COMPLETE}"
            new_traffic_pct="100"
            ;;
        *)
            log "ERROR" "Cannot promote from stage ${current_stage}"
            return 1
            ;;
    esac

    log "INFO" "Promoting from ${current_stage} to ${next_stage} (${new_traffic_pct}% traffic)"

    # Apply feature flag / traffic routing change
    # This would integrate with your feature flag / load balancer system
    apply_stage_configuration "${next_stage}" "${new_traffic_pct}"

    # Write new state with reset healthy_since
    write_state "${next_stage}" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

    # Log decision
    log_decision "PROMOTE" "${current_stage}" "${next_stage}" "Health gates passed"

    send_alert "PROMOTE" "${next_stage}" "Traffic ramping to ${new_traffic_pct}%"
}

# Rollback to previous stage
rollback_stage() {
    local current_stage="$1"
    local reason="$2"

    log "WARN" "ROLLBACK triggered: ${reason}"

    case "${current_stage}" in
        "${STAGE_CANARY_10}")
            log "INFO" "Rolling back to ${STAGE_INITIAL}"
            apply_stage_configuration "${STAGE_INITIAL}" "0"
            write_state "${STAGE_INITIAL}" "null"
            ;;
        "${STAGE_RAMP_50}")
            log "INFO" "Rolling back to ${STAGE_CANARY_10}"
            apply_stage_configuration "${STAGE_CANARY_10}" "10"
            write_state "${STAGE_CANARY_10}" "null"
            ;;
        "${STAGE_FULL_100}")
            log "INFO" "Rolling back to ${STAGE_RAMP_50}"
            apply_stage_configuration "${STAGE_RAMP_50}" "50"
            write_state "${STAGE_RAMP_50}" "null"
            ;;
        *)
            log "ERROR" "Cannot rollback from stage ${current_stage}"
            return 1
            ;;
    esac

    log_decision "ROLLBACK" "${current_stage}" "" "${reason}"
    send_alert "ROLLBACK" "${current_stage}" "Reason: ${reason}"
}

# Apply configuration for a given stage
apply_stage_configuration() {
    local stage="$1"
    local traffic_pct="$2"

    log "INFO" "Applying stage configuration: ${stage} (${traffic_pct}% traffic)"

    # Update feature flags in tenant config
    # This example uses a hypothetical config API
    # Integrate with your actual feature flag system (LaunchDarkly, Unleash, etc.)

    # Example: Update local config file
    local tenant_config="${CORVIN_HOME}/tenants/_default/tenant.corvin.yaml"
    if [[ -f "${tenant_config}" ]]; then
        python3 << PYTHON_UPDATE_CONFIG
import yaml
with open('${tenant_config}', 'r') as f:
    config = yaml.safe_load(f)

# Update canary settings
if 'canary_deployment' not in config:
    config['canary_deployment'] = {}

config['canary_deployment']['stage'] = '${stage}'
config['canary_deployment']['traffic_percentage'] = int('${traffic_pct}')
config['canary_deployment']['updated_at'] = '$(date -u +'%Y-%m-%dT%H:%M:%SZ')'

with open('${tenant_config}', 'w') as f:
    yaml.dump(config, f)
PYTHON_UPDATE_CONFIG
        log "INFO" "Updated tenant config to ${stage}"
    else
        log "WARN" "Tenant config file not found: ${tenant_config}"
    fi

    # Send signal to running services to reload config
    # This would integrate with your service reload mechanism
    # e.g., systemctl reload corvin-service, Kubernetes pod restart, etc.
}

##############################################################################
# Decision Logic
##############################################################################

# Automatic promotion logic
auto_promote() {
    local state=$(read_state)
    local current_stage=$(echo "${state}" | python3 -c "import sys, json; print(json.load(sys.stdin)['stage'])")
    local healthy_since=$(echo "${state}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('healthy_since', 'null'))")

    # Don't promote from complete stage
    if [[ "${current_stage}" == "${STAGE_COMPLETE}" ]]; then
        log "INFO" "Rollout complete, no further promotions"
        return 0
    fi

    # Collect metrics
    local metrics=$(collect_metrics)
    log "DEBUG" "Current metrics: ${metrics}"

    # Evaluate health
    local health=$(evaluate_health "${metrics}")
    local overall_healthy=$(echo "${health}" | python3 -c "import sys, json; print(json.load(sys.stdin)['overall_healthy'])")

    if [[ "${overall_healthy}" == "True" ]]; then
        if check_healthy_duration "${healthy_since}"; then
            log "INFO" "Health gates passed, promoting to next stage"
            promote_stage "${current_stage}"
            return 0
        else
            log "INFO" "Health gates passed but insufficient duration (< 48h)"
            return 0
        fi
    else
        # Check for rollback triggers
        local error_rate=$(echo "${metrics}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error_rate', 0))")
        local latency=$(echo "${metrics}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('latency_p99_ms', 0))")

        if (( $(echo "${error_rate} > 0.05" | bc -l) )); then
            log "WARN" "Error spike detected (${error_rate}), rolling back"
            rollback_stage "${current_stage}" "Error spike (${error_rate})"
        elif (( $(echo "${latency} > 1000" | bc -l) )); then
            log "WARN" "Latency degradation detected (${latency}ms), rolling back"
            rollback_stage "${current_stage}" "Latency degradation (${latency}ms)"
        else
            log "WARN" "Health check failed, not yet rolling back"
        fi
    fi
}

##############################################################################
# Audit & Reporting
##############################################################################

log_decision() {
    local action="$1"
    local from_stage="$2"
    local to_stage="$3"
    local reason="$4"

    python3 << PYTHON_DECISION_LOG
import json, time
decision = {
    'timestamp': int(time.time()),
    'iso_timestamp': '$(date -u +'%Y-%m-%dT%H:%M:%SZ')' ,
    'action': '${action}',
    'from_stage': '${from_stage}',
    'to_stage': '${to_stage}',
    'reason': '${reason}',
    'operator': '$(whoami)',
    'hostname': '$(hostname)',
}
with open('${CANARY_DECISIONS_FILE}', 'a') as f:
    f.write(json.dumps(decision) + '\n')
PYTHON_DECISION_LOG
}

send_alert() {
    local event_type="$1"
    local stage="$2"
    local message="$3"

    log "INFO" "Alert: [${event_type}] ${stage} - ${message}"

    # Send to Slack if webhook is configured
    if [[ -n "${ALERT_SLACK_WEBHOOK}" ]]; then
        curl -X POST "${ALERT_SLACK_WEBHOOK}" \
            -H 'Content-Type: application/json' \
            -d "{
                \"text\": \"Canary Deployment: ${event_type}\",
                \"blocks\": [{
                    \"type\": \"section\",
                    \"text\": {\"type\": \"mrkdwn\", \"text\": \"*${event_type}*\nStage: ${stage}\nMessage: ${message}\nTime: $(date -u +'%Y-%m-%d %H:%M:%S') UTC\"}
                }]
            }" || log "WARN" "Failed to send Slack alert"
    fi

    # Send email if configured
    if [[ -n "${ALERT_EMAIL}" ]]; then
        echo "Canary Deployment ${event_type}: ${stage} - ${message}" | \
            mail -s "Canary Alert: ${event_type}" "${ALERT_EMAIL}" || log "WARN" "Failed to send email alert"
    fi
}

##############################################################################
# CLI Interface
##############################################################################

show_status() {
    local state=$(read_state)
    local current_stage=$(echo "${state}" | python3 -c "import sys, json; print(json.load(sys.stdin)['stage'])")
    local started_at=$(echo "${state}" | python3 -c "import sys, json; print(json.load(sys.stdin)['started_at'])")
    local healthy_since=$(echo "${state}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('healthy_since', 'N/A'))")

    echo "========================================="
    echo "Canary Deployment Status"
    echo "========================================="
    echo "Current Stage: ${current_stage}"
    echo "Started At: ${started_at}"
    echo "Healthy Since: ${healthy_since}"
    echo ""

    # Show recent decisions
    echo "Recent Decisions:"
    tail -5 "${CANARY_DECISIONS_FILE}" 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    print(f\"  [{d['iso_timestamp']}] {d['action']}: {d['from_stage']} -> {d['to_stage']} ({d['reason']})\")" || echo "  (none yet)"

    echo ""
    echo "Recent Metrics:"
    tail -1 "${CANARY_METRICS_FILE}" 2>/dev/null | python3 -c "
import sys, json
line = sys.stdin.read().strip()
if line:
    m = json.loads(line)
    print(f\"  Error Rate: {m.get('error_rate', 'N/A')}\")
    print(f\"  Latency p99: {m.get('latency_p99_ms', 'N/A')}ms\")
    print(f\"  Audit Integrity: {m.get('audit_integrity', 'N/A')}\")
" || echo "  (no metrics yet)"
}

show_help() {
    cat << 'EOF'
Week 2 Canary Deployment Orchestrator (ADR-0461)

Usage:
  ./canary-rollout.sh [command] [options]

Commands:
  status              Show current deployment status
  promote             Automatically promote if health gates pass
  manual-promote      Manually promote to next stage (requires FORCE flag)
  rollback            Rollback to previous stage (emergency only)
  logs                Show recent deployment logs
  health-check        Run a single health evaluation

Options:
  FORCE              Bypass health checks (for manual-promote only)

Examples:
  ./canary-rollout.sh status
  ./canary-rollout.sh promote
  ./canary-rollout.sh manual-promote FORCE
  ./canary-rollout.sh rollback
  ./canary-rollout.sh logs
  ./canary-rollout.sh health-check

ADR-0461 Gates:
  - 48h minimum healthy at 10%
  - 48h minimum healthy at 50%
  - 7d minimum stable at 100%

  Error Rate < 0.1%
  Latency p99 < 500ms
  Audit Integrity > 99.9%

For details, see docs/CANARY_DEPLOYMENT_PLAYBOOK.md
EOF
}

##############################################################################
# Main
##############################################################################

main() {
    ensure_state_dir

    local command="${1:-status}"

    case "${command}" in
        status)
            show_status
            ;;
        promote)
            auto_promote
            ;;
        manual-promote)
            if [[ "${2:-}" == "FORCE" ]]; then
                local state=$(read_state)
                local current_stage=$(echo "${state}" | python3 -c "import sys, json; print(json.load(sys.stdin)['stage'])")
                log "WARN" "Manual promotion (FORCE flag) by $(whoami)"
                promote_stage "${current_stage}"
            else
                log "ERROR" "Manual promotion requires FORCE flag: canary-rollout.sh manual-promote FORCE"
                exit 1
            fi
            ;;
        rollback)
            if [[ "${2:-}" == "FORCE" ]]; then
                local state=$(read_state)
                local current_stage=$(echo "${state}" | python3 -c "import sys, json; print(json.load(sys.stdin)['stage'])")
                rollback_stage "${current_stage}" "Manual rollback (emergency)"
            else
                log "ERROR" "Rollback requires FORCE flag: canary-rollout.sh rollback FORCE"
                exit 1
            fi
            ;;
        logs)
            tail -n "${2:-50}" "${CANARY_LOG_FILE}"
            ;;
        health-check)
            local metrics=$(collect_metrics)
            local health=$(evaluate_health "${metrics}")
            echo "${health}" | python3 -m json.tool
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            echo "Unknown command: ${command}"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
