# Brain v0.2 Deployment Runbook

**Version:** 1.0  
**Status:** Ready for execution  
**Owner:** Release Engineering  
**Last Updated:** 2026-08-23  

---

## PRE-DEPLOYMENT CHECKLIST (T-24h before Stage 1)

### 1. Verify All Dependencies Locked

```bash
# Check Python version compatibility
python3 --version  # Should be ≥3.10

# Verify lock files
test -f pyproject.toml && echo "✅ pyproject.toml exists"
test -f core/gateway/pyproject.toml && echo "✅ Gateway config exists"

# List all installed versions
pip3 freeze | grep -E "(anthropic|fastapi|pydantic|numpy|scikit)" > /tmp/deps.txt
cat /tmp/deps.txt
```

### 2. Verify Compliance Framework Active

```bash
# Check bot-disclosure is enabled
grep -r "bot_disclosure" core/compliance/ 2>/dev/null | wc -l  # Should be > 0

# Verify audit chain boot tripwire
python3 -c "from core.compliance.corvin_compliance_reports.tripwire import assert_all; print('✅ Tripwire loaded')"

# Test consent gate (deny-by-default)
python3 -c "from core.console.corvin_core.feature_flags import is_enabled; print(is_enabled('nonexistent_flag', 'test_user'))" # Should be False
```

### 3. Backup Audit Trail (Pre-deployment snapshot)

```bash
# Create snapshot
SNAPSHOT_DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p ~/.corvin/backups

# Copy all audit trails
for tenant_dir in ~/.corvin/tenants/*/; do
  tenant=$(basename "$tenant_dir")
  if [ -f "$tenant_dir/audit.jsonl" ]; then
    cp "$tenant_dir/audit.jsonl" ~/.corvin/backups/audit_${tenant}_${SNAPSHOT_DATE}.jsonl
    echo "✅ Backed up tenant=$tenant"
  fi
done

# Verify checksums
sha256sum ~/.corvin/backups/*.jsonl > ~/.corvin/backups/checksums_${SNAPSHOT_DATE}.txt
```

### 4. Health Check: Pre-deployment Baseline

```bash
# Endpoint health
curl -f http://localhost:8765/health
curl -f http://localhost:8000/health

# Prometheus metrics availability
curl -f http://localhost:9090/api/v1/query?query=up | jq '.data.result | length'  # Should be > 0

# Memory baseline
free -m | grep Mem | awk '{print "Mem baseline: " $3 "/" $2 " MB"}'

# Error rate baseline (should be near 0)
curl -s "http://localhost:9090/api/v1/query?query=rate(corvin_errors_total%5B5m%5D)" | jq '.data.result[].value[1]'
```

---

## STAGE 1: CANARY DEPLOYMENT (10% traffic, 24h)

### T+0h: Deploy to Canary

```bash
#!/bin/bash
set -e

# Step 1: Pull latest code
echo "[T+0h] Pulling latest code from main..."
cd /home/shumway/projects/CorvinOS
git fetch origin main
git checkout origin/main

# Step 2: Verify no uncommitted changes in production
if [ -n "$(git status --porcelain)" ]; then
  echo "❌ ABORT: Uncommitted changes detected"
  git status
  exit 1
fi

# Step 3: Tag deployment
DEPLOY_TAG="v0.2-rc1-$(date +%s)"
git tag "$DEPLOY_TAG"
echo "✅ Tagged: $DEPLOY_TAG"

# Step 4: Update version in config
corvin config set spec.version=v0.2-rc1
corvin config set features.canary_pct=10

# Step 5: Restart services
echo "[T+0h] Restarting services..."
systemctl restart corvin-service
kubectl rollout restart deployment/corvin-gateway -n default

# Step 6: Wait for rollout
echo "Waiting for deployment to stabilize (60 seconds)..."
sleep 60

# Step 7: Verify health
echo "[T+0h] Verifying health..."
for i in {1..10}; do
  if curl -f http://localhost:8765/health > /dev/null 2>&1; then
    echo "✅ Health check passed (attempt $i)"
    break
  else
    echo "⏳ Waiting... (attempt $i)"
    sleep 5
  fi
done

# Step 8: Log deployment
echo "[T+0h] Deployment complete. Canary enabled for 10% of tenants."
echo "Next action: Monitor health for 24 hours"
echo ""
echo "Watch dashboard: http://localhost:9090/graph"
echo "Key metrics:"
echo "  - rate(corvin_errors_total[5m]) < 1%"
echo "  - histogram_quantile(0.95, corvin_latency_ms) < 1300"
echo "  - corvin_process_memory_bytes < 600000000"
```

### T+0h to T+24h: Monitor Canary Health

```bash
#!/bin/bash
# Run every 30 seconds for 24 hours (2880 checks)

HEALTH_CHECKS=0
FAILED_CHECKS=0
ERROR_THRESHOLD=0.01  # 1%
LATENCY_THRESHOLD=1300  # ms
MEMORY_THRESHOLD=600000000  # bytes (600MB)

echo "Starting 24-hour canary monitoring (2880 × 30s checks)..."

for iteration in {1..2880}; do
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
  
  # Check 1: Error rate
  ERROR_RATE=$(curl -s "http://localhost:9090/api/v1/query?query=rate(corvin_errors_total%5B5m%5D)" | \
    jq -r '.data.result[0].value[1] // "0"')
  
  if (( $(echo "$ERROR_RATE > $ERROR_THRESHOLD" | bc -l) )); then
    echo "❌ [$TIMESTAMP] ERROR RATE ALERT: $ERROR_RATE (threshold: $ERROR_THRESHOLD)"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
  fi
  
  # Check 2: Latency p95
  LATENCY=$(curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,corvin_latency_ms)" | \
    jq -r '.data.result[0].value[1] // "0"')
  
  if (( $(echo "$LATENCY > $LATENCY_THRESHOLD" | bc -l) )); then
    echo "⚠️  [$TIMESTAMP] LATENCY ALERT: ${LATENCY}ms (threshold: ${LATENCY_THRESHOLD}ms)"
  fi
  
  # Check 3: Memory usage
  MEMORY=$(curl -s "http://localhost:9090/api/v1/query?query=corvin_process_memory_bytes" | \
    jq -r '.data.result[0].value[1] // "0"')
  
  if (( $(echo "$MEMORY > $MEMORY_THRESHOLD" | bc -l) )); then
    echo "⚠️  [$TIMESTAMP] MEMORY ALERT: ${MEMORY} bytes (threshold: ${MEMORY_THRESHOLD})"
  fi
  
  HEALTH_CHECKS=$((HEALTH_CHECKS + 1))
  
  # If 3+ consecutive failures, abort early
  if (( FAILED_CHECKS >= 3 )); then
    echo "❌ CRITICAL: 3 consecutive error-rate failures detected. ABORTING CANARY."
    exit 1
  fi
  
  # Progress update every 2h (240 checks)
  if (( HEALTH_CHECKS % 240 == 0 )); then
    HOURS=$((HEALTH_CHECKS / 240))
    echo "✅ [$TIMESTAMP] Progress: ${HOURS}h / 24h. Health checks: $HEALTH_CHECKS. Failed: $FAILED_CHECKS."
  fi
  
  sleep 30
done

echo ""
echo "✅ CANARY COMPLETE: 24 hours of monitoring passed."
echo "Failed checks: $FAILED_CHECKS"
echo ""
echo "Decision: PROCEED TO STAGE 2 (Early Adopters)"
```

---

## STAGE 2: EARLY ADOPTERS (25% traffic, 24h)

### T+24h: Pre-Stage-2 Analysis

```bash
#!/bin/bash

echo "=== Stage 1 Metrics Analysis (T+24h) ==="
echo ""

# Collect Stage 1 metrics
python3 << 'EOF'
import json
from pathlib import Path
from operator.measurement.analysis import generate_report

metrics_file = Path.home() / ".corvin" / "sessions" / "metrics.jsonl"

if metrics_file.exists():
    report = generate_report(str(metrics_file))
    
    print("Baseline Summary:")
    print(f"  Turns: {report['baseline_summary']['turns']}")
    print(f"  Avg latency: {report['baseline_summary']['avg_latency_ms']:.1f}ms")
    print(f"  Avg tokens saved: {report['baseline_summary']['avg_tokens_saved']}")
    
    print("\nCanary Summary:")
    print(f"  Turns: {report['canary_summary']['turns']}")
    print(f"  Avg latency: {report['canary_summary']['avg_latency_ms']:.1f}ms")
    print(f"  Avg tokens saved: {report['canary_summary']['avg_tokens_saved']}")
    
    print("\nComparison:")
    print(f"  Reduction improvement: {report['comparison']['reduction_improvement_pct']:.1f}%")
    print(f"  Latency delta: {report['comparison']['latency_delta_ms']:.1f}ms")
    print(f"  Recommendation: {report['recommendation']}")
    
    if report['recommendation'] == 'CONTINUE':
        print("\n✅ Stage 1 PASSED. Proceeding to Stage 2.")
        exit(0)
    else:
        print(f"\n⚠️  Stage 1 result: {report['recommendation']}")
        exit(1)
else:
    print("⚠️  Metrics file not found. Check if measurement system is active.")
    exit(1)
EOF
```

### T+24h: Deploy to Early Adopters

```bash
#!/bin/bash
set -e

echo "[T+24h] Deploying Stage 2 (25% Early Adopters)..."

# Update canary percentage
corvin config set features.canary_pct=25

# Restart
kubectl rollout restart deployment/corvin-gateway -n default

# Wait for stability
sleep 60

# Verify
curl -f http://localhost:8765/health > /dev/null && echo "✅ Stage 2 deployed"

# Continue monitoring with same health check script as Stage 1
```

---

## STAGE 3: GRADUAL ROLLOUT (50% traffic, 12h)

```bash
# Follow same pattern as Stage 2
# Pre-check: Analyze Stage 1+2 combined metrics
# Deploy: corvin config set features.canary_pct=50
# Monitor: 12h (faster confidence)
# Gate: Same success criteria
```

---

## STAGE 4: FULL PRODUCTION (100%)

```bash
# Follow same pattern
# Pre-check: Analyze Stage 1+2+3 combined metrics
# Deploy: Remove canary flag (100%)
# Mark as stable: corvin config set spec.version=v0.2 (stable, not rc)
# Establish baseline: Monitor for 48h
```

---

## EMERGENCY ROLLBACK

### Option A: Immediate Rollback (< 5 min)

```bash
#!/bin/bash
set -e

STAGE=$1  # canary|early|gradual|full

echo "🚨 EMERGENCY ROLLBACK FROM $STAGE"

# Step 1: Disable all Phase 1-3 flags
echo "Disabling Phase 1-3 feature flags..."
corvin config set features.per_stage_token_budgeting=false
corvin config set features.memory_confidence_gate_enabled=false
corvin config set features.adaptive_context_routing=false

# Step 2: Restart service
echo "Restarting corvin-service..."
systemctl restart corvin-service

# Step 3: Restart Kubernetes
echo "Rolling back Kubernetes..."
kubectl rollout undo deployment/corvin-gateway -n default
kubectl rollout status deployment/corvin-gateway -n default --timeout=5m

# Step 4: Verify recovery
echo "Verifying health check..."
for i in {1..10}; do
  if curl -f http://localhost:8765/health > /dev/null 2>&1; then
    echo "✅ Service recovered"
    break
  fi
  sleep 1
done

# Step 5: Alert ops team
curl -X POST http://slack-webhook-url \
  -d '{"text": "🚨 CRITICAL: Deployment rolled back from '$STAGE' stage. Incident review required."}'

echo ""
echo "✅ ROLLBACK COMPLETE"
echo ""
echo "Next steps:"
echo "1. Notify ops team (Slack alert sent)"
echo "2. Collect logs: journalctl -u corvin-service > /tmp/corvin_rollback_$(date +%s).log"
echo "3. Schedule incident review (< 24h)"
echo "4. Do NOT re-attempt without root cause analysis"
```

### Option B: Controlled Rollback (If time permits)

```bash
#!/bin/bash

# Similar to Option A but with more diagnostics:
# 1. Collect full logs from failed deployment
# 2. Export metrics for analysis
# 3. Backup audit trail before any changes
# 4. Then execute Option A

echo "Collecting pre-rollback diagnostics..."
DIAG_DIR="/tmp/deployment_diagnostics_$(date +%s)"
mkdir -p "$DIAG_DIR"

# Logs
journalctl -u corvin-service > "$DIAG_DIR/corvin-service.log"

# Metrics
curl -s "http://localhost:9090/api/v1/query?query=up" | jq . > "$DIAG_DIR/prometheus_status.json"

# Audit trail
for tenant_dir in ~/.corvin/tenants/*/; do
  tenant=$(basename "$tenant_dir")
  if [ -f "$tenant_dir/audit.jsonl" ]; then
    cp "$tenant_dir/audit.jsonl" "$DIAG_DIR/audit_${tenant}.jsonl"
  fi
done

echo "✅ Diagnostics collected in: $DIAG_DIR"
echo ""

# Then execute Option A
bash $(dirname $0)/rollback.sh "$1"
```

---

## POST-DEPLOYMENT VERIFICATION

### After Each Stage Completes

```bash
#!/bin/bash

STAGE=$1

echo "=== Post-Deployment Verification (Stage: $STAGE) ==="

# 1. Service health
echo ""
echo "1. Service Health Check..."
curl -f http://localhost:8765/health && echo "✅ Core service healthy"
curl -f http://localhost:8000/health && echo "✅ API service healthy"

# 2. Audit chain integrity
echo ""
echo "2. Audit Chain Integrity..."
python3 << 'EOF'
from core.compliance.audit_chain import AuditChainVerifier
from pathlib import Path

verifier = AuditChainVerifier()
for tenant_dir in Path.home().glob(".corvin/tenants/*/"):
    audit_file = tenant_dir / "audit.jsonl"
    if audit_file.exists():
        is_valid = verifier.verify_chain(str(audit_file))
        status = "✅" if is_valid else "❌"
        print(f"{status} {tenant_dir.name}: audit chain valid")
EOF

# 3. Metrics health
echo ""
echo "3. Metrics Health..."
curl -s "http://localhost:9090/api/v1/query?query=rate(corvin_errors_total%5B5m%5D)" | jq -r '.data.result[0].value[1] // "0"' | \
  awk '{if ($1 < 0.01) print "✅ Error rate OK: " $1; else print "❌ Error rate HIGH: " $1}'

# 4. Feature flag state
echo ""
echo "4. Feature Flag State..."
python3 << 'EOF'
from core.console.corvin_core.feature_flags import get_all_flags

flags = get_all_flags()
for flag_name, is_enabled in flags.items():
    if "phase_1" in flag_name or "phase_2" in flag_name or "phase_3" in flag_name:
        status = "ON" if is_enabled else "OFF"
        print(f"  {flag_name}: {status}")
EOF

echo ""
echo "✅ Post-deployment verification complete"
```

---

## MONITORING DASHBOARD SETUP

### Grafana Dashboard (Optional, for visual monitoring)

```bash
# Import pre-configured dashboard
curl -X POST http://localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -d @docs/deployment/grafana-dashboard-brain-v0.2.json

# Dashboard shows:
# - Error rate (red alert if > 1%)
# - Latency p95 (yellow if > 1200ms, red if > 1300ms)
# - Memory usage (yellow if > 500MB, red if > 600MB)
# - Token savings comparison (canary vs. baseline)
# - Circuit breaker status
```

---

## COMMON ISSUES & FIXES

### Issue 1: "Service failed to start" (audit chain corruption)

```bash
# Check bootstrap tripwire error
journalctl -u corvin-service | grep "audit_chain"

# If corrupted:
1. Do NOT force-restart
2. Backup: cp -r ~/.corvin ~/corvin-backup-$(date +%s)
3. Restore: cp ~/corvin-backup-*/tenants/*/audit.jsonl ~/.corvin/tenants/*/
4. Try restart: systemctl restart corvin-service
5. If still fails: contact maintainer (escalation)
```

### Issue 2: "Memory usage increasing over time" (possible leak)

```bash
# Isolate the culprit
1. Note memory baseline
2. Disable Phase 2: corvin config set features.per_stage_token_budgeting=false
3. Wait 30 min, check memory
4. If stable: Phase 2 is culprit
5. If still growing: try disabling Phase 1
6. Report finding to engineering team
```

### Issue 3: "Latency suddenly increased" (performance regression)

```bash
# Check which query is slow
1. Enable debug logging: corvin config set spec.log_level=debug
2. Restart: systemctl restart corvin-service
3. Collect 1h of logs
4. Look for slow operations in audit trail
5. Compare against baseline from pre-deployment

# If Phase 2-related:
corvin config set features.per_stage_token_budgeting=false
systemctl restart corvin-service
# Should see latency drop back to baseline
```

---

## SIGN-OFF & APPROVAL GATE

Before proceeding to the next stage, **Ops Lead must explicitly approve**:

```bash
# Create approval artifact
cat > /tmp/stage_${STAGE}_approval.txt << EOF
Stage: $STAGE
Timestamp: $(date -Iseconds)
Operator: $USER
Metrics Reviewed: YES / NO
Health Checks Passed: YES / NO
No Critical Issues: YES / NO
Approval: APPROVED / NOT APPROVED

Notes:
(Operator writes any concerns or observations here)

Signature: _____________________
EOF

# File must be reviewed by Release Manager before next stage
cat /tmp/stage_${STAGE}_approval.txt
```

---

**End of Runbook**  
**For questions, contact: ops-team@corvin.ai**
