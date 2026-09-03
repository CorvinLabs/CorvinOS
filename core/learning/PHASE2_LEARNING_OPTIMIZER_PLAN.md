# Phase 2a: Learning Optimizer — Implementation Plan

**ADR:** ADR-0314.2 (Learning Infrastructure — Optimizer Loop)  
**Duration:** 120 hours (3-4 weeks)  
**Start:** 2026-09-10 (after Phase 1 48h stabilization)  
**Deliverable:** Closed-loop learning feedback pipeline with config tuning

---

## Problem Statement

Phase 1 (ADR-0314) implemented the **event schema** (immutable learning events) and **persistence** (EventStore). But events sit unused — no feedback loop reads them, no tuning happens. Phase 1 Skills make decisions but never improve from user feedback.

**Phase 2a solves:** Build the **optimizer loop** that closes the feedback cycle.

```
Skill Execution (Phase 1)
    ↓ emit LearningEvent (confidence, output, latency)
    ↓
Feedback Ingestion (NEW — 2a)
    ↓ user rates decision: "good", "bad", "other"
    ↓ event stored with feedback label
    ↓
Config Tuning (NEW — 2a)
    ↓ optimizer reads events (last 1000)
    ↓ calculates confidence drift
    ↓ tunes Skill config (routing thresholds, context weights)
    ↓ A/B test: 50% old config, 50% new config
    ↓
Next Execution (improved)
    ↓ Skill uses tuned config
    ↓ latency / accuracy improves
    ↓ cycle repeats
```

---

## Scope (120h)

### 2a.1: Feedback Ingestion (20h)
- **Input:** User feedback API (`/v1/learning/feedback`)
- **Schema:** `{skill_id, task_id, feedback: "good|bad|other", reason: string}`
- **Validation:** 
  - Feedback must be within 1h of execution (time-bound)
  - Task must exist in audit trail (audit verification)
  - Feedback source must be authenticated (user + tenant validation)
- **Storage:** Append to learning event (immutable link)
- **Tests:** Validation gates, time-bound rejection, audit trail verification

### 2a.2: Confidence Scoring + Drift Detection (25h)
- **Input:** 1000 most recent events for a Skill
- **Calculate:**
  - Baseline confidence (Phase 1: hardcoded 0.8 for success)
  - Feedback confidence (% "good" / total feedback)
  - Drift: |baseline - feedback| > threshold (e.g., 0.2) → model diverged
- **Trigger:** Optimizer runs when drift detected OR every 24h (whichever first)
- **Output:** Confidence report + optimizer signal
- **Tests:** Drift detection, edge cases (no feedback, all "bad", sparse feedback)

### 2a.3: Config Tuning Loop (40h)
- **Algorithm:** Gradient descent on Skill config parameters
  - Routing threshold: confidence_threshold ∈ [0.5, 0.95]
  - Context weight: attention_weight ∈ [0.0, 1.0]
  - Latency target: p99_latency_ms ∈ [50, 500]
- **Optimization:** Maximize confidence while minimizing latency
  - Loss = (1 - confidence_score) + 0.1 * (latency_ms / 100)
  - Update: new_param = old_param + learning_rate * gradient
- **Safety:** Never change param by >±10% per iteration (fail-closed)
- **Validation:** Run tuned config on holdout set (10% of feedback); verify improvement before applying
- **Tests:** Gradient descent convergence, safety bounds, holdout validation

### 2a.4: A/B Testing + Canary Deployment (20h)
- **Setup:**
  - Deploy tuned config to 10% of traffic (canary)
  - Run for 24h; compare metrics vs. 90% baseline
  - Metrics: success_rate, p99_latency, confidence_score, cost
- **Decision:**
  - If canary better: roll forward to 50% traffic
  - If canary same: keep baseline (no regression)
  - If canary worse: rollback tuned config
- **Tests:** A/B framework, metric comparison, rollback procedure

### 2a.5: Monitoring + Observability (15h)
- **Metrics exported:**
  - `learning_feedback_count` (total feedback received)
  - `learning_drift_detected_count` (times drift triggered optimizer)
  - `learning_config_tuned_count` (times config updated)
  - `learning_canary_success_rate` (A/B test winner rate)
  - `learning_optimizer_latency_ms` (optimizer wall-clock time)
- **Dashboard:** Show confidence trend, config deltas, canary results
- **Alerts:**
  - If drift_detected > 5/day → investigate Skill quality
  - If optimizer fails → page SRE

---

## Architecture (High-Level)

```
EventStore (Phase 1)
    ↓
FeedbackIngestion (2a.1)
    ↓
ConfidenceDrift (2a.2) ←→ ConfigTuner (2a.3)
                            ↓
                        CanaryDeployer (2a.4)
                            ↓
                        Skill Config (updated live)
                            ↓
                        Next Execution (improved)
```

**Key Design Decisions:**
1. **Immutable events:** Feedback is appended to learning event; never modify original
2. **Feedback time-bound:** Only feedback within 1h of execution counts (prevents stale labels)
3. **Safety bounds:** Config changes clamped to ±10% per iteration (fail-closed)
4. **Holdout validation:** Tuned config tested on 10% feedback before applying to live traffic
5. **Gradient descent:** Simple, interpretable, proven algorithm (no ML framework needed)

---

## Implementation Files (New)

```
core/learning/
├── optimizer.py                      # ConfidenceDrift + ConfigTuner
├── feedback_ingestion.py             # FeedbackIngestion + validation
├── canary_deployer.py                # A/B test + rollout
├── learning_metrics.py               # Prometheus metrics
└── tests/
    ├── test_feedback_validation.py
    ├── test_confidence_drift.py
    ├── test_config_tuning.py
    ├── test_canary_deployment.py
    └── test_learning_e2e.py
```

**Modifications to existing files:**
- `skill_registry_phase1.py::SkillsRegistry` — Add `get_learning_events()` method (read last N events for a Skill)
- `skills/os_skills_integration.py` — Wire optimizer into L5/L10 (config-aware routing)

---

## Success Criteria (End of Week 4)

- [x] Feedback API live (`POST /v1/learning/feedback`)
- [x] Feedback ingestion validated + audit-logged
- [x] Confidence drift detection working (test: manual drift → detection)
- [x] Config tuning loop implemented (test: gradient descent convergence)
- [x] A/B testing framework working (test: canary 10%, metric comparison)
- [x] Monitoring + dashboards deployed (test: metrics exported to Prometheus)
- [x] E2E test: Full loop (skill exec → feedback → tuning → canary → apply)
- [x] Docs: How to interpret confidence drift + config changes

---

## Testing Strategy (LDD k=1-5)

| Phase | What | How |
|-------|------|-----|
| **k=1** | Dialectical reasoning | Surface tradeoffs (immutability vs. feedback latency, safety bounds vs. learning speed) |
| **k=2** | E2E design | Prove reachability (feedback API callable, optimizer reads EventStore, canary lives, config applied) |
| **k=3** | Red→Green | Implement + unit tests (each component standalone) |
| **k=4** | Refinement | Integration tests (full feedback loop) |
| **k=5** | Docs sync | Update compliance audit, memory, Phase 2 roadmap |

---

## Phase 2b Dependency (Manifests)

Learning Optimizer (2a) unlocks Manifest Validation (2b):
- Manifests define Skill parameters (e.g., `routing_threshold`, `attention_weight`)
- Optimizer tunes these parameters
- Without manifests, tuning is hardcoded per-Skill

**Start 2b:** After 2a is stable (~week 3).

---

## Launch (Week 5)

After 2a + 2b stable:
- Announce "Learning Loop Live" to Skill authors
- Begin Phase 2c (OS-Skills with learning-aware design)
- Prepare canary rollout (2e) for production deployment

---

**Ready to start? → Begin with 2a.1 (Feedback Ingestion) this week.**
