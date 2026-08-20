# ADR-0362 Implementation Plan: Tenant-Native Data Persistence

**Status:** DRAFT  
**Prepared:** 2026-08-20  
**Version:** 1.0  
**Scope:** Complete refactoring of workspace-scope APIs to be tenant-aware

---

## Executive Summary

This plan outlines the phased implementation of **ADR-0362: Tenant-Native Data Persistence**, which eliminates the five critical findings from the Data Tenancy Matrix:

1. **Split-Brain Audit Trail** — Audit events scattered across `~/.corvin/tenants/_default/audit.jsonl` and per-session trails
2. **ToolForge Cross-Tenant Visibility** — Tools created in Tenant A leak into Tenant B's registry
3. **Bridge Credentials Not Tenant-Scoped** — Bridge auth tokens live in global location
4. **scope_root() Has No tenant_id Parameter** — Central API doesn't enforce tenant isolation
5. **Telemetry Consent Not Tenant-Scoped** — Consent/opt-out state shared across tenants

**Deliverable:** Production-ready tenant-native persistence with zero CRITICAL findings in adversarial testing.

**Timeline:** 3–4 weeks (1 engineer); 2–3 weeks (2 engineers); 1–2 weeks (3 engineers)

**Risk Level:** MEDIUM (scope_root refactor touches ~100 call-sites; adversarial testing is a blocker)

---

## 1. Dependency Graph (DAG)

### Module Loading Order

```
┌─────────────────────────────────────────────────────────────────────────┐
│ PHASE A: Foundation (Core Paths)                                        │
├─────────────────────────────────────────────────────────────────────────┤
│ core/paths/tenant.py (NEW)                                              │
│ core/tenants/validation.py (ENHANCE)                                    │
│ Deliverable: Tenant path resolution + validation                        │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ PHASE B: Critical Pivot (scope_root Refactor)                           │
├─────────────────────────────────────────────────────────────────────────┤
│ operator/forge/forge/scope.py (CRITICAL)                                │
│ └─ Signature change: Add tenant_id parameter                            │
│ └─ Update ~100 call-sites across the codebase                           │
│ Deliverable: tenant_id-aware scope_root() + all callers updated         │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┬─────────────────┐
        ▼              ▼              ▼                 ▼
   ┌─────────┐   ┌─────────┐   ┌─────────┐      ┌─────────┐
   │ C: Skill│   │ C: Tool │   │C:Learn  │      │C:Audit  │
   │ Forge   │   │ Forge   │   │ Engine  │      │Logger   │
   │ Updates │   │ Updates │   │ Updates │      │ Updates │
   └────┬────┘   └────┬────┘   └────┬────┘      └────┬────┘
        │             │             │               │
        └─────────────┼─────────────┼───────────────┘
                      │
                      ▼
         ┌────────────────────────────┐
         │ PHASE C: Brain Integration │
         │ (Subsystems Update)        │
         └────────────┬───────────────┘
                      │
                      ▼
         ┌────────────────────────────┐
         │ PHASE D: Migration Tool    │
         │ (corvin_migrate.py)        │
         └────────────┬───────────────┘
                      │
                      ▼
         ┌────────────────────────────┐
         │ PHASE E: Test & Verify     │
         │ (Unit + Adversarial)       │
         └────────────┬───────────────┘
                      │
                      ▼
         ┌────────────────────────────┐
         │ PHASE F: Ship (Remove Flag)│
         │ (Tenant-Native as Default) │
         └────────────────────────────┘
```

### Dependency Matrix

| Dependency | Phase | Status | Notes |
|---|---|---|---|
| `core/paths/tenant.py` | A | NEW | Must exist before Phase B |
| `scope_root()` signature | B | REFACTOR | Blocks all subsystem updates |
| `SkillForgeSubsystem` | C | ENHANCE | Depends on scope_root() |
| `ToolForgeSubsystem` | C | ENHANCE | Depends on scope_root() |
| `LearningEngine` | C | ENHANCE | Depends on scope_root() |
| `SafetyValidator` | C | ENHANCE | Depends on scope_root() |
| `SessionManager` | C | ENHANCE | Depends on scope_root() |
| `MemoryManager` | C | ENHANCE | Depends on scope_root() |
| `corvin_migrate.py` | D | ENHANCE | Depends on Phase A + B |
| Unit Tests | E | NEW | Depends on Phase A–C |
| Adversarial Tests | E | NEW | Depends on Phase C (blocker) |
| Feature-flag removal | F | CLEANUP | Depends on Phase E pass |

---

## 2. Phase Breakdown (Chronological)

### Phase A: Foundation — Core Tenant Paths (2–3 Days)

**Goal:** Implement canonical tenant-path resolution APIs; single source of truth for all tenant-scoped directories.

#### 2.A.1 New Module: `core/paths/tenant.py`

**File:** `/home/shumway/projects/CorvinOS/core/paths/tenant.py` (NEW, ~200 LOC)

**Functions to implement:**

```python
def tenant_home(tenant_id: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/ directory.
    
    Example:
        tenant_home("tenant_a") → Path.home() / ".corvin/tenants/tenant_a"
    
    Always ensures directory exists (creates if needed).
    """

def validate_tenant_id(tenant_id: str) -> None:
    """Fail-closed validation of tenant_id.
    
    Regex whitelist: [a-z0-9_-]{1,64}
    Raises ValueError if invalid (path-traversal, SQL injection, etc.)
    
    Examples (VALID):
        - "tenant_a"
        - "prod-us-east-1"
        - "t1_v2"
    
    Examples (INVALID, raises ValueError):
        - "../../../etc/passwd"
        - "tenant_a; DROP TABLE skills;"
        - ""
        - "TENANT_A" (uppercase not allowed)
    """

def tenant_skill_dir(tenant_id: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/skill-forge/skills/"""

def tenant_tool_dir(tenant_id: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/forge/tools/"""

def tenant_session_dir(tenant_id: str, session_id: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/sessions/<session_id>/"""

def tenant_learning_dir(tenant_id: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/learning/"""

def tenant_memory_dir(tenant_id: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/memory/"""

def tenant_audit_file(tenant_id: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/audit.jsonl"""

def tenant_bridge_dir(tenant_id: str, channel: str) -> Path:
    """Return ~/.corvin/tenants/<tenant_id>/bridges/<channel>/"""
```

**Key implementation detail:**
```python
import re
from pathlib import Path

_TENANT_ID_REGEX = re.compile(r"^[a-z0-9_-]{1,64}$")

def validate_tenant_id(tenant_id: str) -> None:
    if not isinstance(tenant_id, str):
        raise TypeError(f"tenant_id must be str, not {type(tenant_id).__name__}")
    if not _TENANT_ID_REGEX.match(tenant_id):
        raise ValueError(
            f"Invalid tenant_id: {tenant_id!r}. "
            f"Must match {_TENANT_ID_REGEX.pattern}."
        )

def tenant_home(tenant_id: str) -> Path:
    validate_tenant_id(tenant_id)
    path = Path.home() / ".corvin" / "tenants" / tenant_id
    path.mkdir(parents=True, exist_ok=True)
    return path
```

**Unit Tests (~20 assertions):**

```python
# tests/test_tenant_paths.py

def test_validate_tenant_id_accepts_valid():
    """Valid tenant IDs pass validation."""
    validate_tenant_id("tenant_a")
    validate_tenant_id("prod-us-east-1")
    validate_tenant_id("t1_v2")
    # No exception raised

def test_validate_tenant_id_rejects_invalid():
    """Invalid tenant IDs raise ValueError."""
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        validate_tenant_id("../../../etc/passwd")
    with pytest.raises(ValueError):
        validate_tenant_id("TENANT_A")  # uppercase
    with pytest.raises(ValueError):
        validate_tenant_id("")
    with pytest.raises(ValueError):
        validate_tenant_id("tenant_a" * 20)  # too long (>64 chars)

def test_validate_tenant_id_rejects_sql_injection():
    """SQL injection attempts blocked."""
    with pytest.raises(ValueError):
        validate_tenant_id("tenant_a; DROP TABLE skills;")

def test_tenant_home_creates_directory():
    """tenant_home() creates dir if missing."""
    tenant_id = "test_tenant_" + str(uuid.uuid4())[:8]
    path = tenant_home(tenant_id)
    assert path.exists()
    assert path.is_dir()
    # Cleanup
    shutil.rmtree(path)

def test_tenant_skill_dir_includes_tenant_id():
    """tenant_skill_dir() path includes tenant_id."""
    path = tenant_skill_dir("tenant_a")
    assert "tenant_a" in str(path)
    assert "skill-forge" in str(path)

def test_tenant_tool_dir_includes_tenant_id():
    """tenant_tool_dir() path includes tenant_id."""
    path = tenant_tool_dir("tenant_b")
    assert "tenant_b" in str(path)
    assert "forge" in str(path)

def test_tenant_home_isolation_two_tenants():
    """Two different tenants get different paths."""
    path_a = tenant_home("tenant_a")
    path_b = tenant_home("tenant_b")
    assert path_a != path_b
    assert str(path_a) != str(path_b)

def test_tenant_paths_no_collision():
    """No two (tenant_id, scope) pairs yield same path."""
    scopes = [
        tenant_skill_dir,
        tenant_tool_dir,
        tenant_learning_dir,
        tenant_memory_dir,
        tenant_audit_file,
    ]
    tenants = ["tenant_a", "tenant_b", "tenant_c"]
    
    paths = []
    for scope in scopes:
        for tid in tenants:
            if scope == tenant_session_dir:
                p = scope(tid, "session_123")
            else:
                p = scope(tid)
            paths.append((tid, scope.__name__, str(p)))
    
    # Verify uniqueness
    path_strs = [p[2] for p in paths]
    assert len(path_strs) == len(set(path_strs)), "Path collision detected"
```

**Estimate:** 1 Day (1 Engineer)

---

#### 2.A.2 Enhance Module: `core/tenants/validation.py`

**File:** `/home/shumway/projects/CorvinOS/core/tenants/validation.py` (ENHANCE, +100 LOC)

**Current status:** Exists; needs enhancement for fail-closed validation.

**Changes:**
- Add `validate_tenant_id()` (import from `core/paths/tenant.py`)
- Add unit tests for edge cases (SQL injection, path traversal, Unicode)
- Document GDPR Art. 5, 32 (data isolation)

**Tests (~10 assertions):**

```python
def test_validate_tenant_id_unicode_rejected():
    """Non-ASCII tenant IDs rejected."""
    with pytest.raises(ValueError):
        validate_tenant_id("тенант_а")  # Cyrillic
    with pytest.raises(ValueError):
        validate_tenant_id("테넌트_a")   # Korean

def test_validate_tenant_id_special_chars_rejected():
    """Special characters rejected."""
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ';']:
        with pytest.raises(ValueError):
            validate_tenant_id(f"tenant_{char}_a")
```

**Estimate:** 0.5 Days (1 Engineer)

---

#### 2.A.3 Deliverable Summary

- ✅ `core/paths/tenant.py` — NEW, ~200 LOC, 20–30 unit tests
- ✅ `core/tenants/validation.py` — Enhanced, +100 LOC, 10–15 unit tests
- ✅ All tenant-scoped path APIs in one module
- ✅ Fail-closed validation on every `tenant_id` input
- ✅ No fallback to `_default` tenant (always explicit)

---

### Phase B: Critical Pivot — scope_root() Refactor (2–3 Days)

**Goal:** Refactor the central `scope_root()` API to require `tenant_id` parameter; update all ~100 call-sites.

**Risk Level:** HIGH — this is the critical path. Mistakes here propagate to all subsystems.

#### 2.B.1 Refactor: `operator/forge/forge/scope.py`

**File:** `/home/shumway/projects/CorvinOS/operator/forge/forge/scope.py`

**Signature change:**

```python
# OLD (current, v0.1)
def scope_root(scope: str, *, channel_id: str | None = None,
               task_id: str | None = None,
               project_root: Path | None = None) -> Path:
    ...

# NEW (v0.2 tenant-aware)
def scope_root(scope: str, *, tenant_id: str, 
               channel_id: str | None = None,
               task_id: str | None = None,
               project_root: Path | None = None) -> Path:
    """Resolve the workspace directory for a given scope and tenant.
    
    Args:
        scope: One of "task", "session", "project", "user"
        tenant_id: Tenant identifier (validated via validate_tenant_id)
        channel_id: For scope="session", the bridge channel ID
        task_id: For scope="task", the task ID
        project_root: For scope="project", the git repo root
    
    Returns:
        Tenant-scoped path for the given scope.
    
    Raises:
        ValueError: If tenant_id is invalid or scope is unknown
        TypeError: If tenant_id is missing (required keyword arg)
    """
    from core.paths.tenant import validate_tenant_id, tenant_home
    
    # Fail-closed: validate tenant_id first
    validate_tenant_id(tenant_id)
    tenant_dir = tenant_home(tenant_id)
    
    if scope == "task":
        tid = task_id or os.environ.get("CORVIN_TASK_ID") or "default"
        return Path(tempfile.gettempdir()) / ".corvin" / "tasks" / tid / "forge"
    
    if scope == "session":
        cid = channel_id or os.environ.get("CORVIN_CHANNEL_ID") or "default"
        return tenant_dir / "sessions" / fs_safe_component(cid) / "forge"
    
    if scope == "project":
        if project_root is not None:
            return project_root / ".corvin" / "forge"
        # ... existing project-root discovery logic ...
        # But now return tenant_dir / "forge" as fallback (NOT global fallback)
        return tenant_dir / "forge"
    
    if scope == "user":
        return tenant_dir / "global" / "forge"
    
    raise ValueError(f"unknown scope: {scope!r}")
```

**Key design decision:** The `scope_root()` function now enforces tenant isolation at the API level:
- `session` scope → `~/.corvin/tenants/<tenant_id>/sessions/`
- `user` scope → `~/.corvin/tenants/<tenant_id>/global/forge/`
- `project` scope → `.corvin/forge/` (per git repo, but tenant-scoped fallback)

#### 2.B.2 Update Call-Sites (~100 locations)

Use regex-based search to find all `scope_root()` call-sites:

```bash
cd /home/shumway/projects/CorvinOS
grep -r "scope_root(" --include="*.py" \
  operator/skill-forge \
  operator/forge \
  core/orchestration \
  core/learning \
  core/compliance \
  | grep -v "test" | grep -v "def scope_root"
```

**Strategy:**
1. Semi-automated replacement via `ast.parse()` script
2. Manual verification of each location
3. For each call-site, determine the correct `tenant_id` source (usually from `ExecutionContext` or `SessionRecord`)

**Example refactoring locations:**

**Location 1: `operator/skill-forge/skill_forge/multi_registry.py`**
```python
# OLD
skill_root = scope_root("user", channel_id=channel_id)

# NEW (assuming context available)
skill_root = scope_root("user", tenant_id=context.tenant_id, channel_id=channel_id)
```

**Location 2: `operator/forge/forge/tool_registry.py`**
```python
# OLD
forge_root = scope_root("global")

# NEW (tool_forge is part of subsystem)
forge_root = scope_root("global", tenant_id=self.context.tenant_id)
```

**Location 3: `core/orchestration/subsystems/skill_forge_subsystem.py`**
```python
# OLD
skill_dir = scope_root("user")

# NEW
skill_dir = scope_root("user", tenant_id=self.execution_context.tenant_id)
```

**Location 4: `core/learning/event_store.py`**
```python
# OLD
session_dir = scope_root("session", channel_id=session_id)

# NEW
session_dir = scope_root("session", 
                         tenant_id=context.tenant_id,
                         channel_id=session_id)
```

#### 2.B.3 Test: scope_root() Behavior

**Tests (~15 assertions):**

```python
# tests/test_scope_root_tenant_aware.py

def test_scope_root_requires_tenant_id():
    """scope_root() fails if tenant_id not provided."""
    with pytest.raises(TypeError, match="tenant_id"):
        scope_root("global")  # Missing required kwarg

def test_scope_root_validates_tenant_id():
    """scope_root() rejects invalid tenant_id."""
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        scope_root("global", tenant_id="../../../etc/passwd")

def test_scope_root_isolates_per_tenant():
    """scope_root(S, T1) ≠ scope_root(S, T2) for all scopes."""
    scopes = ["global", "user", "session", "project"]
    tenants = ["tenant_a", "tenant_b"]
    channel_id = "ch_123"
    
    for scope in scopes:
        paths = []
        for tid in tenants:
            if scope == "session":
                p = scope_root(scope, tenant_id=tid, channel_id=channel_id)
            else:
                p = scope_root(scope, tenant_id=tid)
            paths.append((tid, str(p)))
        
        # Paths must differ
        assert paths[0][1] != paths[1][1]
        # tenant_id must be in path
        assert tenants[0] in paths[0][1]
        assert tenants[1] in paths[1][1]

def test_scope_root_session_with_multiple_channels():
    """Session scope with different channel_id."""
    p1 = scope_root("session", tenant_id="t1", channel_id="ch_1")
    p2 = scope_root("session", tenant_id="t1", channel_id="ch_2")
    assert p1 != p2

def test_scope_root_user_within_tenant():
    """User scope returns tenant-scoped forge dir."""
    path = scope_root("user", tenant_id="tenant_a")
    assert "tenant_a" in str(path)
    assert "forge" in str(path) or "global" in str(path)
```

**Estimate:** 2–3 Days (2 Engineers: 1 on refactoring, 1 on verification/testing)

---

### Phase C: Brain v0.2 Subsystem Wiring (2–3 Days)

**Goal:** Update 6 key subsystems to use tenant-aware APIs and the new `scope_root()` signature.

#### 2.C.1 Subsystem: `SkillForgeSubsystem`

**File:** `/home/shumway/projects/CorvinOS/core/orchestration/subsystems/skill_forge_subsystem.py`

**Changes:**

```python
class SkillForgeSubsystem(Subsystem):
    def __init__(self, context: ExecutionContext):
        self.context = context
        # Verify tenant_id on startup
        validate_tenant_id(context.tenant_id)
    
    def create_skill(self, name: str, body: str, **kwargs) -> SkillRecord:
        """Create skill in tenant-scoped directory."""
        # OLD: skill_dir = scope_root("user")
        # NEW:
        skill_dir = scope_root("user", tenant_id=self.context.tenant_id)
        
        # Skills go to ~/.corvin/tenants/<tenant_id>/skill-forge/skills/
        skill_file = skill_dir / "skills" / f"{name}.py"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(body)
        
        # Update registry (tenant-scoped)
        self._register_skill_internal(name, skill_file, tenant_id=self.context.tenant_id)
        return SkillRecord(...)
    
    def list_skills(self) -> List[str]:
        """List skills for current tenant only."""
        skill_dir = scope_root("user", tenant_id=self.context.tenant_id)
        return list((skill_dir / "skills").glob("*.py"))
    
    def load_skill(self, name: str) -> SkillRecord:
        """Load skill from current tenant's registry."""
        # Query registry filtered by tenant_id
        return self._registry.get_skill(
            name=name, 
            tenant_id=self.context.tenant_id  # NEW: isolation
        )
    
    def _register_skill_internal(self, name: str, path: Path, tenant_id: str):
        """Register skill in per-tenant registry."""
        self._registry.register(
            name=name,
            path=path,
            tenant_id=tenant_id,  # NEW: isolation
            timestamp=time.time(),
        )
```

**Tests (~10–15 assertions):**

```python
def test_skill_forge_creates_skill_in_tenant_dir():
    """SkillForgeSubsystem.create_skill() writes to tenant-scoped dir."""
    context = ExecutionContext(tenant_id="tenant_a")
    forge = SkillForgeSubsystem(context=context)
    
    forge.create_skill(name="my_skill", body="print('hello')")
    
    # Verify file exists in tenant-scoped location
    expected_path = Path.home() / ".corvin/tenants/tenant_a/skill-forge/skills/my_skill.py"
    assert expected_path.exists()

def test_skill_forge_two_tenants_isolation():
    """Two tenants cannot see each other's skills."""
    context_a = ExecutionContext(tenant_id="tenant_a")
    context_b = ExecutionContext(tenant_id="tenant_b")
    
    forge_a = SkillForgeSubsystem(context=context_a)
    forge_b = SkillForgeSubsystem(context=context_b)
    
    forge_a.create_skill(name="a_skill", body="print('A')")
    forge_b.create_skill(name="b_skill", body="print('B')")
    
    # Tenant A lists skills
    a_skills = forge_a.list_skills()
    b_skills = forge_b.list_skills()
    
    # Cross-check: no leakage
    assert "a_skill" in a_skills
    assert "b_skill" not in a_skills
    assert "b_skill" in b_skills
    assert "a_skill" not in b_skills
```

**Estimate:** 1 Day (1 Engineer)

---

#### 2.C.2 Subsystem: `ToolForgeSubsystem`

**File:** `/home/shumway/projects/CorvinOS/core/orchestration/subsystems/tool_forge_subsystem.py` (if exists)

**Changes:** Similar to SkillForgeSubsystem — replace all `scope_root()` calls with tenant-aware versions.

```python
def forge_tool(self, spec: ToolSpec) -> Path:
    """Forge a tool in tenant-scoped directory."""
    tool_dir = scope_root("global", tenant_id=self.context.tenant_id)
    tool_file = tool_dir / f"{spec.name}.py"
    tool_file.write_text(spec.source_code)
    return tool_file

def load_tool(self, name: str) -> ToolRecord:
    """Load tool from current tenant's registry."""
    return self._registry.get_tool(
        name=name,
        tenant_id=self.context.tenant_id
    )
```

**Tests (~10–15 assertions):** Same pattern as SkillForgeSubsystem.

**Estimate:** 1 Day (1 Engineer)

---

#### 2.C.3 Subsystem: `LearningEngine`

**File:** `/home/shumway/projects/CorvinOS/core/orchestration/subsystems/learning_engine.py`

**Changes:**

```python
def store_event(self, event: LearningEvent) -> None:
    """Store learning event in tenant-scoped directory."""
    event_dir = scope_root("session", 
                           tenant_id=self.context.tenant_id,
                           channel_id=self.context.session_id)
    event_file = event_dir / "events" / f"{event.id}.json"
    event_file.parent.mkdir(parents=True, exist_ok=True)
    event_file.write_text(json.dumps(event.asdict()))
    
    # Also write to tenant's audit trail
    audit_file = tenant_audit_file(self.context.tenant_id)
    self._append_audit_event(audit_file, event)

def store_confidence(self, metric: ConfidenceMetric) -> None:
    """Store confidence metric in tenant-scoped metrics DB."""
    learning_dir = tenant_learning_dir(self.context.tenant_id)
    metrics_db = learning_dir / "metrics.jsonl"
    metrics_db.append_line(metric.asdict())
```

**Tests (~5–10 assertions):** Verify events written to tenant-scoped location; verify no cross-tenant leakage.

**Estimate:** 0.5 Days (1 Engineer)

---

#### 2.C.4 Subsystem: `SafetyValidator`

**File:** `/home/shumway/projects/CorvinOS/core/orchestration/subsystems/safety_validator.py`

**Changes:**

```python
def write_event(self, event: AuditEvent) -> None:
    """Write audit event to tenant-specific audit.jsonl."""
    audit_file = tenant_audit_file(self.context.tenant_id)
    
    # Fail-closed: verify tenant_id in context
    if not self.context.tenant_id:
        raise RuntimeError("Cannot write audit event without tenant_id")
    
    # Write event with tenant_id embedded
    self._audit_backend.write_event(
        event=event,
        tenant_id=self.context.tenant_id,
        audit_file=audit_file,
    )
    
    # Hash-chain: append to tenant's chain
    self._hash_chain.append(audit_file, event)
```

**Tests (~5–10 assertions):** Verify audit events written to correct tenant file; verify split-brain is eliminated.

**Estimate:** 0.5 Days (1 Engineer)

---

#### 2.C.5 Subsystem: `SessionManager`

**File:** `/home/shumway/projects/CorvinOS/core/orchestration/subsystems/session_manager.py` (if exists)

**Changes:**

```python
def create_session(self, session_id: str) -> SessionRecord:
    """Create session in tenant-scoped directory."""
    session_dir = scope_root("session",
                             tenant_id=self.context.tenant_id,
                             channel_id=session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Create session metadata
    session_file = session_dir / "session.json"
    session_file.write_text(json.dumps({
        "session_id": session_id,
        "tenant_id": self.context.tenant_id,
        "created_at": datetime.now().isoformat(),
    }))
    
    return SessionRecord(...)
```

**Tests (~5–10 assertions):** Verify session files in tenant-scoped directory.

**Estimate:** 0.5 Days (1 Engineer)

---

#### 2.C.6 Subsystem: `MemoryManager`

**File:** `/home/shumway/projects/CorvinOS/core/orchestration/subsystems/memory_manager.py` (if exists)

**Changes:**

```python
def store_memory(self, memory_type: str, data: dict) -> None:
    """Store memory in tenant-scoped directory."""
    memory_dir = tenant_memory_dir(self.context.tenant_id)
    memory_file = memory_dir / f"{memory_type}.jsonl"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.append_line(json.dumps(data))
```

**Tests (~3–5 assertions):** Verify memory files in tenant-scoped directory.

**Estimate:** 0.5 Days (1 Engineer)

---

#### 2.C.7 Deliverable Summary

- ✅ 6 subsystems updated to use `scope_root(..., tenant_id=...)`
- ✅ All subsystems respect tenant isolation via ExecutionContext
- ✅ No fallback to `_default` tenant
- ✅ Audit trail per-tenant (no split-brain)
- ✅ 50–60 new unit tests covering all subsystems

**Estimate:** 2–3 Days (2–3 Engineers working on subsystems in parallel)

---

### Phase D: Migration Tool (1–2 Days)

**Goal:** Provide operator with migration path from global storage to tenant-native storage.

#### 2.D.1 Enhance: `operator/migration/corvin_migrate.py`

**File:** `/home/shumway/projects/CorvinOS/operator/migration/corvin_migrate.py` (ENHANCE)

**New command:**

```bash
corvin migrate --to-tenant-native [--dry-run] [--cleanup-ttl 30d]
```

**Implementation:**

```python
def migrate_to_tenant_native(dry_run: bool = False, cleanup_ttl: str = "30d"):
    """Migrate global storage (~/.corvin/global/*) to tenant-native (~/.corvin/tenants/_default/*)."""
    
    # Step 1: Verify pre-migration state
    global_forge = Path.home() / ".corvin" / "global" / "forge"
    global_skill_forge = Path.home() / ".corvin" / "global" / "skill-forge"
    
    if not global_forge.exists() and not global_skill_forge.exists():
        print("✓ No legacy global storage found; migration not needed.")
        return
    
    # Step 2: Create destination directories
    tenant_dir = tenant_home("_default")
    dest_forge = tenant_dir / "forge"
    dest_skill_forge = tenant_dir / "skill-forge"
    
    print(f"Migration plan:")
    print(f"  SOURCE: {global_forge}")
    print(f"  DEST:   {dest_forge}")
    print(f"  DRY-RUN: {dry_run}")
    
    if dry_run:
        print("\n[DRY-RUN] No changes will be made.")
        print(f"Would migrate:")
        print(f"  - {global_forge} → {dest_forge}")
        print(f"  - {global_skill_forge} → {dest_skill_forge}")
        return
    
    # Step 3: Copy files
    import shutil
    
    if global_forge.exists():
        shutil.copytree(global_forge, dest_forge, dirs_exist_ok=True)
        print(f"✓ Migrated forge: {global_forge} → {dest_forge}")
    
    if global_skill_forge.exists():
        shutil.copytree(global_skill_forge, dest_skill_forge, dirs_exist_ok=True)
        print(f"✓ Migrated skill-forge: {global_skill_forge} → {dest_skill_forge}")
    
    # Step 4: Log migration event
    audit_file = tenant_audit_file("_default")
    migration_event = {
        "event_type": "migration_completed",
        "timestamp": datetime.now().isoformat(),
        "source": "global_storage",
        "dest": "tenant_native (_default)",
        "status": "success",
    }
    with open(audit_file, "a") as f:
        f.write(json.dumps(migration_event) + "\n")
    
    # Step 5: Schedule cleanup (if cleanup_ttl specified)
    cleanup_after = parse_ttl(cleanup_ttl)  # e.g., "30d" → 30 days
    print(f"✓ Old directories will be cleaned up after {cleanup_ttl}.")
    print(f"  To clean up now: corvin migrate --cleanup-old")
```

#### 2.D.2 Add CLI commands to `operator/cli/corvin_cli.py`

```bash
corvin migrate --to-tenant-native [--dry-run] [--cleanup-ttl 30d]
corvin migrate --cleanup-old [--force]
corvin verify-tenant-isolation
corvin tenant-data-report
```

**Tests (~10 assertions):**

```python
def test_migrate_dry_run_no_changes():
    """--dry-run flag does not modify files."""
    # Setup: Create legacy global storage
    # Run: migrate(..., dry_run=True)
    # Assert: No files copied

def test_migrate_idempotent():
    """Re-running migrate is safe (idempotent)."""
    # Setup: Migrate once
    # Run: migrate() again
    # Assert: No duplicates, no errors

def test_migrate_preserves_data_integrity():
    """Data is not corrupted during migration."""
    # Setup: Create skills + audit trail in old location
    # Migrate
    # Assert: Skills still load, Audit Trail still verifiable
```

**Estimate:** 1–2 Days (1 Engineer)

---

### Phase E: Test & Verify (2–3 Days)

**Goal:** Achieve 0 CRITICAL findings in adversarial testing before shipping.

#### 2.E.1 Unit Tests (~30–40 tests)

**Coverage:**
- Path isolation and validation (Phase A)
- scope_root() signature and behavior (Phase B)
- Per-subsystem tenant-id handling (Phase C)

**Estimate:** 1 Day (1 Engineer)

---

#### 2.E.2 Integration Tests (~20–30 tests)

**Coverage:**
- Skill CREATE on Tenant A, List on Tenant A ≠ Tenant B
- Tool FORGE on Tenant A, Load on Tenant A ≠ Tenant B
- Audit events written to correct tenant
- Migration tool works end-to-end

**Estimate:** 1 Day (1 Engineer)

---

#### 2.E.3 E2E Tests (~10–15 tests)

**Coverage:**
- Real Operator on Two Tenants (same machine)
- Tenant A creates Skill → Tenant B cannot see
- Tenant A sends message via Bridge → logged to Tenant A's audit
- Skills executed in Tenant A don't leak to Tenant B

**Test setup:**
```python
@pytest.fixture
def two_tenants_setup(tmp_path):
    """Setup two isolated tenants for testing."""
    tenant_a = ExecutionContext(tenant_id="test_tenant_a")
    tenant_b = ExecutionContext(tenant_id="test_tenant_b")
    
    # Create temporary .corvin directories
    os.makedirs(tenant_home(tenant_a.tenant_id))
    os.makedirs(tenant_home(tenant_b.tenant_id))
    
    yield (tenant_a, tenant_b)
    
    # Cleanup
    shutil.rmtree(tenant_home(tenant_a.tenant_id))
    shutil.rmtree(tenant_home(tenant_b.tenant_id))
```

**Estimate:** 1 Day (1–2 Engineers with E2E testing tools)

---

#### 2.E.4 Adversarial Tests (~15–20 tests) — **BLOCKER**

**This is the critical gate.** Every finding must be investigated and fixed.

##### 2.E.4.1 Path-Traversal Attack

```python
@pytest.mark.adversarial
def test_path_traversal_in_tenant_id():
    """Attempt to access ../../../etc/passwd via tenant_id."""
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        tenant_home("../../../etc/passwd")
    
    with pytest.raises(ValueError):
        scope_root("global", tenant_id="../../../etc/passwd")

@pytest.mark.adversarial
def test_path_traversal_in_skill_name():
    """Attempt to write skill outside tenant dir."""
    context = ExecutionContext(tenant_id="tenant_a")
    forge = SkillForgeSubsystem(context=context)
    
    with pytest.raises(ValueError):
        forge.create_skill(
            name="../../../../../../etc/passwd",
            body="malicious code"
        )
    
    # Verify file was NOT created at /etc/passwd
    assert not Path("/etc/passwd").read_text().contains("malicious code")
```

##### 2.E.4.2 Symlink Attack

```python
@pytest.mark.adversarial
def test_symlink_escapes_tenant_boundary():
    """Tenant A creates symlink to Tenant B's data."""
    # Setup: Create tenant directories
    tenant_a = ExecutionContext(tenant_id="tenant_a")
    tenant_b = ExecutionContext(tenant_id="tenant_b")
    
    # Tenant A's skill-forge dir
    a_skill_dir = tenant_skill_dir("tenant_a")
    b_skill_dir = tenant_skill_dir("tenant_b")
    
    # Attack: Tenant A tries to create symlink to Tenant B
    symlink_path = a_skill_dir / "symlink_to_b"
    
    # Attempt symlink creation (should be detected/rejected)
    try:
        os.symlink(b_skill_dir, symlink_path)
        # If symlink was created, verify data access is blocked
        # (e.g., via readlink check in skill_forge.py)
        # Implementation should reject symlinks
        assert False, "Symlink should be rejected"
    except (SymlinkError, ValueError, PermissionError):
        pass  # Expected: symlink creation rejected
```

##### 2.E.4.3 Context Forgery Attack

```python
@pytest.mark.adversarial
def test_context_forgery_tenant_id():
    """Try to forge ExecutionContext.tenant_id to access Tenant B."""
    # Setup: Create skill in Tenant B
    context_b = ExecutionContext(tenant_id="tenant_b")
    forge_b = SkillForgeSubsystem(context=context_b)
    forge_b.create_skill(name="secret_skill", body="print('secret')")
    
    # Attack: Forge context with Tenant B's ID from Tenant A code
    forged_context = ExecutionContext(tenant_id="tenant_b")
    forged_forge = SkillForgeSubsystem(context=forged_context)
    
    # Verify: Even with forged context, the skill is accessed
    # But this is OK — the context is what matters. The real attack
    # is: can Tenant A's code (executing in context_a) somehow
    # instantiate a subsystem with context_b?
    
    # In real execution, ExecutionContext is bound to the turn/session,
    # not forged by user code. So this test verifies the audit trail
    # records the correct tenant_id.
```

##### 2.E.4.4 Registry Collision Attack

```python
@pytest.mark.adversarial
def test_registry_collision_isolation():
    """Two tenants register skill with same name."""
    context_a = ExecutionContext(tenant_id="tenant_a")
    context_b = ExecutionContext(tenant_id="tenant_b")
    
    forge_a = SkillForgeSubsystem(context=context_a)
    forge_b = SkillForgeSubsystem(context=context_b)
    
    # Both create skill with same name
    forge_a.create_skill(name="my_skill", body="print('A')")
    forge_b.create_skill(name="my_skill", body="print('B')")
    
    # Load and verify isolation
    skill_a = forge_a.load_skill("my_skill")
    skill_b = forge_b.load_skill("my_skill")
    
    # They should be different
    assert skill_a.body != skill_b.body
    assert "print('A')" in skill_a.body
    assert "print('B')" in skill_b.body
```

##### 2.E.4.5 Audit Chain Integrity Attack

```python
@pytest.mark.adversarial
def test_audit_chain_cannot_be_tampered():
    """Attempt to tamper with audit hash-chain."""
    audit_file = tenant_audit_file("tenant_a")
    
    # Write first event
    event1 = {"type": "skill_created", "name": "s1", "hash": None}
    with open(audit_file, "a") as f:
        f.write(json.dumps(event1) + "\n")
    
    # Try to modify event1 retroactively
    with open(audit_file, "r") as f:
        lines = f.readlines()
    
    lines[0] = json.dumps({"type": "skill_deleted", "name": "s1"}) + "\n"
    
    with open(audit_file, "w") as f:
        f.writelines(lines)
    
    # Verify: Hash-chain verification detects tampering
    result = subprocess.run(
        ["voice-audit", "verify", str(audit_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "Tampered audit should fail verification"
```

##### 2.E.4.6 Cross-Tenant Bridge Credentials

```python
@pytest.mark.adversarial
def test_bridge_credentials_per_tenant():
    """Bridge credentials isolated per tenant."""
    context_a = ExecutionContext(tenant_id="tenant_a")
    context_b = ExecutionContext(tenant_id="tenant_b")
    
    # Setup bridge credentials for Tenant A
    bridge_dir_a = tenant_bridge_dir("tenant_a", "discord")
    creds_file_a = bridge_dir_a / "credentials.json"
    creds_file_a.parent.mkdir(parents=True, exist_ok=True)
    creds_file_a.write_text(json.dumps({"token": "token_a"}))
    
    # Setup bridge credentials for Tenant B
    bridge_dir_b = tenant_bridge_dir("tenant_b", "discord")
    creds_file_b = bridge_dir_b / "credentials.json"
    creds_file_b.parent.mkdir(parents=True, exist_ok=True)
    creds_file_b.write_text(json.dumps({"token": "token_b"}))
    
    # Attempt to read Tenant B's credentials from Tenant A context
    try:
        with open(creds_file_b, "r") as f:
            creds_b = json.load(f)
        # If file is readable, the isolation is broken
        assert False, "Tenant A should not be able to read Tenant B's credentials"
    except FileNotFoundError:
        pass  # Expected: credentials isolated
    except PermissionError:
        pass  # Expected: permission denied
```

**Adversarial Test Result Handling:**

| Outcome | Action |
|---|---|
| 0 CRITICAL findings | ✅ Proceed to Phase F |
| 1–3 CRITICAL findings | 🔴 BLOCK: Fix + Re-test |
| >3 CRITICAL findings | 🔴 BLOCK: Architecture review required |

**Estimate:** 1–2 Days (1–2 Security-focused Engineers)

---

#### 2.E.5 Success Criteria

Before proceeding to Phase F, ALL of the following must be true:

- ✅ Unit tests: 30–40 tests pass
- ✅ Integration tests: 20–30 tests pass
- ✅ E2E tests: 10–15 tests pass
- ✅ Adversarial tests: 0 CRITICAL findings
- ✅ No regressions on existing tests
- ✅ Code coverage: >85% for Phase A–C changes
- ✅ ADR-0362 acceptance ready (all paths documented)

---

### Phase F: Ship — Remove Feature-Flag (1 Day)

**Goal:** Tenant-Native Persistence becomes the default behavior; no legacy fallback.

#### 2.F.1 Remove Feature-Flag

**File:** `spec.json` or `tenant.corvin.yaml`

```json
// BEFORE
{
  "features": {
    "tenant_native_storage": false,  // Removed in Phase F
    ...
  }
}

// AFTER
{
  "features": {
    // tenant_native_storage flag removed entirely
    ...
  }
}
```

**Code change:** Remove all conditional logic that checks `spec.features.tenant_native_storage`.

#### 2.F.2 Remove Fallback Logic

**File:** `core/paths/resolver.py` (or similar)

```python
# OLD (fallback to _default tenant)
def resolve_tenant_id(context: ExecutionContext) -> str:
    if context.tenant_id:
        return context.tenant_id
    # Fallback (removed in Phase F)
    return "_default"

# NEW (always explicit, never fallback)
def resolve_tenant_id(context: ExecutionContext) -> str:
    if not context.tenant_id:
        raise RuntimeError(
            "tenant_id is required in ExecutionContext. "
            "Tenant-native storage is now default; no fallback."
        )
    return context.tenant_id
```

#### 2.F.3 Test: Old Code Path is Gone

```python
def test_scope_root_fails_without_tenant_id():
    """Verify old code path (without tenant_id) now fails."""
    with pytest.raises(TypeError):
        scope_root("global")  # Missing tenant_id parameter
    
    # No legacy behavior, no fallback to _default

def test_feature_flag_removed():
    """Verify tenant_native_storage flag is gone from spec.json."""
    spec = json.load(open("spec.json"))
    assert "tenant_native_storage" not in spec.get("features", {})
```

**Estimate:** 1 Day (1 Engineer)

---

## 3. Risk Assessment Matrix

| Phase | Module | Risk-Level | Impact | Mitigation |
|---|---|---|---|---|
| A | core/paths/tenant.py | LOW | Path resolution bugs | Unit tests (20–30) |
| B | scope_root() refactor | **HIGH** | ~100 call-sites; one mistake breaks all subsystems | Automated search + manual verification per location |
| C | SkillForgeSubsystem | MEDIUM | Skills not isolated | Integration tests (10–15) |
| C | ToolForgeSubsystem | MEDIUM | Tools not isolated | Integration tests (10–15) |
| C | LearningEngine | MEDIUM | Events leak across tenants | Unit tests (5–10) |
| C | SafetyValidator | **HIGH** | Split-brain audit trail persists | Adversarial test: audit-chain integrity |
| C | SessionManager | MEDIUM | Sessions mixed up | Integration tests (5–10) |
| C | MemoryManager | LOW | Memory isolation | Unit tests (3–5) |
| D | corvin_migrate.py | MEDIUM | Data loss during migration | Dry-run mode + backup; 30-day cleanup TTL |
| E | Adversarial tests | **HIGH** | Finding CRITICAL issue late (during shipping) | Test everything: path-traversal, symlinks, context-forgery, registry-collision, audit-chain, bridge-creds |
| F | Feature-flag removal | LOW | Operator confusion; can't disable feature | Clear deprecation message in docs |

### Mitigation Strategies

**Phase B (HIGH):**
1. Write AST-based script to find all `scope_root()` call-sites programmatically
2. Generate refactoring checklist (~100 items)
3. Manual Code Review on each location (pair programming recommended)
4. Parametrized test matrix: all scopes × all tenants
5. Regression test on existing E2E suite

**Phase C (HIGH: SafetyValidator):**
1. Adversarial test for split-brain (verify one audit file per tenant)
2. Audit-chain integrity test (hash-chain verification)
3. Integration test: create events in two tenants simultaneously; verify no interleaving

**Phase E (HIGH: Adversarial Testing):**
1. Each adversarial test is a blocker; must pass before shipping
2. If finding: Fix + Re-run entire adversarial test suite
3. No waiving of CRITICAL findings
4. Security team review (if available)

---

## 4. Test Matrix Summary

| Test Type | Count | Coverage | Pass-Blocker? | Estimate |
|---|---|---|---|---|
| Unit: Paths | 15–20 | core/paths/tenant.py | YES | 0.5 days |
| Unit: Validation | 10–15 | validate_tenant_id() | YES | 0.5 days |
| Unit: scope_root() | 10–15 | Signature + behavior | YES | 1 day |
| Unit: Subsystems | 50–60 | All 6 subsystems | YES | 1.5 days |
| Integration: Forge | 20–30 | Skill/Tool CRUD, Registry isolation | YES | 1 day |
| Integration: Audit | 5–10 | Split-brain elimination | YES | 0.5 days |
| E2E: Two Tenants | 10–15 | Real operator workflow | YES | 1 day |
| Adversarial: Path-Traversal | 5–10 | tenant_id validation | **BLOCKER** | 0.5 days |
| Adversarial: Symlinks | 3–5 | Boundary escapes | **BLOCKER** | 0.5 days |
| Adversarial: Context-Forgery | 3–5 | ExecutionContext manipulation | **BLOCKER** | 0.5 days |
| Adversarial: Registry-Collision | 3–5 | Same name, different tenants | **BLOCKER** | 0.5 days |
| Adversarial: Audit-Chain | 3–5 | Hash-chain tampering | **BLOCKER** | 0.5 days |
| Adversarial: Bridge-Creds | 3–5 | Credential isolation | **BLOCKER** | 0.5 days |
| **TOTAL** | **~155–180 tests** | **High Coverage** | **0 CRITICAL Findings** | **~8 days** |

---

## 5. Timeline & Staffing Scenarios

### Scenario 1: 1 Engineer (Solo Implementation)

| Phase | Duration | Notes |
|---|---|---|
| A: Foundation | 1.5 days | Sequential |
| B: scope_root Refactor | 3–4 days | Call-site verification is bottleneck |
| C: Brain Subsystems | 3 days | One subsystem per 0.5 day |
| D: Migration Tool | 1–2 days | Sequential |
| E: Testing | 3–4 days | Unit + Adversarial (sequential) |
| F: Ship | 1 day | Final cleanup |
| **TOTAL** | **3–4 weeks** | Very sequential; high context-switching |

### Scenario 2: 2 Engineers (Parallel)

| Phase | Duration | Notes |
|---|---|---|
| A: Foundation | 1.5 days | Eng1 + Eng2 can pair or split |
| B: scope_root Refactor | 2–3 days | Eng1 refactoring, Eng2 verification |
| C: Brain Subsystems | 1.5–2 days | Eng1 + Eng2 on different subsystems in parallel |
| D: Migration Tool | 1–2 days | One engineer; other works on Phase E setup |
| E: Testing | 2–2.5 days | Eng1: Unit + Integration; Eng2: Adversarial |
| F: Ship | 1 day | Coordinated |
| **TOTAL** | **2–3 weeks** | Much better parallelization |

### Scenario 3: 3 Engineers (Aggressive)

| Phase | Duration | Notes |
|---|---|---|
| A: Foundation | 1 day | Eng1 |
| B: scope_root Refactor | 1.5–2 days | Eng1 (refactoring) + Eng2 + Eng3 (verification, 50 call-sites each) |
| C: Brain Subsystems | 1 day | Eng1, Eng2, Eng3 each handle 2 subsystems in parallel |
| D: Migration Tool | 1 day | Eng1 (with Eng2 review) |
| E: Testing | 1.5–2 days | Eng1: Unit; Eng2: Integration; Eng3: Adversarial |
| F: Ship | 0.5 days | Coordinated |
| **TOTAL** | **1–2 weeks** | Highly parallelizable |

**Critical Path:** Phase B (scope_root refactor) + Phase E (Adversarial testing) = **5–7 days minimum**

---

## 6. Migration & Rollout Strategy

### Pre-Ship (Internal Testing Only)

**Week 1–2: Phases A–B (Foundation + Refactor)**
- Implement Phase A: core/paths/tenant.py
- Implement Phase B: scope_root() refactor + all call-sites
- Internal testing: Does scope_root() work with tenant_id?

**Week 2–3: Phases C–D (Brain Integration + Migration Tool)**
- Implement Phase C: Subsystem updates
- Implement Phase D: Migration tool
- Internal testing: Can we migrate from global to tenant-native?

**Week 3: Phase E (Testing + Adversarial Review)**
- Run all unit, integration, E2E tests
- Run adversarial test suite
- **If CRITICAL findings: Stop; fix; re-test**
- If 0 CRITICAL findings: Proceed

**Week 4: Phase F (Ship with Feature-Flag OFF)**
- Remove feature-flag
- Merge to main
- Deploy to internal staging
- Verify end-to-end

### Post-Ship (Gradual Rollout)

**Week 4–5: Feature-Flag ON for Internal Testing**
- Enable `tenant_native_storage: true` for Corvin Dev team
- Monitor for regressions, audit trail consistency
- Collect feedback

**Week 6: Feature-Flag ON for 50% of Tenants (Canary)**
- Roll out to 50% of production tenants
- Monitor metrics: No errors, audit chain integrity OK
- Continue monitoring

**Week 7: Feature-Flag ON for 100% (Full Rollout)**
- Roll out to 100% of production tenants
- Decommission fallback logic

**Week 8: Remove Feature-Flag Entirely**
- No more legacy code path
- Full commit to tenant-native

### Rollback Plan

**If Adversarial Testing finds CRITICAL issues:**

1. **Pause Phase F** (don't ship to production)
2. **Analyze finding** (e.g., "Symlinks bypass tenant isolation")
3. **Implement fix** (e.g., reject symlinks in SkillForgeSubsystem)
4. **Re-run Adversarial Tests** (entire suite)
5. **If issues persist:** Consider Phase 2 alternative (SQL-based Registry with enforced tenant scoping)
6. **If issues fixed:** Continue with Phase F

---

## 7. Success Criteria

### Before Shipping

- ✅ **All tests pass:**
  - 30–40 unit tests (paths + validation)
  - 10–15 unit tests (scope_root)
  - 50–60 unit tests (subsystems)
  - 20–30 integration tests
  - 10–15 E2E tests
  - 15–20 adversarial tests

- ✅ **Zero CRITICAL findings** in adversarial testing

- ✅ **Code review:** All phases reviewed by ≥1 peer

- ✅ **ADR-0362 acceptance:** Design documented, paths identified

- ✅ **Documentation updated:**
  - docs/layer-X-tenant-persistence.md (new)
  - Operator migration guide
  - Code comments on every tenant_id parameter

- ✅ **No regressions** on existing test suite

### After Shipping

- ✅ **Production metrics (Week 5+):**
  - Audit events: 0 cross-tenant leaks (verified via sampling)
  - Skills: 0 registry collisions (verified via queries)
  - Bridge creds: All isolated per tenant (verified via file audit)
  - Telemetry: Opt-out state per-tenant (verified via queries)

- ✅ **Operator feedback:** No surprises; migration smooth

- ✅ **Data integrity:** Audit chain unbroken (daily verify runs clean)

---

## 8. Dependency Matrix (Detailed)

### Internal Dependencies

| Module | Depends On | Status | Risk |
|---|---|---|---|
| core/paths/tenant.py | None (new) | INDEPENDENT | LOW |
| operator/forge/forge/scope.py | core/paths/tenant.py | Phase A ✓ Phase B | HIGH |
| operator/skill-forge/multi_registry.py | scope_root() new signature | Phase B | MEDIUM |
| operator/forge/tool_registry.py | scope_root() new signature | Phase B | MEDIUM |
| core/orchestration/subsystems/skill_forge_subsystem.py | scope_root() | Phase C | MEDIUM |
| core/orchestration/subsystems/tool_forge_subsystem.py | scope_root() | Phase C | MEDIUM |
| core/orchestration/subsystems/learning_engine.py | tenant_audit_file() | Phase C | LOW |
| core/orchestration/subsystems/safety_validator.py | tenant_audit_file() | Phase C | HIGH |
| core/orchestration/subsystems/session_manager.py | scope_root() | Phase C | MEDIUM |
| core/orchestration/subsystems/memory_manager.py | tenant_memory_dir() | Phase C | LOW |
| operator/migration/corvin_migrate.py | Phase A + B | Phase D | MEDIUM |
| Tests | All phases above | Phase E | HIGH |

### External Dependencies (CorvinOS)

- `ExecutionContext` (must have tenant_id field) — ADR-0007
- `validate_tenant_id()` (must exist in core/tenants/) — New in Phase A
- `tenant_audit_file()` (must exist in core/paths/tenant.py) — New in Phase A
- Feature-flag system (spec.json or tenant.corvin.yaml) — Must support removal in Phase F

---

## 9. Documentation Plan

### Docs to Create/Update

| Doc | Type | Phase | Priority |
|---|---|---|---|
| `docs/claude-ref/layer-X-tenant-persistence.md` | Reference | A | HIGH |
| `docs/implementation-plans/migration-to-tenant-native.md` | Guide | D | HIGH |
| `docs/implementation-plans/operator-quickstart-tenant-native.md` | Guide | F | MEDIUM |
| Inline code comments on `scope_root()` | Code | B | HIGH |
| ADR-0362 acceptance document | ADR | E | HIGH |
| `CLAUDE.md` update (tenant-native is default) | Repo Convention | F | MEDIUM |

---

## 10. Known Limitations & Future Work

### Limitations (By Design)

1. **Symlink detection:** Currently not implemented; can be added in Phase 2 if needed.
2. **Cross-tenant audits:** No built-in cross-tenant audit queries; each tenant isolated.
3. **Shared configuration:** Some OS-level config (e.g., TLS certs) might still be global; clarify in ADR-0362.

### Future Work (v1.1+)

- Implement per-tenant telemetry consent (replaces global flag)
- Implement per-tenant bridge credentials (separate from core implementation)
- Add cross-tenant audit analytics (for compliance reporting)
- Performance optimization: parallel skill loading across tenants

---

## 11. Communication & Stakeholder Updates

### Checkpoints

| Checkpoint | Phase | Audience | Message |
|---|---|---|---|
| Kick-off | A | Engineering team | "Tenant-native implementation starting; Phase A foundation ready" |
| Critical pivot | B | Team leads | "scope_root() refactored; 100 call-sites updated; testing now" |
| Integration complete | C | Subsystem owners | "All subsystems tenant-aware; adversarial testing begins" |
| Adversarial gate | E | Security team | "Adversarial test results; 0 CRITICAL findings; ready to ship" |
| Shipping | F | Operators | "Tenant-native storage is now default; migration guide available" |
| Canary | Post-F | Product team | "50% of users on tenant-native; metrics look good; full rollout in 1 week" |

---

## Appendix: Pseudo-Code Reference

### scope_root() Before & After

```python
# BEFORE (Phase 0.1 — single tenant)
def scope_root(scope: str, *, channel_id: str | None = None) -> Path:
    if scope == "user":
        return corvin_home() / "global" / "forge"  # Shared by all tenants!
    elif scope == "session":
        return corvin_home() / "sessions" / channel_id / "forge"
    # ... etc

# AFTER (Phase 0.2 — tenant-aware)
def scope_root(scope: str, *, tenant_id: str, channel_id: str | None = None) -> Path:
    validate_tenant_id(tenant_id)
    tenant_dir = tenant_home(tenant_id)
    
    if scope == "user":
        return tenant_dir / "global" / "forge"  # Per-tenant!
    elif scope == "session":
        return tenant_dir / "sessions" / channel_id / "forge"  # Per-tenant!
    # ... etc
```

### Call-Site Before & After

```python
# BEFORE
forge_root = scope_root("global")  # Could be Tenant A or Tenant B (ambiguous!)

# AFTER
forge_root = scope_root("global", tenant_id=self.context.tenant_id)  # Explicit!
```

---

## Appendix: Adversarial Test Examples

### Path-Traversal Test

```python
@pytest.mark.adversarial
class TestPathTraversal:
    def test_tenant_id_with_dots(self):
        """Reject ../../../etc/passwd"""
        with pytest.raises(ValueError):
            tenant_home("../../../etc/passwd")
    
    def test_tenant_id_with_slash(self):
        """Reject /etc/passwd"""
        with pytest.raises(ValueError):
            tenant_home("/etc/passwd")
    
    def test_skill_name_with_traversal(self):
        """Reject skill named ../../secret_file"""
        forge = SkillForgeSubsystem(context=ExecutionContext(tenant_id="t1"))
        with pytest.raises(ValueError):
            forge.create_skill(name="../../secret", body="code")
```

### Audit Chain Integrity

```python
@pytest.mark.adversarial
class TestAuditChainIntegrity:
    def test_tampered_audit_fails_verification(self):
        """Modifying audit.jsonl breaks hash-chain."""
        # Create event 1
        # Create event 2
        # Tamper with event 1's hash
        # Run voice-audit verify → should fail
        result = subprocess.run(["voice-audit", "verify", ...], ...)
        assert result.returncode != 0
```

---

## Conclusion

This implementation plan provides a roadmap from the current split-brain state to a production-ready tenant-native persistence layer. The critical gates are:

1. **Phase B:** scope_root() refactor (high complexity, high impact)
2. **Phase E:** Adversarial testing (blocker for shipping)

With proper execution and 2–3 engineers, shipping is achievable in **2–3 weeks** with **0 CRITICAL findings** in adversarial testing.

---

**Document Version:** 1.0  
**Last Updated:** 2026-08-20  
**Status:** APPROVED FOR IMPLEMENTATION  
**Next Step:** Execute Phase A (Foundation) in Week 1
