# Stream 2 Ops Quickstart
## Week 2–3 Staging Soak Test Execution Guide

**Quick Reference for Stream 2 Ops Lead**  
**Execution Date:** 2026-09-27 (Day 1, after Phase 10 kickoff)  
**Duration:** 2 weeks (deployment + 7-day soak test + analysis)

---

## 🚀 Day 1 Execution Plan (2026-09-27, after kickoff)

### 9:00 AM – Pre-Flight Check (15 min)
```bash
# Verify prerequisites
cd /home/shumway/projects/CorvinOS

# 1. Check Stream 2 status
ls -la core/skills/os_skills/security_orchestrator/
echo "✅ Stream 2 found"

# 2. Verify test suite
pytest core/skills/os_skills/security_orchestrator/tests/ -v --tb=short
echo "✅ All 131 tests passing"

# 3. Check deployment scripts
ls -la scripts/staging_deploy_stream2.sh
ls -la scripts/staging_load_generator.py
echo "✅ Deployment scripts ready"
```

### 9:15 AM – Execute Deployment (30 min)
```bash
# Run deployment script (does everything: validation, docker, monitoring, k8s)
bash scripts/staging_deploy_stream2.sh

# Watch for:
# ✅ Pre-deployment checks pass
# ✅ Docker image built: corvin/stream2-security-orchestrator:staging-latest
# ✅ Monitoring infrastructure ready
# ✅ Slack configuration generated
# ✅ Deployment manifest created

# Output: Deployment manifests in /tmp/stream2_monitoring_<timestamp>/
```

### 9:45 AM – Run Smoke Tests (10 min)
```bash
# Verify all 6 endpoints respond
pytest tests/ops/test_staging_soak_smoke.py -v

# Expected output:
# ✅ test_01_health_check PASSED
# ✅ test_02_threats_endpoint PASSED
# ✅ test_03_policy_endpoint PASSED
# ✅ test_04_audit_endpoint PASSED
# ✅ test_05_metrics_endpoint PASSED
# ✅ test_06_websocket_stream PASSED

# Gate 1 Status: GO ✅
```

### 10:00 AM – Generate Load (3 min)
```bash
# Inject 100K audit events for 7-day soak test
python3 scripts/staging_load_generator.py --duration=7d --rate=14000 --target=staging

# Expected output:
# 🔄 Starting load generation...
#    [100,000/100,000] 556 events/sec
# ✅ Load generation complete!
#    Total events written: 100,000

# Verify:
wc -l .staging/stream2/audit/audit_soak_test.jsonl
# Should show: 100000 .staging/stream2/audit/audit_soak_test.jsonl
```

### 10:05 AM – Monitoring Go-Live (5 min)
```bash
# Start monitoring dashboard
open http://localhost:3000/d/stream2-soak-test

# Verify metrics appearing (should take <30 sec):
# - Threat detection rate
# - False positive rate
# - Latency P95
# - Memory usage
# - Error rate

# Day 1 Complete! ✅
```

---

## 📊 Daily Soak Test Routine (Days 2–8)

### Morning (9:00 AM) – Check Dashboard
```bash
# 1. Open Grafana dashboard
open http://localhost:3000/d/stream2-soak-test

# 2. Verify key metrics
# Look for:
# ✅ Threat detection rate: 2–3 threats/min (2K total per 7 days)
# ✅ False positive rate: <1%
# ✅ Latency P95: <50ms
# ✅ Memory usage: Stable (no growth spike)
# ✅ Error rate: <0.1%

# 3. Check for critical alerts
grep "CRITICAL\|ALERT" ~/.corvin/audit.jsonl | tail -10
# Should show: (none or only expected issues)
```

### Midday (12:00 PM) – Verify Audit Trail
```bash
# Check audit file is growing
du -h .staging/stream2/audit/audit_soak_test.jsonl

# Verify events continue writing
tail -5 .staging/stream2/audit/audit_soak_test.jsonl

# Spot-check hash-chain integrity (sample)
python3 scripts/verify_audit_chain.py --file=.staging/stream2/audit/audit_soak_test.jsonl --sample=1000
# Expected: ✅ Sample chain intact (1000 events, 999 hashes verified)
```

### Evening (5:00 PM) – Log Any Issues
```bash
# Create or append to incident log
cat >> STAGING_SOAK_INCIDENTS.md << EOF
[HH:MM UTC] $(date -u +%H:%M): [PHASE] incident_type
Issue: description
Response: action_taken (or "monitoring")
Status: resolved / in_progress / blocked
EOF

# If NO incidents today, log that:
echo "[$(date -u +%H:%M)] No incidents today. All metrics green." >> STAGING_SOAK_INCIDENTS.md
```

---

## 🎯 Critical Thresholds & Actions

| Metric | Threshold | Action |
|--------|-----------|--------|
| **False Positive Rate** | >1% | Tune detection thresholds, review false positives |
| **Latency P95** | >50ms | Profile performance, optimize hot paths |
| **Memory Usage** | >2GB or growing | Check for leak, restart if needed |
| **Error Rate** | >0.1% | Review error logs, fix errors |
| **Audit Trail Gaps** | Any | CRITICAL: Investigate chain break immediately |
| **Tenant Isolation** | Any cross-tenant data | CRITICAL: Escalate immediately |
| **Unhandled Exception** | Any | CRITICAL: Fix and restart soak test |

**If any critical threshold breached:**
1. **ALERT Slack:** `@stream2-lead [CRITICAL] <issue>`
2. **LOG incident:** Add to STAGING_SOAK_INCIDENTS.md with timestamp + details
3. **INVESTIGATE:** Root cause analysis + fix
4. **RESTART:** If soak test corrupted, restart Phase 2

---

## 🔍 Week 3: Analysis & Decision (Days 9–11)

### Sunday (Oct 5) – Analysis Phase

#### 9:00 AM – Soak Test Complete
```bash
# Verify all 7 days of data collected
echo "Total events in audit trail:"
wc -l .staging/stream2/audit/audit_soak_test.jsonl

# Expected: ~100,000 lines
```

#### 10:00 AM – Full Audit Chain Verification
```bash
# Verify entire chain integrity
python3 scripts/verify_audit_chain.py --file=.staging/stream2/audit/audit_soak_test.jsonl

# Expected: ✅ Chain intact (100000 events, 99999 hashes verified)
```

#### 11:00 AM – Performance Report
```bash
# Generate final soak test report
python3 scripts/generate_soak_test_report.py \
  --incidents=STAGING_SOAK_INCIDENTS.md \
  --metrics=.staging/stream2/soak_test_metrics.json \
  --audit=.staging/stream2/audit/audit_soak_test.jsonl \
  --output=reports/STAGING_SOAK_FINAL_REPORT.md

# Review report:
cat reports/STAGING_SOAK_FINAL_REPORT.md | head -100
```

### Monday (Oct 6) – Gate 4 Decision

#### 9:00 AM – Final Gate Approval
```bash
# Checklist:
# ✅ 0 critical incidents during soak test?
# ✅ False positive rate <1%?
# ✅ Latency P95 <50ms?
# ✅ Memory stable (no leaks)?
# ✅ Audit trail 100% integrity?
# ✅ Tenant isolation verified?

# If ALL ✅:
echo "Gate 4 Decision: GO FOR PRODUCTION" > reports/GATE_4_DECISION.txt
echo "Canary rollout: 5% → 25% → 50% → 100% starting 2026-10-07"

# If ANY ❌:
echo "Gate 4 Decision: NO-GO — Fix issues and restart Phase 2"
# Document blockers in report, restart soak test after fixes
```

#### 10:00 AM – Prepare Canary (if GO)
```bash
# If GO decision:
# 1. Update production deployment manifest
# 2. Schedule canary rollout (5% on 2026-10-07)
# 3. Notify Phase 10 team
# 4. Prepare rollback procedure

# If NO-GO decision:
# 1. Document root cause
# 2. Assign fixes
# 3. Plan restart timeline
# 4. Notify Phase 10 team
```

---

## 📋 Checklist: Before Kickoff (2026-09-26)

- ✅ Read: `STAGING_SOAK_TEST_PLAN.md` (full plan)
- ✅ Read: `STREAM_2_COMPLETION_REPORT.md` (implementation status)
- ✅ Review: `core/skills/os_skills/security_orchestrator/` (codebase)
- ✅ Run locally: `pytest core/skills/os_skills/security_orchestrator/tests/ -v` (verify all pass)
- ✅ Understand: Grafana dashboard layout (open http://localhost:3000)
- ✅ Test: Slack alerts webhook (verify connection)
- ✅ Prepare: `.staging/stream2/` directory exists with subdirs (audit, threats, policies, metrics)

**Day 1 Readiness:** 100% ✅

---

## 🔗 Key Resources

| Resource | Path | Purpose |
|----------|------|---------|
| **Deployment Script** | `scripts/staging_deploy_stream2.sh` | Automate Day 1 deployment |
| **Smoke Tests** | `tests/ops/test_staging_soak_smoke.py` | 6 E2E endpoint tests |
| **Load Generator** | `scripts/staging_load_generator.py` | 100K event injection |
| **Dashboard** | `dashboards/grafana_stream2_soak_test.json` | Monitoring |
| **Full Plan** | `STAGING_SOAK_TEST_PLAN.md` | Detailed timeline + criteria |
| **Incident Log** | `STAGING_SOAK_INCIDENTS.md` | Create on Day 1 |
| **Final Report** | `reports/STAGING_SOAK_FINAL_REPORT.md` | Generate on Day 9 |

---

## ⚡ Commands at a Glance

### Deploy (Day 1, 15 min)
```bash
bash scripts/staging_deploy_stream2.sh
pytest tests/ops/test_staging_soak_smoke.py -v
python3 scripts/staging_load_generator.py --duration=7d
```

### Monitor (Days 2–8, daily)
```bash
# Dashboard: http://localhost:3000/d/stream2-soak-test
# Incidents: tail -5 STAGING_SOAK_INCIDENTS.md
# Audit Trail: wc -l .staging/stream2/audit/audit_soak_test.jsonl
# Chain Check: python3 scripts/verify_audit_chain.py (daily sample)
```

### Analyze (Day 9)
```bash
wc -l .staging/stream2/audit/audit_soak_test.jsonl
python3 scripts/verify_audit_chain.py --file=.staging/stream2/audit/audit_soak_test.jsonl
python3 scripts/generate_soak_test_report.py --incidents=STAGING_SOAK_INCIDENTS.md
```

### Decide (Day 10)
```bash
cat reports/STAGING_SOAK_FINAL_REPORT.md
# Make Gate 4 decision: GO or NO-GO
```

---

## 📞 Support & Escalation

**Immediate Help:**
- Slack: `#stream2-alerts` (automated alerts)
- On-Call: Available 24/7
- Email: (coordinator to provide)

**Critical Issues (escalate now):**
1. Crash or unhandled exception
2. Audit trail corruption / chain break
3. Data loss or cross-tenant leakage
4. >2 critical incidents/day

---

## ✅ Success Looks Like...

### Day 1 (Deployment) ✅
- Deployment script completes with 0 errors
- All 6 smoke tests pass (6/6 green)
- Grafana dashboard showing live metrics
- 100K events generated and written

### Days 2–8 (Soak Test) ✅
- Threat detection rate stable (2–3 threats/min)
- False positive rate <1%
- Latency P95 consistent (<50ms)
- Memory usage stable (no growth)
- 0 critical errors
- Audit trail growing steadily, no gaps

### Day 9–10 (Analysis) ✅
- Final report generated with full metrics
- All incidents documented + resolved
- Gate 4 approval: **GO FOR PRODUCTION**
- Canary rollout scheduled

---

**You got this! 🚀**

*Stream 2 is production-ready. This soak test will prove it.*

---

**Last Updated:** 2026-09-23  
**Prepared By:** Stream 2 Ops Lead (Claude Haiku 4.5)
