# Track B: Learning Loop Integration — Gate 1: Dialectical Reasoning

**Date:** 2026-09-17  
**Status:** Gate 1 Design Complete  
**Reference:** ADR-0676 (Learning Loop), ADR-0314 (Learning Infrastructure), ADR-0532 (OS-Skills)

---

## Design Question 1: Feedback Persistence Model

### Thesis (Simple Approach)
Update Skill config in-memory only. Feedback → Optimizer → config delta applied to current SkillInstance. On restart, Skill loads original config from manifest.

**Pros:**
- Simple: no persistent state management
- Safe: old sessions can't corrupt new ones

**Cons:**
- Learning lost on restart (work doesn't persist)
- Can't reason about learning history
- Violates audit-trail principle (decisions aren't permanent)

### Antithesis (Complex Approach)
Persist every config update to `~/.corvin/tenants/<tenant>/skills/<skill_id>/config_history.jsonl`. Each update versioned with timestamp, feedback_id, operator (who/what triggered it).

**Pros:**
- Learning persists across restarts
- Audit trail: every config change is logged
- Rollback capability: version N→N-1
- Convergence visible: plot config deltas over time

**Cons:**
- File I/O + disk space
- Concurrency risk: parallel feedback processing
- Schema migration risk: if learnable params change

### Synthesis (Hybrid Approach — **CHOSEN**)
**Persistent config with immutable history:**

1. **Runtime state:** Skill config lives in memory (SkillInstance). Applied to next execution.
2. **Persisted history:** `~/.corvin/tenants/<tenant>/skills/<skill_id>/config_history.jsonl` (append-only).
3. **On load:** Skill loads base config from manifest. Then loads latest version from config_history.
4. **On update:** Emit ConfigUpdateEvent → EventStore (audit). Write to config_history.jsonl (versioned).
5. **Rollback:** CLI `corvin skill-config rollback <skill_id> --version <N>` reads history, restores.

**Constraints:**
- Config updates MUST be versioned + immutable (append-only file, no rewrites)
- All updates audit-logged (who, when, feedback_id, delta, reason)
- Tenant-scoped: one history per skill per tenant
- TTL-gated: config older than N days is archived, not deleted (GDPR)

---

## Design Question 2: Convergence Criteria

### Thesis (Metric-Based)
Convergence = single metric exceeds threshold (confidence > 0.85 OR latency variance < 10%).

**Pros:**
- Simple: one number to check
- Fast: stop learning early

**Cons:**
- Over-fits to single dimension
- May oscillate on boundary (e.g., confidence bounces 0.84↔0.86)
- Ignores stability (high variance under threshold = unstable)

### Antithesis (Multi-Metric AND)
Convergence = ALL metrics converge: confidence > 0.85 AND latency variance < 10% AND error_rate < 1%.

**Pros:**
- Robust: all dimensions must improve
- Prevents false positives

**Cons:**
- Slow: waits for slowest metric to converge
- Too strict: may never converge if one metric stuck

### Synthesis (Multi-Metric with Priorities — **CHOSEN**)
**Convergence = weighted combination + stability window:**

1. **Primary:** Confidence score (P) > 0.85
2. **Secondary:** Latency p95 improvement (L) > -20% (faster than baseline)
3. **Tertiary:** Error rate (E) < 1%
4. **Stability:** All three metrics stable for 20-sample sliding window (no single value changes >5%)

**Converged if:**
- (P > 0.85 AND L improved AND E < 1%) **AND** (20 samples with no values oscillating >5%)

**Metric Calculation:**
- **Confidence:** mean(feedback_confidence_scores) over last 50 samples
- **Latency:** p95(skill_execution_latency_ms) vs baseline_p95
- **Error rate:** errors / total_executions over last 50 samples

**Why this works:**
- Requires improvement, not just high scores (baseline matters)
- Prevents oscillation: stability window detects bouncing
- Allows partial convergence: if one metric stuck, others can continue

---

## Design Question 3: Config Update Triggering

### Thesis (Eager Update)
Every feedback → immediately recompute optimizer → update config (if delta > threshold).

**Pros:**
- Learning is immediate
- Fast adaptation to user corrections

**Cons:**
- Config churn: may oscillate between versions
- No batching: single outlier feedback → config change
- Audit spam: hundreds of updates per hour

### Antithesis (Lazy/Batch Update)
Collect feedback in memory for N hours, then run optimizer once per day in background.

**Pros:**
- Stable: smooth deltas, no oscillation
- Efficient: one update per day

**Cons:**
- Slow: user feedback takes 24h to take effect
- Memory bloat: buffer feedback all day
- Lost on crash: unsaved feedback

### Synthesis (Threshold-Triggered Batching — **CHOSEN**)
**Hybrid approach:**

1. **Collect:** All feedback → FeedbackBatcher (in-memory queue)
2. **Persist:** Every feedback → audit trail + config_history (append-only)
3. **Trigger optimizer when:**
   - N_new_feedback ≥ 10 **OR** time_since_last_update ≥ 1 hour
   - Whichever comes first
4. **Optimizer:** Reads all collected feedback → computes deltas
5. **Apply:** If delta > ε (0.01), update SkillInstance config
6. **Reset:** Clear batcher, log update, resume collecting

**Benefits:**
- Batching: stable, no oscillation
- Responsive: 10 feedbacks = immediate trigger (or 1h timeout)
- Audit-complete: every feedback logged, every update versioned

---

## Design Question 4: Learning Loop Closure

### Thesis (Separate Components)
Feedback → Optimizer → Config Update are three independent services. Feedback collector calls optimizer API, which calls config updater.

**Pros:**
- Decoupled: each service independent
- Testable: each tested separately

**Cons:**
- Cascading failures: if optimizer dies, config never updates
- Hard to reason about: is learning happening?
- Audit gap: if services crash mid-chain, partial state

### Antithesis (Unified Loop)
One FeedbackLoopOrchestrator owns entire chain. Receives feedback, runs optimizer, updates config, emits completion event all atomically.

**Pros:**
- Atomic: all-or-nothing
- Observable: single orchestrator logs everything

**Cons:**
- Monolithic: harder to extend
- Bottleneck: if orchestrator slow, feedback backs up

### Synthesis (Event-Driven Pipeline with Idempotency — **CHOSEN**)
**Loosely coupled but observable:**

1. **FeedbackCollector:** Receives feedback → validates → stores in EventStore → emits FeedbackReceivedEvent
2. **FeedbackBatcher:** Listens for FeedbackReceivedEvent → buffers until trigger
3. **Optimizer:** Listens for OptimizationTriggeredEvent → computes deltas → emits ConfigUpdateDecisionEvent
4. **ConfigApplier:** Listens for ConfigUpdateDecisionEvent → applies to SkillInstance → persists → emits ConfigUpdatedEvent
5. **ConvergenceMonitor:** Listens for ConfigUpdatedEvent → updates metrics → checks convergence → emits ConvergenceDetectedEvent

**Idempotency:**
- Every event has unique ID (feedback_id, optimization_id, config_update_id)
- Consumers deduplicate by ID (reject if already processed)
- Enables safe replay (if one component crashes mid-processing)

**Observability:**
- Full audit trail: every event logged
- Can trace feedback → config update (via IDs)
- Dashboard shows pipeline depth (feedback buffered, optimization in-flight, config applied)

---

## Design Decisions Summary

| Decision | Choice | Reasoning |
|---|---|---|
| **Feedback Persistence** | Hybrid (runtime + versioned history) | Persist learning across restarts, enable rollback, audit-complete |
| **Convergence Criteria** | Multi-metric with stability window | Prevents false positives, handles oscillation, requires real improvement |
| **Update Triggering** | Threshold-batched (10 feedback OR 1h) | Balance responsiveness + stability, avoid churn |
| **Loop Architecture** | Event-driven with idempotency | Loosely coupled, observable, resilient to crashes |

---

## Integration Points (Track A → Track B)

**Track A (Skill Forge v2.0)** provides:
- SkillForgeV2 registry
- SkillMetadata (skill_id, version, handler, dependencies)
- Execution pipeline (execute_skill → latency + success/error)
- NotificationDaemon for event emission

**Track B (Learning Loop)** adds:
- FeedbackCollector: listen for user feedback
- FeedbackBatcher: buffer feedback until threshold
- Optimizer: compute config deltas from feedback
- ConfigApplier: apply deltas to SkillInstance
- ConvergenceMonitor: detect learning convergence
- Config persistence: `~/.corvin/tenants/<tenant>/skills/<skill_id>/config_history.jsonl`

**Integration:**
1. User submits feedback on Skill execution → FeedbackEvent created
2. FeedbackCollector stores event → FeedbackReceivedEvent emitted
3. FeedbackBatcher buffers (≥10 OR ≥1h) → OptimizationTriggeredEvent emitted
4. Optimizer reads feedback → computes deltas → ConfigUpdateDecisionEvent emitted
5. ConfigApplier updates SkillInstance config + persists → ConfigUpdatedEvent emitted
6. ConvergenceMonitor tracks → ConvergenceDetectedEvent emitted
7. Next Skill execution uses updated config → loop closes

---

## Assumptions & Constraints

1. **Learnable Parameters:** Only skill config fields marked `learnable=true` in SkillMetadata
2. **Feedback Quality:** Assume feedback has random noise, inverse-prevalence weighted
3. **Skill Execution:** Each Skill.execute() call emits latency + success/error (from Track A)
4. **Audit Trail:** All Learning Loop events hash-chained (ADR-0314)
5. **Tenant Isolation:** All queries filtered by tenant_id (GDPR Art. 32)
6. **No Rollback During Learning:** Config history is append-only; rollback is manual CLI command

---

## Open Questions for Gate 2

1. **Real Entry Points:** Where does user feedback actually come from? Console UI endpoint? CLI command? Event bus?
2. **Skill Config Schema:** Which parameters are learnable? How are deltas bounded?
3. **Persistence Location:** Absolute path? Does it exist and is it writable?
4. **Monitoring Dashboard:** Should ConvergenceMonitor feed into Vibe (e.g., `/v1/console/learning/convergence`)?
5. **Feedback TTL:** How long to keep feedback in active learning window? (30 days? 90 days?)

---

## Commits Expected (Gates 3-7)

1. **Gate 2:** E2E wiring proof (entry points exist, tests pass)
2. **Gate 3:** FeedbackCollector, FeedbackBatcher, ConfigApplier implementation
3. **Gate 4:** Optimizer integration, ConvergenceDetector wiring
4. **Gate 4 Adversarial:** Attack tests (feedback injection, config corruption, oscillation)
5. **Gate 5:** Docs + ADR finalization + config_history schema documented
6. **E2E Proof:** Real metric improvement (execution latency improved after feedback-driven tuning)
