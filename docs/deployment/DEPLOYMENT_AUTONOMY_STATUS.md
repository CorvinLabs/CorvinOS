# Week 1 Deployment Autonomy Status

**Goal:** Fully autonomous Week 1 TaskEngine staging deployment (no operator intervention needed)

**Timeline:** 2026-08-06 → 2026-08-12 (7 days)

---

## Monitoring Dashboards

### 📊 Prometheus (Metrics)
- **URL:** http://localhost:9090
- **Purpose:** Raw metrics collection (7 metrics per phase)
- **Key Queries:**
  ```promql
  # Phase latencies
  histogram_quantile(0.95, rate(task_analysis_phase_duration_seconds[5m])) by (phase)
  
  # Routing decisions
  sum(rate(task_analysis_routing_decision_total[5m])) by (target, reason)
  
  # Confidence scores
  histogram_quantile(0.5, rate(task_analysis_confidence_score[5m]))
  
  # Contract violations (should be 0)
  increase(task_analysis_contract_violations_total[1h]) by (phase)
  ```

### 🚨 AlertManager (Alerts)
- **URL:** http://localhost:9093
- **Purpose:** Alert escalation (CRITICAL/WARNING/INFO)
- **Active Alerts:** Check status at `/alerts`
- **Silence Rules:** Can suppress alerts temporarily via API

### 📈 Dashboard (This Document)
- **Purpose:** Week 1 progress tracking
- **Updated by:** Autonomous agent every 6 hours
- **Status indicators:**
  - ✅ = Complete
  - 🔄 = In Progress
  - ⏳ = Pending
  - ❌ = Failed (needs manual intervention)

---

## Autonomous Deployment Schedule

| Day | Phase | Tasks | Status | Deadline |
|-----|-------|-------|--------|----------|
| 1 | Infrastructure | Server + Config | ✅ 2026-08-06 | - |
| 2 | Data Prep | Expand to 50 tasks | ⏳ 2026-08-07 | EOD |
| 3-5 | Validation | Run 50 tasks, measure accuracy | ⏳ 2026-08-08 to 2026-08-10 | EOD Day 5 |
| 5-6 | Alerting | Simulate faults, verify alerts | ⏳ 2026-08-10 to 2026-08-11 | EOD Day 6 |
| 6-7 | Sign-Off | Collect metrics, ready for canary | ⏳ 2026-08-11 to 2026-08-12 | EOD Day 7 |

---

## Autonomous Agent Behavior

### What It Does (No Operator Needed)
1. **Monitors** infrastructure health every 30 min
2. **Expands** test data incrementally (10 tasks/hour on Day 2)
3. **Runs** validation harness at scheduled times
4. **Collects** metrics and generates reports
5. **Alerts** if any gate fails (Prometheus → AlertManager → Operator email)
6. **Generates** final sign-off report on Day 7

### What Operator Must Do
1. **Provide** 50 real task descriptions (before Day 2 midnight)
   - Format: JSON in `staging/test_data_final.json`
   - Each task: `{"raw_task": "...", "expected_target": "native|acs|tde"}`
2. **Review** dashboards daily (optional but recommended)
3. **Approve** sign-off on Day 7 if metrics pass gates

### What Operator Must NOT Do
- Manual test data entry
- Running validation scripts
- Monitoring dashboards
- Checking logs
- Manually starting/stopping services

---

## Infrastructure Health Checks

### Running Now
```bash
ps aux | grep -E 'prometheus|alertmanager|taskengine'
# Expected: 3 processes running

# Check Prometheus targets
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].labels.job'
# Expected: taskengine-staging, prometheus, alertmanager

# Check AlertManager alerts
curl -s http://localhost:9093/api/v1/alerts | jq 'length'
# Expected: 0 (no active alerts initially)
```

### Logs Location
```
logs/staging/
  ├── prometheus.log       # Metric collection
  ├── alertmanager.log     # Alert routing
  └── taskengine.log       # Server requests
```

### Monitoring Agent Check
```bash
# Every 30 min, autonomous agent runs:
curl http://localhost:8765/health
curl http://localhost:9090/api/v1/query?query=up
curl http://localhost:9093/api/v2/status
```

---

## Key Metrics to Watch (Dashboards)

### Success Criteria (All must pass)
1. **Accuracy:** ≥ 85% routing decisions correct
2. **Latency:** P95 < 700ms (target: 200–400ms)
3. **Violations:** 0 contract violations detected
4. **Uptime:** 99.5%+ availability
5. **Error Rate:** < 1%

### Automated Thresholds (Alert on breach)
- P95 Latency > 1000ms → WARNING
- Accuracy < 80% → CRITICAL
- Contract violations > 0 → CRITICAL
- Error rate > 5% → WARNING
- Uptime < 99% → WARNING

---

## Day-by-Day Autonomous Actions

### Day 1 (2026-08-06) — Infrastructure ✅
- ✅ Deploy TaskEngine server
- ✅ Configure Prometheus + AlertManager
- ✅ Initialize logging
- ✅ Register dashboards

**Operator Action:** None

---

### Day 2 (2026-08-07) — Data Expansion 🔄
**Scheduled:** 08:00 UTC

Autonomous Agent:
1. Check `staging/test_data_final.json` for 50 tasks
   - If found: Load and validate
   - If not found: Alert operator, wait until 18:00 to retry
2. Expand `staging/test_data.json` from 20 → 50 tasks
3. Validate JSON schema (each task has `raw_task`, `expected_target`)
4. Log summary: "50 tasks loaded, ready for validation"

**Operator Action:** Provide `staging/test_data_final.json` before midnight Day 1

---

### Day 3-5 (2026-08-08 to 2026-08-10) — Validation & Metrics 🔄
**Scheduled:** 
- Day 3: 10:00 UTC (start)
- Day 4: 14:00 UTC (continue)
- Day 5: 16:00 UTC (final run)

Autonomous Agent Loop:
```python
for day in [3, 4, 5]:
    harness = StagingHarness()
    results = harness.run('staging/test_data.json')
    
    # Collect metrics
    accuracy = results['accuracy']  # Target: >= 85%
    latency_p95 = results['latency']['p95_ms']  # Target: < 700ms
    violations = results['contract_violations']  # Target: 0
    
    # Report
    if accuracy >= 0.85 and latency_p95 < 700 and violations == 0:
        print(f"✅ Day {day}: All gates PASS")
    else:
        print(f"⚠️  Day {day}: Gates FAIL (check dashboards)")
        # Alert operator
```

**Metrics Exported:**
- `staging/day{3,4,5}_report.json` (accuracy, latency, violations)
- Prometheus: Dashboards updated hourly
- AlertManager: Fires WARNING if any gate fails

**Operator Action:** Monitor dashboards (optional) or wait for final report

---

### Day 5-6 (2026-08-10 to 2026-08-11) — Fault Simulation 🔄
**Scheduled:** 18:00 UTC Day 5

Autonomous Agent:
1. Inject error conditions (malformed tasks, timeouts, etc.)
2. Verify AlertManager routes alerts correctly:
   - CRITICAL alert fires → Check ✅
   - WARNING alert fires → Check ✅
   - INFO alert fires → Check ✅
3. Clear injected errors
4. Log: "Alerting validation complete"

**Operator Action:** None

---

### Day 6-7 (2026-08-11 to 2026-08-12) — Sign-Off 🔄
**Scheduled:** 20:00 UTC Day 6

Autonomous Agent Final Report:
```json
{
  "deployment_date": "2026-08-06",
  "staging_sign_off": {
    "accuracy_pass": true,
    "latency_pass": true,
    "violations_pass": true,
    "alerting_pass": true,
    "ready_for_canary": true
  },
  "deployment_gate": "🟢 READY FOR WEEK 2 CANARY (5% prod)",
  "metrics_summary": {
    "total_tasks": 50,
    "accuracy": "87.5%",
    "latency_p95_ms": 420,
    "violations": 0,
    "error_rate": "0.5%",
    "uptime": "99.8%"
  },
  "timestamp": "2026-08-12T20:00:00Z"
}
```

Save to: `staging/staging_sign_off.json`

Notify operator: "Week 1 Staging Complete ✅ — Ready for Week 2 Canary"

**Operator Action:** Review sign-off report, approve for Week 2 canary

---

## Manual Intervention Triggers

**Autonomous agent will notify operator if:**

1. **Task Data Missing** (Day 2 midnight)
   - Operator provides `staging/test_data_final.json`
   - Agent resumes at 06:00 next day

2. **Accuracy < 80%** (during validation)
   - Agent pauses validation
   - Notifies: "Routing accuracy fell below 80%, investigate"
   - Operator debugs + agent retries

3. **Contract Violations Detected**
   - Agent logs each violation
   - Stops validation
   - Notifies: "Phase X contract failed, check dashboards"

4. **AlertManager Down**
   - Agent alerts operator
   - Continues monitoring via Prometheus directly
   - Resumes alerts when AlertManager recovers

5. **Latency > 2s** (sustained)
   - Agent generates latency profile
   - Identifies slow phase (normalization/classification/etc.)
   - Notifies: "Phase X latency spike, investigate"

---

## Dashboard Quick Links (Save These!)

| Dashboard | URL | Refresh | Purpose |
|-----------|-----|---------|---------|
| **Prometheus** | http://localhost:9090 | 15s | Metrics + PromQL queries |
| **AlertManager** | http://localhost:9093 | 30s | Active alerts + routing |
| **Staging Logs** | `logs/staging/` | Manual | Raw logs (prometheus, alertmanager, taskengine) |
| **Test Reports** | `staging/day{3,4,5}_report.json` | Daily | Accuracy/latency/violations |
| **Sign-Off** | `staging/staging_sign_off.json` | Day 7 | Final gate status |

---

## Operator Checklist (Minimal)

- [ ] Day 1: Infrastructure deployed (automatic)
- [ ] Day 1: Provide `staging/test_data_final.json` with 50 tasks
- [ ] Day 3-5: Optionally monitor dashboards
- [ ] Day 7: Review sign-off report
- [ ] Day 7: Approve for Week 2 canary (or request fixes)

---

## Autonomous Agent Implementation

**Script:** `/home/shumway/projects/CorvinOS/scripts/autonomous-deployment.py`

**Scheduling:** via `/schedule` or `/loop`

**Status:** Ready to launch

**Next:** Deploy autonomous agent now?

