# Checkpoint Monitoring Specification

**Purpose:** Real-time visibility into Option B + C implementation progress  
**Audience:** Operator (you) during validation  
**Update Frequency:** Every checkpoint completion  

---

## 📊 Dashboard Layout

```
┌─────────────────────────────────────────────────────────────┐
│  OPTION B + C AUTONOMY IMPLEMENTATION — LIVE PROGRESS       │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  OPTION B: Context Pipeline v2 Validation (THIS SESSION)    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ [████░░░░░] 40% complete (k=1/3 running)               │ │
│  │                                                           │ │
│  │ Checkpoint B1: Original + Pipeline Context              │ │
│  │   Status: ✅ GREEN (10/10 prompts correct)              │ │
│  │   Metric: Both layers present in agent prompt            │ │
│  │   Time:   2026-08-24 14:32:15 UTC                        │ │
│  │                                                           │ │
│  │ Checkpoint B2: Tier Classification Accuracy              │ │
│  │   Status: ⏳ IN_PROGRESS (k=2 running)                   │ │
│  │   Metric: 7/10 classifications match human review        │ │
│  │   Target:  9/10 (90% accuracy)                           │ │
│  │   Time:   2026-08-24 14:45:00 UTC                        │ │
│  │                                                           │ │
│  │ Checkpoint B3: Entropy Detection Latency                 │ │
│  │   Status: ⏹️  PENDING (waiting for B2 green)            │ │
│  │   Metric: contradiction_detected_at_iteration            │ │
│  │   Target:  < 2 iterations                                │ │
│  │   Time:   (scheduled after B2 passes)                    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  OPTION C: Session Manager Phase 2.1 (Weeks 1-3)            │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ [░░░░░░░░░░] 0% complete (scheduled Week 1)             │ │
│  │                                                           │ │
│  │ SPRINT 1: SessionLifecycle + Checkpoint                 │ │
│  │   [░░░░░░░░░░] Week 1 (5 days)                           │ │
│  │   Checkpoints: C1 (triggers), C2 (fidelity), C3 (persist)│ │
│  │   Target: 35 tests green by EOW1                         │ │
│  │                                                           │ │
│  │ SPRINT 2: ContextReducer + Recovery                     │ │
│  │   [░░░░░░░░░░] Week 2 (5 days)                           │ │
│  │   Checkpoints: C4 (reduction), C5 (pattern), C6 (replay) │ │
│  │   Target: 40 tests green by EOW2                         │ │
│  │                                                           │ │
│  │ SPRINT 3: Monitors + E2E + Runbook                      │ │
│  │   [░░░░░░░░░░] Week 3 (3 days)                           │ │
│  │   Checkpoints: C7-C10 (all monitors), E2E audit task     │ │
│  │   Target: 75+ tests + 16-hour audit complete by EOW3    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  AUTONOMY LEVEL ACHIEVED                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Current: Tier 2 (1-3 day tasks, with monitoring)        │ │
│  │ After B: Tier 2+ (5+ sessions, no context loss)        │ │
│  │ After C: Tier 3 (16 hours autonomous, 0 intervention)   │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Checkpoint Definitions

### OPTION B (Context Pipeline v2)

#### Checkpoint B1: Two-Layer Context Rendering
**Trigger:** After k=1 implementation complete  
**Observable:** Agent receives prompt with both context layers

```python
# METRIC
def check_b1():
    for test_prompt in test_suite:
        prompt_text = render_agent_prompt(test_prompt)
        has_original = "ORIGINAL CONTEXT" in prompt_text
        has_pipeline = "PIPELINE CONTEXT" in prompt_text
        assert has_original and has_pipeline, "Missing layer"
    return True
```

**Success Criterion:** 10/10 test prompts contain both layers  
**Failure Action:** Roll back layer separation (revert to single-layer)  
**Alert Threshold:** < 9/10 passes  
**Metrics to Log:**
- prompt_contains_original_context: bool
- prompt_contains_pipeline_context: bool
- false_positive_rate: float (additions irrelevant?)
- wall_clock_time: float (k=1 elapsed)

---

#### Checkpoint B2: Tier Quality Classification
**Trigger:** After B1 green, run k=2  
**Observable:** Tier classification matches human review

```python
# METRIC
def check_b2():
    human_reviews = load_human_benchmark()  # 10 additions, manually classified
    for addition in human_reviews:
        model_tier = quality_gate.classify(addition)
        human_tier = human_reviews[addition.id]
        assert model_tier == human_tier, f"Mismatch: model={model_tier}, human={human_tier}"
    return True
```

**Success Criterion:** 9/10 classifications correct (90% accuracy)  
**Failure Action:** Refine tier rules (Tier 1/2/3 thresholds)  
**Alert Threshold:** < 8/10 correct  
**Metrics to Log:**
- tier_classification_accuracy: float
- tier_distribution: {TIER_1: count, TIER_2: count, TIER_3: count}
- confusion_matrix: 3x3 (model vs human)
- wall_clock_time: float (k=2 elapsed)

---

#### Checkpoint B3: Entropy Detection Latency
**Trigger:** After B2 green, run k=3  
**Observable:** Contradictions detected early (< 2 iterations)

```python
# METRIC
def check_b3():
    for task in test_cases_with_known_contradictions:
        detected_at_iteration = run_task_until_contradiction(task)
        assert detected_at_iteration <= 2, f"Late detection: iteration {detected_at_iteration}"
    return True
```

**Success Criterion:** 8/10 tasks detect contradiction before iteration 2  
**Failure Action:** Improve entropy detection heuristics  
**Alert Threshold:** < 7/10 detected early  
**Metrics to Log:**
- contradiction_detected_at_iteration: int
- detection_latency_iterations: float (avg)
- false_positive_rate_entropy: float (false alarms?)
- wall_clock_time: float (k=3 elapsed)

---

### OPTION C (Self-Managed Sessions)

#### Checkpoint C1: Session Split Triggers Fire
**Trigger:** Sprint 1, after SessionLifecycleManager implementation  
**Observable:** All 6 trigger rules activate correctly

```python
# METRIC (6 rules)
def check_c1():
    test_scenarios = {
        "phase_exit": generate_phase_exit_scenario(),
        "context_limit_85": generate_context_limit(0.85),
        "token_burn": generate_token_burn(),
        "explicit_milestone": generate_milestone(),
        "iteration_cap_50": generate_iteration(50),
        "stall_30min": generate_stall(30)
    }
    for rule_name, scenario in test_scenarios.items():
        triggered = lifecycle_mgr.check_triggers(scenario)
        assert rule_name in triggered, f"Rule {rule_name} didn't fire"
    return True
```

**Success Criterion:** 6/6 trigger rules fire in correct scenarios  
**Failure Action:** Debug trigger logic (if/when clauses)  
**Alert Threshold:** < 6/6 firing  
**Metrics to Log:**
- trigger_rules_fired: dict {rule: bool}
- trigger_latency_ms: float (time from condition → detection)
- false_positive_triggers: int
- wall_clock_time: float (C1 elapsed)

---

#### Checkpoint C2: Checkpoint Round-Trip Fidelity
**Trigger:** Sprint 1, after CheckpointManager implementation  
**Observable:** Serialize → Deserialize → Compare = Identity

```python
# METRIC (Round-trip test)
def check_c2():
    for task_state in test_states:
        serialized = checkpoint_mgr.serialize(task_state)
        deserialized = checkpoint_mgr.deserialize(serialized)
        assert task_state == deserialized, "State mismatch after round-trip"
    return True
```

**Success Criterion:** 10/10 test states round-trip perfectly  
**Failure Action:** Debug serialization (missing fields, type mismatches)  
**Alert Threshold:** < 10/10 perfect  
**Metrics to Log:**
- serialization_fidelity: float (fields_preserved / total_fields)
- deserialization_fidelity: float (values_match / total_values)
- checkpoint_json_size: int (bytes)
- wall_clock_time: float (C2 elapsed)

---

#### Checkpoint C3: Checkpoint Persistence
**Trigger:** Sprint 1, after filesystem backend ready  
**Observable:** Write → Read from disk → Verify = Same

```python
# METRIC (Persistence test)
def check_c3():
    for checkpoint in test_checkpoints:
        path = checkpoint_mgr.save(checkpoint)
        loaded = checkpoint_mgr.load(path)
        assert checkpoint == loaded, f"Persistence failed for {path}"
    return True
```

**Success Criterion:** 10/10 checkpoints persist correctly  
**Failure Action:** Debug filesystem I/O (permissions, corruption)  
**Alert Threshold:** < 10/10 loaded correctly  
**Metrics to Log:**
- persistence_success_rate: float
- load_latency_ms: float (read time)
- checkpoint_file_count: int
- total_disk_used_mb: float
- wall_clock_time: float (C3 elapsed)

---

#### Checkpoint C4: Context Reduction Ratio
**Trigger:** Sprint 2, after ContextReducer implementation  
**Observable:** Reduce 200k → 18k tokens (91% compression)

```python
# METRIC (Compression test)
def check_c4():
    for context in test_contexts:
        original_tokens = count_tokens(context)
        reduced = context_reducer.reduce(context)
        reduced_tokens = count_tokens(reduced)
        ratio = reduced_tokens / original_tokens
        assert ratio < 0.15, f"Reduction failed: {ratio:.1%}"  # < 15% = > 85% reduction
    return True
```

**Success Criterion:** 8/10 contexts achieve > 85% reduction  
**Failure Action:** Adjust Tier 2/3 preservation rules  
**Alert Threshold:** < 75% avg reduction  
**Metrics to Log:**
- context_reduction_ratio: float (reduced / original)
- avg_reduction_percent: float (e.g., 91%)
- preserved_fields: list (what stayed)
- dropped_fields: list (what removed)
- wall_clock_time: float (C4 elapsed)

---

#### Checkpoint C5: Recovery Pattern Selection
**Trigger:** Sprint 2, after RecoveryEngine implementation  
**Observable:** Error type → correct recovery pattern mapped

```python
# METRIC (Pattern matching)
def check_c5():
    test_errors = {
        TimeoutError: RecoveryPattern.REPLAY,
        StrategyFailedError: RecoveryPattern.ADAPT,
        ValidationError: RecoveryPattern.BACKTRACK,
        QuotaError: RecoveryPattern.PAUSE,
    }
    for error_type, expected_pattern in test_errors.items():
        error = error_type("test")
        selected_pattern = recovery_engine.select_pattern(error)
        assert selected_pattern == expected_pattern, f"Wrong pattern for {error_type}"
    return True
```

**Success Criterion:** 4/4 error types map to correct patterns  
**Failure Action:** Debug pattern selection logic  
**Alert Threshold:** < 4/4 correct  
**Metrics to Log:**
- recovery_patterns_tested: dict {error_type: pattern_selected}
- pattern_accuracy: float
- pattern_latency_ms: float (selection time)
- wall_clock_time: float (C5 elapsed)

---

#### Checkpoint C6: Replay Idempotency
**Trigger:** Sprint 2, after RecoveryEngine replay tested  
**Observable:** Replay same strategy → same result

```python
# METRIC (Determinism test)
def check_c6():
    for task_and_checkpoint in test_cases:
        result_1 = run_from_checkpoint(task_and_checkpoint)
        result_2 = run_from_checkpoint(task_and_checkpoint)
        assert result_1 == result_2, "Replay produced different results"
    return True
```

**Success Criterion:** 10/10 replays produce identical results  
**Failure Action:** Remove non-deterministic code (random seeds, timestamps)  
**Alert Threshold:** < 10/10 identical  
**Metrics to Log:**
- replay_idempotency_success: bool
- result_divergence_detected: bool
- result_diff: dict (if diverged)
- replay_latency_ms: float
- wall_clock_time: float (C6 elapsed)

---

#### Checkpoint C7-C10: Monitors (Sprint 3)
**Definitions parallel to B1-B3 + C1-C6**

| Checkpoint | Metric | Target | Failure Action |
|---|---|---|---|
| **C7** GoalAlign | goal_alignment_score >= 0.7 throughout | 8/10 tasks | Adjust drift detection threshold |
| **C8** ConsistencyVal | entropy_detected_before_phase_exit | 8/10 tasks | Improve contradiction heuristics |
| **C9** AssumptionTrack | all_assumptions_validated | 8/10 tasks | Add assumption extraction rules |
| **C10** CognitiveLoad | overload_detected_and_reset_triggered | 8/10 tasks | Tune cognitive load thresholds |

---

## 📈 Alerts & Thresholds

### Severity Levels

| Level | Condition | Action |
|---|---|---|
| **🔴 CRITICAL** | Any checkpoint fails go/no-go (< threshold) | HALT: Roll back, root cause analysis |
| **🟡 WARNING** | Checkpoint at risk (trending toward failure) | MONITOR: Adjust, collect more data |
| **🟢 OK** | Checkpoint passed (≥ threshold) | PROCEED: Move to next checkpoint |

### Alert Channels
```yaml
alerts:
  - slack: "#corvinos-deployments"  # Real-time
  - log: "~/.corvin/vibe/checkpoints.log"  # File
  - dashboard: "http://localhost:8080/option-b-c"  # Browser
```

### Example Alert
```
🔴 CRITICAL: Checkpoint B2 Failed
  Metric: tier_classification_accuracy
  Value:  0.65 (65% accuracy)
  Target: 0.90 (90% accuracy)
  Gap:    -0.25 (-25 percentage points)
  
Action Required:
  1. Halt k=2 iteration
  2. Review misclassified additions (human vs model)
  3. Identify rule gaps (which additions got wrong tier?)
  4. Adjust quality_gate thresholds
  5. Retry k=2

Details: file:///~/.corvin/vibe/checkpoints.log#B2_FAILURE_2026-08-24T14:45:00Z
```

---

## 📋 Logging Format

Each checkpoint logs to structured JSON:

```json
{
  "timestamp": "2026-08-24T14:32:15Z",
  "checkpoint_id": "B1",
  "phase": "OPTION_B",
  "iteration": "k=1",
  "status": "GREEN",
  "metrics": {
    "pass_count": 10,
    "total_count": 10,
    "success_rate": 1.0,
    "latency_ms": 1250
  },
  "details": {
    "both_layers_present": true,
    "false_positive_rate": 0.0
  },
  "action": "PROCEED_TO_B2"
}
```

---

## ✅ Success Criteria Summary

### OPTION B Green (All 3 Checkpoints)
```
✅ B1: Both context layers rendered (10/10)
✅ B2: Tier classification accurate (9/10)
✅ B3: Entropy detected early (8/10)
→ Multi-session tasks work without context loss
```

### OPTION C Sprint 1 Green (All 3 Checkpoints)
```
✅ C1: All 6 split triggers fire (6/6)
✅ C2: Checkpoint round-trips perfect (10/10)
✅ C3: Persistence works (10/10)
→ Autonomous checkpoints across sessions enabled
```

### OPTION C Sprint 2 Green (All 3 Checkpoints)
```
✅ C4: Context reduction > 85% (8/10)
✅ C5: Recovery patterns selected correctly (4/4)
✅ C6: Replay is idempotent (10/10)
→ Autonomous error recovery enabled
```

### OPTION C Sprint 3 Green (All 4 Checkpoints + E2E)
```
✅ C7: Goal alignment maintained (8/10)
✅ C8: Entropy detected early (8/10)
✅ C9: Assumptions validated (8/10)
✅ C10: Cognitive load managed (8/10)
✅ E2E: 16-hour audit task completes unattended
→ TIER-3 AUTONOMY ACHIEVED
```

---

**Dashboard Updates:** Every checkpoint complete  
**Frequency:** Real-time (pushed to Slack / logged)  
**Owner:** You (automated via monitoring system)  
**Retention:** All logs archived for post-mortem analysis  
