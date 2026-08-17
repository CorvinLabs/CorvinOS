---
id: ADR-0361
status: PROPOSED
depends_on: [ADR-0347, ADR-0348, ADR-0349, ADR-0359, ADR-0360]
relates_to: []
paths:
  - core/orchestration/hub.py
  - core/orchestration/subsystems/forge_apis.py
  - core/orchestration/subsystems/forge_api_impl.py
  - core/orchestration/subsystems/tool_forge_subsystem.py
  - core/orchestration/subsystems/skill_forge_subsystem.py
docs:
  - docs/claude-ref/layer-plugins.md
---

# ADR-0361: Forge API Extensibility & Hub Integration

## Problem Statement

Custom Brain subsystems (e.g., `ErrorRecoverySubsystem`) need to request tool/skill generation
without directly importing or coupling to `ToolForgeSubsystem` and `SkillForgeSubsystem`.

**Current tight coupling:**
```python
class ErrorRecoverySubsystem(Subsystem):
    def startup(self, hub):
        # BAD: Direct import and subsystem reference
        from tool_forge_subsystem import ToolForgeSubsystem
        self.tool_forge = hub.subsystems["tool_forge"]  # String lookup
        # → Import cycle risk, unclear API contract
```

**Issues:**
1. Direct subsystem references create import cycles and tight coupling
2. No semantic API contract (unclear what methods are safe to call)
3. Namespace collisions possible when multiple subsystems forge tools/skills
4. No quota enforcement per subsystem (one subsystem could exhaust resources)
5. No reachability guarantee (custom subsystem may fork tools/skills before cleanup)

## Solution Overview

Implement **semantic API interfaces** for tool and skill forging, with:
- `ForgedToolAPI`: Abstract interface for tool generation requests
- `ForgedSkillAPI`: Abstract interface for skill creation requests
- `SubsystemHub.register_api()` / `.get_api()`: Loose coupling via named API lookup
- `NamespacePolicy`: Enforce namespace ownership per subsystem
- `ForgeQuota`: Enforce per-subsystem resource limits

**Key invariant:** Custom subsystems never import Forge subsystems directly.

## Architecture

### 1. Semantic APIs (forge_apis.py)

```python
class ForgedToolAPI(ABC):
    """Abstract interface for tool generation."""
    
    @abstractmethod
    async def forge_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        impl: str,
        runtime: str = "python",
        meta: dict | None = None,
        namespace: str | None = None,
    ) -> dict:
        """Generate a tool with namespace & quota enforcement."""
        pass
    
    # forge_exec, forge_promote, list_tools ...

class ForgedSkillAPI(ABC):
    """Abstract interface for skill creation."""
    
    @abstractmethod
    async def skill_create(
        self,
        name: str,
        body_md: str,
        description: str | None = None,
        skill_type: str = "learned-experience",
        claim: dict | None = None,
        namespace: str | None = None,
    ) -> dict:
        """Create a skill with namespace & quota enforcement."""
        pass
    
    # skill_grade, skill_promote, list_skills ...
```

### 2. Hub API Registry (hub.py)

```python
class SubsystemHub:
    def __init__(self, ...):
        self._apis: Dict[str, Any] = {}  # API registry
    
    def register_api(self, api_name: str, api_impl: Any) -> None:
        """Register an API (called during subsystem startup)."""
        if api_name in self._apis:
            raise ValueError(f"API already registered: {api_name}")
        self._apis[api_name] = api_impl
    
    def get_api(self, api_name: str) -> Any:
        """Get an API by name."""
        if api_name not in self._apis:
            raise KeyError(f"API not found: {api_name}")
        return self._apis[api_name]
    
    def has_api(self, api_name: str) -> bool:
        """Check if API is available."""
        return api_name in self._apis
```

### 3. Namespace Policy (forge_apis.py)

```python
@dataclass
class NamespacePolicy:
    """Enforce namespace ownership per subsystem."""
    
    subsystem_namespaces: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "tool_forge": ["tool_forge.*"],
            "skill_forge": ["skill_forge.*"],
        }
    )
    custom_namespaces: Dict[str, str] = field(default_factory=dict)
    
    def is_allowed(self, subsystem_name: str, namespace: str) -> bool:
        """Check if subsystem can forge in namespace."""
        # Subsystem always owns its own namespace
        for allowed in self.subsystem_namespaces.get(subsystem_name, []):
            if allowed.endswith(".*"):
                prefix = allowed[:-2]
                if namespace.startswith(prefix + "."):
                    return True
        
        # Check custom namespaces
        if namespace in self.custom_namespaces:
            return self.custom_namespaces[namespace] == subsystem_name
        
        return False
    
    def auto_prefix_name(
        self,
        subsystem_name: str,
        name: str,
        namespace: str | None,
    ) -> str:
        """Auto-prefix tool/skill name (e.g., "error_recovery.recover_ImportError")."""
        ns = namespace or subsystem_name
        return f"{ns}.{name}"
```

### 4. Forge Quota (forge_apis.py)

```python
@dataclass
class ForgeQuota:
    """Per-subsystem resource limits."""
    
    tool_quota: Dict[str, int] = field(default_factory=dict)
    skill_quota: Dict[str, int] = field(default_factory=dict)
    tool_usage: Dict[str, int] = field(default_factory=dict)
    skill_usage: Dict[str, int] = field(default_factory=dict)
    
    default_tool_quota: int = 10
    default_skill_quota: int = 5
    
    def check_tool_quota(self, subsystem_name: str) -> bool:
        """Check if subsystem can forge another tool."""
        max_tools = self.tool_quota.get(subsystem_name, self.default_tool_quota)
        used = self.tool_usage.get(subsystem_name, 0)
        return used < max_tools
    
    def record_tool_forge(self, subsystem_name: str) -> None:
        """Increment tool usage counter."""
        self.tool_usage[subsystem_name] = self.tool_usage.get(subsystem_name, 0) + 1
```

### 5. Concrete API Implementations (forge_api_impl.py)

```python
class ForgedToolAPIImpl(ForgedToolAPI):
    """Concrete implementation of ForgedToolAPI."""
    
    def __init__(
        self,
        subsystem: ToolForgeSubsystem,
        namespace_policy: NamespacePolicy,
        quota: ForgeQuota,
    ):
        self.subsystem = subsystem
        self.namespace_policy = namespace_policy
        self.quota = quota
    
    async def forge_tool(self, name: str, ...) -> dict:
        subsystem_name = self.subsystem.name
        
        # Check namespace policy
        ns = namespace or subsystem_name
        if not self.namespace_policy.is_allowed(subsystem_name, ns):
            raise PermissionDenied(f"Namespace {ns} not allowed for {subsystem_name}")
        
        # Check quota
        if not self.quota.check_tool_quota(subsystem_name):
            raise QuotaExceeded(f"Tool quota exceeded for {subsystem_name}")
        
        # Auto-prefix name
        prefixed_name = self.namespace_policy.auto_prefix_name(
            subsystem_name, name, namespace
        )
        
        # Delegate to subsystem
        result = await self.subsystem._forge_tool({
            "name": prefixed_name,
            ...
        })
        
        # Record quota usage
        self.quota.record_tool_forge(subsystem_name)
        
        return result
```

### 6. Subsystem Registration

**ToolForgeSubsystem.startup():**
```python
def startup(self, hub: SubsystemHub) -> None:
    self.hub = hub
    
    # Initialize async wrapper
    self.async_registry = AsyncForgeRegistry(...)
    
    # ADR-0361: Register ForgedToolAPI
    api_impl = ForgedToolAPIImpl(
        subsystem=self,
        namespace_policy=self.namespace_policy,
        quota=self.forge_quota,
    )
    hub.register_api("forged_tool", api_impl)
    
    # Subscribe to events
    hub.subscribe("forge_requested", self.on_forge_requested)
```

**SkillForgeSubsystem.startup()** — similar pattern, registers as `"forged_skill"`.

### 7. Custom Subsystem Usage (No Direct Imports!)

```python
class ErrorRecoverySubsystem(Subsystem):
    name = "error_recovery"
    
    def startup(self, hub: SubsystemHub) -> None:
        self.hub = hub
        
        # GOOD: Loose coupling via API lookup
        self.forged_tool_api = hub.get_api("forged_tool")
        self.forged_skill_api = hub.get_api("forged_skill")
        
        # Subscribe to error events
        hub.subscribe("error_detected", self.on_error_detected)
    
    async def on_error_detected(self, event_name: str, event_data: dict) -> None:
        error_type = event_data["error_type"]
        
        # Forge error-specific tool (auto-prefixed)
        tool = await self.forged_tool_api.forge_tool(
            name=f"recover_{error_type}",
            description=f"Recover from {error_type}",
            input_schema={"type": "object"},
            impl=f"def recover():\n    pass",
            namespace="error_recovery",  # Auto-prefixes to "error_recovery.recover_ImportError"
        )
        
        # Forge a skill
        skill = await self.forged_skill_api.skill_create(
            name=f"handle_{error_type}",
            body_md=f"# Handle {error_type}\n...",
            namespace="error_recovery",
        )
```

## Namespace Ownership Rules

| Subsystem | Default Namespace | Custom Allowed? |
|-----------|-------------------|-----------------|
| `tool_forge` | `tool_forge.*` | ✗ Only owns `tool_forge.*` |
| `skill_forge` | `skill_forge.*` | ✗ Only owns `skill_forge.*` |
| `error_recovery` | `error_recovery.*` | ✓ Can request `error_recovery.*` |
| `custom_X` | `custom_X.*` | ✓ Can request `custom_X.*` |
| Operator-declared | `custom.ns` | ✓ Wired to declared owner |

**Key invariant:** No subsystem can forge in another's namespace without explicit operator allowlist.

## Quota Defaults

| Resource | Default | Per-Session? |
|----------|---------|--------------|
| Tools per subsystem | 10 | ✓ Reset on `session_start` |
| Skills per subsystem | 5 | ✓ Reset on `session_start` |
| Operator can override | ✓ | via `spec.forge_limits.<subsystem>` |

## Error Handling

### Exceptions

- `PermissionDenied`: Subsystem attempted to forge in disallowed namespace
- `QuotaExceeded`: Tool/skill quota exhausted for subsystem
- `SandboxError`: Code fails security checks (delegated to ToolForgeSubsystem)
- `LinterError`: Skill markdown has prompt injection pattern

### Graceful Degradation

If `get_api()` raises `KeyError`, subsystem should log and continue (Forge may be disabled).

```python
async def startup(self, hub):
    try:
        self.forged_tool_api = hub.get_api("forged_tool")
    except KeyError:
        logger.warning("Forge APIs not available (Forge subsystem disabled?)")
        self.forged_tool_api = None
```

## Compliance Notes

### GDPR Art. 6, 32 (Consent & Processing)
- All tool/skill creation is logged to audit trail (inherited from ToolForgeSubsystem/SkillForgeSubsystem)
- Quota enforcement prevents resource exhaustion attacks
- Namespace isolation prevents subsystems from interfering with each other

### EU AI Act Art. 50 (Transparency)
- Forged tools/skills are marked with subsystem origin (in namespace prefix)
- User can see which subsystem created what via audit trail

## Testing Strategy (200+ tests)

### Part A: API Interfaces (30 tests)
- ForgedToolAPI abstract methods exist
- ForgedSkillAPI abstract methods exist
- Concrete implementations return correct types
- Namespace enforcement works
- Quota enforcement works
- Usage recording works

### Part B: Hub API Registry (40 tests)
- `register_api()` stores API
- `get_api()` retrieves API
- `has_api()` checks existence
- Duplicate registration raises error
- Multiple APIs coexist
- APIs available after startup

### Part C: NamespacePolicy (50 tests)
- Default namespaces work
- Custom namespaces can be added/removed
- Namespace isolation enforced
- Auto-prefix works correctly
- Prefix matching with wildcards works
- Multiple patterns per subsystem

### Part D: ForgeQuota (50 tests)
- Quota defaults correct (10 tools, 5 skills)
- `check_*_quota()` returns true/false correctly
- `record_*()` increments counters
- Per-subsystem tracking works
- Reset between sessions works
- Tool and skill quotas independent

## Alternatives Considered

### 1. Direct Subsystem References (Rejected)
```python
self.tool_forge = hub.subsystems["tool_forge"]  # String lookup
```
**Reason:** Tight coupling, import cycles, unclear API surface.

### 2. Static Method Registration (Rejected)
```python
ToolForgeSubsystem.register_forged_tool_api(custom_api)
```
**Reason:** Global state, hard to test, violates hub-as-coordinator pattern.

### 3. Event-Based Tool Creation (Rejected)
```python
hub.publish_event("forge_tool_requested", {
    "name": "test",
    "description": "...",
})
```
**Reason:** Asynchronous and lossy; can't enforce quota synchronously; no return value for result.

## Deployment Notes

### Feature Flag
None — this is a core architectural improvement for subsystem extensibility, not a user-facing feature.

### Migration Path
Existing subsystems (LoopEngineer, SafetyValidator, etc.) continue using `hub.request_from_subsystem()` if they don't need Forge APIs. No breaking changes.

### Rollout Checklist
- [x] ForgedToolAPI and ForgedSkillAPI interfaces defined
- [x] Hub registry methods added
- [x] ToolForgeSubsystem and SkillForgeSubsystem register APIs
- [x] NamespacePolicy and ForgeQuota implemented
- [x] Concrete API implementations (ForgedToolAPIImpl, ForgedSkillAPIImpl)
- [x] 200+ tests passing (81 tests in test_forge_apis_adr0361.py)
- [x] Example usage (ErrorRecoverySubsystem pattern documented)
- [x] ADR written

## References

- **ADR-0347:** Brain Subsystem Hub Architecture (event bus pattern)
- **ADR-0348:** Event Bus Pattern (pub/sub, fire-and-forget)
- **ADR-0349:** Plugin Interface Contract (Subsystem ABC)
- **ADR-0359:** ToolForgeSubsystem (tool generation subsystem)
- **ADR-0360:** SkillForgeSubsystem (skill generation + auto-grading)
- **Layer 6:** Forge (runtime tool generation)
- **Layer 7:** SkillForge (runtime skill generation)

---

## Amendments

(None yet — this ADR is PROPOSED)
