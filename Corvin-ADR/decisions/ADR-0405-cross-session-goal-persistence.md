---
id: ADR-0405
status: IMPLEMENTED
date: 2026-08-30
depends_on: [ADR-0404, ADR-0367]
relates_to: [ADR-0406, ADR-0407]
paths:
  - core/session_manager/goal_context.py (NEW: GoalContext class)
  - core/session_manager/checkpoint.py (MODIFIED: goal_context field)
  - core/orchestration/subsystems/session_manager.py (MODIFIED: initialize_task, resume_from_checkpoint)
docs:
  - docs/claude-ref/layer-16-security.md
  - docs/claude-ref/session-management.md
---

# ADR-0405: Cross-Session Goal Persistence

## Problem

When a long-running task is interrupted and resumed across session boundaries, the original task goal is lost. Without goal persistence, resumed sessions cannot validate whether the resumed work aligns with the original intent, risking context drift and invisible divergence.

**Example:** Task "Root cause the cache invalidation bug" begins, makes progress, session times out. On resume in a new session, the agent has no record of the original goal and may inadvertently shift to "optimize cache performance" — a related but distinct task.

## Decision

Persist the original task goal in `SessionCheckpoint` alongside decision history and execution state (ADR-0367). On session resume, restore the goal and validate continuity via similarity scoring before resuming work.

### Data Model

```python
@dataclass(frozen=True)
class SessionCheckpoint:
    # ... existing fields ...
    
    # ADR-0405: Goal persistence for cross-session continuity
    original_goal: Optional[str] = None          # Task goal for similarity validation
    goal_alignment_score: float = 1.0            # Last measured similarity [0.0-1.0]
```

### Lifecycle

1. **Session Start:** Agent captures task goal from user message
   - Goal stored in `ExecutionContext.original_goal`
   - Wired through `GoalAlignmentMonitor.set_goal()`

2. **At Checkpoint (every 5 turns):** Capture current goal state
   - Write goal + latest similarity score to checkpoint
   - Append-only JSONL for auditability

3. **Session Resume:** Load checkpoint and restore goal
   - Extract `original_goal` from checkpoint
   - Re-initialize `GoalAlignmentMonitor` with restored goal
   - First turn after resume: validate similarity >= 0.6 (ADR-0404 threshold)
   - If similarity < 0.6: alert and ask operator for clarification

## Rationale

- **Completeness:** Checkpoints now capture the full semantic contract (goal), not just execution state
- **Auditability:** Goal changes are logged alongside all other context snapshots (GDPR Art. 30)
- **Operator control:** Operator can review and adjust goal on resume if drift is detected
- **Non-invasive:** Goal is optional (`None` for legacy checkpoints); doesn't break existing code

## Constraints

- Goal is immutable in checkpoint (copy-on-write; mutations at next checkpoint only)
- Goal field is not encrypted (same privacy tier as decision history, both in plaintext JSON)
- Goal size capped at 4KB to prevent unbounded storage growth
- Tenant isolation enforced: `tenant_id` must match checkpoint `tenant_id` on restore

## Testing

1. **Unit:** Checkpoint serialization with goal field
   - Test round-trip: create → serialize → deserialize
   - Test default (`None`) for legacy checkpoints
   - Test 4KB size limit (reject oversized goals)

2. **Integration:**
   - Capture goal at session start
   - Write checkpoint after 5 turns
   - Verify goal persisted in checkpoint file
   - Load checkpoint in new session, verify goal restored

3. **E2E:**
   - Full task: start → interrupt → resume
   - Verify original goal recoverable from checkpoint
   - Verify similarity re-calculated on resume

## Implementation Notes

- Field added to `SessionCheckpoint` dataclass (change is backward-compatible)
- Deserialization handles missing `original_goal` key (default to `None`)
- `SessionContinuationManager` unchanged; goal is part of `context_state` dict
- Wiring: `ExecutionContext` must propagate goal to checkpoint creation

## Related

- **ADR-0404:** Goal alignment gate (similarity scoring)
- **ADR-0406:** LDD re-sync protocol (decision history validation)
- **ADR-0367:** Session checkpoint (parent mechanism)
- **ADR-0407:** Master integrating all three

---

## Amendment Log

_None yet._

