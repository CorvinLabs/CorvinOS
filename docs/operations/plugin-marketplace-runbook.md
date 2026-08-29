# Plugin Marketplace Production Deployment Runbook (ADR-0249)

**Status:** Operational guide for production deployment  
**Last Updated:** 2026-08-28  
**Scope:** Plugin marketplace feature (trust, governance, upload, install) — Phase 6 rollout  
**Audience:** Operators, SREs, on-call engineering  

---

## Table of Contents

1. [Pre-Deployment Verification](#pre-deployment-verification)
2. [Dark Ship & Gradual Rollout](#dark-ship--gradual-rollout)
3. [Monitoring & Health Checks](#monitoring--health-checks)
4. [Troubleshooting Guide](#troubleshooting-guide)
5. [Recovery Procedures](#recovery-procedures)
6. [Rollback Procedures](#rollback-procedures)
7. [Post-Deployment Validation](#post-deployment-validation)
8. [Operational Checklists](#operational-checklists)

---

## Pre-Deployment Verification

### Stage 0: Boot Tripwire & Call-Site Verification

The plugin marketplace feature depends on CorvinOS's core compliance tripwire. Before any deployment, verify the boot sequence reaches the Stage 0 audit checkpoint.

**Check: Audit chain reachability**

```bash
#!/usr/bin/env bash

# Test: Verify Stage 0 boot tripwire calls the audit writer
python3 -c "
from corvin_plugins.boot_platform import assert_all
from pathlib import Path
import sys

try:
    assert_all()
    print('✓ Stage 0 audit tripwire OK — core audit writer reachable')
    sys.exit(0)
except Exception as e:
    print(f'✗ Stage 0 FAILED: {e}')
    print('  Deployment must NOT proceed — core audit is unreachable')
    sys.exit(1)
"
```

**Expected outcome:** Exit code 0, message confirms tripwire success.

**Check: Plugin marketplace call sites**

Verify the plugin marketplace routes are wired into the console app:

```bash
#!/usr/bin/env bash

# Test: Verify plugin marketplace routes exist in console app
python3 -c "
from corvin_console.app import app
import json

# Enumerate all routes
routes = [r.rule for r in app.url_map.iter_rules() if '/plugins' in r.rule]

marketplace_routes = [
    '/v1/vibe/plugins',
    '/v1/vibe/plugins/<plugin_id>',
    '/v1/vibe/plugins/<plugin_id>/report',
    '/v1/console/plugins/upload'
]

missing = [r for r in marketplace_routes if not any(m in routes for m in [r])]

if missing:
    print(f'✗ Missing routes: {missing}')
    exit(1)

print(f'✓ All {len(marketplace_routes)} plugin marketplace routes registered')
print('  Routes:')
for r in marketplace_routes:
    print(f'    {r}')
"
```

**Expected outcome:** All 4 routes registered and callable.

### Security Pre-Checks

**Check 1: Trust anchor is configured (if enforcement enabled)**

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_plugins.trust import load_trust_anchors
from pathlib import Path
from corvin_compliance.tripwire import check_enforcement_flag

home = Path.home()
anchors = load_trust_anchors(home / '.corvin')
enforcement = check_enforcement_flag()

if enforcement and not anchors:
    print('✗ CRITICAL: Trust enforcement ON but no anchors configured')
    print('  Set up plugin_trust_anchors.txt before deploying')
    exit(1)

if enforcement:
    print(f'✓ Trust enforcement enabled with {len(anchors)} anchor(s)')
else:
    print('✓ Trust enforcement disabled (ship-dark mode)')
"
```

**Expected outcome:** Anchors configured (or enforcement off), no critical errors.

**Check 2: Feature flag gating**

Verify the feature flags are correctly configured for dark ship:

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

# Load default tenant config
config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Required flags for marketplace
required_flags = [
    'plugin_upload_enabled',
    'plugin_report_enabled',
    'plugin_trust_badge_enabled'
]

print('Feature flag status:')
for flag in required_flags:
    status = config.features.get(flag, False)
    status_str = '✓ ON' if status else '✗ OFF (dark ship)'
    print(f'  {flag}: {status_str}')

# All should be OFF for dark ship
all_off = all(config.features.get(f, False) == False for f in required_flags)
if all_off:
    print('✓ All marketplace flags OFF — dark ship ready')
else:
    print('✗ WARNING: Some flags already ON — review intended rollout schedule')
"
```

**Expected outcome:** All flags OFF for dark ship deployment.

### Performance & Capacity Checks

**Check 1: Registry size and performance baseline**

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_plugins.state import TenantRegistry
from pathlib import Path
import time

# Load registry and measure
registry_path = Path.home() / '.corvin/tenants/_default/plugins/registry.yaml'
tenant_id = '_default'

start = time.time()
registry = TenantRegistry.load(tenant_id)
load_time = (time.time() - start) * 1000  # ms

plugin_count = len(registry.plugins)
size_kb = registry_path.stat().st_size / 1024

print(f'Registry baseline:')
print(f'  Plugins installed: {plugin_count}')
print(f'  File size: {size_kb:.1f} KB')
print(f'  Load time: {load_time:.1f} ms')

# Acceptance criteria
if load_time > 100:
    print(f'✗ WARNING: Load time {load_time:.1f} ms > 100 ms threshold')
if plugin_count > 1000:
    print(f'✗ WARNING: {plugin_count} plugins may stress audit trail')

if load_time <= 100 and plugin_count <= 1000:
    print('✓ Registry size acceptable for deployment')
"
```

**Expected outcome:** Load time <100ms, <1000 plugins, no warnings.

**Check 2: Disk space for backups**

Plugin registry backups are created before each mutation. Verify adequate disk space:

```bash
#!/usr/bin/env bash

python3 -c "
import shutil
from pathlib import Path

plugin_dir = Path.home() / '.corvin/tenants/_default/plugins'
stat = shutil.disk_usage(plugin_dir)

free_mb = stat.free / (1024 * 1024)
registry_size = (plugin_dir / 'registry.yaml').stat().st_size / 1024

# Need at least 10x registry size for headroom
required_mb = (registry_size * 10) / 1024

print(f'Disk space check:')
print(f'  Free space: {free_mb:.1f} MB')
print(f'  Required (10x registry): {required_mb:.1f} MB')

if free_mb < required_mb:
    print(f'✗ CRITICAL: Insufficient disk space')
    exit(1)

print(f'✓ Adequate disk space ({free_mb:.0f} MB available)')
"
```

**Expected outcome:** Free space ≥ 10x registry size, no warnings.

### Audit Trail & Compliance Checks

**Check 1: Audit chain integrity**

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_compliance.audit import verify_audit_chain
from pathlib import Path

audit_path = Path.home() / '.corvin/audit.jsonl'

try:
    result = verify_audit_chain(audit_path, max_lookback=1000)
    if result.valid:
        print(f'✓ Audit chain valid (last {result.entries_verified} entries verified)')
    else:
        print(f'✗ Audit chain corrupted: {result.error}')
        exit(1)
except Exception as e:
    print(f'✗ Audit verification failed: {e}')
    exit(1)
"
```

**Expected outcome:** Audit chain valid, entries verified.

**Check 2: Compliance baseline**

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_compliance import COMPLIANCE_BASELINE

print('Compliance baseline status:')
for mechanism, config in COMPLIANCE_BASELINE.items():
    status = '✓' if config['enabled'] else '✗'
    print(f'  {status} {mechanism}')

critical_mechanisms = ['audit_hash_chain', 'consent_gate', 'path_gate']
all_enabled = all(COMPLIANCE_BASELINE.get(m, {}).get('enabled') for m in critical_mechanisms)

if all_enabled:
    print('✓ Critical compliance mechanisms enabled')
else:
    print('✗ WARNING: Some critical mechanisms disabled')
    exit(1)
"
```

**Expected outcome:** All critical mechanisms enabled.

---

## Dark Ship & Gradual Rollout

### Phase 1: Dark Ship (Feature Flags OFF)

The plugin marketplace ships with all features disabled. Users see no UI changes.

**Deploy to production:**

```bash
#!/usr/bin/env bash
set -e

echo "Phase 1: Dark Ship Deployment"
echo "  - Feature flags OFF (marketplace hidden)"
echo "  - Code present but unreachable"
echo "  - No user-visible changes"

# 1. Deploy new console code (with marketplace routes)
git pull origin main
cd core/console/corvin_console/web-next
npm install && npm run build

# 2. Verify no routes are registered yet (flags should prevent mounting)
python3 -c "
from corvin_console.app import app
routes = [r.rule for r in app.url_map.iter_rules() if '/plugins' in r.rule]
public_routes = [r for r in routes if not r.startswith('/_')]
if public_routes:
    print('✗ WARNING: Public plugin routes accessible before flag enable')
    for r in public_routes:
        print(f'    {r}')
else:
    print('✓ Plugin routes not publicly accessible (dark ship verified)')
"

# 3. Restart console in background (systemd --user)
systemctl --user restart corvin-console

# 4. Verify boot and audit tripwire
sleep 2
python3 -c "
from corvin_plugins.boot_platform import assert_all
assert_all()
print('✓ Boot tripwire verified')
"

echo "✓ Phase 1 complete — marketplace code deployed, features dark"
```

**Verification:**
- Console boots successfully
- Audit chain intact
- No public marketplace routes accessible
- `curl http://localhost:8765/v1/vibe/plugins` returns 404 or 403

### Phase 2: Enable 10% (Canary)

After 24h of dark ship stability, enable the feature for 10% of users.

**Configuration:**

```yaml
# ~/.corvin/tenants/_default/tenant.corvin.yaml

spec:
  features:
    plugin_upload_enabled: false      # Still OFF
    plugin_report_enabled: false      # Still OFF
    plugin_trust_badge_enabled: true  # Enable ONLY trust display
    plugin_marketplace_canary_rollout: "10"  # 10% of users
```

**Enable programmatically:**

```bash
#!/usr/bin/env bash
set -e

echo "Phase 2: 10% Canary Rollout"

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Enable trust badge (read-only, low risk)
config.features['plugin_trust_badge_enabled'] = True

# Set canary percentage
config.features['plugin_marketplace_canary_rollout'] = '10'

config.save(config_path)
print('✓ Feature flags updated: trust badge enabled (10% users)')
"

# Reload console
systemctl --user restart corvin-console

echo "✓ Phase 2: 10% canary active"
echo "  - Trust badges visible to ~10% of users"
echo "  - Upload/report still disabled"
echo "  - Monitor for UI errors, audit events"
```

**Monitoring during canary:**

```bash
#!/usr/bin/env bash

# Check for errors in past 30 minutes
python3 -c "
from corvin_compliance.audit import read_audit_trail
from datetime import datetime, timedelta
from pathlib import Path

audit_path = Path.home() / '.corvin/audit.jsonl'
lookback = datetime.now() - timedelta(minutes=30)

errors = []
for event in read_audit_trail(audit_path):
    if event.timestamp < lookback:
        continue
    if 'error' in event.type or event.type == 'plugin.trust_badge_failed':
        errors.append(event)

if errors:
    print(f'✗ {len(errors)} errors in past 30 minutes:')
    for e in errors[:5]:
        print(f'  {e.timestamp} — {e.type}: {e.details}')
else:
    print('✓ No errors in past 30 minutes')
"

# Check plugin upload endpoint is still 404
curl -s -o /dev/null -w '%{http_code}' http://localhost:8765/v1/console/plugins/upload
# Expected: 404 or 403
```

**Duration:** 24h minimum. Success criteria:
- No errors related to `plugin_trust_badge_enabled`
- Audit events recorded normally
- No performance degradation

### Phase 3: Enable 50% (Partial Rollout)

After successful canary, expand to 50% of users. Enable report functionality (read-only).

```yaml
spec:
  features:
    plugin_upload_enabled: false      # Still OFF (no upload yet)
    plugin_report_enabled: true       # Enable reporting
    plugin_trust_badge_enabled: true  # Still ON
    plugin_marketplace_canary_rollout: "50"  # 50% of users
```

```bash
#!/usr/bin/env bash

echo "Phase 3: 50% Rollout (Report enabled)"

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

config.features['plugin_report_enabled'] = True
config.features['plugin_marketplace_canary_rollout'] = '50'

config.save(config_path)
print('✓ Report endpoint enabled for 50% of users')
"

systemctl --user restart corvin-console

# Verify report endpoint is accessible (if you're in the 50%)
python3 -c "
import requests
try:
    resp = requests.post(
        'http://localhost:8765/v1/vibe/plugins/test-plugin/report',
        json={'reason': 'malicious', 'details': 'test report'},
        timeout=5
    )
    if 200 <= resp.status_code < 500:
        print(f'✓ Report endpoint responding ({resp.status_code})')
    else:
        print(f'✗ Report endpoint error ({resp.status_code})')
except Exception as e:
    print(f'✗ Report endpoint unreachable: {e}')
"
```

**Duration:** 24h minimum. Success criteria:
- Report submissions creating audit events
- No errors in report handling
- Audit trail clean and unbroken

### Phase 4: Enable 100% (Full Release)

Final phase: enable all features for all users, including upload.

```yaml
spec:
  features:
    plugin_upload_enabled: true       # Enable upload
    plugin_report_enabled: true       # Already ON
    plugin_trust_badge_enabled: true  # Already ON
    plugin_marketplace_canary_rollout: "100"  # All users
```

```bash
#!/usr/bin/env bash

echo "Phase 4: 100% Rollout (Full release)"

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

config.features['plugin_upload_enabled'] = True
config.features['plugin_marketplace_canary_rollout'] = '100'

config.save(config_path)
print('✓ All marketplace features enabled for 100% of users')
"

systemctl --user restart corvin-console

# Final verification
echo "✓ Phase 4: Full marketplace live"
echo "  - All 4 routes publicly accessible"
echo "  - All features enabled"
echo "  - Begin normal monitoring"
```

---

## Monitoring & Health Checks

### Operational Dashboards

**Dashboard 1: Plugin Marketplace Health**

```bash
#!/usr/bin/env bash

# Real-time health check script
watch -n 5 'python3 -c "
from corvin_plugins.state import TenantRegistry
from corvin_compliance.audit import read_audit_trail
from datetime import datetime, timedelta
from pathlib import Path
import json

# Load registry
registry = TenantRegistry.load(\"_default\")
audit_path = Path.home() / \".corvin/audit.jsonl\"

# Count events in past hour
lookback = datetime.now() - timedelta(hours=1)
plugin_events = 0
report_events = 0
error_events = 0

for line in audit_path.read_text().strip().split(\"\\n\"):
    if not line:
        continue
    event = json.loads(line)
    if datetime.fromisoformat(event[\"timestamp\"].replace(\"Z\", \"+00:00\")) < lookback:
        continue
    if \"plugin\" in event[\"type\"]:
        plugin_events += 1
    if event[\"type\"] == \"plugin.reported\":
        report_events += 1
    if \"error\" in event[\"type\"]:
        error_events += 1

print(f\"Plugin Marketplace Health\")
print(f\"  Plugins installed: {len(registry.plugins)}\")
print(f\"  Events (past hour): {plugin_events}\")
print(f\"  Reports (past hour): {report_events}\")
print(f\"  Errors (past hour): {error_events}\")

if error_events == 0:
    print(f\"✓ Status: Healthy\")
elif error_events < 5:
    print(f\"⚠ Status: Degraded ({error_events} errors)\")
else:
    print(f\"✗ Status: Critical ({error_events} errors)\")
"'
```

**Dashboard 2: Audit Trail Status**

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_compliance.audit import read_audit_trail, verify_audit_chain
from pathlib import Path
import json

audit_path = Path.home() / \".corvin/audit.jsonl\"

# Verify chain
result = verify_audit_chain(audit_path)

# Count event types
event_types = {}
for line in audit_path.read_text().strip().split(\"\\n\"):
    if not line:
        continue
    event = json.loads(line)
    et = event[\"type\"]
    event_types[et] = event_types.get(et, 0) + 1

print(\"Audit Trail Status\")
print(f\"  Chain valid: {result.valid}\")
print(f\"  Entries verified: {result.entries_verified}\")
print(f\"  Total entries: {sum(event_types.values())}\")
print(f\"  Plugin events: {sum(v for k, v in event_types.items() if 'plugin' in k)}\")

if result.valid:
    print(f\"✓ Audit chain integrity verified\")
else:
    print(f\"✗ Audit chain corrupted: {result.error}\")
"
```

### Alert Thresholds

Set up alerts for the following conditions:

| Metric | Threshold | Action |
|--------|-----------|--------|
| Registry load time | >100ms | Page on-call, investigate I/O bottleneck |
| Audit chain breaks | Any | CRITICAL: stop deployment, verify audit writer |
| Upload failures | >5 in 1h | Check disk space, temp file permissions |
| Report submission errors | >10 in 1h | Audit system may be down, check audit trail |
| Trust verification fails | >20 in 1h | Check trust anchor config, verify enforcement flag |
| Plugin enable/disable latency | >1s | May indicate disk I/O or locking contention |

### Prometheus Metrics (Optional)

If instrumented, collect:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: corvin_plugins
    static_configs:
      - targets: ['localhost:9090']
    metrics_path: /metrics/plugins
```

Metrics to export:

```python
# core/plugins/corvin_plugins/metrics.py (new file)

from prometheus_client import Counter, Histogram

plugin_uploads = Counter(
    'plugin_uploads_total',
    'Total plugin uploads',
    ['origin', 'status']
)

plugin_install_time = Histogram(
    'plugin_install_duration_seconds',
    'Plugin installation duration'
)

plugin_reports = Counter(
    'plugin_reports_total',
    'Total plugin reports',
    ['reason']
)

registry_load_time = Histogram(
    'plugin_registry_load_duration_seconds',
    'Plugin registry load time'
)
```

---

## Troubleshooting Guide

### Issue: Plugin routes return 404 even with flags enabled

**Symptoms:**
```
curl http://localhost:8765/v1/vibe/plugins
→ 404 Not Found
```

**Diagnosis:**

```bash
python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

# Check if flags are actually on
config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

print(f'plugin_trust_badge_enabled: {config.features.get(\"plugin_trust_badge_enabled\", False)}')
print(f'plugin_upload_enabled: {config.features.get(\"plugin_upload_enabled\", False)}')
print(f'plugin_report_enabled: {config.features.get(\"plugin_report_enabled\", False)}')

# Check if routes are registered
from corvin_console.app import app
routes = [r.rule for r in app.url_map.iter_rules() if 'plugin' in r.rule]
print(f'Routes found: {routes}')
"
```

**Fix:**

1. If flags are OFF but should be ON, enable them in `tenant.corvin.yaml`
2. Restart console: `systemctl --user restart corvin-console`
3. If routes still don't appear, check console startup logs:
   ```bash
   journalctl --user -u corvin-console -n 50 --no-pager
   ```

### Issue: Upload endpoint accepts file but rejects it as "untrusted"

**Symptoms:**
```
POST /v1/console/plugins/upload
→ 400 Bad Request
"Trust verdict: forged (no valid signature, no community origin)"
```

**Diagnosis:**

```bash
python3 -c "
from corvin_plugins.trust import evaluate_trust_verdict

# Check the plugin manifest
import yaml
manifest = yaml.safe_load(open('plugin.yaml'))

print(f'Origin declared: {manifest.get(\"origin\", \"community\")}')
print(f'Signature present: {\"signature\" in manifest}')

# If origin=vetted and signature present, verify signature
if manifest.get('origin') == 'vetted' and 'signature' in manifest:
    from corvin_plugins.trust import verify_plugin_signature
    try:
        verify_plugin_signature(manifest)
        print('✓ Signature valid')
    except Exception as e:
        print(f'✗ Signature verification failed: {e}')
"
```

**Fixes:**

| Cause | Fix |
|-------|-----|
| Plugin claims `origin: vetted` but has no signature | Add signature to plugin.yaml before upload |
| Signature is invalid or doesn't match trust anchor | Re-sign plugin with correct maintainer key |
| Plugin claims `origin: community` | Allowed by default; requires operator consent |
| `plugin_trust_enforcement` flag ON but `origin: community` | Operator must approve via web UI modal |

### Issue: Audit chain is broken or corrupted

**Symptoms:**
```
verify_audit_chain() → False
Error: "Hash mismatch at entry N"
```

**Diagnosis:**

```bash
python3 -c "
from corvin_compliance.audit import verify_audit_chain
from pathlib import Path

audit_path = Path.home() / '.corvin/audit.jsonl'
result = verify_audit_chain(audit_path)

print(f'Chain valid: {result.valid}')
print(f'Entries verified: {result.entries_verified}')
print(f'Error: {result.error}')
print(f'Failed at entry: {result.failed_at}')

if result.failed_at:
    # Show the problematic entry
    import json
    for i, line in enumerate(audit_path.read_text().split('\\n')):
        if i == result.failed_at and line:
            event = json.loads(line)
            print(f'\\nFailed entry ({i}):')
            print(f'  Type: {event[\"type\"]}')
            print(f'  Hash: {event.get(\"hash\", \"MISSING\")}')
            break
"
```

**Recovery:**

⚠️ **DO NOT edit `audit.jsonl` directly.** Corrupted audit chain is a critical integrity violation.

1. **If corruption is recent** (last few hours), rollback system state:
   ```bash
   # Stop all services
   systemctl --user stop corvin-console corvin-service
   
   # Restore audit backup (if available)
   cp ~/.corvin/audit.jsonl.bak ~/.corvin/audit.jsonl
   
   # Restart
   systemctl --user start corvin-console
   ```

2. **If corruption is widespread**, contact Corvin Labs support with:
   ```bash
   tar czf audit-corruption-report.tar.gz \
     ~/.corvin/audit.jsonl \
     ~/.corvin/audit.jsonl.bak \
     ~/.corvin/tenants/_default/plugins/registry.yaml \
     ~/.corvin/tenants/_default/plugins/registry.yaml.bak
   ```

### Issue: Registry backup not created before mutations

**Symptoms:**
```
Plugin installed, but registry.yaml.bak is not updated
```

**Diagnosis:**

```bash
ls -la ~/.corvin/tenants/_default/plugins/
# Check file timestamps
stat registry.yaml
stat registry.yaml.bak
```

**Fixes:**

1. Check permissions:
   ```bash
   ls -la ~/.corvin/tenants/_default/plugins/
   # Should be: -rw------- (0600)
   ```

2. Ensure directory is writable:
   ```bash
   touch ~/.corvin/tenants/_default/plugins/.write-test
   rm ~/.corvin/tenants/_default/plugins/.write-test
   ```

3. Check available disk space:
   ```bash
   df -h ~/.corvin/tenants/_default/plugins/
   # Must have >= 10 MB free
   ```

### Issue: Plugin report submissions not creating audit events

**Symptoms:**
```
POST /v1/vibe/plugins/my-plugin/report
→ 200 OK (success message)
But audit.jsonl doesn't contain the event
```

**Diagnosis:**

```bash
python3 -c "
from corvin_compliance.audit import read_audit_trail
from datetime import datetime, timedelta
from pathlib import Path

lookback = datetime.now() - timedelta(minutes=10)

audit_path = Path.home() / '.corvin/audit.jsonl'
for line in audit_path.read_text().strip().split('\\n'):
    import json
    if not line:
        continue
    event = json.loads(line)
    if event['type'] == 'plugin.reported':
        print(f'✓ Found report event: {event}')
        exit(0)

print('✗ No plugin.reported events in past 10 minutes')
"
```

**Fixes:**

1. Check audit writer is reachable:
   ```bash
   python3 -c "
   from corvin_plugins.boot_platform import assert_all
   assert_all()
   print('✓ Audit writer reachable')
   "
   ```

2. Check console logs for audit errors:
   ```bash
   journalctl --user -u corvin-console | grep -i "audit" | tail -20
   ```

3. Verify audit trail permissions:
   ```bash
   ls -la ~/.corvin/audit.jsonl
   # Should be: -rw------- (0600)
   ```

---

## Recovery Procedures

### Scenario 1: Registry Corrupted, Backup Available

**Step 1: Detect corruption**

```bash
python3 -c "
from corvin_plugins.state import TenantRegistry

try:
    registry = TenantRegistry.load('_default')
    print('✓ Registry loads successfully')
except Exception as e:
    print(f'✗ Registry corrupted: {e}')
"
```

**Step 2: Auto-recovery (automatic on next load)**

```python
# core/plugins/corvin_plugins/state.py already handles this
# No manual action required if backup exists
```

**Step 3: Verify recovery**

```bash
python3 -c "
from corvin_plugins.state import TenantRegistry
from corvin_compliance.audit import read_audit_trail

registry = TenantRegistry.load('_default')
print(f'✓ Registry recovered: {len(registry.plugins)} plugins')

# Check audit trail for recovery event
for line in Path.home() / '.corvin/audit.jsonl':
    import json
    event = json.loads(line)
    if event['type'] == 'plugin.registry_recovered':
        print(f'✓ Audit event recorded: {event}')
"
```

### Scenario 2: Registry Corrupted, Backup Also Corrupted

**Step 1: Detect both are bad**

```bash
python3 -c "
from corvin_plugins.state import TenantRegistry
from pathlib import Path

try:
    registry = TenantRegistry.load('_default', auto_recover=False)
except Exception as e:
    print(f'Main registry corrupted: {e}')

backup_path = Path.home() / '.corvin/tenants/_default/plugins/registry.yaml.bak'
try:
    import yaml
    yaml.safe_load(backup_path.read_text())
except Exception as e:
    print(f'Backup also corrupted: {e}')
"
```

**Step 2: Manual recovery from previous backups**

```bash
#!/usr/bin/env bash

# Check if daily backups exist (if automated backup system in place)
ls -la ~/.corvin/backups/registry.yaml.* | head -10

# Restore from most recent good backup
restore_from=$(ls -t ~/.corvin/backups/registry.yaml.* | head -1)

echo "Restoring from: $restore_from"
cp "$restore_from" ~/.corvin/tenants/_default/plugins/registry.yaml

# Verify it loads
python3 -c "
from corvin_plugins.state import TenantRegistry
registry = TenantRegistry.load('_default')
print(f'✓ Restored registry: {len(registry.plugins)} plugins')
"
```

**Step 3: Record recovery in audit trail**

```bash
python3 -c "
from corvin_compliance.audit import write_event

write_event('plugin.registry_recovered_manual', {
    'source': 'manual_restore',
    'timestamp': datetime.now().isoformat(),
    'from_backup': 'registry.yaml.bak'
})
"
```

### Scenario 3: Trust Anchor Compromised

**If maintainer key is compromised:**

```bash
#!/usr/bin/env bash

echo "Trusted Key Compromise Recovery"

# Step 1: Revoke old anchor
python3 -c "
from pathlib import Path
anchor_file = Path.home() / '.corvin/global/plugin_trust_anchors.txt'

# Comment out the compromised key
content = anchor_file.read_text()
lines = content.split('\\n')
for i, line in enumerate(lines):
    if 'compromised' in line.lower() or '<key_to_revoke>' in line:
        lines[i] = '# REVOKED (compromised 2026-08-28): ' + lines[i]

anchor_file.write_text('\\n'.join(lines))
print('✓ Revoked compromised key from trust anchors')
"

# Step 2: Generate new key (see Plugin Trust Anchor Procedures)
# ssh-keygen -t ed25519 -f ~/.ssh/corvin-plugins-NEW ...

# Step 3: Add new key to anchors
python3 -c "
from pathlib import Path
# Add new anchor to file
anchor_file = Path.home() / '.corvin/global/plugin_trust_anchors.txt'
anchor_file.write_text(
    anchor_file.read_text() + 
    '# Rotated key (2026-08-28): <new-key-base64url>\\n'
)
print('✓ Added new trust anchor')
"

# Step 4: Sign all vetted plugins with new key
# (See Plugin Trust Anchor Procedures for signing procedure)

# Step 5: Audit the rotation
python3 -c "
from corvin_compliance.audit import write_event
write_event('plugin.trust_anchor_rotated', {
    'reason': 'key_compromise',
    'old_anchor_revoked': True,
    'new_anchor_added': True,
})
"

echo "✓ Trust anchor rotation complete"
echo "  Next: Re-sign all vetted plugins with new key"
```

### Scenario 4: Plugin Marketplace Flag Stuck "ON"

**If you need to disable the feature urgently:**

```bash
#!/usr/bin/env bash

echo "Emergency Disable: Plugin Marketplace"

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Disable all marketplace features
config.features['plugin_upload_enabled'] = False
config.features['plugin_report_enabled'] = False
config.features['plugin_trust_badge_enabled'] = False

config.save(config_path)
print('✓ All marketplace features disabled')
"

# Reload without restarting (if hot-reload supported)
# Or restart console
systemctl --user restart corvin-console

echo "✓ Plugin marketplace disabled"
```

---

## Rollback Procedures

### Quick Rollback: Disable Feature Flags

**Fastest rollback path (minutes):**

```bash
#!/usr/bin/env bash

echo "Rollback: Disable Plugin Marketplace Features"

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Disable all features
for flag in ['plugin_upload_enabled', 'plugin_report_enabled', 'plugin_trust_badge_enabled']:
    config.features[flag] = False

config.save(config_path)
"

systemctl --user restart corvin-console

echo "✓ Plugin marketplace rolled back (features disabled)"
echo "  - UI shows no marketplace"
echo "  - Routes return 404/403"
echo "  - Existing installs continue to work"
```

**Verification:**
```bash
curl -I http://localhost:8765/v1/vibe/plugins
# Should return 404 or 403
```

### Full Rollback: Revert Code

**If code itself has a critical bug:**

```bash
#!/usr/bin/env bash

echo "Full Rollback: Reverting Plugin Marketplace Code"

# 1. Stop services
systemctl --user stop corvin-console corvin-service

# 2. Revert code to previous commit
git revert <commit-hash>  # The marketplace deployment commit

# 3. Rebuild and redeploy
cd core/console/corvin_console/web-next
npm install && npm run build

# 4. Restart services
systemctl --user start corvin-console

# 5. Verify
sleep 2
curl -I http://localhost:8765/v1/vibe/plugins
# Should return 404 (routes don't exist)

echo "✓ Full rollback complete"
```

### Partial Rollback: Revert to Phase N

If rollout is progressing but you want to go back one phase:

```bash
#!/usr/bin/env bash

# From Phase 4 (100%) → Phase 3 (50%)
python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Keep report + badge, disable upload
config.features['plugin_upload_enabled'] = False
config.features['plugin_marketplace_canary_rollout'] = '50'

config.save(config_path)
"

systemctl --user restart corvin-console
echo "✓ Rolled back to Phase 3 (50% users, no upload)"
```

---

## Post-Deployment Validation

### Week 1: Stability Validation

**Automated check (run daily):**

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_compliance.audit import read_audit_trail, verify_audit_chain
from corvin_plugins.state import TenantRegistry
from datetime import datetime, timedelta
from pathlib import Path
import json

audit_path = Path.home() / '.corvin/audit.jsonl'
lookback = datetime.now() - timedelta(days=1)

# Check 1: Registry health
registry = TenantRegistry.load('_default')
print(f'Registry plugins: {len(registry.plugins)}')

# Check 2: Audit chain integrity
result = verify_audit_chain(audit_path, max_lookback=10000)
print(f'Audit chain: {\"✓\" if result.valid else \"✗\"} ({result.entries_verified} entries)')

# Check 3: Error count
errors = 0
uploads = 0
reports = 0

for line in audit_path.read_text().strip().split('\\n'):
    if not line:
        continue
    event = json.loads(line)
    if datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')) < lookback:
        continue
    if 'error' in event['type']:
        errors += 1
    if event['type'] == 'plugin.uploaded':
        uploads += 1
    if event['type'] == 'plugin.reported':
        reports += 1

print(f'\\nPast 24h:')
print(f'  Plugin uploads: {uploads}')
print(f'  Community reports: {reports}')
print(f'  Errors: {errors}')

if errors > 50:
    print(f'\\n✗ WARNING: High error count ({errors})')
    exit(1)

print(f'\\n✓ Deployment validation: PASS')
"
```

### Week 2–4: Feature Validation

**User-facing feature checks:**

```bash
#!/usr/bin/env bash

# Test 1: Trust badges render for all origins
python3 -c "
from corvin_plugins.state import TenantRegistry

registry = TenantRegistry.load('_default')
origins = set()
for plugin in registry.plugins.values():
    origins.add(plugin.origin)

print(f'Plugin origins in registry: {origins}')
print(f'  ✓ All origins represented in test set')
"

# Test 2: Upload workflow end-to-end
python3 -c "
import tempfile
import tarfile
from pathlib import Path

# Create a minimal test plugin
tmpdir = Path(tempfile.mkdtemp())
manifest = tmpdir / 'plugin.yaml'
manifest.write_text('''
id: test-validation-plugin
version: 1.0.0
origin: community
plugin_type: custom
''')

# Pack into tarball
archive = tmpdir / 'test-plugin.tar.gz'
with tarfile.open(archive, 'w:gz') as tar:
    tar.add(manifest, arcname='plugin.yaml')

print(f'✓ Test plugin archive created: {archive}')

# Cleanup
import shutil
shutil.rmtree(tmpdir)
"

# Test 3: Report submission
python3 -c "
import requests
import json

payload = {
    'reason': 'test_validation',
    'details': 'Post-deployment validation test (safe to ignore)'
}

try:
    resp = requests.post(
        'http://localhost:8765/v1/vibe/plugins/test-plugin/report',
        json=payload,
        timeout=5
    )
    if resp.status_code == 200:
        report_id = resp.json().get('report_id')
        print(f'✓ Report submission successful (ID: {report_id})')
    else:
        print(f'✗ Report submission failed ({resp.status_code})')
except Exception as e:
    print(f'✗ Report endpoint error: {e}')
"
```

### Metrics to Track

| Metric | Target | Action If Not Met |
|--------|--------|------------------|
| Audit chain valid | 100% uptime | Investigate corruption |
| Upload success rate | >95% | Check disk space, audit writer |
| Report latency (p99) | <500ms | Check audit system I/O |
| Plugin list load time | <100ms | Check registry size, disk I/O |
| Error rate | <1% of events | Review error logs, page on-call |

---

## Operational Checklists

### Pre-Deployment Checklist

- [ ] Stage 0 tripwire passes (audit chain reachable)
- [ ] All 4 plugin marketplace routes registered
- [ ] Trust anchor configured (if enforcement ON)
- [ ] All feature flags OFF (dark ship verified)
- [ ] Registry load time <100ms
- [ ] Disk space >10x registry size
- [ ] Audit chain integrity verified (100% valid)
- [ ] All critical compliance mechanisms enabled
- [ ] No recent errors in console logs
- [ ] Backup restoration tested (if possible)

### Dark Ship Deployment Checklist

- [ ] Code deployed to production
- [ ] Console boots successfully
- [ ] Audit tripwire passes
- [ ] No public marketplace routes accessible
- [ ] Feature flags verified OFF
- [ ] Console restart successful
- [ ] Monitoring dashboards show no errors
- [ ] Audit trail continues recording normally

### Phase 2 Canary (10%) Checklist

- [ ] Dark ship stable for ≥24h
- [ ] Trust badge feature flag enabled
- [ ] Canary percentage set to 10%
- [ ] Console restart successful
- [ ] No errors in audit trail (past hour)
- [ ] Trust badge renders correctly in sample of users
- [ ] No performance regression observed
- [ ] Monitoring alerts functional

### Phase 3 Rollout (50%) Checklist

- [ ] Canary stable for ≥24h
- [ ] No errors correlated with trust badge
- [ ] Report feature flag enabled
- [ ] Canary percentage set to 50%
- [ ] Console restart successful
- [ ] Report submissions creating audit events
- [ ] No duplicate or malformed reports
- [ ] Latency <500ms for submissions

### Phase 4 Full Release (100%) Checklist

- [ ] Phase 3 stable for ≥24h
- [ ] No errors correlated with reports
- [ ] Upload feature flag enabled
- [ ] Canary percentage set to 100%
- [ ] Console restart successful
- [ ] Upload endpoint accepting files
- [ ] Manifest validation working
- [ ] All marketplace routes publicly accessible
- [ ] Begin continuous monitoring

### Post-Deployment Monitoring Checklist (Daily)

- [ ] Audit chain integrity (run `verify_audit_chain`)
- [ ] Error count in past 24h <50
- [ ] Registry load time <100ms
- [ ] No upload failures (or <5)
- [ ] No report handling errors
- [ ] Disk space adequate (>5GB remaining)
- [ ] No temp files stuck (check for `.registry-*.tmp`)
- [ ] Backup files recent (mtime <24h)
- [ ] Console uptime >99.5%

### Weekly Validation Checklist

- [ ] Manual smoke test: upload plugin
- [ ] Manual smoke test: submit report
- [ ] Manual smoke test: enable/disable plugin
- [ ] Audit trail has expected event volume
- [ ] No new error categories
- [ ] Trust verification working (if enforcement ON)
- [ ] Registry backups being created
- [ ] All three marketplace features toggle independently
- [ ] Compliance baseline still 100% enabled

---

## Quick Reference

### Emergency Procedures

**Complete marketplace outage → quick disable:**
```bash
python3 -c "
from corvin_console.models import TenantConfig
config = TenantConfig.load()
for f in ['plugin_upload_enabled', 'plugin_report_enabled', 'plugin_trust_badge_enabled']:
    config.features[f] = False
config.save()
"
systemctl --user restart corvin-console
```

**Audit chain corrupted → stop everything:**
```bash
systemctl --user stop corvin-console corvin-service
# Contact Corvin Labs support
```

**Registry corrupted → auto-recovery will kick in:**
```bash
# TenantRegistry.load() automatically restores from .bak
# Monitor audit trail for recovery event
journalctl --user -u corvin-console | grep plugin.registry
```

### Key Files

| File | Purpose | Backup? |
|------|---------|---------|
| `~/.corvin/tenants/_default/plugins/registry.yaml` | Plugin registry | Yes (`.bak`) |
| `~/.corvin/global/plugin_trust_anchors.txt` | Trust anchors | Via `~/.corvin/` backup |
| `~/.corvin/audit.jsonl` | Audit trail | Daily (ideally) |
| `~/.corvin/tenants/_default/tenant.corvin.yaml` | Feature flags | Via git/config mgmt |

### Contacts & Escalation

| Scenario | Primary | Secondary |
|----------|---------|-----------|
| Audit chain broken | On-call SRE | Corvin Labs support |
| Trust anchor compromised | Maintainer | Security team |
| Plugin upload spam | On-call SRE | Community moderation team |
| Consensus needed on rollback | Eng lead | Product manager |

---

**Version:** 1.0  
**Last Updated:** 2026-08-28  
**Status:** Production Ready  
**Next Review:** 2026-09-28
