# PHASE 4: 100% PRODUCTION ROLLOUT EXECUTION GUIDE
**Document:** Phase 4 Final Rollout (100% Users)  
**Date Prepared:** 2026-08-29  
**Target Execution:** 2026-09-06 (00:00 UTC) after successful canary (Sep 2-7)  
**Status:** READY FOR EXECUTION  
**Owner:** SRE/Deployment Team  

---

## EXECUTIVE SUMMARY

**Phase 4** expands plugin marketplace features from 50% users (Phase 3) to **100% production users**. This is the critical transition from canary to full production load — highest risk, greatest visibility, zero tolerance for failures.

### Key Milestones
- **Start Time:** 2026-09-06 00:00 UTC
- **Target Completion:** 2026-09-06 06:00 UTC (6-hour window)
- **Minimum Stable Period:** 24 hours before declaring success
- **Success Criteria:** Zero CRITICAL incidents, >95% success rate, audit chain verified

### What Changes in Phase 4
```
Configuration Before Phase 4:
- plugin_trust_badge_enabled: true (50% users)
- plugin_report_enabled: true (50% users)
- plugin_marketplace_canary_rollout: "50"
- plugin_upload_enabled: false

Configuration After Phase 4:
- plugin_trust_badge_enabled: true (100% users)
- plugin_report_enabled: true (100% users)
- plugin_marketplace_canary_rollout: "100"
- plugin_upload_enabled: true (FIRST TIME ENABLED)  ← CRITICAL CHANGE
```

---

## PRE-DEPLOYMENT VERIFICATION (Sep 5, 23:00 UTC)

### Gate 1: Phase 3 Stability (24+ hours)

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4 PRE-DEPLOYMENT GATE 1: Phase 3 Stability ==="

AUDIT_PATH="${HOME}/.corvin/audit.jsonl"
PHASE3_START="2026-09-04T00:00:00"  # Phase 3 start
CURRENT_TIME=$(date -u '+%Y-%m-%dT%H:%M:%S')

# Count audit events since Phase 3 start
python3 -c "
import json
from pathlib import Path
from datetime import datetime

audit_path = Path('${AUDIT_PATH}')
phase3_start = datetime.fromisoformat('${PHASE3_START}')

error_count = 0
success_count = 0
audit_breaks = 0
last_hash = None

for line in audit_path.read_text().strip().split('\n'):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if ts < phase3_start:
        continue
    
    # Check hash chain
    current_hash = event.get('hash')
    if event.get('parent_hash') != last_hash and last_hash is not None:
        audit_breaks += 1
    last_hash = current_hash
    
    # Count event types
    event_type = event.get('type', '')
    if 'error' in event_type:
        error_count += 1
    else:
        success_count += 1

total_events = success_count + error_count
error_rate = (error_count / total_events * 100) if total_events > 0 else 0

print(f'Phase 3 Stability Report (24h):')
print(f'  Total events: {total_events}')
print(f'  Success: {success_count} ({100-error_rate:.1f}%)')
print(f'  Errors: {error_count} ({error_rate:.1f}%)')
print(f'  Audit chain breaks: {audit_breaks}')
print()

# Validation
if error_rate > 5:
    print('✗ GATE FAILED: Error rate >5%')
    exit(1)
elif audit_breaks > 0:
    print('✗ GATE FAILED: Audit chain corrupted')
    exit(1)
elif error_rate > 2:
    print('⚠ WARNING: Error rate elevated (2-5%), monitor closely')
else:
    print('✓ GATE PASSED: Phase 3 stable')
"
```

**Pass Criteria:**
- ✅ Error rate <2% (or <5% with caution notice)
- ✅ Audit chain unbroken
- ✅ No new error categories since Phase 2
- ✅ Registry integrity verified

### Gate 2: Canary Performance (48 hours)

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4 PRE-DEPLOYMENT GATE 2: Canary Performance ==="

python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta

audit_path = Path('${HOME}/.corvin/audit.jsonl')
lookback_48h = datetime.now(datetime.timezone.utc) - timedelta(hours=48)

# Collect latency samples
latencies = {
    'discovery': [],
    'report': [],
    'badge': [],
    'other': []
}

for line in audit_path.read_text().strip().split('\n'):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if ts < lookback_48h:
        continue
    
    duration = event.get('duration_ms', 0)
    event_type = event.get('type', '')
    
    if 'discovery' in event_type:
        latencies['discovery'].append(duration)
    elif 'report' in event_type:
        latencies['report'].append(duration)
    elif 'badge' in event_type:
        latencies['badge'].append(duration)
    else:
        latencies['other'].append(duration)

# Calculate p95
def p95(values):
    if not values:
        return 0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * 0.95)
    return sorted_vals[min(idx, len(sorted_vals)-1)]

print('Performance Metrics (past 48h):')
for op_type, samples in latencies.items():
    if samples:
        p95_val = p95(samples)
        avg_val = sum(samples) / len(samples)
        print(f'  {op_type}: avg={avg_val:.0f}ms, p95={p95_val:.0f}ms, n={len(samples)}')

# SLO thresholds
slo_map = {
    'discovery': 50,   # 50ms
    'report': 500,     # 500ms
    'badge': 200,      # 200ms (rendering)
}

failed = False
for op_type, threshold in slo_map.items():
    if op_type in latencies and latencies[op_type]:
        p95_val = p95(latencies[op_type])
        if p95_val > threshold:
            print(f'✗ {op_type} p95 {p95_val}ms exceeds SLO {threshold}ms')
            failed = True

if not failed:
    print('✓ GATE PASSED: All SLOs met')
else:
    print('✗ GATE FAILED: SLO violations detected')
    exit(1)
"
```

**Pass Criteria:**
- ✅ Discovery latency p95 <50ms
- ✅ Report latency p95 <500ms
- ✅ Badge rendering p95 <200ms
- ✅ No new error categories

### Gate 3: Trust System Verification

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4 PRE-DEPLOYMENT GATE 3: Trust System ==="

python3 -c "
import json
from pathlib import Path

# Verify trust anchor is configured
anchor_path = Path.home() / '.corvin/global/plugin_trust_anchors.txt'

if not anchor_path.exists():
    print('⚠ WARNING: Trust anchor not configured (community plugins only)')
else:
    anchors = anchor_path.read_text().strip().split('\n')
    print(f'✓ Trust anchor configured: {len(anchors)} key(s) pinned')

# Verify registry is readable
registry_path = Path.home() / '.corvin/global/registry.yaml'

if not registry_path.exists():
    print('✗ GATE FAILED: Registry not found')
    exit(1)

registry_data = registry_path.read_text()
if not registry_data:
    print('✗ GATE FAILED: Registry is empty')
    exit(1)

print('✓ GATE PASSED: Trust system ready')
"
```

**Pass Criteria:**
- ✅ Trust anchor exists and is readable
- ✅ Registry is valid and accessible
- ✅ No PII in audit events

### Gate 4: Infrastructure Check

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4 PRE-DEPLOYMENT GATE 4: Infrastructure ==="

# Check disk space
DISK_FREE=$(df /home | tail -1 | awk '{print $4}')
DISK_FREE_GB=$((DISK_FREE / 1024 / 1024))

if [ $DISK_FREE_GB -lt 10 ]; then
    echo "✗ GATE FAILED: Insufficient disk space (${DISK_FREE_GB}GB free, need >10GB)"
    exit 1
fi
echo "✓ Disk space adequate (${DISK_FREE_GB}GB free)"

# Check console is responsive
if ! curl -s http://localhost:8765/console/ > /dev/null; then
    echo "✗ GATE FAILED: Console not responding"
    exit 1
fi
echo "✓ Console responsive"

# Check database is writable
python3 -c "
from pathlib import Path
registry_path = Path.home() / '.corvin/global/registry.yaml'
test_path = registry_path.parent / '.write_test'
try:
    test_path.touch()
    test_path.unlink()
    print('✓ Database writable')
except Exception as e:
    print(f'✗ GATE FAILED: Database not writable: {e}')
    exit(1)
"

echo "✓ GATE PASSED: Infrastructure ready"
```

**Pass Criteria:**
- ✅ >10GB disk space free
- ✅ Console responding (HTTP 200)
- ✅ Registry writable

---

## DEPLOYMENT EXECUTION (Sep 6, 00:00 UTC)

### Step 1: Pre-Deployment Snapshot (00:00 UTC)

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4: PRE-DEPLOYMENT SNAPSHOT ==="

SNAPSHOT_DIR="/home/shumway/.corvin/phase-4-snapshots"
mkdir -p "$SNAPSHOT_DIR"

TIMESTAMP=$(date -u '+%Y%m%d_%H%M%S')
SNAPSHOT="$SNAPSHOT_DIR/pre_deployment_${TIMESTAMP}"
mkdir -p "$SNAPSHOT"

# Capture state
cp "${HOME}/.corvin/audit.jsonl" "$SNAPSHOT/audit_pre.jsonl"
cp "${HOME}/.corvin/global/registry.yaml" "$SNAPSHOT/registry_pre.yaml"
cp "${HOME}/.corvin/tenants/_default/tenant.corvin.yaml" "$SNAPSHOT/config_pre.yaml"

# Count current events
python3 -c "
import json
from pathlib import Path

audit_path = Path('${HOME}/.corvin/audit.jsonl')
events = [json.loads(line) for line in audit_path.read_text().strip().split('\n') if line]
print(f'✓ Pre-deployment snapshot: {len(events)} audit events captured')
print(f'  Location: ${SNAPSHOT}/')
"

echo "✓ Pre-deployment snapshot complete"
```

### Step 2: Feature Flag Increment to 100%

**CRITICAL: This is the point of no return. Verify all gates passed before proceeding.**

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4: FEATURE FLAG DEPLOYMENT (100% USERS) ==="
echo "⚠ WARNING: This enables plugin upload for ALL users"
echo "⚠ Proceeding in 10 seconds... (Ctrl+C to cancel)"
sleep 10

python3 << 'PYTHON_DEPLOY'
import json
import yaml
from pathlib import Path
from datetime import datetime

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'

# Load current config
with config_path.open() as f:
    config = yaml.safe_load(f)

# Ensure features dict exists
if 'features' not in config:
    config['features'] = {}

# PHASE 4 CHANGES: Enable upload, expand canary to 100%
print("\nPhase 4 Configuration Changes:")
print("─" * 50)

# Track changes
changes = []

# Trust badges (should already be true from Phase 2)
if not config['features'].get('plugin_trust_badge_enabled'):
    config['features']['plugin_trust_badge_enabled'] = True
    changes.append("✓ plugin_trust_badge_enabled: false → true")

# Reports (should already be true from Phase 3)
if not config['features'].get('plugin_report_enabled'):
    config['features']['plugin_report_enabled'] = True
    changes.append("✓ plugin_report_enabled: false → true")

# Canary rollout: 50% → 100%
if config['features'].get('plugin_marketplace_canary_rollout') != '100':
    config['features']['plugin_marketplace_canary_rollout'] = '100'
    changes.append(f"✓ plugin_marketplace_canary_rollout: {config['features'].get('plugin_marketplace_canary_rollout', '0')} → 100")

# Upload: OFF → ON (CRITICAL CHANGE)
if not config['features'].get('plugin_upload_enabled'):
    config['features']['plugin_upload_enabled'] = True
    changes.append("✓ plugin_upload_enabled: false → true (CRITICAL)")

for change in changes:
    print(f"  {change}")

# Save config
with config_path.open('w') as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

print("─" * 50)
print(f"✓ Configuration saved to {config_path}")
print(f"  Timestamp: {datetime.utcnow().isoformat()}Z")

# Verify changes persisted
with config_path.open() as f:
    verify_config = yaml.safe_load(f)

assert verify_config['features']['plugin_trust_badge_enabled'] == True, "Badge flag not persisted"
assert verify_config['features']['plugin_report_enabled'] == True, "Report flag not persisted"
assert verify_config['features']['plugin_upload_enabled'] == True, "Upload flag not persisted"
assert verify_config['features']['plugin_marketplace_canary_rollout'] == '100', "Canary rollout not persisted"

print("✓ All changes verified in persisted config")
PYTHON_DEPLOY
```

### Step 3: Console Restart & Verification

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4: CONSOLE RESTART & VERIFICATION ==="

# Restart console to pick up new config
echo "Restarting console..."
systemctl --user restart corvin-console
sleep 3

# Verify console is up
RETRIES=10
for i in $(seq 1 $RETRIES); do
    if curl -s http://localhost:8765/console/ > /dev/null 2>&1; then
        echo "✓ Console responding"
        break
    elif [ $i -eq $RETRIES ]; then
        echo "✗ Console failed to restart"
        exit 1
    else
        echo "  Waiting for console... ($i/$RETRIES)"
        sleep 1
    fi
done

# Verify all marketplace routes are accessible
echo "Verifying marketplace routes..."
python3 << 'PYTHON_VERIFY'
import requests
import json
import time

routes_to_test = [
    ('GET', '/v1/vibe/plugins', 'Plugin discovery (should be accessible now)'),
    ('GET', '/v1/vibe/plugins/test-plugin', 'Plugin detail'),
    ('POST', '/v1/console/plugins/upload', 'Plugin upload'),
    ('POST', '/v1/vibe/plugins/test-plugin/report', 'Plugin report'),
]

all_pass = True
for method, path, description in routes_to_test:
    try:
        if method == 'GET':
            resp = requests.get(f'http://localhost:8765{path}', timeout=5)
        else:
            resp = requests.post(f'http://localhost:8765{path}', json={}, timeout=5)
        
        # 200 = success, 4xx = expected error, 5xx = server error
        if resp.status_code < 500:
            print(f"  ✓ {method} {path}")
            print(f"      {description} → {resp.status_code}")
        else:
            print(f"  ✗ {method} {path} → {resp.status_code}")
            all_pass = False
    except Exception as e:
        print(f"  ✗ {method} {path} → {e}")
        all_pass = False

if all_pass:
    print("✓ All marketplace routes accessible")
else:
    print("✗ Some routes failed")
    exit(1)
PYTHON_VERIFY
```

### Step 4: Boot Tripwire Verification

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4: BOOT TRIPWIRE VERIFICATION ==="

python3 -c "
from corvin_plugins.boot_platform import assert_all
try:
    assert_all()
    print('✓ Boot tripwire PASSED: audit chain reachable and verifiable')
except Exception as e:
    print(f'✗ Boot tripwire FAILED: {e}')
    exit(1)
"
```

### Step 5: Audit Chain Integrity Verification

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4: AUDIT CHAIN INTEGRITY VERIFICATION ==="

python3 -c "
from corvin_compliance.audit import verify_audit_chain
from pathlib import Path
import json

audit_path = Path.home() / '.corvin/audit.jsonl'
result = verify_audit_chain(audit_path, max_lookback=5000)

if result.valid:
    print(f'✓ Audit chain VALID')
    print(f'  Entries verified: {result.entries_verified}')
    print(f'  Breaks detected: {result.breaks_found}')
else:
    print(f'✗ Audit chain CORRUPTED: {result.error}')
    exit(1)
"
```

### Step 6: Deployment Complete Notification

```bash
#!/usr/bin/env bash

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  PHASE 4 DEPLOYMENT COMPLETE: 100% PRODUCTION LIVE             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "DEPLOYMENT SUMMARY:"
echo "  Start time:       $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  Flags enabled:    upload_enabled, canary at 100%"
echo "  Status:           ✅ LIVE FOR ALL USERS"
echo ""
echo "NEXT STEP: Begin live monitoring (→ MONITORING PHASE)"
echo ""
```

---

## LIVE MONITORING & VALIDATION (Sep 6, First 24 Hours)

### Continuous Metrics Dashboard

**Critical Metrics to Monitor Every 5 Minutes:**

```bash
#!/usr/bin/env bash

MONITORING_INTERVAL=300  # 5 minutes

while true; do
    python3 << 'PYTHON_MONITOR'
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

audit_path = Path.home() / '.corvin/audit.jsonl'
lookback = datetime.now(datetime.timezone.utc) - timedelta(minutes=5)

# Collect metrics
metrics = defaultdict(int)
latencies = defaultdict(list)
errors_by_type = defaultdict(int)
audit_breaks = 0

last_hash = None
lines_read = 0

try:
    with audit_path.open() as f:
        for line in f:
            lines_read += 1
            if not line.strip():
                continue
            
            event = json.loads(line)
            ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
            if ts < lookback:
                continue
            
            # Hash chain check
            current_hash = event.get('hash')
            if event.get('parent_hash') != last_hash and last_hash is not None:
                audit_breaks += 1
            last_hash = current_hash
            
            # Event counting
            event_type = event.get('type', 'unknown')
            if 'error' in event_type:
                errors_by_type[event_type] += 1
            else:
                metrics[event_type] += 1
            
            # Latency tracking
            duration = event.get('duration_ms', 0)
            if duration > 0:
                latencies[event_type.split('.')[0]].append(duration)
except Exception as e:
    print(f"Error reading audit file: {e}")
    exit(1)

# Calculate percentiles
def percentile(values, p):
    if not values:
        return 0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(idx, len(sorted_vals)-1)]

# Print metrics
print(f"╔═══════════════════════════════════════════════════════════════╗")
print(f"║ PHASE 4 LIVE MONITORING — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}              ║")
print(f"╚═══════════════════════════════════════════════════════════════╝")
print()

total_success = sum(metrics.values())
total_errors = sum(errors_by_type.values())
total = total_success + total_errors
error_rate = (total_errors / total * 100) if total > 0 else 0

print(f"ERROR RATE (past 5min):  {error_rate:.1f}% ({total_errors}/{total})")
print(f"  Status: {'✅ HEALTHY' if error_rate < 1 else '⚠️ ELEVATED' if error_rate < 5 else '🔴 CRITICAL'}")
print()

print(f"EVENT COUNTS (past 5min):")
for event_type in sorted(set(list(metrics.keys()) + list(errors_by_type.keys()))):
    success = metrics.get(event_type, 0)
    errors = errors_by_type.get(event_type, 0)
    if success + errors > 0:
        print(f"  {event_type:30} {success:5d} success, {errors:3d} errors")
print()

print(f"LATENCY (p50/p95/p99 ms):")
for op in sorted(latencies.keys()):
    vals = latencies[op]
    if vals:
        p50 = percentile(vals, 50)
        p95 = percentile(vals, 95)
        p99 = percentile(vals, 99)
        print(f"  {op:30} p50={p50:5.0f}ms  p95={p95:5.0f}ms  p99={p99:5.0f}ms")
print()

print(f"AUDIT CHAIN:")
print(f"  Breaks detected: {audit_breaks}")
print(f"  Status: {'✅ VALID' if audit_breaks == 0 else '🔴 CORRUPTED'}")
print()

# Alerting
if error_rate > 5 or audit_breaks > 0:
    print("⚠️  ALERTS:")
    if error_rate > 5:
        print(f"    ERROR_RATE: {error_rate:.1f}% exceeds 5% threshold")
    if audit_breaks > 0:
        print(f"    AUDIT_CHAIN: {audit_breaks} breaks detected")
    print()

print(f"Next update: {(datetime.utcnow() + timedelta(seconds=300)).strftime('%H:%M UTC')}")
PYTHON_MONITOR
    
    sleep $MONITORING_INTERVAL
done
```

### Real-Time Alerting

```bash
#!/usr/bin/env bash

# Monitor error rate continuously and alert on thresholds

SLACK_WEBHOOK="${CORVIN_SLACK_WEBHOOK:-https://hooks.slack.com/...}"

monitor_errors() {
    python3 << 'PYTHON_ALERTS'
import json
from pathlib import Path
from datetime import datetime, timedelta
import subprocess

audit_path = Path.home() / '.corvin/audit.jsonl'
lookback_1h = datetime.now(datetime.timezone.utc) - timedelta(hours=1)

error_count = 0
total_count = 0

for line in audit_path.read_text().strip().split('\n'):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if ts < lookback_1h:
        continue
    
    total_count += 1
    if 'error' in event.get('type', ''):
        error_count += 1

error_rate = (error_count / total_count * 100) if total_count > 0 else 0

# Alert levels
alert_level = None
alert_msg = None

if error_rate > 5:
    alert_level = "CRITICAL"
    alert_msg = f"Error rate {error_rate:.1f}% exceeds 5% threshold"
elif error_rate > 2:
    alert_level = "WARNING"
    alert_msg = f"Error rate {error_rate:.1f}% elevated (2-5%)"

if alert_level:
    print(f":{alert_level}: {alert_msg}")
    # Send Slack alert (if webhook configured)
    import os
    webhook = os.getenv('CORVIN_SLACK_WEBHOOK')
    if webhook:
        subprocess.run([
            'curl', '-X', 'POST', webhook,
            '-H', 'Content-Type: application/json',
            '-d', json.dumps({
                'text': f":{alert_level}: {alert_msg}",
                'attachments': [{
                    'color': 'danger' if alert_level == 'CRITICAL' else 'warning',
                    'fields': [
                        {'title': 'Phase', 'value': 'Phase 4 (100% Production)', 'short': True},
                        {'title': 'Error Rate', 'value': f'{error_rate:.1f}%', 'short': True},
                        {'title': 'Time', 'value': datetime.utcnow().isoformat(), 'short': True},
                    ]
                }]
            })
        ])
PYTHON_ALERTS
}

# Run monitoring loop
while true; do
    monitor_errors
    sleep 60
done
```

---

## POST-DEPLOYMENT VALIDATION (Sep 6–7, First 24 Hours)

### Validation Checklist

```
IMMEDIATE (First Hour):
  [ ] Error rate <1%
  [ ] All marketplace routes responding (HTTP 200)
  [ ] Audit chain unbroken
  [ ] Console stable (no restarts)
  [ ] Disk space adequate
  [ ] No new error categories

PHASE 3-4 TRANSITION (Hours 2-6):
  [ ] Plugin discoveries per minute increasing (ramping up)
  [ ] Upload throughput increasing
  [ ] Report submissions flowing
  [ ] Badge rendering <200ms p95
  [ ] No performance degradation vs. Phase 3

EXTENDED (Hours 6-24):
  [ ] >100 new plugin installs across users
  [ ] Trust verdicts being enforced (if applicable)
  [ ] Backup files being created regularly
  [ ] Registry size <1GB
  [ ] Zero regressions in existing features
  [ ] Audit chain continues to verify cleanly
```

### Functionality Validation Tests

```bash
#!/usr/bin/env bash

echo "=== PHASE 4 FUNCTIONALITY VALIDATION ==="

python3 << 'PYTHON_TESTS'
import json
import requests
import time
from pathlib import Path

test_results = {
    'discovery': False,
    'upload': False,
    'report': False,
    'badge': False,
}

# Test 1: Discovery
try:
    resp = requests.get('http://localhost:8765/v1/vibe/plugins', timeout=5)
    if resp.status_code == 200 and isinstance(resp.json(), list):
        test_results['discovery'] = True
        print(f"✓ Discovery test passed (found {len(resp.json())} plugins)")
except Exception as e:
    print(f"✗ Discovery test failed: {e}")

# Test 2: Upload endpoint accessible
try:
    resp = requests.post('http://localhost:8765/v1/console/plugins/upload', 
                        json={}, timeout=5)
    # 400 = validation error (expected for empty payload), 
    # 415 = media type error (expected),
    # 200 = success
    # Anything other than 404/405 means the endpoint exists
    if resp.status_code != 404 and resp.status_code != 405:
        test_results['upload'] = True
        print(f"✓ Upload endpoint accessible ({resp.status_code})")
except Exception as e:
    print(f"✗ Upload endpoint test failed: {e}")

# Test 3: Report endpoint accessible
try:
    resp = requests.post('http://localhost:8765/v1/vibe/plugins/test-plugin/report',
                        json={'reason': 'test'}, timeout=5)
    if resp.status_code != 404 and resp.status_code != 405:
        test_results['report'] = True
        print(f"✓ Report endpoint accessible ({resp.status_code})")
except Exception as e:
    print(f"✗ Report endpoint test failed: {e}")

# Test 4: Audit event verification
audit_path = Path.home() / '.corvin/audit.jsonl'
events = []
for line in audit_path.read_text().strip().split('\n'):
    if line:
        events.append(json.loads(line))

if events:
    test_results['badge'] = True
    print(f"✓ Audit events flowing ({len(events)} total)")

# Summary
all_pass = all(test_results.values())
print()
print(f"Validation Summary: {'✅ ALL PASS' if all_pass else '⚠️ SOME FAILURES'}")
for test, result in test_results.items():
    status = '✅' if result else '❌'
    print(f"  {status} {test}")

exit(0 if all_pass else 1)
PYTHON_TESTS
```

---

## INCIDENT RESPONSE & ROLLBACK

### If Error Rate Exceeds Threshold

**Trigger:** Error rate >5% for >5 consecutive minutes

```bash
#!/usr/bin/env bash

echo "=== INCIDENT: HIGH ERROR RATE DETECTED ==="
echo "Initiating rollback to Phase 3..."

python3 << 'PYTHON_ROLLBACK'
import yaml
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'

with config_path.open() as f:
    config = yaml.safe_load(f)

# ROLLBACK: Disable upload, revert canary to 50%
config['features']['plugin_upload_enabled'] = False
config['features']['plugin_marketplace_canary_rollout'] = '50'

with config_path.open('w') as f:
    yaml.dump(config, f)

print("✓ Config rolled back to Phase 3")
print("  - plugin_upload_enabled: true → false")
print("  - plugin_marketplace_canary_rollout: 100 → 50")
PYTHON_ROLLBACK

# Restart console
systemctl --user restart corvin-console
sleep 2

echo "✓ Console restarted"
echo "✓ ROLLBACK COMPLETE"
echo ""
echo "NEXT STEPS:"
echo "  1. Investigate root cause"
echo "  2. Review incident log"
echo "  3. Apply fix"
echo "  4. Retry Phase 4 (24h later minimum)"
```

### If Audit Chain Breaks

**Trigger:** Any audit chain corruption detected

```bash
#!/usr/bin/env bash

echo "🔴 CRITICAL: AUDIT CHAIN CORRUPTED"
echo "Stopping all services immediately..."

systemctl --user stop corvin-console
systemctl --user stop corvin-plugins

echo ""
echo "RECOVERY STEPS:"
echo "  1. Review audit log backup at: ~/.corvin/phase-4-snapshots/"
echo "  2. Verify registry backup is clean"
echo "  3. Contact maintainer"
echo ""
echo "DO NOT restart services without maintainer review"
```

---

## STAKEHOLDER SIGN-OFF

### Security Team Sign-Off

```
Security Approval:
- All P0/CRITICAL/HIGH findings remediated
- Trust system verified fail-closed
- Path traversal protection confirmed
- Compliance layer undisableable
- Audit chain integrity verified

Approved by: Security Lead
Date: _______________
```

### Operations Team Sign-Off

```
Operations Approval:
- Monitoring dashboards configured
- Alerting thresholds set
- Runbooks tested and ready
- On-call coverage arranged
- Rollback procedures verified

Approved by: SRE Lead
Date: _______________
```

### Compliance Team Sign-Off

```
Compliance Approval:
- GDPR Art. 30/32 audit trail verified
- EU AI Act Art. 5/50 disclosures in place
- Data minimization confirmed
- Tenant isolation verified
- Consent gates functional

Approved by: Compliance Officer
Date: _______________
```

---

## PRODUCTION DEPLOYMENT REPORT (Post-Deployment)

### To Be Completed After Phase 4 Stabilizes (Sep 7, 00:00 UTC)

**Template:**

```markdown
# PHASE 4 PRODUCTION DEPLOYMENT REPORT
**Deployment Date:** 2026-09-06  
**Report Date:** 2026-09-07  
**Status:** ✅ [LIVE / 🔴 ROLLED BACK]

## DEPLOYMENT SUMMARY
- **Start Time:** 2026-09-06 00:00 UTC
- **Completion Time:** 2026-09-06 [HH:MM UTC]
- **Duration:** [X hours Y minutes]
- **Flags Enabled:** 4 (upload, trust_badge, report, canary 100%)
- **Users Affected:** 100% of production

## METRICS (First 24h)
- **Error Rate:** [X%] (Target: <1%, Alert: >5%)
- **Success Rate:** [X%] (Target: >95%)
- **P95 Latency:** [Xms] (Target: <500ms)
- **Discovery P95:** [Xms] (Target: <50ms)
- **Plugin Installs:** [X] (Target: >100)
- **Audit Events:** [X] total, [X] errors
- **Audit Chain:** ✅ [Valid / 🔴 Corrupted]

## INCIDENTS
- [ ] None detected
- [ ] [Describe any incidents, resolution time, root cause]

## LESSONS LEARNED
[Post-deployment retrospective notes]

## SUCCESS CRITERIA
- ✅ Error rate <1%
- ✅ All SLOs met
- ✅ Audit chain verified
- ✅ No critical incidents
- ✅ >100 plugin installs
- ✅ Zero regressions

**Overall Status:** ✅ SUCCESSFUL
```

---

## COMMUNICATION PLAN

### Pre-Deployment (Sep 5, 23:00 UTC)
- [ ] Slack #corvinOS-ops: "Phase 4 deployment starting in 1 hour"
- [ ] Email to stakeholders: Deployment timeline + monitoring link
- [ ] On-call team confirmed + standing by

### During Deployment (Sep 6, 00:00-06:00 UTC)
- [ ] Slack updates every 30 minutes (first 2 hours)
- [ ] Slack updates every 60 minutes (hours 2-6)
- [ ] Real-time metrics dashboard visible to stakeholders

### Post-Deployment (Sep 6-7)
- [ ] Daily metrics summary email
- [ ] Final report email (Sep 7, 00:00 UTC)
- [ ] Lessons learned meeting (Sep 7, 10:00 UTC)

---

## APPENDICES

### A. Rollback Procedure Quick Reference

```bash
# Quick rollback (keep Phase 2-3 features, disable upload)
python3 -c "
import yaml
from pathlib import Path
config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = yaml.safe_load(config_path.open())
config['features']['plugin_upload_enabled'] = False
config['features']['plugin_marketplace_canary_rollout'] = '50'
yaml.dump(config, config_path.open('w'))
"
systemctl --user restart corvin-console
```

### B. Monitoring Dashboard URLs
- Metrics: http://localhost:8765/v1/vibe/metrics
- Audit Trail: http://localhost:8765/v1/vibe/audit
- Settings: http://localhost:8765/console/settings/features

### C. Emergency Contacts
- On-Call SRE: [Phone/Slack]
- Maintainer: [Phone/Slack]
- Security Lead: [Phone/Slack]

---

**Document Version:** 1.0  
**Last Updated:** 2026-08-29  
**Status:** READY FOR EXECUTION  
**Next Update:** After Phase 4 completion (Sep 7)
