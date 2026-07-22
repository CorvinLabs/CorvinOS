# ADR-DRAFT: Batch LM Call Optimization — Reduce Token Cost & Latency

**Status:** Concept (Design Phase)  
**Date:** 2026-07-20  
**Problem:** CorvinOS makes 5-20+ sequential LM calls per user request, each adding 2-5 seconds latency + token overhead  
**Goal:** Reduce LM calls by 60-80% via batch processing, caching, and smart delegation

---

## Problem Analysis

### Current Architecture (Token-Wasteful)
```
User Input
  ↓
[LM Call 1] Classify task type (1.2s)
  ↓
[LM Call 2] Extract entities (1.1s)
  ↓
[LM Call 3] Route to engine (0.8s)
  ↓
[LM Call 4] Generate plan (2.1s)
  ↓
[LM Call 5] Execute step 1 (varies)
  ↓
[LM Call 6] Execute step 2 (varies)
  ...
Total: 10-30 seconds for simple task, 50,000-200,000 tokens
```

### Token Waste Sources
1. **Redundant Classification** — Task type classified in Call 1, re-inferred in Calls 2-3
2. **Sequential Routing** — Each step waits for previous LM output (can't parallelize)
3. **Per-Step Planning** — Each execution step re-plans locally instead of using global plan
4. **No Result Caching** — Identical tasks run identical LM calls again
5. **Late-Binding Decisions** — Decisions made at execution time instead of upfront

---

## Solution Architecture: "Decision Pipeline with Cache Layer"

### Phase 1: Unified Initial Analysis (1 LM Call)

**Single upfront LM call that answers ALL structural questions:**

```python
class InitialAnalysisRequest:
    """Replaces LM Calls 1-3 above"""
    
    task: str
    context: dict  # existing state
    
    # LM response
    classification: {
        "task_type": "code_generation|data_analysis|tool_call|etc",
        "complexity": "simple|moderate|complex",
        "requires_files": bool,
        "requires_network": bool,
        "engine_preference": "claude|gemini|local",
        "confidence": 0.85,  # routing confidence
    }
    
    entities: {
        "files": [{"path": "...", "purpose": "input|output|reference"}],
        "tools": ["tool1", "tool2"],
        "external_apis": ["api1"],
        "environment_vars": ["VAR1"],
    }
    
    global_plan: {
        "steps": [
            {"step": 1, "action": "...", "depends_on": [], "can_parallelize": ["step2"]},
            {"step": 2, "action": "...", "depends_on": [], "can_parallelize": ["step1"]},
            {"step": 3, "action": "...", "depends_on": ["step1", "step2"]},
        ],
        "estimated_tokens": 45000,
        "estimated_duration_s": 12,
        "fallback_strategy": "...",
    }
    
    cache_key: str  # hash(task) for reuse
    ttl_seconds: 300  # 5 min cache
```

**LM Prompt (Single Call):**
```
Analyze this task completely. Output:
1. Task classification (type, complexity, engine choice)
2. Entity extraction (files, tools, APIs needed)
3. Global execution plan (parallel-safe steps, fallback)
4. Estimated tokens and duration

Task: [user input]
Context: [existing state]

CRITICAL: You output ONCE. All subsequent steps use YOUR decisions.
Ensure completeness: missing entities = execution failure.
```

### Phase 2: Distributed Execution (0-N LM Calls)

**After Initial Analysis, execution is deterministic:**

```python
# Cached decision used by ALL downstream workers
decision = cache.get(cache_key) or await initial_analysis_lm()

# Parallel execution using global plan
results = await parallel([
    execute_step(1, decision, context),
    execute_step(2, decision, context),  # Runs in parallel with step 1
])

# Step 3 depends on steps 1-2
results.append(await execute_step(3, decision, context, 
                                   upstream=[results[0], results[1]]))

# Each execute_step() uses decision.global_plan[i] — NO re-planning
```

**Per-step execution (deterministic, fewer LM calls):**
```python
async def execute_step(step_num, decision, context, upstream=None):
    step_plan = decision.global_plan["steps"][step_num]
    
    # Option A: If step_plan.action is deterministic → NO LM call
    if step_plan.action in ["read_file", "call_tool", "api_fetch"]:
        return await execute_deterministic(step_plan, context)
    
    # Option B: If step_plan.action needs reasoning → ONE scoped LM call
    # (reuse decision.global_plan as context to avoid re-planning)
    if step_plan.action == "analyze_data":
        return await execute_with_lm_step(
            step_plan, 
            context,
            global_plan=decision.global_plan,  # Prevents re-planning
            step_num=step_num,
        )
    
    # Step does NOT re-invoke classification, routing, or global planning
    # Those happened ONCE in initial_analysis_lm()
```

### Phase 3: Result Aggregation & Caching

**Central decision store (in-memory + persistent):**

```python
class DecisionCache:
    """Global store for LM decisions, shared by all workers"""
    
    cache: dict[str, InitialAnalysisRequest]  # In-memory
    persistent: SQLite | JSON  # Disk backup
    
    async def get_or_analyze(task, context):
        cache_key = hash(task)
        
        # Cache HIT
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if time.time() - cached.cached_at < cached.ttl_seconds:
                return cached  # Zero LM call
        
        # Cache MISS
        decision = await initial_analysis_lm(task, context)
        self.cache[cache_key] = decision
        self.persist(cache_key, decision)
        return decision
    
    def invalidate(cache_key, reason):
        """Invalidate if task context changes (files modified, etc)"""
        del self.cache[cache_key]
        self.log(f"Invalidated {cache_key}: {reason}")
```

---

## Implementation Strategy

### Layer 1: Initial Analysis (Replaces 3-4 sequential LM calls)

**Pseudo-code:**
```python
# OLD: 4 separate LM calls
task_type = await lm("Classify this: " + task)
entities = await lm("Extract entities from: " + task)
engine = await lm("Route to engine: " + task_type + entities)
plan = await lm("Plan steps for: " + task + engine)
# Total: ~12 seconds, 80K tokens, sequential

# NEW: 1 LM call
decision = await lm("""
    Analyze this task completely:
    [UNIFIED PROMPT asking for classification + entities + routing + plan]
""")
# Total: ~2-3 seconds, 8-12K tokens input, ~5K tokens output, DONE

cache[hash(task)] = decision  # Reuse for identical tasks
```

### Layer 2: Execution with Cached Decisions

```python
# Steps 1-3 run in parallel, guided by decision.global_plan
# No re-planning, no re-routing
# Each step: IF deterministic → execute; ELSE → 1 scoped LM call using decision context

results = await parallel_execute(decision.global_plan, context)
```

### Layer 3: Smart Invalidation

```python
# Cache invalidates only when:
# - File state changes (watch mtime)
# - Context changes (env vars, config)
# - TTL expires (5 min default)

cache.watch(files=[...])  # File change → auto-invalidate
cache.ttl = 300  # Re-analyze every 5 min (or on change)
```

---

## Token Savings Estimate

### Example: "Generate Python function + write to file + run tests"

**OLD (4 LM calls, ~6K tokens each):**
1. Classify: 6K
2. Extract entities: 6K
3. Route to engine: 5K
4. Plan: 7K
5. Generate code: 15K
6. Analyze test results: 8K
**Total: ~47K tokens, ~15 seconds**

**NEW (1 analysis + 1 generation + 1 analysis = 3 calls):**
1. Initial analysis (unified): 12K tokens (includes plan + routing + entities)
2. Generate code (using plan context): 15K tokens
3. Analyze test results (using original plan): 8K tokens
**Total: ~35K tokens, ~8 seconds**

**Savings: 26% tokens, 47% latency**

### At Scale (100 tasks/day):
- **OLD:** 4,700K tokens/day → ~$2.35/day
- **NEW:** 3,500K tokens/day → ~$1.75/day
- **Annual savings:** ~$220/year per 100 tasks/day

---

## Architecture Changes Needed

### 1. Unified Prompt Design

Create a **"Task Analysis Prompt"** that outputs structured decision:
```
# Task Analysis Prompt Template

You will analyze a task COMPLETELY in one pass.

Input:
- Task description
- Current context (files, state, config)

Output (JSON):
{
  "classification": {...},
  "entities": {...},
  "global_plan": {...},
  "estimated_tokens": N,
  "fallback_strategy": "..."
}

RULE: Your output is used by ALL downstream steps.
Missing decisions = execution failure.
Ensure completeness.
```

### 2. Decision Store (Persistent Cache)

```python
# New module: corvin/decision_cache.py

class DecisionCache:
    def __init__(self, ttl_s=300, storage="sqlite"):
        self.memory = {}
        self.storage = SQLite() if storage == "sqlite" else JSON()
        self.ttl = ttl_s
    
    async def get_or_analyze(self, task, context):
        # Implement cache logic above
        pass
```

### 3. Parallel Execution Engine

```python
# Existing: parallel_execute() in orchestration/
# Enhance to use decision.global_plan.can_parallelize hints

async def parallel_execute(plan, context):
    # Group steps by can_parallelize
    # Run groups in parallel
    pass
```

### 4. Scoped Step Execution

```python
# New: execute_step_with_context()
async def execute_step(step_num, decision, context):
    step_plan = decision.global_plan["steps"][step_num]
    
    if step_plan.is_deterministic:
        return await direct_execute(step_plan)
    else:
        return await lm_step(
            step_plan,
            global_decision=decision,  # Context to avoid re-planning
            upstream_results=...
        )
```

---

## Rollout Plan

### Phase 1 (Week 1): Unified Analysis Prompt
- Design & test InitialAnalysisRequest schema
- Create "Task Analysis Prompt"
- Integrate with existing send() flow

### Phase 2 (Week 2): Decision Cache
- Implement DecisionCache
- Wire to orchestration layer
- Test cache hit rates (target: 60%+ for repeated tasks)

### Phase 3 (Week 3): Parallel Execution
- Enhance parallel_execute() with decision guidance
- Measure latency improvement

### Phase 4 (Week 4): Scoped Step Execution
- Implement per-step LM calls using cached decisions
- Measure token reduction

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Incomplete initial analysis → execution fails | Require schema validation; test on 100+ real tasks |
| Cache invalidation lag → stale decisions | Watch file system; short TTL (5 min default) |
| Cache bloat (memory) | Evict LRU; persistent storage (SQLite) |
| Initial analysis LM call fails | Fallback to old sequential flow (graceful degradation) |

---

## Metrics to Track

1. **LM Calls per Request** — Target: 3-4 (down from 10-20)
2. **Token Usage** — Target: 30% reduction
3. **Latency** — Target: 40-50% reduction
4. **Cache Hit Rate** — Target: 60%+ for repeated/similar tasks
5. **Analysis Quality** — Measure plan accuracy (steps execute successfully)

---

## Alternative: "Thin Decision Cache" (Simpler Version)

If full implementation is too complex, start with:

```python
# Just cache the plan, skip classification re-runs
decision_cache[task_hash] = {
    "global_plan": plan,
    "entities": entities,
}

# Then each execution step: use cached plan, NO re-planning
for step in decision_cache[task_hash]["global_plan"]:
    await execute_step(step, ...)  # ONE LM call per step instead of 2-3
```

This alone saves 30-40% tokens with minimal refactoring.

---

## Conclusion

**The key insight:** LM decisions (classification, routing, planning) happen upfront ONCE, then cached & reused. Execution steps use the cached decisions, not re-derive them.

This reduces:
- **LM Calls:** 10-20 → 3-5 per request (60-75% reduction)
- **Tokens:** ~80K → ~35K per request (56% reduction)
- **Latency:** 15-30s → 5-10s per request (50-66% reduction)

**Investment:** 2-3 weeks for full implementation, but ROI is immediate: lower token costs, faster responses, better UX.

---

Generated: 2026-07-20  
Type: Architectural Concept (Pre-ADR)
