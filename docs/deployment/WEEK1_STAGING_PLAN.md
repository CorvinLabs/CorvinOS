# Week 1 Staging Plan — TaskEngine (ADR-0267) Phase 6 Deployment

**Timeline:** 2026-08-06 → 2026-08-12 (Week 1)  
**Phase:** Internal testing with operators  
**Success Gate:** 85% routing accuracy, < 700ms p95 latency  

---

## Overview

**Objective:** Validate TaskEngine (6-phase routing) in staging with 50 real operator tasks before Week 2 canary (5% prod).

**Test Scope:**
- 50 real tasks from operator queue (mix of bug_fix, feature, refactor, incident, docs)
- Prometheus metrics collection + AlertManager validation
- Contract violation detection
- Model selection accuracy (Haiku vs Opus)
- Delegation router correctness (native/ACS/TDE)
- End-to-end latency measurement

**Success Criteria:**
- ✅ 85%+ routing decisions match operator expectation
- ✅ P95 latency < 700ms (target: 200–400ms)
- ✅ Zero contract violations
- ✅ < 1% error rate
- ✅ Alerting triggers correctly on simulated faults

---

## Phase 1: Staging Infrastructure (Day 1–2)

### 1.1 Deploy TaskEngine to Staging

```bash
# Tag current commit for staging
git tag -a staging/adr0267-v1.0.0 b2621d8 -m "ADR-0267 Phase 6: monitoring + edge-case tests"

# Deploy to staging environment
# (Assumes staging cluster accessible)
export CORVIN_ENV=staging
export TASK_ANALYSIS_LOG_LEVEL=debug
export PROMETHEUS_ENABLED=true
export ALERTMANAGER_ENABLED=true

# Start TaskEngine server
cd /home/shumway/projects/CorvinOS
uv run python -m operator.task_analysis.server --port 8765
```

### 1.2 Configure Prometheus

**File:** `/home/shumway/projects/CorvinOS/config/prometheus-staging.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'taskengine-staging'
    static_configs:
      - targets: ['localhost:8765']
    metrics_path: '/metrics'
    scrape_interval: 5s  # Tight interval for staging
```

Start Prometheus:
```bash
prometheus --config.file=config/prometheus-staging.yml
```

### 1.3 Configure AlertManager

**File:** `/home/shumway/projects/CorvinOS/config/alertmanager-staging.yml`

```yaml
global:
  resolve_timeout: 5m

route:
  receiver: 'staging-alerts'
  group_by: ['alertname', 'job']
  group_wait: 10s
  group_interval: 30s
  repeat_interval: 1h

receivers:
  - name: 'staging-alerts'
    webhook_configs:
      - url: 'http://localhost:9000/alerts'  # Staging webhook receiver
```

Start AlertManager:
```bash
alertmanager --config.file=config/alertmanager-staging.yml
```

### 1.4 Verify Instrumentation

```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets

# Check AlertManager rules
curl http://localhost:9093/api/alerts

# Test TaskEngine health
curl http://localhost:8765/health
```

---

## Phase 2: Test Data Preparation (Day 2–3)

### 2.1 Collect 50 Real Operator Tasks

**Source:** Operator task backlog / memory files

**Format:** JSON array in `/staging/test_data.json`

```json
[
  {
    "id": "task-001",
    "raw_task": "Fix crash in voice module when processing long audio files",
    "expected_type": "bug_fix",
    "expected_severity": "high",
    "expected_target": "native",
    "expected_model": "opus",
    "notes": "Production issue affecting 100+ users"
  },
  {
    "id": "task-002",
    "raw_task": "Process 10GB customer data from warehouse, aggregate into summary stats",
    "expected_type": "analysis",
    "expected_severity": "medium",
    "expected_target": "acs",
    "expected_model": "haiku",
    "notes": "Big-data carve-out, Rule 1 (vocabulary)"
  }
  // ... 48 more
]
```

### 2.2 Load Test Data into Staging

```python
import json
import requests

with open('/staging/test_data.json') as f:
    tasks = json.load(f)

for task in tasks:
    response = requests.post(
        'http://localhost:8765/analyze',
        json={'raw_task': task['raw_task']}
    )
    task['result'] = response.json()
    task['status'] = 'analyzed' if response.status_code == 200 else 'failed'

# Save results
with open('/staging/test_results.json', 'w') as f:
    json.dump(tasks, f, indent=2)
```

---

## Phase 3: Validation & Metrics Collection (Day 3–5)

### 3.1 Accuracy Validation

**Script:** `/staging/validate_accuracy.py`

```python
import json
from collections import defaultdict

def validate_routing(tasks):
    """Measure routing accuracy vs. expected."""
    metrics = {
        'total': len(tasks),
        'correct': 0,
        'by_type': defaultdict(lambda: {'total': 0, 'correct': 0}),
        'by_severity': defaultdict(lambda: {'total': 0, 'correct': 0}),
    }
    
    for task in tasks:
        result = task['result']
        expected_target = task['expected_target']
        actual_target = result['decision_target']
        
        metrics['by_type'][task['expected_type']]['total'] += 1
        metrics['by_severity'][task['expected_severity']]['total'] += 1
        
        if actual_target == expected_target:
            metrics['correct'] += 1
            metrics['by_type'][task['expected_type']]['correct'] += 1
            metrics['by_severity'][task['expected_severity']]['correct'] += 1
    
    # Calculate accuracy
    accuracy = metrics['correct'] / metrics['total']
    
    # Print breakdown
    print(f"Overall Accuracy: {accuracy:.1%}")
    print(f"\nBy Type:")
    for typ, data in metrics['by_type'].items():
        type_acc = data['correct'] / data['total'] if data['total'] > 0 else 0.0
        print(f"  {typ}: {type_acc:.1%} ({data['correct']}/{data['total']})")
    
    return accuracy >= 0.85

with open('/staging/test_results.json') as f:
    tasks = json.load(f)

if validate_routing(tasks):
    print("\n✅ PASS: Accuracy >= 85%")
else:
    print("\n❌ FAIL: Accuracy < 85% — investigate routing gaps")
```

Run:
```bash
python /staging/validate_accuracy.py
```

### 3.2 Latency Measurement

**Script:** `/staging/measure_latency.py`

```python
import json
import statistics

with open('/staging/test_results.json') as f:
    tasks = json.load(f)

latencies_ms = [task.get('latency_ms', 0) for task in tasks if task.get('latency_ms')]

if latencies_ms:
    print(f"Latency Stats (ms):")
    print(f"  Min:    {min(latencies_ms):.0f}")
    print(f"  Median: {statistics.median(latencies_ms):.0f}")
    print(f"  Mean:   {statistics.mean(latencies_ms):.0f}")
    print(f"  P95:    {sorted(latencies_ms)[int(0.95 * len(latencies_ms))]:.0f}")
    print(f"  Max:    {max(latencies_ms):.0f}")
    
    p95 = sorted(latencies_ms)[int(0.95 * len(latencies_ms))]
    if p95 < 700:
        print(f"\n✅ PASS: P95 < 700ms")
    else:
        print(f"\n⚠️  WARN: P95 = {p95:.0f}ms (target < 700ms) — investigate bottleneck")
```

### 3.3 Prometheus Metrics Export

**Query to Dashboard:**

```promql
# Phase latencies
histogram_quantile(0.95, rate(task_analysis_phase_duration_seconds[5m])) by (phase)

# Confidence distribution
histogram_quantile(0.5, rate(task_analysis_confidence_score[5m]))

# Routing decision breakdown
sum(rate(task_analysis_routing_decision_total[5m])) by (target, reason)

# Contract violations
increase(task_analysis_contract_violations_total[1h]) by (phase)

# Model selection ratio
sum(rate(task_analysis_model_selection_total[5m])) by (model) / ignoring(model) group_left sum(rate(task_analysis_model_selection_total[5m]))
```

---

## Phase 4: Alerting Validation (Day 5–6)

### 4.1 Simulate Faults & Verify Alerts

**Script:** `/staging/simulate_faults.py`

```python
import requests
import time

def simulate_high_error_rate():
    """Inject tasks that will cause errors."""
    bad_tasks = [
        "",  # Empty
        "x",  # Too short
        None,  # Null
    ]
    for task in bad_tasks:
        try:
            requests.post('http://localhost:8765/analyze', json={'raw_task': task})
        except:
            pass
    time.sleep(30)  # Wait for metric collection

def simulate_high_latency():
    """Send complex tasks that exceed latency budget."""
    # Tasks designed to hit TDE (most expensive)
    heavy_tasks = [
        "completely rewrite entire system architecture" * 50,
        "refactor all layers with full redesign" * 50,
    ]
    for task in heavy_tasks:
        requests.post('http://localhost:8765/analyze', json={'raw_task': task})
    time.sleep(30)

def simulate_low_confidence():
    """Send ambiguous tasks that produce low confidence."""
    ambiguous_tasks = [
        "do something",
        "make it better",
        "fix issues",
    ]
    for task in ambiguous_tasks:
        try:
            requests.post('http://localhost:8765/analyze', json={'raw_task': task})
        except:
            pass
    time.sleep(30)

# Run simulations
print("Simulating high error rate...")
simulate_high_error_rate()

print("Checking AlertManager for CRITICAL alerts...")
response = requests.get('http://localhost:9093/api/v1/alerts?state=active')
alerts = response.json()['data']
critical_alerts = [a for a in alerts if a['labels']['severity'] == 'CRITICAL']
print(f"Found {len(critical_alerts)} CRITICAL alerts ✅")

print("\nSimulating high latency...")
simulate_high_latency()
time.sleep(30)

print("Checking for latency WARNING alerts...")
response = requests.get('http://localhost:9093/api/v1/alerts?state=active')
alerts = response.json()['data']
latency_alerts = [a for a in alerts if 'latency' in a['labels'].get('alertname', '').lower()]
print(f"Found {len(latency_alerts)} latency alerts ✅")
```

### 4.2 Verify Alert Notifications

**Expected alerts during Day 5–6:**

- ✅ CRITICAL: contract_violation triggered
- ✅ WARNING: low_confidence triggered
- ✅ WARNING: high_latency_p95 triggered
- ✅ INFO: routing_distribution updated

---

## Phase 5: Feedback & Sign-Off (Day 6–7)

### 5.1 Operator Review

Gather feedback from operators:
- ✅ Routing decisions feel correct?
- ✅ Latency acceptable?
- ✅ Any unexpected misdirections?
- ✅ Alerts useful / noisy?

### 5.2 Collect Final Metrics

```bash
# Export final Prometheus data
curl 'http://localhost:9090/api/v1/query?query=task_analysis_routing_decision_total' > /staging/final_metrics.json

# Export AlertManager history
curl 'http://localhost:9093/api/v1/alerts/groups' > /staging/alert_history.json
```

### 5.3 Sign-Off Checklist

- [ ] 50 tasks analyzed
- [ ] Accuracy ≥ 85%
- [ ] P95 latency < 700ms
- [ ] Zero contract violations
- [ ] Alerting validated (5/5 alert types triggered)
- [ ] Operator feedback collected
- [ ] Incident response plan ready

**Sign-off:** Once all items ✅, proceed to **Week 2 Canary (5% prod)**.

---

## Rollback Plan

If staging reveals critical issues:

1. **< 1 hour to rollback:**
   - Revert TaskEngine to previous version
   - Clear metrics + alert cache
   - Resume with previous routing logic

2. **Root cause analysis:**
   - Review failed task results
   - Check contract violations
   - Profile latency bottleneck

3. **Fix + re-test:**
   - Apply targeted fix
   - Re-run Phase 3 validation
   - Re-submit to Week 1 if time permits

---

## Operator Notes

_Append-only, timestamped._

**2026-08-06:** Week 1 Staging Plan created. Deployment infrastructure ready. Awaiting operator sign-off.

