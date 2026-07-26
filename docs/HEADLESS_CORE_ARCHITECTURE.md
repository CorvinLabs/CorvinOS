# CorvinOS Headless Core Architecture
## Stable OS Engine with Engine/Worker, Plugin Scoping, Tenant Isolation

**Date:** 2026-07-26  
**Status:** Conceptual Framework (Ready for ADR)  
**Audience:** Architecture, DevOps, Platform Team

---

## The Shift: From Bridge-Centric to Core-Centric

### Current (Today)
```
CorvinOS = Compliance Core + Discord Bridge + Slack Bridge + Web UI + ...
           (tightly coupled, UI-driven)
```

**Problem:**
- Bridges are in stdlib → every install carries Discord, Slack, etc.
- UI state affects server state (Web UI restart breaks API)
- Hard to deploy as pure headless server
- Scaling: bridges block orchestration threads

### New: Headless Core Engine (Proposed)
```
CorvinOS Core Engine (Server Only, No UI)
├─ Compliance (Tier-0, hardcoded)
├─ Agentic Compute (Tier-1, in-process or remote)
├─ Engine Control (Tier-1, local)
├─ A2A + TDE + Recall (Tier-1, in-process)
└─ HTTP API Gateway (REST + gRPC)

Bridges (Plugins, Tier-2/3)
├─ Discord Bridge (separate process/plugin)
├─ Slack Bridge (separate process/plugin)
├─ Web UI (separate app, talks to Core API)
└─ Voice (separate plugin)
```

**Benefits:**
- ✅ Core is pure backend (no UI entanglement)
- ✅ Bridges can fail without crashing core
- ✅ Easier to scale (headless farm of worker nodes)
- ✅ Simpler to deploy (server + minimal plugins)
- ✅ Tenant isolation clearer (plugins are tenant-scoped)

---

## Architecture: Headless Core + Plugin Scoping

### Tier-0: Core Compliance (Hardcoded in Binary)
```
core/
├─ core_compliance/
│  ├─ audit_writer.py
│  ├─ consent_gate.py
│  ├─ flow_guard.py
│  ├─ house_rules.py
│  └─ erasure.py
```

**Deployment:** Always present. Not plugin-loadable. Tripwired at boot.

---

### Tier-1: Core Infrastructure (Bundled, Required)

#### Global Plugin Scope (in Repo)
```
/home/shumway/projects/CorvinOS/core/core_plugins/

Global Plugins (bundled with binary, always loaded):
├─ audit-compliance/                (Tier-0, in-process)
├─ a2a-orchestration/               (Tier-1, in-process)
├─ tde-routing/                     (Tier-1, in-process)
├─ conversation-recall/             (Tier-1, in-process)
├─ acs-manager/                     (Tier-1, in-process or remote)
├─ compute-worker/                  (Tier-1, in-process or remote)
├─ delegation-router/               (Tier-1, in-process)
├─ workflow-engine/                 (Tier-1, in-process)
├─ engine-control/                  (Tier-1, in-process)
└─ admin-control-plane/             (Tier-1, in-process)
```

**Deployment:** Bundled in Python wheel. Loaded by PluginRegistry before any tenant plugins.

---

### Tier-2/3: Tenant Plugins (Scoped, Optional)

#### Tenant-Specific Plugin Scope
```
~/.corvin/tenants/_default/

Tenant Plugins (user/admin-installed, per-tenant):
├─ plugins/
│  ├─ discord-bridge/               (Tier-2, in-process or subprocess)
│  ├─ slack-bridge/                 (Tier-2, in-process or subprocess)
│  ├─ telegram-bridge/              (Tier-3, community, subprocess)
│  ├─ structured-logging/           (Tier-2, in-process)
│  ├─ postgres-audit-backend/       (Tier-3, licensed, in-process)
│  ├─ okta-auth/                    (Tier-3, licensed, in-process)
│  ├─ custom-routing/               (Tier-2, custom, in-process)
│  ├─ monitoring-alerts/            (Tier-2, in-process)
│  └─ (user-installed plugins)
│
└─ plugin_config.yaml               (tenant plugin settings)
```

**Deployment:** User/admin can install, enable, disable without restarting core.

---

## Plugin Scoping: Global vs. Tenant

### Global Plugins (Core)
```python
# core/plugin_loader.py

class PluginLoader:
    def load_global_plugins(self):
        """Load Tier-0 + Tier-1 from repo."""
        global_path = Path(__file__).parent / "core_plugins"
        
        for plugin_dir in global_path.iterdir():
            if not plugin_dir.is_dir():
                continue
            
            # Load plugin
            plugin = self._load_plugin(plugin_dir)
            
            # Tier-0: Always load, crash if fail
            if plugin.plugin_type == "tier-0":
                try:
                    plugin.on_load(self.ctx)
                    self.registry.register(plugin)
                except Exception as e:
                    raise BootError(f"Tier-0 plugin failed: {e}")
            
            # Tier-1: Load, degrade if fail
            elif plugin.plugin_type == "tier-1":
                try:
                    plugin.on_load(self.ctx)
                    self.registry.register(plugin)
                except Exception as e:
                    logger.error(f"Tier-1 plugin failed: {e} (degraded)")
    
    def load_tenant_plugins(self, tenant_id: str):
        """Load Tier-2/3 from tenant home."""
        tenant_home = Path.home() / ".corvin" / "tenants" / tenant_id
        plugins_path = tenant_home / "plugins"
        
        if not plugins_path.exists():
            return  # No tenant plugins
        
        for plugin_dir in plugins_path.iterdir():
            if not plugin_dir.is_dir():
                continue
            
            try:
                plugin = self._load_plugin(plugin_dir, tenant_id=tenant_id)
                plugin.on_load(PluginContext(..., tenant_id=tenant_id))
                self.registry.register(plugin)
                logger.info(f"Tenant plugin loaded: {plugin.plugin_id}")
            except Exception as e:
                logger.warning(f"Tenant plugin failed: {plugin_dir.name}: {e}")
                # Continue; tenant plugins are optional
```

### Boot Sequence

```python
# core/bootstrap.py

def boot_corvin_os():
    """
    Boot sequence: global first, tenant second.
    """
    loader = PluginLoader()
    
    # Phase 1: Load global Tier-0 + Tier-1 (required)
    loader.load_global_plugins()
    # At this point: compliance + orchestration guaranteed
    
    # Phase 2: Load tenant plugins (optional, per-tenant)
    for tenant_id in get_configured_tenants():
        loader.load_tenant_plugins(tenant_id)
    
    # Phase 3: Start API server
    api = APIServer(plugin_registry=loader.registry)
    api.start(host="0.0.0.0", port=8000)
```

---

## Directory Structure (Revised)

```
/home/shumway/projects/CorvinOS/
├─ core/
│  ├─ compliance/
│  │  ├─ audit_writer.py          (Tier-0, in binary)
│  │  ├─ consent_gate.py
│  │  ├─ flow_guard.py
│  │  ├─ house_rules.py
│  │  └─ erasure.py
│  │
│  ├─ core_plugins/               (Tier-0 + Tier-1, bundled)
│  │  ├─ audit_compliance/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  └─ test/
│  │  ├─ a2a_orchestration/
│  │  ├─ tde_routing/
│  │  ├─ conversation_recall/
│  │  ├─ acs_manager/
│  │  ├─ compute_worker/
│  │  ├─ delegation_router/
│  │  ├─ workflow_engine/
│  │  ├─ engine_control/
│  │  └─ admin_control_plane/
│  │
│  ├─ plugins/                    (Base plugin interface)
│  │  ├─ base.py
│  │  ├─ registry.py
│  │  ├─ loader.py
│  │  └─ context.py
│  │
│  └─ api/
│     ├─ server.py               (FastAPI/gRPC)
│     ├─ routes/
│     │  ├─ admin.py
│     │  ├─ health.py
│     │  ├─ engine.py
│     │  ├─ execution.py
│     │  └─ delegation.py
│     └─ middleware/

~/.corvin/
├─ tenants/
│  └─ _default/
│     ├─ plugins/                (Tenant-scoped Tier-2/3)
│     │  ├─ discord_bridge/
│     │  │  ├─ __init__.py
│     │  │  ├─ plugin.py
│     │  │  ├─ bridge.py
│     │  │  └─ config.yaml
│     │  ├─ slack_bridge/
│     │  ├─ postgres_backend/
│     │  └─ custom_routing/
│     │
│     ├─ plugin_config.yaml       (Tenant plugin settings)
│     ├─ audit.jsonl              (Immutable audit trail)
│     └─ hooks/                   (Tenant extension hooks)

/separate/apps/ (if monorepo)
├─ web-ui/                       (React, talks to Core API)
├─ cli/                           (CLI client)
└─ docs/
```

---

## API Server: Only Entry Point

### REST API (No UI Dependency)

```python
# core/api/server.py

@app.get("/health")
async def health_check():
    """Is core alive? Minimal response."""
    return {"status": "healthy"}

@app.post("/api/chat")
async def execute_request(request: Request):
    """Execute a request (get response, no bridge routing)."""
    # Request → Engine Control → Delegate/Execute → Response
    # No UI knowledge, pure API
    return {"response": "...", "cost": 100, "model": "sonnet"}

@app.get("/api/admin/plugins")
async def list_plugins(current_user: User = Depends(require_admin)):
    """List plugins (global + tenant)."""
    return control_plane.list_plugins()

@app.post("/api/admin/plugins/install")
async def install_plugin(plugin_id: str, license_key: str, current_user: User = Depends(require_admin)):
    """Install a tenant plugin."""
    # Validate license
    # Download plugin
    # Load into registry
    # Audit event
    return {"ok": True, "plugin_id": plugin_id}
```

### gRPC (For High-Performance Bridges)

```protobuf
// core/api/corvin.proto

service CorvinCore {
  rpc ExecuteRequest(ExecuteRequest) returns (ExecuteResponse);
  rpc Stream(StreamRequest) returns (stream StreamChunk);
  rpc Delegate(DelegateRequest) returns (DelegateResponse);
}
```

**Why gRPC?**
- Low latency (bridges need <100ms roundtrip)
- Streaming (for long-running tasks)
- Better than REST for worker-to-core communication

---

## Bridge Deployment Model

### Option A: In-Process Plugins (Simple)
```
CorvinOS Core (single process)
├─ Tier-0/1: hardcoded
├─ Discord Bridge (plugin, same process)
├─ Slack Bridge (plugin, same process)
└─ ... (all plugins in same process)
```

**Pros:** Simple deployment, low latency  
**Cons:** One plugin crash can crash everything

### Option B: Subprocess Plugins (Safer)
```
CorvinOS Core (process A)
├─ Tier-0/1: hardcoded
├─ gRPC server on :8000
└─ [wait for plugins]

Discord Bridge (subprocess B, gRPC client)
Slack Bridge (subprocess C, gRPC client)
Telegram Bridge (subprocess D, gRPC client)
```

**Pros:** Plugins isolated, can restart independently  
**Cons:** More complex deployment, slightly higher latency (gRPC)

### Option C: Hybrid (Recommended)
```
CorvinOS Core (process A)
├─ Tier-0/1: hardcoded (in-process)
├─ Tier-2 Essential (in-process, e.g., structured logging)
└─ gRPC server on :8000

Discord Bridge (subprocess, gRPC client)  # Pre-installed but separate
Slack Bridge (subprocess, gRPC client)
Custom Bridges (subprocess, user-installed)
```

**Pros:** Best of both (critical stuff safe, bridges flexible)

---

## Stability Improvements with Headless Core

### 1. Engine/Worker Isolation
```
Core Engine Selection & Worker Management (Tier-1, in-process)
├─ Request comes in via API
├─ Engine Control selects engine (Haiku/Sonnet/Opus)
├─ Delegation Router picks ACS/TDE/Native
└─ Worker executes (either in-process or remote)

If worker fails → core continues (fallback to native)
If bridge fails → core continues (API still works)
```

### 2. No UI Restart = No Service Disruption
```
OLD: Web UI restart → API down → users disconnected
NEW: Bridge restart → API continues → users briefly disconnected from one channel
     (but other bridges + API still work)
```

### 3. Worker Scaling
```
CorvinOS Core (fixed, stable)
├─ 1 instance in region
├─ Handles routing/delegation
└─ Points to N worker pools

Worker Pool (auto-scaling)
├─ 100+ instances (can scale up/down)
├─ Handles actual execution
└─ Stateless (each worker independent)
```

### 4. Easier Testing
```
# Test core without any bridges
pytest core/ -v
# Core works standalone

# Test bridge independently
pytest discord_bridge/ -v
# Bridge can mock Core API
```

---

## Tenant Plugin Isolation

### Problem: Tenant A's Plugin Crashes Tenant B

### Solution: Tenant-Scoped Registry
```python
# core/plugin_loader.py

class TenantAwarePluginRegistry:
    def __init__(self):
        self.global_plugins = {}     # Tier-0/1, shared
        self.tenant_plugins = {}     # Tier-2/3, per-tenant
    
    def get_plugins_for_tenant(self, tenant_id: str) -> list:
        """Return global + tenant plugins for this tenant."""
        plugins = list(self.global_plugins.values())
        tenant_scoped = self.tenant_plugins.get(tenant_id, {})
        plugins.extend(tenant_scoped.values())
        return plugins
    
    def load_tenant_plugin(self, tenant_id: str, plugin_id: str):
        """Install plugin for this tenant only."""
        try:
            plugin = self._load(plugin_id)
            plugin.on_load(PluginContext(..., tenant_id=tenant_id))
            
            if tenant_id not in self.tenant_plugins:
                self.tenant_plugins[tenant_id] = {}
            
            self.tenant_plugins[tenant_id][plugin_id] = plugin
            
            # Audit (per tenant)
            self.audit_writer.log_event({
                "event_type": "plugin.installed",
                "tenant_id": tenant_id,
                "plugin_id": plugin_id,
                "admin_user": ctx.user_id,
            })
        except Exception as e:
            logger.error(f"Failed to load {plugin_id} for {tenant_id}: {e}")
            # Fail silently (tenant plugin optional)
    
    def disable_tenant_plugin(self, tenant_id: str, plugin_id: str):
        """Uninstall plugin for this tenant."""
        if tenant_id in self.tenant_plugins:
            if plugin_id in self.tenant_plugins[tenant_id]:
                plugin = self.tenant_plugins[tenant_id][plugin_id]
                plugin.on_unload()
                del self.tenant_plugins[tenant_id][plugin_id]
                
                # Audit
                self.audit_writer.log_event({
                    "event_type": "plugin.disabled",
                    "tenant_id": tenant_id,
                    "plugin_id": plugin_id,
                    "admin_user": ctx.user_id,
                })
```

**Guarantee:** Tenant A installs broken plugin → only Tenant A affected, Tenant B unaffected.

---

## Config: Headless Mode

### Binary Decision: Headless or Bridged

```yaml
# .corvin/tenants/_default/config.yaml

server:
  mode: "headless"           # headless or bridged
  host: "0.0.0.0"
  port: 8000
  api: "rest+grpc"           # rest or rest+grpc

plugins:
  load_mode: "hybrid"        # in-process or subprocess or hybrid
  
  auto_load:
    - audit-compliance
    - a2a-orchestration
    - tde-routing
    - conversation-recall
    - acs-manager
    - compute-worker
    - delegation-router
    - workflow-engine
    - engine-control
    - admin-control-plane
  
  # Tenant plugins loaded separately per tenant
```

---

## Deployment Models

### Model A: Single Core + Bridges (Small)
```
CorvinOS Core (headless, all Tier-0/1)
  ├─ API on port 8000
  └─ Runs in same container
  
Discord Bridge (subprocess)
  ├─ Connects via gRPC
  ├─ Runs in same container
  └─ Restarts independently

Docker image: Single CorvinOS image (includes Core + Bridges)
```

### Model B: Core + Bridge Farm (Medium)
```
CorvinOS Core (headless)
  ├─ API on port 8000
  ├─ Container A
  └─ Stable core (rarely restarts)

Discord Bridge (container B, separate)
  ├─ gRPC client
  └─ Restarts independently

Slack Bridge (container C, separate)
Telegram Bridge (container D, separate)

Kubernetes: Core is StatefulSet, Bridges are Deployments
```

### Model C: Distributed Workers (Large)
```
CorvinOS Core (headless, routing only)
  ├─ API on port 8000
  ├─ Region A
  └─ Delegates to workers

Worker Pool (region A)
  ├─ 100+ instances
  ├─ Stateless
  └─ Auto-scaling

Worker Pool (region B)
  ├─ 100+ instances
  └─ Auto-scaling

Bridges (multi-region)
  ├─ gRPC clients
  └─ High availability
```

---

## Updated Boot Sequence

```python
# __main__.py (entry point)

async def main():
    # Phase 1: Boot Tier-0 + Tier-1 (global)
    loader = PluginLoader()
    loader.load_global_plugins()
    
    # Phase 2: Load tenant plugins
    for tenant_id in get_tenants():
        loader.load_tenant_plugins(tenant_id)
    
    # Phase 3: Start API server (headless)
    api = APIServer(
        host=config.server.host,
        port=config.server.port,
        api_type=config.server.api
    )
    
    logger.info("✅ CorvinOS Core ready")
    logger.info(f"  Tier-0: {len(loader.global_tier_0)} plugins (compliance)")
    logger.info(f"  Tier-1: {len(loader.global_tier_1)} plugins (orchestration)")
    logger.info(f"  Tenant: {len(loader.tenant_plugins)} plugins")
    logger.info(f"  API: {config.server.api} on {config.server.host}:{config.server.port}")
    
    # Serve indefinitely
    await api.serve()
```

---

## ADR Updates Needed

### ADR-0234 (revised)
- Change: Bridges are NOT in Tier-2, they're external plugins
- New: Tier-2 in-process: Structured Logging, Monitoring
- New: Bridges/UI are Tier-2 external (subprocess or separate app)

### ADR-0237 (new)
- Plugin Scoping (global vs. tenant)
- In-process vs. subprocess plugin load
- Tenant isolation guarantees

### ADR-0238 (new)
- Headless Core Architecture
- API-driven (no UI coupling)
- Deployment models (single, farm, distributed)

### ADR-0239 (new)
- Engine/Worker Isolation
- Stability guarantees (one plugin crash ≠ system crash)
- Scaling models

---

## Summary: Headless Core Benefits

| Aspect | Before (Bridged) | After (Headless) |
|--------|-----------------|-----------------|
| **Core restart** | Breaks API + Discord | Breaks nothing (bridges restart independently) |
| **Scaling** | Whole app scales | Core + Workers scale separately |
| **Testing** | Must mock Discord/Slack | Test Core API independently |
| **Stability** | One plugin can crash all | Isolated failures (tenant + bridge-specific) |
| **Deployment** | Monolith (one container) | Microservices (Core + Bridge farm) |
| **Performance** | Bridges block threads | Bridges async via gRPC |
| **Development** | All in one process | Easier to debug (separate processes) |

---

## Next Steps

1. Create ADRs (0237, 0238, 0239)
2. Refactor core_plugins directory structure
3. Extract Bridges to subprocess model
4. Add gRPC API gateway
5. Update plugin loader (global + tenant scoping)
6. Document headless deployment guide

