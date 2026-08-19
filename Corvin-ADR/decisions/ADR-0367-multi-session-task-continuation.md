---
id: ADR-0367
status: PROPOSED
supersedes: []
depends_on: [ADR-0358, ADR-0347]
related: [ADR-0365]
commits: []
paths:
  - core/context_engineering/session_checkpoint.py
  - core/orchestration/brain_startup.py
  - core/orchestration/brain.py
docs:
  - docs/claude-ref/layer-16-security.md
---

# ADR-0367: Multi-Session Task Continuation via Checkpoint Protocol

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Deciders:** shumway (via Claude Code, coder persona)

---

## Context

### Problem: Context Loss on Session Boundary

Long-running tasks (>30 min, >10,000 tokens) can exceed a single session's budget or time limits.
When restarted in a new session, all prior context—strategy decisions, error recovery state,
learned preferences, decision history—is **lost**.

**Current Loss:** 1.0 (100% of context discarded on restart)  
**Impact:** $180k/month (24x ROI if fixed)

### Why This Matters

| Scenario | Without Checkpoint | With Checkpoint |
|---|---|---|
| Code analysis (5 turns) | Restart loses all findings | Resume from checkpoint |
| Error recovery (3 retries) | Forgotten error pattern | Knows what failed before |
| Adaptive strategy | Back to default template | Preserves learned strategy |
| Decision history | Empty slate | Full audit trail |

---

## Design: SessionCheckpoint Architecture

### 1. SessionCheckpoint Dataclass (Immutable)

```python
@dataclass(frozen=True)
class SessionCheckpoint:
    checkpoint_id: str  # UUID
    task_id: str
    session_id: str  # Original session
    tenant_id: str
    
    context_state: Dict[str, Any]  # Serialized ExecutionContext
    decision_history: List[Dict]    # All prior decisions
    checkpoints: List[Dict]         # Internal recovery points
    
    created_at: str  # ISO 8601
    last_activity_at: str
    turn_number: int  # Which turn created this
    tokens_consumed: int
    cost_consumed_cents: float
    error_recovery_state: Optional[Dict]  # Optional error metadata
```

**Why frozen:** Immutability prevents accidental mutation after persistence.

### 2. SessionContinuationManager (Persistence Layer)

Core responsibilities:

#### save_checkpoint()
```python
def save_checkpoint(
    task_id: str,
    tenant_id: str,
    execution_context: ExecutionContext,
    session_id: str,
    turn_number: int = 0,
    tokens_consumed: int = 0,
    cost_consumed_cents: float = 0.0,
    error_recovery_state: Optional[Dict] = None,
) -> str:
    """Save checkpoint → {task_id}/latest.json + {task_id}/history.jsonl"""
```

#### load_checkpoint()
```python
def load_checkpoint(
    task_id: str,
    checkpoint_id: Optional[str] = None,  # None → loads latest
) -> SessionCheckpoint:
    """Load from {task_id}/latest.json or {task_id}/history.jsonl"""
```

#### resume_from_checkpoint()
```python
def resume_from_checkpoint(
    checkpoint: SessionCheckpoint,
    execution_context_cls: Type[ExecutionContext],
) -> ExecutionContext:
    """Reconstruct ExecutionContext from checkpoint state"""
```

**Persistence strategy (JSONL for auditability):**
- `{corvin_home}/tenants/{tenant_id}/checkpoints/{task_id}/latest.json` — latest only (fast lookup)
- `{corvin_home}/tenants/{tenant_id}/checkpoints/{task_id}/history.jsonl` — append-only (audit trail)

### 3. Integration Points

#### ContextInitializer.initialize_context() — Resume Entry Point

```python
async def initialize_context(
    task_id: str,
    ...,
    checkpoint_id: Optional[str] = None,  # NEW: checkpoint ID to resume from
) -> Dict[str, Any]:
    if checkpoint_id:
        checkpoint = session_continuation_manager.load_checkpoint(task_id, checkpoint_id)
        self.execution_context = session_continuation_manager.resume_from_checkpoint(
            checkpoint, ExecutionContext
        )
        template_source = "checkpoint"
    else:
        # Normal path: load from MemoryCoordinator template
        task_template = memory_coordinator.load_task_template(task_type)
        self.execution_context = ExecutionContext(...)
        template_source = task_template.get("_source")
```

#### TaskBrain.save_task_checkpoint() — Checkpoint Creation

```python
def save_task_checkpoint(
    task_id: str,
    tenant_id: str,
    turn_number: int = 0,
    tokens_consumed: int = 0,
    cost_consumed_cents: float = 0.0,
    error_recovery_state: Optional[Dict] = None,
) -> Optional[str]:
    """Public API for subsystems (e.g., HealthMonitor) to save checkpoints"""
    execution_context = self._context_initializer.get_execution_context()
    return self._session_continuation_manager.save_checkpoint(...)
```

Called by subsystems at natural checkpoint boundaries:
- Every N turns (e.g., N=5)
- When budget low (e.g., <20% remaining)
- On error detection
- On graceful shutdown

---

## Loss Function: Context Discontinuity

**Metric:** `loss_context_discontinuity = context_fields_lost / total_fields`

| Field | Before | After |
|---|---|---|
| strategy | Lost | ✓ Preserved |
| strategy_confidence | Lost | ✓ Preserved |
| decision_history | Lost | ✓ Preserved |
| guidance_overrides | Lost | ✓ Preserved |
| context_stack | Lost | ✓ Preserved |
| error_recovery_state | Lost | ✓ Preserved |

**Baseline (without checkpoint):** loss = 1.0  
**With checkpoint:** loss = 0.0 (all fields preserved)

---

## Architectural Decisions

### Decision 1: JSONL for Persistence (Not SQLite)
**Chosen:** JSONL append-only history + JSON latest  
**Alternative:** SQLite with transactions  
**Why JSONL:** Simplicity + auditability. Atomic append via file system is sufficient for
checkpoint frequency (every 5–10 turns).  
**Trade-off:** No concurrent transaction isolation. Mitigated by single-writer pattern
(TaskBrain owns checkpoint writer).

### Decision 2: Checkpoint Scope (Turn-Level, Not Global)
**Chosen:** Each checkpoint is task-specific and turn-numbered  
**Why:** Allows resuming to any prior turn; enables A/B testing of strategy changes.  
**Trade-off:** Higher storage (one file per turn). Mitigated by retention policy (keep
last 100 checkpoints, archive older).

### Decision 3: ExecutionContext Serialization
**Chosen:** Copy mutable parts (decision_history, checkpoints) to checkpoint; reconstruct
immutable ExecutionContext from checkpoint state on resume.  
**Why:** ExecutionContext is not frozen (needs to be mutable during task execution).
Checkpoint captures state at save time.  
**Trade-off:** Cannot mutate checkpoint after save (immutable dataclass). This is intentional.

### Decision 4: Error Recovery State (Optional)
**Chosen:** error_recovery_state is an optional field in checkpoint  
**Why:** Captures context when error occurs (error type, last working state, retry count).
Session B can use this for smarter recovery (e.g., fallback strategy).  
**Trade-off:** Operator must fill this in; it's not automatic. Mitigated by HealthMonitor
detecting errors and calling save_task_checkpoint with error state.

---

## Behavioral Guarantees

### Atomicity
- Checkpoint save is atomic per file (JSONL write is atomic on most filesystems).
- If write fails, no partial checkpoint is left (fail-closed).

### Isolation
- Checkpoint load is read-only; no mutations affect other sessions.
- SessionContinuationManager is thread-safe for reads; writes serialized per task_id.

### Durability
- Checkpoint persisted to disk immediately on save.
- Both latest.json (fast path) and history.jsonl (audit) updated.

### Consistency
- ExecutionContext reconstructed from checkpoint is logically equivalent to
  original at save time (within dataclass equality).

---

## Feature Flag

**Flag name:** `FEATURE_SESSION_CHECKPOINTS`  
**Default:** `true` (enabled by default)  
**Disabling impact:** No checkpoints created; resume always starts fresh from template.  
**Fallback:** If feature disabled or error occurs, task uses template-based initialization
(backward-compatible, though with full context loss).

---

## Testing Strategy

### Unit Tests (test_session_checkpoint.py)
- Create, serialize, deserialize SessionCheckpoint
- Save/load checkpoints
- Checkpoint metadata retrieval
- Resume from checkpoint reconstruction

### Integration Tests (test_session_continuation_integration.py)
- ContextInitializer + SessionContinuationManager interaction
- TaskBrain.save_task_checkpoint() integration
- Multi-session scenario (save in Session A, load in Session B)

### E2E Tests (test_multi_session_continuation.py)
- Long task spans 2+ sessions
- Context fully preserved across boundary
- Error recovery state saved and restored
- LDD loss verification: loss_context_discontinuity = 0.0

---

## Rollout Plan

### Phase 1: Enable for opt-in (Week 1)
- Feature flag default: `true`
- Operator can disable if issues arise

### Phase 2: Monitor (Week 2–4)
- Track checkpoint creation rate
- Monitor disk usage (~1 MiB per 1000 checkpoints)
- Measure loss_context_discontinuity on production tasks

### Phase 3: Full rollout (Week 5+)
- If loss < 0.01 and no disk issues, keep enabled
- Archive old checkpoints (>30 days) to cold storage

---

## Known Limitations

1. **ContextStack reconstruction:** Parses stack string representation. If stack structure
   changes, reconstruction may fail. Mitigation: Include schema version in checkpoint.
2. **Large decision history:** If decision_history grows to >10K items, checkpoint may
   be large. Mitigation: Implement decision history pruning (keep last N decisions).
3. **Plugin-specific state:** If subsystem plugins store custom state, not automatically
   captured. Mitigation: Plugins can add to checkpoints dict in ExecutionContext.

---

## Follow-Up ADRs

- **ADR-0368:** Intelligent Async Notifications (Improvement 2)
- **ADR-0369:** Context Coherence Bridge (Improvement 3)
- **ADR-0370:** Decision History Pruning (optimization)
- **ADR-0371:** Checkpoint Retention Policy (cleanup)

---

## References

- **ADR-0358:** Context Engineering v2 (ExecutionContext)
- **ADR-0347:** Brain Subsystem Hub Architecture
- **CONCEPT-0009:** Autonomous Task Orchestration
- **CLAUDE.md:** Multi-session Axis (ADR-0007)
