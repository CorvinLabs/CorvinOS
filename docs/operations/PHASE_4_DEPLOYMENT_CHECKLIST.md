# PHASE 4 DEPLOYMENT CHECKLIST
**Plugin Marketplace 100% Production Rollout**  
**Target Date:** 2026-09-06 00:00 UTC  
**Deployment Lead:** _______________  
**Backup Lead:** _______________  

---

## PRE-DEPLOYMENT (Sep 5, 23:00 UTC)

### 1. Pre-Deployment Gates (Execute All)

**Gate 1: Phase 3 Stability (24+ hours)**
- [ ] Run stability verification script
- [ ] Confirm error rate <5%
- [ ] Confirm audit chain unbroken
- [ ] Confirm no new error categories
- [ ] **Result:** PASS / FAIL

**Gate 2: Canary Performance (48 hours)**
- [ ] Run performance verification script
- [ ] Confirm discovery latency p95 <50ms
- [ ] Confirm report latency p95 <500ms
- [ ] Confirm badge rendering p95 <200ms
- [ ] **Result:** PASS / FAIL

**Gate 3: Trust System**
- [ ] Verify trust anchor configured
- [ ] Verify registry readable and valid
- [ ] Verify no PII in audit events
- [ ] **Result:** PASS / FAIL

**Gate 4: Infrastructure**
- [ ] Verify disk space >10GB
- [ ] Verify console responsive (HTTP 200)
- [ ] Verify registry writable
- [ ] **Result:** PASS / FAIL

**ALL GATES PASSED?** ☐ YES (Proceed) ☐ NO (Abort)

### 2. Team & Communication

- [ ] On-call SRE confirmed and standing by
- [ ] Security lead available for escalations
- [ ] Maintainer contacted (aware of deployment)
- [ ] Slack #corvinOS-ops notification sent (countdown)
- [ ] Stakeholders notified with dashboard link
- [ ] Incident commander assigned

### 3. Pre-Deployment Snapshot

- [ ] Execute snapshot script
- [ ] Verify audit.jsonl backed up
- [ ] Verify registry.yaml backed up
- [ ] Verify config.yaml backed up
- [ ] Confirm snapshot location: `~/.corvin/phase-4-snapshots/`

### 4. Final Verification

- [ ] No uncommitted changes in repo
- [ ] All feature flag code paths reviewed
- [ ] Rollback procedure tested (on staging)
- [ ] Monitoring dashboards functional
- [ ] Alerting webhooks working

**PRE-DEPLOYMENT SIGN-OFF:** _______________  
**Time:** _______________  

---

## DEPLOYMENT EXECUTION (Sep 6, 00:00 UTC)

### 1. Deployment Start (00:00 UTC)

- [ ] Timestamp recorded: _______________
- [ ] Team members in war room / chat
- [ ] Slack status updated: "Phase 4 deployment in progress"
- [ ] Console monitoring dashboard visible to all

### 2. Feature Flag Increment (00:05 UTC)

**Execute Phase 4 configuration deployment:**

```bash
# Run Phase 4 deployment script
bash scripts/phase-4-deploy.sh
```

**Track changes:**
- [ ] plugin_trust_badge_enabled: verified true
- [ ] plugin_report_enabled: verified true
- [ ] plugin_marketplace_canary_rollout: verified 100
- [ ] plugin_upload_enabled: verified true (CRITICAL)

**Verification:**
- [ ] Config file saved
- [ ] Changes persisted to disk
- [ ] Console restart initiated
- [ ] Console back online within 3 minutes

### 3. Verification Phase (00:10 UTC)

**Run verification scripts:**

- [ ] Route verification: All 4 marketplace routes responding
- [ ] Boot tripwire: PASSED
- [ ] Audit chain integrity: VERIFIED
- [ ] Console uptime: Confirmed stable

**Critical check: Can users access plugins now?**
- [ ] Discovery page loads: ☐ YES ☐ NO
- [ ] Upload form visible: ☐ YES ☐ NO
- [ ] Report button visible: ☐ YES ☐ NO

### 4. Go-Live Confirmation (00:15 UTC)

**Decision point:**

```
All verification checks passed?
  ☐ YES → Proceed (Phase 4 LIVE)
  ☐ NO  → Execute rollback immediately
```

**If all checks pass:**
- [ ] Slack #corvinOS-ops: "Phase 4 LIVE - 100% users now have marketplace"
- [ ] Email to stakeholders: "Go-live confirmation"
- [ ] Dashboard link shared

**If any check fails:**
- [ ] Slack #corvinOS-ops: "Phase 4 deployment PAUSED - investigating..."
- [ ] Execute rollback to Phase 3
- [ ] Contact security lead + maintainer

---

## LIVE MONITORING (Sep 6, 00:15 UTC - Sep 7, 00:15 UTC)

### Hour 0-1: Intensive Monitoring

**Every 5 minutes:**
- [ ] Error rate <2%: ☐ PASS ☐ FAIL
- [ ] No new errors: ☐ PASS ☐ FAIL
- [ ] Console responding: ☐ PASS ☐ FAIL
- [ ] Disk space adequate: ☐ PASS ☐ FAIL

**Escalation trigger:** If ANY check fails, initiate incident response

### Hour 1-6: Regular Monitoring

**Every 15 minutes:**
- [ ] Error rate <2%
- [ ] Plugin installs increasing (0 → increasing)
- [ ] Reports flowing (if any)
- [ ] Audit chain valid

### Hour 6-24: Extended Monitoring

**Every 30 minutes:**
- [ ] Error rate <2%
- [ ] Total plugin installs >0 (target: >50)
- [ ] Audit chain unbroken
- [ ] Disk space adequate

**End-of-day (Sep 6, 23:00 UTC):**
- [ ] 24-hour metrics collected
- [ ] No critical incidents
- [ ] All SLOs met
- [ ] Ready for Phase 4 → STABLE declaration

### Daily Monitoring (Sep 7)

**Morning (09:00 UTC):**
- [ ] Error rate stable <2%
- [ ] Plugin ecosystem healthy
- [ ] No new anomalies
- [ ] Audit trail verified

**Status check:**
```
Is Phase 4 STABLE?
  ☐ YES  → Declare success, prepare release notes
  ☐ NO   → Continue monitoring, identify root cause
  ☐ FAIL → Rollback, post-mortem
```

---

## INCIDENT RESPONSE

### If Error Rate >5% (Any Time)

- [ ] Slack alert: "🔴 HIGH ERROR RATE DETECTED"
- [ ] Contact on-call SRE immediately
- [ ] Investigate root cause (logs, errors, patterns)
- [ ] Decision within 15 minutes:
  - ☐ Known issue, fix incoming → monitor closely
  - ☐ Unknown issue → initiate rollback
- [ ] If rollback:
  - [ ] Execute rollback script
  - [ ] Verify Phase 3 stable
  - [ ] Post-incident review scheduled

### If Audit Chain Breaks (Critical)

- [ ] 🔴 STOP ALL SERVICES IMMEDIATELY
- [ ] Contact maintainer + security lead
- [ ] DO NOT restart without review
- [ ] Preserve audit logs
- [ ] Post-mortem investigation

### If Console Crashes

- [ ] Check logs: `journalctl --user -u corvin-console -n 50`
- [ ] Restart console manually
- [ ] If restart fails: revert code, retry
- [ ] If repeat failures: escalate

---

## POST-DEPLOYMENT VALIDATION (Sep 7)

### Morning Metrics Review (09:00 UTC)

**Collect 24-hour metrics:**
- [ ] Total events: _______________
- [ ] Error count: _______________
- [ ] Error rate: _______________%
- [ ] Success rate: _______________%
- [ ] Plugin installs: _______________
- [ ] Discovery latency p95: _______________ms
- [ ] Report latency p95: _______________ms
- [ ] Audit chain breaks: _______________

**Latency by operation:**
- Discovery: p50 ____ p95 ____ p99 ____
- Upload: p50 ____ p95 ____ p99 ____
- Report: p50 ____ p95 ____ p99 ____
- Badge: p50 ____ p95 ____ p99 ____

### Success Criteria Verification

```
Metric                          Target      Actual    PASS?
─────────────────────────────────────────────────────────
Error rate                      <1%         ____%     ☐
Success rate                    >95%        ____%     ☐
Discovery p95 latency           <50ms       ____ms    ☐
Report p95 latency              <500ms      ____ms    ☐
Plugin installs                 >100        ____      ☐
Audit chain breaks              0           ____      ☐
Critical incidents              0           ____      ☐
New error categories            0           ____      ☐

ALL SUCCESS CRITERIA MET?  ☐ YES  ☐ NO
```

### Phase 4 Status Declaration

**If all criteria met:**
```
✅ PHASE 4 SUCCESSFUL
   Status: STABLE & PRODUCTION-READY
   Recommendation: Continue at 100%
   Next: Phase 5 (optional, governance UI + trust enforcement)
```

**If criteria not met:**
```
⚠️ PHASE 4 NEEDS INVESTIGATION
   Issues identified: _______________
   Root cause analysis: _______________
   Recommended action: _______________
```

### Stakeholder Notifications

- [ ] Email sent to security team: "Phase 4 validation complete"
- [ ] Email sent to ops team: "Phase 4 metrics & lessons learned"
- [ ] Email sent to product: "Marketplace live for 100% users"
- [ ] Slack #corvinOS-ops: "Phase 4 ✅ STABLE"

---

## SIGN-OFFS

### Deployment Execution Sign-Off

**SRE Lead:** _______________  
**Date/Time:** _______________  
**Status:** ☐ COMPLETE ☐ ROLLED BACK

### Security Validation Sign-Off

**Security Lead:** _______________  
**Date/Time:** _______________  
**Issues:** ☐ None ☐ [Listed below]  

_________________________________

### Compliance Sign-Off

**Compliance Officer:** _______________  
**Date/Time:** _______________  
**Status:** ☐ APPROVED ☐ CONDITIONAL

_________________________________

### Final Go-Live Authorization

**Decision Maker:** _______________  
**Date/Time:** _______________  
**Decision:** ☐ PROCEED TO STABLE ☐ CONTINUE MONITORING ☐ ROLLBACK

---

## APPENDICES

### Monitoring Dashboard URLs
- Metrics: http://localhost:8765/v1/vibe/metrics
- Audit: http://localhost:8765/v1/vibe/audit
- Settings: http://localhost:8765/console/settings/features

### Emergency Contacts
| Role | Name | Contact |
|------|------|---------|
| On-Call SRE | | |
| Security Lead | | |
| Maintainer | | |
| Incident Commander | | |

### Rollback Command (Emergency Use)
```bash
# Execute if deployment fails
python3 -c "
import yaml
from pathlib import Path
c = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
cfg = yaml.safe_load(c.open())
cfg['features']['plugin_upload_enabled'] = False
cfg['features']['plugin_marketplace_canary_rollout'] = '50'
yaml.dump(cfg, c.open('w'))
"
systemctl --user restart corvin-console
```

---

**Deployment Checklist Version:** 1.0  
**Last Updated:** 2026-08-29  
**Status:** READY FOR USE
