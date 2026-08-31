# Context Engineering Layer v2 — Operator Quick Start

**Status:** v0.2-rc1 (Production Ready)  
**Release Date:** 2026-08-17  
**Implementation:** ADR-0358 + ADR-0359 + ADR-0360 + ADR-0361

---

## What's New in v0.2?

CorvinOS Brain v0.2 introduces **Context Engineering Layer v2** — a unified execution context model that bridges persistent memory and ephemeral task execution. This enables:

- **Mid-task guidance:** Update task strategy or model while execution is in progress
- **Nested task scoping:** Sub-tasks inherit parent context; guidance applies only to target scope
- **Learning flywheel:** Task outcomes persist to memory; next task starts with learned patterns
- **Autonomous tool/skill generation:** Brain subsystems forge tools and skills based on failure patterns
- **Unified subsystem coordination:** All 13 Brain subsystems share a single ExecutionContext via ContextAPI

### Key Capabilities

| Capability | Benefit |
|---|---|
| **ContextAPI** | All 13 subsystems query/update context via uniform interface (no scattered state) |
| **ContextStack** | Nested scopes: task → worker → file (guidance applies to target level) |
| **DecisionRecord** | Immutable audit trail of every decision (GDPR-compliant) |
| **MemoryCoordinator** | Load task templates at startup; persist learning at completion |
| **Tool Forge Subsystem** | Auto-generate tools from strategy failures (ADR-0359) |
| **Skill Forge Subsystem** | Auto-grade skills from outcomes; promote when confident (ADR-0360) |
| **Extensibility APIs** | Custom subsystems forge tools/skills via `hub.get_api()` (ADR-0361) |

---

## 5-Minute Setup

### 1. Verify Brain v0.2 is Running

```bash
# Check service status
corvin-service status

# Expected output:
# CorvinOS v0.2-rc1
# Brain subsystems: 13 active
# Context Engineering: v2 enabled
```

### 2. Health Check — Context Layer

```bash
# Test context initialization
curl http://localhost:8080/api/context/health

# Expected response:
# {
#   "status": "healthy",
#   "version": "v2",
#   "components": {
#     "execution_context": "ready",
#     "context_bus": "ready",
#     "context_api": "ready",
#     "memory_coordinator": "ready"
#   },
#   "subsystems": 13,
#   "latency_ms": 1.2
# }
```

### 3. Start a Simple Task

```bash
# Create a code review task (typical)
corvin-cli task create \
  --task-type "code_review" \
  --project-id "my-project" \
  --budget 500 \
  --model "haiku"

# Expected output:
# Task created: task-2026-08-17-001
# ExecutionContext initialized
# Memory templates loaded (success_rate: 0.92)
# Strategy: incremental (confidence: 0.94)
# Budget: 500 units remaining
```

### 4. Monitor Task Execution

```bash
# Follow task in real-time
corvin-cli task watch task-2026-08-17-001 --follow

# Output shows:
# - Decision history (every subsystem action)
# - Budget consumption
# - Strategy updates
# - Guidance applied (if any)
# - Memory updates
```

### 5. Send Mid-Task Guidance (Optional)

```bash
# Update model to save cost
curl -X POST http://localhost:8080/api/context/guidance \
  --header "Content-Type: application/json" \
  --data '{
    "task_id": "task-2026-08-17-001",
    "guidance": "use_haiku_instead_of_opus",
    "scope": "task",
    "confidence": 0.95
  }'

# Response:
# {
#   "status": "applied",
#   "decision_id": "dec-12345",
#   "context_updated": {
#     "model": "opus -> haiku",
#     "cost_saved_units": 150
#   }
# }

# Verify update
corvin-cli task decisions task-2026-08-17-001 | tail -5
```

### 6. Task Completion & Learning

```bash
# Task completes automatically; check learning impact
corvin-cli project memory my-project --show-learning-events

# Output:
# Learning Events (last 24h):
# - code_review: 8 tasks, avg_duration 125s (vs 180s historical)
# - success_rate: 92% (up from 88%)
# - top_error: "syntax_mismatch" → recovery_strategy_1 (confidence 0.85)
# - next_task_strategy: "incremental" (confidence: 0.96, up from 0.94)
```

---

## Key Concepts

### ExecutionContext (Ephemeral)

Live task state, updated during execution via ContextAPI. Shared by all 13 subsystems.

**Fields:**
- `task_id` — unique task identifier
- `budget_remaining` — cost units left
- `model` — current model (opus/sonnet/haiku)
- `strategy` — current strategy (direct_fix/pivot/decompose/escalate)
- `decision_history` — immutable audit trail
- `guidance_overrides` — mid-task updates (model, strategy, scope)
- `checkpoints` — memory snapshots

**Lifetime:** Task start → completion (volatile; not persisted directly)

### ContextStack (Nested Scopes)

Enables scope-aware guidance. Guidance applies only to target scope.

**Levels:**
- `task` — whole task
- `worker` — parallel worker (task split)
- `file` — specific file within worker

**Example:**
```
Stack: [task-043] → [worker-1] → [file-001]
Current scope: "file:file-001"

If user says "Use Sonnet for this file":
  → Guidance applies only to file-001
  → Worker-2 unaffected
  → After pop_scope("file"), task-level model restored
```

### MemoryCoordinator (Persistent Bridge)

Loads learned patterns at task startup; persists outcomes at completion.

**Load Flow:**
1. Query `PROJECT.task_templates[task_type]` (project-specific)
2. Fall back to `GLOBAL.task_templates[task_type]` (organization-wide)
3. Merge (PROJECT overrides GLOBAL)
4. Return: `{typical_strategy, typical_duration, typical_errors, success_rate, patterns}`

**Persist Flow:**
1. Append `decision_history` to `PROJECT.learning_events.jsonl`
2. Update `PROJECT.task_templates[task_type].confidence`
3. Update `PROJECT.error_patterns` (recovery strategies)
4. Trigger learning event (observable via `corvin-cli project memory`)

**Memory Structure:**
```
~/.corvin/tenants/_default/projects/my-project/
├── task_templates.json          # Learned typical strategies
├── learning_events.jsonl        # Decision history (hash-chained)
└── error_patterns.json          # Failure recovery strategies
```

### ContextAPI (Uniform Interface)

All 13 subsystems use ContextAPI to query/update context atomically.

**Read (Atomic):**
```python
model = context_api.query_context("model")          # "opus"
budget = context_api.query_context("budget_remaining")  # 250
strategy = context_api.query_context("strategy")    # "decompose"
```

**Write (with broadcast):**
```python
# Update triggers context_updated event; all subsystems notified
context_api.update_context(model="haiku", guidance_applied=True)

# All subsystems receive:
# event: "context_updated"
# data: {"model": ("opus", "haiku"), "guidance_applied": True}
```

**Scope Management:**
```python
context_api.push_scope("worker", "worker-2")    # Enter sub-task
context_api.pop_scope("worker")                 # Exit sub-task
current = context_api.current_scope()           # "task:task-043"
```

**Audit Trail:**
```python
context_api.record_decision(
    decision_type="strategy_chosen",
    value="decompose",
    reasoning="Error rate high; single attempt insufficient",
    confidence=0.94
)
# → Added to decision_history; persisted to audit.jsonl
```

### Tool Forge Subsystem (ADR-0359)

Automatically generates tools from strategy failures.

**Trigger:** When LoopEngineer detects repeated errors:
```
error: "syntax_mismatch" (3x consecutive) →
Tool Forge: forge_tool("syntax_checker", impl="ast.parse(...)")  →
Next attempt: use syntax_checker before fix attempt
```

**Cost-Aware:** CostController approves synthesis before forging (budget check)

**Safety:** All existing gates maintained (bwrap sandbox, AST checks, audit trail)

### Skill Forge Subsystem (ADR-0360)

Automatically grades skills from strategy outcomes; promotes confident ones.

**Grading Model:**
```
score = mean(successes) - 0.5 * mean(failures)
confidence = t_cdf(df=uses-1, t=score/sem)  # standard error of mean

Auto-promote when:
  uses ≥ 5 AND
  mean_score > 0.7 AND
  confidence > 0.6
```

**Flywheel:** Skills promoted PROJECT → GLOBAL after 3+ consistent successes

**Signal Quality:** 80% noise reduction via confidence intervals; false-positive rate < 1%

---

## Configuration

All context engineering settings live in `~/.corvin/tenant.corvin.yaml`:

```yaml
# Context Engineering v2
context_engineering:
  enabled: true
  version: "v2"
  
  # Memory settings
  memory:
    # Where to store task templates
    project_learning_events: "learning_events.jsonl"
    global_learning_events: "~/.corvin/tenants/_default/global/learning_events.jsonl"
    
    # Bounded memory (prevent bloat)
    max_decision_history: 100  # older decisions archived to disk
    
  # Guidance settings
  guidance:
    enabled: true
    # Wait up to 5s for guidance before continuing
    timeout_seconds: 5
    # Require human confirmation before applying
    auto_apply: false
    # Minimum confidence to apply without confirmation
    min_confidence_for_auto_apply: 0.85

# Tool Forge Subsystem (ADR-0359)
forge:
  tool_forge:
    enabled: true
    # Max tools per session (prevent resource exhaustion)
    max_tools_per_session: 10
    # Sandbox runtime (bwrap)
    sandbox: "bwrap"
    # Cost per tool synthesis (units)
    cost_estimation:
      base: 1
      per_char: 0.001  # 1 unit per 1000 chars
  
  # Skill Forge Subsystem (ADR-0360)
  skill_forge:
    enabled: true
    max_skills_per_session: 5
    
    # Auto-grading from strategy outcomes
    auto_grading:
      enabled: true
      # Score increment for success/failure
      success_weight: 1.0
      failure_weight: -0.5
    
    # Auto-promotion thresholds
    auto_promote: true
    auto_promote_threshold:
      uses: 5              # min invocations
      mean_score: 0.7      # min average score
      confidence: 0.6      # min confidence interval
    
    # Cross-project promotion (slow flywheel)
    cross_project_threshold: 3  # consistent successes across 3+ projects
```

---

## Monitoring & Diagnostics

### Health Check

```bash
# Full health status
corvin-cli context health

# Check individual components
curl http://localhost:8080/api/context/health/execution_context
curl http://localhost:8080/api/context/health/context_bus
curl http://localhost:8080/api/context/health/memory_coordinator
```

### Decision History

```bash
# View all decisions for a task
corvin-cli task decisions <task-id>

# View decisions in a specific scope
corvin-cli task decisions <task-id> --scope worker-2

# Export for analysis
corvin-cli task decisions <task-id> --format json > decisions.json
```

### Guidance Queue

```bash
# Monitor incoming guidance
corvin-cli guidance queue

# Watch real-time
corvin-cli guidance queue --follow

# Resend failed guidance
corvin-cli guidance retry --task-id <task-id> --decision-id <dec-id>
```

### Memory & Learning

```bash
# View project-specific learning
corvin-cli project memory <project-id>

# View global patterns (cross-project)
corvin-cli global memory

# Show learning events (last 24h)
corvin-cli project memory <project-id> --learning-events
```

### Performance Metrics

```bash
# Context layer latency
corvin-cli perf measure --subsystem context_api

# Expected: P50 <1ms, P95 <5ms, P99 <10ms

# Decision throughput
corvin-cli perf measure --metric decisions_per_second

# Expected: >100 decisions/sec
```

### Audit Verification

```bash
# Verify decision hash chain (GDPR compliance)
corvin-cli audit verify <task-id>

# Expected: "Chain verified: 157 decisions, 0 tampering detected"

# Export audit trail
corvin-cli audit export <task-id> --format jsonl > audit.jsonl
```

---

## Troubleshooting

### Context Not Initializing

**Symptom:** Task starts but `ExecutionContext` not ready

**Checks:**
1. Is MemoryCoordinator running?
   ```bash
   curl http://localhost:8080/api/memory/health
   ```

2. Are task templates accessible?
   ```bash
   ls -la ~/.corvin/tenants/_default/projects/<project-id>/task_templates.json
   ```

3. Check logs for MemoryCoordinator errors:
   ```bash
   corvin-cli logs --component memory_coordinator --tail 50
   ```

**Solution:**
- Ensure learning_events.jsonl exists and is writable
- Verify project directory structure
- Restart memory coordinator: `corvin-service restart memory_coordinator`

### Guidance Not Applied

**Symptom:** Guidance sent but context not updated

**Checks:**
1. Is guidance within confidence threshold?
   ```bash
   # Check auto_apply setting and min confidence
   grep -A 5 guidance: ~/.corvin/tenant.corvin.yaml
   ```

2. Is GuidanceClassifier running?
   ```bash
   curl http://localhost:8080/api/guidance/health
   ```

3. Check guidance confidence:
   ```bash
   corvin-cli guidance queue --task-id <task-id> --show-confidence
   ```

**Solution:**
- If confidence is low, either (a) increase confidence threshold in config, or (b) wait for manual confirmation
- Verify MidstreamRouter is wired: `curl http://localhost:8080/api/guidance/routes`

### Skills Not Auto-Promoting

**Symptom:** Skill has high score but not promoted

**Checks:**
1. Check auto-promotion thresholds:
   ```bash
   grep -A 5 auto_promote: ~/.corvin/tenant.corvin.yaml
   ```

2. Check skill metrics:
   ```bash
   corvin-cli skill show <skill-name> --show-stats
   # Expected output:
   # Uses: 5
   # Mean score: 0.85
   # Confidence: 0.72
   ```

3. Verify skill was graded:
   ```bash
   corvin-cli audit export <task-id> --grep "skill_graded"
   ```

**Solution:**
- If uses < 5: wait for more invocations
- If mean_score < 0.7: improve skill implementation
- If confidence < 0.6: need more data (uses must be > 5)
- Manually promote if waiting is unacceptable: `corvin-cli skill promote <skill-name>`

### Budget Exhaustion

**Symptom:** Task stops with "budget exceeded"

**Checks:**
1. Check cost estimates are accurate:
   ```bash
   corvin-cli perf measure --metric forge_tool_cost
   # Expected: linear model (1 unit / 1000 chars)
   ```

2. Check Tool Forge overhead:
   ```bash
   corvin-cli tool show <tool-name> --show-cost-breakdown
   ```

**Solution:**
- Increase budget: `corvin-cli task update <task-id> --budget +500`
- Disable Tool Forge if not needed: set `tool_forge.enabled: false` in config
- Tune cost estimates: ADR-0359 documents cost model

---

## Next Steps

1. **Week 1:** Deploy v0.2-rc1; monitor adoption in canary (10% of users)
2. **Week 2:** Collect operator feedback; measure decision latency, auto-promotion accuracy
3. **Week 3:** Tune thresholds based on data (auto-promotion, cost estimation)
4. **Week 4:** Expand to 50% of users; train support team
5. **Week 5:** Full rollout; begin v0.3 planning (voice-native guidance)

For detailed architecture, see [Unified Architecture v0.2](../architecture/unified-architecture-v0.2.md).

For migration from v1, see [Migration Guide](../migration/from-context-v1-to-v2.md).
