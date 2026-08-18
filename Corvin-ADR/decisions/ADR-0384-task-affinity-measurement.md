---
id: ADR-0384
status: PROPOSED
depends_on: [ADR-0383, ADR-0314]
relates_to: [ADR-0385]
paths:
  - core/learning/affinity_model.py
  - core/learning/task_classifier.py
  - tests/learning/test_affinity.py
docs:
  - docs/v0.6-design/V0.6_IDEAS.md
---

# ADR-0384: Task Affinity Measurement

## Problem

Operators have different strengths across task types (some excel at authentication, struggle with memory management). The fingerprint model (ADR-0383) needs a principled way to measure and track per-task-type success rates.

## Solution

**Per-task-type Bayesian update** on every turn outcome. Affinity confidence saturates at n=30 samples, enabling reliable affinity-based routing by v0.7.

## Key Mechanisms

### 1. Task Type Classification

Every turn is classified into exactly one task type:

```python
TASK_TYPES = {
    "auth": "authentication (credentials, OAuth, MFA)",
    "memory": "memory management (caching, RAM optimization)",
    "schema": "database schema (design, migration, indexing)",
    "api": "API design (endpoints, contracts, versioning)",
    "perf": "performance optimization (latency, throughput)",
    "ui": "user interface (React, CSS, accessibility)",
    "security": "security hardening (cryptography, input validation)",
    "testing": "testing (unit, integration, E2E)",
}
```

**Classification rule:** Extract from turn metadata or infer from turn goal + task context. One turn = one type.

### 2. Outcome Measurement

Every turn produces an outcome:

```python
Outcome = Literal["success", "failure", "unclear"]
```

- **success:** Operator chose correct solution, fixed the problem, or verified working code
- **failure:** Operator's choice led to errors, rework, or suboptimal results
- **unclear:** Incomplete turn, operator didn't finish task (don't update affinity)

### 3. Bayesian Update

```python
def update_affinity(
    operator_id: str,
    tenant_id: str,
    task_type: str,
    outcome: Outcome,
) -> TaskAffinity:
    """Increment success/failure count, recalculate affinity."""
    
    # Load current affinity
    affinity = load_affinity(operator_id, task_type)
    
    # Increment counters
    if outcome == "success":
        success_count += 1
    elif outcome == "failure":
        failure_count += 1
    else:  # unclear
        return affinity  # no update
    
    # Recalculate
    total = success_count + failure_count
    success_rate = success_count / total if total > 0 else 0.5
    confidence = min(1.0, total / 30.0)  # saturate at 30 samples
    
    affinity = affinity.update(
        success_rate=success_rate,
        sample_count=total,
        confidence=confidence,
        strength_tier=categorize_strength(success_rate),
        last_practiced=now(),
    )
    
    # Persist
    save_affinity(operator_id, task_type, affinity)
    
    # Emit learning event
    emit_event(
        "task_affinity_updated",
        operator_id=operator_id,
        task_type=task_type,
        success_rate=success_rate,
        sample_count=total,
        confidence=confidence,
    )
    
    return affinity
```

### 4. Confidence Saturation

```python
def saturation_curve(sample_count: int) -> float:
    """Confidence = min(1.0, n / 30)."""
    return min(1.0, sample_count / 30.0)
```

- **n=1:** confidence 0.03 (very uncertain)
- **n=10:** confidence 0.33 (low certainty)
- **n=30:** confidence 1.0 (fully confident, saturated)
- **n=100+:** confidence 1.0 (no additional certainty gain)

**Rationale:** 30 samples = ~95% confidence interval width for Bernoulli outcomes at p=0.5.

### 5. Strength Tier Classification

```python
def categorize_strength(success_rate: float) -> str:
    """Map success rate to tier."""
    if success_rate >= 0.75:
        return "strong"      # 75%+ success: this operator is good at this type
    elif success_rate >= 0.45:
        return "neutral"     # 45-75%: average, no special advantage
    else:
        return "weak"        # <45%: operator struggles with this type
```

## Measurement Accuracy

### Validation: Cross-Validation

For every operator with ≥30 decisions on a task type:
1. Hold out 10 recent decisions (test set)
2. Measure affinity on remaining 20 decisions
3. Compare predicted success_rate to actual success on test set
4. Measure error: `|predicted_rate - actual_rate|`

**Target:** Mean absolute error (MAE) < 0.15 (15 percentage points).

### Benchmark

```python
def benchmark_affinity_prediction():
    """Validate affinity model accuracy."""
    
    # For each operator with ≥30 decisions per task type
    for operator_id in operators:
        for task_type in task_types:
            decisions = load_decisions(operator_id, task_type)
            if len(decisions) < 30:
                continue
            
            # Train on first 20, test on last 10
            train_set = decisions[:20]
            test_set = decisions[20:30]
            
            # Measured affinity from training set
            train_affinity = compute_affinity_from(train_set)
            predicted_success_rate = train_affinity.success_rate
            
            # Actual success on test set
            actual_success_rate = sum(1 for d in test_set if d.outcome == "success") / len(test_set)
            
            # Error
            error = abs(predicted_success_rate - actual_success_rate)
            record_error(operator_id, task_type, error)
    
    # Aggregate
    errors = collect_all_errors()
    mean_error = mean(errors)
    percentile_95 = percentile(errors, 95)
    
    assert mean_error < 0.15, f"MAE {mean_error} exceeds target 0.15"
    assert percentile_95 < 0.30, f"p95 error {percentile_95} exceeds target 0.30"
```

## Testing (25+ tests)

### Unit Tests (15)
- Bayesian update: success/failure/unclear cases (3)
- Confidence saturation curve (2)
- Strength tier classification (3)
- Cross-validation framework (3)
- Edge cases: zero/one/many samples (4)

### Integration Tests (10)
- Affinity persistence (3)
- Task type classification (4)
- Learning event emission (3)

---

## GDPR Notes

Affinity data is fully derived from operator's own decisions (Art. 5, 6, 30, 32). No cross-operator profiling or enrichment. Stored locally, hash-chained in audit trail.

## Rollback

Set `spec.features.operator_modeling_affinity: false` to disable updates (v0.5 behavior: no affinity tracking).

