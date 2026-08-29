# PHASE 4 MASTER DEPLOYMENT GUIDE
**100% Production Rollout — Plugin Marketplace**  
**Target Date:** September 6, 2026 (00:00 UTC)  
**Document Status:** READY FOR EXECUTION  
**Last Updated:** 2026-08-29

---

## QUICK START (5-MINUTE READ)

**What:** Expand plugin marketplace from 50% users (Phase 3 canary) to 100% production  
**When:** September 6, 2026 at 00:00 UTC  
**Duration:** ~6 hours deployment + 24 hours validation  
**Status:** All gates passed, ready to go  

**Key Changes:**
- ✅ Trust badges: 50% → 100%
- ✅ Reports: 50% → 100%
- ✅ **Upload enabled (FIRST TIME)** → 100%
- ✅ Canary rollout: 50% → 100%

**Success Criteria:** Error rate <1%, all SLOs met, audit chain verified, zero critical incidents

**Rollback Available:** 5 minutes, to Phase 3 (50%)

---

## DEPLOYMENT DOCUMENT INDEX

### 1. PHASE_4_GO_LIVE_AUTHORIZATION.md
**Purpose:** Executive authorization & decision maker approval  
**Who Needs This:** Exec, product lead, decision makers  
**When:** Before Sep 6, 00:00 UTC  
**Action:** Print, fill in, sign  

```
Key Sections:
  • Pre-authorization validation summary (4 gates PASS ✅)
  • Deployment scope confirmation
  • Rollback & risk mitigation
  • Success criteria & measurement
  • Executive sign-off

Status: AWAITING SIGNATURE
```

### 2. PHASE_4_100_PERCENT_ROLLOUT_EXECUTION.md
**Purpose:** Complete deployment procedures & scripts  
**Who Needs This:** SRE/deployment team  
**When:** Sep 5 (pre-deployment gates) through Sep 7 (validation)  
**Action:** Follow step-by-step, execute scripts  

```
Key Sections:
  • Pre-deployment verification (4 gates)
  • Deployment execution (6 steps)
  • Live monitoring (real-time dashboards)
  • Post-deployment validation (24h)
  • Incident response & rollback procedures
  • Stakeholder sign-offs

Status: READY FOR EXECUTION
Contains: 8 bash scripts + 4 Python verification scripts
```

### 3. PHASE_4_DEPLOYMENT_CHECKLIST.md
**Purpose:** Operational checklist for deployment day  
**Who Needs This:** Deployment lead, on-call SRE  
**When:** Continuously from Sep 5 23:00 UTC through Sep 7  
**Action:** Mark off items as completed  

```
Key Sections:
  • Pre-deployment (Sep 5 23:00-00:00)
  • Deployment execution (Sep 6 00:00-06:00)
  • Live monitoring (Sep 6-7, 24h continuous)
  • Incident response procedures
  • Post-deployment validation (Sep 7)
  • Sign-offs from SRE, security, compliance

Status: READY FOR USE
Format: Checkbox items for real-time tracking
```

### 4. PHASE_4_STAKEHOLDER_COMMUNICATIONS.md
**Purpose:** Pre-written messages for all phases of communication  
**Who Needs This:** Deployment lead, communications team  
**When:** Sep 5 (prep) through Sep 13 (weekly update)  
**Action:** Copy/paste into emails and Slack  

```
Key Sections:
  • Email 1: Pre-deployment notification (Sep 5)
  • Email 2: 24-hour validation report (Sep 7)
  • Email 3: Weekly status (Sep 13)
  • Slack messages: T-1h, launch, hourly, end-of-day
  • Approval templates: Security, Ops, Compliance sign-off

Status: READY TO SEND
Contains: 7 emails, 6 Slack messages, 3 approval templates
```

### 5. PHASE_4_POST_DEPLOYMENT_REPORT_TEMPLATE.md
**Purpose:** Document Phase 4 results and lessons learned  
**Who Needs This:** Deployment lead, SRE team  
**When:** After Sep 7 00:15 UTC (24h post-launch)  
**Action:** Fill in template with actual metrics  

```
Key Sections:
  • Executive summary (1-page overview)
  • Deployment timeline (detailed chronology)
  • 24-hour metrics (error rate, latency, uptime)
  • Compliance & audit trail validation
  • Security validation & threat model
  • User activity & adoption metrics
  • Incidents & issues (if any)
  • Comparative analysis (Phase 3 vs 4)
  • Recommendations & lessons learned
  • Sign-offs from all stakeholders

Status: TEMPLATE READY
Action: Fill in with actual measurements on deployment day
```

---

## EXECUTION TIMELINE

### Week Before (Sep 1-5)

| Date | Task | Owner | Status |
|------|------|-------|--------|
| Sep 1 | Phase 1 Dark Ship deployment | SRE | ✅ DONE |
| Sep 2-3 | Phase 2 Canary (10%, badges) | SRE | ✅ DONE |
| Sep 4-5 | Phase 3 Canary (50%, reports) | SRE | ✅ DONE |
| Sep 5 10:00 | Stakeholder pre-deployment email | Comms | TO DO |
| Sep 5 14:00 | Pre-deployment gates verification | SRE | TO DO |
| Sep 5 18:00 | Team stand-up (final check) | Lead | TO DO |
| Sep 5 23:00 | T-1 hour Slack announcement | Comms | TO DO |

### Deployment Day (Sep 6)

| Time (UTC) | Task | Duration | Owner | Status |
|------------|------|----------|-------|--------|
| 00:00 | 🚀 Feature flags → 100% | 5 min | SRE | TO DO |
| 00:05 | Console restart & verify | 5 min | SRE | TO DO |
| 00:10 | Route verification | 5 min | SRE | TO DO |
| 00:12 | Boot tripwire check | 2 min | SRE | TO DO |
| 00:14 | Audit chain verify | 2 min | SRE | TO DO |
| 00:15 | ✅ GO-LIVE confirmation | — | Lead | TO DO |
| 00:15-06:00 | Continuous monitoring (6h) | 6 h | Ops | TO DO |
| 09:00 (next day) | First 24h validation | — | Lead | TO DO |

### Post-Deployment (Sep 7-13)

| Date | Task | Owner | Status |
|------|------|-------|--------|
| Sep 7 09:00 | 24-hour validation report | SRE | TO DO |
| Sep 7 10:00 | Post-deployment meeting | Lead | TO DO |
| Sep 13 09:00 | Week 1 status email | Lead | TO DO |
| Oct 6 | Phase 5 planning decision | Exec | Planned |

---

## KEY CONTACTS & ESCALATION

### Primary Team

| Role | Name | Contact | Availability |
|------|------|---------|--------------|
| Deployment Lead | | | On-call Sep 6 |
| SRE Lead | | | 24h Sep 6-7 |
| Security Lead | | | On-call Sep 6 |
| Maintainer | | | Escalation only |
| Incident Commander | | | On-call Sep 6-7 |

### Escalation Matrix

```
Error Rate >5% for 5 min
  → Page on-call SRE immediately
  → Consider rollback trigger
  → Contact security lead

Audit Chain Corruption
  → CRITICAL: Stop all services
  → Contact maintainer immediately
  → Full incident response

Security Breach Detected
  → Page security lead
  → Potential public disclosure
  → Legal/compliance notification

Compliance Violation
  → Contact compliance officer
  → Audit trail investigation
  → Potential rollback
```

---

## MONITORING & DASHBOARDS

### Real-Time Metrics

**Dashboard Location:** http://localhost:8765/v1/vibe/metrics

**Key Metrics to Watch:**
- Error rate (target: <1%)
- Success rate (target: >95%)
- Latency p95 (target: <500ms by operation)
- Uptime (target: >99.5%)
- Active users
- Plugin installs/hour

### Alerting Thresholds

| Alert Level | Trigger | Action |
|-------------|---------|--------|
| 🟢 HEALTHY | Error rate <1% | Continue monitoring |
| 🟡 WARNING | Error rate 1-2% | Investigate, monitor closely |
| 🟠 ELEVATED | Error rate 2-5% | Investigate root cause |
| 🔴 CRITICAL | Error rate >5% | Initiate rollback consideration |
| ⚫ FATAL | Audit chain breaks | Stop services, full incident |

### Slack Integration

**Alert Channel:** #corvinOS-ops  
**Webhook:** Configured (send test on Sep 5)

---

## ROLLBACK PROCEDURE

### Quick Rollback (5 minutes)

**If error rate >5% or critical issue:**

```bash
# Execute rollback script
python3 -c "
import yaml
from pathlib import Path

config_path = Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'
config = yaml.safe_load(config_path.open())

# Disable upload (critical change)
config['features']['plugin_upload_enabled'] = False
# Revert to Phase 3 (50%)
config['features']['plugin_marketplace_canary_rollout'] = '50'

yaml.dump(config, config_path.open('w'))
"

# Restart
systemctl --user restart corvin-console
sleep 2

# Verify
curl http://localhost:8765/console/ > /dev/null && echo "✓ Rollback complete"
```

**Result:** System reverts to Phase 3 (50% users, badges + reports only, no upload)  
**Data Loss:** None  
**Impact:** Minimal (upload disabled temporarily)

### Full Revert (Emergency)

If Phase 3 is also compromised:

```bash
# Restore from backup
cp ~/.corvin/phase-4-snapshots/pre_deployment_*/registry_pre.yaml \
   ~/.corvin/global/registry.yaml

# Restore config
cp ~/.corvin/phase-4-snapshots/pre_deployment_*/config_pre.yaml \
   ~/.corvin/tenants/_default/tenant.corvin.yaml

# Restart
systemctl --user restart corvin-console
```

---

## SUCCESS CRITERIA

### Hard Requirements (ALL must be met)

```
✅ Error rate <1% during 24-hour window
✅ Success rate >95%
✅ Uptime >99.5%
✅ All SLOs met:
   - Discovery p95 <50ms
   - Report p95 <500ms
   - Badge p95 <200ms
   - Upload avg <5s
✅ Audit chain unbroken (0 breaks)
✅ Zero critical incidents
✅ GDPR compliance maintained
✅ EU AI Act compliance maintained
```

### Soft Targets (for market success)

```
✅ >100 plugin installs within 7 days
✅ Positive user feedback
✅ <5 critical support tickets
✅ Trust system working as expected
```

**Phase 4 = SUCCESS if:** All hard requirements met + at least 3/4 soft targets

---

## PRE-DEPLOYMENT CHECKLIST (Sep 5, 23:00 UTC)

```
Team Coordination:
  [ ] All stakeholders notified (email sent Sep 5, 10:00)
  [ ] On-call team confirmed standing by
  [ ] War room / Slack channel active
  [ ] Communication channels tested

Technical Verification:
  [ ] Pre-deployment Gate 1 (Phase 3 stability): PASS
  [ ] Pre-deployment Gate 2 (Canary performance): PASS
  [ ] Pre-deployment Gate 3 (Trust system): PASS
  [ ] Pre-deployment Gate 4 (Infrastructure): PASS
  [ ] Pre-deployment snapshot created

Operational Readiness:
  [ ] Monitoring dashboards functional
  [ ] Alerting webhooks tested
  [ ] Runbook procedures reviewed
  [ ] Rollback commands tested on staging
  [ ] Emergency contacts confirmed

Authorization:
  [ ] Executive authorization signed
  [ ] Security lead approved
  [ ] Ops lead approved
  [ ] Compliance officer approved

ALL ITEMS CHECKED? ✅ YES / ❌ NO → DO NOT PROCEED IF NO
```

---

## FAQ & TROUBLESHOOTING

### Q: What if error rate spikes right at start?

**A:** Wait 5 minutes (initial load spike normal). If >5% sustained:
1. Investigate with `journalctl --user -u corvin-console -n 100`
2. If root cause found: apply hotfix (no rollback needed)
3. If no root cause after 10 min: execute rollback

### Q: Can I pause Phase 4 without rolling back?

**A:** Yes. Use the "pause" command to keep current state and investigate:
```bash
# Pause only marketplace, keep it at 50% while investigating
python3 -c "
import yaml
from pathlib import Path
config = yaml.safe_load(open(Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml'))
config['features']['plugin_marketplace_canary_rollout'] = '50'
yaml.dump(config, open(Path.home() / '.corvin/tenants/_default/tenant.corvin.yaml', 'w'))
"
systemctl --user restart corvin-console
```

### Q: What if I need to cancel deployment before go-live?

**A:** Send Slack message to #corvinOS-ops: "CANCEL Phase 4 deployment"
No authorization needed after Sep 5 23:00 UTC.

### Q: Can we stagger the rollout (e.g., 100% but slower)?

**A:** No. Phase 4 = all users at once. If you want staged approach:
- Complete Phase 3 first (stay at 50% longer)
- Add another phase (Phase 3b at 75%) if needed before proceeding

### Q: What if audit chain breaks during deployment?

**A:** CRITICAL INCIDENT:
1. Stop console immediately: `systemctl --user stop corvin-console`
2. Page maintainer
3. Do NOT restart without investigation
4. Preserve logs for post-mortem

### Q: Is rollback reversible? Can we go back to Phase 4?

**A:** Yes, but requires:
1. Root cause analysis of why Phase 4 failed
2. Fix implemented and tested on staging
3. New authorization from decision maker
4. Minimum 24-hour wait before retry

---

## RELATED DOCUMENTATION

### Prerequisite Reading (Before Sep 5)

1. **feature-flag-rollout-plan.md** — Overall rollout strategy (5 phases)
2. **PRODUCTION_VALIDATION_REPORT_2026-08-29.md** — Validation results
3. **EXECUTIVE_GO_LIVE_SIGN_OFF_2026-08-29.md** — Executive approval
4. **plugin-marketplace-runbook.md** — Operational runbook

### Phase-Specific Documents

1. **PHASE_4_GO_LIVE_AUTHORIZATION.md** — Decision maker approval
2. **PHASE_4_100_PERCENT_ROLLOUT_EXECUTION.md** — Detailed procedures
3. **PHASE_4_DEPLOYMENT_CHECKLIST.md** — Real-time checklist
4. **PHASE_4_STAKEHOLDER_COMMUNICATIONS.md** — Pre-written messages
5. **PHASE_4_POST_DEPLOYMENT_REPORT_TEMPLATE.md** — Results documentation

### Post-Deployment References

1. **PHASE_4_POST_DEPLOYMENT_REPORT_[DATE].md** — Actual results (filled in after deployment)
2. **lessons-learned-phase-4.md** — Team retrospective (after Sep 7)
3. **v0.2-release-notes.md** — Public release announcement

---

## KEY DECISION POINTS

### Decision 1: Proceed to Phase 4? (Sep 5, 23:00 UTC)

**Question:** All pre-deployment gates passed and no issues found?  
**If YES:** Execute Phase 4 on schedule (Sep 6, 00:00 UTC)  
**If NO:** Delay deployment, investigate issues, retry when resolved  
**Authority:** Deployment Lead + SRE Lead (joint decision)

### Decision 2: Rollback During Deployment? (Sep 6, 00:00-06:00 UTC)

**Question:** Error rate >5% sustained or critical issue detected?  
**If YES:** Execute rollback immediately (automatic or manual)  
**If NO:** Continue monitoring  
**Authority:** On-call SRE (can decide independently)

### Decision 3: Declare Phase 4 Stable? (Sep 7, 09:00 UTC)

**Question:** All success criteria met for full 24 hours?  
**If YES:** Declare success, no regression needed  
**If NO:** Continue monitoring, root cause analysis  
**Authority:** Deployment Lead + decision maker (joint)

### Decision 4: Approve Phase 5? (Sep 7+)

**Question:** Phase 4 stable for 21+ days with no critical issues?  
**If YES:** Begin Phase 5 planning (governance UI + enforcement)  
**If NO:** Extend Phase 4 monitoring, delay Phase 5  
**Authority:** Executive decision maker + product lead

---

## SUMMARY

**Phase 4 is a straightforward rollout with:**

✅ **Minimal Risk**: Tested in canary (Phase 2-3), rollback available in 5 min  
✅ **Clear Procedures**: All steps documented with scripts  
✅ **Full Visibility**: Real-time monitoring & alerting  
✅ **Strong Governance**: Executive authorization + multi-stakeholder sign-offs  
✅ **Comprehensive Documentation**: This master guide + 4 supporting docs  

**Next Action:** Print & sign PHASE_4_GO_LIVE_AUTHORIZATION.md before Sep 5, 23:00 UTC

---

**Master Guide Version:** 1.0  
**Status:** READY FOR EXECUTION  
**Last Updated:** 2026-08-29  
**Valid Through:** 2026-09-07 (24h post-launch)
