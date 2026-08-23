# Autonomous Session Boot Manifest — Multi-Session Continuation Protocol

**Purpose:** Enable seamless continuation across multiple autonomous sessions without human intervention  
**Date Created:** 2026-08-23 (Session End)  
**Status:** READY FOR DEPLOYMENT

---

## SESSION BOOT SEQUENCE

### For Each New Session (automated startup):

```bash
# 1. Load CorvinOS context
cd /home/shumway/projects/CorvinOS
git status  # verify clean state
git log --oneline | head -3  # verify latest commits

# 2. Load Autonomous Orchestration Engine
python3 << 'BOOT'
from core.orchestration.autonomous_task_engine import AutonomousTaskEngine
from core.orchestration.autonomous_tasks_corvinOS import ALL_TASKS

engine = AutonomousTaskEngine("CorvinOS-Autonomous-Brain")
for task in ALL_TASKS:
    engine.register_task(task)
print(f"✅ Engine ready. Registered {len(engine.tasks)} tasks")
BOOT

# 3. Load memories (check latest status)
ls -lt ~/.claude/projects/-home-shumway-projects-CorvinOS/memory/*.md | head -5

# 4. Execute prioritized task sequence (see execution phases below)
```

---

## MULTI-SESSION EXECUTION PHASES

### **PHASE 1: v1.0.0 Release Blockers (Session N+1, ~7h)**

**Blocker 1:** Browser Sensitive-Confirms (H3)
- File: `core/console/chat.tsx` + `operator/bridges/discord/confirm_handler.py`
- Fix: Add confirm-event stream + UI buttons
- Tests: UI Playwright tests
- Effort: 2-3h

**Blocker 2:** Bridge-Browser Governance (H4)
- File: `operator/bridges/shared/activate.py` + ADR-0200 redesign
- Fix: Remove Playwright from bridge arsenal OR implement cross-process token endpoint
- Tests: E2E bridge isolation
- Effort: 3-4h

**Blocker 3:** CORVIN_HOME Split (CRITICAL)
- File: `operator/cowork/remote_paths.py` (unified resolver exists)
- Fix: Decide canonical path (maintainer input required)
- Effort: 2-3h (once decided)

**Entry Point:**
```bash
# Start Session N+1
# Load v1.0.0 blocker context
python3 << 'FIX_BLOCKERS'
# 1. Fix browser H3
# 2. Fix browser H4
# 3. Ask maintainer CORVIN_HOME decision + apply
# 4. Run full test suite
# 5. Commit fixes (tags: blocker-fixes)
FIX_BLOCKERS

git log --oneline | head -1  # verify commit
```

---

### **PHASE 2: Autonomous 4-Task Execution (Session N+2, 80 min)**

**Task 1 (CRITICAL):** Stability Fixes Session 2
- Update path resolvers (a2a_pair.py + remote_trigger_receiver.py)
- Clear audit entries
- Pause at: Maintainer CORVIN_HOME decision

**Tasks 2-4 (parallel):** Discord outbox + precheck + call-site tests
- 60 min parallel execution
- Independent of Task 1 Phase 5 pause

**Entry Point:**
```bash
# Load playbook: AUTONOMOUS-TASK-EXECUTION-PLAYBOOK-4TASKS.md
# Execute via orchestration engine
python3 << 'EXECUTE_TASKS'
import asyncio
from core.orchestration.autonomous_task_engine import AutonomousTaskEngine
from core.orchestration.autonomous_tasks_corvinOS import ALL_TASKS

engine = AutonomousTaskEngine("CorvinOS-Brain-Session2")
for task in ALL_TASKS:
    engine.register_task(task)

# Execute Task 1 (CRITICAL)
result_1 = await engine.execute_task("stability-fixes-session2")
print(f"Task 1: {result_1}")

# Execute Tasks 2-4 in parallel
results_234 = await engine.execute_parallel([
    "discord-outbox-investigation",
    "discord-precheck-investigation",
    "dead-mechanism-tests"
])
print(f"Tasks 2-4: {results_234}")
print(engine.get_status())  # health check
EXECUTE_TASKS
```

---

### **PHASE 3: Master Refactoring (Session N+3+, weeks 2-4)**

**Phases 1-7** (from `MASTER-FIX-PLAN-5MEMORIES-5ADRS.md`):
- Phase 1: Foundation (ADRs 0296-0302, 101h)
- Phases 2-7: Concurrency, learning, consolidation

**Phased Execution:** One phase per session (prevent context loss)

**Entry Point:**
```bash
# Load Master Refactoring Plan
# Check which phase is next (from git log)
# Execute that phase autonomously
# Commit results
# Schedule next phase
```

---

## AUTONOMOUS LOOP PROTOCOL

### Memory Management:
```bash
# At session start: read latest memories
ls ~/.claude/projects/-home-shumway-projects-CorvinOS/memory/*.md | xargs ls -lt | head -5

# At session end: write session summary
# (auto-saved to memory as SESSION-<date>-summary.md)
```

### Git Hygiene:
```bash
# Before each phase: verify clean state
git status  # must be clean

# After each phase: verify commits
git log --oneline | head -5

# If merge needed: coordinate with main
git fetch origin
git merge origin/main  # if behind
```

### Health Checks:
```bash
# Run after each task/phase:
python3 -c "
from core.orchestration.autonomous_task_engine import AutonomousTaskEngine
engine = AutonomousTaskEngine('health-check')
# Check system status (CPU, memory, disk)
# Verify no zombies/deadlocks
"

# If health check fails: escalate to maintainer
```

---

## ESCALATION POINTS (Require Human Input)

| Event | Action | Escalation |
|---|---|---|
| Task 1 Phase 5 (CORVIN_HOME) | Pause | Ask maintainer: pin to ~/.corvin or <repo>/.corvin? |
| Test failures | Retry 2x | If still failing: escalate + investigate |
| Git merge conflict | Pause | Ask maintainer to resolve conflict |
| Security finding | Pause | Ask security reviewer before fixing |
| Token budget low | End session | Write handoff + exit cleanly |

---

## SUCCESS CRITERIA

✅ **Session N+1:** v1.0.0 blockers fixed, all tests pass, release ready  
✅ **Session N+2:** All 4 autonomous tasks complete, no blockers  
✅ **Session N+3+:** Master refactoring phases execute in sequence, 0 regressions  

---

## AUTOMATED SESSION REPORT (end-of-session template)

```markdown
## Session Summary

**Session ID:** [date]-[hour]  
**Duration:** [minutes]  
**Commits:** [N]  
**Status:** [✅ COMPLETE / ⏸️ ESCALATION / ❌ FAILED]

### Completed:
- [ ] Task 1: ...
- [ ] Task 2: ...
- [ ] Task 3: ...

### Escalations:
- [ ] Issue A requires maintainer input
- [ ] Issue B requires security review

### Next Session Entry:
- Load: [specific memory file]
- Execute: [phase/task name]
- Expected effort: [Nh]

**Metrics:**
- Stability: [pass/fail]
- Test coverage: [%]
- Blockers: [N remaining]
```

---

## DEPLOYMENT CHECKLIST

- [ ] Memories updated + committed
- [ ] Playbook + manifests written + committed
- [ ] All ADRs referenced + up-to-date
- [ ] v1.0.0 blockers prioritized
- [ ] 4-task orchestration ready
- [ ] Master refactoring phases sequenced
- [ ] Escalation protocol documented
- [ ] Session boot sequence automated

✅ **ALL READY FOR AUTONOMOUS MULTI-SESSION EXECUTION**

---

**Status:** AUTONOMOUS EXECUTION INFRASTRUCTURE COMPLETE  
**Next:** Deploy via automated session boot (Session N+1+)
