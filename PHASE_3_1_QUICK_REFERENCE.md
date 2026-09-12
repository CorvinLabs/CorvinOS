# Phase 3.1 Quick Reference — BayesianGateTuner

**Status:** ✅ Complete | **Date:** 2026-09-12 | **LoC:** 1,026 (learning.py)

## Instantiation

```python
from core.quality_gates import (
    BayesianGateTuner, GateFeedback, FeedbackType, KnowledgeGraph
)

# Create tuner
graph = KnowledgeGraph("db.sqlite", tenant_id="production")
tuner = BayesianGateTuner(
    graph=graph,
    tenant_id="production",
    initial_thresholds={"IdeaGate": 0.8, "ConceptGate": 0.85}
)
```

## Processing Feedback

```python
# Create feedback signal
feedback = GateFeedback(
    artifact_id="IDEA-2024-001",
    gate_name="IdeaGate",
    feedback_type=FeedbackType.VERDICT_CORRECT,
    confidence_score=0.9,
    tenant_id="production",
    verdict_correct=True,
)

# Update distribution (audit-logged)
tuner.update_from_feedback(feedback)
```

## Checking Convergence

```python
# Check if thresholds have converged
if tuner.is_converged():
    print("✓ Learning complete, thresholds stable")
else:
    print("⏳ Still learning...")

# Get current thresholds
thresholds = tuner.get_thresholds()
print(f"IdeaGate threshold: {thresholds['IdeaGate']:.4f}")

# Get detailed statistics
stats = tuner.get_statistics()
for gate, stat in stats.items():
    print(f"{gate}:")
    print(f"  Samples: {stat['sample_count']}")
    print(f"  Mean: {stat['mean_threshold']:.4f}")
    print(f"  Confidence: {stat['confidence']:.2%}")
    print(f"  Status: {stat['convergence_status']}")
```

## Saving Checkpoints

```python
# Save checkpoint (append-only, never overwrites)
path = tuner.save_checkpoint(
    path="/var/backups/quality-gates",
    label="phase-1-validation-complete"
)
print(f"Checkpoint saved: {path}")

# Checkpoint is immutable and audit-logged
```

## Feedback Types

| Type | Usage | Effect |
|------|-------|--------|
| `VERDICT_CORRECT` | Gate verdict was right | Increase alpha (reinforce) |
| `VERDICT_INCORRECT` | Gate verdict was wrong | Increase beta (penalize) |
| `CONFIDENCE_CALIBRATION` | Operator feedback on confidence | Balance based on signal |
| `THRESHOLD_BOUNDARY` | Feedback near threshold | Double-weight impact |

## Convergence Criteria

```
- Minimum 10 samples per gate
- KL-divergence < 1% to prior
- All gates must converge for is_converged() → True
```

## Audit Trail

Every operation creates audit events:
- ✓ Threshold updates (`tuner_events` table)
- ✓ Checkpoints (`tuner_checkpoints` table)
- ✓ Resets (`tuner_resets` table)

All events are:
- Hash-chained (cryptographic linking)
- Tenant-scoped (isolation)
- Immutable (append-only)
- Timestamped (ISO8601)

## Confidence Computation

```python
# Compute confidence for artifact
artifact = {"artifact_id": "IDEA-2024-001"}
confidence = tuner.compute_confidence(artifact)
# Returns: {"IdeaGate": 0.75, "ConceptGate": 0.82, ...}
```

Confidence factors:
- Distribution variance (lower = higher confidence)
- Sample count (more samples = higher confidence, log saturation)
- Recent KL-divergence (high divergence = lower confidence)

## Advanced Operations

```python
# Get feedback summary
summary = tuner.feedback_summary()
# Returns: {
#     "verdict_correct": 42,
#     "verdict_incorrect": 8,
#     "confidence_calibration": 5,
#     "threshold_boundary": 3
# }

# Reset a gate to uniform prior (audit-logged)
tuner.reset_to_prior(gate_name="IdeaGate")

# Export model (non-audit export)
export_path = tuner.export_model(path="/exports")
```

## Error Handling

All operations are fail-closed:

```python
try:
    tuner.update_from_feedback(feedback)
except ValueError as e:
    # Validation failed (tenant_id, confidence range, etc.)
    print(f"Invalid feedback: {e}")
except RuntimeError as e:
    # Audit chain write failed (operation rejected)
    print(f"Audit trail error: {e}")
```

## Load-Bearing Invariants

1. **Audit-First:** Every threshold update logged BEFORE state changes
2. **Tenant-Isolated:** All operations scoped to tenant_id
3. **Append-Only:** Checkpoints never overwritten
4. **Fail-Closed:** Audit failure → operation rejected
5. **Immutable:** All events frozen dataclasses

## Integration with Downstream Systems

- **Phase 3.2 (Decision History):** Tracks what decisions were made
- **Phase 3.3 (Outcome Feedback):** Closed-loop learning
- **Console UI:** Statistics, convergence status, threshold visualization
- **CLI Tools:** Export, reset, checkpoint management

## Performance Characteristics

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| `update_from_feedback` | O(1) | Constant-time Beta update |
| `compute_confidence` | O(n) | n = number of gates |
| `is_converged` | O(n) | Checks all gates |
| `get_statistics` | O(n) | Computes all gate stats |
| `save_checkpoint` | O(1) | JSON write (no GC) |
| `_compute_kl_divergence` | O(1) | Digamma approximation |

## Testing

Run test suite:

```bash
cd /home/shumway/projects/CorvinOS
pytest core/quality_gates/tests/test_learning.py -v
```

Test coverage:
- ✓ 3 test classes
- ✓ 26 test methods
- ✓ Initialization, validation, feedback processing
- ✓ Convergence detection, statistics
- ✓ Audit trail integration, cross-tenant isolation
- ✓ Checkpoint management

## Key Constants

```python
BayesianGateTuner.CONVERGENCE_KLD_THRESHOLD  # 0.01 (1%)
BayesianGateTuner.PRIOR_ALPHA                # 1.0 (uniform)
BayesianGateTuner.PRIOR_BETA                 # 1.0 (uniform)
BayesianGateTuner.MIN_SAMPLES_FOR_CONVERGENCE # 10
BayesianGateTuner.CONVERGENCE_WINDOW_SIZE    # 100
```

## Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
# DEBUG: Statistics, convergence checks
# INFO:  Threshold updates, checkpoints
# ERROR: Audit failures
```

## Files

- **Implementation:** `core/quality_gates/learning.py` (1,026 lines)
- **Tests:** `core/quality_gates/tests/test_learning.py` (545 lines)
- **Exports:** `core/quality_gates/__init__.py` (updated)
- **Summary:** `PHASE_3_1_IMPLEMENTATION_SUMMARY.md`

---

**Ready for:** Phase 3.2 (Decision History) | **ADR:** ADR-0689
