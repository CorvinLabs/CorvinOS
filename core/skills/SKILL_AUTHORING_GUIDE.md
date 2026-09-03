# Phase 3a: Community Skill Authoring Guide

**How to Write & Submit a Skill to the ACP Marketplace**

---

## 1. What is a Skill?

A **Skill** is an executable, learnable Python class that:
- Takes structured input → produces structured output
- Emits learning events (confidence scores, feedback)
- Declares parameters (tunable by optimizer)
- Declares dependencies (other Skills it needs)
- Is versioned + composable via manifests

**Example:** `os.cost_optimizer` takes {request} → returns {route_decision, cost_estimate, confidence}

---

## 2. Skill Template (Boilerplate)

```python
"""Phase 3: Community Skill Template."""

from dataclasses import dataclass
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillConfig:
    """Tunable parameters (learned by optimizer)."""
    param_1: float = 0.5         # Example: threshold (tuned by gradient descent)
    param_2: int = 10            # Example: batch size
    version: int = 0             # Config version (incremented by optimizer)


class MyCustomSkill:
    """Your Skill implementation.
    
    Manifest (manifest.yaml):
        skill_id: my_custom_skill
        version: 1.0.0
        boot_layer: installed
        parameters:
          - name: param_1
            type: float
            default: 0.5
            bounds: [0.0, 1.0]
        dependencies:
          - skill_id: os.delegation_router
            version: ">=0.1.0"
    """

    def __init__(self, config: Optional[SkillConfig] = None, learning_backend=None):
        self.skill_id = "my_custom_skill"
        self.version = "1.0.0"
        self.config = config or SkillConfig()
        self.learning_backend = learning_backend

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Skill logic.
        
        Args:
            input: Dict with required keys (check your manifest)
        
        Returns:
            Dict with output + confidence score
        """
        # Step 1: Validate input
        required_keys = ["key_1", "key_2"]
        for key in required_keys:
            if key not in input:
                return {
                    "error": f"Missing required input: {key}",
                    "confidence": 0.0
                }

        # Step 2: Execute logic (using self.config for tunable params)
        key_1 = input["key_1"]
        key_2 = input["key_2"]
        
        result = key_1 + key_2 * self.config.param_1  # Example computation
        confidence = min(1.0, max(0.0, result))        # Compute confidence (0.0-1.0)

        # Step 3: Emit learning event (for optimizer feedback loop)
        self._emit_learning_event(input, result, confidence)

        # Step 4: Return result with confidence
        return {
            "result": result,
            "confidence": confidence,
            "reasoning": f"Computed {result} with confidence {confidence:.2f}"
        }

    def _emit_learning_event(self, input: Dict, output: Any, confidence: float) -> None:
        """Emit learning event (used by Phase 2a optimizer)."""
        if not self.learning_backend:
            return

        event = {
            "skill_id": self.skill_id,
            "input_keys": list(input.keys()),
            "output": output,
            "confidence": confidence,
            "config_version": self.config.version,
        }

        try:
            self.learning_backend.emit_event(event)
        except Exception as e:
            logger.error(f"Failed to emit learning event: {e}")
```

---

## 3. LDD for Skill Development (k=1-5)

### k=1: Dialectical Reasoning
- **Thesis:** My Skill solves [problem]
- **Antithesis:** What if [edge case]? What if [alternative approach]?
- **Synthesis:** My Skill handles [edge cases] + [assumptions explicit]

### k=2: E2E Wiring Proof
- **Reachability:** How is my Skill called? (integration test)
- **Real boundary:** HTTP endpoint? CLI? Plugin registry?
- **Proof:** Single E2E test that goes through real transport

### k=3: Red→Green (Implement + Test)
- Write Skill code (follow template above)
- Unit tests for execute() logic
- Unit tests for edge cases
- Unit tests for confidence scoring

### k=4: Refinement
- Integration tests (with other Skills, dependencies)
- Performance benchmarks (latency, memory)
- Manifest validation (schema + semver)

### k=5: Docs Sync
- Docstring in execute() explains I/O
- manifest.yaml complete + validated
- README with usage examples
- Parameters documented (bounds, learning implications)

---

## 4. Manifest Template (manifest.yaml)

```yaml
---
id: my_custom_skill        # Unique ID (lowercase, underscores)
status: PROPOSED           # PROPOSED → ACCEPTED → STABLE
depends_on: []             # Other Skills (if any)
relates_to: []             # Related Skills
paths:
  - core/skills/my_custom_skill.py
  - tests/skills/test_my_custom_skill.py
docs:
  - docs/skills/my_custom_skill.md
commits:
  - <commit_hash_when_submitted>
---

# Skill Manifest

**skill_id:** my_custom_skill  
**version:** 1.0.0 (semver)  
**boot_layer:** installed (or: bundled, core, compliance)  
**description:** Brief description of what this Skill does

## Parameters (tunable by optimizer)

| Name | Type | Default | Bounds | Learning |
|------|------|---------|--------|----------|
| param_1 | float | 0.5 | [0.0, 1.0] | Threshold (gradient descent tunes this) |
| param_2 | int | 10 | [1, 100] | Batch size (optimizer adjusts) |

## Dependencies

- **skill_id:** os.delegation_router
  **version:** >=0.1.0

- **skill_id:** os.vibe_engineering
  **version:** ~1.0

## Entry Point

`my_custom_skill:MyCustomSkill.execute`

## Input Schema

```json
{
  "key_1": "number (required)",
  "key_2": "number (required)"
}
```

## Output Schema

```json
{
  "result": "computed value",
  "confidence": "0.0-1.0 score",
  "reasoning": "explanation"
}
```

## Learning Events

Skill emits: `skill_executed` (with confidence score for optimizer)

## Audit Events

Skill may emit: `skill_executed`, `skill_failed`, `skill_timeout` (Phase 1)

---
```

---

## 5. Testing Checklist (Before Submission)

```bash
# Unit tests
pytest tests/skills/test_my_custom_skill.py -v
# Expected: ✅ All pass

# Manifest validation
python3 core/skills/manifest_validator.py < manifest.yaml
# Expected: ✅ Schema valid, DAG resolved

# Integration test (E2E with real Skills)
pytest tests/skills/test_my_custom_skill_e2e.py -v
# Expected: ✅ Execute → output + confidence score

# Docstring check
python3 -m pydoc my_custom_skill.MyCustomSkill
# Expected: ✅ Help text complete

# Performance baseline (latency)
python3 benchmarks/skill_latency.py my_custom_skill
# Expected: p99 latency < 500ms

# Learning events (verify confidence scoring)
python3 core/learning/test_skill_learning.py my_custom_skill
# Expected: ✅ Confidence scores 0.0-1.0, monotonic
```

---

## 6. Submission Workflow

1. **Fork CorvinOS repo**
2. **Create branch:** `skills/my_custom_skill`
3. **Add Skill files:**
   ```
   core/skills/my_custom_skill.py      (implementation)
   core/skills/manifest_my_custom.yaml (metadata)
   tests/skills/test_*.py              (tests)
   docs/skills/my_custom_skill.md      (documentation)
   ```
4. **Pass LDD k=1-5** (self-review via checklist)
5. **Pass all tests** (unit + E2E + performance)
6. **Validate manifest** (schema + DAG)
7. **Create PR** with checklist + test results
8. **Vetting workflow:**
   - Auto-check: Schema, tests, latency (PASS/FAIL in 1h)
   - Human review: Security, correctness, fit (2–5 days)
   - Certification: Bronze (if approved) → Silver (with 10 ratings) → Gold (community favorite)
9. **Merged:** Skill indexed in marketplace

---

## 7. Best Practices

### DO ✅
- Use type hints (`Dict[str, Any]`, `Optional[str]`)
- Emit learning events (for optimizer to tune your Skill)
- Write docstrings (explain I/O clearly)
- Test edge cases (empty input, huge numbers, timeouts)
- Validate manifest (DAG must resolve)
- Keep latency <500ms (Phase 1 timeout is 5s; <500ms = safe)

### DON'T ❌
- Don't block indefinitely (use timeouts)
- Don't modify global state (not composable)
- Don't import heavy ML frameworks in Skill (lazy-load in __init__)
- Don't hardcode parameters (declare in manifest + use self.config)
- Don't skip learning events (optimizer can't learn from you)
- Don't ignore error handling (fail gracefully, return confidence=0.0)

---

## 8. Example: Simple Skill

```python
"""Simple example: RandomRouterSkill (for testing)."""

from typing import Dict, Any
import random


class RandomRouterSkill:
    """Routes to random engine (for testing marketplace)."""

    def __init__(self):
        self.skill_id = "test_random_router"
        self.version = "1.0.0"

    def execute(self, input: Dict[str, Any]) -> Dict:
        engines = ["haiku", "sonnet", "opus"]
        chosen = random.choice(engines)
        confidence = 0.33  # Random choice = low confidence

        return {
            "engine": chosen,
            "confidence": confidence,
            "reasoning": f"Random pick: {chosen}"
        }
```

**Manifest:**
```yaml
skill_id: test_random_router
version: 1.0.0
boot_layer: installed
```

**Test:**
```python
def test_random_router():
    skill = RandomRouterSkill()
    result = skill.execute({"dummy": "input"})
    assert result["engine"] in ["haiku", "sonnet", "opus"]
    assert 0.0 <= result["confidence"] <= 1.0
    assert "reasoning" in result
```

---

## 9. Getting Help

- **Slack:** #skills-authoring (questions, code review)
- **Docs:** [Phase 3a Guide](SKILL_AUTHORING_GUIDE.md)
- **Examples:** `core/skills/os_skills_*.py` (reference implementations)
- **LDD:** [Loop-Driven Engineering](../docs/ldd-mandatory.md)

---

**Ready to write your first Skill? Start with the template above!** 🚀

Submit to `#skills-authoring` for feedback before PR.
