# Brain/Vibe-v1/Context-v1 Deprecation Migration Guide

**Status:** ADR-0538 Phase A (Audit + Deprecation Warnings)  
**Timeline:** Phase A (weeks 1–2) → Phase B (weeks 3–4) → Phase C (weeks 5–8)

---

## Overview

CorvinOS is phasing out legacy subsystems (`core.brain`, `core.vibe`, `core.context_pipeline_v1`) in favor of **ACP Skills** (Agentic Control Plane) and modern infrastructure.

| Legacy Component | Replacement (ACP Skills) | ADR |
|---|---|---|
| Brain subsystem (context tracking, guidance) | `os.context_adapter` Skill | ADR-0532 Phase 1 |
| Workflow optimization + guidance | `os.workflow_optimizer` Skill | ADR-0532 Phase 2 |
| Decision history + learning | ADR-0314 Learning Infrastructure | ADR-0314 |
| Context preservation | `core.context_engineering` (L10) | ADR-0555/0556/0557 |
| Safety validation (high-risk gates) | L44 house_rules_enforcer + consent gates | ADR-0527 |

---

## Phase A: Audit + Deprecation Warnings (NOW — Weeks 1–2)

**Goal:** Mark deprecated code; measure live usage via audit trail.

### Deprecated Call Sites (Audit Result)

**Test Files (5 files, 7 import lines):**

| File | Import | Count | Status |
|---|---|---|---|
| `tests/adversarial/test_week5_adversarial_vectors.py` | `from core.brain.task_context_tracker import TaskContextTracker, SafetyValidator, TaskContext` | 1 | ⚠️ Marked `@pytest.mark.deprecated` |
| `tests/e2e/test_workflow_phase2_basics.py` | `from core.brain.workflow_bridge import WorkflowBridge` | 1 | ⚠️ Marked `@pytest.mark.deprecated` |
| `tests/integration/test_week4_context_e2e.py` | `from core.brain.task_context_tracker import TaskContextTracker, TaskContext, TaskStatus, SafetyValidator` | 1 | ⚠️ Marked `@pytest.mark.deprecated` |
| `tests/e2e/test_production_validation_complete.py` | `# from core.brain.execution_context import ExecutionContext` (COMMENTED OUT) | 1 | ✅ Already disabled |
| `tests/integration/test_phase_b_compat_layer_e2e.py` | Reference in test string (not executable import) | 1 | ✅ Test-only reference |

**Production Code (0 files):**
- ✅ No production call sites found
- Compliance gates (`core/compliance/phase_c_gates/`) monitor for violations

**Warnings Added:**
- `core/brain/task_context_tracker.py` — TaskContextTracker + SafetyValidator (2 warnings)
- `core/brain/workflow_bridge.py` — WorkflowBridge (1 warning)
- `core/brain/__init__.py` — Already had deprecation notice (no change)

---

## Phase B: Compat Layer (Weeks 3–4)

**Goal:** Route deprecated APIs → ACP Skills transparently; zero breaking changes.

### Roadmap

1. Create `core/legacy_compat/brain_shim.py` — wraps old Brain APIs
2. Implement skill-delegation: TaskContextTracker calls → `os.context_adapter` Skill
3. Implement skill-delegation: WorkflowBridge calls → `os.workflow_optimizer` Skill
4. Audit both paths (legacy and new); measure convergence

### Migration Path (for manual updates)

**If you are calling `TaskContextTracker`:**

```python
# OLD (deprecated)
from core.brain.task_context_tracker import TaskContextTracker
tracker = TaskContextTracker()
await tracker.push_context(context)

# NEW (ACP Skills)
from core.skills.os_skills.context_adapter import ContextAdapterSkill
skill = ContextAdapterSkill()
await skill.execute(action="push_context", context=context)
```

**If you are calling `WorkflowBridge`:**

```python
# OLD (deprecated)
from core.brain.workflow_bridge import WorkflowBridge
bridge = WorkflowBridge(exec_ctx, bus)
await bridge.subscribe_to_workflow_events()

# NEW (ACP Skills)
from core.skills.os_skills.workflow_optimizer import WorkflowOptimizerSkill
skill = WorkflowOptimizerSkill()
await skill.execute(action="subscribe_to_workflow_events")
```

**If you are calling `SafetyValidator`:**

```python
# OLD (deprecated)
from core.brain.task_context_tracker import SafetyValidator
validator = SafetyValidator(tracker)
is_safe, reason = await validator.validate_guidance(text, risk_level)

# NEW (ACP Skills + L44 Gates)
from core.security.house_rules import house_rules_enforcer
from core.security.consent import consent_gate
is_safe = await house_rules_enforcer(guidance_text=text)
if not is_safe:
    is_allowed = await consent_gate(action="high_risk_guidance", user_id=...)
```

---

## Phase C: Measured Deletion (Weeks 5–8)

**Goal:** Delete deprecated code after telemetry confirms 0 live usage.

### Verification Steps

1. Check audit trail for last `TaskContextTracker` instantiation
2. Check audit trail for last `WorkflowBridge` instantiation
3. Verify all test imports have been updated or marked deprecated
4. Delete files:
   - `core/brain/task_context_tracker.py`
   - `core/brain/workflow_bridge.py`
   - `core/brain/__init__.py`
5. Verify build + all tests still pass
6. Commit with `[phase-c-deletion]` tag

---

## Testing Strategy

### Phase A Tests

- ✅ Deprecation warnings are logged when classes are instantiated
- ✅ Test suite still passes with deprecated imports (compat layer not yet active)
- ✅ Compliance gates do not block deprecated code (Phase A)

### Phase B Tests

- Deprecated APIs route to Skills transparently
- Old test suite passes without changes (compat layer intercepts)
- New test suite uses Skills directly (no deprecated paths)
- Audit trail shows both old (compat) and new (Skills) execution

### Phase C Tests

- Deprecated imports raise ImportError (code deleted)
- Full migration complete (all old tests updated or deleted)
- No production impact

---

## Troubleshooting

### "TaskContextTracker instantiation warning in my tests"

This is expected (Phase A). The deprecation warning is logged; tests continue to pass.

**Action:** Mark the test with `@pytest.mark.deprecated` (Phase A) or update it to use Skills (Phase B).

### "I need to use Brain APIs in production code"

Don't. Contact the team; a compat layer will be created (Phase B) to route your calls to Skills without code changes.

### "When will this be deleted?"

Phase C starts after telemetry confirms 0 usage. Expected: end of week 8 (2026-09-25). Until then, APIs are stable.

---

## References

- **ADR-0538:** Legacy Cleanup Initiative (Corvin-ADR repo)
- **ADR-0532:** ACP Skills Roadmap (Phases 1–3, Corvin-ADR repo)
- **ADR-0314:** Learning Infrastructure (Corvin-ADR repo)
- **L10 (Path Gate):** `docs/claude-ref/layer-10-path-gate.md`
- **L44 (House Rules):** `docs/claude-ref/layer-44-house-rules.md`

---

**Last Updated:** 2026-09-10  
**Next Phase:** Phase B (Compat Layer) — ~2026-09-17
