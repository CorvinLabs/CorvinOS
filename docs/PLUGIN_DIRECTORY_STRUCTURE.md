# CorvinOS Plugin Directory Structure
## Global vs. Tenant-Scoped, Bundled vs. User-Installed

**Date:** 2026-07-26  
**Status:** Specification (Ready to Implement)  
**Replaces:** Flat plugin list with clear scoping model

---

## Directory Hierarchy

```
CorvinOS Project Root
/home/shumway/projects/CorvinOS/

├─ core/
│  ├─ compliance/                    ← Tier-0 HARDCODED (no plugins)
│  │  ├─ audit_writer.py
│  │  ├─ consent_gate.py
│  │  ├─ flow_guard.py
│  │  ├─ house_rules.py
│  │  └─ erasure.py
│  │
│  ├─ core_plugins/                  ← Tier-0 + Tier-1 BUNDLED (in wheel)
│  │  ├─ __init__.py
│  │  ├─ plugin_loader.py            ← Loads all core plugins
│  │  │
│  │  ├─ audit_compliance/           (Tier-0, compliance)
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py                ← class AuditCompliancePlugin(CorvinPlugin)
│  │  │  ├─ audit_writer.py
│  │  │  ├─ hash_chain.py
│  │  │  └─ test/
│  │  │     ├─ test_audit_writer.py
│  │  │     ├─ test_hash_chain.py
│  │  │     └─ test_integration.py
│  │  │
│  │  ├─ a2a_orchestration/          (Tier-1, infrastructure)
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py                ← class A2APlugin(CorvinPlugin)
│  │  │  ├─ orchestrator.py          ← Core A2A logic
│  │  │  ├─ hooks.py                 ← Extension points
│  │  │  ├─ attestation.py           ← Ed25519 verification
│  │  │  └─ test/
│  │  │
│  │  ├─ tde_routing/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ router.py
│  │  │  ├─ cost_model.py
│  │  │  └─ test/
│  │  │
│  │  ├─ conversation_recall/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ storage.py               ← Abstract backend
│  │  │  ├─ file_backend.py          ← Default file-based
│  │  │  ├─ encryption.py
│  │  │  └─ test/
│  │  │
│  │  ├─ acs_manager/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ manager.py               ← ACS orchestration
│  │  │  ├─ task_queue.py
│  │  │  ├─ worker_lifecycle.py
│  │  │  └─ test/
│  │  │
│  │  ├─ compute_worker/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ executor.py              ← Per-worker sandbox
│  │  │  ├─ token_counter.py
│  │  │  └─ test/
│  │  │
│  │  ├─ delegation_router/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ router.py                ← Policy-driven routing
│  │  │  ├─ policies.py
│  │  │  └─ test/
│  │  │
│  │  ├─ workflow_engine/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ dag_runner.py            ← DAG execution
│  │  │  ├─ nodes.py                 ← code/merge/route/ask_human
│  │  │  └─ test/
│  │  │
│  │  ├─ engine_control/             (Tier-1, execution layer)
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ engine_registry.py       ← Native/Hermes/TDE/ACS
│  │  │  ├─ provider_model.py        ← ADR-0181
│  │  │  ├─ routing_policy.py
│  │  │  └─ test/
│  │  │
│  │  ├─ admin_control_plane/
│  │  │  ├─ __init__.py
│  │  │  ├─ plugin.py
│  │  │  ├─ control.py               ← Admin operations
│  │  │  ├─ license.py               ← License validation
│  │  │  └─ test/
│  │  │
│  │  └─ test/
│  │     ├─ test_global_plugins.py   ← All global plugins boot
│  │     ├─ test_tier_isolation.py
│  │     └─ conftest.py
│  │
│  ├─ plugins/                       ← Plugin base interfaces
│  │  ├─ __init__.py
│  │  ├─ base.py                     ← CorvinPlugin abstract class
│  │  ├─ registry.py                 ← PluginRegistry (global + tenant scoped)
│  │  ├─ loader.py                   ← PluginLoader (global + tenant)
│  │  ├─ context.py                  ← PluginContext, hooks
│  │  └─ test/
│  │
│  ├─ api/                           ← API server (REST + gRPC)
│  │  ├─ __init__.py
│  │  ├─ server.py                   ← FastAPI app
│  │  ├─ routes/
│  │  │  ├─ __init__.py
│  │  │  ├─ admin.py                 ← /api/admin/*
│  │  │  ├─ engine.py                ← /api/engine/*
│  │  │  ├─ execution.py             ← /api/execute
│  │  │  ├─ delegation.py            ← /api/delegate
│  │  │  ├─ health.py                ← /health
│  │  │  └─ plugins.py               ← /api/admin/plugins/*
│  │  │
│  │  ├─ middleware/
│  │  │  ├─ __init__.py
│  │  │  ├─ correlation_id.py        ← X-Correlation-ID injection
│  │  │  ├─ tenant_isolation.py      ← X-Tenant-ID validation
│  │  │  ├─ auth.py                  ← JWT validation
│  │  │  └─ error_handler.py
│  │  │
│  │  ├─ grpc/
│  │  │  ├─ corvin.proto
│  │  │  ├─ corvin_pb2.py
│  │  │  ├─ corvin_pb2_grpc.py
│  │  │  ├─ server.py                ← gRPC server (for bridges)
│  │  │  └─ client.py                ← gRPC client example
│  │  │
│  │  └─ test/
│  │     ├─ test_api.py
│  │     ├─ test_grpc.py
│  │     └─ conftest.py
│  │
│  └─ orchestration/                 ← Internal (no plugins)
│     ├─ __init__.py
│     ├─ bootstrap.py                ← Boot sequence
│     ├─ lifecycle.py                ← Startup/shutdown
│     └─ test/
│
├─ bridges/                          ← OPTIONAL, separate directory (not in core)
│  └─ (bridges can be in separate package or monorepo, not core_plugins)
│
└─ tests/
   ├─ conftest.py                    ← Shared test config
   ├─ test_core_isolation.py         ← Core works without bridges
   ├─ test_plugin_scoping.py         ← Tenant isolation
   └─ fixtures/
      ├─ mock_plugins.py
      ├─ mock_tenants.py
      └─ test_data.py


User Home
~/.corvin/

├─ global/                           ← Installation-wide config
│  ├─ config.yaml
│  ├─ license.json
│  └─ stats.jsonl
│
└─ tenants/
   ├─ _default/                      ← Default tenant
   │  ├─ config.yaml                 ← Tenant-specific config
   │  ├─ audit.jsonl                 ← Immutable audit trail
   │  ├─ plugins/                    ← Tenant-scoped Tier-2/3 (USER CAN MODIFY)
   │  │  ├─ discord_bridge/          (Pre-installed Tier-2, can disable)
   │  │  │  ├─ __init__.py
   │  │  │  ├─ plugin.py             ← class DiscordBridgePlugin(CorvinPlugin)
   │  │  │  ├─ bridge.py             ← Discord client logic
   │  │  │  ├─ config.yaml           ← Token, settings (per tenant)
   │  │  │  └─ state.json            ← Runtime state
   │  │  │
   │  │  ├─ slack_bridge/            (Pre-installed Tier-2, can disable)
   │  │  │  ├─ __init__.py
   │  │  │  ├─ plugin.py
   │  │  │  ├─ bridge.py
   │  │  │  └─ config.yaml
   │  │  │
   │  │  ├─ structured_logging/      (Tier-2, Loki/ELK integration)
   │  │  │  ├─ __init__.py
   │  │  │  ├─ plugin.py
   │  │  │  ├─ logger.py
   │  │  │  └─ config.yaml
   │  │  │
   │  │  ├─ postgres_audit_backend/  (Tier-3, licensed)
   │  │  │  ├─ __init__.py
   │  │  │  ├─ plugin.py
   │  │  │  ├─ backend.py
   │  │  │  ├─ config.yaml           ← DB URL, credentials
   │  │  │  └─ license.key
   │  │  │
   │  │  ├─ okta_auth/               (Tier-3, licensed)
   │  │  │  ├─ __init__.py
   │  │  │  ├─ plugin.py
   │  │  │  ├─ okta_client.py
   │  │  │  ├─ config.yaml
   │  │  │  └─ license.key
   │  │  │
   │  │  ├─ custom_routing/          (Tier-2, user-created)
   │  │  │  ├─ __init__.py
   │  │  │  ├─ plugin.py             ← Custom geo-routing
   │  │  │  ├─ routing_logic.py
   │  │  │  └─ config.yaml
   │  │  │
   │  │  └─ (user can add more plugins here)
   │  │
   │  ├─ hooks/                      ← Tenant extension hooks (code snippets)
   │  │  ├─ model_selection.py       ← Custom model choice logic
   │  │  ├─ engine_selection.py      ← Custom engine routing
   │  │  ├─ routing_policy.py
   │  │  └─ (user can add custom hooks)
   │  │
   │  ├─ recall_storage/             ← User data (encrypted)
   │  │  └─ conversations.db
   │  │
   │  ├─ session_storage/            ← User sessions
   │  │  └─ sessions.jsonl
   │  │
   │  └─ logs/                       ← Local logs
   │     ├─ corvin.log
   │     └─ plugins.log
   │
   ├─ tenant2/                       ← Additional tenant (optional)
   │  ├─ config.yaml
   │  ├─ audit.jsonl
   │  ├─ plugins/
   │  │  ├─ slack_bridge/            (Tenant2 can have different versions)
   │  │  └─ (tenant2-specific plugins)
   │  └─ ...
   │
   └─ tenant3/
      └─ ...
```

---

## Plugin Lifecycle: Where Files Go

### Step 1: Plugin Discovered (in Repo)

```
core_plugins/discord_bridge/
├─ __init__.py
├─ plugin.py             ← Must define class DiscordBridgePlugin(CorvinPlugin)
├─ bridge.py
├─ requirements.txt
└─ test/
```

### Step 2: Plugin Shipped (in Wheel)

```
corvinOS-0.11.0-py3-none-any.whl
└─ corvin/
   └─ core/
      └─ core_plugins/
         └─ discord_bridge/
            └─ ... (all files from step 1)
```

### Step 3: Plugin Installed (on User Machine)

```
User runs: corvinctl plugin install discord-bridge
OR: corvinctl plugin enable discord-bridge

System:
  1. Check if already installed (in ~/.corvin/tenants/_default/plugins/)
  2. If not: copy from wheel to ~/.corvin/tenants/_default/plugins/
  3. Load plugin into registry
  4. Call plugin.on_load()
  5. Audit event logged
```

### Step 4: Plugin Used (Runtime)

```
User's request → API → Engine Control → Engine → Discord Bridge (plugin)
                                           ↓
                              Bridge handles routing to Discord
                                           ↓
                              Response comes back
```

---

## Global Plugins: Always Present

### Bundled in Wheel, Auto-loaded

```python
# core/core_plugins/plugin_loader.py

GLOBAL_PLUGINS = [
    "audit_compliance",
    "a2a_orchestration",
    "tde_routing",
    "conversation_recall",
    "acs_manager",
    "compute_worker",
    "delegation_router",
    "workflow_engine",
    "engine_control",
    "admin_control_plane",
]

def load_global_plugins():
    """Load all global plugins from core_plugins/"""
    for plugin_name in GLOBAL_PLUGINS:
        module = __import__(f"corvin.core.core_plugins.{plugin_name}")
        plugin_class = getattr(module, f"{plugin_name}Plugin")
        plugin = plugin_class()
        
        # Tier-0: crash if fail
        if plugin.plugin_type == "tier-0":
            plugin.on_load(ctx)
            registry.register(plugin)
        
        # Tier-1: degrade if fail
        elif plugin.plugin_type == "tier-1":
            try:
                plugin.on_load(ctx)
                registry.register(plugin)
            except Exception as e:
                logger.error(f"Tier-1 plugin {plugin_name} failed: {e}")
```

---

## Tenant Plugins: User-Installed, Optional

### Pre-installed (Downloadable from Package)

```
~/.corvin/tenants/_default/plugins/

discord_bridge/       ← Pre-installed, can disable
slack_bridge/         ← Pre-installed, can disable
structured_logging/   ← Pre-installed, can disable
```

### User-Installed (via CLI or API)

```
corvinctl plugin install --plugin-id postgres-audit-backend \
                         --license-key sk_live_abc123 \
                         --tenant-id _default

Result:
~/.corvin/tenants/_default/plugins/postgres_audit_backend/
```

---

## Config Hierarchy

### Installation-Wide

```yaml
# ~/.corvin/global/config.yaml

server:
  mode: "headless"
  host: "0.0.0.0"
  port: 8000
  api: "rest+grpc"

plugins:
  load_mode: "hybrid"        # in-process or subprocess
  auto_load_global: true     # always load Tier-0/1
```

### Tenant-Specific

```yaml
# ~/.corvin/tenants/_default/config.yaml

tenant:
  id: "_default"
  name: "Default Tenant"

plugins:
  enabled:
    - discord-bridge
    - slack-bridge
    - structured-logging
  
  disabled:
    - postgres-audit-backend     # User disabled this
    - custom-routing             # Not installed

plugin_config:
  discord-bridge:
    token: "${DISCORD_TOKEN}"    # From env or secrets
    rate_limit: 100
  
  slack-bridge:
    token: "${SLACK_TOKEN}"
```

---

## Comparison: Old vs. New

### Old (Monolithic)

```
CorvinOS/
├─ discord_bridge/          (always loaded, in-process)
├─ slack_bridge/            (always loaded, in-process)
├─ telegram_bridge/         (always loaded, in-process)
├─ forge/                   (always loaded, in-process)
└─ ... (everything in one process)

Deployment: Single binary, single process
Scale: Bridges block threads
Stability: One plugin crash → system crash
```

### New (Modular)

```
Core (Tier-0/1, in-process, always)
├─ Audit, Consent, Flow Guard, House Rules, Erasure
├─ A2A, TDE, Recall, ACS, Compute, Delegation, Workflows, Engine
└─ Admin Control Plane

Tenant Plugins (~/.corvin/tenants/_default/plugins/)
├─ discord_bridge (optional, can fail independently)
├─ slack_bridge (optional, can fail independently)
├─ structured_logging (optional)
├─ postgres_audit_backend (licensed, optional)
└─ (user-installed)

Deployment: Core + bridges as subprocesses (via gRPC)
Scale: Core + bridge farm independent
Stability: Plugin crash doesn't affect core
```

---

## Summary: Plugin Organization

| Aspect | Global (Repo) | Tenant (Home) |
|--------|---------------|--------------|
| **Location** | `/repo/core/core_plugins/` | `~/.corvin/tenants/*/plugins/` |
| **Scope** | All instances | Per-tenant |
| **Tier** | 0 + 1 only | 2 + 3 only |
| **Lifecycle** | Bundled in wheel | User-installed/managed |
| **Restart required** | Yes (code change) | No (plugin disable enough) |
| **Isolation** | Shared across tenants | Per-tenant |
| **Examples** | Audit, A2A, TDE, ACS | Discord, Slack, Postgres backend |

