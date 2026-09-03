# Migration Guide: Brain / Vibe / Context-v1 → ACP Skills

**Status:** Phase A (Deprecation Notice)  
**Effective Date:** 2026-09-03  
**Deadline:** Week 5 (Phase C start; after Phase B compat layer)

---

## Quick Reference

| Old API | Old Module | New Skill | Skill Docs | Migration Effort |
|---|---|---|---|---|
| `get_session_context(task_id)` | `core.brain.conversation_recall` | `os.context_adapter` | ADR-0555 | 1–2h |
| `delegate_to_persona(req, type)` | `core.vibe_engineering.routing` | `os.delegation_router` | ADR-0532 | 1–2h |
| `create_snapshot_v1(ctx)` | `core.context_engineering.snapshot` | `HybridContextModel` | ADR-0555 | 1–2h |
| `VibeBrainAdapter.do_X()` | `core.vibe_engineering` | `os.delegation_router` + Skills | ADR-0532 | 2–4h |
| All Brain recovery logic | `core.brain` | `os.workflow_optimizer` | ADR-0532 Phase 2 | Blocked (Phase 2) |

---

## Migration Paths

### Context Migration: `get_session_context()` → `os.context_adapter` Skill

**Old code:**
```python
from core.brain.conversation_recall import get_session_context

ctx = get_session_context(task_id="my_task")
print(ctx["user_id"])
```

**New code (direct Skill call):**
```python
from core.skills.os_skills.context_adapter import ContextAdapterSkill

skill = ContextAdapterSkill()
result = skill.execute(task_id="my_task")
ctx = result.output  # Same shape as before
print(ctx["user_id"])
```

**Compat path (Phase B):** Old import still works, but routes through Skill transparently:
```python
from core.legacy_compat.brain_compat import get_session_context

# Same API, but compat layer calls os.context_adapter internally
ctx = get_session_context(task_id="my_task")
```

---

### Routing Migration: `delegate_to_persona()` → `os.delegation_router` Skill

**Old code:**
```python
from core.vibe_engineering.routing import delegate_to_persona

engine_id = delegate_to_persona(request, task_type="complex")
```

**New code (direct Skill call):**
```python
from core.skills.os_skills.delegation_router import DelegationRouterSkill

skill = DelegationRouterSkill()
result = skill.execute(request=request, task_type="complex")
engine_id = result.output  # engine_id or routing decision
```

**Compat path (Phase B):**
```python
from core.legacy_compat.vibe_compat import delegate_to_persona

engine_id = delegate_to_persona(request, task_type="complex")
```

---

### Snapshot Migration: `create_snapshot_v1()` → HybridContextModel

**Old code:**
```python
from core.context_engineering.snapshot import create_snapshot_v1

snapshot = create_snapshot_v1(context=ctx)
```

**New code (direct model call):**
```python
from core.skills.os_skills.context_adapter import HybridContextModel

model = HybridContextModel()
snapshot = model.create_snapshot(context=ctx)  # Same shape as before
```

**Compat path (Phase B):**
```python
from core.legacy_compat.context_compat import create_snapshot_v1

snapshot = create_snapshot_v1(context=ctx)
```

---

## Testing

### Unit Test Template (Old API Test)

```python
import pytest
from core.brain.conversation_recall import get_session_context

def test_get_session_context_old_api():
    """Test old API (pre-migration)."""
    ctx = get_session_context(task_id="test_123")
    assert ctx["user_id"] is not None
```

### Unit Test Template (New Skill)

```python
import pytest
from core.skills.os_skills.context_adapter import ContextAdapterSkill

def test_context_adapter_skill():
    """Test new Skill."""
    skill = ContextAdapterSkill()
    result = skill.execute(task_id="test_123")
    assert result.output["user_id"] is not None
```

### E2E Test Template (Real Transport)

```python
import pytest
from core.skills.os_skills.context_adapter import ContextAdapterSkill

def test_context_adapter_e2e():
    """E2E test: Skill through real audit trail."""
    skill = ContextAdapterSkill()
    result = skill.execute(task_id="test_123")
    
    # Verify Skill executed AND audit event was logged
    assert result.status == "success"
    
    # Audit trail should have SkillExecutedEvent
    audit_events = get_audit_trail(task_id="test_123")
    assert any(e.event_type == "skill_executed" and "context_adapter" in e.skill_id 
               for e in audit_events)
```

---

## Phase Timeline

| Phase | Timeline | What Happens | Your Action |
|---|---|---|---|
| **Phase A** | Weeks 1–2 | APIs marked @deprecated, telemetry enabled | Review this guide |
| **Phase B** | Weeks 3–4 | Compat layer live; old APIs route to Skills transparently | Migrate to Skills (or use compat) |
| **Phase C** | Weeks 5–8 | Old code deleted (if telemetry shows <5 calls/day) | Ensure you've migrated (no compat layer) |

---

## Rollout Strategy

### Option 1: Immediate Migration (Recommended)

**During Phase B (weeks 3–4):**
1. Replace old imports with new Skill calls
2. Run tests (compat layer ensures old tests still pass)
3. Deploy
4. Verify audit trail shows Skill execution
5. Remove old imports

**Timeline:** 1–2h per API

### Option 2: Compat Layer Ride-Along (Safe, Lazy)

**During Phase B:**
1. Do nothing (compat layer handles old APIs transparently)
2. Compat layer automatically routes to Skills
3. Telemetry shows migration is happening

**During Phase C:**
1. Migrate to new Skill APIs (before old code is deleted)
2. After Phase C: compat layer itself is deprecated

**Timeline:** Deferred until Phase C start

### Option 3: Hybrid (Best of Both)

**Phase B:**
1. Migrate critical paths to Skills (routing, core context)
2. Leave low-priority code on compat layer

**Phase C:**
1. Migrate remaining code (before old code deletion)

---

## Common Issues & Solutions

### Issue: "Skill not found" during Phase B

**Cause:** Skill not registered in registry during Phase B.  
**Solution:** Check `core/skills/os_skills/__init__.py` includes your Skill. See ADR-0532.

### Issue: Compat layer slow (>5ms per call)

**Cause:** Compat layer adds overhead (thin wrapper + Skill dispatch).  
**Solution:** Expected during Phase B. Native Skill calls are faster. Migrate if latency-sensitive.

### Issue: Audit trail shows "compat_layer_call" instead of "skill_executed"

**Cause:** Compat layer is correctly routing through Skill; audit shows both.  
**Solution:** This is correct. Phase C telemetry counts compat calls → if <5/day, all users migrated.

---

## FAQ

**Q: Can I stay on the old API forever?**  
A: No. Phase C deletes old code (week 8+). After that, old APIs raise `ModuleNotFoundError`.

**Q: Do my tests need to change?**  
A: Not during Phase B (compat layer keeps old APIs working). Change them in Phase C (after migration).

**Q: What if my code is in a Plugin?**  
A: Plugins are handled the same way. Migrate during Phase B, or compat layer carries you until Phase C.

**Q: Can I use both old API and new Skill?**  
A: Yes, during Phase B. But compat layer routes both to the same Skill (wasting resources). Migrate to single call for efficiency.

---

## Support & Questions

- **ADR-0538:** Deprecation covenant + timeline
- **ADR-0532:** ACP Skills architecture
- **ADR-0555:** HybridContextModel + context pipeline
- **GitHub Issue:** (TBD) Link to Phase A tracking issue

