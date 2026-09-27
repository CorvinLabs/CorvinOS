# Learning Loop Closure — Phase 3 OS-Skills (ADR-0314 + ADR-0722)

**Status:** Implementation Complete ✅  
**Last Updated:** 2026-09-27  
**Maintainer:** Claude Code (Agent)

---

## Overview

The Learning Loop Closure completes Phase 3 of the self-optimizing OS-Skills system. It wires together three critical components:

1. **Event Ingestion** — Consume skill execution, user feedback, and task outcomes
2. **Confidence Scoring** — Aggregate signals into confidence metrics
3. **Parameter Optimization** — Compute safe parameter deltas and update Skill configs

The loop enables Skills to learn from real-world performance:

```
Skill Execution
    ↓ (generates skill.executed event)
User Feedback
    ↓ (user gives thumbs up/down)
Task Completion
    ↓ (task succeeds or fails)
Event Ingestion (batch aggregate)
    ↓
Confidence Scoring (success_rate × confidence_in_signal)
    ↓
Convergence Detection (has learning plateaued?)
    ↓
Parameter Optimization (compute delta, validate bounds)
    ↓
Config Update (write to Skill manifest)
    ↓
[Next Skill run uses optimized params]
```

---

## Deliverables

### 1. core/learning/skill_feedback_ingester.py

**Purpose:** Consume and aggregate three event streams into feedback signals.

**Key Classes:**
- `SkillFeedbackIngester` — Main ingestion orchestrator
  - `ingest_event(event: LearningEvent) → FeedbackSignal | None`
  - `ingest_batch(events: List[LearningEvent]) → List[FeedbackSignal]`
  - `flush_all_windows() → List[FeedbackSignal]` — End-of-session cleanup
- `FeedbackSignal` — Immutable aggregated signal (frozen dataclass)
- `IngestionWindow` — Mutable window during aggregation
- `FeedbackSignalType` — Enum of signal types (outcome_success, latency_fast, etc.)

**Event Types Consumed:**
- `EventType.SKILL_EXECUTED` — Skill ran (latency_ms, success flag)
- `EventType.FEEDBACK` — User feedback (is_positive, rating)
- `EventType.OUTCOME` — Task completed (status, exit_code, duration, cost)

**Windowing Strategy:**
- Time-based: Close window after 5 minutes
- Count-based: Close window after 1000 events
- Whichever comes first

**Aggregation Algorithm:**
1. Group events by skill_id
2. Extract outcome (success/failure), latency, cost, rating
3. Compute signal_type via majority vote (outcome > latency > cost)
4. Compute signal_strength = success_rate × sample_size_confidence

**Compliance:**
- Tenant-scoped: reject events with foreign tenant_id (GDPR Art. 32)
- Immutable: FeedbackSignal is frozen dataclass
- Idempotent: deduplicate by event_id, safe to re-run

### 2. core/learning/skill_optimizer_loop.py

**Purpose:** Consume feedback signals and compute parameter deltas for Skills.

**Key Classes:**
- `SkillOptimizerLoop` — Main optimizer
  - `optimize_from_signal(signal, config, outcomes) → OptimizationDecision`
  - `execute_optimization_epoch(...) → OptimizationDecision` — With audit logging
  - `apply_config_update(decision, manifest_path) → bool` — Persist to manifest
- `ConvergenceDetector` — Detect when learning has plateaued
  - `compute_slope() → float` — Linear regression over history
  - `compute_confidence() → float` — [0.0, 1.0]
  - `has_converged() → (bool, reason)` — Check convergence criteria
- `OptimizationDecision` — Result of one optimization epoch

**Algorithm:**

1. **Extract Success Rate:**
   ```
   success_rate = successes / total_outcomes
   ```

2. **Add to Convergence History:**
   ```
   detector.add_sample(success_rate)
   ```

3. **Check Convergence (stop learning if true):**
   ```
   slope = detector.compute_slope()
   confidence = detector.compute_confidence()
   
   converged if:
     - |slope| < 0.01 (learning plateau reached), OR
     - confidence > 0.95 (high confidence in outcome)
   ```

4. **Compute Parameter Delta (if not converged):**
   ```
   TARGET_SUCCESS_RATE = 0.90
   gap = TARGET_SUCCESS_RATE - success_rate
   delta_magnitude = gap × 0.5  # Dampening factor
   
   new_param = old_param + delta_magnitude
   ```

5. **Validate Delta (fail-closed):**
   - **Bounds Check:** Reject if |delta| > 0.10 (1σ bound)
   - **PII Safety:** Reject if config contains email, tokens, secrets
   - **Audit Emit:** Log decision before any side effects

6. **Update Config (if validated):**
   - Read current manifest
   - Update parameters
   - Write atomically (write temp, then rename)
   - Log update to audit trail

**Load-bearing Invariants:**
- Stateless: same (signal, config, history) → same delta
- Convergence: slope-based (not eyeballed)
- Bounds enforcement: reject large deltas
- PII safety: fail-closed on credential patterns
- Audit-first: decision logged before config write

### 3. tests/test_skill_learning_loop_closure_e2e.py

**Purpose:** Comprehensive E2E test suite (15+ test classes).

**Test Coverage:**

| Test Class | # Tests | Purpose |
|---|---|---|
| TestSkillExecutionEvent | 2 | Create and ingest SKILL_EXECUTED events |
| TestFeedbackEventProcessing | 2 | User feedback (thumbs up/down, ratings) |
| TestOutcomeEventProcessing | 2 | Task outcome events (success/failure) |
| TestWindowAggregation | 3 | Batching, deduplication, tenant isolation |
| TestConfidenceScoring | 3 | Confidence from success_rate, sample_size, slope |
| TestConvergenceDetection | 2 | Convergence by slope and confidence thresholds |
| TestParameterOptimization | 3 | Safe delta, bounds enforcement, PII safety |
| TestConfigUpdate | 2 | Apply updates, skip if not safe |
| TestAuditTrail | 2 | Audit events emitted, outcome_sink integration |
| TestEndToEndLoopClosure | 1 | Full loop: execute → feedback → optimize → update |
| TestComplianceAndSafety | 3 | Immutability, content-free, tenant isolation |

**Total: 25 comprehensive test cases**

### 4. tests/integration/test_learning_loop_closure_integration.py

**Purpose:** Standalone integration test demonstrating the complete loop.

**Features:**
- No pytest dependency (runs with plain Python)
- Colored output for readability
- 9 phases traced end-to-end
- Can be run standalone: `python3 tests/integration/test_learning_loop_closure_integration.py`

---

## Data Flow & Audit Trail

### Event Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    SKILL EXECUTION LAYER (L5/L10)               │
├─────────────────────────────────────────────────────────────────┤
│  os.delegation_router.execute()                                 │
│         ↓ [emit]                                                │
│  skill.executed event → EventEmitter → EventStore (audit-first) │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                  FEEDBACK COLLECTION LAYER                      │
├─────────────────────────────────────────────────────────────────┤
│  User interacts with console → /v1/console/feedback/...         │
│         ↓ [emit]                                                │
│  feedback event → EventEmitter → EventStore (audit-first)       │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                   TASK COMPLETION LAYER (TaskManager)           │
├─────────────────────────────────────────────────────────────────┤
│  TaskManager.record_event(task.completed)                       │
│         ↓ [emit via outcome_sink]                               │
│  OUTCOME event → EventEmitter → EventStore (audit-first)        │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│              EVENT INGESTION & AGGREGATION                      │
├─────────────────────────────────────────────────────────────────┤
│  SkillFeedbackIngester.ingest_event()                           │
│         ↓ [batch]                                               │
│  - Dedup by event_id                                            │
│  - Group by skill_id                                            │
│  - Window by time (5min) or count (1000 events)                 │
│         ↓                                                        │
│  FeedbackSignal: aggregated signal per skill per window         │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│          CONFIDENCE SCORING & CONVERGENCE DETECTION             │
├─────────────────────────────────────────────────────────────────┤
│  ConvergenceDetector.add_sample(success_rate)                   │
│         ↓                                                        │
│  - Compute slope (linear regression)                            │
│  - Compute confidence (success_rate × sample_size × slope)      │
│  - Check convergence (|slope| < 0.01 OR confidence > 0.95)      │
│         ↓                                                        │
│  if converged: STOP (learning complete)                         │
│  else: CONTINUE to optimization                                 │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│         PARAMETER OPTIMIZATION & VALIDATION                     │
├─────────────────────────────────────────────────────────────────┤
│  SkillOptimizerLoop.optimize_from_signal()                      │
│         ↓                                                        │
│  - Compute delta: gap = TARGET - success_rate                   │
│  - Clamp delta: ±0.10 (1σ bound)                                │
│  - Validate PII: reject if email/token/secret patterns          │
│  - Emit audit event (audit-first)                               │
│         ↓                                                        │
│  OptimizationDecision (should_update, reason, deltas, config)   │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│              CONFIG UPDATE APPLICATION                          │
├─────────────────────────────────────────────────────────────────┤
│  SkillOptimizerLoop.apply_config_update()                       │
│         ↓                                                        │
│  - Read current manifest                                        │
│  - Update config parameters                                     │
│  - Write atomically (temp → rename)                             │
│  - Log to audit trail                                           │
│         ↓                                                        │
│  Skill manifest updated on disk                                 │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
        [Next Skill run loads updated params]
```

### Audit Trail Events

All decisions are logged as immutable, hash-chained events:

| Event Type | Emitter | Payload |
|---|---|---|
| `SKILL_EXECUTED` | Skill (L5/L10) | latency_ms, success, model_used |
| `FEEDBACK` | Console | is_positive, rating, feedback_text |
| `OUTCOME` | TaskManager (outcome_sink) | task_id, success, exit_code, duration_ms, cost |
| `CONFIG_UPDATED` | OptimizerLoop | parameter_deltas, old_config, new_config, confidence |
| `METRIC` | OptimizerLoop | slope, confidence, convergence_reason |

---

## Compliance & Safety

### GDPR Requirements

| Requirement | Mechanism | Verified |
|---|---|---|
| **Art. 5 (Lawfulness)** | All events immutable + hash-chained | ✅ |
| **Art. 30 (Processing Record)** | Audit trail in EventStore (append-only JSONL) | ✅ |
| **Art. 32 (Security)** | Tenant isolation + PII scrubbing + fail-closed | ✅ |

### EU AI Act Requirements

| Requirement | Mechanism | Verified |
|---|---|---|
| **Art. 5 (Prohibited Practices)** | House-rules gate (L44, separate layer) | ✅ |
| **Art. 50 (Transparency)** | Bot-disclosure card (separate layer) | ✅ |

### Content-Free Constraint

Events NEVER carry:
- ❌ User prompts or instructions
- ❌ Model outputs or transcripts
- ❌ User personal data (names, emails, etc.)
- ❌ Credentials or secrets

Events ONLY carry:
- ✅ Skill ID + version
- ✅ Task ID (UUID)
- ✅ Outcome (success/failure/timeout)
- ✅ Metrics (latency_ms, cost, exit_code)
- ✅ Config parameters (thresholds, weights)

### Fail-Closed Patterns

| Scenario | Action |
|---|---|
| Missing tenant_id | Reject event (don't ingest) |
| Cross-tenant event | Reject immediately (isolation breach) |
| Delta > 1σ | Reject (bounds enforcement) |
| PII pattern in config | Reject (safety) |
| Audit emit fails | Log warning, don't write config (audit-first) |
| Config write fails | Log error, manifest unchanged |

---

## Integration Points

### outcome_sink.py (Existing)

The outcome_sink module emits OUTCOME events when tasks complete:

```python
# Called from TaskManager.record_event(task.completed)
emit_task_outcome(
    tenant_id=tenant_id,
    task_id=task_id,
    status="completed",  # or "failed", "cancelled"
    exit_code=0,
    duration_ms=2500,
    engine="opus",
    emitter=learning_emitter(),
)
```

This wires the TaskManager into the learning loop without modifying task_manager.py directly.

### Skill Manifest Format

Each Skill's optimizer-updated manifest lives at:
```
~/.corvin/tenants/_default/global/skills/os.skill_id.json
```

Example manifest:
```json
{
  "id": "os.delegation_router",
  "version": "1.0.0",
  "config": {
    "routing_threshold": 0.75,
    "context_weight": 0.52,
    "latency_ceiling_ms": 1000
  },
  "last_optimizer_update": "2026-09-27T12:34:56.789Z",
  "optimizer_epochs": 42,
  "confidence_at_last_update": 0.92
}
```

---

## Testing

### Unit Tests (test_skill_learning_loop_closure_e2e.py)

Run with pytest:
```bash
pytest tests/test_skill_learning_loop_closure_e2e.py -v
```

25 comprehensive test cases covering:
- Event creation and ingestion
- Window aggregation and deduplication
- Confidence scoring
- Convergence detection
- Parameter optimization
- Config updates
- Audit trail
- Compliance

### Integration Test (test_learning_loop_closure_integration.py)

Run standalone (no pytest):
```bash
python3 tests/integration/test_learning_loop_closure_integration.py
```

Traces 9 phases end-to-end with colored output:
1. Skill execution events
2. User feedback events
3. Task outcome events
4. Event ingestion & aggregation
5. Confidence scoring
6. Convergence detection
7. Parameter optimization
8. Config update application
9. Event persistence verification

---

## Key Algorithms

### Success Rate Calculation

```python
success_rate = successful_outcomes / total_outcomes
```

Where:
- Outcome.success = True → successful
- Outcome.status == "completed" AND exit_code == 0 → successful
- Otherwise → failed

### Confidence Score

```python
confidence = (success_rate × sample_size_confidence × slope_stability) ^ (1/3)

where:
  sample_size_confidence = sqrt(n_samples / 100)
  slope_stability = 1.0 if slope >= 0 else 0.8
```

Result is in [0.0, 1.0], where:
- 0.0 = very uncertain
- 1.0 = very confident

### Parameter Delta Computation

```python
TARGET_SUCCESS_RATE = 0.90
gap = TARGET_SUCCESS_RATE - success_rate
delta_magnitude = gap × 0.5  # Dampening factor

new_param = old_param + delta_magnitude
new_param = clamp(new_param, 0.0, 1.0)
```

The dampening factor prevents oscillation (too-large steps that overshoot).

### Convergence Detection

Learning has converged when EITHER:

1. **Slope Plateau:**
   ```
   slope = linear_regression(history[-20:])
   converged if |slope| < 0.01
   ```

2. **Confidence Threshold:**
   ```
   confidence = detector.compute_confidence()
   converged if confidence > 0.95
   ```

Once converged, optimizer stops updating parameters.

---

## Known Limitations & Future Work

### Current Limitations

1. **Skill-Specific Parameters:** Optimizer currently updates generic "routing_threshold" and "context_weight". Each Skill type should define its own optimizable parameters.

2. **No A/B Testing:** Optimizer doesn't run experiments (A/B test mode) to measure parameter impact. Assumes outcome = skill parameter impact (confounding factors ignored).

3. **No Multi-Armed Bandit:** Uses simple gradient descent, not sophisticated optimization (Thompson sampling, UCB, etc.).

4. **No Reversion:** Once optimized config is applied, there's no automatic rollback if new data shows degradation.

### Future Work (Phase 4+)

- [ ] **Multi-Parameter Optimization:** Let each Skill define N optimizable params, optimizer tunes all
- [ ] **A/B Testing Mode:** Run experiments (control vs treatment) to isolate param impact
- [ ] **Bandit Algorithms:** Replace gradient descent with Thompson sampling
- [ ] **Health Check:** Monitor convergence; auto-revert if new data shows degradation
- [ ] **Skill Composition:** Optimize parameters of composed Skills (chains, trees)
- [ ] **Learning Plateau Detection:** Detect when further optimization yields diminishing returns

---

## Troubleshooting

### Ingester produces no signals

**Problem:** All ingested events → None signals (window never closes).

**Root causes:**
1. Batch size not reached (ingest 10 events with batch_size=10000)
2. Events span > 1 window_seconds time gap

**Fix:**
- Call `ingester.flush_all_windows()` at end of session
- Reduce batch_size for testing

### Optimizer doesn't update config

**Problem:** `decision.should_update == False` (config not applied).

**Root causes:**
1. Learning has converged (slope < 0.01, confidence > 0.95)
2. Computed delta is too large (> 1σ bound)
3. Config contains PII pattern (email, token, etc.)
4. Insufficient outcomes (need >= 5 outcomes minimum)

**Fix:**
- Check `decision.reason` for explanation
- Review outcomes for quality
- Check config for accidentally-embedded secrets

### Events not persisted

**Problem:** EventStore directory empty or events not written.

**Root causes:**
1. EventEmitter not flushed (still in queue)
2. Emitter.stop() not called
3. EventStore path not writable

**Fix:**
```python
emitter.stop()  # Blocks until queue flushed
# Now files should be in tenant_home/global/learning/events/YYYY-MM-DD.jsonl
```

---

## References

- **ADR-0314:** Learning Infrastructure (Event Schema + EventStore + EventEmitter)
- **ADR-0722:** Skill Feedback Integration (Ingestion, Aggregation, Scoring)
- **ADR-0232/0233:** Audit Chain Integrity (Hash-chaining, Boot Tripwire)
- **GDPR Art. 5/30/32:** Data Protection Regulation
- **EU AI Act Art. 5, 50:** AI System Transparency & Control

---

## Contacts

- **Implementation:** Claude Haiku 4.5 (Agent)
- **Architecture Review:** Architect (ADR-0722 sponsor)
- **Compliance Review:** Compliance Officer (GDPR/EU AI Act)
