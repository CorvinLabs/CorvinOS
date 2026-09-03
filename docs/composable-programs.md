# Composable Programs: Skills Calling Skills

Write Skills that call other Skills, building complex behaviors from reusable parts.

![Skill Composition Tree](docs/assets/skill-composition-tree.svg)

---

## Composition Basics

A Skill can call other Skills via `registry.execute()`:

```python
from core.skills.skill_interface import Skill
from core.skills.skill_registry_phase1 import skill_registry

class ComposedSkill(Skill):
    id = "my.composed"
    version = "1.0"
    dependencies = ["os.delegation_router@1.2", "os.vibe_engineering@0.3"]
    
    def execute(self, input: dict) -> dict:
        # Call Skill 1
        router_result = skill_registry.execute("os.delegation_router", input)
        
        # Call Skill 2
        vibe_result = skill_registry.execute("os.vibe_engineering", input)
        
        # Combine results
        return {
            "routing": router_result,
            "vibe": vibe_result,
            "combined": self.combine(router_result, vibe_result)
        }
    
    def combine(self, a: dict, b: dict) -> dict:
        return {"score": (a["score"] + b["score"]) / 2}
```

---

## Real-World Example: Multi-Step Routing

```python
class AdvancedRoutingSkill(Skill):
    id = "example.advanced_routing"
    version = "1.0"
    dependencies = [
        "os.delegation_router@1.2",
        "os.vibe_engineering@0.3",
        "core.cost_estimator@2.1"
    ]
    
    def execute(self, input: dict) -> dict:
        # Step 1: Assess task complexity
        routing_decision = skill_registry.execute(
            "os.delegation_router",
            {"complexity": input.get("complexity", 5)}
        )
        
        # Step 2: Understand user vibe
        vibe_decision = skill_registry.execute(
            "os.vibe_engineering",
            {"user_id": input.get("user_id")}
        )
        
        # Step 3: Estimate cost
        cost_estimate = skill_registry.execute(
            "core.cost_estimator",
            {"engine": routing_decision["engine"]}
        )
        
        # Step 4: Final decision (custom logic)
        if cost_estimate["cost"] > input.get("max_budget", 1.0):
            engine = "claude-haiku-4-5"  # Cheaper fallback
        else:
            engine = routing_decision["engine"]
        
        return {
            "engine": engine,
            "cost": cost_estimate["cost"],
            "vibe_confidence": vibe_decision.get("confidence", 0.5)
        }
```

---

## Dependency Declaration

Always declare dependencies:

```python
class VotingSkill(Skill):
    id = "my.voting"
    version = "1.0"
    # Declare which Skills you call
    dependencies = [
        "os.delegation_router@1.2",  # Pin to v1.2
        "os.vibe_engineering",        # Use latest (no pin)
        "custom.my_skill@1.0"
    ]
    
    def execute(self, input: dict) -> dict:
        # Can call any of these
        r1 = skill_registry.execute("os.delegation_router", input)
        r2 = skill_registry.execute("os.vibe_engineering", input)
        r3 = skill_registry.execute("custom.my_skill", input)
        
        # ... combine results ...
        return result
```

**Why declare dependencies?**
- ✅ DAG validation (catch circular refs at registration)
- ✅ Deployment tracking (know what impacts what)
- ✅ Version management (know which versions you're using)
- ✅ Audit trail (dependency chain is transparent)

---

## DAG Validation & Circular Dependencies

The system detects circular dependencies:

```python
# GOOD: Linear dependency chain
# A → B → C
class A(Skill):
    id = "a"
    dependencies = ["b"]

class B(Skill):
    id = "b"
    dependencies = ["c"]

class C(Skill):
    id = "c"
    dependencies = []

# Registration succeeds ✓


# BAD: Circular dependency
# A → B → A
class A(Skill):
    id = "a"
    dependencies = ["b"]

class B(Skill):
    id = "b"
    dependencies = ["a"]

# Registration fails ✗
# Error: CircularDependencyError: a → b → a
```

---

## Execution Order (Topological Sort)

Skills are executed in dependency order:

```python
# Dependency tree:
#
#     A
#    / \
#   B   C
#   |
#   D

# Execution order: D → B → (C, A in parallel)
# A waits for B and C to complete before starting
```

---

## Composition Patterns

### Pattern 1: Sequential Processing

```python
class Pipeline(Skill):
    id = "patterns.pipeline"
    dependencies = ["step1", "step2", "step3"]
    
    def execute(self, input: dict) -> dict:
        # Pass output of step 1 to step 2
        result1 = skill_registry.execute("step1", input)
        result2 = skill_registry.execute("step2", result1)
        result3 = skill_registry.execute("step3", result2)
        return result3
```

### Pattern 2: Parallel Processing

```python
class Parallel(Skill):
    id = "patterns.parallel"
    dependencies = ["branch1", "branch2", "branch3"]
    
    def execute(self, input: dict) -> dict:
        # Execute all branches in parallel (depends on runtime)
        r1 = skill_registry.execute("branch1", input)
        r2 = skill_registry.execute("branch2", input)
        r3 = skill_registry.execute("branch3", input)
        
        # Combine results
        return self.merge(r1, r2, r3)
```

### Pattern 3: Branching Logic

```python
class BranchingRouter(Skill):
    id = "patterns.branching"
    dependencies = ["classifier", "path_a", "path_b"]
    
    def execute(self, input: dict) -> dict:
        # Classify input
        classification = skill_registry.execute("classifier", input)
        
        # Route based on classification
        if classification["type"] == "urgent":
            return skill_registry.execute("path_a", input)
        else:
            return skill_registry.execute("path_b", input)
```

### Pattern 4: Voting / Consensus

```python
class VotingRouter(Skill):
    id = "patterns.voting"
    dependencies = ["voter1", "voter2", "voter3"]
    
    def execute(self, input: dict) -> dict:
        votes = []
        for voter in ["voter1", "voter2", "voter3"]:
            result = skill_registry.execute(voter, input)
            votes.append(result.get("choice"))
        
        # Majority vote
        from collections import Counter
        choice = Counter(votes).most_common(1)[0][0]
        
        return {"choice": choice, "votes": votes}
```

---

## Error Handling in Composition

All errors bubble up and are logged:

```python
class ErrorHandling(Skill):
    id = "patterns.error_handling"
    dependencies = ["risky_skill"]
    
    def execute(self, input: dict) -> dict:
        try:
            result = skill_registry.execute("risky_skill", input)
        except SkillExecutionError as e:
            # Graceful fallback
            return {"error": str(e), "fallback": True}
        
        return result

# Audit trail will show BOTH:
# 1. risky_skill execution (failed)
# 2. error_handling execution (succeeded with fallback)
```

---

## Real-World: Content Moderation Pipeline

```python
class ContentModerationPipeline(Skill):
    id = "example.moderation_pipeline"
    version = "1.0"
    dependencies = [
        "toxicity_detector@2.0",
        "spam_classifier@1.5",
        "nsfw_filter@1.1"
    ]
    
    def execute(self, input: dict) -> dict:
        content = input.get("content", "")
        
        # Step 1: Check toxicity
        toxicity = skill_registry.execute(
            "toxicity_detector",
            {"text": content}
        )
        if toxicity.get("is_toxic"):
            return {
                "approved": False,
                "reason": "Toxic content detected",
                "score": toxicity.get("score", 0)
            }
        
        # Step 2: Check spam
        spam = skill_registry.execute(
            "spam_classifier",
            {"text": content}
        )
        if spam.get("is_spam"):
            return {
                "approved": False,
                "reason": "Spam detected",
                "score": spam.get("score", 0)
            }
        
        # Step 3: Check NSFW
        nsfw = skill_registry.execute(
            "nsfw_filter",
            {"text": content}
        )
        if nsfw.get("is_nsfw"):
            return {
                "approved": False,
                "reason": "NSFW content",
                "score": nsfw.get("score", 0)
            }
        
        # All checks passed
        return {
            "approved": True,
            "toxicity_score": toxicity.get("score", 0),
            "spam_score": spam.get("score", 0),
            "nsfw_score": nsfw.get("score", 0)
        }
```

---

## Debugging Composition

### View Dependency Tree

```bash
corvin skills dependencies my.composed

Output:
  my.composed v1.0
    ├─ os.delegation_router v1.2
    │  └─ (no dependencies)
    ├─ os.vibe_engineering v0.3
    │  └─ (no dependencies)
    └─ core.cost_estimator v2.1
       └─ (no dependencies)
```

### Trace Execution Chain

```bash
corvin audit trace task <task_id> --show-composition

Output:
  Task: task_12345
  Execution chain:
    1. my.composed.execute() starts
    2. ├─ os.delegation_router.execute()  (completes: 42ms)
    3. ├─ os.vibe_engineering.execute()   (completes: 28ms)
    4. ├─ core.cost_estimator.execute()   (completes: 15ms)
    5. └─ my.composed.execute() finishes (total: 95ms)
```

---

## FAQ

**Q: Can I call the same Skill twice?**  
A: Yes. It will execute twice, and both calls are audited separately.

**Q: What if a dependency is slow?**  
A: Caller waits. Timeout is configurable (default 30s).

**Q: Can I dynamically call a Skill (not in dependencies)?**  
A: No. All called Skills must be in `dependencies` list (for DAG validation).

**Q: How deep can composition go?**  
A: Any depth, as long as DAG is valid (no circular refs).

**Q: Do composed Skills incur overhead?**  
A: Minimal. Just function calls + audit events. No serialization overhead.

---

## Next Steps

- **[Skills API Reference](skills-api-reference.md)** — Full API docs
- **[ACP Vision](acp-vision.md)** — How composition enables the ACP architecture
