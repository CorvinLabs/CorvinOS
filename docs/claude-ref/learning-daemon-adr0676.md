# ADR-0676: Phase 3 Background Learning Daemon for Creator 2.0

**Status:** ACCEPTED  
**Date:** 2026-09-17  
**References:** ADR-0314 (Learning Infrastructure), ADR-0532 (OS-Skills), ADR-0233 (Audit)  
**Implementation:** `/home/shumway/projects/CorvinOS/core/skills/os_skills/daemon/`

## Overview

The Phase 3 Background Learning Daemon enables Creator 2.0 to continuously learn from real-world skill usage, automatically regenerating skills when their weights improve significantly.

**Core Loop:**
1. **ChangeDetector** watches DataHub for source changes
2. **ExecutionListener** tracks generated skill usage (latency, errors, quality)
3. **FeedbackCollector** aggregates user ratings (1–5) with inverse-prevalence weighting
4. **CausalGraph** models Source → Skill → Outcome relationships
5. **WeightLearner** performs gradient descent on skill weights
6. **RegenerationScheduler** queues skills for regeneration when weights improve
7. **DaemonIntegration** orchestrates the event loop, checkpoint recovery, and lifecycle

## Architecture

### Components

#### 1. DataSourceChangeDetector
**Purpose:** Poll DataHub every 60s for source changes  
**Responsibilities:**
- Detect new sources (emit `SourceAddedEvent`)
- Detect updates (emit `SourceUpdatedEvent`)
- Detect deletions (emit `SourceDeletedEvent`)

**State:**
- `seen_sources: Dict[str, str]` — track content hash per source

**API:**
```python
detector.detect_changes(current_sources: Dict[str, str]) -> List[SourceChangedEvent]
```

#### 2. SkillExecutionListener
**Purpose:** Track real skill usage and performance metrics  
**Responsibilities:**
- Record when each skill runs
- Track latency, success/failure, quality (if measurable)
- Link skill to source(s) used

**State:**
- `skill_stats: Dict[str, Dict]` — per-skill: exec_count, error_count, avg_latency, quality_scores

**API:**
```python
listener.record_execution(event: SkillExecutedEvent) -> None
listener.get_skill_stats(skill_id: str) -> Optional[Dict]
```

#### 3. FeedbackCollector
**Purpose:** Aggregate user ratings and compute inverse-prevalence weights  
**Responsibilities:**
- Record 1–5 ratings from operators/users
- Classify as positive (5), negative (1), neutral (2–4)
- Compute signal distribution (% of positive/negative/neutral)
- Apply inverse-prevalence weighting (rare signals matter more)

**State:**
- `signal_distribution: Dict[str, int]` — counts per signal type

**Key Algorithm:**
```
inverse_prevalence_weight[signal] = (1 / prevalence[signal]) / sum(all_weights)
```
Example: If 70% feedback is neutral, 20% positive, 10% negative:
- negative weight = 10x (most important)
- positive weight = 5x
- neutral weight = 1.4x

**API:**
```python
collector.create_feedback_event(skill_id: str, rating: int, execution_id: str = "") -> FeedbackEvent
collector.compute_inverse_prevalence_weights() -> Dict[str, float]
```

#### 4. CausalGraph
**Purpose:** Model influence relationships: Source → Skill → Outcome  
**Responsibilities:**
- Build DAG of dependencies
- Detect cycles (reject if found)
- Compute influence scores (source→outcome paths)
- Prioritize sources by influence

**State:**
- `nodes: Dict[str, CausalNode]` — three types: source, skill, outcome
- `edges: Dict[Tuple[str, str], CausalEdge]` — dependencies with weights

**Cycle Detection:** Tarjan's algorithm (DFS with recursion stack)

**Influence Computation:** BFS, sum path weights (capped at 1.0)

**API:**
```python
graph.add_node(node_id: str, node_type: str)
graph.add_edge(from_id: str, to_id: str, edge_type: str, weight: float = 0.5)
graph.compute_influence(source_id: str) -> float
graph.validate_dag() -> bool
```

#### 5. WeightLearner
**Purpose:** Gradient descent learning with adaptive learning rates  
**Responsibilities:**
- Initialize weights (default 0.5)
- Compute gradient from loss change
- Update weights via momentum-smoothed descent
- Detect convergence, oscillation, stalling
- Adapt learning rate on oscillation

**State:**
- `weights: Dict[str, float]` — [0, 1] clamped
- `learning_rates: Dict[str, float]` — per-weight adaptive rate
- `convergence_status: Dict[str, ConvergenceStatus]` — LEARNING | CONVERGED | OSCILLATING | DISABLED | STALLED
- `momentum: Dict[str, float]` — previous delta

**Core Algorithm:**
```
gradient = (loss_after - loss_before) / weight_delta * signal_importance
momentum_term = 0.9 * prev_delta + 0.1 * gradient
delta = learning_rate * momentum_term
weight_new = clamp(weight_old - delta, 0, 1)
```

**Convergence Criteria (all must hold):**
- Last 10 deltas: average magnitude < epsilon (0.002)
- Amplitude (max - min of last 10) < 0.10
- Count >= 10 updates

**Oscillation Detection:**
- If amplitude >= 0.10 despite small magnitude, reduce LR by 10x

**Stalling:**
- If all last 10 deltas < 1e-6, mark STALLED (no improvement)

**API:**
```python
learner.update_weight(weight_name: str, loss_before: float, loss_after: float,
                      weight_delta: float, signal_importance: float = 1.0) -> WeightUpdateEvent
learner.get_convergence_status(weight_name: str) -> ConvergenceStatus
```

#### 6. RegenerationScheduler
**Purpose:** Queue skills for regeneration when weights improve  
**Responsibilities:**
- Detect significant loss improvements (> threshold, default 0.05)
- Batch regenerations (up to `batch_size` per call)
- Prioritize by loss_delta and influence
- Prevent duplicate queue entries

**State:**
- `queue: List[RegenerationQueueItem]` — pending regen
- `processed: List[RegenerationQueueItem]` — completed regen history
- `skill_last_regen: Dict[str, str]` — timestamp of last regen per skill

**Reason Types:**
- `WEIGHT_CHANGE` — loss improved significantly
- `NEW_SOURCE` — new data source available
- `ERROR_RECOVERY` — skill had too many errors
- `PRIORITY_BOOST` — manually prioritized

**API:**
```python
scheduler.should_regenerate(skill_id: str, loss_delta: float) -> bool
scheduler.queue_regeneration(skill_id: str, reason: str, priority: float = 0.5,
                             loss_delta: Optional[float] = None) -> Optional[RegenerationQueueItem]
scheduler.get_next_batch() -> List[RegenerationQueueItem]
```

#### 7. DaemonIntegration
**Purpose:** Coordinate all components and manage lifecycle  
**Responsibilities:**
- Orchestrate 60s event loop
- Load/save checkpoints (every 60 ticks ≈ 1 hour)
- Handle source change events
- Dispatch skill execution and feedback to learner
- Manage daemon start/stop/bootstrap (recovery)

**State:**
- `tick_count: int` — event loop iteration number
- `is_running: bool` — daemon status
- All sub-components (detector, listener, collector, graph, learner, scheduler)

**Checkpoint Format (JSON):**
```json
{
  "timestamp": "2026-09-17T12:34:56.789Z",
  "tick_count": 3600,
  "weights": {"skill_001": 0.65, ...},
  "convergence_statuses": {"skill_001": "converged", ...},
  "learning_rates": {"skill_001": 0.005, ...},
  "feedback_count": 1250,
  "weight_updates_count": 450,
  "skills_regenerated": 12,
  "checksum": "sha256_hash_of_json"
}
```

**API:**
```python
daemon.start() -> None
daemon.stop() -> None
daemon.process_event_tick() -> Dict
daemon.on_source_changed(event: SourceChangedEvent) -> None
daemon.on_skill_executed(event: SkillExecutedEvent) -> None
daemon.on_feedback(event: FeedbackEvent) -> None
daemon.get_next_regen_batch() -> List[RegenerationQueueItem]
```

## Event Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│ Event Loop (60s tick)                                               │
└─────────────────────────────────────────────────────────────────────┘
                                 ↓
        ┌──────────────────────────────────────┐
        │ Phase 1: Source Detection            │
        │ ChangeDetector.detect_changes()      │
        │ → SourceChangedEvent (add/upd/del)  │
        └──────────────────────────────────────┘
                        ↓
        ┌──────────────────────────────────────┐
        │ Phase 2: Skill Execution             │
        │ ExecutionListener.record_execution() │
        │ → SkillExecutedEvent                 │
        │   (latency, quality, errors)         │
        └──────────────────────────────────────┘
                        ↓
        ┌──────────────────────────────────────┐
        │ Phase 3: User Feedback               │
        │ FeedbackCollector.record_feedback()  │
        │ → FeedbackEvent (1–5 rating)         │
        └──────────────────────────────────────┘
                        ↓
        ┌──────────────────────────────────────┐
        │ Phase 4: Causal Graph Update         │
        │ Add nodes for new sources/skills     │
        │ Add edges for dependencies           │
        │ Validate DAG (no cycles)             │
        └──────────────────────────────────────┘
                        ↓
        ┌──────────────────────────────────────┐
        │ Phase 5: Weight Learning             │
        │ WeightLearner.update_weight()        │
        │ → gradient descent → delta           │
        │ → convergence check → adapt LR       │
        └──────────────────────────────────────┘
                        ↓
        ┌──────────────────────────────────────┐
        │ Phase 6: Regeneration Queue          │
        │ if loss_delta > threshold:           │
        │   RegenerationScheduler.queue()      │
        │ → sorted by priority (highest first) │
        └──────────────────────────────────────┘
                        ↓
        ┌──────────────────────────────────────┐
        │ Phase 7: Checkpointing (every 60t)   │
        │ Save state to JSON + SHA256 hash     │
        │ on crash, recover from checkpoint    │
        └──────────────────────────────────────┘
```

## Audit Integration

**All events are immutable and hash-chained.**

Events emitted:
1. `daemon_started` — timestamp, configuration
2. `learning_iteration_complete` — iteration_num, weights_delta, loss_delta
3. `skill_queued_for_regeneration` — skill_id, loss_improvement, reason
4. `daemon_checkpoint` — state_hash, weights, last_update_time

Compliance constraints (GDPR Art. 30, 32):
- No PII in feedback (comments excluded)
- All events tenant-scoped
- Hash-chain verified at boot (ADR-0232 tripwire)

## Performance Characteristics

| Metric | Target | Achieved |
|--------|--------|----------|
| Feedback processing latency | <1ms per event | ~0.5ms |
| Convergence time | <500 feedback samples | ~200 samples typical |
| Memory usage | Constant (no leaks) | Verified @ 1000+ events |
| Max feedback per iteration | 100+ | 150+ tested |
| Daemon tick frequency | 60s | Configurable |
| Checkpoint retention | 30 days | Via `retention_days` |

## Testing (93+ test cases)

Total test coverage across 9 test files:
- `test_weight_learner.py`: 12 tests (gradient, convergence, oscillation, momentum, importance)
- `test_causal_graph.py`: 10 tests (DAG, influence, cycle detection)
- `test_feedback_collector.py`: 10 tests (inverse-prevalence, ratings, signals)
- `test_daemon_integration.py`: 11 tests (lifecycle, checkpoint, state)
- `test_execution_listener.py`: 5 tests (tracking, stats)
- `test_change_detector.py`: 7 tests (add/update/delete)
- `test_regeneration_scheduler.py`: 8 tests (queuing, batching, priority)
- `test_phase3_gate.py`: 9 tests (8 ADR-0676 gates + E2E)
- `test_audit_integration_e2e.py`: 21 tests (audit, E2E wiring, adversarial, performance)

**E2E Wiring Proof:**
- Full loop tested: Source → Execution → Feedback → Learning → Regen Queue
- Checkpoint recovery tested
- Causal graph influence tested
- All components reachable end-to-end

**Adversarial Tests:**
- Circular graph rejection
- Missing feedback handling
- Concurrent feedback merging
- High-amplitude oscillation + LR adaptation
- Skill execution errors
- Massive feedback volume (150+ signals)
- Corrupted checkpoint rejection
- Very high learning rate (capped)
- Division by zero handling

**Performance Tests:**
- Feedback latency verified
- Long-run (1000+ events) memory stability verified

## Usage Example

```python
from daemon.integration import DaemonIntegration
from daemon.execution_listener import SkillExecutedEvent
from daemon.feedback_collector import FeedbackEvent
from daemon.change_detector import SourceChangedEvent

# Initialize daemon
daemon = DaemonIntegration(corvin_home="~/.corvin")
daemon.start()

# When DataHub detects new source
source_event = SourceChangedEvent(
    source_id="memory:tier2",
    change_type="added",
    new_hash="abc123"
)
daemon.on_source_changed(source_event)

# When Creator 2.0 generates a skill
# (done by creator, not daemon)

# When the skill executes
exec_event = SkillExecutedEvent(
    skill_id="creator_skill_001",
    source_id="memory:tier2",
    success=True,
    duration_ms=245.5,
    outcome_quality=0.82
)
daemon.on_skill_executed(exec_event)

# When user provides feedback
feedback_event = FeedbackEvent(
    skill_id="creator_skill_001",
    rating=5,
    signal_type="positive"
)
daemon.on_feedback(feedback_event)

# Every 60 ticks (~1 hour), checkpoint is saved
# On crash, daemon recovers via bootstrap()

# Get next batch for regeneration
batch = daemon.get_next_regen_batch()
for item in batch:
    print(f"Regenerate {item.skill_id} (priority={item.priority})")

# Shutdown
daemon.stop()
```

## Known Limitations

1. **No real-time regeneration:** Daemon is event-driven but regenerations happen in batches (configurable)
2. **Synthetic loss calculation:** In integration.py, loss is currently mocked (loss = 1 - rating/5); real implementation would use actual metric deltas
3. **No cross-skill dependencies:** Graph models source→skill→outcome, not skill→skill dependencies
4. **No exploration:** Learning is purely exploitation (no epsilon-greedy or other exploration)

## Future Extensions (Phase 4+)

1. **Skill composition:** Learn weights for skill combination chains
2. **Adaptive epsilon:** Introduce exploration when convergence plateaus
3. **Multi-objective learning:** Balance quality, latency, cost
4. **Federated learning:** Aggregate learnings across Creator instances
5. **Real-time regeneration:** Stream regenerations rather than batching

## References

- ADR-0314: Learning Infrastructure (event schema, persistence)
- ADR-0233: Plugin audit integration (event backend)
- ADR-0232: Boot tripwire (hash-chain verification)
- ADR-0676 Implementation Plan: `/home/shumway/projects/Corvin-ADR/implementation/ADR-0676-IMPLEMENTATION-PLAN.md`
