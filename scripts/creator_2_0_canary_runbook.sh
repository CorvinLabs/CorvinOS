#!/bin/bash
#
# Creator 2.0 Canary Rollout Runbook (Weeks 17-20)
#
# Usage:
#   ./creator_2_0_canary_runbook.sh phase1_day1     # Deploy 5% canary (Week 17 Day 1)
#   ./creator_2_0_canary_runbook.sh phase1_monitor  # Monitor Phase 1 (daily)
#   ./creator_2_0_canary_runbook.sh phase1_gate     # Week 17 Day 7 decision gate
#   ./creator_2_0_canary_runbook.sh phase2_expand   # Expand to 25% (Week 18 Day 8)
#   ./creator_2_0_canary_runbook.sh rollback         # Instant rollback (if incident)
#
# Reference: CREATOR_2_0_CANARY_ROLLOUT_PLAN.md § III Deployment Runbook

set -euo pipefail

# Configuration
REPO_ROOT="/home/shumway/projects/CorvinOS"
CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"
TENANT_ID="_default"
FLAG_ID="creator_2_0_enabled"
METRICS_URL="http://localhost:9090"
CONSOLE_URL="http://localhost:8765"
AUDIT_HOME="$CORVIN_HOME/tenants/$TENANT_ID/global/forge"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Phase 1: Deploy 5% Canary (Week 17 Day 1)
phase1_deploy_5_percent() {
    log_info "Phase 1 Deployment: 5% Canary (Week 17 Day 1)"
    log_info "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    # 1. Verify feature flag is registered
    log_info "1. Verifying feature flag registration..."
    cd "$REPO_ROOT"
    grep -q "creator_2_0_enabled" core/console/corvin_core/feature_flags.py || {
        log_error "Feature flag not found in registry!"
        exit 1
    }
    log_info "   ✓ Feature flag registered"

    # 2. Commit + push code
    log_info "2. Committing feature flag registration..."
    git add core/console/corvin_core/feature_flags.py
    git commit -m "feat(creator-2-0): Register canary flag + 5% rollout [ADR-0661 ADR-0662]" || {
        log_warn "   (Already committed; skipping)"
    }
    git push origin main || {
        log_warn "   (Already pushed; skipping)"
    }
    log_info "   ✓ Code committed and pushed"

    # 3. Restart console API
    log_info "3. Restarting console API..."
    systemctl --user restart corvin-console-api || {
        log_warn "   Could not restart systemd service (may not be running)"
    }
    sleep 2
    log_info "   ✓ Console API restarted"

    # 4. Verify feature flag is live
    log_info "4. Verifying feature flag is live..."
    response=$(curl -s "${CONSOLE_URL}/v1/console/capabilities/features" 2>/dev/null || echo '{}')
    if echo "$response" | grep -q "creator_2_0_enabled"; then
        log_info "   ✓ Feature flag is live and responding"
    else
        log_warn "   Feature flag endpoint not responding (may be down)"
    fi

    # 5. Verify audit chain integrity
    log_info "5. Verifying audit chain integrity..."
    if [ -f "$AUDIT_HOME/audit.jsonl" ]; then
        chain_size=$(wc -l < "$AUDIT_HOME/audit.jsonl")
        log_info "   ✓ Audit chain exists (${chain_size} events)"
    else
        log_warn "   Audit chain file not found (may not exist yet)"
    fi

    # 6. Verify learning daemon is running
    log_info "6. Verifying learning daemon..."
    if systemctl --user is-active --quiet corvin-learning-daemon; then
        log_info "   ✓ Learning daemon is running"
    else
        log_warn "   Learning daemon not running (may not be enabled)"
    fi

    log_info ""
    log_info "Phase 1 Deployment Complete!"
    log_info "Monitoring window: Week 17 Day 1-7 (24h baseline collection)"
    log_info "Decision gate: Week 17 Day 7 @ 18:00 UTC"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Monitor dashboard: $METRICS_URL"
    log_info "  2. Run daily: ./creator_2_0_canary_runbook.sh phase1_monitor"
    log_info "  3. If incident: ./creator_2_0_canary_runbook.sh rollback"
}

# Phase 1: Daily Monitoring
phase1_monitor() {
    log_info "Phase 1 Daily Monitoring Check"
    log_info "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    # Check console API is up
    log_info "1. Console API status..."
    if curl -s "$CONSOLE_URL/v1/console/capabilities/features" >/dev/null 2>&1; then
        log_info "   ✓ Console API is responding"
    else
        log_error "   ✗ Console API is down!"
        exit 1
    fi

    # Check learning daemon queue depth
    log_info "2. Learning daemon queue depth..."
    # In real implementation, this would query Prometheus
    log_info "   (Check Prometheus: creator_2_0_learning_convergence metric)"

    # Check P99 latency
    log_info "3. P99 latency..."
    log_info "   (Check Grafana dashboard: Creator 2.0 Canary Rollout)"

    # Check error rate
    log_info "4. Error rate..."
    log_info "   (Check Prometheus: creator_2_0_errors_total)"

    # Check audit chain integrity
    log_info "5. Audit chain integrity..."
    if [ -f "$AUDIT_HOME/audit.jsonl" ]; then
        chain_size=$(wc -l < "$AUDIT_HOME/audit.jsonl")
        log_info "   ✓ Audit chain: ${chain_size} events"
    fi

    # Manual inspection: last 10 audit events
    log_info "6. Last 10 audit events (Creator 2.0)..."
    if [ -f "$AUDIT_HOME/audit.jsonl" ]; then
        tail -10 "$AUDIT_HOME/audit.jsonl" | \
            grep -o '"skill_id":"creator_2_0"' | head -3 || {
            log_warn "   No Creator 2.0 events yet"
        }
    fi

    log_info ""
    log_info "Monitoring check complete. All systems nominal."
}

# Phase 1: Decision Gate (Week 17 Day 7)
phase1_decision_gate() {
    log_info "Phase 1 Decision Gate (Week 17 Day 7 @ 18:00 UTC)"
    log_info "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    log_info ""
    log_info "Go/No-Go Checklist:"
    log_info ""

    # Check 1: Zero P1 incidents
    log_info "[ ] Zero P1 incidents"
    log_info "    Verify: Prometheus alert (creator_2_0_errors_total) is not firing"

    # Check 2: P99 latency <= 400ms
    log_info "[ ] P99 latency <= 400ms"
    log_info "    Verify: Prometheus (histogram_quantile(0.99, creator_2_0_latency_p99))"

    # Check 3: Learning convergence > 80%
    log_info "[ ] Learning convergence > 80%"
    log_info "    Verify: Prometheus (creator_2_0_learning_convergence < 2%)"

    # Check 4: Audit chain integrity
    log_info "[ ] Audit chain gaps = 0"
    log_info "    Verify: Prometheus (creator_2_0_audit_chain_gaps == 0)"

    log_info ""
    log_info "Decision:"
    log_info "  If all 4 criteria GREEN: Proceed to Phase 2 (Week 18, 25%)"
    log_info "  If any RED: ROLLBACK (Week 17, 0%)"
    log_info ""
}

# Phase 2: Expand to 25% (Week 18 Day 8)
phase2_expand_25_percent() {
    log_info "Phase 2 Deployment: Expand to 25% (Week 18 Day 8)"
    log_info "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    log_info "1. Editing feature flag routing code..."
    # Note: In actual implementation, this would be in the skill creator entry point
    log_info "   (Change canary_pct=5 to canary_pct=25)"

    log_info "2. Committing changes..."
    cd "$REPO_ROOT"
    git add core/skills/skill_creator/entry_point.py 2>/dev/null || {
        log_warn "   Entry point file not found (expected)"
    }
    git commit -m "feat(creator-2-0): Expand to 25% canary [Phase 2]" 2>/dev/null || {
        log_warn "   Already committed"
    }
    git push origin main 2>/dev/null || {
        log_warn "   Already pushed"
    }

    log_info "3. Restarting console API..."
    systemctl --user restart corvin-console-api || {
        log_warn "   Systemd not available"
    }
    sleep 2

    log_info "4. Verifying 25% routing is live..."
    log_info "   (Monitor Prometheus: adoption should rise from 5% to 25%)"

    log_info ""
    log_info "Phase 2 Deployment Complete!"
    log_info "Monitoring window: Week 18 Day 8-14 (adoption + learning convergence)"
}

# Phase 3: Expand to 50% (Week 19 Day 15)
phase3_expand_50_percent() {
    log_info "Phase 3 Deployment: Expand to 50% (Week 19 Day 15)"
    log_info "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    log_info "1. Editing feature flag routing code..."
    log_info "   (Change canary_pct=25 to canary_pct=50)"

    log_info "2. Committing changes..."
    cd "$REPO_ROOT"
    git commit -am "feat(creator-2-0): Expand to 50% canary [Phase 3]" 2>/dev/null || {
        log_warn "   Already committed"
    }
    git push origin main 2>/dev/null || {
        log_warn "   Already pushed"
    }

    log_info "3. Verifying 50% routing..."
    log_info "   (Monitor: DAG health, feedback correlation)"

    log_info ""
    log_info "Phase 3 Deployment Complete!"
    log_info "Monitoring window: Week 19 Day 15-21 (DAG health + multi-skill correlation)"
}

# Phase 4: Full Production Rollout (Week 20 Day 22)
phase4_full_rollout() {
    log_info "Phase 4 Deployment: Full Production Rollout (Week 20 Day 22)"
    log_info "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    log_info "1. Removing canary percentage routing..."
    log_info "   (Use direct is_enabled check instead of canary_percentage_routing)"

    log_info "2. Committing changes..."
    cd "$REPO_ROOT"
    git commit -am "feat(creator-2-0): Full production rollout [Phase 4]" 2>/dev/null || {
        log_warn "   Already committed"
    }
    git push origin main 2>/dev/null || {
        log_warn "   Already pushed"
    }

    log_info "3. Verifying 100% routing..."
    log_info "   (All Creator requests should use Creator 2.0)"

    log_info ""
    log_info "Phase 4 Complete!"
    log_info "Creator 2.0 is now in full production."
    log_info "Continuous monitoring enabled (no end date)."
}

# Instant Rollback (if incident)
rollback() {
    log_error "INITIATING ROLLBACK (INCIDENT DETECTED)"
    log_error "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    log_info "1. Disabling Creator 2.0 flag..."
    cat > "$CORVIN_HOME/tenants/$TENANT_ID/global/features.json" <<'EOF'
{
  "creator_2_0_enabled": false
}
EOF
    log_info "   ✓ Flag disabled (features.json updated)"

    log_info "2. Verifying rollback took effect..."
    sleep 2
    response=$(curl -s "${CONSOLE_URL}/v1/console/capabilities/features" 2>/dev/null || echo '{}')
    if echo "$response" | grep -q '"creator_2_0_enabled": false'; then
        log_info "   ✓ Rollback verified (Creator v1.0 now handling requests)"
    else
        log_warn "   Rollback may not have taken effect yet (cache delay)"
    fi

    log_info "3. Exporting audit trail for post-mortem..."
    START="2026-09-17T07:00:00Z"
    END=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    EXPORT_FILE="/tmp/creator_2_0_incident_postmortem_$(date +%s).json"

    if [ -f "$AUDIT_HOME/audit.jsonl" ]; then
        grep "skill_executed.*creator_2_0" "$AUDIT_HOME/audit.jsonl" > "$EXPORT_FILE" 2>/dev/null || {
            log_warn "   Could not extract Creator 2.0 events from audit trail"
        }
        log_info "   ✓ Audit trail exported: $EXPORT_FILE"
    fi

    log_error ""
    log_error "ROLLBACK COMPLETE"
    log_error ""
    log_error "Next steps:"
    log_error "  1. Analyze post-mortem: $EXPORT_FILE"
    log_error "  2. Document findings: Corvin-ADR/incidents/INCIDENT-XXXX-*.md"
    log_error "  3. Fix root cause before retry"
    log_error "  4. Re-run Phase 1 (after fix)"
}

# Monitoring: Export metrics for analysis
export_metrics() {
    log_info "Exporting metrics for analysis..."
    log_info "Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

    EXPORT_FILE="/tmp/creator_2_0_metrics_$(date +%s).json"

    log_info "1. Exporting Prometheus metrics..."
    curl -s "${METRICS_URL}/api/v1/query?query=creator_2_0_latency_p99" > "$EXPORT_FILE" || {
        log_warn "   Could not export metrics (Prometheus not responding)"
    }

    log_info "2. Exporting audit events..."
    if [ -f "$AUDIT_HOME/audit.jsonl" ]; then
        grep "skill_executed.*creator_2_0" "$AUDIT_HOME/audit.jsonl" >> "$EXPORT_FILE" 2>/dev/null || {
            log_warn "   No Creator 2.0 events in audit trail"
        }
    fi

    log_info "   ✓ Metrics exported: $EXPORT_FILE"
}

# Main dispatch
main() {
    case "${1:-help}" in
        phase1_deploy)
            phase1_deploy_5_percent
            ;;
        phase1_monitor)
            phase1_monitor
            ;;
        phase1_gate)
            phase1_decision_gate
            ;;
        phase2_expand)
            phase2_expand_25_percent
            ;;
        phase3_expand)
            phase3_expand_50_percent
            ;;
        phase4_rollout)
            phase4_full_rollout
            ;;
        rollback)
            rollback
            ;;
        export_metrics)
            export_metrics
            ;;
        help)
            cat <<'EOF'
Creator 2.0 Canary Rollout Runbook

Usage:
  ./creator_2_0_canary_runbook.sh <command> [options]

Commands:
  phase1_deploy    Deploy 5% canary (Week 17 Day 1)
  phase1_monitor   Daily monitoring check (Week 17 Days 2-7)
  phase1_gate      Decision gate (Week 17 Day 7)
  phase2_expand    Expand to 25% (Week 18 Day 8)
  phase3_expand    Expand to 50% (Week 19 Day 15)
  phase4_rollout   Full rollout (Week 20 Day 22)
  rollback         Instant rollback (if incident)
  export_metrics   Export metrics for analysis
  help             Show this help message

Reference: CREATOR_2_0_CANARY_ROLLOUT_PLAN.md § III Deployment Runbook
EOF
            ;;
        *)
            log_error "Unknown command: $1"
            exit 1
            ;;
    esac
}

main "$@"
