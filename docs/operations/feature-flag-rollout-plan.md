# Plugin Marketplace Feature Flag Rollout Plan

**Document:** Feature Flag Rollout Plan  
**Date Created:** 2026-08-29  
**Status:** Ready for Deployment  
**Owner:** SRE/Deployment Team  
**Target:** Production Rollout Week of 2026-09-01

---

## Overview

The plugin marketplace feature (ADR-0249, ADR-0383, ADR-0385) ships "dark" (all features OFF by default) with a phased rollout over 5 phases spanning 7–10 days. Each phase includes verification gates and rollback paths.

### Flags Summary

Four primary feature flags control the marketplace:

| Flag | Purpose | Default | Phase Enabled |
|------|---------|---------|--------------|
| `plugin_trust_badge_enabled` | Display trust badges (Builtin/Vetted/Community) | OFF | Phase 2 |
| `plugin_report_enabled` | Enable community report submissions | OFF | Phase 3 |
| `plugin_upload_enabled` | Enable plugin upload/installation | OFF | Phase 4 |
| `plugin_governance_ui_enabled` | Display governance UI (permissions, ratings) | OFF | Phase 5 |

**Optional rollout control:**
| Flag | Purpose | Default | Phase Enabled |
|------|---------|---------|--------------|
| `plugin_marketplace_canary_rollout` | Percentage of users seeing features (10/50/100) | "0" | Phase 2 |
| `plugin_trust_enforcement` | Enforce trust verdict (fail-closed on untrusted) | OFF | Phase 5 |

---

## Phase Schedule

```
Phase 1: Dark Ship           Day 0–1  (1 day)   ← Code deployed, all features OFF
Phase 2: Trust Badges        Day 2–3  (2 days)  ← 10% of users see badges
Phase 3: Reports             Day 4–5  (2 days)  ← 50% of users can submit reports
Phase 4: Upload              Day 6–7  (2 days)  ← 100% of users can install plugins
Phase 5: Full Governance     Day 8+   (TBD)     ← UI panels, ratings, enforcement
```

**Total Duration:** 7–8 days minimum. Each phase requires ≥24h stability verification before proceeding.

---

## Phase 1: Dark Ship (Day 0–1)

### Objective
Deploy plugin marketplace code to production with all features disabled (invisible to users).

### Pre-Deployment Gates
1. ✅ All tests passing (285+ plugin tests green)
2. ✅ Security audit complete (ADR-0383: sandbox security verified)
3. ✅ Performance baseline: registry load <100ms, manifest parsing <50ms
4. ✅ Audit chain integrity verified
5. ✅ Backup system tested

### Deployment Steps

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 1: DARK SHIP DEPLOYMENT ==="
echo "Deploying plugin marketplace code (all features OFF)"

# 1. Update code from main branch
cd /home/shumway/projects/CorvinOS
git fetch origin main
git reset --hard origin/main

# 2. Verify no uncommitted changes
git status --porcelain | grep -v "^??" && echo "✗ Uncommitted changes" && exit 1 || true

# 3. Deploy console frontend
cd core/console/corvin_console/web-next
npm install
npm run build

# 4. Verify marketplace routes are NOT yet mounted
python3 -c "
from corvin_console.app import app

# These routes should NOT be accessible yet (routes may exist but return 404)
test_routes = [
    '/v1/vibe/plugins',
    '/v1/vibe/plugins/<plugin_id>',
    '/v1/console/plugins/upload'
]

# Routes may be defined but gated by feature flags
# Check that flags are OFF to confirm dark ship
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

flags_off = [
    'plugin_trust_badge_enabled' not in config.features or config.features['plugin_trust_badge_enabled'] == False,
    'plugin_report_enabled' not in config.features or config.features['plugin_report_enabled'] == False,
    'plugin_upload_enabled' not in config.features or config.features['plugin_upload_enabled'] == False,
]

if all(flags_off):
    print('✓ Dark ship verified: all marketplace flags OFF')
else:
    print('✗ WARNING: some flags are already ON')
    exit(1)
"

# 5. Restart console
systemctl --user restart corvin-console
sleep 2

# 6. Verify boot tripwire
python3 -c "
from corvin_plugins.boot_platform import assert_all
assert_all()
print('✓ Boot tripwire passed: audit chain reachable')
"

# 7. Verify audit chain integrity
python3 -c "
from corvin_compliance.audit import verify_audit_chain
from pathlib import Path

audit_path = Path.home() / '.corvin/audit.jsonl'
result = verify_audit_chain(audit_path, max_lookback=1000)

if result.valid:
    print(f'✓ Audit chain valid: {result.entries_verified} entries verified')
else:
    print(f'✗ Audit chain corrupted: {result.error}')
    exit(1)
"

echo "✓ Phase 1 complete: marketplace code deployed dark"
```

### Verification Checklist
- [ ] Console boots successfully without errors
- [ ] Audit tripwire passes
- [ ] Audit chain verified (100% valid)
- [ ] `curl http://localhost:8765/v1/vibe/plugins` returns 404 or 403
- [ ] `curl http://localhost:8765/v1/console/plugins/upload` returns 404 or 403
- [ ] No errors in console logs: `journalctl --user -u corvin-console -n 20`
- [ ] Registry loads successfully: `python3 -c "from corvin_plugins.state import TenantRegistry; TenantRegistry.load('_default')"`

### Duration
**24 hours minimum** in dark ship before proceeding to Phase 2.

### Rollback
If critical errors detected, revert code:
```bash
cd /home/shumway/projects/CorvinOS
git reset --hard <previous-commit>
cd core/console/corvin_console/web-next
npm run build
systemctl --user restart corvin-console
```

---

## Phase 2: Trust Badges (Day 2–3)

### Objective
Enable trust badge display for 10% of users (canary). Badges show origin (Builtin/Vetted/Community) without any download/install functionality yet.

### Pre-Phase 2 Gates
1. ✅ Phase 1 stable for ≥24h (no errors in audit trail)
2. ✅ Registry integrity verified
3. ✅ No performance regression observed

### Deployment Steps

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 2: TRUST BADGES (10% CANARY) ==="

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Enable trust badge display
config.features['plugin_trust_badge_enabled'] = True

# Set canary to 10%
config.features['plugin_marketplace_canary_rollout'] = '10'

config.save(config_path)
print('✓ Feature flags updated: trust badges enabled (10% users)')
"

# Reload configuration
systemctl --user restart corvin-console
sleep 2

# Verify
python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

badge_enabled = config.features.get('plugin_trust_badge_enabled', False)
canary_pct = config.features.get('plugin_marketplace_canary_rollout', '0')

print(f'Trust badges enabled: {badge_enabled}')
print(f'Canary rollout: {canary_pct}%')

if badge_enabled and canary_pct == '10':
    print('✓ Phase 2 configuration verified')
else:
    exit(1)
"

echo "✓ Phase 2 deployed: 10% canary active"
```

### Monitoring During Phase 2

```bash
#!/usr/bin/env bash

# Monitor for 4 hours
watch -n 60 'python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta

audit_path = Path.home() / \".corvin/audit.jsonl\"
lookback = datetime.now() - timedelta(hours=4)

# Count events
badge_errors = 0
badge_success = 0
other_errors = 0

for line in audit_path.read_text().strip().split(\"\\n\"):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event[\"timestamp\"].replace(\"Z\", \"+00:00\"))
    if ts < lookback:
        continue
    
    if \"badge\" in event.get(\"type\", \"\"):
        if \"error\" in event.get(\"type\", \"\"):
            badge_errors += 1
        else:
            badge_success += 1
    elif \"error\" in event.get(\"type\", \"\"):
        other_errors += 1

print(f\"Badge events: {badge_success} success, {badge_errors} errors\")
print(f\"Other errors (past 4h): {other_errors}\")

if badge_errors > 10 or other_errors > 50:
    print(\"✗ WARNING: High error rate\")
else:
    print(\"✓ Canary healthy\")
"'
```

### Success Criteria for Phase 2
- [ ] No errors related to `plugin_trust_badge_enabled` in audit trail
- [ ] Trust badges rendering correctly for sample of users
- [ ] No performance degradation (registry load still <100ms)
- [ ] Audit chain continues to verify cleanly
- [ ] <1% error rate in trust badge operations

### Duration
**24–48 hours minimum** at 10% before proceeding to Phase 3.

### Rollback
```bash
python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)
config.features['plugin_trust_badge_enabled'] = False
config.features['plugin_marketplace_canary_rollout'] = '0'
config.save(config_path)
"
systemctl --user restart corvin-console
```

---

## Phase 3: Report Submissions (Day 4–5)

### Objective
Enable community report submissions for 50% of users. Reports allow users to flag suspicious plugins (non-functional, malicious, etc.). This is read-only for operators (no enforcement yet).

### Pre-Phase 3 Gates
1. ✅ Phase 2 stable for ≥24h
2. ✅ No errors correlated with trust badge feature
3. ✅ Canary performance acceptable

### Deployment Steps

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 3: REPORT SUBMISSIONS (50%) ==="

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Enable report submissions
config.features['plugin_report_enabled'] = True

# Expand canary to 50%
config.features['plugin_marketplace_canary_rollout'] = '50'

config.save(config_path)
print('✓ Feature flags updated: reports enabled (50% users)')
"

systemctl --user restart corvin-console
sleep 2

# Verify report endpoint is accessible
python3 -c "
import requests
import json

try:
    # Test report submission (will fail with 404/403 if not enabled, or succeed if enabled)
    resp = requests.post(
        'http://localhost:8765/v1/vibe/plugins/test-plugin/report',
        json={'reason': 'test', 'details': 'Verification test'},
        timeout=5
    )
    
    # 200 = accepted, 400 = validation error, 403 = disabled, 404 = route missing
    if resp.status_code in [200, 400]:
        print(f'✓ Report endpoint responding ({resp.status_code})')
    else:
        print(f'⚠ Unexpected status: {resp.status_code}')
except Exception as e:
    print(f'✗ Report endpoint unreachable: {e}')
"

echo "✓ Phase 3 deployed: 50% report submissions active"
```

### Monitoring During Phase 3

```bash
#!/usr/bin/env bash

watch -n 60 'python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta

audit_path = Path.home() / \".corvin/audit.jsonl\"
lookback = datetime.now() - timedelta(hours=2)

report_count = 0
report_errors = 0
audit_errors = 0

for line in audit_path.read_text().strip().split(\"\\n\"):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event[\"timestamp\"].replace(\"Z\", \"+00:00\"))
    if ts < lookback:
        continue
    
    if event[\"type\"] == \"plugin.reported\":
        report_count += 1
    elif \"report\" in event.get(\"type\", \"\") and \"error\" in event.get(\"type\", \"\"):
        report_errors += 1
    elif \"error\" in event.get(\"type\", \"\"):
        audit_errors += 1

print(f\"Reports (past 2h): {report_count} submitted, {report_errors} errors\")
print(f\"Other errors: {audit_errors}\")
print(f\"Audit chain: {\"✓\" if audit_errors == 0 else \"✗\"}\")
"'
```

### Success Criteria for Phase 3
- [ ] Report submissions creating audit events
- [ ] No duplicate or malformed reports in audit trail
- [ ] Report latency <500ms (p99)
- [ ] No report handling errors
- [ ] <1% error rate in report operations
- [ ] Audit chain continues to verify cleanly

### Duration
**24–48 hours minimum** at 50% before proceeding to Phase 4.

### Rollback
```bash
python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)
config.features['plugin_report_enabled'] = False
config.features['plugin_marketplace_canary_rollout'] = '50'  # Keep badges
config.save(config_path)
"
systemctl --user restart corvin-console
```

---

## Phase 4: Upload & Installation (Day 6–7)

### Objective
Enable plugin upload and installation for 100% of users. This is the full marketplace feature.

### Pre-Phase 4 Gates
1. ✅ Phase 3 stable for ≥24h
2. ✅ No errors correlated with report feature
3. ✅ Trust anchor configured (if enforcement is planned)
4. ✅ Disk space verified (>10x registry size)

### Deployment Steps

```bash
#!/usr/bin/env bash
set -e

echo "=== PHASE 4: UPLOAD & INSTALLATION (100%) ==="

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Enable upload
config.features['plugin_upload_enabled'] = True

# Expand canary to 100%
config.features['plugin_marketplace_canary_rollout'] = '100'

config.save(config_path)
print('✓ Feature flags updated: upload enabled (100% users)')
"

systemctl --user restart corvin-console
sleep 2

# Verify all routes are accessible
python3 -c "
import requests

routes = [
    ('GET', '/v1/vibe/plugins'),
    ('POST', '/v1/console/plugins/upload'),
    ('POST', '/v1/vibe/plugins/test-plugin/report'),
]

print('Verifying marketplace routes:')
for method, path in routes:
    try:
        if method == 'GET':
            resp = requests.get(f'http://localhost:8765{path}', timeout=5)
        else:
            resp = requests.post(f'http://localhost:8765{path}', json={}, timeout=5)
        
        # 200 = success, 4xx = expected client error, 5xx = server error
        if resp.status_code < 500:
            print(f'  ✓ {method} {path} → {resp.status_code}')
        else:
            print(f'  ✗ {method} {path} → {resp.status_code}')
    except Exception as e:
        print(f'  ✗ {method} {path} → {e}')
"

echo "✓ Phase 4 deployed: full marketplace live (100% users)"
```

### Monitoring During Phase 4

```bash
#!/usr/bin/env bash

watch -n 60 'python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta
import time

audit_path = Path.home() / \".corvin/audit.jsonl\"
lookback = datetime.now() - timedelta(hours=1)

uploads = 0
installs = 0
reports = 0
errors = 0

for line in audit_path.read_text().strip().split(\"\\n\"):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event[\"timestamp\"].replace(\"Z\", \"+00:00\"))
    if ts < lookback:
        continue
    
    event_type = event.get(\"type\", \"\")
    if event_type == \"plugin.uploaded\":
        uploads += 1
    elif event_type == \"plugin.installed\":
        installs += 1
    elif event_type == \"plugin.reported\":
        reports += 1
    elif \"error\" in event_type:
        errors += 1

total_events = uploads + installs + reports + errors
success_rate = ((total_events - errors) / total_events * 100) if total_events > 0 else 100

print(f\"Marketplace Activity (past 1h):\")
print(f\"  Uploads: {uploads}\")
print(f\"  Installs: {installs}\")
print(f\"  Reports: {reports}\")
print(f\"  Errors: {errors}\")
print(f\"  Success rate: {success_rate:.1f}%\")

if errors > 10 or success_rate < 95:
    print(f\"✗ WARNING: High error rate\")
else:
    print(f\"✓ Healthy\")
"'
```

### Success Criteria for Phase 4
- [ ] All 4 marketplace routes publicly accessible (no 404)
- [ ] Upload endpoint accepting files
- [ ] Manifest validation working correctly
- [ ] Plugin installations creating audit events
- [ ] >95% success rate on uploads and installations
- [ ] Disk space adequate (still >5GB free)
- [ ] Backup files being created regularly
- [ ] Audit chain continues to verify cleanly

### Duration
**24–48 hours minimum** at 100% (full production load) before declaring stable.

### Rollback
```bash
python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)
config.features['plugin_upload_enabled'] = False
config.features['plugin_marketplace_canary_rollout'] = '100'  # Keep badges & reports
config.save(config_path)
"
systemctl --user restart corvin-console
```

---

## Phase 5: Trust Enforcement & Governance UI (Day 8+)

### Objective
Enable trust enforcement (fail-closed on untrusted plugins) and display governance UI with ratings, permissions, and trust badges.

**This phase is optional and can be deferred.** Marketplace is fully functional in Phase 4.

### Pre-Phase 5 Gates (if proceeding)
1. ✅ Phase 4 stable for ≥48h
2. ✅ Trust anchor configured and tested
3. ✅ Operator review of governance UI complete

### Deployment (Optional)

```bash
#!/usr/bin/env bash

echo "=== PHASE 5: TRUST ENFORCEMENT & GOVERNANCE UI (OPTIONAL) ==="

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Enable trust enforcement (fail-closed)
config.features['plugin_trust_enforcement'] = True

# Enable governance UI
config.features['plugin_governance_ui_enabled'] = True

config.save(config_path)
print('✓ Trust enforcement and governance UI enabled')
"

systemctl --user restart corvin-console
echo "✓ Phase 5 complete (OPTIONAL)"
```

---

## Emergency Procedures

### Quick Disable (All Features)

If marketplace needs to be disabled immediately:

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = TenantConfig.load(config_path)

# Disable all marketplace features
for flag in [
    'plugin_trust_badge_enabled',
    'plugin_report_enabled',
    'plugin_upload_enabled',
    'plugin_governance_ui_enabled',
    'plugin_trust_enforcement'
]:
    config.features[flag] = False

config.features['plugin_marketplace_canary_rollout'] = '0'
config.save(config_path)
print('✓ All marketplace features disabled')
"

systemctl --user restart corvin-console
echo "✓ Marketplace disabled — routes no longer accessible"
```

### Escalation Matrix

| Scenario | Action | Owner |
|----------|--------|-------|
| >5% error rate | Pause current phase for 1h, investigate | On-call SRE |
| Audit chain broken | Stop all services immediately | On-call SRE + Maintainer |
| >100 errors/5min | Disable all features | On-call SRE |
| Registry corrupted | Restore from backup, audit recovery | Maintainer |
| Trust anchor compromised | Revoke key, create new anchor, re-sign plugins | Maintainer |

---

## Rollout Timeline

```
Date      | Phase    | Duration | Success Criteria
----------|----------|----------|------------------------------------------
Sep 01    | Phase 1  | 24h      | Boot succeeds, audit clean, routes dark
Sep 02    | Phase 2  | 24-48h   | Trust badges visible, <1% error rate
Sep 04    | Phase 3  | 24-48h   | Reports submitted, audit clean
Sep 06    | Phase 4  | 24-48h   | Full marketplace live, >95% success
Sep 08+   | Phase 5  | TBD      | Governance UI + enforcement (optional)
```

---

## Validation & Monitoring

### Daily Checklist (Each Phase)
- [ ] Audit chain integrity verified
- [ ] Error count <50 in past 24h
- [ ] No new error categories
- [ ] Disk space adequate (>5GB free)
- [ ] Console uptime >99.5%
- [ ] Registry loads <100ms
- [ ] No temp files stuck (`.registry-*.tmp`)

### Key Metrics
| Metric | Target | Alert If |
|--------|--------|----------|
| Audit chain valid | 100% | Any break |
| Success rate | >95% | <90% |
| Latency p95 | <500ms | >1s |
| Error rate | <1% | >5% |
| Disk free | >5GB | <1GB |

---

## Documentation & Communication

### Pre-Rollout (Sep 01)
- [ ] Release notes published (docs/releases/PLUGIN_MARKETPLACE_v0.1.md)
- [ ] Operator runbook reviewed (docs/operations/plugin-marketplace-runbook.md)
- [ ] Monitoring dashboards configured
- [ ] Escalation contacts confirmed

### During Rollout (Daily)
- [ ] Phase progress posted to Slack #corvinOS-ops
- [ ] Any anomalies logged in incident system
- [ ] Daily metrics summary email to stakeholders

### Post-Rollout (Sep 08+)
- [ ] Success metrics report (error rate, latency, volume)
- [ ] Lessons learned document
- [ ] Post-mortem on any issues discovered

---

**Version:** 1.0  
**Last Updated:** 2026-08-29  
**Status:** Ready for Deployment  
**Next Review:** After Phase 4 completion (Sep 07)
