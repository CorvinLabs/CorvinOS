# Autonomous Task Execution Playbook — 4 Tasks Production Deployment

**Status:** READY FOR EXECUTION  
**Date:** 2026-08-23  
**Orchestration:** CONCEPT-0009 (AutonomousTaskEngine deployed)  
**Token Budget Required:** ~6-8k per full cycle (all 4 tasks parallel)

---

## TASK 1: Stability Fixes Session 2 [CRITICAL]

**Estimated Effort:** 30 min  
**Timeout:** 1800s  
**Retries:** 2

### Phase 1: Update a2a_pair.py
```bash
# File: operator/bridges/shared/a2a_pair.py or operator/cowork/a2a_pair.py
# Find: def _origins_dir() -> Path
# Replace with: from operator.cowork.remote_paths import get_remote_origins_dir
# Update callers: origins_dir = get_remote_origins_dir()
```

### Phase 2: Update remote_trigger_receiver.py
```bash
# File: operator/bridges/shared/remote_trigger_receiver.py
# Find: def _default_repo_relative() 
# Replace with: from operator.cowork.remote_paths import get_remote_origins_dir
# Update callers: origins = get_remote_origins_dir()
```

### Phase 3: Clear Audit Entries
```bash
# Remove 380 mac_tampered entries (2026-07-11 to 07-13) from ~/.corvin/audit.jsonl
# Keep audit chain integrity (GDPR Art. 30/32)
# Tool: operator/audit/cleanup_stale_entries.py (new, ~50 LoC)
```

### Phase 4: Run Tests
```bash
pytest tests/test_remote_paths_unified.py -v
# Expected: 8/8 PASS
```

### Phase 5: ESCALATION POINT — Maintainer Decision
**REQUIRES HUMAN INPUT — Do NOT proceed without decision:**

Ask maintainer: CORVIN_HOME canonical path?
- **Option A:** Pin to `~/.corvin` (GDPR-compliant, migrate 56 MB history)
- **Option B:** Pin to `<repo>/.corvin` (non-disruptive, violates CLAUDE.md)

Decision impacts L37 (rotation) + L36 (erasure) compliance fixes.

---

## TASK 2: Discord Outbox Silent-Drop [HIGH]

**Estimated Effort:** 1h  
**Timeout:** 3600s  
**Retries:** 3  
**Parallelizable:** YES (independent of Tasks 3-4)

### Investigation Steps
1. **Check Relay Health**
   - File: operator/bridges/discord/relay_listener.py
   - Verify: RelayListener.status() → healthy
   - Metrics: last_ping, connection_state, error_count

2. **Inspect Queue State**
   - File: operator/bridges/discord/outbox_queue.py
   - Count: undelivered messages in queue
   - Check: queue.put() → queue.get() chain integrity
   - Hypothesis: messages stuck in queue (never dequeued)

3. **Trace Delivery Transport**
   - File: operator/bridges/discord/webhook_sender.py
   - Verify: webhook URL valid, retries working
   - Log: last 10 delivery attempts (success/failure)
   - Fix hypothesis: Add timeout + fallback if webhook unresponsive

### Expected Root Cause
- Outbox queue full + listener blocked waiting for queue.get() → silent wedge
- OR webhook endpoint unavailable + no fallback/retry

### Fix Approach
- Add queue depth monitoring
- Add webhook health check
- Add exponential backoff for retries

---

## TASK 3: Discord Precheck Silent-Wedge [HIGH]

**Estimated Effort:** 1h  
**Timeout:** 3600s  
**Retries:** 3  
**Parallelizable:** YES (independent of Tasks 2, 4)

### Investigation Steps
1. **Analyze Precheck Loop**
   - File: operator/bridges/discord/precheck.py
   - Find: where does precheck.run() hang?
   - Hypothesis: deadlock in `checks.run()` vs `queue.put()`

2. **Trace Execution Path**
   - Add trace logging to precheck loop
   - Identify: which check hangs? (rate-limit, permission, etc.)
   - Timeline: when does loop stop responding?

3. **Apply Fix**
   - Add timeout to precheck.run() call
   - Add fallback: if timeout, skip check + proceed
   - Add circuit-breaker: if repeated timeouts, disable precheck

### Expected Root Cause
- Precheck makes external API call (Discord permission check) that hangs indefinitely
- No timeout → loop stalls → queue never drains → silent delivery failure

### Fix Approach
- Wrap precheck.run() in asyncio.wait_for(timeout=10s)
- Fallback: on timeout, assume check passes (safe default)
- Metrics: track timeout frequency, escalate if >10% checks timeout

---

## TASK 4: Dead-Mechanism Call-Site Tests [MEDIUM]

**Estimated Effort:** 30 min  
**Timeout:** 1800s  
**Retries:** 2  
**Parallelizable:** YES (independent of Tasks 2-3)

### Call Sites (6 total)
```
1. operator/bridges/shared/feedback.py::submit_feedback()
2. core/orchestration/subsystems/learning_engine.py::record_outcome()
3. core/skills/skill_executor.py::execute_and_feedback()
4. operator/cowork/remote_trigger_receiver.py::on_feedback_received()
5. core/console/feedback_handler.py::handle_user_feedback()
6. operator/skill-forge/skill_grader.py::grade_skill_outcome()
```

### Test Template (for each call site)
```python
class TestFeedbackCallSite_<CallSiteName>:
    def test_feedback_submitted(self):
        """Verify feedback is submitted and processed."""
        # Arrange: mock dependencies
        # Act: call feedback function
        # Assert: feedback recorded in feedback.db + event emitted
        
    def test_feedback_with_bad_input(self):
        """Verify error handling (not silently dropped)."""
        # Test: None rating, empty feedback text, invalid outcome type
        # Verify: exceptions raised or safely defaulted
        
    def test_feedback_linkage_to_decision(self):
        """Verify feedback links to prior decision_id."""
        # Test: record_outcome(decision_id, outcome)
        # Verify: decision_id stored in feedback record
```

### Implementation
1. Create: `tests/test_feedback_call_sites.py` (~200 LoC)
2. Run: `pytest tests/test_feedback_call_sites.py -v`
3. Expected: 18+ tests PASS (3 per call site)

---

## EXECUTION SEQUENCE

### Phase 1: Task 1 (CRITICAL) — Sequential
```
Task 1 Phase 1-4: Auto (update paths, clear audit, test) → ~20 min
Task 1 Phase 5: PAUSE for maintainer decision → human input required
```

### Phase 2: Tasks 2-4 (HIGH+MEDIUM) — Parallel
```
Start immediately after Task 1 Phase 4 (don't wait for maintainer decision)

Task 2 (Outbox investigation):     ~60 min
Task 3 (Precheck investigation):   ~60 min (parallel with Task 2)
Task 4 (Call-site tests):          ~30 min (parallel with Tasks 2-3)

Estimated total for Phase 2: 60 min (parallel execution)
```

### Total Time: ~80 min (1h 20 min)

---

## SUCCESS CRITERIA

✅ **Task 1:** Paths unified, audit cleaned, maintainer decision captured  
✅ **Task 2:** Outbox root cause identified + fix deployed  
✅ **Task 3:** Precheck deadlock resolved + timeout added  
✅ **Task 4:** All 6 call sites have unit tests, 100% pass rate

---

## NEXT SESSION ENTRY POINT

```bash
# 1. Load orchestration engine
cd /home/shumway/projects/CorvinOS
python -c "
from core.orchestration.autonomous_task_engine import AutonomousTaskEngine
from core.orchestration.autonomous_tasks_corvinOS import ALL_TASKS

engine = AutonomousTaskEngine('CorvinOS-Brain-Prod')
for task in ALL_TASKS:
    engine.register_task(task)

# 2. Execute tasks
import asyncio
results = asyncio.run(engine.execute_parallel([
    'discord-outbox-investigation',
    'discord-precheck-investigation',
    'dead-mechanism-tests'
]))

# Task 1 runs separately (CRITICAL priority, escalation at step 5)

# 3. Monitor
print(engine.get_status())
"

# 4. If Task 1 escalation triggers, wait for maintainer input, then proceed with Step 5
```

---

## RISKS & MITIGATION

| Risk | Mitigation |
|---|---|
| Task 1 escalation blocks progress | Queue other tasks parallel (independent) |
| Discord investigations find no root cause | Increase logging, add manual trace points |
| Tests reveal undocumented call sites | Extend test suite incrementally |
| Timeout too aggressive (Tasks 2-3) | Start with 60s, tune down from there |

---

**Status:** ✅ PRODUCTION DEPLOYMENT READY  
**Next Action:** Execute playbook tasks 1-4 in dedicated Session (80 min estimated)
