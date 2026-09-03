# Phase A: Legacy Subsystem Deprecation — Execution Plan (Weeks 1–2)

**Owner:** Claude Code (assisted by project maintainer)  
**Timeline:** 8–10 calendar days (2–3 focused work days)  
**Blocker:** None (Phase A is non-breaking, read-only + telemetry only)

---

## Overview

Phase A is **visibility + marking**, not deletion. By end of week 2, we will have:
- ✅ Complete list of all live callsites (Brain, Vibe, Context v1)
- ✅ All old APIs marked @deprecated
- ✅ Telemetry infrastructure ready
- ✅ Plugin ecosystem notified
- ✅ Zero code deleted (fully reversible)

---

## Task Breakdown

### Task 1: Audit Brain Engineering Callsites (1.5 days)

**Goal:** Find every import + dynamic call to `core.brain` outside tests.

**Steps:**

```bash
# Step 1a: Direct imports
cd /home/shumway/projects/CorvinOS
grep -r "from core.brain" core/ tests/ --include="*.py" | grep -v "test_" | tee /tmp/brain_imports.txt
grep -r "import core.brain" core/ tests/ --include="*.py" | grep -v "test_" >> /tmp/brain_imports.txt

# Step 1b: Dynamic calls (getattr, importlib)
grep -r "getattr.*brain\|importlib.*brain" core/ --include="*.py" | grep -v "test_" >> /tmp/brain_imports.txt

# Step 1c: Re-exports in __init__.py
find core/ -name "__init__.py" -exec grep -l "from.*brain" {} \; >> /tmp/brain_reexports.txt

# Step 1d: Plugin scan (check each plugin's imports)
find .corvin/plugins/ -name "*.py" 2>/dev/null | xargs grep -l "from core.brain\|import core.brain" 2>/dev/null >> /tmp/brain_plugin_uses.txt 2>/dev/null || echo "No plugins yet"
```

**Output:** File `/tmp/brain_imports.txt` + `/tmp/brain_reexports.txt` + `/tmp/brain_plugin_uses.txt`

**Document:**
- Create `docs/implementation/AUDIT_RESULTS_BRAIN.md` with formatted table:
  ```
  | File | Line | API | Purpose | Can Migrate? | Notes |
  |---|---|---|---|---|---|
  | core/plugins/plugin_x/handler.py | 42 | get_session_context() | Context setup | YES | Use os.context_adapter |
  | ... | | | | | |
  ```

---

### Task 2: Audit Vibe Engineering Callsites (1.5 days)

**Similar to Task 1, for Vibe:**

```bash
# Step 2a: Direct imports
grep -r "from core.vibe_engineering\|VibeBrainAdapter\|persona_dispatch" core/ tests/ --include="*.py" \
  | grep -v "test_" | tee /tmp/vibe_imports.txt

# Step 2b: Re-exports
find core/ -name "__init__.py" -exec grep -l "vibe" {} \; >> /tmp/vibe_reexports.txt

# Step 2c: Plugin scan
find .corvin/plugins/ -name "*.py" 2>/dev/null | xargs grep -l "vibe_engineering\|VibeBrainAdapter" 2>/dev/null >> /tmp/vibe_plugin_uses.txt 2>/dev/null || true

# Step 2d: Look for persona_slot, persona_dispatch, brain_slot (Vibe-era concepts)
grep -r "persona_slot\|persona_dispatch\|brain_slot" core/ --include="*.py" | grep -v "test_" >> /tmp/vibe_patterns.txt
```

**Document:** `docs/implementation/AUDIT_RESULTS_VIBE.md` (same format as Brain)

---

### Task 3: Audit Context Engineering v1 Callsites (1 day)

**For legacy Context v1 (L24–L25 snapshot + worker):**

```bash
# Step 3a: Legacy snapshot API (deprecated in Phase 4)
grep -r "create_snapshot_v1\|ContextSnapshot\|snapshot_worker" core/ tests/ --include="*.py" \
  | grep -v "test_" | tee /tmp/context_v1_imports.txt

# Step 3b: Re-exports
find core/context_engineering -name "__init__.py" -exec grep -l "snapshot_v1\|worker" {} \; >> /tmp/context_reexports.txt

# Step 3c: Plugin scan
find .corvin/plugins/ -name "*.py" 2>/dev/null | xargs grep -l "ContextSnapshot\|snapshot_v1" 2>/dev/null >> /tmp/context_plugin_uses.txt 2>/dev/null || true
```

**Document:** `docs/implementation/AUDIT_RESULTS_CONTEXT_V1.md`

---

### Task 4: AST-Walk for Dynamic Calls (0.5 days)

**Find getattr / importlib / eval patterns that grep misses:**

```python
# File: scripts/find_dynamic_old_api_calls.py
import ast
import os

OLD_MODULES = {"core.brain", "core.vibe_engineering", "core.context_engineering.v1"}
OLD_CLASSES = {"VibeBrainAdapter", "ContextSnapshot", "BrainSlot"}

class OldAPIFinder(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.results = []

    def visit_Call(self, node):
        # getattr(obj, "brain") or importlib.import_module("core.brain")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in {"import_module", "import_string"}:
                if node.args and isinstance(node.args[0], ast.Constant):
                    if any(old in str(node.args[0].value) for old in OLD_MODULES):
                        self.results.append((self.filename, node.lineno, f"importlib: {node.args[0].value}"))
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in OLD_CLASSES:
            self.results.append((self.filename, node.lineno, f"Direct class: {node.id}"))
        self.generic_visit(node)

for root, dirs, files in os.walk("core"):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path) as fh:
                try:
                    tree = ast.parse(fh.read())
                    finder = OldAPIFinder(path)
                    finder.visit(tree)
                    for result in finder.results:
                        print(f"{result[0]}:{result[1]} | {result[2]}")
                except SyntaxError:
                    pass

```

**Run:**
```bash
python scripts/find_dynamic_old_api_calls.py | tee /tmp/dynamic_calls.txt
```

**Document:** Add findings to the respective AUDIT_RESULTS_* files under "Dynamic/Indirect calls"

---

### Task 5: Consolidate Audit Report (0.5 days)

**Create master spreadsheet:**

File: `docs/implementation/CALLSITE_AUDIT_CONSOLIDATED.md`

```markdown
# Consolidated Callsite Audit — Brain / Vibe / Context v1

**Date:** 2026-09-03  
**Auditor:** Claude Code  
**Total Callsites Found:** XXX

## Summary by Subsystem

| Subsystem | Total Callsites | In-Repo | Plugins | Migrate to Skills | Unknown / Review |
|---|---|---|---|---|---|
| Brain Engineering | N | X | Y | A | B |
| Vibe Engineering | N | X | Y | A | B |
| Context v1 | N | X | Y | A | B |
| **TOTAL** | **N** | **X** | **Y** | **A** | **B** |

## Detailed Findings

[Embed tables from AUDIT_RESULTS_*.md here, one per subsystem]

## Migration Checklist

- [ ] All "Unknown" callsites resolved or marked "defer to Phase B"
- [ ] Plugin maintainers contacted (see Task 7)
- [ ] Ready for Phase B: compat layer wiring
```

---

### Task 6: Mark All Old APIs as @deprecated (1 day)

**Goal:** Add Python `@deprecated` decorator to every old API.

**Files to update:**

1. **`core/brain/conversation_recall.py`**
   ```python
   from functools import wraps
   import warnings

   def deprecated(replacement: str):
       def decorator(func):
           @wraps(func)
           def wrapper(*args, **kwargs):
               warnings.warn(
                   f"{func.__name__} is deprecated; use {replacement} instead. "
                   f"Will be removed in week 7 (Phase C). "
                   f"See ADR-0538 for migration guide.",
                   DeprecationWarning,
                   stacklevel=2
               )
               return func(*args, **kwargs)
           return wrapper
       return decorator

   @deprecated("os.context_adapter Skill (ADR-0532)")
   def get_session_context(task_id: str) -> dict:
       ...

   @deprecated("os.context_adapter Skill (ADR-0532)")
   def recall_recent_sessions(user_id: str, limit: int = 5) -> List[dict]:
       ...
   ```

2. **`core/vibe_engineering/routing.py`**
   ```python
   @deprecated("os.delegation_router Skill (ADR-0532 Phase 1)")
   def delegate_to_persona(request: Request, task_type: str) -> PersonaID:
       ...

   @deprecated("os.delegation_router Skill (ADR-0532 Phase 1)")
   class VibeBrainAdapter:
       ...
   ```

3. **`core/context_engineering/snapshot.py` (legacy v1)**
   ```python
   @deprecated("os.context_adapter Skill + HybridContextModel (ADR-0555)")
   def create_snapshot_v1(context: dict) -> Snapshot:
       ...
   ```

4. **All `__init__.py` re-exports**
   - Check each `from core.brain import ...` in `__init__.py` files
   - Add deprecation warning to the re-export:
     ```python
     from core.brain.conversation_recall import get_session_context as _get_session_context_old
     # Deprecated: use os.context_adapter Skill instead (ADR-0538)
     get_session_context = _get_session_context_old
     ```

**Verification:**
```bash
# Confirm all old functions have @deprecated
grep -r "@deprecated" core/brain core/vibe_engineering core/context_engineering | wc -l
# Should match number of functions in audit report
```

---

### Task 7: Set Up Telemetry (1 day)

**Goal:** Log every deprecated API call with stack trace + timestamp.

**File:** `core/telemetry/deprecated_api_calls.py` (new file)

```python
import logging
import traceback
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

class DeprecatedAPICallLogger:
    """Centralized logging for all deprecated Brain/Vibe/Context-v1 API calls.
    
    Events are:
    - Logged to audit trail (ADR-0314 SkillAuditEvent)
    - Scraped for telemetry dashboard
    - Used to measure Phase C completion (no calls → safe to delete)
    """

    @staticmethod
    def log_call(api_name: str, module: str, caller_file: str, caller_line: int, **kwargs):
        """Log a deprecated API call.
        
        Args:
            api_name: e.g., "get_session_context"
            module: e.g., "core.brain.conversation_recall"
            caller_file: the file that called the deprecated API
            caller_line: line number in the caller
            **kwargs: extra context (task_id, tenant_id, etc.)
        """
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "deprecated_api_call",
            "api_name": api_name,
            "module": module,
            "caller_file": caller_file,
            "caller_line": caller_line,
            "stack_trace": traceback.format_stack(),
            **kwargs
        }
        
        # Log to structured logger (feeds telemetry)
        logger.warning(f"DEPRECATED_API_CALL: {api_name} ({module})", extra=event)
        
        # Also emit to audit trail (opt-in, Phase B)
        # audit_backend.write_event(SkillAuditEvent(...))

DeprecatedAPICallTracker = DeprecatedAPICallLogger
```

**Integration into each deprecated function:**

```python
@deprecated("os.context_adapter Skill")
def get_session_context(task_id: str) -> dict:
    # Log the call
    DeprecatedAPICallTracker.log_call(
        api_name="get_session_context",
        module="core.brain.conversation_recall",
        caller_file=inspect.currentframe().f_back.f_code.co_filename,
        caller_line=inspect.currentframe().f_back.f_lineno,
        task_id=task_id
    )
    # ... rest of function
```

**Dashboard Setup (Phase B, but wire now):**
- Telemetry sink: aggregate `deprecated_api_call` events
- Display: chart of calls/day by API_NAME (show trend toward zero)
- Alerting: if calls spike unexpectedly, investigate

---

### Task 8: Create Migration Guide (0.5 days)

**File:** `docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md`

```markdown
# Migration Guide: Brain / Vibe / Context v1 → ACP Skills

## Quick Reference

| Old API | New Skill | Migration Path |
|---|---|---|
| `get_session_context(task_id)` | `os.context_adapter` | See below: Context Migration |
| `delegate_to_persona(req, type)` | `os.delegation_router` | See below: Routing Migration |
| `VibeBrainAdapter.do_X()` | `os.delegation_router` | |
| `create_snapshot_v1(ctx)` | `HybridContextModel` | See below: Snapshot Migration |

## Context Migration (get_session_context → os.context_adapter)

**Old code:**
```python
from core.brain.conversation_recall import get_session_context
ctx = get_session_context(task_id="my_task")
```

**New code (direct Skill call):**
```python
from core.skills.os_skills.context_adapter import ContextAdapterSkill
skill = ContextAdapterSkill()
ctx = skill.execute(task_id="my_task")
```

**Compat path (Phase B):**
```python
from core.legacy_compat.brain_compat import get_session_context
# Same API, but compat layer routes to Skill internally
ctx = get_session_context(task_id="my_task")
```

[More examples...]

## Testing

- [ ] Old code still works (test against compat layer)
- [ ] New code works (test against Skill directly)
- [ ] E2E test through the Skill's real entry point
```

---

### Task 9: Notify Plugin Ecosystem (0.5 days)

**Goal:** Alert plugin maintainers + provide migration path.

**Action:** Create GitHub issue + send notice:

**Issue title:** "⚠️ Deprecation Notice: Brain/Vibe/Context-v1 APIs to be removed in 4 weeks (ADR-0538)"

**Issue body:**

```markdown
# Deprecation Notice: Legacy Brain / Vibe / Context-v1 APIs

As of 2026-09-03, the following APIs are **deprecated** and will be **deleted on 2026-10-03** (Phase C):

- `core.brain.conversation_recall.get_session_context()`
- `core.vibe_engineering.routing.delegate_to_persona()`
- `core.context_engineering.snapshot.create_snapshot_v1()`
- All re-exports from these modules

**Why?** These were replaced by ACP Skills (ADR-0532):
- Brain → `os.context_adapter` Skill
- Vibe → `os.delegation_router` Skill
- Context v1 → `os.context_adapter` + HybridContextModel (ADR-0555)

**Timeline:**
- **Week 1–2 (Now):** APIs marked @deprecated, compat layer prepared
- **Week 3–4:** Compat layer goes live (old APIs transparently route to Skills)
- **Week 5–8:** Measurement + deletion (only if metrics say "safe")

**For Plugin Authors:**

Your plugin will still work during weeks 1–4 (compat layer handles old API calls), but:

1. **During Phase B (weeks 3–4):** Migrate your plugin to use Skills directly
   - See [Migration Guide](docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md)
   - Update imports + test
   - Submit PR or comment here

2. **After Phase C (week 8+):** Old APIs will be deleted
   - Plugins still on old APIs will crash
   - No automatic compat path after Phase C

**Questions?** Reply in this issue or see ADR-0538 for full decision.
```

**Send to:**
- GitHub issue (pinned)
- Plugin authors (direct message / email)
- CLAUDE.md (update with note)

---

## Deliverables by End of Phase A

| Deliverable | Owner | Status |
|---|---|---|
| AUDIT_RESULTS_BRAIN.md | Claude Code | [ ] |
| AUDIT_RESULTS_VIBE.md | Claude Code | [ ] |
| AUDIT_RESULTS_CONTEXT_V1.md | Claude Code | [ ] |
| CALLSITE_AUDIT_CONSOLIDATED.md | Claude Code | [ ] |
| find_dynamic_old_api_calls.py script | Claude Code | [ ] |
| @deprecated markers on all old APIs | Claude Code | [ ] |
| deprecated_api_calls.py telemetry module | Claude Code | [ ] |
| MIGRATION_GUIDE_TO_SKILLS.md | Claude Code | [ ] |
| GitHub issue + plugin notification | Maintainer | [ ] |
| docs/implementation/LEGACY_DEPRECATION_ROADMAP.md | Claude Code | [ ] |

---

## Exit Checklist (Phase A Complete)

- [ ] All callsites identified + documented (0 "unknown")
- [ ] All old APIs marked @deprecated + warnings clear
- [ ] Telemetry infrastructure ready (logging + dashboard placeholder)
- [ ] Migration guide available + comprehensive
- [ ] Plugin authors notified + timeline clear
- [ ] No code deleted (fully reversible)
- [ ] Ready to move to Phase B (weeks 3–4)

---

## Rollback Plan (if needed before Phase B)

```bash
# If we decide to halt deprecation mid-Phase A:
git revert <commits adding @deprecated>
rm docs/implementation/AUDIT_RESULTS_*.md
rm core/telemetry/deprecated_api_calls.py
# (API continues to work as-is; no compat layer needed yet)
```

**Risk of Phase A rollback:** Low (only adds markers + telemetry, no deletes)

---

## Notes

- Phase A is **read-only** from old code perspective (only adds markers + logging)
- **No compat layer yet** (that's Phase B) — old APIs still call themselves
- **Measurements start in Phase B** — we only have baselines after compat wiring
- **Phase A can pause indefinitely** if findings are unexpected (safe state to hold)
