# Skills API Reference

Complete API documentation for the Skills Registry, execution model, and composition.

---

## Registry API

### Execute a Skill

```python
from core.skills.skill_registry_phase1 import skill_registry

result = skill_registry.execute(skill_id, input, timeout=30)

# Arguments:
#   skill_id (str): "os.delegation_router"
#   input (dict): {"complexity": 10, "task_type": "analysis"}
#   timeout (int): Max seconds before timeout (default: 30)
#
# Returns:
#   dict: Skill output (structure defined by Skill)
#
# Raises:
#   SkillNotFound: Skill ID not registered
#   SkillExecutionError: Skill.execute() failed
#   SkillTimeoutError: Execution exceeded timeout
```

**Example:**
```python
result = skill_registry.execute("os.delegation_router", {
    "complexity": 10,
    "task_type": "analysis"
})
print(result)  # {"engine": "claude-opus-5"}
```

---

### List All Skills

```python
skills = skill_registry.list_all()

# Returns:
#   list[Skill]: All registered Skill objects
#
# Example output:
#   [
#     Skill(id="os.delegation_router", version="1.2"),
#     Skill(id="os.vibe_engineering", version="0.3"),
#     Skill(id="os.context_adapter", version="2.0"),
#   ]
```

---

### Get Single Skill

```python
skill = skill_registry.get(skill_id)

# Arguments:
#   skill_id (str): "os.vibe_engineering"
#
# Returns:
#   Skill object (or None if not found)
#
# Example:
#   skill = skill_registry.get("os.vibe_engineering")
#   print(skill.version)  # "0.3"
#   print(skill.dependencies)  # ["os.delegation_router@1.2"]
```

---

### Register a New Skill

```python
skill_registry.register(skill: Skill)

# Arguments:
#   skill: Skill instance (must implement Skill interface)
#
# Raises:
#   SkillIDConflict: Skill.id already exists
#   InvalidSkillMetadata: Missing required fields
#   CircularDependencyError: Circular dependency detected
#
# Example:
#   class MySkill(Skill):
#       id = "my.custom_skill"
#       version = "1.0"
#       def execute(self, input: dict) -> dict:
#           return {"result": "ok"}
#   
#   skill_registry.register(MySkill())
```

---

### Check if Enabled

```python
is_enabled = skill_registry.is_enabled(skill_id)

# Returns:
#   bool: True if Skill is enabled and callable
#
# Example:
#   if skill_registry.is_enabled("os.vibe_engineering"):
#       result = skill_registry.execute("os.vibe_engineering", input)
```

---

### Disable a Skill

```python
skill_registry.disable(skill_id, reason: str = "")

# Arguments:
#   skill_id: "os.vibe_engineering"
#   reason: Optional reason for disablement
#
# Raises:
#   CannotDisableMetaSkill: Compliance Skills cannot be disabled
#
# Audit Event:
#   SKILL_DISABLED {skill_id, reason, requestor, timestamp}
```

---

## Skill Interface

All Skills must implement:

```python
from core.skills.skill_interface import Skill

class MySkill(Skill):
    # Required metadata
    id: str = "my.skill"
    version: str = "1.0"
    name: str = "My Custom Skill"
    description: str = "Does something useful"
    origin: str = "community"  # "builtin" | "vetted" | "community"
    boot_layer: str = "bundled"  # "compliance" | "core" | "bundled" | "installed"
    
    # Optional metadata
    dependencies: list[str] = []  # ["os.vibe_engineering@0.3"]
    required_checks: list[str] = []  # ["consent_gate", "audit_trail"]
    config: dict = {}  # Default configuration
    
    # Required method
    def execute(self, input: dict) -> dict:
        """Execute the Skill's logic.
        
        Args:
            input: Skill input (structure defined by caller)
        
        Returns:
            dict: Skill output (structure defined by Skill)
        
        Raises:
            ValueError: Invalid input
            RuntimeError: Execution error
        """
        if "required_field" not in input:
            raise ValueError("Missing required_field")
        
        # Deterministic Python
        result = self.process(input)
        
        return {"result": result}
    
    # Helper method (optional)
    def process(self, input: dict):
        return f"Processed: {input}"
```

---

## Skill Composition

Compose Skills using `registry.execute()`:

```python
from core.skills.skill_registry_phase1 import skill_registry

class ContextAdapterSkill(Skill):
    id = "os.context_adapter"
    version = "2.0"
    dependencies = ["os.delegation_router@1.2", "os.vibe_engineering@0.3"]
    
    def execute(self, input: dict) -> dict:
        # Call Skill 1
        vibe_result = skill_registry.execute("os.vibe_engineering", input)
        
        # Use result in Skill 2
        enriched_input = {**input, "vibe": vibe_result}
        routing_result = skill_registry.execute(
            "os.delegation_router",
            enriched_input
        )
        
        return {
            "engine": routing_result["engine"],
            "vibe": vibe_result,
            "context": enriched_input
        }
```

**Dependency DAG Validation:**
- Circular dependencies rejected at registration
- Topological sort ensures correct execution order
- Missing dependencies raise error at runtime

---

## Audit Integration (Automatic)

Every Skill execution is logged automatically:

```python
result = skill_registry.execute("os.vibe_engineering", input)

# Automatically emits:
# {
#   "event_type": "SKILL_EXECUTED",
#   "skill_id": "os.vibe_engineering",
#   "skill_version": "0.3",
#   "input": input,
#   "output": result,
#   "timestamp": "2026-09-02T12:34:56.789Z",
#   "tenant_id": "_default",
#   "lom": "os_vibe_engineering.py:156",
#   "hash": "sha256(...)",
#   "prev_hash": "sha256(...)"
# }

# No additional configuration needed
```

---

## Confidence & Feedback API

```python
# Get confidence score
skill = skill_registry.get("os.vibe_engineering")
confidence = skill.get_confidence()
# Returns: float (0.0–1.0)

# Get feedback history
feedback = skill.get_feedback_history(limit=10)
# Returns: list[dict] sorted by timestamp (newest first)

# Example:
# [
#   {"timestamp": "2026-09-02T15:45:00Z", "type": "outcome", "signal": "yes"},
#   {"timestamp": "2026-09-02T14:30:00Z", "type": "metric", "signal": {"latency_ms": 42}},
# ]
```

---

## Error Handling

All Skill errors are:
1. Logged to audit trail
2. Re-raised to caller
3. Not silently caught

```python
try:
    result = skill_registry.execute("os.vibe_engineering", {"bad": "input"})
except ValueError as e:
    print(f"Input validation failed: {e}")
    # Error is in audit trail at this point
except SkillExecutionError as e:
    print(f"Skill failed: {e}")
    # Operator can check audit trail for details

# In audit trail:
# {
#   "event_type": "SKILL_EXECUTED",
#   "skill_id": "os.vibe_engineering",
#   "error": "ValueError: Missing required field",
#   "error_traceback": "...",
#   "hash": "sha256(...)"
# }
```

---

## Versioning API

```python
# Get Skill version
skill = skill_registry.get("os.vibe_engineering")
version = skill.get_version()
# Returns: "0.3"

# Pin to specific version (in dependencies)
dependencies = ["os.vibe_engineering@0.3"]  # Lock to v0.3

# Rollback to previous version
corvin skills rollback os.vibe_engineering --to 0.2
# Downtime: < 30 seconds
```

---

## Deployment API (CLI)

```bash
# Deploy new version (canary 10%)
corvin skills deploy os.vibe_engineering v0.4 --canary 10%

# Scale canary
corvin skills scale os.vibe_engineering 50%

# Full deployment
corvin skills scale os.vibe_engineering 100%

# Rollback
corvin skills rollback os.vibe_engineering --to 0.3

# Disable Skill
corvin skills disable os.vibe_engineering --reason "Testing"

# List all Skills
corvin skills list

# Get Skill info
corvin skills info os.vibe_engineering

# Check confidence
corvin skills confidence os.vibe_engineering
```

---

## Configuration API

```python
# Update Skill config (via Optimizer, not manually)
skill = skill_registry.get("os.vibe_engineering")
skill.config["threshold"] = 0.65

# Audit event emitted:
# {
#   "event_type": "SKILL_CONFIG_UPDATED",
#   "skill_id": "os.vibe_engineering",
#   "param_delta": {"threshold": {"from": 0.70, "to": 0.65}},
#   "confidence_before": 0.60,
#   "confidence_after": 0.87
# }
```

---

## FAQ

**Q: What if a Skill dependency is not found?**  
A: Error raised at runtime (not at registration time). Operator must fix dependencies.

**Q: Can I call registry.execute() inside a Skill's execute()?**  
A: Yes! This is composition. Skill calls other Skills via registry.execute().

**Q: What's the max timeout?**  
A: Default 30s. Configurable per call. Hard max: 300s (5 minutes).

**Q: Can I get detailed error messages?**  
A: Yes, check audit trail. Errors logged with full traceback.

**Q: Can I modify a Skill after registration?**  
A: No. Register a new version instead. Old version pinned dependencies stay on old version.

---

## Next Steps

- **[Composable Programs](composable-programs.md)** — Real-world composition examples
- **[Skills System](skills-system.md)** — Skill lifecycle overview
