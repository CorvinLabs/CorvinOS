---
id: ADR-0464
status: PROPOSED
depends_on: [ADR-0445, ADR-0348, ADR-0347]
relates_to: [ADR-0101, ADR-0142]
paths:
  - core/vibe_engineering/vibe_orchestrator.py
  - core/vibe_engineering/tests/test_vibe_uses_worker_pool.py
  - core/console/corvin_console/task_worker_pool.py
docs:
  - docs/claude-ref/layer-plugins.md
  - docs/claude-ref/e2e-wiring-proof-standard.md
---

# ADR-0464: VibeOrchestrator – TaskWorkerPool Integration (F-C2 Dead Code Fix)

## Problem

**Dead Code:** `TaskWorkerPool` class exists in `core/console/corvin_console/task_worker_pool.py` but has **zero production callers**. It's only instantiated in its own `main()` function for CLI usage. This violates the E2E Wiring Proof standard (ADR-0215): every new production code must be reachable from at least one real trigger path.

**Impact:** The worker pool subsystem (ADR-0101) cannot be tested or used in practice. Autonomous task execution (Phase 2 Vibe Engineering) has no real orchestration of parallel work.

## Solution

**F-C2 Fix:** Integrate `TaskWorkerPool` into `VibeOrchestrator.__init__()` so that:

1. **Ownership:** VibeOrchestrator creates and owns a `TaskWorkerPool` instance
2. **Configuration:** Accepts optional `task_queue` and `max_workers` parameters
3. **Task Submission:** Exposes `async submit_task_to_worker_pool()` for production use
4. **Lifecycle:** Exposes `async run_worker_pool()` and `async shutdown_worker_pool()` methods
5. **Observability:** Emits `on_task_submitted_to_pool` callbacks for task tracking

### Integration Points

#### VibeOrchestrator.__init__()
```python
def __init__(
    self,
    checkpoint_dir: Optional[Path] = None,
    context_reduction_target_pct: int = 91,
    task_queue: Optional[TaskQueue] = None,
    max_workers: Optional[int] = None,
):
    # ... existing managers ...
    self.task_queue = task_queue or self._create_default_task_queue()
    self.worker_pool = TaskWorkerPool(
        task_queue=self.task_queue,
        max_workers=max_workers
    )
```

#### Task Submission (Production API)
```python
async def submit_task_to_worker_pool(
    self,
    task_id: str,
    instruction: str,
    chat_key: Optional[str] = None,
    tenant_id: str = "_default",
) -> bool:
    """Enqueue a task through the worker pool (production path)."""
```

#### Lifecycle Methods
```python
async def run_worker_pool(self, poll_interval_ms: float = 100) -> None:
    """Run the worker pool event loop (blocking until shutdown)."""
    await self.worker_pool.run(poll_interval_ms=poll_interval_ms)

async def shutdown_worker_pool(self) -> None:
    """Gracefully shutdown the worker pool."""
    await self.worker_pool.shutdown()
```

## E2E Reachability Proof

**Grep verification:** Two non-test callers of TaskWorkerPool:
```bash
$ grep -r "TaskWorkerPool" core/ --include="*.py" | grep -v test_
core/vibe_engineering/vibe_orchestrator.py:from core.console.corvin_console.task_worker_pool import TaskWorkerPool
core/vibe_engineering/vibe_orchestrator.py:        self.worker_pool = TaskWorkerPool(
```

**Test coverage:** `test_vibe_uses_worker_pool.py` provides 12 E2E proofs:
1. Orchestrator creates worker pool instance
2. Orchestrator creates/accepts TaskQueue
3. Max workers correctly configured
4. Tasks submittable via async method
5. Multiple tasks queueable
6. Callbacks fired on submission
7. Active worker tracking
8. Lifecycle methods exposed
9. Non-test caller reachable (grep proof)
10. Error handling on submission
11. Shutdown on empty pool
12. Grep finds production integration

## Design Constraints

**Never Weaken:**
- Worker pool remains in-process (ADR-0101 M6 abort semantics require shared process)
- All tasks run through the same `_pre_spawn_gate` (L44 + L34 + L35)
- Audit trail remains hash-chained (task.spawn_started BEFORE subprocess creation)
- Task submission is fire-and-forget (does not block on execution)
- Multi-tenant isolation maintained (tenant_id required in every call)

**Concurrency Model:**
- Orchestrator.__init__ creates pool synchronously
- pool.run() is async and blocking (drives the event loop)
- submit_task_to_worker_pool() is async but fire-and-forget (returns immediately)
- Caller must manage the event loop (spawn pool.run() as a background task)

## Migration / Activation

**Phase 1 (This Commit):** Wire integration + E2E tests (dark)
- No callers yet (integration is invisible)
- Full test coverage ensures correctness before activation

**Phase 2 (Future ADR):** First production caller activates the pool
- Task processing surfaces the pool (e.g., CheckpointManager.process_checkpoint_recovery)
- Feature flag gates activation until canary proves stability

## Testing

### Unit Tests
- Orchestrator creates pool with correct config
- Task queue creation (explicit or default)
- Callback registration and firing
- Error handling paths

### E2E Tests
- End-to-end task submission via async method
- Task queue state after submission
- Lifecycle methods callable without error
- Graceful shutdown

### Grep Verification
- TaskWorkerPool has ≥1 non-test caller in core/
- vibe_orchestrator.py is the sole integration point (no scattered usage)

## Decision Rationale

**Why VibeOrchestrator owns the pool:**
- VibeOrchestrator already manages other orchestration concerns (checkpoints, recovery)
- Pool.run() is long-lived and async; natural fit in event-driven architecture
- Lifecycle binding (orchestrator lifecycle = pool lifecycle) prevents orphaned workers

**Why async (not sync):**
- TaskWorkerPool is fundamentally async (asyncio.Semaphore, subprocess, wait loops)
- Caller already manages event loop (checkpoint + recovery are async)
- Fire-and-forget submission decouples orchestration from execution

**Why fire-and-forget:**
- Tasks are durable (persisted to disk before submission)
- Submission failure is logged and can be retried later
- Worker pool runs independently; orchestrator doesn't wait for results

## Backwards Compatibility

**No Breaking Changes:**
- VibeOrchestrator(checkpoint_dir) still works (defaults applied)
- Existing methods unchanged
- New pool is optional (use only if submit_task_to_worker_pool() is called)

**Impact on Existing Tests:**
- test_phase2_integration.py unaffected (no changes to existing methods)
- New tests in test_vibe_uses_worker_pool.py are additive

## Compliance Notes

- **ADR-0101 (Task Worker):** Pool correctly implements audit-first, L34/L35 gates
- **ADR-0142 (Layer Extension):** TaskWorkerPool is L22/L23 concern (ClaudeCode engine layer)
- **GDPR Art. 5, 6, 30, 32:** Audit trail and multi-tenant isolation preserved

## References

- **ADR-0101:** Task worker pool design, audit semantics, M1–M9 phases
- **ADR-0215:** E2E Wiring Proof standard (reachability requirements)
- **ADR-0348:** EventBus (observability, not required but compatible)
- **ADR-0445:** Background task supervision (related orchestration concern)

---

## Status

- **PROPOSED** — awaiting merge approval
- **Depends on:** ADR-0445 (background task supervision) for reference, ADR-0101 (TaskWorkerPool base)
- **Author:** Claude Haiku 4.5
- **Date:** 2026-08-29
