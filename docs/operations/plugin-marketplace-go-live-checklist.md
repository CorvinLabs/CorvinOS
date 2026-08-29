# Plugin Marketplace Go-Live Checklist

**Document:** Production Deployment Go-Live Checklist  
**Date Created:** 2026-08-29  
**Status:** Ready for Deployment  
**Owner:** Deployment Lead / SRE  
**Deployment Date:** 2026-09-01 (Target)

---

## Pre-Deployment Phase (Day 0 — Aug 29–Aug 31)

### Code Freeze & Testing

- [ ] Feature branch merged to `main` (commit hash: `________`)
- [ ] All 285+ plugin tests passing (run: `pytest tests/ -k plugin`)
- [ ] All 20+ marketplace API tests passing
- [ ] All 34+ trust anchor tests passing
- [ ] No regressions in existing functionality (smoke test suite)
- [ ] Code review completed (≥2 approvals from maintainers)
- [ ] Security audit completed (0 CRITICAL, 0 HIGH findings)
- [ ] Load testing passed (expected volume: 1000 uploads/day → success)

### Documentation Review

- [ ] Release notes finalized (docs/releases/PLUGIN_MARKETPLACE_v0.1.md)
- [ ] Operator runbook reviewed (docs/operations/plugin-marketplace-runbook.md)
- [ ] Feature flag rollout plan reviewed (docs/operations/feature-flag-rollout-plan.md)
- [ ] Monitoring setup verified (docs/operations/plugin-marketplace-monitoring.md)
- [ ] Trust anchor procedures reviewed (docs/operations/plugin-trust-anchor-procedures.md)
- [ ] All docs link to correct ADRs (ADR-0249, ADR-0383, ADR-0385)

### Infrastructure & Compliance

- [ ] Backup system tested (registry.yaml → registry.yaml.bak)
- [ ] Audit chain integrity verified (verify_audit_chain passes)
- [ ] GDPR compliance checklist signed off (Art. 30/32, consent, deletion)
- [ ] EU AI Act compliance checklist signed off (Art. 50 disclosure, fail-closed)
- [ ] Audit-trail encryption at-rest configured (if applicable)
- [ ] Backup retention policy set (30 days minimum)
- [ ] Disaster recovery plan reviewed (recovery time <1h)

### Communication & Coordination

- [ ] Release notes published to stakeholders
- [ ] Operator training completed (runbook walk-through)
- [ ] On-call schedule confirmed (SRE coverage Sep 1–8)
- [ ] Incident commander assigned (for Go-Live day)
- [ ] Escalation contacts confirmed (email, Slack, phone)
- [ ] Rollback procedure tested (verified revert works)

### Monitoring & Alerting

- [ ] Prometheus/monitoring scrape targets configured (if applicable)
- [ ] Alert rules loaded and tested (email, Slack, incident tracking)
- [ ] Dashboards deployed (marketplace health, audit trail, registry)
- [ ] Alert channels verified (test alert to email, Slack)
- [ ] On-call alert routing confirmed
- [ ] Log aggregation system operational (audit.jsonl shipping)

---

## Phase 1: Dark Ship Deployment (Day 0–1 — Sep 1)

### Pre-Deployment Verification (4 hours before)

- [ ] All services running and healthy
  - [ ] Console: `systemctl --user status corvin-console` → active
  - [ ] Service: `systemctl --user status corvin-service` → active
  - [ ] Audit tripwire reachable: `python3 -c "from corvin_plugins.boot_platform import assert_all; assert_all()"`

- [ ] Baseline metrics captured
  - [ ] Current console uptime
  - [ ] Current audit chain entry count
  - [ ] Current registry size
  - [ ] Current disk free space (should be >10GB)

- [ ] Feature flags verified OFF (dark ship)
  ```bash
  python3 -c "
  from corvin_console.models import TenantConfig
  config = TenantConfig.load()
  flags = [
    'plugin_trust_badge_enabled',
    'plugin_report_enabled',
    'plugin_upload_enabled',
    'plugin_governance_ui_enabled',
  ]
  for f in flags:
    print(f'{f}: {config.features.get(f, False)}')
  "
  ```
  All should show `False`

- [ ] Disk space verified
  ```bash
  df -h ~/.corvin/
  # Should show >10GB free
  ```

### Deployment Execution (1–2 hours)

- [ ] Deploy code to production
  - [ ] Git pull: `git fetch origin main && git reset --hard origin/main`
  - [ ] Verify commit: `git log -1 --oneline` → `[commit-hash] feat(plugins): marketplace...`
  - [ ] Build console: `cd core/console && npm install && npm run build`
  - [ ] Restart console: `systemctl --user restart corvin-console`

- [ ] Verify deployed code
  ```bash
  # Check that routes exist (even if gated by flags)
  python3 -c "
  from corvin_console.app import app
  routes = [r.rule for r in app.url_map.iter_rules() if 'plugin' in r.rule]
  print(f'Plugin routes registered: {len(routes)}')
  for r in routes[:10]:
    print(f'  {r}')
  "
  ```

- [ ] Verify boot sequence
  - [ ] Console boots without errors: `journalctl --user -u corvin-console -n 30`
  - [ ] Audit tripwire passes: `python3 -c "from corvin_plugins.boot_platform import assert_all; assert_all()"`
  - [ ] Audit chain valid: `python3 -c "from corvin_compliance.audit import verify_audit_chain; verify_audit_chain()"`

- [ ] Verify dark ship (routes not accessible)
  ```bash
  # These should all return 404 or 403
  curl -I http://localhost:8765/v1/vibe/plugins
  curl -I http://localhost:8765/v1/console/plugins/upload
  curl -I http://localhost:8765/v1/vibe/plugins/test/report
  ```

### Post-Deployment Verification (1 hour)

- [ ] Monitoring dashboards loading
  - [ ] Plugin Marketplace Health dashboard visible
  - [ ] Audit Trail Status dashboard visible
  - [ ] No gaps in time-series data

- [ ] Audit trail healthy
  - [ ] New events appearing (e.g., `console.restarted`)
  - [ ] Chain verification passes
  - [ ] No error events (check for `error.*` type)

- [ ] Registry healthy
  - [ ] Registry loads successfully
  - [ ] Registry load time <100ms
  - [ ] Backup file exists and is recent

- [ ] Logs clean
  - [ ] No CRITICAL errors in console logs
  - [ ] No CRITICAL errors in system logs
  - [ ] Warnings are expected and documented

### Phase 1 Success Criteria

- ✅ All deployment steps completed
- ✅ Boot tripwire passes
- ✅ Audit chain valid
- ✅ All 4 marketplace routes NOT accessible (dark ship verified)
- ✅ Feature flags confirmed OFF
- ✅ No new errors in logs
- ✅ Monitoring dashboards operational
- ✅ On-call team standing by

**Duration:** 24 hours in dark ship before proceeding to Phase 2

---

## Phase 2: Trust Badges (Day 2–3 — Sep 2–3)

### Pre-Phase 2 Verification (24h after Phase 1)

- [ ] Phase 1 stability validated
  - [ ] Audit trail clean for past 24h (error count < 10)
  - [ ] No unexpected console restarts
  - [ ] Disk space still adequate (>5GB free)
  - [ ] Registry still loads <100ms

- [ ] Go-live status report reviewed
  - [ ] No incidents reported
  - [ ] Monitoring dashboards clean
  - [ ] No escalations needed

### Phase 2 Deployment

- [ ] Enable trust badge feature flag
  ```bash
  python3 -c "
  from corvin_console.models import TenantConfig
  config = TenantConfig.load()
  config.features['plugin_trust_badge_enabled'] = True
  config.features['plugin_marketplace_canary_rollout'] = '10'
  config.save()
  "
  ```

- [ ] Restart console
  ```bash
  systemctl --user restart corvin-console
  sleep 2
  ```

- [ ] Verify trust badge route accessible (to 10% of users)
  ```bash
  # Should return 200, not 404
  curl -I http://localhost:8765/v1/vibe/plugins
  ```

- [ ] Monitor for errors (watch for 2h)
  ```bash
  # Watch audit trail for badge-related errors
  watch -n 30 'python3 -c "
  import json
  from pathlib import Path
  from datetime import datetime, timedelta
  
  audit_path = Path.home() / \".corvin/audit.jsonl\"
  lookback = datetime.now() - timedelta(hours=2)
  
  badge_errors = 0
  for line in audit_path.read_text().strip().split(\"\\n\"):
    if not line:
      continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event[\"timestamp\"].replace(\"Z\", \"+00:00\"))
    if ts < lookback:
      continue
    if \"badge\" in event.get(\"type\", \"\") and \"error\" in event.get(\"type\", \"\"):
      badge_errors += 1
  
  print(f\"Badge errors (past 2h): {badge_errors}\")
  if badge_errors < 5:
    print(\"✓ Healthy\")
  else:
    print(\"✗ High error rate\")
  "'
  ```

### Phase 2 Success Criteria

- ✅ Feature flag enabled successfully
- ✅ Trust badge route accessible (200 response)
- ✅ <1% error rate in badge operations
- ✅ No audit chain breaks
- ✅ Disk space adequate
- ✅ No performance regression

**Duration:** 24–48 hours at 10% before proceeding to Phase 3

---

## Phase 3: Reports (Day 4–5 — Sep 4–5)

### Pre-Phase 3 Verification

- [ ] Phase 2 stable for 24h+
  - [ ] No badge-related errors in past 24h
  - [ ] Performance metrics stable
  - [ ] Disk space adequate

### Phase 3 Deployment

- [ ] Enable report feature flag
  ```bash
  python3 -c "
  from corvin_console.models import TenantConfig
  config = TenantConfig.load()
  config.features['plugin_report_enabled'] = True
  config.features['plugin_marketplace_canary_rollout'] = '50'
  config.save()
  "
  systemctl --user restart corvin-console
  ```

- [ ] Test report submission
  ```bash
  python3 -c "
  import requests
  resp = requests.post(
    'http://localhost:8765/v1/vibe/plugins/test-plugin/report',
    json={'reason': 'test', 'details': 'deployment test'}
  )
  print(f'Report submission: {resp.status_code}')
  "
  ```

### Phase 3 Success Criteria

- ✅ Report endpoint accessible (200 response)
- ✅ Reports creating audit events
- ✅ <1% error rate in report operations
- ✅ Report latency <500ms (p95)

**Duration:** 24–48 hours at 50% before proceeding to Phase 4

---

## Phase 4: Upload & Installation (Day 6–7 — Sep 6–7)

### Pre-Phase 4 Verification

- [ ] Phase 3 stable for 24h+
  - [ ] No report-related errors
  - [ ] Performance metrics stable
  - [ ] Disk space adequate (>5GB)

### Phase 4 Deployment

- [ ] Enable upload feature flag
  ```bash
  python3 -c "
  from corvin_console.models import TenantConfig
  config = TenantConfig.load()
  config.features['plugin_upload_enabled'] = True
  config.features['plugin_marketplace_canary_rollout'] = '100'
  config.save()
  "
  systemctl --user restart corvin-console
  ```

- [ ] Test upload workflow (end-to-end)
  ```bash
  # Create minimal test plugin
  mkdir -p /tmp/test-plugin
  cat > /tmp/test-plugin/plugin.yaml << 'EOF'
  id: test-plugin
  version: 1.0.0
  origin: community
  plugin_type: skill
  EOF
  
  # Pack into tarball
  cd /tmp
  tar czf test-plugin.tar.gz test-plugin/
  
  # Upload
  python3 -c "
  import requests
  with open('/tmp/test-plugin.tar.gz', 'rb') as f:
    resp = requests.post(
      'http://localhost:8765/v1/console/plugins/upload',
      files={'file': f}
    )
    print(f'Upload response: {resp.status_code}')
    if resp.status_code != 200:
      print(f'Error: {resp.text}')
  "
  ```

- [ ] Verify all routes accessible
  ```bash
  curl -I http://localhost:8765/v1/vibe/plugins       # Should be 200
  curl -I http://localhost:8765/v1/console/plugins/upload  # Should be 200
  ```

### Phase 4 Success Criteria

- ✅ All marketplace routes accessible (200 responses)
- ✅ Upload endpoint accepting files
- ✅ Manifest validation working
- ✅ Plugins installing successfully
- ✅ >95% success rate on uploads/installs
- ✅ Audit events recording correctly

**Duration:** 24–48 hours at 100% before declaring stable

---

## Production Stabilization (Day 8+)

### Daily Checklist (First 7 Days)

Each morning, run this verification:

```bash
#!/usr/bin/env bash

echo "=== Daily Marketplace Health Check ==="

# 1. Audit chain integrity
python3 -c "
from corvin_compliance.audit import verify_audit_chain
from pathlib import Path
result = verify_audit_chain(Path.home() / '.corvin/audit.jsonl')
print(f'✓ Audit chain: {\"VALID\" if result.valid else \"BROKEN\"}')
"

# 2. Error count (past 24h)
python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta
audit_path = Path.home() / '.corvin/audit.jsonl'
lookback = datetime.now() - timedelta(hours=24)
errors = sum(1 for line in audit_path.read_text().split('\n')
             if line and 'error' in (lambda x: json.loads(x).get('type', ''))(line))
print(f'✓ Errors (24h): {errors}')
if errors > 50:
    print('  ✗ WARNING: High error count')
"

# 3. Disk space
python3 -c "
import shutil
from pathlib import Path
stat = shutil.disk_usage(Path.home() / '.corvin')
free_gb = stat.free / (1024**3)
print(f'✓ Disk free: {free_gb:.1f} GB')
if free_gb < 1:
    print('  ✗ CRITICAL: Disk space critical')
"

# 4. Registry health
python3 -c "
from corvin_plugins.state import TenantRegistry
registry = TenantRegistry.load('_default')
print(f'✓ Plugins: {len(registry.plugins)} installed')
"

echo "✓ Daily check complete"
```

- [ ] Day 1 (Sep 2): All metrics good, no incidents
- [ ] Day 2 (Sep 3): All metrics good, no incidents
- [ ] Day 3 (Sep 4): All metrics good, no incidents
- [ ] Day 4 (Sep 5): All metrics good, no incidents
- [ ] Day 5 (Sep 6): All metrics good, no incidents
- [ ] Day 6 (Sep 7): All metrics good, no incidents
- [ ] Day 7 (Sep 8): All metrics good, production stable

### Weekly Checklist

After first week of production stability:

- [ ] Manual smoke tests completed (upload, install, report)
- [ ] Audit trail volume meets expectations
- [ ] No new error categories discovered
- [ ] Trust verification working (if enforcement ON)
- [ ] Registry backups being created regularly
- [ ] All three marketplace features toggle independently
- [ ] Compliance baseline still 100% enabled
- [ ] Performance metrics within SLOs

### Success Metrics (First Month)

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Audit chain uptime | 100% | ___% | [ ] |
| Upload success rate | >95% | ___% | [ ] |
| Report success rate | >95% | ___% | [ ] |
| Latency p95 (upload) | <5s | ___ms | [ ] |
| Latency p95 (report) | <500ms | ___ms | [ ] |
| Error rate | <1% | ___% | [ ] |
| Disk space headroom | >5GB | ___GB | [ ] |
| Console uptime | >99% | ___% | [ ] |

---

## Rollback Decision Tree

### If Phase 1 Fails

**Symptom:** Errors in audit trail after deployment  
**Action:** Revert code before enabling any features

```bash
cd /home/shumway/projects/CorvinOS
git revert <deployment-commit>
cd core/console && npm run build
systemctl --user restart corvin-console
```

**Status:** Rollback to pre-marketplace code

### If Phase 2 Fails

**Symptom:** >5% error rate in trust badge operations  
**Action:** Disable trust badge feature only

```bash
python3 -c "
from corvin_console.models import TenantConfig
config = TenantConfig.load()
config.features['plugin_trust_badge_enabled'] = False
config.save()
"
systemctl --user restart corvin-console
```

**Status:** Fall back to Phase 1 (code present, features off)

### If Phase 3 Fails

**Symptom:** Report submissions causing audit chain breaks  
**Action:** Disable report feature, keep badges enabled

```bash
python3 -c "
from corvin_console.models import TenantConfig
config = TenantConfig.load()
config.features['plugin_report_enabled'] = False
config.features['plugin_marketplace_canary_rollout'] = '10'  # Back to Phase 2
config.save()
"
systemctl --user restart corvin-console
```

**Status:** Fall back to Phase 2 (badges only)

### If Phase 4 Fails

**Symptom:** Upload/install causing registry corruption  
**Action:** Disable uploads, recover registry from backup

```bash
python3 -c "
from corvin_console.models import TenantConfig
from pathlib import Path

# Disable uploads
config = TenantConfig.load()
config.features['plugin_upload_enabled'] = False
config.save()

# Recover registry
registry_path = Path.home() / '.corvin/tenants/_default/plugins/registry.yaml'
backup_path = registry_path.with_suffix('.yaml.bak')
if backup_path.exists():
    backup_path.replace(registry_path)
    print('✓ Registry recovered from backup')
else:
    print('✗ No backup found — critical situation')
"
systemctl --user restart corvin-console
```

**Status:** Fall back to Phase 3 (badges + reports only)

### Critical Failure

**If audit chain breaks or data corruption detected:**

1. **STOP all services immediately**
2. **Do NOT try to fix manually**
3. **Contact Corvin Labs support**
4. **Prepare incident report**

```bash
systemctl --user stop corvin-console corvin-service
echo "Services stopped. Waiting for incident response."
```

---

## Sign-Off

### Pre-Deployment Sign-Off

**I confirm all pre-deployment checks are complete and the system is ready for Phase 1 deployment.**

- [ ] **Deployment Lead:** _________________ Date: _______
- [ ] **SRE/Infrastructure:** _________________ Date: _______
- [ ] **Security Team:** _________________ Date: _______
- [ ] **Product Manager:** _________________ Date: _______

### Phase 1 Success Sign-Off

**I confirm Phase 1 (dark ship) is stable and Phase 2 can proceed.**

- [ ] **On-Call SRE:** _________________ Date: _______
- [ ] **Incident Commander:** _________________ Date: _______

### Phase 4 Success Sign-Off

**I confirm Phase 4 (full release) is stable and production deployment is complete.**

- [ ] **Deployment Lead:** _________________ Date: _______
- [ ] **SRE/Infrastructure:** _________________ Date: _______
- [ ] **Product Manager:** _________________ Date: _______

---

## Reference Documents

- **Release Notes:** docs/releases/PLUGIN_MARKETPLACE_v0.1.md
- **Operator Runbook:** docs/operations/plugin-marketplace-runbook.md
- **Feature Flag Rollout:** docs/operations/feature-flag-rollout-plan.md
- **Monitoring Setup:** docs/operations/plugin-marketplace-monitoring.md
- **Trust Anchor Procedures:** docs/operations/plugin-trust-anchor-procedures.md
- **Architecture ADRs:** ADR-0249, ADR-0383, ADR-0385

---

**Version:** 1.0  
**Last Updated:** 2026-08-29  
**Status:** Ready for Deployment  
**Target Deployment:** 2026-09-01
