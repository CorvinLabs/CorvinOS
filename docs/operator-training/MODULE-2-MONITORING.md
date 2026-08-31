# Brain v0.2 Operator Training — Module 2: Monitoring Dashboard
## 45-Minute Hands-On Guide

**Version:** 1.0 (2026-08-23)  
**Target Audience:** Production operators, on-call engineers  
**Prerequisite:** Module 1 (Architecture) — basic understanding of subsystems  
**Outcome:** Read the monitoring dashboard, interpret metrics, set up alerting

---

## Learning Objectives

By the end of this module, you will:
1. Access the Brain v0.2 monitoring dashboard
2. Interpret 10 key metrics (error rate, latency, memory, throughput)
3. Recognize healthy vs. unhealthy system state
4. Set up Prometheus alerts for 5 critical conditions
5. Respond to a metric anomaly with informed action

---

## Section 1: Dashboard Access & Layout (10 minutes)

### How to Access

**Option 1: Web UI (Recommended)**
```bash
# Open in browser
http://localhost:9090/graph

# Or from production
https://corvin-monitoring.internal/graph
```

**Option 2: CLI**
```bash
corvin metrics query 'rate(corvin_requests_total[5m])'
```

### Dashboard Home

The main dashboard shows **4 status cards** across the top:

| Card | What It Shows | Healthy State | Red Flag |
|------|---------------|---|---|
| **ENGINE** | Claude Code engine status | Green dot + "responding" | Red dot or timeout |
| **API KEYS** | Credential vault status | 4/4 keys stored | Red dot = missing key |
| **SUBSYSTEMS** | Count of healthy subsystems | 13/13 online | <13 online |
| **AUDIT LOG** | Hash-chain integrity | Green dot + "verified" | Red dot = chain broken |

### Key Metric Groups

```
┌─────────────────────────────────────┐
│  BRAIN v0.2 MONITORING DASHBOARD   │
├─────────────────────────────────────┤
│ Error Rate    │ Latency (p50/p95)  │
│ Throughput    │ Memory Usage       │
├─────────────────────────────────────┤
│ Cost Model    │ Strategy Success   │
│ Tool/Skill    │ Policy Violations  │
├─────────────────────────────────────┤
│ Subsystem Details (expandable)     │
└─────────────────────────────────────┘
```

---

## Section 2: The 10 Critical Metrics (25 minutes)

### Metric 1: Error Rate (PRIMARY KPI)

**PromQL:**
```
rate(corvin_errors_total[5m])
```

**What It Measures:** Percentage of requests that failed in last 5 minutes.

**Healthy Range:** < 1%  
**Warning Level:** 1–2%  
**Critical Level:** > 2% (sustained >5 min)

**Breakdown by Subsystem:**
```
rate(corvin_errors_total{subsystem="cost_controller"}[5m])
rate(corvin_errors_total{subsystem="safety_validator"}[5m])
rate(corvin_errors_total{subsystem="learning_engine"}[5m])
```

**Example:**
```
# At 10:30 UTC
corvin_errors_total = 125 (total errors since startup)
# At 10:35 UTC
corvin_errors_total = 130 (5 new errors in 5 min)
# Rate calculation:
(130 - 125) / 300 seconds = 5 / 300 = 0.0167 = 1.67% error rate
```

**What to Do:**
- < 1%: Normal ✅
- 1–2%: Investigate, but not urgent
- > 2%: Page on-call, check error log for pattern

### Metric 2: Latency p95 (PERFORMANCE KPI)

**PromQL:**
```
histogram_quantile(0.95, corvin_latency_ms)
```

**What It Measures:** The 95th percentile of request latency (95% of requests complete within this time).

**Healthy Baseline:** ~1200 ms  
**Warning Level:** 1300 ms (>8% increase)  
**Critical Level:** 1500 ms (>25% increase)

**Breaking Down by Subsystem:**
```
histogram_quantile(0.95, corvin_latency_ms{subsystem="orchestrator"})
histogram_quantile(0.95, corvin_latency_ms{subsystem="loop_engineer"})
```

**Percentile Distribution:**
```
p50  = 900ms   (half finish by here)
p95  = 1200ms  (95% finish by here)
p99  = 1400ms  (99% finish by here)
max  = 1800ms  (outliers)
```

**Example Scenario:**
```
Baseline (healthy):
  p50 = 900ms, p95 = 1200ms, p99 = 1400ms

After deployment:
  p50 = 920ms, p95 = 1320ms, p99 = 1600ms
  
Verdict: p95 increased by 10% → investigate, but not critical yet
```

**What to Do:**
- Baseline (1200ms): Normal ✅
- 1200–1300ms: Check if related to recent code change
- > 1300ms: Investigate context graph traversal (ADR-0328)

### Metric 3: Latency p50 and p99 (Supporting)

**PromQL:**
```
histogram_quantile(0.50, corvin_latency_ms)  # p50
histogram_quantile(0.99, corvin_latency_ms)  # p99
```

**Why Both Matter:**
- **p50 high:** System is generally slow
- **p95 high, p50 normal:** Some tasks are outliers (GC pause, network blip)
- **p99 high, p95 normal:** Rare pathological cases

**Healthy State:**
```
p50 < 950ms AND p95 < 1300ms AND p99 < 1500ms
```

### Metric 4: Throughput (CAPACITY KPI)

**PromQL:**
```
rate(corvin_requests_total[5m])
```

**What It Measures:** Requests per second over last 5 minutes.

**Expected Range:** 10–100 req/s (depends on deployment)  
**Drop Below Expected:** Possible subsystem hang  
**Spike Above Expected:** Possible load surge or client error

**Example:**
```
Normal: 50 req/s
Suddenly drops to 5 req/s → investigate what happened

Normal: 50 req/s
Suddenly spikes to 500 req/s → likely DDoS or client bug
```

**What to Do:**
- Drops unexpectedly: Page on-call
- Spikes unexpectedly: Check client-side (bridge overload?)

### Metric 5: Memory Usage (STABILITY KPI)

**PromQL:**
```
corvin_process_memory_bytes / 1e6  # Convert bytes to MB
```

**Healthy Baseline:** ~500 MB  
**Warning Level:** > 600 MB (20% over baseline)  
**Critical Level:** > 800 MB (60% over baseline)

**Memory Growth Pattern Analysis:**
```
# Healthy growth: flat or small sawtooth (GC)
Linear: ┌─────────────
        └─────────────  (Memory steadily increases)
        → MEMORY LEAK, restart subsystem

Sawtooth: ∨─∧─∨─∧─∨─  (Peaks then drops)
        → GC working, normal

Spike: ┌──┐
       └──┘─────────── (Single jump, stays high)
       → One-time allocation, then stable
```

**Per-Subsystem Memory:**
```
corvin_process_memory_bytes{subsystem="learning_engine"}
corvin_process_memory_bytes{subsystem="context_bridge"}
corvin_process_memory_bytes{subsystem="tool_forge"}
```

**What to Do:**
- Flat or sawtooth: Normal ✅
- Linear growth: MEMORY LEAK, restart immediately
- Spike then stable: Probably OK, monitor
- > 800 MB: Manual scaling or service restart

### Metric 6: Cost Model Accuracy

**PromQL:**
```
corvin_cost_estimate_error_pct
```

**What It Measures:** |predicted_tokens - actual_tokens| / actual_tokens

**Healthy Range:** < 10% error  
**Warning Level:** 10–20% error  
**Critical Level:** > 20% error (model drift)

**Time Series Analysis:**
```
2026-08-23 10:00 UTC:  5% error (good)
2026-08-23 11:00 UTC:  7% error (good)
2026-08-23 12:00 UTC: 18% error (warning)
2026-08-23 13:00 UTC: 32% error (critical!)

Verdict: Model drift detected around 12:00 UTC
Action: Retrain cost model, investigate what changed
```

**What to Do:**
- < 10%: Normal ✅
- 10–20%: Schedule retraining within 24h
- > 20%: Immediate retraining or disable cost budgeting

### Metric 7: Strategy Success Rate

**PromQL:**
```
rate(corvin_strategy_succeeded_total[5m]) 
  / rate(corvin_strategy_attempted_total[5m])
```

**What It Measures:** Percentage of strategy attempts that succeeded.

**Healthy Range:** > 70%  
**Warning Level:** 50–70%  
**Critical Level:** < 50% (strategies not working)

**Per-Strategy Breakdown:**
```
decompose success rate
retry success rate
escalate success rate
```

**Example:**
```
Decompose: 85% success
Retry: 60% success
Escalate: 40% success

Overall: (successes) / (attempts) = 65%
Verdict: Escalate strategy underperforming, consider retraining
```

**What to Do:**
- > 70%: Normal ✅
- 50–70%: Investigate which strategy is failing
- < 50%: Circuit breaker activates (48h cooldown on failing strategy)

### Metric 8: Tool/Skill Reuse Rate

**PromQL:**
```
corvin_forged_tools_reused_total / corvin_forged_tools_created_total
corvin_skills_reused_total / corvin_skills_created_total
```

**What It Measures:** % of forged tools/skills that are used again.

**Expected Target:** > 30% reuse (tools/skills are valuable)

**Healthy Pattern:**
```
Week 1: 20% (learning, tools not yet discovered)
Week 2: 35% (tools spreading across tasks)
Week 3: 45% (strong patterns identified)
```

**What to Do:**
- > 30%: Good, keep as-is ✅
- < 30%: Tools may be too specific (too narrow context)
- Trending down: Learning system may have regressed

### Metric 9: Policy Violation Count

**PromQL:**
```
corvin_policy_violations_total
```

**What It Measures:** Number of times SafetyValidator blocked an action.

**Healthy State:** < 10 per hour (rare)

**Severity Levels:**
- CRITICAL: Block task start (should never happen in normal ops)
- WARNING: Block specific action (expected, <1 per 100 requests)
- INFO: Logged but allowed (advisory)

**Example:**
```
CRITICAL: "Budget exhausted, refusing task" → failsafe activated
WARNING: "API call would exceed rate limit, retrying" → expected
INFO: "Prompt contains discouraged pattern, but allowed" → advisory
```

**What to Do:**
- < 10/hour: Normal ✅
- 10–100/hour: Something triggered SafetyValidator (check logs)
- > 100/hour: Possible attack or config error, page on-call

### Metric 10: Subsystem Health (Aggregated)

**PromQL:**
```
corvin_subsystem_health_status{subsystem="*"}
```

**What It Measures:** Each subsystem reports health (1=healthy, 0=unhealthy).

**Dashboard View:**
```
✓ HealthMonitor (online, 5ms latency)
✓ ContextBridge (online, 8ms latency)
✓ LoopEngineer (online, 120ms latency)  ← slower than usual
✗ ToolForge (OFFLINE, no heartbeat >5 min)  ← CRITICAL
✓ CostController (online, 2ms latency)
...
```

**What to Do:**
- All green: Normal ✅
- Orange (slower): Monitor, not urgent
- Red (offline): Restart subsystem immediately

---

## Section 3: Alerting Rules You Must Set Up (7 minutes)

### Alert Rule 1: Error Rate Too High

```yaml
alert: HighErrorRate
expr: rate(corvin_errors_total[5m]) > 0.02  # > 2%
for: 5m
labels:
  severity: critical
annotations:
  summary: "Error rate {{$value}}% (threshold 2%)"
  action: "Check error log: tail -f ~/.corvin/tenants/*/audit.jsonl | grep error"
```

### Alert Rule 2: Latency Degradation

```yaml
alert: HighLatency
expr: histogram_quantile(0.95, corvin_latency_ms) > 1300
for: 5m
labels:
  severity: warning
annotations:
  summary: "p95 latency {{$value}}ms (baseline 1200ms)"
  action: "Profile slow subsystem: corvin metrics query 'corvin_latency_ms'"
```

### Alert Rule 3: Memory Leak Detected

```yaml
alert: MemoryLeak
expr: rate(corvin_process_memory_bytes[1h]) > 1e6  # Growing >1MB/min
for: 30m  # Only alert if sustained
labels:
  severity: critical
annotations:
  summary: "Memory growing {{$value}} bytes/min"
  action: "Restart suspect subsystem: corvin restart-subsystem <name>"
```

### Alert Rule 4: Subsystem Offline

```yaml
alert: SubsystemOffline
expr: corvin_subsystem_health_status == 0
for: 2m
labels:
  severity: critical
annotations:
  summary: "{{$labels.subsystem}} offline for >2 min"
  action: "Restart: systemctl restart corvin-subsystem@{{$labels.subsystem}}"
```

### Alert Rule 5: Audit Chain Corruption

```yaml
alert: AuditChainInvalid
expr: corvin_audit_chain_valid == 0
for: 1m
labels:
  severity: critical
annotations:
  summary: "Audit chain integrity check failed!"
  action: "CRITICAL: Do NOT restart. Escalate to maintainer immediately."
```

### How to Deploy These Alerts

```bash
# 1. Create alert rules file
cat > /etc/prometheus/rules/corvin-alerts.yml << 'EOF'
groups:
  - name: corvin_alerts
    rules:
      - alert: HighErrorRate
        expr: rate(corvin_errors_total[5m]) > 0.02
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Error rate {{$value}}%"
EOF

# 2. Reload Prometheus
systemctl reload prometheus

# 3. Verify rules loaded
curl http://localhost:9090/api/v1/rules | grep corvin_alerts
```

---

## Section 4: Reading a Live Dashboard (3 minutes)

### Healthy System Snapshot (You Should See This)

```
TIME: 2026-08-23 10:30 UTC

ERROR RATE:        0.3%  ✓ (target: <1%)
LATENCY p95:     1210ms  ✓ (target: <1300ms)
THROUGHPUT:       65 req/s ✓ (normal)
MEMORY:           510 MB  ✓ (stable)
COST ERROR:        7%    ✓ (<10%)
STRATEGY SUCCESS:  78%    ✓ (>70%)
TOOL REUSE:        32%    ✓ (>30%)
POLICY VIOLATIONS: 2/hour ✓ (<10/hour)

SUBSYSTEMS (all green):
  ✓ HealthMonitor     (3ms)
  ✓ ContextBridge     (8ms)
  ✓ LoopEngineer    (120ms)
  ✓ CostController    (2ms)
  ✓ SafetyValidator   (1ms)
  ✓ ToolForge        (45ms)
  ✓ SkillForge       (32ms)
  ... (all 13 online)
```

### Unhealthy System Snapshot (RED FLAGS!)

```
TIME: 2026-08-23 11:45 UTC

ERROR RATE:        3.2%  🔴 (exceeds 2%)
LATENCY p95:     1650ms  🔴 (exceeds 1300ms)
THROUGHPUT:       8 req/s  🔴 (below normal 65)
MEMORY:           750 MB  🟡 (spiked 240 MB in 5 min)
COST ERROR:       28%     🔴 (exceeds 20%)
STRATEGY SUCCESS:  42%    🔴 (below 50%)
POLICY VIOLATIONS: 85/hour 🔴 (exceeds 10/hour)

SUBSYSTEMS (multiple issues):
  ✓ HealthMonitor     (3ms)
  ✗ ContextBridge    (OFFLINE >5 min) 🔴
  🟡 LoopEngineer    (2100ms, >2x normal)
  ✗ CostController   (TIMEOUT, no response) 🔴
  ✓ SafetyValidator   (1ms)
  ✓ ToolForge        (45ms)
  ✓ SkillForge       (32ms)
  ... (11/13 online)
```

**IMMEDIATE ACTIONS:**
1. Page on-call engineer
2. Restart CostController and ContextBridge
3. Check error log for pattern
4. Prepare rollback (if needed in next 10 min)

---

## Quick Reference: Metric Thresholds

| Metric | Healthy | Warning | Critical | Action |
|--------|---------|---------|----------|--------|
| Error Rate | <1% | 1–2% | >2% (5m) | Investigate error log |
| Latency p95 | <1300ms | 1300–1500ms | >1500ms | Check CPU, profile |
| Memory | <600MB | 600–800MB | >800MB | Check for leaks |
| Throughput | 50–100 r/s | <50 r/s | 0 r/s | Restart subsystem |
| Cost Error | <10% | 10–20% | >20% | Retrain model |
| Strategy Success | >70% | 50–70% | <50% | Investigate strategy |
| Policy Violations | <10/hr | 10–100/hr | >100/hr | Check SafetyValidator |
| Subsystems | 13/13 | <13 | 0 | Restart all |

---

## Self-Check Questions

1. **Your error rate jumps from 0.5% to 2.5% at 10:30 UTC. What's your first action?**  
   _Answer: Check error log for pattern. Is it all subsystems or specific one? Page on-call if sustained >5 min._

2. **Memory grows from 500MB to 750MB in 10 minutes. Should you restart?**  
   _Answer: Check if linear growth (leak) or single spike (GC). If linear, restart. If spike then stable, monitor._

3. **ToolForge subsystem goes offline. What's your next step?**  
   _Answer: Restart it: `corvin restart-subsystem tool_forge`. Monitor for recovery (should be <30s)._

4. **Cost estimate error drops from 8% to 25%. What does that mean?**  
   _Answer: Cost model drift detected. Schedule retraining. Consider disabling cost budgeting temporarily._

---

**Next Module:** [Incident Response Module](MODULE-3-INCIDENT-RESPONSE.md) (60 min)  
**Time Spent:** 45 minutes  
**Status:** Ready to respond to real incidents ✅
