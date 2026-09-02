# Deployment Guide: Rolling Out Skill Changes Safely

This guide explains how to deploy Skill changes to production using **staged rollout**, **canary monitoring**, and **instant rollback**.

---

## Staged Rollout Strategy

### Timeline: Day 1–7

```svg
<svg viewBox="0 0 950 500" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="950" height="500" fill="#F9FAFB"/>
  
  <!-- Title -->
  <text x="475" y="30" font-size="20" font-weight="bold" text-anchor="middle" fill="#1F2937">
    Staged Rollout: 10% → 50% → 100% Over 7 Days
  </text>
  
  <!-- Day 1: 10% -->
  <g id="day1">
    <rect x="50" y="80" width="120" height="80" rx="4" fill="#DBEAFE" stroke="#3B82F6" stroke-width="2"/>
    <text x="110" y="105" font-size="12" font-weight="bold" text-anchor="middle" fill="#1E40AF">Day 1</text>
    <text x="110" y="130" font-size="11" text-anchor="middle" fill="#1E40AF">10%</text>
    <text x="110" y="150" font-size="10" text-anchor="middle" fill="#1E40AF">Canary</text>
  </g>
  
  <!-- Arrow 1 -->
  <path d="M 170 120 L 210 120" stroke="#6B7280" stroke-width="2" fill="none" marker-end="url(#arrowhead)"/>
  <text x="190" y="110" font-size="9" fill="#6B7280">gate:OK</text>
  
  <!-- Day 2-3: 50% -->
  <g id="day23">
    <rect x="210" y="80" width="120" height="80" rx="4" fill="#FEF3C7" stroke="#F59E0B" stroke-width="2"/>
    <text x="270" y="105" font-size="12" font-weight="bold" text-anchor="middle" fill="#92400E">Days 2–3</text>
    <text x="270" y="130" font-size="11" text-anchor="middle" fill="#92400E">50%</text>
    <text x="270" y="150" font-size="10" text-anchor="middle" fill="#92400E">Ramp</text>
  </g>
  
  <!-- Arrow 2 -->
  <path d="M 330 120 L 370 120" stroke="#6B7280" stroke-width="2" fill="none" marker-end="url(#arrowhead)"/>
  <text x="350" y="110" font-size="9" fill="#6B7280">gate:OK</text>
  
  <!-- Day 4-7: 100% -->
  <g id="day47">
    <rect x="370" y="80" width="120" height="80" rx="4" fill="#DCFCE7" stroke="#10B981" stroke-width="2"/>
    <text x="430" y="105" font-size="12" font-weight="bold" text-anchor="middle" fill="#065F46">Days 4–7</text>
    <text x="430" y="130" font-size="11" text-anchor="middle" fill="#065F46">100%</text>
    <text x="430" y="150" font-size="10" text-anchor="middle" fill="#065F46">General</text>
  </g>
  
  <!-- Metrics during deployment -->
  <text x="50" y="210" font-size="12" font-weight="bold" fill="#1F2937">Monitored Metrics (24/7):</text>
  
  <!-- Metric 1: Latency -->
  <g id="metric1">
    <rect x="50" y="230" width="250" height="100" rx="4" fill="#F3F4F6" stroke="#D1D5DB" stroke-width="1"/>
    <text x="175" y="250" font-size="11" font-weight="bold" text-anchor="middle" fill="#1F2937">Latency (ms)</text>
    
    <!-- Mini chart -->
    <polyline points="60,310 90,300 120,295 150,290 180,285 210,280" 
              fill="none" stroke="#3B82F6" stroke-width="2"/>
    <text x="175" y="330" font-size="9" text-anchor="middle" fill="#374151">Baseline: 50ms</text>
    <text x="175" y="345" font-size="9" text-anchor="middle" fill="#10B981">✅ Good: +0-5ms</text>
  </g>
  
  <!-- Metric 2: Error Rate -->
  <g id="metric2">
    <rect x="320" y="230" width="250" height="100" rx="4" fill="#F3F4F6" stroke="#D1D5DB" stroke-width="1"/>
    <text x="445" y="250" font-size="11" font-weight="bold" text-anchor="middle" fill="#1F2937">Error Rate (%)</text>
    
    <!-- Mini chart -->
    <polyline points="330,310 360,305 390,302 420,300 450,298 480,296" 
              fill="none" stroke="#3B82F6" stroke-width="2"/>
    <text x="445" y="330" font-size="9" text-anchor="middle" fill="#374151">Baseline: 0.1%</text>
    <text x="445" y="345" font-size="9" text-anchor="middle" fill="#10B981">✅ Good: <0.2%</text>
  </g>
  
  <!-- Metric 3: Confidence -->
  <g id="metric3">
    <rect x="590" y="230" width="250" height="100" rx="4" fill="#F3F4F6" stroke="#D1D5DB" stroke-width="1"/>
    <text x="715" y="250" font-size="11" font-weight="bold" text-anchor="middle" fill="#1F2937">Confidence (%)</text>
    
    <!-- Mini chart -->
    <polyline points="600,310 630,295 660,280 690,265 720,250 750,238" 
              fill="none" stroke="#3B82F6" stroke-width="2"/>
    <text x="715" y="330" font-size="9" text-anchor="middle" fill="#374151">Target: 95%</text>
    <text x="715" y="345" font-size="9" text-anchor="middle" fill="#10B981">✅ On track: 90%</text>
  </g>
  
  <!-- Gates section -->
  <text x="50" y="380" font-size="12" font-weight="bold" fill="#1F2937">Automatic Gates (Block Rollout If Triggered):</text>
  <text x="50" y="400" font-size="10" fill="#6B7280">🚫 Latency > 100ms (2x baseline) → STOP</text>
  <text x="50" y="420" font-size="10" fill="#6B7280">🚫 Error rate > 1% → STOP</text>
  <text x="50" y="440" font-size="10" fill="#6B7280">🚫 Confidence < 75% → STOP</text>
  <text x="50" y="460" font-size="10" fill="#6B7280">🚫 Audit chain broken → STOP (fail-closed)</text>
  
  <!-- Arrow marker -->
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#6B7280"/>
    </marker>
  </defs>
</svg>
```

### Stage 1: Canary (Day 1, 10% Traffic)

**Goal:** Test with real traffic before wide rollout.

**Process:**
```bash
# Deploy new Skill version
corvin skill deploy os.delegation_router --version 2.1.0 --canary 10%

# Verify deployment
corvin skill status os.delegation_router
# Output: v2.1.0 deployed to 10% of traffic

# Monitor for 24 hours
corvin metrics dashboard --skill os.delegation_router --interval 1h
# Watch: latency, errors, confidence
```

**Success Criteria:**
- ✅ Latency: +0–5ms (vs baseline 50ms)
- ✅ Error rate: < 0.2% (vs baseline 0.1%)
- ✅ Confidence: > 85% (vs target 95%)
- ✅ Audit chain: Verified (no missing events)

**If any metric fails:**
```bash
# Instant rollback
corvin skill rollback os.delegation_router --to-version 2.0.1
# Immediately reverts to previous version
# All in-flight requests finish on old version
```

### Stage 2: Ramp (Days 2–3, 50% Traffic)

After 24h canary succeeds, increase traffic:

```bash
# Ramp to 50%
corvin skill deploy os.delegation_router --version 2.1.0 --ramp 50%

# Automated monitoring continues
corvin metrics dashboard --skill os.delegation_router --interval 30m
```

**Success Criteria:** Same as Stage 1.

### Stage 3: General Availability (Days 4–7, 100% Traffic)

Final rollout to all traffic:

```bash
# Deploy to 100%
corvin skill deploy os.delegation_router --version 2.1.0 --ramp 100%

# Final verification
corvin skill status os.deployment_router
# Output: v2.1.0 deployed to 100% (GA)
```

---

## Canary Monitoring: Real-Time Metrics

### Metrics Dashboard

```
┌─────────────────────────────────────────────────────┐
│ os.delegation_router (v2.1.0) — Canary: 10%        │
├─────────────────────────────────────────────────────┤
│ Latency:                                            │
│   Baseline (v2.0.1): 50.2ms                         │
│   Canary (v2.1.0):   51.8ms  (↑ +1.6ms) ✅ OK       │
│                                                     │
│ Error Rate:                                         │
│   Baseline: 0.11%                                   │
│   Canary:   0.14%  (↑ +0.03%) ✅ OK                 │
│                                                     │
│ Confidence:                                         │
│   Baseline: 91.3%                                   │
│   Canary:   89.7%  (↓ -1.6%) ⚠️ Watch              │
│                                                     │
│ Sample Size:                                        │
│   Baseline: 127,340 requests (24h)                  │
│   Canary:   12,892 requests (24h)                   │
│                                                     │
│ Status: 🟢 PASS — All metrics within tolerance      │
│ Next: Proceed to Stage 2 (50% traffic)              │
└─────────────────────────────────────────────────────┘
```

### Monitoring Setup

```yaml
# core/deployment/canary_config.yaml
canary_rules:
  os.delegation_router:
    latency_threshold_ms: 100  # 2x baseline
    error_rate_threshold: 0.01  # 1%
    confidence_threshold: 0.75  # Don't deploy if low
    audit_chain_required: true  # Must verify
    
    stages:
      - name: "canary"
        traffic_percent: 10
        duration_hours: 24
        auto_pass_metrics:
          - latency < 100ms
          - error_rate < 1%
          - confidence > 75%
          
      - name: "ramp"
        traffic_percent: 50
        duration_hours: 48
        
      - name: "ga"
        traffic_percent: 100
        duration_hours: 72  # Monitor for 3 more days post-GA
```

---

## Instant Rollback

If any metric exceeds tolerance:

```bash
# Automatic rollback (triggered by monitoring)
corvin skill rollback os.delegation_router --reason "Latency exceeded threshold"

# Or manual rollback
corvin skill rollback os.delegation_router --to-version 2.0.1

# Verify rollback
corvin skill status os.delegation_router
# Output: v2.0.1 (rolled back from v2.1.0)
```

**Rollback mechanics:**
1. New traffic routes to old version instantly (< 1s)
2. In-flight requests finish on version they started with (graceful)
3. Old version becomes active again
4. Audit trail records rollback: `skill_deployed_rollback` event

---

## Zero-Downtime Architecture

**How new Skill versions load without downtime:**

```
Request comes in
      ↓
┌─────────────────────────────────────────────┐
│ Router selects version (old or new)         │
│ - If canary: 10% → new version              │
│ - Otherwise: 90% → old version              │
└─────────────────────────────────────────────┘
      ↓
   Route to correct instance
      ↓
Old version still running
New version loading in parallel
      ↓
When new version ready:
  Re-configure router (atomic)
  New requests use new version
      ↓
Old version continues
(in-flight requests finish)
      ↓
After 1h: old version shut down
```

**Result:** Zero downtime, graceful transition, instant rollback.

---

## A/B Equivalence Testing

Before rolling out a new Skill version, prove the new behavior matches the old:

### Equivalence Test

```python
# tests/deployment/test_router_equivalence.py

def test_v2_0_1_vs_v2_1_0_equivalence():
    """Prove v2.1.0 produces same results as v2.0.1"""
    
    test_cases = [
        {"input": {"complexity": 0.3}, "expected_route": "haiku"},
        {"input": {"complexity": 0.7}, "expected_route": "opus"},
        {"input": {"complexity": 0.5}, "expected_route": "sonnet"},
        # ... 100+ test cases
    ]
    
    old_skill = get_skill_version("os.delegation_router", "2.0.1")
    new_skill = get_skill_version("os.delegation_router", "2.1.0")
    
    for test_case in test_cases:
        old_result = old_skill.execute(test_case["input"])
        new_result = new_skill.execute(test_case["input"])
        
        # Both should route to same model
        assert old_result["route_to"] == new_result["route_to"], \
            f"Equivalence broken: {test_case}"
        
        # Both should have similar confidence
        assert abs(old_result["confidence"] - new_result["confidence"]) < 0.05, \
            f"Confidence mismatch: {test_case}"
```

**Run before deployment:**
```bash
pytest tests/deployment/test_router_equivalence.py -v

# Output: 156 test cases PASSED
# Ready to deploy: v2.0.1 → v2.1.0
```

---

## Post-Deploy Verification

### Verify Deployment Succeeded

```bash
# Check all metrics post-GA
corvin metrics query \
  --skill os.delegation_router \
  --since 2026-09-02T00:00:00Z \
  --until 2026-09-03T00:00:00Z

# Output: Full metrics CSV
```

### Verify Audit Trail

```bash
# Confirm all Skill decisions were logged
corvin audit filter \
  --skill os.delegation_router \
  --event-type skill_executed \
  --since 2026-09-02

# Count events
corvin audit count --skill os.delegation_router --date 2026-09-02
# Output: 1,234,567 events (expected 1.2M for typical day)
```

### Verify Feedback Loop

```bash
# Check if feedback is being collected
corvin audit filter \
  --event-type skill_feedback \
  --skill os.delegation_router \
  --since 2026-09-02

# Should see feedback events within 1h of deploy
```

---

## Compliance Gates

### Audit Chain Verification (Hard Stop)

Before any stage of rollout, verify audit chain is intact:

```bash
corvin audit verify-chain --tenant=_default

# If broken: STOP immediately, don't deploy
# Exit code 1, rollback to previous version
```

### Consent Verification

Ensure users gave consent before Skill makes decisions:

```bash
# Sample consent events
corvin audit filter \
  --event-type consent_granted \
  --skill os.delegation_router \
  --sample-size 1000

# Verify 100% of sampled requests had consent
```

### GDPR Compliance Report

Generate compliance report for deployed Skill:

```bash
corvin audit export \
  --skill os.delegation_router \
  --format=pdf \
  --include-gates consent_granted,house_rule_denied,audit_chain_verified
  
# Output: compliance_report_os_delegation_router_v2.1.0.pdf
```

---

## Deployment Checklist

Before deploying a new Skill version:

- [ ] **Code review** — 2+ reviewers approve
- [ ] **Equivalence tests** — New ≈ Old for known inputs
- [ ] **Unit tests** — 100% pass
- [ ] **E2E tests** — Real execution path verified
- [ ] **Load test** — Latency under 100ms at 10K req/s
- [ ] **Audit tests** — Events logged correctly
- [ ] **Consent verified** — Users gave consent
- [ ] **Feedback schema** — Updated if needed
- [ ] **Documentation** — Updated skills-system.md
- [ ] **ADR written** — If structural change
- [ ] **Rollback plan** — Know how to roll back
- [ ] **Monitoring set up** — Alerts configured
- [ ] **On-call scheduled** — Someone watching 24/7

---

## Troubleshooting

### Scenario: Canary Latency Spiked

**Symptom:** Day 1 canary shows latency 150ms (vs 50ms baseline)

**Investigation:**
```bash
# 1. Check if new code is slower
corvin audit filter --skill os.delegation_router --date 2026-09-02 --limit 100 \
  | jq '.[] | {latency_ms: .latency_ms, version: .skill_version}' \
  | sort | uniq -c

# 2. Check for dependency issues
corvin audit trace os.delegation_router --task <recent_task_id>
# Does the router call slow dependencies?

# 3. Rollback immediately
corvin skill rollback os.delegation_router --to-version 2.0.1
```

### Scenario: Confidence Stuck Low

**Symptom:** Confidence 78% (target 95%) after 3 days canary

**Investigation:**
```bash
# 1. Check feedback quality
corvin audit filter \
  --event-type skill_feedback \
  --skill os.delegation_router \
  --limit 100 | jq '.[] | {correct: .correct}' | sort | uniq -c

# 2. If feedback is mixed (50/50), might be inherent skill limit
# 3. If mostly wrong, bug in new version
# 4. Decide: extend canary, debug, or rollback
```

---

## See Also

- **[Skills System](skills-system.md)** — How Skills are versioned
- **[Learning Loop](learning-loop.md)** — Confidence tracking
- **[Audit Trail](audit-trail.md)** — Verification at every stage
- **[ADR-0533](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Manifest schema & versioning

---

**Safe deployment is built into Skills. Staged rollout, real-time monitoring, instant rollback, and audit verification make production changes low-risk.**
