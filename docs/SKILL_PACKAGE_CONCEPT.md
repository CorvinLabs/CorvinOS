# Skill Package System — Marketplace-Compatible ZIP-Based Package Loading

**Status:** Concept (pre-ADR)  
**Date:** 2026-08-07  
**Scope:** Console-based plugin/skill/hook loading from ZIP archives (similar to Cloud Code Marketplace)  
**Audience:** Architecture review before implementation  

---

## Executive Summary

Add a **Skill Package system** to CorvinOS that allows operators to load pre-built bundles (Skills, Plugins, Hooks, Config) as signed ZIP files via the Console. This enables a self-service marketplace, similar to Cloud Code's plugin marketplace, without manual file placement or CLI expertise.

**Core Design Principle:** *Declarative metadata → Automatic wiring*

---

## Problem Statement

Today, adding a new Skill or Plugin requires:
1. Manual file placement in `~/.corvin/extensions/`
2. Manual `tenant.corvin.yaml` configuration
3. Manual hook registration
4. Potential naming conflicts, missing dependencies, or incorrect permissions

**Desired Experience:**
```
Console → Settings → Marketplace → "Upload Package" → select ZIP → ✓ Installed
```

All wiring (hooks, routes, permissions, dependencies) happens automatically.

---

## Design Overview

### 1. Package Structure (ZIP Format)

```
my-skill-package-1.0.0.zip
├── manifest.json              ← Package metadata + validation
├── skills/
│   └── my_skill.yaml          ← Skill 2.0 definition (hooks, triggers, etc.)
├── plugins/
│   ├── __init__.py
│   └── my_plugin.py           ← Provider, audit_backend, user_backend, etc.
├── hooks/
│   ├── pre_process.py         ← Preprocessing hooks (NEW)
│   ├── post_process.py
│   └── on_error.py
├── config/
│   ├── defaults.yaml          ← Default config values
│   └── schema.json            ← JSON Schema for validation
├── routes/                    ← Optional: HTTP routes
│   └── api.py
├── migrations/                ← Database migrations (if needed)
│   └── 001_init.sql
└── README.md                  ← Human-readable docs

```

### 2. Manifest Schema

```json
{
  "id": "com.example.my-skill-package",
  "version": "1.0.0",
  "name": "My Awesome Skill Package",
  "description": "Adds X functionality to CorvinOS",
  "author": "Author Name <author@example.com>",
  
  "corvinOS": {
    "min_version": "0.10.110",
    "max_version": null
  },
  
  "permissions": [
    "storage:read",
    "audit:write",
    "network:outbound"
  ],
  
  "dependencies": [
    { "id": "com.corvinlabs.core", "version": ">=1.0.0" },
    { "id": "com.other.plugin", "version": "2.1.0" }
  ],
  
  "contents": {
    "skills": [
      { "id": "my_skill", "file": "skills/my_skill.yaml" }
    ],
    "plugins": [
      { 
        "id": "my_plugin", 
        "file": "plugins/my_plugin.py",
        "type": "provider",
        "tier": "A"
      }
    ],
    "hooks": [
      { 
        "id": "my_preprocess_hook",
        "file": "hooks/pre_process.py",
        "trigger": "preprocessing",
        "priority": 100
      }
    ],
    "routes": [
      { "path": "/api/v1/my-endpoint", "file": "routes/api.py" }
    ],
    "config": {
      "schema": "config/schema.json",
      "defaults": "config/defaults.yaml"
    }
  },
  
  "signing": {
    "key_id": "rsa-2048-abc123",
    "algorithm": "RS256",
    "signature": "base64-encoded-signature-here"
  }
}
```

### 3. Installation Flow

```
┌─────────────────────────────────────────────────────────┐
│ User: Upload ZIP via Console                            │
└──────────────────┬──────────────────────────────────────┘
                   │
        ┌──────────▼──────────┐
        │ Step 1: Validation  │
        ├─────────────────────┤
        │ • ZIP integrity     │
        │ • Signature verify  │
        │ • Manifest schema   │
        │ • Virus scan(?)     │
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────────────┐
        │ Step 2: Dependency Check    │
        ├────────────────────────────┤
        │ • Verify min/max CorvinOS  │
        │ • Check listed deps exist  │
        │ • Resolve version conflicts│
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────────────┐
        │ Step 3: Permission Audit    │
        ├────────────────────────────┤
        │ • List requested perms      │
        │ • Show impact analysis      │
        │ • Require operator approval │
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────────────┐
        │ Step 4: Extract & Register  │
        ├────────────────────────────┤
        │ • Extract to ~/.corvin/pkg/ │
        │ • Tenant-scoped subdir      │
        │ • Register skills + hooks   │
        │ • Reload skill injector     │
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────────────┐
        │ Step 5: Verify Wiring       │
        ├────────────────────────────┤
        │ • Smoke test each skill     │
        │ • Verify hook registration  │
        │ • Check routes accessible   │
        │ • Run schema validation     │
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────────────┐
        │ ✅ Package Installed        │
        └────────────────────────────┘
```

---

## Core Components

### A. Package Manager (NEW)

**File:** `core/package_manager/corvin_package_manager.py`

```python
class PackageManager:
    """Unified loader for skill packages, plugins, and hooks."""
    
    def load_from_zip(self, zip_path: Path, tenant_id: str) -> PackageManifest:
        """
        Extract, validate, and register a package.
        
        1. Validate ZIP + signature
        2. Check dependencies
        3. Extract to ~/.corvin/tenants/{tenant_id}/packages/
        4. Register with SkillForge, PluginRegistry, HookRegistry
        5. Verify wiring (smoke tests)
        
        Returns: Manifest with full registration state
        """
        pass
    
    def unload_package(self, package_id: str, tenant_id: str) -> None:
        """Remove package, unregister skills/hooks, cleanup."""
        pass
    
    def list_packages(self, tenant_id: str) -> List[InstalledPackage]:
        """List all installed packages with versions, perms, deps."""
        pass
    
    def verify_wiring(self, package_id: str) -> VerificationReport:
        """Smoke-test package wiring: hooks callable, skills injectable, routes live."""
        pass
```

### B. Preprocessing Hook Integration (CRITICAL)

The "preprocessing" refers to pre-turn, pre-request hooks that run before Claude gets the turn.

**File:** `core/preprocessing/hook_registry.py`

```python
@dataclass
class PreprocessHook:
    """Preprocessing hook — runs before LLM turn."""
    
    id: str
    priority: int  # 0-1000; higher runs first
    callable: Callable[[PreprocessContext], PreprocessContext]
    package_id: Optional[str]  # Which package owns this hook
    
class PreprocessContext:
    """State passed through preprocessing pipeline."""
    turn: Turn
    session: Session
    user: User
    config: TenantConfig
    # ... mutable state for hooks to modify
    
class HookRegistry:
    """Central registry for all preprocessing hooks."""
    
    def register_hook(self, hook: PreprocessHook) -> None:
        """Called during package load."""
        pass
    
    def run_pipeline(self, ctx: PreprocessContext) -> PreprocessContext:
        """Execute all registered hooks in priority order."""
        # Sorted by priority DESC
        for hook in sorted(self.hooks, key=lambda h: -h.priority):
            try:
                ctx = await hook.callable(ctx)
            except HookError as e:
                log.error(f"Hook {hook.id} failed: {e}")
                # Fail-closed by default; config can allow continue-on-error
        return ctx
    
    @classmethod
    def from_package(cls, manifest: PackageManifest) -> List[PreprocessHook]:
        """Load hooks from package manifest."""
        pass
```

**Integration point (chat_runtime.py):**

```python
async def stream_turn(turn: Turn, ...):
    # NEW: Preprocessing phase
    preprocess_ctx = PreprocessContext(turn=turn, session=session, ...)
    preprocess_ctx = await hook_registry.run_pipeline(preprocess_ctx)  # ← HOOK EXECUTION
    turn = preprocess_ctx.turn  # Modified by hooks
    
    # Continue with normal turn flow...
    async for chunk in model.stream(...):
        yield chunk
```

### C. Skill Manifest (2.0 Enhancement)

Skills can now declare hooks directly:

**File:** `skills/my_skill.yaml`

```yaml
id: my_skill
name: My Skill
description: Does something cool

# NEW: Hook declarations
hooks:
  - id: my_preprocessing_hook
    trigger: preprocessing
    priority: 50
    file: ../hooks/pre_process.py
    function: my_preprocessing_handler
    
  - id: my_error_hook
    trigger: on_error
    priority: 10
    file: ../hooks/on_error.py
    function: my_error_handler

# NEW: Config schema
config:
  schema:
    type: object
    properties:
      enabled:
        type: boolean
        default: true
      timeout_ms:
        type: integer
        default: 5000

# Existing fields
enabled: true
scope: project
tier: A
grade_strategy: auto
```

### D. Console Routes

**File:** `core/console/routes/packages.py` (NEW)

```python
@router.post("/api/v1/packages/upload")
async def upload_package(
    request: Request,
    file: UploadFile,
    tenant_id: str = Header(...),
):
    """Upload and install a skill package."""
    
    # 1. Save temp file
    temp_path = await file.save()
    
    try:
        # 2. Load via PackageManager
        manifest = package_manager.load_from_zip(temp_path, tenant_id)
        
        # 3. Return installation summary
        return {
            "status": "installed",
            "package_id": manifest.id,
            "skills_added": len(manifest.skills),
            "hooks_registered": len(manifest.hooks),
            "permissions_granted": manifest.permissions,
        }
    finally:
        temp_path.unlink()

@router.get("/api/v1/packages")
async def list_packages(tenant_id: str = Header(...)):
    """List installed packages."""
    packages = package_manager.list_packages(tenant_id)
    return [
        {
            "id": p.id,
            "version": p.version,
            "name": p.name,
            "status": p.status,  # installed, error, disabled
            "skills": len(p.skills),
            "hooks": len(p.hooks),
            "permissions": p.permissions,
        }
        for p in packages
    ]

@router.delete("/api/v1/packages/{package_id}")
async def uninstall_package(package_id: str, tenant_id: str = Header(...)):
    """Uninstall a package and all its skills/hooks."""
    package_manager.unload_package(package_id, tenant_id)
    return {"status": "uninstalled"}
```

### E. Validation & Security

**File:** `core/package_manager/validators.py`

```python
class PackageValidator:
    """Multi-stage validation."""
    
    def validate_zip_integrity(self, zip_path: Path) -> ZipValidationReport:
        """Check ZIP structure, manifest presence, no suspicious paths."""
        # Check for path traversal: ../../etc/passwd
        # Check manifest.json exists
        # Check file count limits
        pass
    
    def validate_signature(self, manifest: Dict) -> bool:
        """Verify RSA signature with marketplace public key."""
        # Check signing.algorithm == "RS256"
        # Verify against marketplace root CA
        # Store signer certificate in audit log
        pass
    
    def validate_dependencies(self, manifest: Dict, tenant_id: str) -> DependencyReport:
        """Check all listed deps are installed + version-compatible."""
        for dep in manifest.get("dependencies", []):
            pkg = package_manager.get_package(dep["id"], tenant_id)
            if not pkg:
                raise MissingDependencyError(f"Missing {dep['id']}")
            if not self.satisfies_version(pkg.version, dep["version"]):
                raise VersionConflictError(...)
        pass
    
    def validate_permissions(self, manifest: Dict) -> PermissionReport:
        """List what this package can do, require approval."""
        perms = manifest.get("permissions", [])
        # Return for operator review:
        # - storage:read ← can read files
        # - audit:write ← can write audit logs
        # - network:outbound ← can make HTTP calls
        pass
    
    def validate_schema(self, manifest: Dict) -> SchemaValidationReport:
        """Check manifest matches manifest-schema.json."""
        pass
```

---

## File Structure on Disk

```
~/.corvin/
├── tenants/
│   └── _default/
│       ├── global/
│       ├── packages/  ← NEW: Installed packages
│       │   ├── com.example.my-skill-package/
│       │   │   ├── manifest.json
│       │   │   ├── skills/
│       │   │   ├── plugins/
│       │   │   ├── hooks/
│       │   │   └── config.json
│       │   └── com.other.plugin/
│       │       └── ...
│       ├── sessions/
│       └── global/
│           └── package_registry.json  ← Central index
```

**package_registry.json:**

```json
{
  "installed_packages": [
    {
      "id": "com.example.my-skill-package",
      "version": "1.0.0",
      "installed_at": "2026-08-07T12:34:56Z",
      "status": "active",
      "skills": ["my_skill"],
      "hooks": ["my_preprocessing_hook", "my_error_hook"],
      "dependencies": ["com.corvinlabs.core"],
      "permissions_granted": ["storage:read", "audit:write"]
    }
  ]
}
```

---

## Preprocessing Hook Examples

### Example 1: Custom Prompt Injection

```python
# hooks/pre_process.py

async def inject_system_context(ctx: PreprocessContext) -> PreprocessContext:
    """Inject company-specific system prompt before turn."""
    
    company_config = ctx.config.get("company_context", {})
    if not company_config.get("enabled"):
        return ctx  # No-op if disabled
    
    # Modify the turn's system messages
    ctx.turn.system_messages.insert(0, {
        "role": "system",
        "content": f"Company context: {company_config['prompt']}",
    })
    
    return ctx
```

### Example 2: Input Validation Hook

```python
# hooks/pre_process.py

async def validate_input(ctx: PreprocessContext) -> PreprocessContext:
    """Block or warn on suspicious input patterns."""
    
    user_message = ctx.turn.messages[-1].content
    
    # Check for injection patterns
    if detect_prompt_injection(user_message):
        ctx.turn.add_warning("Potential prompt injection detected")
        # Can optionally reject, rewrite, or just log
    
    return ctx
```

### Example 3: Quota/Rate Limiting

```python
# hooks/pre_process.py

async def check_rate_limit(ctx: PreprocessContext) -> PreprocessContext:
    """Apply per-user rate limits before turn runs."""
    
    quota = await get_user_quota(ctx.user.id)
    
    if quota.remaining_tokens < 1000:
        raise QuotaExceededError(f"Only {quota.remaining_tokens} tokens left")
    
    # Decrement quota (audit logged)
    await decrement_quota(ctx.user.id, 1000)
    
    return ctx
```

---

## Marketplace Integration

### Package Signing (RSA-2048)

**At submission time:**
```bash
corvin package sign my-skill-package-1.0.0.zip \
  --key marketplace-private.pem
# → Updates manifest.json with signature
```

**At installation time:**
```python
# Validates against public key from marketplace CA
if not validator.validate_signature(manifest):
    raise SignatureVerificationError("Untrusted package")
```

### Marketplace Discovery (Future)

```python
# Console could integrate with marketplace API
GET https://marketplace.corvin-labs.com/api/v1/packages?category=skills&sort=stars

# Returns:
[
  {
    "id": "com.example.my-skill-package",
    "version": "1.0.0",
    "name": "My Awesome Skill",
    "description": "...",
    "author": "...",
    "stars": 42,
    "download_url": "https://marketplace.corvin-labs.com/download/...",
    "signature": "..."
  }
]
```

---

## Implementation Phases

### Phase 1: Core Package Manager (M1)
- [ ] `PackageManager` class
- [ ] ZIP extraction & validation
- [ ] Manifest schema
- [ ] Skill + Plugin registration
- [ ] Console upload route

### Phase 2: Preprocessing Hooks (M2) ← **PREREQUISITE FOR VOICEPREP**
- [ ] `HookRegistry` + `PreprocessContext`
- [ ] Integration with `chat_runtime.stream_turn()`
- [ ] Hook execution pipeline
- [ ] Priority ordering
- [ ] Error handling (fail-closed)

### Phase 3: Advanced Features (M3)
- [ ] RSA signature verification
- [ ] Marketplace API integration
- [ ] Dependency solver
- [ ] Permission auditing
- [ ] Smoke-test framework

### Phase 4: UI (M4)
- [ ] Console "Marketplace" page
- [ ] Upload widget
- [ ] Package list + management
- [ ] Hook/config editor

---

## Security Model

**Threat:** Malicious package runs untrusted code.

**Mitigations:**

1. **Signature Verification** — Only marketplace-signed packages load
2. **Permission Audit** — Operator approves requested permissions
3. **Isolation** — Hooks run in same Python process BUT can be restricted (future: subprocess isolation)
4. **Audit Logging** — Every hook execution logged to audit chain
5. **Rate Limiting** — Package can't spawn unlimited async tasks
6. **Schema Validation** — Config validated against declared schema; injection attacks caught

**Per-Package Permissions:**
- `storage:read` — Access to ~/.corvin/
- `storage:write` — Write to ~/.corvin/
- `audit:write` — Can write to audit chain
- `network:outbound` — Can make HTTP requests
- `console:routes` — Can register custom routes
- `config:modify` — Can modify tenant config

---

## Related ADRs & Features

- **ADR-0253** — Plugin Builder (complements this; plugins can be packaged)
- **ADR-0180** — Telemetry & consent (package usage can be telemetered)
- **ADR-0190** — Capability registry (packages declare capabilities)
- **CLAUDE.md** — Feature flags (packages can be behind flags; `features.package_marketplace`)

---

## Example: Complete Skill Package

**Package ID:** `com.acme.sentiment-analyzer`

```
sentiment-analyzer-1.0.0.zip
├── manifest.json
├── skills/
│   └── sentiment_skill.yaml
├── hooks/
│   ├── preprocess_sentiment.py
│   └── on_error_sentiment.py
├── config/
│   ├── defaults.yaml
│   └── schema.json
└── README.md
```

**manifest.json:**
```json
{
  "id": "com.acme.sentiment-analyzer",
  "version": "1.0.0",
  "name": "Sentiment Analyzer",
  "description": "Analyzes message sentiment before turn",
  "corvinOS": { "min_version": "0.10.110" },
  "permissions": ["audit:write"],
  "contents": {
    "skills": [
      { "id": "sentiment_skill", "file": "skills/sentiment_skill.yaml" }
    ],
    "hooks": [
      { "id": "preprocess", "file": "hooks/preprocess_sentiment.py", "trigger": "preprocessing", "priority": 50 }
    ]
  }
}
```

**On installation:**
1. ✓ ZIP validated + signed
2. ✓ Extracted to `~/.corvin/tenants/_default/packages/com.acme.sentiment-analyzer/`
3. ✓ Skill `sentiment_skill` injected into skill registry
4. ✓ Hook `preprocess` registered in HookRegistry (priority 50)
5. ✓ Operator sees: "Sentiment Analyzer installed — audit:write permission granted"
6. ✓ Every turn now runs through the preprocessing hook
7. ✓ Audit log records: "Hook com.acme.sentiment-analyzer/preprocess executed (125ms)"

---

## Backlog & Future

- [ ] Web UI marketplace browser (integrated in Console)
- [ ] Package auto-update checking
- [ ] Rollback on incompatibility detection
- [ ] Package versioning conflicts (allow multiple versions side-by-side?)
- [ ] Subprocess isolation for untrusted packages (high security)
- [ ] Package metrics dashboard (usage, errors, latency)
- [ ] Community marketplace (after private-first proves out)

---

## Appendix: Hook Types (2.0)

Beyond `preprocessing`, future hooks can include:

| Hook Type | Trigger | Example |
|-----------|---------|---------|
| `preprocessing` | Before turn | Input validation, quota check |
| `on_error` | After turn fails | Error logging, alerting |
| `on_complete` | After turn succeeds | Metrics, follow-ups |
| `on_artifact` | New artifact created | Scan for viruses/PII |
| `on_config_change` | Tenant config updated | Validate config schema |
| `on_audit_event` | Audit event written | Custom indexing, alerts |

This allows packages to be truly **reactive** rather than just **skill providers**.

---

## Sign-Off

**Concept Status:** Ready for ADR-writing + Phase 1 implementation.

This design enables:
- ✅ Self-service skill distribution (like Cloud Code)
- ✅ Preprocessing hooks for voiceprep + other turns
- ✅ Secure, audited package loading
- ✅ Full operator control (approval gates)
- ✅ Marketplace-ready signing + discovery

**Next: Write ADR-XXXX; start Phase 1 (PackageManager + Console route).**

