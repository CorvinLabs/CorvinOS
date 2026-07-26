# CorvinOS Plugin System — Detailed Implementation Plan (Phase 1)
## With License Model & Admin Control

**Date:** 2026-07-26  
**Status:** Implementation Framework (Ready for K_MAX=5 Iteration)  
**Audience:** Engineering + Product

---

## Critical Refinement: What ADRs Missed

### The ADR-0234/0235 Gap

**ADR-0234/0235 said:** "Core is 2.4 KB (compliance only), everything else is plugin."

**What we actually need:** 
- Compliance core (2.4 KB) ✅
- **License-protected infrastructure** (A2A, TDE, Audit Backends, Auth) — not plugins, not free
- Standard Edition (pre-installed, free)
- Premium plugins (charged)
- Community plugins (free, community-maintained)

**The Problem:** A2A is strategic IP. If we make it a plugin, anyone forks it → we can't charge. Same with TDE, advanced routing, managed auth.

### Revised Architecture (4-Tier, Not 3-Tier)

```
Tier 0: Mandatory Core (2.4 KB, hardcoded)
  ├─ HTTP Router
  ├─ Audit Writer (L16)
  ├─ Consent Gate (L18)
  ├─ Flow Guard (L34)
  ├─ House Rules (L44)
  ├─ Erasure (L36)
  └─ Plugin Registry

Tier 1: License Core (Free in Open Source, $$$ in Managed)
  ├─ A2A Instance Coordination (L38)  ← Strategic IP
  ├─ TDE Routing Engine (L22)         ← Differentiator
  ├─ Conversation Recall (L28)        ← Data, user trust
  ├─ Admin Dashboard                  ← Control plane
  └─ Multi-Tenant System (L19-21)     ← Enterprise requirement
  
  **Why "License Core"?** These are open-source but NOT replaceable.
  Forks get them. Managed SaaS charges per-user. Enterprise licenses gate access to API.
  Admin can't rip these out.

Tier 2: Standard Edition (Pre-installed, Free)
  ├─ Forge (L6)           ← Differentiator
  ├─ SkillForge (L7)      ← Differentiator
  ├─ Bridges (Discord, Slack, etc.)
  ├─ Logging (Structured, L23 STT metadata)
  └─ Health Monitoring    ← NerveFiber basics

Tier 3: Premium Plugins (Charged)
  ├─ Advanced STT (Cloud providers)
  ├─ Advanced Data Classification (ML)
  ├─ Custom Audit Backends (Postgres, Splunk, etc.)
  ├─ Custom Auth Backends (OKTA, LDAP, SAML)
  └─ Advanced Monitoring (Predictive alerts)

Tier 4: Community Marketplace (Free, community-maintained)
  ├─ Custom bridges (Telegram, Matrix, etc.)
  ├─ Domain tools
  └─ Compliance templates
```

---

## Phase 1 Implementation: Layers 0 + 1

**Goal:** Extract compliance core + license infrastructure. Make it *extensible* but *not replaceable*.

### Timeline
- **Weeks 1-2:** Core extraction + plugin registry (ADR-0236 → code)
- **Weeks 3-4:** License infrastructure (A2A, TDE, Recall)
- **Weeks 5-6:** Admin control plane + extension points
- **Weeks 7-8:** Testing + hardening

### Detailed Work Streams

---

## Stream 1: Core Extraction (Weeks 1-2)

### 1.1 Plugin Registry + Loader

**Current state:** Plugins in `core/plugins/` scattered.  
**Target:** Unified registry with lifecycle contract.

```python
# core/plugins/registry.py — THE SOURCE OF TRUTH

class PluginRegistry:
    """Central registry. Admin can't disable compliance plugins."""
    
    def __init__(self, corvin_home: Path, tenant_id: str):
        self.registry: dict[str, Plugin] = {}
        self.license_core: set[str] = {
            "audit-compliance/1.0.0",
            "a2a-orchestration/1.0.0",
            "tde-routing/1.0.0",
            "conversation-recall/1.0.0",
            "admin-control-plane/1.0.0",
        }
    
    def load_all(self) -> None:
        """Boot: load core + license plugins (required), then optional."""
        # Phase 1: Load Tier 0 (hardcoded, mandatory)
        self._load_core_compliance()
        
        # Phase 2: Load Tier 1 (license infrastructure, required if open-source)
        self._load_license_core()
        
        # Phase 3: Load Tier 2 (standard, optional but pre-installed)
        self._load_standard_edition()
        
        # Phase 4: Load Tier 3 (premium, gated by license)
        self._load_premium_plugins()
    
    def _load_core_compliance(self):
        """NEVER fails silently. Tripwire on any error."""
        plugins = [
            AuditWriterPlugin(),
            ConsentGatePlugin(),
            FlowGuardPlugin(),
            HouseRulesPlugin(),
            ErasurePlugin(),
        ]
        for p in plugins:
            try:
                p.on_load(PluginContext(self))
                self.registry[p.plugin_id] = p
            except Exception as e:
                raise BootError(f"Core compliance plugin {p.plugin_id} failed: {e}")
    
    def _load_license_core(self):
        """License infrastructure. Required in open-source, gated in managed."""
        license_required = [
            ("a2a-orchestration", A2APlugin()),
            ("tde-routing", TDEPlugin()),
            ("conversation-recall", ConversationRecallPlugin()),
            ("admin-control-plane", AdminPlugin()),
        ]
        for name, plugin in license_required:
            if self._is_license_feature_available(name):
                try:
                    plugin.on_load(PluginContext(self))
                    self.registry[plugin.plugin_id] = plugin
                except Exception as e:
                    # Log, but don't crash — degrade gracefully
                    logger.error(f"License plugin {name} failed: {e}")
            else:
                logger.info(f"License feature {name} not available (license)")
    
    def _is_license_feature_available(self, feature_name: str) -> bool:
        """Check license tier. Stub for now."""
        # In Managed: read from license.json
        # In Open-Source: always True
        return os.getenv("CORVIN_MANAGED") != "1"
    
    def health_check_all(self) -> dict[str, HealthStatus]:
        """Health of all plugins. License-core failures → degradation, not crash."""
        results = {}
        for pid, plugin in self.registry.items():
            try:
                status = plugin.health_check()
                results[pid] = status
                
                # If Tier 0 fails: CRASH
                if pid.startswith("audit-") or pid.startswith("consent-"):
                    if not status.ok:
                        raise BootError(f"Core compliance {pid} unhealthy")
                
                # If Tier 1 fails: DEGRADE (log, continue)
                if pid in self.license_core:
                    if not status.ok:
                        logger.warning(f"License feature {pid} degraded: {status.message}")
            except Exception as e:
                results[pid] = HealthStatus(ok=False, message=str(e))
        return results
    
    def can_disable_plugin(self, plugin_id: str) -> bool:
        """Admin asks: can I turn this off?"""
        # Tier 0: NO
        if plugin_id.startswith(("audit-", "consent-", "flow-", "house-", "erasure-")):
            return False
        
        # Tier 1: NO (license core)
        if plugin_id in self.license_core:
            return False
        
        # Tier 2+: YES
        return True
```

**Tests:**
- ✅ Mandatory plugins fail to load → boot crashes
- ✅ License plugins fail → degrade, don't crash
- ✅ Standard plugins optional → system continues
- ✅ `can_disable_plugin()` returns correct tier

**Outcome:** Admin can see which plugins are mandatory, which are optional.

---

### 1.2 Plugin Lifecycle Contract

```python
# core/plugins/base.py — INTERFACE FOR ALL PLUGINS

from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class PluginContext:
    """What every plugin gets on load."""
    registry: "PluginRegistry"
    audit_writer: "AuditWriter"
    consent_gate: "ConsentGate"
    logger: "CorvinLogger"
    tenant_id: str
    corvin_home: Path

class CorvinPlugin(ABC):
    """Base class for all plugins (compliance + standard + premium)."""
    
    plugin_id: str  # e.g., "audit-compliance/1.0.0"
    plugin_type: str  # "tier-0", "tier-1", "tier-2", "tier-3"
    version: str
    display_name: str
    description: str
    dependencies: list[str] = []  # e.g., ["audit-compliance/1.0.0"]
    
    @abstractmethod
    def on_load(self, ctx: PluginContext) -> None:
        """Boot hook. Initialize resources. Raise if critical failure."""
        pass
    
    @abstractmethod
    def on_unload(self) -> None:
        """Shutdown hook. Clean up resources."""
        pass
    
    @abstractmethod
    def on_config_change(self, config: dict) -> None:
        """Config changed. Reload if needed."""
        pass
    
    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Is this plugin healthy? Called every N seconds."""
        pass
    
    @property
    def is_replaceable(self) -> bool:
        """Can this plugin be disabled/replaced?"""
        return self.plugin_type not in ("tier-0", "tier-1")
```

**Why separate `tier-0` and `tier-1`?**
- **Tier-0:** Compliance (hardcoded, immutable, fail-closed)
- **Tier-1:** License infrastructure (strategic IP, required in open-source, gated in managed)

Admin sees both as "mandatory" but they're enforced differently.

---

### 1.3 Admin Control Interface

```python
# core/admin/control_plane.py

class AdminControlPlane:
    """What admin can see and do."""
    
    def __init__(self, registry: PluginRegistry):
        self.registry = registry
    
    def list_plugins(self) -> list[PluginInfo]:
        """Admin dashboard: show all plugins with tier + disableability."""
        return [
            PluginInfo(
                plugin_id=plugin.plugin_id,
                tier=plugin.plugin_type,
                is_disableable=self.registry.can_disable_plugin(plugin.plugin_id),
                status=self.registry.registry[plugin.plugin_id].health_check(),
                reason_if_not_disableable="Compliance requirement" | "License core" | None,
            )
            for plugin in self.registry.registry.values()
        ]
    
    def disable_plugin(self, plugin_id: str) -> Result:
        """Admin tries to disable a plugin."""
        if not self.registry.can_disable_plugin(plugin_id):
            return Result(
                ok=False,
                message=f"{plugin_id} cannot be disabled (tier {plugin.plugin_type})"
            )
        
        try:
            plugin = self.registry.registry[plugin_id]
            plugin.on_unload()
            del self.registry.registry[plugin_id]
            
            # Audit event
            self.registry.audit_writer.log_event({
                "event_type": "admin.plugin_disabled",
                "plugin_id": plugin_id,
                "admin_user": ctx.user_id,
            })
            
            return Result(ok=True, message=f"{plugin_id} disabled")
        except Exception as e:
            return Result(ok=False, message=str(e))
    
    def install_premium_plugin(self, plugin_id: str, license_key: str) -> Result:
        """Admin installs a premium plugin."""
        # Validate license
        if not self._validate_license(plugin_id, license_key):
            return Result(ok=False, message="Invalid license for this feature")
        
        # Load plugin
        try:
            plugin = self._load_plugin_by_id(plugin_id)
            plugin.on_load(PluginContext(...))
            self.registry.registry[plugin_id] = plugin
            
            # Audit event
            self.registry.audit_writer.log_event({
                "event_type": "admin.premium_plugin_installed",
                "plugin_id": plugin_id,
                "admin_user": ctx.user_id,
            })
            
            return Result(ok=True, message=f"{plugin_id} installed")
        except Exception as e:
            return Result(ok=False, message=str(e))
    
    def get_plugin_config(self, plugin_id: str) -> dict:
        """Admin retrieves plugin settings."""
        plugin = self.registry.registry.get(plugin_id)
        if not plugin:
            return {}
        
        # Each plugin defines its own config schema
        return plugin.get_config()
    
    def set_plugin_config(self, plugin_id: str, config: dict) -> Result:
        """Admin changes plugin settings (e.g., audit backend URL)."""
        plugin = self.registry.registry.get(plugin_id)
        if not plugin:
            return Result(ok=False, message=f"Plugin {plugin_id} not found")
        
        try:
            plugin.on_config_change(config)
            self.registry.audit_writer.log_event({
                "event_type": "admin.plugin_config_changed",
                "plugin_id": plugin_id,
                "admin_user": ctx.user_id,
            })
            return Result(ok=True, message="Config updated")
        except Exception as e:
            return Result(ok=False, message=str(e))
```

**Admin's power:**
- ✅ Can see all plugins + their tier
- ✅ Can disable Tier 2/3 plugins
- ❌ Cannot disable Tier 0/1 plugins
- ✅ Can configure any plugin
- ✅ Can install premium plugins (with license key)

---

## Stream 2: License Infrastructure (Weeks 3-4)

### 2.1 A2A as Tier-1 (Not Plugin)

**Current state:** A2A is in `core/orchestration/`.  
**Target:** Move to tier-1 infrastructure, but keep it *extensible*.

```python
# core/license/a2a_core.py

class A2AOrchestrationPlugin(CorvinPlugin):
    """
    A2A is strategic IP. It's in core (not replaceable).
    But it has extension points for custom routing/attestation.
    """
    
    plugin_id = "a2a-orchestration/1.0.0"
    plugin_type = "tier-1"  # License infrastructure
    
    def on_load(self, ctx: PluginContext):
        self.ctx = ctx
        self.hook_manager = HookManager()
        
        # Register default hooks (e.g., standard attestation)
        self._register_default_hooks()
    
    def _register_default_hooks(self):
        """Built-in hooks that can't be overridden (compliance)."""
        self.hook_manager.register(
            "attestation.verify",
            self._verify_instance_attestation_default  # REQUIRED
        )
        self.hook_manager.register(
            "routing.select_target",
            self._select_target_default  # Can be overridden
        )
    
    def send_task(self, envelope: TaskEnvelope) -> Result:
        """Send a task to a peer instance."""
        # Before send: compliance check (immutable)
        if not self._verify_instance_attestation_default(envelope.target_instance):
            return Result(ok=False, message="Instance attestation failed")
        
        # Before send: custom routing (extensible via hook)
        selected = self.hook_manager.call("routing.select_target", envelope)
        if not selected:
            selected = envelope.target_instance  # Fallback to default
        
        # Send
        try:
            response = self._send_http_request(selected, envelope)
            self.ctx.audit_writer.log_event({
                "event_type": "a2a.task_sent",
                "source": os.getenv("CORVIN_INSTANCE_ID"),
                "target": selected,
                "envelope_id": envelope.envelope_id,
            })
            return Result(ok=True, data=response)
        except Exception as e:
            self.ctx.audit_writer.log_event({
                "event_type": "a2a.task_failed",
                "source": os.getenv("CORVIN_INSTANCE_ID"),
                "target": selected,
                "error": type(e).__name__,
            })
            return Result(ok=False, message=str(e))
    
    def register_hook(self, hook_name: str, handler: Callable) -> Result:
        """Plugin can register a custom hook (e.g., custom routing)."""
        ALLOWED_HOOKS = {
            "routing.select_target",      # Choose target instance dynamically
            "attestation.custom_verify",  # ADD to attestation checks (not replace)
            "envelope.pre_send",          # Inspect before send
        }
        
        if hook_name not in ALLOWED_HOOKS:
            return Result(ok=False, message=f"Hook {hook_name} not allowed")
        
        self.hook_manager.register(hook_name, handler)
        return Result(ok=True, message=f"Hook {hook_name} registered")
    
    def health_check(self) -> HealthStatus:
        """Is A2A connected?"""
        if self._peer_instances_reachable():
            return HealthStatus(ok=True)
        else:
            return HealthStatus(ok=False, message="No peer instances reachable")
```

**Why Tier-1 (not replaceable)?**
- A2A is how instances coordinate → if someone forks, they get this for free
- We can't charge if it's pluggable
- But we CAN charge for "managed A2A" (hosted, monitored, global network)

**What's extensible?**
- Custom routing logic
- Additional attestation checks
- Hook points (pre_send, post_receive)

**What's NOT extensible?**
- Core attestation (Ed25519 signature check)
- Audit logging of all A2A events
- Denial of send if attestation fails

---

### 2.2 TDE as Tier-1 (Strategic Differentiator)

Same pattern as A2A: Tier-1, required, but has extension points.

```python
class TDERoutingPlugin(CorvinPlugin):
    """TDE is strategic. But we allow custom cost models via hooks."""
    
    plugin_id = "tde-routing/1.0.0"
    plugin_type = "tier-1"
    
    def register_cost_model(self, model_name: str, cost_fn: Callable) -> Result:
        """
        Plugin can register a custom cost model.
        E.g., "my-org-cost" = use our internal pricing.
        But core TDE logic (routing algorithm) is immutable.
        """
        if not callable(cost_fn):
            return Result(ok=False, message="cost_fn must be callable")
        
        self.custom_cost_models[model_name] = cost_fn
        return Result(ok=True, message=f"Cost model {model_name} registered")
```

---

### 2.3 Conversation Recall as Tier-1 (User Data Protection)

```python
class ConversationRecallPlugin(CorvinPlugin):
    """
    User data is load-bearing. Recall is Tier-1.
    
    Extensibility: custom storage backends (but core data model is immutable).
    """
    
    plugin_id = "conversation-recall/1.0.0"
    plugin_type = "tier-1"
    
    def register_storage_backend(self, backend_name: str, backend: RecallBackend) -> Result:
        """
        Plugin can add a custom storage backend.
        E.g., "postgres-local" = store locally, "s3-archive" = archive to S3.
        But core schema is immutable.
        """
        self.backends[backend_name] = backend
        return Result(ok=True, message=f"Backend {backend_name} registered")
```

---

## Stream 3: Admin Control Plane (Weeks 5-6)

### 3.1 Control Plane REST API

```python
# core/admin/api.py

@app.get("/api/admin/plugins")
async def list_plugins(current_user: User = Depends(require_admin)):
    """Admin dashboard: list all plugins with tier + status."""
    return control_plane.list_plugins()

@app.post("/api/admin/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, current_user: User = Depends(require_admin)):
    """Try to disable a plugin."""
    return control_plane.disable_plugin(plugin_id)

@app.post("/api/admin/plugins/{plugin_id}/config")
async def set_plugin_config(
    plugin_id: str,
    config: dict,
    current_user: User = Depends(require_admin)
):
    """Update plugin config."""
    return control_plane.set_plugin_config(plugin_id, config)

@app.post("/api/admin/plugins/install-premium")
async def install_premium(
    plugin_id: str,
    license_key: str,
    current_user: User = Depends(require_admin)
):
    """Install a premium plugin."""
    return control_plane.install_premium_plugin(plugin_id, license_key)
```

### 3.2 Admin Dashboard (React)

```
Plugins Tab:

┌─────────────────────────────────────────────────────────┐
│ Plugin                    Tier    Status    Actions     │
├─────────────────────────────────────────────────────────┤
│ audit-compliance          Tier 0  ✅        [info]      │
│ consent-gate              Tier 0  ✅        [info]      │
│ a2a-orchestration         Tier 1  ✅        [config]    │
│ tde-routing               Tier 1  ✅        [config]    │
│ forge                     Tier 2  ✅        [disable]   │
│ discord-bridge            Tier 2  ✅        [disable]   │
│ advanced-stт              Tier 3  ⚠️        [install]   │
│ okta-auth                 Tier 3  ❌        [install]   │
└─────────────────────────────────────────────────────────┘

Legend:
- Tier 0: Mandatory compliance (cannot disable)
- Tier 1: License core (cannot disable, built-in)
- Tier 2: Standard edition (can disable, but probably shouldn't)
- Tier 3: Premium (install with license key)
```

---

## Stream 4: Testing & Hardening (Weeks 7-8)

### 4.1 Test Matrix

```
Test Suite: core_plugin_system_test.py

✅ PluginRegistry
  - Load Tier 0 → crash if any fail
  - Load Tier 1 → degrade if fail
  - Load Tier 2 → skip if fail
  - Load Tier 3 → skip if license invalid
  
✅ AdminControl
  - can_disable_plugin(tier-0) → False
  - can_disable_plugin(tier-1) → False
  - can_disable_plugin(tier-2) → True
  - disable_plugin(tier-2) → OK, audit logged
  - disable_plugin(tier-0) → Error, not disableable
  
✅ A2A Extensibility
  - register_hook("routing.select_target") → OK
  - register_hook("attestation.verify") → Error (immutable)
  - custom routing called for each send
  
✅ Multi-Tenant Isolation
  - Plugins in tenant-A can't see tenant-B's config
  - Audit events isolated per tenant
  
✅ Graceful Degradation
  - If Tier-1 plugin fails → log warning, continue
  - If Tier-0 plugin fails → crash
  - If Tier-2 plugin fails → skip, continue
  
✅ License Gate
  - Premium plugin without license → install fails
  - Premium plugin with license → install OK
  - License expired → plugin disables on next check
```

---

## Revised ADR Strategy

The ADRs need these updates:

### ADR-0234 (Core vs. Plugins) — Revised
- Add "Tier-1: License Infrastructure" tier
- Clarify: A2A, TDE, Recall are Tier-1 (not replaceable, but have extension points)
- Extension points are HOW we let admins customize without replacing

### ADR-0237 (Admin Control & Extensibility) — NEW
- Define which features are extensible (hooks, backends, models)
- Define which features are immutable (compliance, attestation)
- Admin control plane REST API
- Extension point registry

### ADR-0238 (License Enforcement) — NEW
- How premium plugins are gated
- How open-source vs. managed mode differs
- How license expiry is handled

---

## Key Principles for Admin & Extensibility

### 1. **Hierarchy of Control**
```
Admin can't touch:
  Tier 0 (compliance) — hardcoded, tripwired
  Tier 1 (license) — required, strategic IP

Admin can configure:
  Tier 1 hooks (A2A routing, TDE cost model, Recall storage)
  Tier 2 (standard) plugins
  Tier 3 (premium) plugins (if licensed)
```

### 2. **Extensibility without Replacement**
```
A2A is NOT replaceable (Tier-1).
But admin can register custom routing hooks.
⟹ Keeps IP protected, allows customization.
```

### 3. **License Model**
```
Open-source:  All tiers unlocked, no license check
Managed:      Tier 1 bundled in subscription
              Tier 2 bundled in subscription
              Tier 3 gated by license key
```

---

## Implementation Checklist

- [ ] **Week 1-2:** Core extraction + Plugin Registry
  - [ ] PluginRegistry with tier system
  - [ ] Plugin lifecycle contract (on_load, on_unload, health_check)
  - [ ] AdminControlPlane basic class
  - [ ] 20+ unit tests (load, disable, health)

- [ ] **Week 3-4:** License infrastructure
  - [ ] A2APlugin refactored (Tier-1, extensible)
  - [ ] TDEPlugin refactored (Tier-1, extensible)
  - [ ] ConversationRecallPlugin refactored (Tier-1)
  - [ ] Hook registration mechanism
  - [ ] License gate for Tier 3

- [ ] **Week 5-6:** Admin control plane
  - [ ] REST API (/api/admin/plugins/*)
  - [ ] AdminDashboard React component
  - [ ] Config panel per plugin
  - [ ] License key installation UI

- [ ] **Week 7-8:** Testing & hardening
  - [ ] Full test matrix
  - [ ] E2E: disable Tier-2, verify system works
  - [ ] E2E: install premium plugin, verify license check
  - [ ] E2E: custom hook registration, verify it's called
  - [ ] Docs: Admin guide + Extension point reference

---

## Success Criteria

✅ **Architecture:**
- Core is 2.4 KB, untouchable
- Tier-1 is 2 KB, required but has hooks
- Tier-2/3 are optional
- Admin can see tier + disableability for every plugin

✅ **Control:**
- Admin can disable Tier-2/3 plugins from dashboard
- Admin can't disable Tier-0/1 (system prevents it)
- Admin can configure any plugin
- All actions audited

✅ **Extensibility:**
- A2A accepts custom routing hooks
- TDE accepts custom cost models
- Recall accepts custom storage backends
- Each extension point is documented

✅ **License:**
- Premium plugins require license key
- Open-source mode unlocks everything
- Managed mode checks license on boot
- License expiry disables Tier-3 plugins

---

## Example: How Admin Customizes A2A

```python
# Admin's custom plugin (optional)
from corvin_plugins import CorvinPlugin

class CustomRoutingPlugin(CorvinPlugin):
    plugin_id = "custom-routing/1.0.0"
    plugin_type = "tier-2"
    
    def on_load(self, ctx):
        # Register custom routing logic
        a2a = ctx.registry.registry.get("a2a-orchestration/1.0.0")
        
        def my_routing_logic(envelope):
            # Route based on org, region, cost, etc.
            if envelope.target_instance.region == "eu":
                return self.eu_peer
            return envelope.target_instance
        
        a2a.register_hook("routing.select_target", my_routing_logic)
        ctx.logger.info("Custom A2A routing registered")
```

Admin installs this custom plugin → A2A uses it for routing.
But A2A's core (attestation, audit, send) is immutable.

---

## Conclusion

**What changed from ADR-0234:**
1. Added Tier-1: License infrastructure (A2A, TDE, Recall)
2. Made Tier-1 required but extensible (not replaceable)
3. Admin control plane with clear permissions
4. Extension points (hooks) instead of replacement

**Why this works:**
- Compliance is immutable (Tier-0) ✅
- Strategic IP is protected (Tier-1) ✅
- Admin has clear control (dashboard) ✅
- System is extensible (hooks) ✅
- License model is defensible ✅

