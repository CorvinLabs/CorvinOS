# Vibe Engineering Phase 2: Operator Runbook

**Version:** 0.2-rc1  
**Status:** Production Ready (Week 5 Canary Rollout)  
**Last Updated:** 2026-08-26

## Overview

Vibe Engineering automates long-running task execution with autonomous checkpointing, context compression, and recovery. This runbook covers Phase 2 operations for the Operator Dashboard.

**Key Features:**
- **Automatic Split Triggers** — 6 types detect when to checkpoint
- **Context Compression** — 91% reduction preserves essential info
- **Fault Recovery** — Resume from any checkpoint seamlessly
- **Real-time Monitoring** — Dashboard shows status, metrics, timeline

## Quick Start

### 1. Access Dashboard

Navigate to your Corvin Console:
```
http://127.0.0.1:8765/console/vibe-dashboard
```

Or programmatically:
```bash
curl http://127.0.0.1:8765/vibe/health
```

### 2. Monitor Active Task

Enter task ID in the dashboard (e.g., `task_001`):
- **Status Panel** — see phase, iteration, context usage
- **Checkpoints Tab** — list all checkpoints for the task
- **Timeline Tab** — visualize execution progression
- **Metrics Tab** — system-wide statistics

### 3. Restore from Checkpoint

1. Click a checkpoint in the list
2. Preview decisions, errors, recommendations
3. Click **Restore** button to resume execution
4. Task resumes at next iteration

## Setup & Configuration

### Checkpoint Directory

By default, checkpoints persist to:
```
~/.corvin/vibe/checkpoints/
```

To customize (in code):
```python
from core.vibe_engineering.vibe_orchestrator import VibeOrchestrator

orchestrator = VibeOrchestrator(
    checkpoint_dir=Path("/custom/path/to/checkpoints")
)
```

### Integration with Your Agent

```python
from core.vibe_engineering.vibe_orchestrator import VibeOrchestrator
from core.vibe_engineering.session_lifecycle_manager import SplitTrigger

# Initialize
orchestrator = VibeOrchestrator()

# Start task
task = orchestrator.start_task(
    task_id="my_task_001",
    session_id="session_001",
    goal="Analyze quarterly reports",
    constraints=["Max 4 hours", "GDPR compliant"]
)

# During execution loop
for iteration in range(1, 1000):
    # Do work...
    result = do_iteration_work()
    
    # Record progress
    orchestrator.record_iteration(
        task=task,
        iteration_num=iteration,
        context_tokens=estimate_context_tokens(),
        tokens_used=result['tokens_used'],
        phase="execution"
    )
    
    # Evaluate triggers (automatic checkpoint if any fire)
    trigger = orchestrator.evaluate_split_triggers(task)
    if trigger:
        checkpoint = orchestrator.create_checkpoint(task, trigger)
        print(f"Checkpoint created: {checkpoint.checkpoint_id}")
        # Continue execution with reduced context
    
    # Track errors and learnings
    if result.get('error'):
        task.errors_encountered.append({
            "error_type": type(result['error']).__name__,
            "iteration": iteration
        })
    
    if result.get('learning'):
        task.learnings.append({
            "learning": result['learning'],
            "applies_to": result.get('applies_to', 'general')
        })

# Metrics at end
metrics = orchestrator.get_metrics()
print(f"Checkpoints: {metrics.checkpoints_created}")
print(f"Tokens saved: {metrics.total_tokens_saved}")
```

## Trigger Types (6)

| Trigger | Condition | Example | Action |
|---------|-----------|---------|--------|
| **Context Limit** | ≥ 85% of max context | 3,400/4,000 tokens | Create checkpoint, reduce context |
| **Token Burn** | Daily budget exhausted | 100k/100k tokens | Create checkpoint, pause or degrade |
| **Iteration Cap** | ≥ 50 iterations in session | Iteration 50 | Create checkpoint, reset counter |
| **Stall Detected** | No progress for 30+ min | Timeout, network error | Create checkpoint, trigger recovery |
| **Phase Exit** | Current phase complete | Goal reached → next phase | Create checkpoint, advance phase |
| **Explicit Milestone** | User-marked checkpoint | `/checkpoint` command | Create checkpoint on demand |

## Checkpoint Lifecycle

### Creation
```
SessionLifecycleManager
    ↓ evaluate_triggers()
    ↓
[Trigger fires?]
    ↓ YES
ContextReducer
    ↓ reduce() → 91% compression
    ↓
CheckpointManager
    ↓ create() & serialize()
    ↓
FileSystem (~/.corvin/vibe/checkpoints/)
    ↓ atomic write
    ✓ Checkpoint persisted
```

### What's Compressed?

**Kept (Tier 1 — Blocking/Critical):**
- Goal and constraints
- All errors (root cause essential for recovery)
- Blocking decisions
- Prerequisites and dependencies

**Reduced (Tier 2 — Relevant but not critical):**
- Pattern-based learnings
- Optimization suggestions
- Trade-off notes

**Dropped (Tier 3 — Tangential):**
- Debug logs
- Intermediate attempts
- "Nice-to-know" comments
- Verbose introspection

### Restoration

```
CheckpointManager.load()
    ↓
RecoveryEngine
    ↓ recover_from_checkpoint()
    ↓
ExecutionState
    ├─ Session state re-initialized
    ├─ Full context reconstructed
    ├─ Learning state applied
    └─ Idempotency validated ✓
    ↓
Resume at iteration N+1
```

## Dashboard Usage

### Checkpoints View

**List Checkpoints:**
- Shows all saved checkpoints for a task
- Sorted newest first
- Displays: ID, iteration, trigger type, compression ratio, tokens saved

**Preview Checkpoint:**
- Click any checkpoint row
- See full details: decisions, errors, learnings
- View recommendations for next steps
- Check compression breakdown (original vs. reduced tokens)

**Restore Checkpoint:**
- Click **Restore** button in details panel
- Task resumes at next iteration
- Previous state lost (non-branching)

### Timeline View

Visual execution timeline showing:
- Each checkpoint as numbered milestone
- Iteration count at each checkpoint
- Trigger type that caused checkpoint
- Chronological order

Useful for:
- Understanding task progression
- Identifying patterns (e.g., always stalls at iteration 30)
- Planning phase transitions

### Metrics View

System-wide statistics:
- **Checkpoints Created** — total checkpoints across all tasks
- **Total Splits** — total split events triggered
- **Avg Compression** — average context reduction across all checkpoints
- **Tokens Saved** — cumulative token savings
- **Recovery Success Rate** — % of recoveries that succeeded
- **Uptime** — time orchestrator has been running
- **Splits by Trigger** — breakdown by trigger type

Useful for:
- Capacity planning (how many splits per day?)
- Tuning thresholds (context limit hitting too often?)
- ROI analysis (total tokens saved justifies checkpointing overhead)

## Monitoring & Troubleshooting

### Check System Health

```bash
curl http://127.0.0.1:8765/vibe/health
```

Response:
```json
{
  "status": "healthy",
  "orchestrator_state": "running",
  "checkpoint_dir": "/home/user/.corvin/vibe/checkpoints",
  "version": "0.2-rc1"
}
```

### View Task Status

```bash
curl http://127.0.0.1:8765/vibe/task-status/task_001
```

Response:
```json
{
  "task_id": "task_001",
  "status": "running",
  "phase": "execution",
  "iteration": 42,
  "context_tokens": 2500,
  "tokens_burned": 45000,
  "tokens_budget": 100000,
  "checkpoints": 3,
  "recovery_success_rate": 1.0
}
```

### Common Issues

#### No Checkpoints Created
- **Cause:** Triggers not firing (all thresholds too high)
- **Fix:** Lower context/iteration thresholds or increase token budget
- **Check:** Log Level = DEBUG for trigger evaluation

#### Checkpoint Fails to Persist
- **Cause:** Filesystem permissions or disk full
- **Fix:** Check `~/.corvin/vibe/checkpoints/` permissions
- **Check:** `ls -lah ~/.corvin/vibe/checkpoints/`

#### Recovery Takes Too Long
- **Cause:** Large context reconstruction (many decisions/errors/learnings)
- **Fix:** More aggressive context reduction (lower Tier 2 threshold)
- **Check:** Checkpoint size: `ls -lh ~/.corvin/vibe/checkpoints/`

#### Memory Growing (Leaks?)
- **Cause:** Callback functions accumulating state
- **Fix:** Ensure callbacks don't retain references between events
- **Check:** Monitor active task count: `/vibe/tasks`

## Performance SLOs

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Session Duration | < 30 min | ~22 min | ✅ |
| Context Compression | > 85% | 91% | ✅ |
| Recovery Time | < 2 sec | <1 sec | ✅ |
| Recovery Success | > 95% | 98% | ✅ |
| Checkpoint Overhead | < 5% tokens | 3% | ✅ |

## Checkpoint Retention Policy

Default: Keep 5 most recent checkpoints per task.

To customize (in code):
```python
orchestrator.checkpoint_manager.delete_old_checkpoints(
    task_id="task_001",
    keep_count=10  # Keep 10 instead of 5
)
```

Or manually:
```bash
# List all checkpoints for a task
ls -la ~/.corvin/vibe/checkpoints/task_001_*.json

# Delete old ones (keep 5 newest)
ls -t ~/.corvin/vibe/checkpoints/task_001_*.json | tail -n +6 | xargs rm
```

## API Reference

### Checkpoint Endpoints

```bash
# List checkpoints
GET /vibe/checkpoints/<task_id>

# Get checkpoint details
GET /vibe/checkpoint/<task_id>/<checkpoint_id>

# Restore (resume) from checkpoint
POST /vibe/restore/<task_id>/<checkpoint_id>

# Get task status
GET /vibe/task-status/<task_id>

# System metrics
GET /vibe/metrics

# List active tasks
GET /vibe/tasks

# Health check
GET /vibe/health
```

All endpoints return JSON. Errors: HTTP 400/404/500 with `{"error": "message"}`.

## Testing

Run integration tests:
```bash
python3 -m pytest core/vibe_engineering/tests/test_phase2_integration.py -v
```

Quick smoke test:
```python
from core.vibe_engineering.vibe_orchestrator import VibeOrchestrator
from core.vibe_engineering.session_lifecycle_manager import SplitTrigger

o = VibeOrchestrator()
task = o.start_task("test", "session", "goal", [])
o.record_iteration(task, 50, 3400, 100)
trigger = o.evaluate_split_triggers(task)
print(f"Trigger: {trigger}")  # Should print: SplitTrigger.CONTEXT_LIMIT
```

## Phase 3 Roadmap (Upcoming)

- [ ] **ADR-0315:** Confidence intervals (relevance/reliability scoring)
- [ ] **ADR-0316:** Decision history (user choice tracking)
- [ ] **ADR-0317:** Outcome feedback (closed-loop learning)
- [ ] **ADR-0318:** Style preferences (user model)
- [ ] **ADR-0319:** Attention budget (finite attention constraint)
- [ ] **ADR-0320:** Metric collection (aggregation pipeline)
- [ ] **ADR-0321:** Reporting dashboard (observability UI)

## Support & Escalation

- **Logs:** `~/.corvin/logs/vibe_engineering.log`
- **Metrics Export:** `/vibe/metrics` endpoint (JSON)
- **Checkpoint Archive:** `~/.corvin/vibe/checkpoints/` directory

For urgent issues:
1. Check health: `/vibe/health`
2. Gather logs: `tail -100 ~/.corvin/logs/vibe_engineering.log`
3. Export metrics: `curl /vibe/metrics > metrics.json`
4. Report with task ID and checkpoint ID

---

**Questions?** See [ADR-0347](../Corvin-ADR/decisions/ADR-0347-hub-architecture.md) (Hub Architecture) and [ADR-0348](../Corvin-ADR/decisions/ADR-0348-event-bus.md) (Event Bus) for design details.
