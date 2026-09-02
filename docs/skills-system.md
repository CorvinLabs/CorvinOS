# CorvinOS Skills System

A **Skill** is a versioned, composable, self-learning program that performs a specific function in CorvinOS. Every Skill:
- Has a clear purpose and unique ID
- Declares its dependencies (other Skills)
- Executes deterministically with inputs/outputs
- Receives feedback and improves over time
- Logs every decision immutably (audit trail)

This guide explains how Skills work, how to use them, and how to write your own.

---

## What is a Skill?

### Definition
A Skill is a Python class + metadata that encapsulates a repeatable business logic:

```python
from corvin_skills.base import Skill

@Skill.register
class DelegationRouter(Skill):
    skill_id = "os.delegation_router"
    version = "1.0.0"
    depends_on = ["classify_content", "estimate_complexity"]
    
    def execute(self, request: dict) -> dict:
        # Compose other Skills
        classified = self.call_skill("classify_content", request)
        estimated = self.call_skill("estimate_complexity", classified)
        
        # Route based on complexity
        if estimated["complexity"] > 0.7:
            route = "opus"  # Complex: use large model
        else:
            route = "haiku"  # Simple: use fast model
        
        return {
            "route_to": route,
            "confidence": estimated.get("confidence", 0.5),
            "reasoning": estimated.get("reasoning", "")
        }
```

### Core Properties
Every Skill has:

| Property | Type | Description |
|---|---|---|
| **skill_id** | str | Unique identifier (e.g., `os.delegation_router`) |
| **version** | str | Semantic version (e.g., `1.0.0`) |
| **origin** | str | `builtin` \| `vetted` \| `community` |
| **boot_layer** | str | `meta` \| `core` \| `bundled` \| `installed` |
| **depends_on** | list[str] | Skills this Skill calls (optional) |

---

## Skill Lifecycle

```svg
<svg viewBox="0 0 900 600" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="900" height="600" fill="#F9FAFB"/>
  
  <!-- Title -->
  <text x="450" y="30" font-size="22" font-weight="bold" text-anchor="middle" fill="#1F2937">
    Skill Lifecycle: Registration → Execution → Feedback → Optimization
  </text>
  
  <!-- Stage 1: Registration -->
  <rect x="50" y="80" width="160" height="80" rx="4" fill="#DBEAFE" stroke="#3B82F6" stroke-width="2"/>
  <text x="130" y="110" font-size="12" font-weight="bold" text-anchor="middle" fill="#1E40AF">
    1. Registration
  </text>
  <text x="130" y="135" font-size="10" text-anchor="middle" fill="#1E40AF">
    Skill loaded,
  </text>
  <text x="130" y="150" font-size="10" text-anchor="middle" fill="#1E40AF">
    metadata validated
  </text>
  
  <!-- Arrow 1 -->
  <path d="M 210 120 L 270 120" stroke="#6B7280" stroke-width="2" fill="none" marker-end="url(#arrowhead)"/>
  
  <!-- Stage 2: Execution -->
  <rect x="270" y="80" width="160" height="80" rx="4" fill="#DCFCE7" stroke="#10B981" stroke-width="2"/>
  <text x="350" y="110" font-size="12" font-weight="bold" text-anchor="middle" fill="#065F46">
    2. Execution
  </text>
  <text x="350" y="135" font-size="10" text-anchor="middle" fill="#065F46">
    Process input,
  </text>
  <text x="350" y="150" font-size="10" text-anchor="middle" fill="#065F46">
    produce output
  </text>
  
  <!-- Arrow 2 -->
  <path d="M 430 120 L 490 120" stroke="#6B7280" stroke-width="2" fill="none" marker-end="url(#arrowhead)"/>
  
  <!-- Stage 3: Audit -->
  <rect x="490" y="80" width="160" height="80" rx="4" fill="#FEE2E2" stroke="#EF4444" stroke-width="2"/>
  <text x="570" y="110" font-size="12" font-weight="bold" text-anchor="middle" fill="#DC2626">
    3. Audit
  </text>
  <text x="570" y="135" font-size="10" text-anchor="middle" fill="#DC2626">
    Log event,
  </text>
  <text x="570" y="150" font-size="10" text-anchor="middle" fill="#DC2626">
    hash-chain link
  </text>
  
  <!-- Arrow 3 -->
  <path d="M 650 120 L 710 120" stroke="#6B7280" stroke-width="2" fill="none" marker-end="url(#arrowhead)"/>
  
  <!-- Stage 4: Feedback -->
  <rect x="710" y="80" width="160" height="80" rx="4" fill="#FEF3C7" stroke="#F59E0B" stroke-width="2"/>
  <text x="790" y="110" font-size="12" font-weight="bold" text-anchor="middle" fill="#92400E">
    4. Feedback
  </text>
  <text x="790" y="135" font-size="10" text-anchor="middle" fill="#92400E">
    User evaluates
  </text>
  <text x="790" y="150" font-size="10" text-anchor="middle" fill="#92400E">
    outcome
  </text>
  
  <!-- Feedback loop back -->
  <path d="M 790 160 L 790 200 L 350 200 L 350 170" stroke="#F59E0B" stroke-width="2" fill="none" marker-end="url(#arrowhead-orange)" stroke-dasharray="5,5"/>
  
  <!-- Stage 5: Optimization (bottom) -->
  <rect x="280" y="280" width="160" height="80" rx="4" fill="#E0E7FF" stroke="#6366F1" stroke-width="2"/>
  <text x="360" y="310" font-size="12" font-weight="bold" text-anchor="middle" fill="#312E81">
    5. Optimization
  </text>
  <text x="360" y="335" font-size="10" text-anchor="middle" fill="#312E81">
    Tune parameters,
  </text>
  <text x="360" y="350" font-size="10" text-anchor="middle" fill="#312E81">
    improve confidence
  </text>
  
  <!-- Arrow to Execution (cycle) -->
  <path d="M 360 280 L 360 160" stroke="#6366F1" stroke-width="2" fill="none" marker-end="url(#arrowhead-indigo)" stroke-dasharray="5,5"/>
  
  <!-- Convergence tracker (right side) -->
  <rect x="550" y="280" width="240" height="150" rx="4" fill="#F0F9FF" stroke="#0369A1" stroke-width="2"/>
  <text x="670" y="305" font-size="12" font-weight="bold" text-anchor="middle" fill="#0369A1">
    Convergence Tracking
  </text>
  
  <!-- Mini chart inside -->
  <polyline points="580,380 600,370 620,360 640,345 660,330 680,310 700,295 720,280 740,270 760,265" 
            fill="none" stroke="#10B981" stroke-width="2"/>
  <text x="670" y="425" font-size="10" text-anchor="middle" fill="#065F46">
    Confidence → 95% target
  </text>
  <text x="670" y="440" font-size="10" text-anchor="middle" fill="#065F46">
    Week 1: 60% → Week 4: 92%
  </text>
  
  <!-- Timeline labels -->
  <text x="50" y="500" font-size="11" fill="#6B7280">
    ⏱ Week 1: Registration + Execution + Audit + Feedback collected
  </text>
  <text x="50" y="525" font-size="11" fill="#6B7280">
    ⏱ Week 2-4: Feedback processed, parameters tuned, confidence improves
  </text>
  <text x="50" y="550" font-size="11" fill="#6B7280">
    ⏱ Week 4+: Skill converged to ~95% confidence, minimal tuning needed
  </text>
  
  <!-- Arrow marker -->
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#6B7280"/>
    </marker>
    <marker id="arrowhead-orange" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#F59E0B"/>
    </marker>
    <marker id="arrowhead-indigo" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#6366F1"/>
    </marker>
  </defs>
</svg>
```

### Stage Details

1. **Registration**
   - Skill loaded and metadata validated
   - Dependencies verified (all referenced Skills exist)
   - Boot layer determined (meta → core → bundled → installed)
   - Audit event: `skill_loaded`

2. **Execution**
   - Input validated (type-checked)
   - Skill's `execute()` method called
   - Output produced
   - Latency measured

3. **Audit**
   - Event logged immutably: `skill_executed`
   - Hash-chained to previous event
   - LoM (Line of Moral Responsibility) recorded
   - Tenant ID verified (no cross-tenant data)

4. **Feedback**
   - User evaluates the output (yes/no/maybe/other)
   - Feedback event logged: `skill_feedback`
   - Confidence score calculated

5. **Optimization**
   - Weekly optimizer runs
   - Tunes Skill parameters based on feedback
   - Updates config: `skill_config_updated`
   - Confidence improves over time

---

## Skill Metadata

### Required Fields

```python
@Skill.register
class MySkill(Skill):
    skill_id = "namespace.skill_name"     # Unique identifier
    version = "1.0.0"                     # Semantic version
    origin = "builtin"                    # builtin | vetted | community
    boot_layer = "core"                   # meta | core | bundled | installed
```

### Optional Fields

```python
@Skill.register
class MySkill(Skill):
    depends_on = ["other.skill", "third.skill"]    # Skills I call
    description = "Brief description of what this Skill does"
    author = "author@example.com"
    tags = ["routing", "classification", "learning"]
    config_schema = {...}                          # JSON Schema for parameters
```

---

## Skill Interface

Every Skill must implement the `execute()` method:

```python
def execute(self, request: dict) -> dict:
    """
    Process input and return output.
    
    Args:
        request: Input dict with required keys
        
    Returns:
        dict: Output with results + metadata
        
    Raises:
        SkillExecutionError: If execution fails
    """
    # Your logic here
    result = process(request)
    return {
        "result": result,
        "confidence": 0.92,
        "reasoning": "..."
    }
```

### Accessing SkillContext

Inside `execute()`, you have access to context:

```python
def execute(self, request: dict) -> dict:
    # Call another Skill
    classified = self.call_skill("classify_content", request)
    
    # Get current tenant (GDPR isolation)
    tenant_id = self.context.tenant_id
    
    # Get config (may be tuned by optimizer)
    config = self.get_config()
    
    # Get feedback history (for debugging)
    feedback = self.get_feedback_history(limit=100)
    
    return {...}
```

---

## Creating Custom Skills

### Step 1: Define the Skill Class

```python
from corvin_skills.base import Skill

@Skill.register
class ContentClassifier(Skill):
    skill_id = "custom.content_classifier"
    version = "1.0.0"
    origin = "community"
    boot_layer = "installed"
    description = "Classify content into categories"
    
    def execute(self, request: dict) -> dict:
        content = request.get("content", "")
        
        # Simple classification logic
        if len(content) < 100:
            category = "short"
        elif "question" in content.lower():
            category = "question"
        else:
            category = "article"
        
        return {
            "category": category,
            "confidence": 0.85
        }
```

### Step 2: Add to Registry

```bash
# Place your Skill in: core/skills/custom_skills/content_classifier.py
# The registry auto-discovers it on boot
```

### Step 3: Test Locally

```bash
python -c "
from core.skills.skill_registry import get_skill
skill = get_skill('custom.content_classifier')
result = skill.execute({'content': 'What is CorvinOS?'})
print(result)
"
```

### Step 4: Add Unit + E2E Tests

```python
# tests/skills/test_content_classifier.py
import pytest
from core.skills.custom_skills.content_classifier import ContentClassifier

def test_execute_short_content():
    skill = ContentClassifier()
    result = skill.execute({"content": "Hi"})
    assert result["category"] == "short"

def test_execute_question():
    skill = ContentClassifier()
    result = skill.execute({"content": "What is the meaning of life?"})
    assert result["category"] == "question"

def test_e2e_skill_execution():
    """Prove the Skill is reachable end-to-end"""
    from core.skills.skill_registry import execute_skill
    
    result = execute_skill("custom.content_classifier", {
        "content": "This is a test article with more than 100 characters to test"
    })
    assert result["category"] == "article"
    assert "confidence" in result
```

### Step 5: Request Feedback Integration

Optionally, configure how users provide feedback:

```python
class ContentClassifier(Skill):
    # ... (as before)
    
    def get_feedback_schema(self) -> dict:
        """Describe what feedback looks like"""
        return {
            "type": "object",
            "properties": {
                "correct": {"type": "boolean"},
                "actual_category": {"type": "string"}
            }
        }
```

---

## Skill Composition

Skills call other Skills. Dependencies are **declared and verified**.

### Example: Router that Composes Three Skills

```python
@Skill.register
class DelegationRouter(Skill):
    skill_id = "os.delegation_router"
    version = "1.0.0"
    depends_on = ["classify_content", "estimate_complexity", "select_engine"]
    
    def execute(self, request: dict) -> dict:
        # Call Skill 1: Classify
        classified = self.call_skill("classify_content", request)
        
        # Call Skill 2: Estimate complexity
        estimated = self.call_skill(
            "estimate_complexity",
            classified
        )
        
        # Call Skill 3: Select best engine
        selected = self.call_skill(
            "select_engine",
            {**classified, **estimated}
        )
        
        return selected
```

### Handling Errors

If a Skill fails, the whole composition fails (fail-closed):

```python
def execute(self, request: dict) -> dict:
    try:
        classified = self.call_skill("classify_content", request)
    except SkillExecutionError as e:
        # Skill failed; propagate error
        self.log_error(f"Classify failed: {e}")
        raise
    
    # If we get here, classified succeeded
    return {
        "category": classified["category"],
        "confidence": classified.get("confidence", 0.0)
    }
```

---

## Versioning & Semver

### Version Format

Skills use semantic versioning: `MAJOR.MINOR.PATCH`

| Version | When to Use | Example |
|---|---|---|
| **PATCH** | Bug fix, internal refactor | `1.0.0` → `1.0.1` |
| **MINOR** | New feature, backward-compatible | `1.0.1` → `1.1.0` |
| **MAJOR** | Breaking change to input/output | `1.1.0` → `2.0.0` |

### Backward Compatibility

When you change a Skill:

- ✅ **Safe:** Add optional fields to output, rename internal variables
- ⚠️ **Minor version bump:** Add optional input parameter
- ❌ **Major version bump (or don't do it):** Remove required field, change meaning of a field

---

## Skill Registry API

### Registering a Skill

```python
# Automatic: @Skill.register decorator
@Skill.register
class MySkill(Skill):
    skill_id = "my.skill"
    ...

# Manual: If you need custom registration
from core.skills.skill_registry import register_skill
register_skill(MySkill())
```

### Executing a Skill

```python
from core.skills.skill_registry import execute_skill

result = execute_skill(
    skill_id="my.skill",
    input={"key": "value"},
    tenant_id="_default"
)
```

### Listing All Skills

```python
from core.skills.skill_registry import list_all_skills

skills = list_all_skills()
for skill in skills:
    print(f"{skill.skill_id} ({skill.version})")
```

### Getting a Single Skill

```python
from core.skills.skill_registry import get_skill

skill = get_skill("my.skill")
print(f"Version: {skill.version}")
print(f"Depends on: {skill.depends_on}")
```

### Checking if Enabled

```python
from core.skills.skill_registry import is_enabled

if is_enabled("my.skill"):
    print("Skill is active")
else:
    print("Skill is disabled")
```

---

## Examples

### Example 1: Simple Skill (No Dependencies)

```python
@Skill.register
class RandomNumberSkill(Skill):
    skill_id = "demo.random_number"
    version = "1.0.0"
    origin = "community"
    boot_layer = "installed"
    
    def execute(self, request: dict) -> dict:
        import random
        max_val = request.get("max", 100)
        return {
            "number": random.randint(0, max_val),
            "confidence": 1.0
        }
```

### Example 2: Skill with Dependencies

```python
@Skill.register
class Summarizer(Skill):
    skill_id = "demo.summarizer"
    version = "1.0.0"
    depends_on = ["demo.text_cleaner", "demo.extract_key_points"]
    
    def execute(self, request: dict) -> dict:
        text = request.get("text", "")
        
        # Clean text
        cleaned = self.call_skill("demo.text_cleaner", {"text": text})
        
        # Extract points
        points = self.call_skill(
            "demo.extract_key_points",
            {"text": cleaned["cleaned_text"]}
        )
        
        return {
            "summary": " ".join(points["points"]),
            "point_count": len(points["points"])
        }
```

### Example 3: Skill with Learning

```python
@Skill.register
class Classifier(Skill):
    skill_id = "demo.classifier"
    version = "2.0.0"
    
    def execute(self, request: dict) -> dict:
        text = request.get("text", "")
        
        # Get current config (may be tuned)
        config = self.get_config()
        threshold = config.get("threshold", 0.5)
        
        # Classify
        score = calculate_score(text)
        category = "positive" if score > threshold else "negative"
        
        return {
            "category": category,
            "score": score,
            "confidence": abs(score - threshold)
        }
    
    def get_feedback_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "correct": {"type": "boolean"},
                "actual_category": {"type": "string", "enum": ["positive", "negative"]}
            }
        }
```

---

## Debugging Skills

### View Execution History

```bash
corvin audit show-task <task_id> --skill-only
```

### Get Skill Feedback

```bash
corvin skill feedback <skill_id> --last 50
```

### Check Convergence

```bash
corvin skill convergence <skill_id>
# Output: Confidence: 92% (target: 95%)
```

### Trace Composition Chain

```bash
corvin skill trace <skill_id> --task <task_id>
# Output: skill_a → skill_b → skill_c → result
```

---

## Next Steps

- **[Composable Programs](composable-programs.md)** — Write complex behavior by composing Skills
- **[Learning Loop](learning-loop.md)** — Understand how Skills improve over time
- **[Audit Trail](audit-trail.md)** — See how every decision is logged immutably
- **[ACP Vision](acp-vision.md)** — Learn about replacing L-Layers with Skills
- **[Skills API Reference](skills-api-reference.md)** — Complete API documentation

---

**Skills are the heart of CorvinOS.** Every subsystem is a Skill. Every Skill learns. Every decision is audited.
