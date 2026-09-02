# Skills API Reference

Complete API documentation for registering, executing, and composing Skills in CorvinOS.

---

## Skill Registry API

### `register_skill(skill_instance: Skill) -> None`

Register a new Skill with the system.

**Signature:**
```python
def register_skill(skill_instance: Skill) -> None:
    """
    Register a Skill instance.
    
    Args:
        skill_instance: Skill object with required metadata
        
    Raises:
        SkillValidationError: If metadata invalid (missing id, version, etc.)
        SkillDependencyError: If dependencies don't exist
        SkillConflictError: If skill_id already registered
    """
```

**Example:**
```python
from corvin_skills.base import Skill
from corvin_skills.registry import register_skill

@Skill.register
class MySkill(Skill):
    skill_id = "custom.my_skill"
    version = "1.0.0"
    origin = "community"
    
    def execute(self, request: dict) -> dict:
        return {"result": "ok"}

# @Skill.register decorator calls register_skill automatically
```

**Error Cases:**
```python
# ❌ Missing skill_id
class BadSkill(Skill):
    version = "1.0.0"
    # Error: skill_id required

# ❌ Dependency doesn't exist
class BadDeps(Skill):
    skill_id = "custom.bad_deps"
    depends_on = ["nonexistent.skill"]
    # Error: Dependency not found

# ❌ Duplicate skill_id
register_skill(skill_instance)
register_skill(skill_instance)  # Same id
# Error: skill_id already registered
```

---

### `execute_skill(skill_id: str, input: dict, tenant_id: str = "_default") -> dict`

Execute a Skill and return its output.

**Signature:**
```python
def execute_skill(
    skill_id: str,
    input: dict,
    tenant_id: str = "_default",
    trace: bool = False
) -> dict:
    """
    Execute a Skill.
    
    Args:
        skill_id: Skill identifier (e.g., "os.router")
        input: Input dict
        tenant_id: Tenant isolation (GDPR Art. 5)
        trace: If True, include execution trace in output
        
    Returns:
        dict: Skill output + metadata (latency, confidence, etc.)
        
    Raises:
        SkillNotFoundError: If skill_id doesn't exist
        SkillDisabledError: If Skill is disabled
        SkillExecutionError: If execute() raises exception
    """
```

**Example:**
```python
from corvin_skills.registry import execute_skill

# Simple execution
result = execute_skill(
    skill_id="os.delegation_router",
    input={"request": "summarize this"}
)
# Returns: {"route_to": "opus", "confidence": 0.88, ...}

# With tracing
result = execute_skill(
    skill_id="os.delegation_router",
    input={"request": "summarize this"},
    trace=True
)
# Returns: {
#   "route_to": "opus",
#   "trace": {
#     "classify_content": {...},
#     "estimate_complexity": {...},
#     ...
#   }
# }
```

---

### `get_skill(skill_id: str) -> Skill`

Retrieve a Skill instance.

**Signature:**
```python
def get_skill(skill_id: str) -> Skill:
    """
    Get a Skill by ID.
    
    Args:
        skill_id: Skill identifier
        
    Returns:
        Skill: The Skill instance
        
    Raises:
        SkillNotFoundError: If skill_id doesn't exist
    """
```

**Example:**
```python
from corvin_skills.registry import get_skill

skill = get_skill("os.delegation_router")
print(f"Version: {skill.version}")
print(f"Depends on: {skill.depends_on}")
print(f"Confidence: {skill.get_confidence()}")
```

---

### `list_all_skills() -> list[Skill]`

List all registered Skills.

**Signature:**
```python
def list_all_skills(
    origin: str = None,  # "builtin" | "vetted" | "community"
    boot_layer: str = None,  # "meta" | "core" | "bundled" | "installed"
    enabled_only: bool = False
) -> list[Skill]:
    """
    List all registered Skills.
    
    Args:
        origin: Filter by origin (optional)
        boot_layer: Filter by boot layer (optional)
        enabled_only: If True, exclude disabled Skills
        
    Returns:
        list: Skill instances
    """
```

**Example:**
```python
from corvin_skills.registry import list_all_skills

# All Skills
all_skills = list_all_skills()
for skill in all_skills:
    print(f"{skill.skill_id} ({skill.version})")

# Only builtin Skills
builtin = list_all_skills(origin="builtin")

# Only enabled OS-Skills
os_skills = list_all_skills(origin="builtin", boot_layer="core", enabled_only=True)
```

---

### `is_enabled(skill_id: str) -> bool`

Check if a Skill is enabled.

**Signature:**
```python
def is_enabled(skill_id: str) -> bool:
    """
    Check if a Skill is enabled.
    
    Args:
        skill_id: Skill identifier
        
    Returns:
        bool: True if enabled, False otherwise
    """
```

**Example:**
```python
from corvin_skills.registry import is_enabled

if is_enabled("custom.my_skill"):
    result = execute_skill("custom.my_skill", {...})
else:
    print("Skill is disabled")
```

---

### `disable_skill(skill_id: str) -> None`

Disable a Skill (audit-logged, reversible).

**Signature:**
```python
def disable_skill(skill_id: str, reason: str = "") -> None:
    """
    Disable a Skill.
    
    Args:
        skill_id: Skill identifier
        reason: Reason for disabling (for audit trail)
        
    Raises:
        SkillNotFoundError: If skill_id doesn't exist
        SkillDisableRefused: If Skill is in Meta-layer (immutable)
    """
```

**Example:**
```python
from corvin_skills.registry import disable_skill

# Disable a Skill
disable_skill("custom.slow_skill", reason="Performance degradation")

# Can't disable Meta-Skills
disable_skill("meta.audit_chain")
# Error: SkillDisableRefused
```

---

## Skill Class Interface

### Base Class: `Skill`

All Skills inherit from `Skill`:

```python
from corvin_skills.base import Skill

@Skill.register
class MySkill(Skill):
    # Required metadata
    skill_id = "custom.my_skill"
    version = "1.0.0"
    origin = "community"  # builtin | vetted | community
    boot_layer = "installed"  # meta | core | bundled | installed
    
    # Optional metadata
    depends_on = []  # Skills this one calls
    description = "Brief description"
    author = "author@example.com"
    tags = ["tag1", "tag2"]
    
    # Required method
    def execute(self, request: dict) -> dict:
        """Process input and return output."""
        return {"result": "value"}
    
    # Optional methods
    def get_feedback_schema(self) -> dict:
        """Describe what feedback looks like."""
        return {...}
```

### Method: `execute(request: dict) -> dict`

**Must be implemented by every Skill.**

```python
def execute(self, request: dict) -> dict:
    """
    Process input and produce output.
    
    Args:
        request: Input dict with required keys
        
    Returns:
        dict: Output with results + metadata
        - "confidence": float [0-1]
        - "reasoning": str (optional, for debugging)
        - Other keys specific to this Skill
        
    Raises:
        SkillExecutionError: If execution fails
    """
```

**Example:**
```python
def execute(self, request: dict) -> dict:
    text = request.get("text", "")
    
    if not text:
        raise SkillExecutionError("text required")
    
    # Process
    result = my_logic(text)
    
    return {
        "result": result,
        "confidence": 0.85,
        "reasoning": "Processed text with v2.1 logic"
    }
```

### Method: `call_skill(skill_id: str, input: dict) -> dict`

Call another Skill from within execute().

```python
def call_skill(self, skill_id: str, input: dict) -> dict:
    """
    Call another Skill.
    
    Args:
        skill_id: Skill to call
        input: Input to pass
        
    Returns:
        dict: Skill output
        
    Raises:
        SkillNotFoundError: If skill_id doesn't exist
        SkillExecutionError: If called Skill fails
    """
```

**Example:**
```python
def execute(self, request: dict) -> dict:
    # Call Skill 1
    classified = self.call_skill("classify_content", request)
    
    # Call Skill 2, passing Skill 1's output
    routed = self.call_skill("select_engine", classified)
    
    return routed
```

### Method: `get_config() -> dict`

Get current Skill configuration (may be tuned by optimizer).

```python
def get_config(self) -> dict:
    """
    Get current Skill configuration.
    
    Returns:
        dict: Config parameters (may have been tuned by optimizer)
    """
```

**Example:**
```python
def execute(self, request: dict) -> dict:
    config = self.get_config()
    threshold = config.get("threshold", 0.5)  # Default: 0.5
    
    complexity = estimate(request)
    route = "opus" if complexity > threshold else "haiku"
    
    return {"route": route}
```

### Method: `get_confidence() -> float`

Get current confidence score.

```python
def get_confidence(self) -> float:
    """
    Get current confidence score [0.0–1.0].
    
    Returns:
        float: Confidence, or 0.5 if no feedback yet
    """
```

**Example:**
```python
skill = get_skill("os.delegation_router")
confidence = skill.get_confidence()
if confidence < 0.8:
    print("Skill not yet converged")
```

### Method: `get_feedback_history(limit: int = 100) -> list[dict]`

Get recent feedback for this Skill.

```python
def get_feedback_history(self, limit: int = 100) -> list[dict]:
    """
    Get recent feedback events.
    
    Args:
        limit: Max number of events to return
        
    Returns:
        list: Feedback events (most recent first)
    """
```

**Example:**
```python
skill = get_skill("os.delegation_router")
feedback = skill.get_feedback_history(limit=50)
for event in feedback:
    print(f"Feedback: {event['feedback_type']} = {event['signal']}")
```

---

## Skill Composition

### Dependency Declaration

Declare all Skills you call:

```python
@Skill.register
class Router(Skill):
    skill_id = "os.delegation_router"
    version = "2.0.0"
    depends_on = ["classify_content", "estimate_complexity", "select_engine"]
    
    def execute(self, request: dict) -> dict:
        # These are verified to exist
        classified = self.call_skill("classify_content", request)
        estimated = self.call_skill("estimate_complexity", classified)
        selected = self.call_skill("select_engine", {**classified, **estimated})
        return selected
```

**Validation:**
- At registration time: All dependencies must exist
- At execution time: All dependencies must be enabled
- Circular dependencies: Detected and blocked

### Dependency Resolution

```python
from corvin_skills.registry import resolve_dependencies

# Get dependency tree
deps = resolve_dependencies("os.delegation_router")
# Returns: {
#   "classify_content": {...},
#   "estimate_complexity": {...},
#   "select_engine": {...}
# }

# Get execution order (topological sort)
order = resolve_dependencies("os.delegation_router", topo_sort=True)
# Returns: [
#   "classify_content",      # No dependencies
#   "estimate_complexity",   # Depends on classify_content
#   "select_engine"          # Depends on both
# ]
```

### Error Handling in Composition

If a called Skill fails, the entire composition fails (fail-closed):

```python
def execute(self, request: dict) -> dict:
    try:
        classified = self.call_skill("classify_content", request)
    except SkillExecutionError as e:
        # Skill failed; propagate
        raise SkillExecutionError(f"Dependency failed: {e}")
    
    # If we get here, classified succeeded
    return {"classification": classified["category"]}
```

---

## Learning API

### Method: `get_feedback_schema() -> dict`

Describe what feedback looks like for this Skill.

```python
def get_feedback_schema(self) -> dict:
    """
    JSON Schema describing feedback format.
    
    Returns:
        dict: JSON Schema (or empty dict if no feedback)
    """
```

**Example:**
```python
def get_feedback_schema(self) -> dict:
    return {
        "type": "object",
        "properties": {
            "correct": {
                "type": "boolean",
                "description": "Was the routing decision correct?"
            },
            "actual_route": {
                "type": "string",
                "enum": ["haiku", "sonnet", "opus"],
                "description": "What should have been routed?"
            }
        },
        "required": ["correct"]
    }
```

### Function: `submit_feedback(task_id: str, feedback: dict) -> None`

Submit feedback for a Skill execution.

```python
from corvin_skills.learning import submit_feedback

def submit_feedback(
    task_id: str,
    skill_id: str,
    feedback: dict,
    tenant_id: str = "_default"
) -> None:
    """
    Submit feedback for a Skill execution.
    
    Args:
        task_id: Task that produced the decision
        skill_id: Which Skill to give feedback to
        feedback: Feedback object (schema defined by Skill)
        tenant_id: Tenant isolation
        
    Raises:
        SkillNotFoundError: If skill_id doesn't exist
    """
```

**Example:**
```python
from corvin_skills.learning import submit_feedback

# User provides feedback
submit_feedback(
    task_id="task_xyz",
    skill_id="os.delegation_router",
    feedback={
        "correct": False,
        "actual_route": "sonnet"  # Should have used Sonnet, not Opus
    }
)
# Audit: Event logged, optimizer can learn from this
```

---

## Configuration API

### Method: `get_default_config() -> dict`

Get default configuration for a Skill.

```python
@Skill.register
class MySkill(Skill):
    skill_id = "custom.my_skill"
    version = "1.0.0"
    
    def get_default_config(self) -> dict:
        return {
            "threshold": 0.5,
            "timeout_ms": 1000,
            "retries": 3
        }
    
    def execute(self, request: dict) -> dict:
        config = self.get_config()
        threshold = config["threshold"]
        # Use config...
```

### Function: `update_skill_config(skill_id: str, config: dict) -> None`

Manually update Skill configuration (audit-logged).

```python
from corvin_skills.registry import update_skill_config

# Manual config update (operator, not optimizer)
update_skill_config(
    skill_id="custom.my_skill",
    config={"threshold": 0.7, "timeout_ms": 2000}
)
# Audit: Event logged "skill_config_updated"
```

---

## Audit + Telemetry

### Auto-Logged Metadata

Every Skill execution automatically logs:

```json
{
  "event_type": "skill_executed",
  "skill_id": "os.delegation_router",
  "skill_version": "2.0.1",
  "timestamp": "2026-09-02T14:30:45.123Z",
  "latency_ms": 42,
  "input": {...},
  "output": {...},
  "confidence": 0.88,
  "lom": "core/skills/os_skills/router.py:L237",
  "lom_hash": "sha256(...)",
  "tenant_id": "_default",
  "hash": "sha256(...)",
  "prev_hash": "sha256(...)"
}
```

### Function: `audit_custom_event(event_type: str, payload: dict) -> None`

Log custom audit event (optional).

```python
from corvin_skills.audit import audit_custom_event

def execute(self, request: dict) -> dict:
    # ... process ...
    
    # Optional: Log custom event for debugging
    audit_custom_event("my_skill_debug", {
        "stage": "classification",
        "debug_info": "some details"
    })
    
    return {...}
```

---

## Error Handling

### Exception Hierarchy

```
SkillError
├── SkillNotFoundError
├── SkillDisabledError
├── SkillExecutionError
│   ├── SkillTimeoutError
│   └── SkillDependencyError
├── SkillValidationError
├── SkillConflictError
└── SkillDisableRefused
```

### Example: Catching Errors

```python
from corvin_skills.registry import execute_skill
from corvin_skills.errors import (
    SkillNotFoundError,
    SkillExecutionError,
    SkillDisabledError
)

try:
    result = execute_skill("os.delegation_router", {"request": "..."})
except SkillNotFoundError:
    print("Skill doesn't exist")
except SkillDisabledError:
    print("Skill is disabled")
except SkillExecutionError as e:
    print(f"Skill failed: {e}")
    # Still returns partial result if available
```

---

## CLI Commands

### `corvin skill`

Command-line interface for Skills.

```bash
# List all Skills
corvin skill list

# Get Skill details
corvin skill info <skill_id>

# Execute Skill
corvin skill execute \
  --skill-id os.delegation_router \
  --input '{"request": "summarize"}'

# Get convergence status
corvin skill convergence <skill_id>

# Get feedback history
corvin skill feedback <skill_id> --last 50

# Trace composition
corvin skill trace <skill_id> --task <task_id>

# Disable a Skill
corvin skill disable <skill_id> --reason "Performance issue"

# Enable a Skill
corvin skill enable <skill_id>

# Deploy new version
corvin skill deploy <skill_id> --version <version> --canary 10%

# Rollback
corvin skill rollback <skill_id> --to-version <version>
```

---

## Best Practices

1. **Always declare dependencies:**
   ```python
   depends_on = ["skill_a", "skill_b"]  # Explicit
   ```

2. **Validate input:**
   ```python
   def execute(self, request: dict) -> dict:
       required_field = request.get("required")
       if not required_field:
           raise SkillExecutionError("required field missing")
   ```

3. **Return confidence:**
   ```python
   return {
       "result": result,
       "confidence": 0.92  # Always include
   }
   ```

4. **Implement feedback schema:**
   ```python
   def get_feedback_schema(self) -> dict:
       return {"type": "object", ...}
   ```

5. **Use versioning:**
   - PATCH: Bug fix or internal refactor
   - MINOR: New feature, backward-compatible
   - MAJOR: Breaking change (output format, etc.)

---

## See Also

- **[Skills System](skills-system.md)** — Detailed guide to writing Skills
- **[Composable Programs](composable-programs.md)** — Composition patterns
- **[Learning Loop](learning-loop.md)** — Feedback and optimization
- **[Audit Trail](audit-trail.md)** — What gets logged

---

**The Skills API is simple but powerful. Register, execute, compose, and learn.**
