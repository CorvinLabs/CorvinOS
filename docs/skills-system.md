# Skills System: The Core Unit

A **Skill** is a versioned, auditable program that makes a decision or performs an action.

![Skill Lifecycle](docs/assets/skill-lifecycle.svg)

---

## What is a Skill?

Think of a Skill like an "app" on your phone:
- Each app has a name, version, author
- Each app does one job (routing, prioritizing, caching, etc.)
- You can update one app without restarting your phone
- All app actions are logged (for debugging + compliance)

A Skill is exactly that for CorvinOS.

---

## Skill Lifecycle

1. **Registration** → Skill registered in registry (immutable once registered)
2. **Execution** → Input → Skill.execute() → Output (+ audit event)
3. **Audit** → Event logged to immutable audit trail
4. **Feedback** → User rates the decision (good/bad/other)
5. **Optimization** → Optimizer reads feedback → tunes Skill config
6. **Learning** → Confidence score improves over time

---

## Skill Metadata

Every Skill has:

```python
class Skill:
    id: str                    # "os.delegation_router"
    version: str               # "1.2" (semver)
    name: str                  # "Delegation Router"
    description: str           # "Routes tasks by complexity"
    origin: str                # "builtin" | "vetted" | "community"
    boot_layer: str            # "compliance" | "core" | "bundled" | "installed"
    dependencies: list[str]    # ["os.vibe_engineering@0.3"]
    required_checks: list[str] # ["consent_gate", "audit_trail"]
    config: dict               # Default configuration
```

---

## Skill Interface

Every Skill implements:

```python
def execute(self, input: dict) -> dict:
    """Execute the Skill's logic."""
    # Deterministic Python
    if input["complexity"] > self.config["threshold"]:
        return {"engine": "claude-opus-5"}
    else:
        return {"engine": "claude-haiku-4-5"}
```

**Audit integration is automatic:**
- Input + output logged
- LoM binding added (code identity proof)
- Hash-chain verified
- Tenant isolation enforced

---

## Creating a Custom Skill

```python
from core.skills.skill_interface import Skill
from core.skills.skill_registry_phase1 import skill_registry

class VotingRouterSkill(Skill):
    id = "custom.voting_router"
    version = "1.0"
    description = "Routes by majority vote of three sub-skills"
    origin = "community"
    dependencies = ["os.delegation_router@1.2", "os.vibe_engineering@0.3"]
    
    def execute(self, input: dict) -> dict:
        # Call other Skills (composition)
        vote1 = skill_registry.execute("os.delegation_router", input)
        vote2 = skill_registry.execute("os.vibe_engineering", input)
        # ... voting logic ...
        return result

# Register it
skill_registry.register(VotingRouterSkill())

# Use it
result = skill_registry.execute("custom.voting_router", input)
```

---

## Skill Composition

Skills can call other Skills (like Python imports):

![Skill Composition Tree](docs/assets/skill-composition-tree.svg)

```python
# os.context_adapter composes routing + vibe
class ContextAdapter(Skill):
    dependencies = ["os.delegation_router", "os.vibe_engineering"]
    
    def execute(self, input: dict) -> dict:
        # Call Skill 1
        vibe_priority = skill_registry.execute("os.vibe_engineering", input)
        
        # Use result in Skill 2
        input_with_priority = {**input, "priority": vibe_priority}
        engine = skill_registry.execute("os.delegation_router", input_with_priority)
        
        return {"engine": engine, "priority": vibe_priority}
```

**Benefits:**
- ✅ Single change propagates (update os.vibe_engineering → context_adapter sees it)
- ✅ Versioning per-Skill (no monolithic updates)
- ✅ Reusable components (write once, compose many ways)
- ✅ DAG validation (no circular references)

---

## Versioning & Semver

Skills follow semantic versioning:

```
os.vibe_engineering@0.1  → os.vibe_engineering@0.2  (backward compatible)
                             (bug fix, backward compatible)

os.vibe_engineering@0.2  → os.vibe_engineering@1.0  (breaking change)
                             (new algorithm, breaking API)
```

**Rollback is instant:**

```bash
# Rollback to previous version
corvin skills rollback os.vibe_engineering --to 0.2

# Downtime: 0 seconds
# Old version is pinned again
```

---

## Skill Registry API

```python
# Register a Skill
registry.register(MySkill())

# Execute a Skill
result = registry.execute("my.skill", input={"key": "value"})

# List all Skills
skills = registry.list_all()

# Get one Skill
skill = registry.get("os.vibe_engineering")

# Check if enabled
is_enabled = registry.is_enabled("os.vibe_engineering")

# Get confidence + feedback
confidence = registry.get("os.vibe_engineering").get_confidence()
feedback = registry.get("os.vibe_engineering").get_feedback_history()
```

---

## Audit Trail (Automatic)

Every Skill execution is logged:

```json
{
  "event_type": "SKILL_EXECUTED",
  "skill_id": "os.delegation_router",
  "skill_version": "1.2",
  "input": {"complexity": 10, "task_type": "analysis"},
  "output": {"engine": "claude-opus-5"},
  "timestamp": "2026-09-02T12:34:56.789Z",
  "tenant_id": "_default",
  "lom": "os_delegation_router.py:156",
  "lom_hash": "sha256(...)",
  "hash": "sha256(...)",
  "prev_hash": "sha256(...)"
}
```

**No configuration needed.** Audit is built-in.

---

## Examples

### Example 1: Simple Routing Skill

```python
class SimpleRouter(Skill):
    id = "examples.simple_router"
    version = "1.0"
    
    def execute(self, input: dict) -> dict:
        if input["urgency"] == "high":
            return {"engine": "claude-opus-5"}
        else:
            return {"engine": "claude-haiku-4-5"}

registry.register(SimpleRouter())
result = registry.execute("examples.simple_router", {"urgency": "high"})
# Output: {"engine": "claude-opus-5"}
```

### Example 2: Composable Skill (calls other Skills)

See [Composable Programs](composable-programs.md) for detailed examples.

### Example 3: Tracking Confidence

See [Learning Loop](learning-loop.md) for feedback integration.

---

## FAQ

**Q: What if a Skill execution fails?**  
A: Error is caught, logged to audit trail, and re-raised. Callers handle the exception.

**Q: Can I update a Skill without restarting CorvinOS?**  
A: Yes! Register a new version (v1.1). Pinned dependencies use the old version until you explicitly upgrade.

**Q: What if two Skills depend on each other (circular)?**  
A: Dependency DAG is validated at registration time. Circular dependencies are rejected.

**Q: Is there a way to disable a Skill?**  
A: Yes, but only for bundled/installed Skills (not compliance meta-Skills). Use registry.disable().

**Q: How do I know if my Skill is being called?**  
A: Check audit trail: `corvin audit trace skill my.skill --task=<task_id>`. Must see SKILL_EXECUTED events.

**Q: Can multiple Skills with the same ID exist?**  
A: No. `id` is globally unique. You can have multiple versions (v1.0, v1.1), but only one is active.

---

## Next Steps

- **[Composable Programs](composable-programs.md)** — Write Skills that call other Skills
- **[Skills API Reference](skills-api-reference.md)** — Full API reference
- **[Learning Loop](learning-loop.md)** — How Skills improve through feedback
