# TreeOfThoughts Phase 8+ Roadmap

**Status:** Phase 8 ✅ COMPLETE | Phase 9 ✅ COMPLETE (implementation)  
**Version:** v0.2  
**Audience:** Product, Engineering

---

## Overview

TreeOfThoughts Phases 1-7 deliver **reactive learning**: patterns record execution, operators grade them, confidence scores update.

Phase 8+ moves to **proactive learning**: detecting anomalies, discovering new patterns, predicting failures, and composing high-confidence strategies.

---

## Phase 8: Anomaly Detection & Auto-Recovery

**Goal:** System notices when confidence drops unexpectedly and suggests fixes.

### 8a: Confidence Monitoring (Week 1)

Detect sudden drops in pattern confidence:

```
Baseline: pattern_openai_tts confidence = 0.88 (stable for 7 days)
Anomaly: confidence drops to 0.62 in 4 hours
Alert: "OpenAI TTS reliability degraded — 5 recent failures"
Suggestion: "Try Edge TTS (0.76) or implement retry-backoff"
```

**Implementation:**
- Rolling 7-day baseline per pattern
- Alert threshold: >20% drop in 4 hours
- Suggest top 3 alternative patterns by confidence

**Success metric:** Catch TTS provider failures within 1 hour of first symptoms

---

## Phase 9: Pattern Discovery

**Goal:** System auto-learns new patterns from production data without operator input.

### 9a: Failure Clustering (Week 2-3)

Group related failures to identify recurring patterns:

```
Failures:
- auth_failure (50x) → anti-pattern: retry_backoff
- rate_limit_429 (150x) → pattern: exponential_backoff_with_jitter
- network_timeout (80x) → pattern: connection_pool_reuse

Discovered: "When receiving 429, use exp-backoff instead of instant retry"
Confidence: 0.65 (from 280 failure samples)
```

**Implementation:**
- Cluster error types by `error_type` + `context`
- Infer anti_when contexts (when to avoid pattern)
- Auto-register discovery with 0.5 baseline confidence
- Require ≥50 samples before proposing

**Success metric:** Discover 3-5 new patterns per week in production

### 9b: Implementation Complete ✅

**Delivered:**
- `FailureClusterer` class in `core/learning/pattern_discovery.py`
  - Deterministic context signature extraction (GDPR-compliant, no PII)
  - Frequency-based clustering by error_type + context patterns
  - Automatic when/anti_when condition inference
  - 50-sample safety gate (no pattern proposed below threshold)
  - Append-only audit trail logging

- `LearningIntegration` Phase 9 methods:
  - `record_failure(subject_id, error_type, context)` — buffer failures for clustering
  - `discover_patterns()` — trigger clustering and auto-registration
  - `get_failure_clusters()` — retrieve all clusters
  - `get_discovered_patterns()` — retrieve successfully discovered patterns

- TreeNode auto-registration:
  - Pattern ID: `pattern_auto_{error_type}_{cluster_id}`
  - Baseline confidence: 0.5 (conservative)
  - Automatic when/anti_when contexts inferred from cluster patterns
  - Source metadata: sample count, error type, context patterns

- Test coverage: 21 E2E tests (all passing)
  - Core clustering logic
  - Context signature (PII-safe)
  - Minimum sample enforcement (50+ gate)
  - Pattern inference
  - Integration with LearningIntegration
  - Audit trail completeness
  - GDPR compliance (no PII in logs)

**Files:**
- `core/learning/pattern_discovery.py` — FailureClusterer implementation (408 lines)
- `core/learning/integration.py` — LearningIntegration Phase 9 methods (update)
- `tests/test_learning_phase9_discovery.py` — 21 E2E tests

**Compliance:**
- GDPR Art. 5 (data minimization): Context signature ignores PII/timestamps
- GDPR Art. 30 (audit trail): Append-only discoveries.jsonl log
- GDPR Art. 32 (integrity): Hash-chained to audit.jsonl via LearningEventStore

---

## Phase 10: Predictive Failure Detection

**Goal:** Warn operators before failures occur.

### 10a: Leading Indicators (Week 4)

Monitor metrics that precede failures:

```
Pattern: OpenAI TTS Latency
History: Normal latency 100-150ms
Leading indicator: Latency spikes to 500ms
→ 40% probability of failure in next 10 calls

Alert: "OpenAI TTS latency degrading — prepare fallback"
```

**Implementation:**
- Track latency histogram per pattern
- Compute z-score for each new measurement
- Trigger alert when z-score > 2.5 (2 std deviations)
- Include fallback pattern in alert

**Success metric:** Predict failures with 70%+ precision, 60%+ recall

---

## Phase 11: Pattern Composition & Strategy Learning

**Goal:** Combine high-confidence patterns into larger workflows.

### 11a: Strategy Synthesis (Week 5)

Learn when to combine patterns:

```
High-confidence patterns:
- retry_exponential_backoff (0.88)
- tts_fallback_to_edge (0.82)
- connection_pool_reuse (0.85)

Strategy: "Voice Synthesis Resilience" (0.82)
├── Try OpenAI TTS with exponential backoff
├── On rate-limit: switch to Edge TTS
└── Reuse connection pool to avoid timeout

Usage: Apply this strategy to all voice_synthesis patterns
```

**Implementation:**
- Identify patterns used in same execution context
- Test composition on historical data
- Measure combined success rate
- Auto-promote to executable strategies

**Success metric:** Auto-discover 2-3 working strategies per month

---

## Phase 12: Causal Analysis & Root Cause Learning

**Goal:** Operators understand WHY confidence changed.

### 12a: Change Attribution (Week 6+)

When confidence drops, explain why:

```
Pattern: Connection Pool Reuse
Confidence change: 0.85 → 0.62 (-0.23)

Likely causes (ranked):
1. Deployment event 2 hours ago (+0.70 probability)
   - New TLS cert configuration
   - 8 connection resets observed post-deploy
   
2. Traffic spike (+0.15 probability)
   - Doubled request rate in last hour
   - Pool exhaustion detected

3. False positive (-0.05 probability)
   - Noise, single sample skew
```

**Implementation:**
- Timeline analysis: correlate confidence changes with deployments
- Metric correlation: latency/error-rate changes near confidence drops
- Operator feedback loop: "Was this deployment?"

**Success metric:** Correctly identify root cause 80%+ of time

---

## Measurement & Iteration

### Key Metrics (Phases 8-12)

| Metric | Target | Phase |
|--------|--------|-------|
| Anomaly detection latency | <1 hour | 8 |
| Discovery precision | >80% | 9 |
| Predictive recall | >60% | 10 |
| Strategy success rate | >75% | 11 |
| Root cause accuracy | >80% | 12 |

### Operator Feedback Loop

After each phase:
1. Run on 10% of instances for 1 week
2. Gather operator feedback (Was alert useful? Was discovery accurate?)
3. Calibrate thresholds
4. Roll to 100% or iterate

---

## Architecture Constraints

### GDPR Compliance

- All learning events audit-logged (CorvinOS.audit.jsonl)
- No PII in anomaly/discovery data (pattern IDs only)
- Causal analysis limited to operator-initiated (no silent correlation)

### Performance

- Anomaly detection: <100ms per pattern
- Discovery: runs async, no blocking on chat turns
- Predictive model: pre-computed, O(1) lookup

### Rollback

Each phase is independently disableable:
```yaml
spec.features:
  learning_anomaly_detection: true
  learning_pattern_discovery: false  # Disable if too noisy
  learning_predictive_alerts: true
  learning_strategy_synthesis: false  # Pre-Phase 11
```

---

## Timeline

| Phase | Name | Duration | Owner |
|-------|------|----------|-------|
| **7** | Live Wiring (current) | 1 week | Deployment |
| **8** | Anomaly Detection | 1 week | ML/Ops |
| **9** | Pattern Discovery | 2 weeks | ML |
| **10** | Predictive Failures | 1 week | ML/Analytics |
| **11** | Strategy Composition | 1 week | ML/System Design |
| **12** | Causal Analysis | 2 weeks | Analytics/Ops |

**Total:** 8 weeks after Phase 7c deployment

---

## Success Criteria (End of Phase 12)

- ✅ System proactively learns 5+ new patterns/week
- ✅ Anomalies detected within 1 hour
- ✅ Operators run ≥3 experiments per week using predicted alerts
- ✅ Strategy composition reduces manual pattern selection by 40%
- ✅ Causal analysis answers "why" for 80% of confidence changes
- ✅ <1% false alert rate (avoid operator fatigue)

---

## Open Questions

1. **Should Phase 8-12 be behind feature flags?** (Defer learning until opt-in or ship dark-by-default)
2. **How to validate discovery without live A/B testing?** (Simulation? Historical replay?)
3. **What's the right alert threshold?** (Too low = fatigue; too high = misses)
4. **Should operators be able to reject auto-discovered patterns?** (Create negative feedback loop)

---

**Next:** Present to product team for prioritization. Run Phase 8 pilot after Phase 7c deployment stabilizes (Week 2 of 7c canary).

**Status:** READY FOR ROADMAP REVIEW  
**Last Updated:** 2026-08-17
