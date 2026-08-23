# CONCEPT: Self-Managed Sessions in Corvin Brain

**Status:** DRAFT (Design Phase)  
**Created:** 2026-08-23  
**Related:** ADR-0347–0350 (Brain Architecture), CONCEPT-0009 (Cognitive Hub)

---

## Problem Statement

**Current Limitation:** Corvin Brain v0.2-rc1 can run single long-running tasks with error recovery, but **cannot autonomously manage very long multi-phase tasks** (days, hundreds of iterations) without manual intervention.

**Why It Matters:** Tasks like "audit entire codebase → find patterns → propose architecture → implement → test → integrate" require:
- Context-window management (100k+ tokens → new sessions)
- Phase transitions (Planning → Execution → Validation → Finalization)
- Checkpoint recovery (resume from breakpoint, not restart)
- Adaptive strategy (if stuck, try different approach)

**Goal:** Enable Corvin to open/close sessions autonomously with well-defined checkpoints, so tasks run end-to-end without human handoffs.

---

## Architecture Overview

### High-Level Flow

```
TaskBrain (orchestration hub)
├─ Phase Manager (tracks current phase + exit conditions)
├─ Checkpoint Manager (serializes state at milestones)
├─ Session Lifecycle Manager (creates/closes sessions)
├─ Context Reducer (trims 200k → 20k tokens intelligently)
├─ Entropie Detector (recognizes drift from task goal)
├─ Self-Knowledge Monitor (recognizes when Corvin is stuck)
├─ Error Propagation Tracker (detects upstream failures)
└─ Recovery Engine (replay from checkpoint, idempotent)

All coordinated through existing EventBus + RequestRouter pattern
(no new central orchestration, reuse Brain v0.2 hub)
```

---

## 1. Session-Lifecycle-Events (When to Split)

### Trigger Rules (in priority order)

| Trigger | When | Action |
|---------|------|--------|
| **Phase Exit** | Phase exit condition met | Close phase, checkpoint, start new phase |
| **Context Limit** | Context window ≥ 85% | Checkpoint, new session (same phase) |
| **Token Burn** | Cost ≥ daily budget limit | Checkpoint, pause, escalate |
| **Explicit Milestone** | Task explicitly marks milestone | Checkpoint, optional new session |
| **Iteration Cap** | Iteration count ≥ N (e.g., 50) | Checkpoint, new session (same phase) |
| **Stall Detected** | No progress ≥ M minutes (e.g., 30) | Checkpoint, retry/pivot, same session or new |

### Implementation: `SessionLifecycleManager` Subsystem

```python
class SessionLifecycleManager(Subsystem):
    """Detects session-split triggers and orchestrates new sessions."""
    
    async def on_event(self, event_name, data):
        # Listen for:
        # - phase_exit_condition_met
        # - context_window_high
        # - cost_exceeded
        # - task_milestone_reached
        # - iteration_cap_reached
        # - task_stalled (from HealthMonitor)
        pass
    
    async def handle_request(self, request_type, **kwargs):
        # - should_split_session()
        # - create_checkpoint()
        # - inject_checkpoint_to_new_session()
        pass
```

**Events Emitted:**
- `session_split_triggered` (condition met)
- `checkpoint_created` (state saved)
- `new_session_spawned` (next session started)
- `session_recovery_attempt` (retry from checkpoint)

---

## 2. Checkpoint Definition & Serialization

### Checkpoint Structure

```json
{
  "metadata": {
    "task_id": "abc123",
    "session_id": "session_2",
    "phase": "execution",
    "checkpoint_num": 3,
    "created_at": "2026-08-23T14:30:00Z",
    "trigger": "context_limit",
    "context_window_used": 165000,
    "iterations_completed": 47
  },
  "task_state": {
    "goal": "Audit codebase and propose architecture refactor",
    "original_constraints": [...],
    "user_intent": "Find patterns, not implement yet",
    "progress_summary": "Scanned 250 files, found 5 architecture smells"
  },
  "open_subgoals": [
    {
      "id": "subgoal_1",
      "description": "Validate each smell across test suite",
      "status": "in_progress",
      "work_done": "Validated smell #1, #2 — 3 more to go"
    }
  ],
  "artifacts": [
    {
      "name": "findings.md",
      "path": "/tmp/task_abc123/findings.md",
      "size_bytes": 45000,
      "hash": "sha256:abc...",
      "essential": true,
      "reason": "Contains all validated findings needed for next phase"
    }
  ],
  "learning_state": {
    "strategies_tried": ["direct_fix:2", "pivot:1"],
    "strategies_succeeded": ["direct_fix:2"],
    "error_patterns": ["timeout_during_validation:3"],
    "next_strategy_recommendation": "If validation times out again, parallelize via ACS"
  },
  "context_essentials": {
    "kept_sections": [
      "task_goal",
      "constraints",
      "validated_findings",
      "error_patterns",
      "strategy_recommendations"
    ],
    "dropped_sections": [
      "intermediate_debug_logs",
      "tried_but_failed_approaches",
      "micro_steps_already_executed"
    ],
    "size_reduction": "200k → 18k tokens (91% reduction)"
  }
}
```

### Checkpoint Manager Subsystem

```python
class CheckpointManager(Subsystem):
    """Creates, stores, and restores checkpoints."""
    
    async def create_checkpoint(self, 
                               task_id: str,
                               phase: str,
                               trigger: str) -> Dict:
        """
        Serialize current execution state to checkpoint.
        
        Returns checkpoint dict with:
        - metadata (task_id, phase, trigger, timestamp)
        - task_state (goal, constraints, progress)
        - open_subgoals (what remains)
        - artifacts (files needed for next session)
        - learning_state (strategies, errors)
        - context_essentials (trimmed context)
        """
        # Query all subsystems for their state:
        # - ContextBridge: memory summary
        # - LearningEngine: strategy recommendations
        # - LoopEngineer: error patterns
        # - Orchestrator: open dependencies
        
        # Serialize to disk
        return checkpoint_dict
    
    async def restore_from_checkpoint(self, checkpoint_id: str):
        """Load checkpoint, inject into new session context."""
        pass
    
    async def on_event(self, event_name, data):
        # Listen for checkpoint-creation triggers
        pass
```

**Why This Works:**
- **Deterministic:** Same checkpoint → same next steps (replay-able)
- **Minimal:** 91% context reduction (200k → 18k tokens)
- **Complete:** All needed decisions + artifacts preserved
- **Attributable:** Each section explains why it's kept/dropped

---

## 3. Context-Handoff Strategy: Intelligent Reduction

### The Challenge

Original session accumulated:
- 200k tokens of chat history, debug logs, intermediate attempts
- New session needs → ~20k tokens to continue (10% of original)

**Solution: Tiered Context Preservation**

| Tier | Content | Keep? | Reason |
|------|---------|-------|--------|
| **Tier 0: Must-Have** | Task goal, constraints, validated findings, error patterns | ✅ | Without this, new session doesn't know what it's solving |
| **Tier 1: Should-Have** | Strategy recommendations from learning, current phase, artifacts | ✅ | Speeds up new session (no re-learning) |
| **Tier 2: Nice-to-Have** | Intermediate attempts, successful but now-stale approaches | ❌ | New session can rediscover if needed, wastes tokens |
| **Tier 3: Never-Keep** | Debug logs, micro-step transcripts, raw token counts | ❌ | Not needed for continuation, pure noise |

### Implementation: `ContextReducer` Subsystem

```python
class ContextReducer(Subsystem):
    """Intelligently trims context for handoff."""
    
    async def reduce_context(self, full_context: str, 
                            checkpoint: Dict) -> str:
        """
        Reduce 200k → 20k tokens preserving essentials.
        
        Algorithm:
        1. Extract Tier-0 (MUST-HAVE): goal, constraints, validated findings
        2. Add Tier-1 (SHOULD-HAVE): strategies, current phase, key artifacts
        3. Summarize Tier-2 (SKIP): "Tried 3 approaches, found optimal on attempt 2"
        4. Emit with clear boundaries: "=== Context Reduction Summary ===" section
        5. Validate: Include token count estimate
        
        Returns:
        - reduced_context: ~20k tokens, clearly marked boundaries
        - reduction_metadata: what was kept/dropped and why
        """
        # Use ExecContext's summarization + selective deletion
        # (don't re-summarize from scratch — reuse Brain's own context bridge)
        pass
    
    async def on_event(self, event_name, data):
        # Listen for checkpoint creation
        # → automatically reduce context for next session
        pass
```

**Token Accounting:**
- Task goal + constraints: 2k
- Validated findings (structured): 8k
- Current phase + subgoals: 3k
- Strategy recommendations: 2k
- Error patterns + next approaches: 2k
- Artifact references: 1k
- **Total: ~18k** (plenty of margin to 20k budget)

---

## 4. Recovery Patterns: Idempotent Replay

### The Invariant

**Checkpoints must be deterministic:**
```
checkpoint_N_state → same new_session_state → deterministic next steps
```

### Recovery Modes

| Scenario | Mode | Action |
|----------|------|--------|
| **Timeout during execution** | Replay | Restart from checkpoint, same strategy (idempotent) |
| **Strategy failed** | Adapt | Restart from checkpoint, different strategy (LearningEngine recommends) |
| **Validation error upstream** | Backtrack | Restore from earlier checkpoint, fix root cause |
| **Quota exceeded** | Pause → Resume | Checkpoint, wait for quota reset, resume |

### Implementation: `RecoveryEngine` Subsystem

```python
class RecoveryEngine(Subsystem):
    """Orchestrates deterministic recovery from checkpoints."""
    
    async def handle_request(self, request_type, **kwargs):
        if request_type == "can_replay_idempotently":
            # Check: Is this step deterministic (no randomness, same inputs)?
            return {"idempotent": True}  # or False
        
        if request_type == "restore_and_resume":
            checkpoint_id = kwargs['checkpoint_id']
            recovery_mode = kwargs['mode']  # 'replay' | 'adapt' | 'backtrack'
            
            # 1. Load checkpoint
            checkpoint = await CheckpointManager.restore_from_checkpoint(checkpoint_id)
            
            # 2. Inject into new session's ExecutionContext
            await ContextBridge.inject_checkpoint(checkpoint)
            
            # 3. If 'adapt' mode, ask LearningEngine for different strategy
            if recovery_mode == 'adapt':
                strategies = await LearningEngine.recommend_alternative_strategies(
                    error=checkpoint['learning_state']['last_error']
                )
            
            # 4. Resume execution from checkpoint state
            # (new session continues, unaware of the split)
            pass
    
    async def on_event(self, event_name, data):
        # Listen for recovery triggers:
        # - task_timeout
        # - strategy_failed
        # - escalation_needed
        pass
```

---

## 5. Missing Blockers: The Five Silent Failures

### Blocker 1: Context Decay

**Problem:**
```
Task goal: "Audit codebase, find patterns, propose architecture"
After 50 iterations in Execution phase:
  Corvin is deep in file #150, optimizing microservices communication
  Has lost sight that original goal was architecture-LEVEL patterns
  Might miss the forest for the trees
```

**Signal to Detect:** "Am I still solving the original task, or have I drifted?"

**Solution: `GoalAlignmentMonitor` Subsystem**

```python
class GoalAlignmentMonitor(Subsystem):
    """Detects drift from original task goal."""
    
    async def on_event(self, event_name, data):
        if event_name == "strategy_applied" or event_name == "iteration_complete":
            # Ask: Does current work still align with original goal?
            alignment_score = await self.check_goal_alignment()
            
            if alignment_score < 0.6:  # Drifted significantly
                await self.hub.publish_event(
                    "goal_drift_detected",
                    {
                        "original_goal": self.task_goal,
                        "current_work": data['current_focus'],
                        "alignment_score": alignment_score,
                        "recommendation": "Return to Phase N.1 and re-align"
                    }
                )
    
    async def check_goal_alignment(self) -> float:
        """
        Returns [0.0 - 1.0] where:
        1.0 = perfectly aligned with original goal
        0.0 = completely unrelated to original goal
        
        Uses semantic similarity between:
        - Original task goal
        - Current iteration work
        
        Cheap operation: ~1k tokens for comparison
        """
        pass
```

**Trigger for Session Split:** If alignment < 0.5, checkpoint + escalate to human OR automatically restart phase from earlier checkpoint.

---

### Blocker 2: Entropie (Increasing Chaos)

**Problem:**
```
Phase 1 (Planning): Orderly. Found 5 clean patterns.
Phase 2 (Execution): Added 3 contradictory implementations.
Phase 3 (Validation): Tests fail because impl contradicts Phase 1 findings.
```

**Signal to Detect:** "Is the system's entropy increasing? Am I creating conflicts?"

**Solution: `ConsistencyValidator` Subsystem**

```python
class ConsistencyValidator(Subsystem):
    """Detects contradictions between phases."""
    
    async def on_event(self, event_name, data):
        if event_name == "phase_transition":
            # Check for contradictions between current & next phase
            conflicts = await self.find_contradictions(
                phase_from=data['phase_from'],
                phase_to=data['phase_to']
            )
            
            if conflicts:
                await self.hub.publish_event(
                    "entropy_detected",
                    {
                        "conflicts": conflicts,
                        "severity": "high" if len(conflicts) > 3 else "medium",
                        "recommendation": "Review Phase 1 output before entering Phase 2"
                    }
                )
    
    async def find_contradictions(self, phase_from, phase_to) -> List[Dict]:
        """
        Compare decisions from phase_from with planned work in phase_to.
        
        Cheap heuristic:
        - Extract 5-7 key decisions from phase_from
        - Check if phase_to work contradicts them
        - Return list of conflicts
        """
        pass
```

**Trigger for Session Split:** If entropy detected, halt phase → human review OR backtrack to last clean checkpoint.

---

### Blocker 3: Error Propagation (Cascade Failures)

**Problem:**
```
Phase 1: Made assumption A (minor, unvalidated)
Phase 2: Built on assumption A (propagated)
Phase 3: Assumption A turns out wrong → entire Phase 2-3 now broken
Result: Lost 20 hours of work, need to restart Phase 1
```

**Signal to Detect:** "Did Phase 1 make any unvalidated assumptions? Will Phase 3 crash if they're wrong?"

**Solution: `AssumptionTracker` Subsystem**

```python
class AssumptionTracker(Subsystem):
    """Flags unvalidated assumptions at phase boundaries."""
    
    async def on_event(self, event_name, data):
        if event_name == "phase_exit_condition_met":
            # Collect all assumptions made in this phase
            assumptions = await self.extract_assumptions(data['phase'])
            unvalidated = [a for a in assumptions if not a['validated']]
            
            if unvalidated:
                await self.hub.publish_event(
                    "unvalidated_assumptions",
                    {
                        "phase": data['phase'],
                        "assumptions": unvalidated,
                        "recommendation": "Validate or document risk before next phase"
                    }
                )
    
    async def extract_assumptions(self, phase: str) -> List[Dict]:
        """
        Parse phase output for language patterns like:
        - "Assuming that..."
        - "We expect..."
        - "Based on X, we can infer..."
        - "Without evidence, we assume..."
        
        Flag as unvalidated if no follow-up validation found.
        """
        pass
```

**Trigger for Session Split:** If critical unvalidated assumptions exist, halt → human review OR spike to validate before proceeding.

---

### Blocker 4: Local Optima (Stuck in Suboptimal Solution)

**Problem:**
```
Iteration 1-30: Try approach A
  → Success rate: 60%
  → Corvin gets stuck optimizing A (marginal gains)
  → Doesn't try approach B (potentially 90% success)
```

**Signal to Detect:** "Am I stuck in a local optimum? Should I reset and try something different?"

**Solution: `ExplorationScheduler` Subsystem**

```python
class ExplorationScheduler(Subsystem):
    """Forces periodic exploration of alternatives."""
    
    async def on_event(self, event_name, data):
        if event_name == "strategy_succeeded":
            # Track success over last N iterations
            recent_success_rate = await self.compute_success_rate(window=10)
            
            if recent_success_rate >= 0.6 and recent_success_rate < 0.8:
                # Stuck in local optimum: decent but not great
                if self.iterations_on_current_strategy > 15:
                    await self.hub.publish_event(
                        "local_optimum_suspected",
                        {
                            "current_strategy": data['strategy'],
                            "success_rate": recent_success_rate,
                            "iterations_on_strategy": self.iterations_on_current_strategy,
                            "recommendation": "Try alternative approach for 5 iterations"
                        }
                    )
    
    async def handle_request(self, request_type, **kwargs):
        if request_type == "should_explore_alternatives":
            # Return True if we should try a different strategy
            # (e.g., every 20 iterations, or if success rate plateaus)
            pass
```

**Trigger for Session Split:** If local optimum detected, checkpoint + try alternative strategy in new session.

---

### Blocker 5: Self-Knowledge (Recognizing Overload)

**Problem:**
```
Corvin is exhausted after 100 iterations, context is a mess,
but has no mechanism to say "I need to pause, consolidate, and restart fresh."
Keeps grinding until timeout.
```

**Signal to Detect:** "Am I overloaded? Do I need a break + fresh start?"

**Solution: `SelfMonitoringSubsystem` Subsystem**

```python
class SelfMonitoringSubsystem(Subsystem):
    """Monitors Corvin's own cognitive load + suggests breaks."""
    
    async def on_event(self, event_name, data):
        # Listen to all events, track metrics:
        
        if event_name in ["error_detected", "strategy_failed", "task_stalled"]:
            self.error_count += 1
            self.recent_errors.append((time.time(), event_name))
        
        if event_name == "iteration_complete":
            self.iterations_completed += 1
        
        # Every N iterations, check health
        if self.iterations_completed % 20 == 0:
            await self.assess_cognitive_load()
    
    async def assess_cognitive_load(self) -> Dict:
        """
        Compute cognitive-load score:
        
        Inputs:
        - error_rate (errors per iteration)
        - context_size (tokens used)
        - strategy_diversity (tried N different strategies?)
        - time_spent (wallclock time since last checkpoint)
        - token_burn_rate (tokens/iteration)
        
        Output:
        {
          "cognitive_load": 0.0-1.0,
          "recommendation": "fresh_start" | "checkpoint_continue" | "pause",
          "reason": "error_rate_too_high" | "context_saturation" | "strategy_exhaustion"
        }
        
        If cognitive_load > 0.8:
          → Emit "cognitive_overload" event
          → Suggest checkpoint + pause
          → Next session starts fresh (full context reset)
        """
        pass
```

**Trigger for Session Split:** If overload detected, checkpoint + **reset context entirely** (start fresh session with minimal bootstrap context).

---

## 6. Integration with Brain v0.2

### Subsystems to Add

| Subsystem | Purpose | Trigger |
|-----------|---------|---------|
| `SessionLifecycleManager` | Detect split conditions | Pubsub events |
| `CheckpointManager` | Serialize/restore state | On split trigger |
| `ContextReducer` | Trim 200k → 20k | On checkpoint create |
| `RecoveryEngine` | Replay from checkpoint | On error/timeout |
| `GoalAlignmentMonitor` | Detect goal drift | Every iteration |
| `ConsistencyValidator` | Find contradictions | Phase transitions |
| `AssumptionTracker` | Flag unvalidated assumptions | Phase exits |
| `ExplorationScheduler` | Force alt-strategy exploration | Every 20 iterations |
| `SelfMonitoringSubsystem` | Recognize overload | Every 20 iterations |

**Total New Subsystems: 9**

**Integration Points (existing Brain):**
- `ContextBridge`: Already handles memory injection → reuse for checkpoint restore
- `LoopEngineer`: Already tries strategies → reuse for recovery + exploration
- `HealthMonitor`: Already detects stalls → coordinate with SessionLifecycleManager
- `LearningEngine`: Already recommends strategies → use for recovery mode adaptation
- `EventBus`: Reuse for all new events

### Architecture Diagram

```
TaskBrain v0.2-rc1 (existing)
│
├─ HealthMonitor ──┐
├─ ContextBridge   ├─ Coordinate via EventBus
├─ LoopEngineer ───┤
├─ Orchestrator    │
└─ LearningEngine ─┤
                    │
                   NEW: SessionLifecycleManager ◄─ Triggers session splits
                    │
                   NEW: CheckpointManager ◄──────── Serializes state
                    │                       
                   NEW: ContextReducer ◄─────────── Trims context
                    │                       
                   NEW: RecoveryEngine ◄─────────── Replays from checkpoint
                    │
                   NEW: 5 Monitors
                    ├─ GoalAlignmentMonitor
                    ├─ ConsistencyValidator
                    ├─ AssumptionTracker
                    ├─ ExplorationScheduler
                    └─ SelfMonitoringSubsystem
```

---

## 7. Example: Long-Running Task Flow

### Task: "Audit codebase, propose architecture refactor"

```
Timeline:
  T=0h00: Start Planning Phase
    • Scan codebase structure
    • Identify 5 architecture smells
    • Propose 3 refactoring approaches
    → Checkpoint #1 (end of Planning)
    → New session A1 spawned
  
  T=2h30: Start Execution Phase (Session A1)
    • Validate smell #1 across test suite
    • Implement first refactor
    • Validate smell #2
    → Context growing (180k tokens)
    → GoalAlignmentMonitor: "Still on track ✓"
    → Checkpoint #2 (15 iterations into Execution)
    → New session A2 spawned (same phase, different iteration batch)
  
  T=5h00: Continue Execution (Session A2)
    • Validate smell #3, #4, #5
    • Implement remaining refactors
    → ConsistencyValidator: "Found contradiction! Implementation in #4 conflicts with #1"
    → Halt phase
    → Checkpoint #3 (current state)
    → New session A2b (recovery mode: backtrack)
  
  T=5h15: Recovery (Session A2b)
    • Load Checkpoint #1 (pre-implementation)
    • Re-validate assumption about smell #1
    • Confirm conflict real
    → Escalate to human: "Smells #1 and #4 are mutually exclusive. Which refactor do you prefer?"
  
  T=6h00: Human input received
    • Update checkpoint with human decision
    • New session A3 spawned (Execution phase, with resolved conflict)
  
  T=10h00: Execution complete
    → Checkpoint #4 (end of Execution)
    → New session B1 spawned (Validation phase)
  
  T=14h00: Validation complete
    → Checkpoint #5 (end of Validation)
    → New session C1 spawned (Finalization phase)
  
  T=16h00: Finalization complete
    → Task done. Total: 16 wallclock hours, no human hands-on time.
```

**Self-Management Highlights:**
- ✅ 5 automatic session splits (not manual handoffs)
- ✅ 2 checkpoints prevent full restarts (recovery, backtrack)
- ✅ Context reduced 91% per split (200k → 18k)
- ✅ GoalAlignmentMonitor kept task focused
- ✅ ConsistencyValidator caught architecture conflict
- ✅ SelfMonitoringSubsystem would've signaled overload (none here, short task)

---

## 8. Open Questions & Future Work

### Q1: What if Corvin makes contradictory decisions across sessions?

**A:** ConsistencyValidator catches at phase transitions. If conflict found → halt, human review, OR backtrack to earlier checkpoint and re-solve.

### Q2: How does learning persist across sessions?

**A:** LearningEngine's strategy recommendations + error patterns are serialized in checkpoint. New session starts with learned strategies already available.

### Q3: What if a checkpoint is stale (task changed mid-execution)?

**A:** Checkpoints include timestamp + task_id. If task redefined, checkpoints are invalidated (human resets task goal in ExecutionContext). New session starts with new baseline.

### Q4: Can checkpoints be parallelized (multiple sessions from one checkpoint)?

**A:** **Not recommended for correctness.** Checkpoints assume linear ordering. Parallel sessions should only happen at task-dependency boundaries (Orchestrator manages).

### Q5: What's the cost of checkpoint creation?

**A:** ~2-5 seconds (serialize current state, write to disk). Cheap relative to 30+ min phases.

---

## 9. Success Metrics

A Self-Managed Sessions system is production-ready when:

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Session Duration** | < 30 min avg | Wallclock time between splits |
| **Context Reduction** | > 85% | (original size - reduced size) / original |
| **Recovery Success** | > 95% | Checkpoints restored successfully |
| **Goal Alignment** | > 0.7 avg | Alignment score throughout task |
| **Human Interventions** | < 2 per long task | Escalations needed (conflicts, ambiguity) |
| **End-to-End Task Time** | 3-5 days max | Entire audit + implement + validate |

---

## 10. Next Steps (Phase 2 Roadmap)

1. **Implement 9 new subsystems** (~3-4 weeks)
   - SessionLifecycleManager, CheckpointManager, ContextReducer, RecoveryEngine
   - GoalAlignmentMonitor, ConsistencyValidator, AssumptionTracker, ExplorationScheduler, SelfMonitoringSubsystem
   - Tests: 150+ new test cases

2. **E2E test on real long-running task** (~2 weeks)
   - Run audit task end-to-end with synthetic checkpoints
   - Measure token reduction, split latency, recovery success
   - Verify goal alignment throughout

3. **Operator runbook** (~1 week)
   - How to recover from checkpoint manually
   - How to interpret monitoring events
   - How to tune split thresholds

4. **Production pilot** (~2 weeks)
   - Deploy with 10% users
   - Monitor error rates, cost trends, human escalations
   - Iterate on thresholds

---

## References

- **ADR-0347:** Brain Subsystem Hub Architecture
- **ADR-0348:** Event Bus Pattern  
- **ADR-0349:** Plugin Interface Contract
- **ADR-0350:** Configuration-Driven Plugin Loading
- **CONCEPT-0009:** Cognitive Hub (Brain v0.2-rc1)
- **Brain v0.2-rc1:** 13 subsystems, pub/sub event coordination

