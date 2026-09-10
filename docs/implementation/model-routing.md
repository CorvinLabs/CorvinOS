# ADR-0377: Multi-Model Routing & Cost Optimizer

## Implementation Guide

### Overview

The Multi-Model Routing system estimates task complexity and assigns models (Haiku, Sonnet, Opus) per subsystem to optimize cost while maintaining accuracy.

**Key insight:** Different subsystems have different complexity profiles. A task can use multiple models simultaneously — cheap Haiku for simple analysis, expensive Opus for complex refactoring.

### Architecture

```
Task starts
  ↓
CostOptimizer.compute_routing_plan()
  ├─ Load TaskTemplate (historical data)
  ├─ Estimate task complexity
  ├─ Determine subsystem complexities
  └─ Route each subsystem to optimal model
  ↓
Execute subsystems with routed models
  ↓
CostTracker.track_subsystem_cost()
  ├─ Record actual cost
  ├─ Compare vs. estimate
  └─ Emit SubsystemCostEvent (audit trail)
  ↓
Learning loop (ADR-0314) uses events to improve estimates
```

### Core Classes

#### 1. ModelRoutingPlan

Immutable decision object (frozen dataclass):

```python
@dataclass(frozen=True)
class ModelRoutingPlan:
    task_id: str
    task_type: str
    subsystem_routes: Dict[str, str]  # e.g., {"code_analyzer": "Haiku", "refactoring_engine": "Opus"}
    estimated_total_cost: float
    confidence: float  # 0.0 to 1.0
    reasoning: Dict[str, Any]  # Per-subsystem reasoning
    created_at: datetime
```

**Usage:**
```python
# Get model for a subsystem
model = plan.get_model_for_subsystem("code_analyzer")  # "Haiku"

# Serialize to audit trail
audit_dict = plan.to_dict()
```

#### 2. CostOptimizer

Computes routing based on task complexity and operator style:

```python
optimizer = CostOptimizer(
    template_provider=lambda task_type: TaskTemplate(...),
    operator_style_provider=lambda op_id: OperatorStyle(...),
)

plan = optimizer.compute_routing_plan(
    task_id="task_123",
    task_type="code_review",
    context=ExecutionContext(...),
)
```

**Routing logic:**
1. Load task template (historical stats)
2. Estimate overall complexity (0.0 to 1.0)
3. Apply operator style bias (speed_bias, accuracy_bias)
4. For each subsystem:
   - Estimate subsystem-specific complexity
   - Compare against complexity threshold
   - Assign model (Haiku/Sonnet/Opus)
5. Sum total estimated cost
6. Return ModelRoutingPlan with confidence score

**Complexity estimation:**
- **Overall:** 60% context size + 40% template historical duration
- **Per-subsystem:**
  - `code_analyzer`: based on code lines (up to 500)
  - `refactoring_engine`: from refactoring_scope parameter
  - `test_generator`: from coverage_target parameter

**Model thresholds:**
- Complexity < threshold → Haiku (cheapest)
- Complexity < (threshold + 0.25) → Sonnet (middle)
- Complexity >= (threshold + 0.25) → Opus (best)

**Operator style effects:**
```python
# Default: complexity_threshold = 0.5
# speed_bias = 0.0 → threshold = 0.5 (balanced)
# speed_bias = 1.0 → threshold = 0.2 (aggressive on Haiku)
# accuracy_bias = 1.0 → prefer Opus
```

#### 3. CostTracker

Tracks actual cost vs. estimates:

```python
tracker = CostTracker(event_store)

event = tracker.track_subsystem_cost(
    task_id="task_123",
    subsystem="code_analyzer",
    model_used="Haiku",
    actual_cost=0.22,
    routing_plan=plan,
    success=True,
)
```

**Features:**
- Records SubsystemCostEvent (immutable, audit-ready)
- Compares actual vs. estimated cost
- Alerts on >30% variance
- Includes routing plan hash for traceability

#### 4. SubsystemCostEvent

Audit trail event:

```python
@dataclass(frozen=True)
class SubsystemCostEvent:
    task_id: str
    subsystem: str
    model_used: str
    estimated_cost: float
    actual_cost: float
    variance: float
    success: bool
    error_msg: Optional[str]
    timestamp: datetime
    routing_plan_hash: Optional[str]  # SHA256 of routing plan
```

### Integration Points

#### 1. LoopEngineer Integration

In your `LoopEngineer.run()` method, compute routing plan before executing subsystems:

```python
from core.orchestration.cost_optimizer import CostOptimizer
from core.orchestration.model_routing import ExecutionContext

class LoopEngineer:
    def __init__(self, ...):
        self.cost_optimizer = CostOptimizer(
            template_provider=self.memplace.get_task_template,
            operator_style_provider=self.memplace.get_operator_style,
        )
        self.cost_tracker = CostTracker(self.audit_backend)

    def run(self, task_id, task_type, operator_id, context_dict):
        # Prepare execution context
        context = ExecutionContext(
            task_id=task_id,
            task_type=task_type,
            operator_id=operator_id,
            code=context_dict.get("code"),
            refactoring_scope=context_dict.get("refactoring_scope"),
            coverage_target=context_dict.get("coverage_target"),
        )

        # Compute routing plan
        routing_plan = self.cost_optimizer.compute_routing_plan(
            task_id=task_id,
            task_type=task_type,
            context=context,
        )

        # Execute subsystems with routed models
        for subsystem, model in routing_plan.subsystem_routes.items():
            start_cost = self._get_model_cost_counter(model)
            
            try:
                result = self._execute_subsystem(subsystem, model, context)
                actual_cost = self._get_model_cost_counter(model) - start_cost
                
                # Track success
                self.cost_tracker.track_subsystem_cost(
                    task_id=task_id,
                    subsystem=subsystem,
                    model_used=model,
                    actual_cost=actual_cost,
                    routing_plan=routing_plan,
                    success=True,
                )
            except Exception as e:
                # Track failure
                self.cost_tracker.track_subsystem_cost(
                    task_id=task_id,
                    subsystem=subsystem,
                    model_used=model,
                    actual_cost=0.0,
                    routing_plan=routing_plan,
                    success=False,
                    error_msg=str(e),
                )
                raise
```

#### 2. Console UI (Phase 2)

Show estimated cost before task starts:

```typescript
// In chat UI, before execution:
{routing_plan && (
  <div className="cost-estimate">
    <p>Estimated cost: ${routing_plan.estimated_total_cost.toFixed(2)}</p>
    <p>Confidence: {(routing_plan.confidence * 100).toFixed(0)}%</p>
    <details>
      <summary>Per-subsystem routing</summary>
      {Object.entries(routing_plan.subsystem_routes).map(([sub, model]) => (
        <div key={sub}>{sub}: {model}</div>
      ))}
    </details>
  </div>
)}
```

### TaskTemplate Schema

Enhance your TaskTemplate to include per-subsystem cost data:

```json
{
  "task_type": "code_review",
  "duration_mean_minutes": 30,
  "duration_stddev_minutes": 15,
  "subsystems": {
    "code_analyzer": {
      "complexity_typical": 0.6,
      "model_haiku_cost": 0.25,
      "model_opus_cost": 0.65,
      "haiku_accuracy": 0.80,
      "opus_accuracy": 0.95
    },
    "refactoring_engine": {
      "complexity_typical": 0.4,
      "model_haiku_cost": 0.20,
      "model_opus_cost": 0.50,
      "haiku_accuracy": 0.90,
      "opus_accuracy": 0.98
    },
    "test_generator": {
      "complexity_typical": 0.7,
      "model_haiku_cost": 0.30,
      "model_opus_cost": 0.75,
      "haiku_accuracy": 0.75,
      "opus_accuracy": 0.92
    }
  },
  "sample_count": 100
}
```

### Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Cost savings | 30%+ vs. all-Opus baseline | Sum(actual_cost) over 100 tasks |
| Accuracy | <5% drop from all-Opus | Success rate Haiku-routed vs. Opus-only |
| Prediction quality | Estimates within 20% of actual | Variance across 100 tasks |
| Confidence improvement | >85% when sample_count >= 20 | CostOptimizer.compute_routing_plan().confidence |

### Testing

Run unit tests:
```bash
pytest core/orchestration/tests/test_cost_optimizer.py -v
```

### Next Steps (Phase 2)

1. **Memplace integration** — Load TaskTemplate + OperatorStyle from persistent store
2. **Console UI** — Show estimated cost before task
3. **Feedback loop** — Use learning events (ADR-0314) to improve estimates
4. **E2E tests** — 20-task scenario with real cost tracking
5. **Dashboard** — Visualize cost vs. estimate over time

---

**Implementation Status:** Phase 1 (Core) ✅ DONE
