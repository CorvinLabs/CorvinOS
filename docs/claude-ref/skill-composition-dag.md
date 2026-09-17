# Skill Composition & DAG (ADR-0535)

> Load when building OS-Skills that depend on other Skills, or when implementing skill composition.

## Overview

OS-Skills can compose: one skill can call another skill within its execution. ADR-0535 defines:

1. **Dependency Declaration** (manifest.yaml)
2. **DAG Validation** (install-time cycle detection)
3. **Execution Ordering** (topological sort)
4. **Calling Convention** (in-skill composition)
5. **Feedback Isolation** (independent grading)

## Dependency Declaration

Each skill declares its dependencies in SkillMetadata.depends_on:

```python
from core.skills.skill_dag_loader import SkillDependency, VersionConstraint

metadata = SkillMetadata(
    id="os.workflow_optimizer",
    name="workflow_optimizer",
    description="...",
    version="1.0.0",
    origin=SkillOrigin.BUILTIN,
    owner="corvin",
    depends_on=[
        SkillDependency(
            name="os.delegation_router",
            version=">=1.0.0",
            constraint_type=VersionConstraint.SEMVER_RANGE,
            required=True,              # Fail if unavailable
            call_pattern="per_worker",  # once, per_worker, on_demand
            call_budget_ms=50,          # Time budget per call
            timeout_handling="fail_parent",  # or: degrade_gracefully
        ),
        SkillDependency(
            name="os.context_adapter",
            version=">=0.9.0 <2.0.0",
            required=False,  # Continue if unavailable
            call_budget_ms=100,
            timeout_handling="degrade_gracefully",
        ),
    ],
)
```

**Semantics:**

| Field | Meaning |
|---|---|
| `name` | Skill ID (e.g., "os.delegation_router") |
| `version` | Semver constraint (e.g., ">=1.0.0") |
| `constraint_type` | EXACT, SEMVER_RANGE, or ANY |
| `required` | true → missing dep = fail install/execution; false → degrade gracefully |
| `call_pattern` | once, per_worker, on_demand (hint for budgeting) |
| `call_budget_ms` | Time limit per call (summed if called N times) |
| `timeout_handling` | fail_parent (dep timeout → parent times out) or degrade_gracefully |

## DAG Validation (Install-Time)

When a skill is registered, the DAG loader validates its dependencies:

```python
from core.skills.skill_dag_loader import SkillDAGLoader

loader = SkillDAGLoader(registry)

# Validate before execution
report = loader.validate_skill_dependencies("os.workflow_optimizer")

if not report.is_valid:
    print("Blockers:", report.blockers)  # ["Dependency X not found", "Cycle detected", ...]
    print("Warnings:", report.warnings)  # ["Dependency Y missing but soft", ...]
```

**Checks performed:**

1. **Existence**: All dependencies exist in registry
2. **Version Constraints**: Installed versions satisfy declared constraints
3. **Cycle Detection** (DFS): No circular dependencies (A→B→C→A forbidden)
4. **Version Compatibility**: Required and soft deps are tracked separately

**Example: Cycle Detection**

```python
# This will be REJECTED (cycle: A → B → A)
skill_a = SkillMetadata(
    id="test.a",
    depends_on=[SkillDependency(name="test.b", version=">=1.0.0", required=True)]
)
skill_b = SkillMetadata(
    id="test.b",
    depends_on=[SkillDependency(name="test.a", version=">=1.0.0", required=True)]
)
# Validation will report: "Cyclic dependency detected: test.a → test.b → test.a"
```

## Execution Ordering (Topological Sort)

Before executing a set of skills, compute their execution order using Kahn's algorithm:

```python
from core.skills.skill_dag_loader import topological_sort_skills

# Load skills in dependency order
loaded_skills, error = loader.load_with_dag(
    skill_ids=["os.workflow_optimizer", "os.context_adapter", "os.delegation_router"],
    strict_mode=True
)

if error:
    print("Load failed:", error)
    return

# loaded_skills is now in topological order:
# [os.delegation_router, os.context_adapter, os.workflow_optimizer]
# Dependencies are ready before dependents
```

**Ordering guarantees:**

- If skill A depends on skill B, then B comes before A in the sorted list
- Independent skills can be in any order (both are valid)
- Sort is deterministic: same input → same output
- Cycles are impossible (caught at validation time)

**Example: Diamond Graph**

```
         D (depends on B, C)
        / \
       B   C (both depend on A)
        \ /
         A

Topological order: [A, B, C, D] or [A, C, B, D] (B/C interchangeable)
Always: A comes first, D comes last, B and C before D
```

## Calling Convention (In-Skill Composition)

A skill calls a dependency via the registry's `execute()` method:

```python
class WorkflowOptimizerSkill(Skill):
    def execute(self, input: dict) -> dict:
        # Call a dependency (required)
        result = self.registry.execute(
            skill_id='os.delegation_router',
            input={'task_shape': 'big_data', 'context_size': 50000},
            timeout_ms=50,
            lom='core/skills/os_skills.py:WorkflowOptimizerSkill.execute',
            # LoM (Line of Moral Responsibility) is REQUIRED (ADR-0537)
        )
        
        if result.status != 'success':
            if self.dependency_required:
                raise SkillDependencyError(f"Required skill failed: {result.error_message}")
            else:
                # Degrade gracefully
                output = {'engine': 'native', 'confidence': 0.0}  # Fallback
                audit_log('SKILL_DEP_DEGRADATION', dep='os.delegation_router')
        else:
            output = result.output
        
        return output
```

**Execution isolation:**

- Calling skill waits for result (synchronous)
- Timeout enforced: if dep exceeds `call_budget_ms`, call times out
- Exception caught: if dep crashes, calling skill catches and handles per `timeout_handling`
- LoM is required: every call must name the line of moral responsibility (ADR-0537)

## Feedback Isolation (Independent Grading)

Each skill's feedback is scored independently:

```python
# Skill execution logs to audit trail with:
# - skill_id, status, output, execution_time_ms, error_message
# - lom (line of moral responsibility)
# - tenant_id (GDPR Art. 5 isolation)

# Learning system (ADR-0314) scores independently:
# - os.delegation_router: scored on routing correctness (yes/no user feedback)
# - os.context_adapter: scored on context relevance
# - os.workflow_optimizer: scored on wall-clock efficiency

# If delegation_router times out during a call:
# - delegation_router gets a "timeout" event + failed execution
# - workflow_optimizer gets a degradation event (if soft dep)
# - Optimizer's score is independent of router's failure
```

**Why:** Composition shouldn't cascade blame. If os.workflow_optimizer calls os.delegation_router, and delegation times out, should optimizer be penalized? Only if it's a required dependency and the optimizer handles it incorrectly. Soft deps can fail without penalizing the parent.

## Version Compatibility (Future)

As Skills evolve, maintain compatibility:

```yaml
# os.workflow_optimizer manifest — version compatibility

compatibility:
  - orchestrator_version: "1.0.0"
    requires:
      os.delegation_router: ">=1.0.0, <2.0.0"
      os.context_adapter: ">=0.9.0, <2.0.0"
    
  - orchestrator_version: "2.0.0"
    requires:
      os.delegation_router: ">=1.2.0"  # Needed new feature
      os.context_adapter: ">=1.0.0"    # Breaking change
    
  - orchestrator_version: "2.1.0"
    requires:
      os.delegation_router: ">=1.2.0, <3.0.0"
      os.context_adapter: ">=1.0.0, <3.0.0"
```

When a dependency is upgraded, check the compatibility table. If the current skill version is incompatible, alert the operator.

## API Reference

### SkillDAGLoader

```python
loader = SkillDAGLoader(registry)

# Load skills in dependency order
loaded_skills, errors = loader.load_with_dag(
    skill_ids=["os.delegation_router", "os.workflow_optimizer"],
    strict_mode=True,  # If True, any error blocks load; if False, warnings only
)

# Validate a single skill
report = loader.validate_skill_dependencies("os.workflow_optimizer")
# Returns: ValidationReport(is_valid, blockers, warnings)
```

### Global Load Function

```python
from core.skills.skill_registry_phase1 import load_skills_with_dag

loaded, errors = load_skills_with_dag(
    registry=None,  # None → get_registry()
    skill_ids=["os.delegation_router"],
    strict_mode=True,
)
```

### Version Constraints

```python
from core.skills.skill_dag_loader import VersionConstraint

SkillDependency(
    name="os.delegation_router",
    version="1.2.3",
    constraint_type=VersionConstraint.EXACT,  # Exact match: 1.2.3
)

SkillDependency(
    name="os.delegation_router",
    version=">=1.0.0",
    constraint_type=VersionConstraint.SEMVER_RANGE,  # Semver range: >=1.0.0
)

SkillDependency(
    name="os.delegation_router",
    version="*",
    constraint_type=VersionConstraint.ANY,  # Any version
)
```

## Audit Trail Integration

Every skill execution is audit-logged with:

```json
{
  "event_type": "skill.executed",
  "skill_id": "os.workflow_optimizer",
  "status": "success",
  "decision": {
    "engine": "native",
    "confidence": 0.95,
    "workflow_optimized": true
  },
  "execution_time_ms": 123.4,
  "timestamp": "2026-09-17T12:34:56.789Z",
  "lom": "core/skills/os_skills.py:WorkflowOptimizerSkill.execute",
  "lom_hash": "sha256(...)",  # Cryptographic binding to source
  "tenant_id": "_default"
}
```

The audit chain is immutable and hash-linked (GDPR Art. 30, 32). Validation failures are also logged.

## Testing

### Unit Tests (Gate 3)

```bash
pytest core/skills/tests/test_skill_dag_gate3.py -xvs
# Tests: 17 validation, 8 sort, 5 loader, 2 performance
```

### Adversarial Tests (Gate 4)

```bash
pytest core/skills/tests/test_skill_dag_gate4.py -xvs
# Tests: circular deadlock, missing deps, version conflicts, DAG explosion, edge cases
```

### E2E Integration Tests

```bash
pytest core/skills/tests/test_skill_dag_e2e_integration.py -xvs
# Tests: real skill instances, audit trail, compliance tier enforcement
```

## Must NOT do

- **Don't create cycles** — the DAG loader will reject them at install-time
- **Don't skip LoM** — every skill call must include `lom='file:function'` (ADR-0537)
- **Don't weaken soft deps** — they should continue gracefully if unavailable
- **Don't time out silently** — respect `timeout_handling` (fail_parent vs degrade_gracefully)
- **Don't blame one skill for another's failure** — feedback is independent
- **Don't hardcode dependencies** — always declare them in SkillMetadata.depends_on

## Related

- **ADR-0532**: OS-Skills Architecture (high-level system design)
- **ADR-0533**: Skill Feedback Integration (learning loop)
- **ADR-0534**: Skill Versioning (version management)
- **ADR-0537**: LoM Cryptographic Binding (line-of-responsibility proof)
- **ADR-0314**: Learning Infrastructure (feedback signals)
