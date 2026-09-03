# ⚠️ Plugin Notice: Brain/Vibe/Context-v1 APIs Deprecated (ADR-0538)

**Effective Date:** 2026-09-03  
**Deadline:** 2026-10-03 (Phase C start; old code will be deleted)

---

## Summary

Three legacy subsystems are being phased out in favor of **ACP Skills (ADR-0532+)**:

| Old API | Replacement Skill | Migration Guide |
|---|---|---|
| `core.brain.*` | `os.context_adapter` + `os.workflow_optimizer` | [docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md](MIGRATION_GUIDE_TO_SKILLS.md) |
| `core.vibe_engineering.*` | `os.delegation_router` | [docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md](MIGRATION_GUIDE_TO_SKILLS.md) |
| `core.context_engineering.snapshot.*` | `os.context_adapter` + HybridContextModel | [docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md](MIGRATION_GUIDE_TO_SKILLS.md) |

---

## Timeline

### Phase A (Weeks 1–2): Deprecation Notice [NOW]
- APIs marked @deprecated
- Telemetry infrastructure enabled
- Migration guide published
- **Your action:** Start planning migration

### Phase B (Weeks 3–4): Compat Layer [SAFE]
- Old APIs transparently route to new Skills via compat layer
- Old code stays in repository (no deletions yet)
- Plugins can migrate at own pace OR stay on compat layer
- **Your action:** Migrate your plugin OR use compat layer (both work)

### Phase C (Weeks 5–8): Measured Deletion [HARD DEADLINE]
- Old code deleted (only if telemetry shows <5 calls/day)
- Compat layer retired
- Plugins MUST have migrated by now OR will crash
- **Your action:** Complete migration before week 5

---

## What You Need to Do

### Option 1: Migrate Your Plugin (Recommended)

**During Phase B (weeks 3–4):**

1. **Audit your plugin** for old API imports:
   ```bash
   grep -r "from core.brain\|from core.vibe_engineering\|ContextSnapshot" your_plugin/ --include="*.py"
   ```

2. **If found:** Follow migration guide
   - Replace old imports with new Skill imports
   - Test with new Skill interface
   - Submit PR or notify maintainer

3. **Timeline:** 1–2 hours per API call site

4. **Test:** Ensure plugin still works
   ```bash
   pytest your_plugin/tests/ -v
   ```

5. **Deploy:** Update plugin in production before Phase C

### Option 2: Ride Compat Layer (Works until Phase C)

**During Phase B:**
- Do nothing (compat layer handles old APIs transparently)
- Plugin still works without changes

**During Phase C:**
- Compat layer is deleted
- Plugin crashes if still using old APIs
- **Downtime risk:** HIGH

### Option 3: Hybrid (Best of Both Worlds)

**Phase B:**
- Migrate critical paths → new Skills
- Leave non-critical code on compat layer

**Phase C:**
- Migrate remaining code (before old code deletion)

---

## Migration Example

### Before (Old API)
```python
# your_plugin/handler.py
from core.brain.conversation_recall import get_session_context
from core.vibe_engineering.routing import delegate_to_persona

def handle_request(task):
    ctx = get_session_context(task.id)
    engine = delegate_to_persona(task.request, task.type)
    ...
```

### After (New Skills)
```python
# your_plugin/handler.py
from core.skills.os_skills.context_adapter import ContextAdapterSkill
from core.skills.os_skills.delegation_router import DelegationRouterSkill

def handle_request(task):
    ctx_skill = ContextAdapterSkill()
    ctx = ctx_skill.execute(task_id=task.id).output
    
    route_skill = DelegationRouterSkill()
    engine = route_skill.execute(request=task.request, task_type=task.type).output
    ...
```

### Using Compat Layer (No Changes)
```python
# your_plugin/handler.py — NO CHANGES NEEDED
from core.brain.conversation_recall import get_session_context  # Still works!
from core.vibe_engineering.routing import delegate_to_persona     # Still works!

# compat layer routes these to Skills automatically during Phase B
```

---

## FAQ

**Q: Do I have to migrate?**  
A: Yes, by week 5 (Phase C start). After that, old APIs don't exist.

**Q: Can I use compat layer during Phase B?**  
A: Yes. compat layer is safe + transparent during Phase B. Migrate whenever convenient.

**Q: What if I don't migrate in time?**  
A: Your plugin will crash in Phase C when old code is deleted. Downtime + data loss risk.

**Q: Where's the migration guide?**  
A: [docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md](MIGRATION_GUIDE_TO_SKILLS.md)

**Q: What if my plugin is unmaintained?**  
A: See "Laggard Plugins" below.

**Q: Will my tests still pass?**  
A: Yes, during Phase B (compat layer + old code still present). In Phase C, must use new APIs.

---

## Laggard Plugins (Unmaintained or Unresponsive)

**If your plugin isn't migrated by Phase B end (week 4):**

1. **Notify the maintainer** → they have 1 week to migrate (week 5)
2. **If unresponsive:** Plugin is marked "deprecated; maintained by community"
3. **In Phase C:** Plugin owner must have migrated OR plugin will crash

**Option for plugin owners:**
- Submit a PR to migrate (maintainers will review + merge)
- Notify us if you need help (we'll provide migration examples)

---

## Testing & Validation

### Unit Test (New Skill)
```python
def test_my_plugin_with_new_skill():
    from core.skills.os_skills.context_adapter import ContextAdapterSkill
    skill = ContextAdapterSkill()
    result = skill.execute(task_id="test_123")
    assert result.output is not None
```

### E2E Test (Real Audit Trail)
```python
def test_my_plugin_e2e():
    result = handle_request(task)
    # Verify Skill was called (check audit trail)
    audit_events = get_audit_trail(task.id)
    assert any("context_adapter" in e.skill_id for e in audit_events)
```

---

## Support

- **Migration Guide:** [docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md](MIGRATION_GUIDE_TO_SKILLS.md)
- **ADR-0538:** [ADR-0538 (Deprecation Covenant)](../../Corvin-ADR/decisions/ADR-0538-legacy-subsystem-deprecation-covenant.md)
- **ADR-0532:** [ADR-0532 (ACP Skills Architecture)](../../Corvin-ADR/decisions/ADR-0532-agentic-control-plane.md)
- **GitHub Issues:** (TBD) Link for questions + help

---

## Dates at a Glance

| Date | Phase | What | Your Action |
|---|---|---|---|
| 2026-09-03 | A | Deprecation notice + telemetry | Plan migration |
| 2026-09-17 | A end | Audit complete | Start migration |
| 2026-10-01 | B end | Compat layer stable | Complete migration |
| 2026-10-08 | C start | Old code deleted | Deploy migrated plugin |

**Do not delay. Old code deletes 2026-10-08.**

