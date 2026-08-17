# Migration Guide: ExecutionContext v1 → v2

**Status:** v0.2-rc1  
**ADR:** ADR-0358  
**Audience:** Plugin authors, custom subsystem developers

---

## Overview

Context Engineering Layer v2 introduces **ExecutionContextV2** alongside v1. This is **NOT a breaking change** — both versions coexist.

**Migration Timeline:**
- **v0.2 (NOW):** v2 launches, v1 stays for metadata
- **v0.3:** v2 becomes default (v1 deprecated, still functional)
- **v1.0:** v1 removed (estimated Q4 2026)

---

## What Changed?

### ExecutionContext v1 (METADATA ONLY)

Used for routing and delegation; never updated during task execution.

```python
@dataclass
class ExecutionContext:
    engine: str           # "claude-code", "hermes", etc.
    model: str            # "opus", "sonnet", "haiku"
    delegation_path: str  # routing hint
```

**Stays unchanged.** Still used by:
- `dispatcher.route(ctx.engine, ctx.delegation_path)`
- `model_selector.resolve_os_model(ctx.model)`
- Audit trail (immutable; never updated)

### ExecutionContextV2 (EPHEMERAL TASK STATE)

**NEW** in v0.2. Replaces the old "global state" patterns.

```python
@dataclass
class ExecutionContextV2:
    task_id: str
    context_stack: ContextStack          # nested scopes
    decision_history: list[DecisionRecord]  # audit trail
    budget_remaining: float
    model: str                            # mutable
    strategy: str                         # mutable
    strategy_confidence: float
    guidance_overrides: dict              # mid-task updates
    checkpoints: list[dict]               # memory snapshots
```

**NEW features:**
- Updatable during execution (guidance, strategy changes)
- Nested scoping (task → worker → file)
- Immutable audit trail integrated
- Shared by all 13 subsystems via ContextAPI

---

## Migration Decision Tree

### Are You...

#### 1. **Using ExecutionContext only for routing?**

**→ No migration needed.** Keep using v1.

```python
# This still works exactly as before
ctx = ExecutionContext(engine="claude-code", model="haiku")
dispatcher.route(ctx.engine, ctx.delegation_path)
```

#### 2. **Storing global task state (budget, strategy, errors)?**

**→ Migrate to ContextAPI.** Stop storing in globals; use shared ExecutionContextV2 instead.

**Before (v1 pattern):**
```python
# BAD: Global state scattered everywhere
_current_budget = 500
_current_strategy = "decompose"
_error_log = []

class MySubsystem(Subsystem):
    def handle_request(self, payload):
        global _current_budget
        _current_budget -= 10
        if _error_log:
            self._apply_recovery()
```

**After (v2 pattern):**
```python
# GOOD: All subsystems use ContextAPI
class MySubsystem(Subsystem):
    def startup(self, hub):
        self.context_api = ContextAPI("my_subsystem", hub.context_bus)
    
    async def handle_request(self, payload):
        # Query atomically
        budget = self.context_api.query_context("budget_remaining")
        
        # Update atomically (broadcasts to all subsystems)
        self.context_api.update_context(budget_remaining=budget - 10)
        
        # Record decision (audit trail)
        self.context_api.record_decision(
            "action_taken",
            value="synthesis_attempted",
            reasoning="Error recovery needed"
        )
```

#### 3. **Building a custom subsystem?**

**→ Use ContextAPI from day one.** Implement `Subsystem` interface (ADR-0349).

**Skeleton:**
```python
from core.orchestration.subsystems import Subsystem
from core.context_engineering.context_api import ContextAPI

class MyCustomSubsystem(Subsystem):
    """Custom subsystem example."""
    
    async def startup(self, hub):
        """Called when Brain starts."""
        self.context_api = ContextAPI("my_custom", hub.context_bus)
    
    async def handle_request(self, request_type: str, **kwargs):
        """Handle requests from other subsystems or API."""
        if request_type == "do_something":
            result = await self._do_something(kwargs)
            return result
        raise NotImplementedError(f"Unknown request: {request_type}")
    
    async def on_event(self, event_name: str, event_data: dict):
        """React to Brain events."""
        if event_name == "strategy_failed":
            await self._on_strategy_failure(event_data)
    
    async def _do_something(self, params):
        # Use ContextAPI
        model = self.context_api.query_context("model")
        budget = self.context_api.query_context("budget_remaining")
        
        # Do work
        result = await self._work(model, budget, **params)
        
        # Update context
        self.context_api.update_context(budget_remaining=budget - 100)
        
        # Record decision
        self.context_api.record_decision(
            "subsystem_action",
            value="work_completed",
            confidence=0.9
        )
        
        return result
    
    async def _work(self, model, budget, **params):
        # Actual implementation
        return {"status": "success", "output": "..."}
    
    async def _on_strategy_failure(self, event_data):
        # React to events
        error = event_data.get("error")
        print(f"Strategy failed: {error}")
```

**Register in `corvin.yaml`:**
```yaml
brain:
  subsystems:
    - type: "custom"
      id: "my_custom_subsystem"
      class: "mypackage.MyCustomSubsystem"
      config:
        enabled: true
```

#### 4. **Want to forge tools/skills from your subsystem?**

**→ Use ForgedToolAPI / ForgedSkillAPI (ADR-0361).** Access via Hub.

```python
class MySubsystem(Subsystem):
    async def startup(self, hub):
        self.context_api = ContextAPI("my_subsystem", hub.context_bus)
        self.forged_tool_api = hub.get_api("forged_tool")
        self.forged_skill_api = hub.get_api("forged_skill")
    
    async def on_error(self, event_name, event_data):
        error = event_data.get("error")
        
        # Forge a recovery tool
        tool = await self.forged_tool_api.forge_tool(
            name=f"recover_{error}",
            description=f"Recovery strategy for {error}",
            impl=f"def recover(err): ...",
            namespace="my_subsystem",  # auto-prefixed
        )
        
        # Execute tool
        result = await self.forged_tool_api.forge_exec(
            f"my_subsystem.recover_{error}",
            {"error_data": error}
        )
        
        # Create learned skill
        skill = await self.forged_skill_api.skill_create(
            name=f"skill_{error}",
            body_md=f"# Recovery for {error}\nWhen you see {error}, try...",
            namespace="my_subsystem",
        )
        
        # Grade skill (auto-grading will handle most cases)
        await self.forged_skill_api.skill_grade(
            f"my_subsystem.skill_{error}",
            score=0.9 if result["success"] else 0.1
        )
```

---

## Subsystem Updates (if needed)

### Affected Subsystems in v0.2

All 13 Brain subsystems have been updated to use ContextAPI. If you maintain a custom subsystem:

| Subsystem | v1 Pattern | v2 Pattern |
|---|---|---|
| LoopEngineer | Direct `_current_strategy` variable | `context_api.query_context("strategy")` |
| CostController | Direct `_budget` variable | `context_api.update_context(budget_remaining=...)` |
| HealthMonitor | Direct `_error_rate` tracking | Subscribes to `error` events; updates via ContextAPI |
| SafetyValidator | Direct `_forbidden_actions` set | `context_api.query_context("guidance_overrides")` |
| StrategyAdvisor | Direct `_success_history` dict | ContextAPI + LearningEngine integration |

### Code Pattern: v1 → v2

**v1 (Global State):**
```python
class OldSubsystem(Subsystem):
    def __init__(self):
        self._strategy = None
        self._budget = 1000
        self._errors = []
    
    async def handle_request(self, request):
        self._budget -= 100
        if self._budget < 0:
            raise BudgetExhausted()
        self._errors.append(request.get("error"))
```

**v2 (ContextAPI):**
```python
class NewSubsystem(Subsystem):
    async def startup(self, hub):
        self.context_api = ContextAPI("new_subsystem", hub.context_bus)
    
    async def handle_request(self, request):
        budget = self.context_api.query_context("budget_remaining")
        if budget < 100:
            raise BudgetExhausted()
        
        self.context_api.update_context(budget_remaining=budget - 100)
        self.context_api.record_decision(
            "budget_deducted",
            value=100,
            confidence=1.0
        )
```

---

## Testing: v1 ↔ v2 Coexistence

**Test suite:** `tests/test_context_engineering_v2/test_v1_v2_compat.py`

Run to verify v1 still works:

```bash
# All v1 functionality intact
pytest tests/test_context_engineering_v2/test_v1_v2_compat.py -v

# Expected output:
# test_v1_metadata_routing PASSED
# test_v1_execution_context_init PASSED
# test_v1_audit_trail_immutable PASSED
# test_v1_v2_bridge_coexistence PASSED
```

---

## ContextBridge: v1 → v2 Conversion

If you need to convert a v1 ExecutionContext to v2:

```python
from core.orchestration.context_bridge import ContextBridge

# Start with v1
ctx_v1 = ExecutionContext(engine="claude-code", model="opus")

# Convert to v2
ctx_v2 = ContextBridge.v1_to_v2(
    ctx_v1=ctx_v1,
    task_id="task-001",
    budget=500,
    task_type="code_review"
)

# ctx_v2 is now ExecutionContextV2 with:
# - task_id, budget, model from task metadata
# - strategy loaded from PROJECT/GLOBAL memory
# - decision_history initialized
# - guidance_overrides empty
```

---

## Breaking Changes: NONE

✅ **v1 APIs unchanged.** All existing code continues to work.

```python
# These still work exactly as before
ctx = ExecutionContext(engine="claude-code", model="haiku")
dispatcher.route(ctx.engine, ctx.delegation_path)
audit_trail.record(ctx)  # immutable
```

---

## Deprecation Timeline

| Version | Status | v1 Behavior |
|---|---|---|
| **v0.2** (NOW) | Current | v1 works; v2 preferred for new code |
| **v0.3** | Next | v1 still works; deprecation warning added |
| **v1.0** | Future (Q4 2026) | v1 removed; v2 required |

---

## FAQ

**Q: Do I need to migrate my code immediately?**  
A: No. v0.2 runs both v1 and v2. Migrate at your convenience (v0.3 will warn; v1.0 will require it).

**Q: Will v1 code break when v0.3 ships?**  
A: No. Deprecation warnings added; functionality unchanged. Plenty of time to migrate.

**Q: How do I test my subsystem with v2?**  
A: Write tests that use `ContextAPI` and `hub.get_api()`. See `tests/test_context_engineering_v2/test_subsystem_adoption.py` for examples.

**Q: Can I use both v1 and v2 in the same subsystem?**  
A: Yes. Use v1 for routing metadata; use v2 for state management. ContextBridge handles conversion if needed.

**Q: How do I access ExecutionContextV2 at runtime?**  
A: Via `ContextAPI`:
```python
self.context_api = ContextAPI("my_subsystem", hub.context_bus)
budget = self.context_api.query_context("budget_remaining")
```

**Q: How do I create a custom subsystem from scratch?**  
A: Use the skeleton above. Key steps:
1. Inherit from `Subsystem` (ADR-0349)
2. Implement `startup(hub)`, `handle_request()`, `on_event()`
3. Fetch `ContextAPI` and `ForgedToolAPI`/`ForgedSkillAPI` in `startup()`
4. Register in `corvin.yaml`

**Q: What if I want to keep using globals?**  
A: Not recommended; will break in v1.0. ContextAPI is safer (atomic, audited, shared). But v1 still works for now.

---

## Resources

- **ADR-0358:** [Context Engineering Layer v2](../../Corvin-ADR/decisions/ADR-0358-context-engineering-layer-v2.md)
- **ADR-0349:** [Subsystem Interface Contract](../../Corvin-ADR/decisions/ADR-0349-plugin-interface-contract.md)
- **ADR-0361:** [Extensibility APIs](../../Corvin-ADR/decisions/ADR-0361-forged-tool-skill-extensibility-contract.md)
- **Operator Guide:** [Context Engineering v2 Quick Start](../operator-quickstart/context-engineering-v2.md)
- **Examples:** `core/orchestration/subsystems/` (see LoopEngineer, CostController, HealthMonitor)
- **Tests:** `tests/test_context_engineering_v2/` (11 test files, 182+ tests)
