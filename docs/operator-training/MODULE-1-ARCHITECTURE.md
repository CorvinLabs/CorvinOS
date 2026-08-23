# Brain v0.2 Operator Training — Module 1: Architecture
## 45-Minute Deep Dive

**Version:** 1.0 (2026-08-23)  
**Target Audience:** Production operators, on-call engineers  
**Prerequisite:** None — this is the foundation module  
**Outcome:** Understand Brain v0.2 design, key subsystems, and how they communicate

---

## Learning Objectives

By the end of this module, you will:
1. Describe the Hub architecture and why it matters
2. Name the 13 core subsystems and their responsibilities
3. Explain event-driven communication (publish/subscribe)
4. Trace a request from entry to decision and back
5. Recognize when a subsystem is misbehaving from its signals

---

## Section 1: Why Brain v0.2? (5 minutes)

### The Problem We're Solving

**Brain v0.1 had:**
- Scattered global state (hard to debug)
- No cross-subsystem communication
- Manual error recovery
- No learning from outcomes

**Brain v0.2 introduces:**
- **Unified state model** (ExecutionContext v2)
- **Central Hub** for coordination
- **13 autonomous subsystems** (health, cost, safety, learning)
- **Event-driven architecture** (loose coupling)
- **Autonomous recovery** (forged tools on failure)

### Key Improvements from v0.1 → v0.2

| v0.1 | v0.2 | Benefit |
|-----|-----|---------|
| Subsystems in silos | Hub coordinates all | Single point of visibility |
| No shared context | ExecutionContext v2 | All subsystems see same state |
| Manual recovery | Autonomous tools | Faster healing, less human work |
| No learning | Auto-grade skills | Better strategies over time |

---

## Section 2: The Hub Architecture (15 minutes)

### Visual Model

```
┌────────────────────────────────────────────────┐
│         TaskBrain (Main Orchestrator)          │
│                                                │
│  ┌──────────────────────────────────────────┐ │
│  │   SubsystemHub (Event Bus + Router)      │ │
│  │                                          │ │
│  │  ┌─ Event Publisher (one-way broadcasts)│ │
│  │  ├─ Request Router (two-way queries)    │ │
│  │  ├─ Subsystem Registry                  │ │
│  │  └─ ContextBus (FIFO event ordering)    │ │
│  └──────────────────────────────────────────┘ │
│         ↓  ↓  ↓  ↓  ↓  ↓  ↓  ↓  ↓  ↓          │
│                                                │
│  13 Registered Subsystems (see next section)  │
└────────────────────────────────────────────────┘
```

### Core Principle: Loose Coupling

**Rule:** No subsystem imports another. All communication flows through the Hub.

**Why?**
- Subsystems can be added/removed without recompilation
- Subsystems can fail independently
- Easy to test in isolation (mock hub)
- New subsystems can be added by users

### Communication Patterns

#### 1. **Events** (One-Way, Async)
```python
# Subsystem publishes event
hub.publish_event("strategy_selected", {"strategy": "decompose", "confidence": 0.85})

# Other subsystems subscribe (they decide how to react)
@hub.on_event("strategy_selected")
async def on_strategy_selected(event_name, event_data):
    strategy = event_data["strategy"]
    # Take action...
```

**Characteristics:**
- Non-blocking (fire-and-forget)
- FIFO ordering via asyncio.Queue
- Subscribers decide what to do
- No response expected

#### 2. **Requests** (Two-Way, Sync)
```python
# Subsystem A asks Subsystem B for information
response = await hub.request_from_subsystem(
    "cost_controller",
    "estimate_cost",
    task_tokens=1000,
    model="claude-3-5-sonnet"
)
# response = {"cost_units": 45, "reduction_potential": 0.25}
```

**Characteristics:**
- Blocking (caller waits for response)
- Used for queries, not notifications
- Request/response pairs must match
- Timeout protection (default 30s)

---

## Section 3: The 13 Core Subsystems (15 minutes)

### Tier 1: Foundation (Always On)

#### 1. **HealthMonitor**
**Responsibility:** Detect stalls, errors, and unhealthy subsystems  
**Key Metrics:**
- Subsystem response time (p50, p95, p99)
- Error rate per subsystem
- Memory footprint

**Signals to Watch:**
- `health_check_failed` event → subsystem not responding
- `latency_spike` event → timeout risk
- Memory threshold exceeded → restart candidate

**When to Escalate:** If HealthMonitor itself fails to emit heartbeats (5m silence), restart it immediately.

#### 2. **ContextBridge**
**Responsibility:** Session splits, checkpoints, memory persistence  
**Key Metrics:**
- Checkpoint save time (<100ms)
- Memory load time (<50ms)
- Context accuracy (0 missing events)

**Events It Publishes:**
- `checkpoint_created` → on task completion
- `context_restored` → when reattaching to session

**When to Escalate:** If context_restored shows stale data (decision timestamp > 1h), suspect memory corruption.

#### 3. **LoopEngineer**
**Responsibility:** Strategy selection, healing (retry, decompose, escalate)  
**Key Metrics:**
- Strategy success rate
- Healing efficacy (% of errors healed)
- Decomposition depth

**Events It Publishes:**
- `strategy_selected` → decision made
- `healing_attempted` → recovery in progress
- `strategy_failed` → healing unsuccessful

**When to Escalate:** Circuit breaker activates after 5+ consecutive failures on same strategy (48h cooldown).

#### 4. **Orchestrator**
**Responsibility:** Parallel task management, worker pool coordination  
**Key Metrics:**
- Active workers (should ~= CPU count)
- Queue depth (0 = good, >100 = backlog)
- Task completion latency

**When to Escalate:** Queue depth > 500 for >10 min (resource exhaustion, likely memory leak).

### Tier 2: Learning & Autonomy

#### 5. **LearningEngine** ← NEW in v0.2
**Responsibility:** Track outcomes, grade strategies, detect patterns  
**Key Metrics:**
- Event store size (max 10,000 entries per session)
- Grade distribution (mean confidence)
- Pattern detection accuracy

**Events It Publishes:**
- `outcome_recorded` → decision + result stored
- `pattern_detected` → learned behavior identified

**When to Escalate:** If event store grows unbounded (should cap at 10k), suspect queue overflow.

#### 6. **CostController** ← NEW in v0.2
**Responsibility:** Budget tracking, token estimation, budget alerts  
**Key Metrics:**
- Budget consumed vs. estimated error (<10%)
- Token burn rate (tokens/second)
- Remaining budget

**Events It Publishes:**
- `budget_warning` (>80% consumed)
- `budget_exhausted` (failsafe triggered)

**When to Escalate:** If estimate error > 20% persistently, cost model may need retraining.

#### 7. **SafetyValidator** ← NEW in v0.2
**Responsibility:** Policy enforcement, resource exhaustion checks, state validation  
**Key Metrics:**
- Policy violation count
- Resource spike frequency
- State validation failures

**Events It Publishes:**
- `policy_violation` → blocked action
- `resource_exhaustion_detected` → memory/CPU spike

**When to Escalate:** If any CRITICAL policy violation is logged, immediate incident response required.

#### 8. **StrategyAdvisor** ← NEW in v0.2
**Responsibility:** Strategy ranking, meta-learning, guidance selection  
**Key Metrics:**
- Strategy success rate by context
- Advice acceptance rate
- Guidance relevance score

**When to Escalate:** If advice acceptance drops below 20%, model drift detected → retraining needed.

### Tier 3: Generation

#### 9. **ToolForgeSubsystem**
**Responsibility:** On-demand tool generation, execution, promotion  
**Key Metrics:**
- Tools forged per session
- Tool execution success rate
- Reuse rate (% of tasks using forged tools)

**Events It Publishes:**
- `tool_forged` → new tool created
- `tool_executed` → execution completed
- `tool_promoted` → moved to PROJECT/GLOBAL

**When to Escalate:** If tool_executed success rate < 50%, sandbox may be too restrictive.

#### 10. **SkillForgeSubsystem**
**Responsibility:** Skill creation, auto-grading, promotion  
**Key Metrics:**
- Skills created per session
- Auto-grade grade distribution (should be bell curve)
- False positive rate on promotion

**Events It Publishes:**
- `skill_created` → new skill added
- `skill_graded` → outcome scored
- `skill_promoted` → moved to PROJECT/GLOBAL

**When to Escalate:** If false positive rate (promoted skills that underperform) > 5%, lower promotion thresholds.

### Tier 4: Distributed Coordination

#### 11. **ContextAPI** (not a subsystem, but key component)
**Responsibility:** Unified interface for all subsystems to query/update context  
**API Methods:**
- `query_context(key)` → read-only, <1µs latency
- `update_context(**kwargs)` → broadcast to all subscribers
- `record_decision(event_name, **metadata)` → audit trail

**When to Escalate:** If query latency > 10µs, suspect lock contention in ContextVar.

#### 12. **ContextBus**
**Responsibility:** FIFO event queue, deterministic ordering  
**Implementation:** `asyncio.Queue` (100 max size)

**Events Flowing Through It:**
- Every subsystem event passes through
- Subscribers process in order
- Lost events logged (full queue)

**When to Escalate:** If queue full errors appear, increase max size or reduce event rate.

#### 13. **Hub API** (RequestRouter)
**Responsibility:** Route requests to correct subsystem, handle timeouts  
**Key Methods:**
- `request_from_subsystem(name, type, **kwargs)` → 30s timeout
- `list_subsystems()` → introspection

**When to Escalate:** If request timeouts occur consistently (>5% of requests), subsystem may be hung.

---

## Section 4: Tracing a Request (8 minutes)

### Example: User asks "How much will this cost?"

```
USER INPUT
    ↓
TaskBrain.run_task(ExecutionContext v2)
    ↓
Orchestrator.handle_request("estimate_cost", task_tokens=1000)
    ↓
CostController responds:
    {"cost_units": 45, "reduction_potential": 0.25}
    ↓
LoopEngineer publishes event: "cost_estimated"
    ↓
LearningEngine receives event, records outcome
    ↓
SafetyValidator checks: is cost within budget?
    ├─ YES: allows operation
    └─ NO: publishes "budget_warning"
    ↓
Result returned to user
```

### Key Insight: Every Step is Observable

In production, you see:
1. **Request entry:** `corvin_requests_total{subsystem="cost_controller"}`
2. **Latency:** `corvin_latency_ms{subsystem="cost_controller"}`
3. **Event publish:** `corvin_events_published_total{event="cost_estimated"}`
4. **Safety check:** `corvin_policy_checks_total{result="allowed|blocked"}`

---

## Section 5: Failure Modes (You Need to Know This!) (5 minutes)

### Failure Mode 1: Subsystem Hangs
**Symptom:** Request timeout (30s exceeded)  
**Likely Cause:** Synchronous I/O (file write, network call) without timeout  
**Operator Action:** Restart that subsystem (via `corvin restart-subsystem <name>`)

### Failure Mode 2: Event Queue Overflow
**Symptom:** `"ContextBus full, dropping events"`  
**Likely Cause:** Subscribers too slow, not processing events fast enough  
**Operator Action:** Identify slow subscriber, restart it or optimize

### Failure Mode 3: Memory Leak in Subsystem
**Symptom:** Memory grows linearly over hours  
**Likely Cause:** Unclosed resource (file handle, DB connection), unbounded list  
**Operator Action:** Restart subsystem, investigate unclosed resources in code

### Failure Mode 4: Hub itself crashes
**Symptom:** All subsystems become unreachable  
**Likely Cause:** Bug in event dispatching, circular dependency  
**Operator Action:** Restart TaskBrain (entire service restarts)

---

## Summary

| Concept | Remember This |
|---------|---------------|
| **Hub** | Central coordinator, all traffic flows through it |
| **Events** | One-way broadcasts, async, FIFO ordered |
| **Requests** | Two-way queries, sync, 30s timeout |
| **13 Subsystems** | Each owns one problem, no direct imports |
| **ExecutionContext v2** | Shared mutable state, versioned, async-safe |
| **Failure Mode** | Hang/timeout → restart subsystem, leak → check resources |

---

## Quick Reference: Subsystem Names & Responsibilities

```
foundation:
  ✓ HealthMonitor — detect problems
  ✓ ContextBridge — save/restore state
  ✓ LoopEngineer — strategy selection, healing
  ✓ Orchestrator — worker pool, parallelism

learning:
  ✓ LearningEngine — track outcomes, grade strategies
  ✓ CostController — estimate tokens, track budget
  ✓ SafetyValidator — enforce policies, resource checks
  ✓ StrategyAdvisor — rank strategies, guidance

generation:
  ✓ ToolForgeSubsystem — create recovery tools
  ✓ SkillForgeSubsystem — create, grade, promote skills

coordination:
  ✓ ContextAPI — unified state interface
  ✓ ContextBus — FIFO event queue (asyncio.Queue)
  ✓ Hub RequestRouter — request routing, timeouts
```

---

## Self-Check Questions

1. **Why is the Hub better than direct imports between subsystems?**  
   _Answer: Loose coupling, easy testing, subsystems can be added/removed independently_

2. **What's the difference between events and requests?**  
   _Answer: Events are one-way async (fire-and-forget), requests are two-way sync (blocking)_

3. **If CostController times out, what do you do?**  
   _Answer: Check if it's hung, restart it via `corvin restart-subsystem cost_controller`_

4. **What's ExecutionContext v2's main advantage over v1?**  
   _Answer: v2 is mutable, shared by all subsystems, enables cross-subsystem learning_

---

**Next Module:** [Monitoring Dashboard Module](MODULE-2-MONITORING.md) (45 min)  
**Time Spent:** 45 minutes  
**Status:** Ready to proceed to monitoring ✅
