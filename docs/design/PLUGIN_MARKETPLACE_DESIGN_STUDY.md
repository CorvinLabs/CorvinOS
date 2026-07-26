# Plugin Marketplace — Design Study (NOT an ADR)

**Status:** **Design study.** Superseded in part by
[ADR-0233](../../../Corvin-ADR/decisions/0233-plugin-system-consolidation.md);
NOT promoted to an ADR and NOT an implementation plan.
**Date:** 2026-07-26
**Author:** Claude Code
**Implementation plan:** [`docs/implementation/PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md`](../implementation/PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md)

> **Read this first.** The document below was written as "Ready for Implementation"
> and claimed to be production-ready. An audit on 2026-07-26 measured the
> accompanying prototype (`core/orchestration/plugin_system/`, since retired) and
> found: `api.py` not importable, three of six manager modules 0 bytes, nothing
> wired into backend or frontend, `test_models.py` consisting of 22 `pytest.skip`
> calls, and **none** of the compliance mechanisms below implemented — no hash-chain
> write, no checksum or signature verification, no consent gate, no sandbox, no
> cgroup quota.
>
> What ADR-0233 keeps: the product goal (runtime install/enable/configure with a
> Console surface), the JSON-Schema-driven settings UI, the manifest/dependency/
> settings-validation model (salvaged into `corvin_plugins/manifest.py`), and the
> lifecycle-hook shape.
>
> What ADR-0233 supersedes:
> - **Tier A/B/C as trust levels** → "tier" means ADR-0156's capability boundary
>   repo-wide; provenance is a separate `origin` field (builtin/vetted/community).
> - **A new marketplace downloader** → distribution reuses ADR-0096 (`mcp_manager`,
>   per-spawn SHA256/digest verification, L34/L35 gates) and ADR-0142/0156.
> - **Quota via cgroups, signing authority, community tier, ratings, monetization**
>   → out of scope until a separate ADR with a real signing authority exists.
> - **A second `PluginRegistry`/lifecycle** → the ADR-0030 contract in
>   `core/plugins/corvin_plugins/` is the only one.
>
> Sections below that describe those superseded parts are retained for the design
> rationale only. Do not implement from this document.

---

## Executive Summary

**Goal:** Transform CorvinOS from "feature flag" extensibility to "app-store" extensibility — plugins install, configure, and uninstall at runtime, without core releases.

**How it works:**
1. User opens **Settings → Plugins → Marketplace**
2. Discovers "AI Code Review v2.0.1" 
3. Clicks **Install** → system downloads, verifies, sandboxes, and activates
4. Plugin shows a **Settings panel** (auto-generated from JSON Schema)
5. User adjusts settings (model, review depth) → plugin reloads gracefully
6. Uninstall removes plugin + cleans up state

**Compliance baked in:** Every install/enable/config-change logged to hash-chained audit.jsonl (GDPR Art. 30). Tier C plugins require explicit consent (GDPR Art. 6,7). Signed artifacts (L37). Isolated execution (Forge-bwrap).

---

## Context

**Current state:** Feature additions require:
- Code change in core
- Feature flag definition
- Manual Console UI for each setting
- Full release cycle

**Problems this creates:**
1. **Rigid versioning:** Features tied to CorvinOS releases; can't hotfix a plugin
2. **No discovery:** Where do users find new plugins? Buried in release notes?
3. **All-or-nothing:** Flag is binary; no "partial rollout" of a plugin feature
4. **No third-party ecosystem:** Community can't build & distribute plugins
5. **Audit blind spots:** Plugin activations not logged to GDPR trail
6. **Silent failures:** Bad plugins can crash core (no isolation)

---

## Decision

We will build **Plugin System v1 (v0.11.0)**, a unified extension platform with:

### Layer Structure

```
┌─────────────────────────────────────────┐
│ Plugin Marketplace (corvinlabs, Community)
│  ↑ metadata, ratings, binaries, docs    
└──────────────────┬──────────────────────┘
                   │ download + verify
┌──────────────────▼──────────────────────┐
│ Plugin Registry (local, per-tenant)     │
│  ↓ install / enable / config / uninstall
└──────────────────┬──────────────────────┘
                   │ IPC / MCP / Forge
┌──────────────────▼──────────────────────┐
│ Plugin Runtime (Sandbox + Lifecycle)    │
│  ↓ hooks, state, quota, telemetry       
└──────────────────┬──────────────────────┘
                   │ audit events
┌──────────────────▼──────────────────────┐
│ Audit Log (hash-chained, GDPR)          │
└─────────────────────────────────────────┘
```

### 5 Architectural Pillars

#### 1. **Plugin Registry (Versioned, Persistent)**

**File:** `.corvin/tenants/_default/plugins/registry.yaml`

```yaml
spec:
  version: 1
  schema_version: "1.0"     # For backward-compat when we change registry format
  
plugins:
  ai-code-review:
    # Identity
    id: ai-code-review
    version: "2.0.1"          # Semver (major.minor.patch)
    marketplace_id: corvinlabs/ai-code-review/2.0.1
    
    # Installation metadata
    installed_at: 2026-07-26T10:30:00Z
    installed_by: user@example.com
    update_policy: minor      # major | minor | patch | none
    
    # Enablement state
    enabled: true
    enabled_at: 2026-07-26T10:31:00Z
    
    # Settings (user-configured values)
    settings:
      model: sonnet
      review_depth: 3
      custom_rules: "{}"
    settings_schema_version: "1.0"  # Detect breaking schema changes
    
    # Compliance metadata (LOAD-BEARING)
    tier: b                   # a (built-in) | b (vetted) | c (community)
    pii_risk: medium          # none | low | medium | high → gates consent
    requires_consent: true    # GDPR Art. 6,7
    sandbox_mode: true        # Run in bwrap?
    sandbox_tier: light       # light (Tier B) | strict (Tier C)
    audit_required: true
    
    # Entrypoint (plugin type determines how it runs)
    entrypoint:
      type: mcp               # mcp | forge-tool | skill-forge | python-native
      # For MCP: server_url: "localhost:9999"
      # For Forge: module: "plugins/ai-code-review/tool.py"
      # For SkillForge: module: "plugins/ai-code-review/skill.md"
      # For Python: module: "plugins/ai-code-review/__init__.py"
    
    # Dependencies + version constraints
    dependencies:
      postgres-query-tool: ">=1.0.0"
      syntax-analyzer: "2.0.0"  # Exact match
    
    # Marketplace metadata (for offline mode)
    marketplace:
      source: https://marketplace.corvinlabs.com
      artifact_url: "marketplace/ai-code-review-2.0.1.zip"
      checksum: sha256:deadbeef
      size_bytes: 5200000
      cached_locally: true      # Do we have the .zip on disk?
      cache_path: ".corvin/tenants/_default/plugins/cache/ai-code-review-2.0.1.zip"
      
      # Fallback mirrors for offline mode
      mirrors:
        - url: https://mirror1.corvinlabs.io/plugins
          status: healthy
        - url: https://mirror2.corvinlabs.io/plugins
          status: unhealthy_last_checked_at: 2026-07-26T08:00:00Z
    
    # Resource quota (NEW: prevents runaway plugins)
    quota:
      monthly_tokens_usd: 50.0      # Budget cap for this plugin
      tokens_used_this_month: 12.34
      cpu_percent_max: 25           # Max CPU% during execution
      memory_mb_max: 512            # Max memory footprint
      
    # State persistence (NEW: where does plugin store local data?)
    state:
      storage_path: ".corvin/tenants/_default/plugins/state/ai-code-review"
      size_bytes: 4500000
      last_cleanup: 2026-07-20T00:00:00Z
    
    # Signing & trust (NEW: who signed this?)
    signature:
      algorithm: "rsa-4096"
      signed_by: "corvinlabs-signer-v1"
      timestamp: 2026-07-26T10:30:00Z
      valid_until: 2027-07-26T10:30:00Z
    
    # Version breaking-change tracking (NEW)
    version_history:
      - version: "1.9.0"
        settings_schema_version: "1.0"
        breaking_changes: []
      - version: "2.0.0"
        settings_schema_version: "2.0"  # NEW SCHEMA
        breaking_changes:
          - "old_setting 'legacy_rules' removed, replaced by 'custom_rules'"
            migration: "copy legacy_rules JSON to custom_rules"
      - version: "2.0.1"
        settings_schema_version: "2.0"
        breaking_changes: []
    
    # Last error recovery (NEW)
    last_error: null
    error_retry_count: 0
    graceful_shutdown_in_progress: false
```

#### 2. **Settings Schema + Dynamic UI**

**File:** `.corvin/tenants/_default/plugins/schemas/ai-code-review.json`

```json
{
  "$schema": "http://json-schema.org/draft-7/schema",
  "title": "AI Code Review Settings",
  "description": "Configure your code review parameters",
  "type": "object",
  "properties": {
    "model": {
      "type": "string",
      "enum": ["haiku", "sonnet", "opus"],
      "default": "sonnet",
      "title": "Claude Model",
      "description": "Haiku = faster/cheaper, Opus = slower/better",
      "ui": {
        "component": "select",
        "category": "Model Selection",
        "required": true
      }
    },
    "review_depth": {
      "type": "integer",
      "minimum": 1,
      "maximum": 5,
      "default": 3,
      "title": "Review Depth",
      "ui": {
        "component": "slider",
        "category": "Behavior",
        "step": 1
      }
    },
    "max_file_size_kb": {
      "type": "integer",
      "minimum": 10,
      "maximum": 5000,
      "default": 500,
      "title": "Max File Size (KB)",
      "description": "Skip files larger than this",
      "ui": {
        "component": "number",
        "category": "Performance"
      }
    }
  },
  "required": ["model"],
  "additionalProperties": false
}
```

**Console auto-generates a panel from this schema.** No custom UI code per plugin.

#### 3. **Lifecycle Hooks + Graceful State Management**

```python
# plugins/ai-code-review/__init__.py

from corvin_plugin import (
    Plugin, PluginConfig, PluginLogger, PluginContext,
    PluginStateManager, PluginQuotaManager, PluginTelemetry
)
import asyncio

class AICodeReviewPlugin(Plugin):
    """Main plugin class — all hooks optional."""
    
    def __init__(self):
        self.model = None
        self.cache = {}
    
    async def on_install(self, config: PluginConfig, logger: PluginLogger):
        """Called once at install time (not at every boot)."""
        logger.info(f"Installing v{config.version}")
        # Download models, validate API keys, etc.
        # This is NOT per-boot overhead.
    
    async def on_enable(self, context: PluginContext, logger: PluginLogger):
        """Called when user toggles ON in Console."""
        logger.info("Loading models into memory")
        model_name = context.config.settings["model"]
        # Load the model (expensive operation)
    
    async def on_config_change(
        self,
        old_settings: dict,
        new_settings: dict,
        context: PluginContext,
        logger: PluginLogger
    ):
        """Called when user changes settings."""
        if old_settings["model"] != new_settings["model"]:
            logger.info(f"Model change: {old_settings['model']} → {new_settings['model']}")
            # Reload model (or queue it if currently executing)
            await self._reload_model_gracefully(new_settings["model"])
        
        # Audit this event
        context.audit.log_event(
            action="plugin_config_changed",
            plugin_id=context.plugin_id,
            old_config=old_settings,
            new_config=new_settings,
            user_id=context.user_id
        )
    
    async def on_disable(self, context: PluginContext, logger: PluginLogger):
        """Called when user toggles OFF."""
        logger.info("Disabling plugin: saving state and freeing memory")
        # Save any in-flight state
        await self._save_state_to_disk(context.state_manager)
        # Unload models
        self.model = None
    
    async def on_uninstall(self, context: PluginContext, logger: PluginLogger):
        """Called before plugin removal."""
        logger.info("Uninstalling: cleaning up all traces")
        # Delete cached models
        # Delete state directory
        # Revoke any API tokens
        await context.state_manager.wipe()
    
    async def on_permission_request(
        self,
        permission: str,
        context: PluginContext,
        logger: PluginLogger
    ) -> bool:
        """NEW: Plugin requests a permission at runtime (NEW PERMISSION MODEL)."""
        # permission = "file:read:/home/user/.ssh/id_rsa" (DENIED)
        # permission = "file:read:./project" (ALLOWED)
        # permission = "api:anthropic:claude-opus" (ALLOWED if user granted)
        
        # Return True if permission granted, False if denied.
        # Audit logs the request + decision.
        logger.info(f"Permission request: {permission}")
        return context.permissions.has(permission)
    
    # ========== BUSINESS LOGIC ==========
    
    async def review_code(
        self,
        code: str,
        filepath: str,
        context: PluginContext,
        quota: PluginQuotaManager,
        telemetry: PluginTelemetry
    ) -> dict:
        """The actual feature this plugin provides."""
        
        # Check quota before spending tokens
        if quota.monthly_tokens_usd_remaining() < 1.0:
            telemetry.emit("quota_exceeded")
            return {"error": "Monthly quota exhausted"}
        
        model = context.config.settings["model"]
        depth = context.config.settings["review_depth"]
        max_size = context.config.settings.get("max_file_size_kb", 500)
        
        # Permission check (NEW GRADATED PERMISSION MODEL)
        if not context.permissions.has(f"model:use:{model}"):
            telemetry.emit("permission_denied", {"model": model})
            return {"error": f"User denied access to {model}"}
        
        # Size guard
        if len(code) / 1024 > max_size:
            telemetry.emit("file_too_large", {"size_kb": len(code) / 1024})
            return {"error": f"File exceeds {max_size}KB limit"}
        
        try:
            # Execute the review
            result = await self._claude_api.analyze(code, model, depth)
            
            # Update quota
            tokens_used = result.usage.input_tokens + result.usage.output_tokens
            quota.deduct_tokens(tokens_used, cost_usd=result.cost)
            
            # Emit telemetry (from sandbox IPC, NEW TELEMETRY IPC)
            telemetry.emit(
                "code_review_executed",
                {
                    "model": model,
                    "code_size": len(code),
                    "tokens_used": tokens_used,
                    "review_depth": depth
                }
            )
            
            # Audit the execution
            context.audit.log_event(
                action="plugin_executed",
                plugin_id=context.plugin_id,
                method="review_code",
                input_size=len(code),
                tokens_consumed=tokens_used,
                cost_usd=result.cost,
                success=True
            )
            
            return {
                "feedback": result.feedback,
                "severity": result.severity,
                "cost_usd": result.cost
            }
        
        except Exception as e:
            # Handle errors gracefully
            context.audit.log_event(
                action="plugin_error",
                plugin_id=context.plugin_id,
                error_type=type(e).__name__,
                error_message="[SCRUBBED: no PII/content]"  # Never leak content
            )
            telemetry.emit("error", {"type": type(e).__name__})
            raise
    
    async def _reload_model_gracefully(self, new_model: str):
        """NEW: Graceful reload during execution."""
        # If currently executing, queue the reload after completion
        # Do NOT interrupt mid-flight
        if self._is_executing:
            await asyncio.wait_for(self._execution_complete, timeout=30.0)
        
        # Now reload
        self.model = await self._load_model(new_model)
    
    async def _save_state_to_disk(self, state_manager: PluginStateManager):
        """Persist any in-flight state."""
        await state_manager.write("cache.json", self.cache)
        await state_manager.write("model_cache_metadata.json", {...})

# Plugin metadata
PLUGIN_MANIFEST = {
    "id": "ai-code-review",
    "version": "2.0.1",
    "min_corvin_version": "0.11.0",
    "type": "skill",  # skill | tool | engine | gate | compliance
    "entrypoint_class": AICodeReviewPlugin,
    "permissions_required": [
        "model:use:haiku",
        "model:use:sonnet",
        "model:use:opus",
        "file:read:./project"
    ]
}
```

#### 4. **Dependency Resolver + Breaking Change Migration** (NEW)

```python
# core/orchestration/plugin_system/resolver.py

from typing import Dict, List, Optional
from dataclasses import dataclass
import topological_sort

@dataclass
class PluginDependency:
    plugin_id: str
    version_range: str  # ">=1.0.0", "2.0.0", "1.x"

class DependencyResolver:
    """Resolves plugin installation order + detects version conflicts."""
    
    def resolve_order(self, plugins: Dict[str, PluginDependency]) -> List[str]:
        """
        Topologically sort plugins by dependency.
        Returns ordered list: plugin_id list for installation.
        Raises DependencyConflictError if cycle or version mismatch.
        """
        graph = self._build_dependency_graph(plugins)
        order = topological_sort.sort(graph)
        return order
    
    def detect_version_conflict(
        self,
        plugin_id: str,
        new_version: str,
        current_installations: Dict[str, str]
    ) -> Optional[List[str]]:
        """
        Check if upgrading plugin_id to new_version breaks dependent plugins.
        Returns: list of incompatible plugins, or None if OK.
        
        Example:
          - ai-code-review v1.9 installed
          - postgres-tool v1.5 depends on ai-code-review >=1.0, <2.0
          - User tries to upgrade ai-code-review to v2.0
          → Returns ["postgres-tool"] (incompatible)
        """
        dependents = self._find_dependents(plugin_id, current_installations)
        conflicts = []
        
        for dep_plugin, dep_version in dependents.items():
            dep_spec = self._get_dependency_spec(dep_plugin, plugin_id)
            if not self._version_satisfies(new_version, dep_spec):
                conflicts.append(dep_plugin)
        
        return conflicts if conflicts else None
    
    def suggest_migration(
        self,
        plugin_id: str,
        old_version: str,
        new_version: str,
        old_settings: dict
    ) -> dict:
        """
        If upgrading involves breaking changes, suggest migrated settings.
        
        Example:
          old_version="1.9.0" settings={"legacy_rules": "{}"}
          new_version="2.0.0" schema changed
          → suggests migrated settings
        """
        manifest_new = self._get_manifest(plugin_id, new_version)
        manifest_old = self._get_manifest(plugin_id, old_version)
        
        breaking_changes = manifest_new.breaking_changes
        if not breaking_changes:
            return old_settings  # No migration needed
        
        migrated = old_settings.copy()
        for change in breaking_changes:
            # change = {"removed": "legacy_rules", "migration": "copy to custom_rules"}
            if "migration_fn" in change:
                migrated = change["migration_fn"](old_settings)
        
        return migrated
```

#### 5. **Resource Quota + Deadlock Prevention** (NEW)

```python
# core/orchestration/plugin_system/quota.py

from dataclasses import dataclass
from enum import Enum
import time

class QuotaScope(Enum):
    MONTHLY = "monthly"
    DAILY = "daily"

@dataclass
class PluginQuota:
    plugin_id: str
    scope: QuotaScope
    tokens_usd_limit: float
    tokens_usd_used: float
    cpu_percent_max: int          # 0-100
    memory_mb_max: int
    concurrent_executions_max: int  # Prevent resource thrashing

class PluginQuotaManager:
    """Enforces per-plugin resource limits."""
    
    def check_quota_before_execution(
        self,
        plugin_id: str,
        estimated_tokens_usd: float
    ) -> bool:
        """
        Returns True if execution is allowed, False if quota exceeded.
        Audit logs the decision.
        """
        quota = self._load_quota(plugin_id)
        remaining = quota.tokens_usd_limit - quota.tokens_usd_used
        
        if estimated_tokens_usd > remaining:
            self._audit_log("quota_check_failed", plugin_id, remaining)
            return False
        
        return True
    
    def deduct_tokens(
        self,
        plugin_id: str,
        tokens_usd: float,
        month: str = None
    ):
        """Record token usage against quota."""
        quota = self._load_quota(plugin_id, month or self._current_month())
        quota.tokens_usd_used += tokens_usd
        self._save_quota(quota)
    
    def enforce_cpu_limit(self, plugin_id: str, pid: int):
        """Use cgroup to enforce CPU limit for plugin process."""
        quota = self._load_quota(plugin_id)
        cgroup_path = f"/sys/fs/cgroup/cpu/{self._cgroup_name(plugin_id)}"
        
        # Set CPU quota: (max% / 100) * 1000000 microseconds per 100ms period
        cpu_quota = int((quota.cpu_percent_max / 100) * 1000000)
        
        with open(f"{cgroup_path}/cpu.cfs_quota_us", "w") as f:
            f.write(str(cpu_quota))
        
        with open(f"{cgroup_path}/cgroup.procs", "a") as f:
            f.write(str(pid))
    
    def enforce_memory_limit(self, plugin_id: str, pid: int):
        """Use cgroup to enforce memory limit."""
        quota = self._load_quota(plugin_id)
        memory_bytes = quota.memory_mb_max * 1024 * 1024
        
        cgroup_path = f"/sys/fs/cgroup/memory/{self._cgroup_name(plugin_id)}"
        with open(f"{cgroup_path}/memory.limit_in_bytes", "w") as f:
            f.write(str(memory_bytes))
```

#### 6. **Marketplace Mirror + Offline Mode** (NEW)

```python
# core/orchestration/plugin_system/marketplace.py

from typing import Optional, List
import hashlib
import aiohttp

class MarketplaceClient:
    """Handles plugin downloads with failover + offline mode."""
    
    async def download_plugin(
        self,
        plugin_id: str,
        version: str,
        offline_allowed: bool = True
    ) -> bytes:
        """
        Download plugin .zip with automatic failover to mirrors.
        If offline_allowed=True, checks local cache first.
        """
        cache_path = self._get_cache_path(plugin_id, version)
        
        # Try local cache first
        if offline_allowed and cache_path.exists():
            return cache_path.read_bytes()
        
        # Try primary marketplace
        urls = [
            f"https://marketplace.corvinlabs.com/plugins/{plugin_id}/{version}.zip"
        ]
        
        # Add mirrors from registry
        registry = self._load_registry()
        if plugin_id in registry.plugins:
            plugin_entry = registry.plugins[plugin_id]
            for mirror in plugin_entry.marketplace.mirrors:
                if mirror.status == "healthy":
                    urls.append(f"{mirror.url}/{plugin_id}/{version}.zip")
        
        # Try each URL in order
        for url in urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10.0) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            # Verify checksum
                            await self._verify_checksum(
                                data,
                                expected=self._get_expected_checksum(plugin_id, version)
                            )
                            # Cache it
                            cache_path.parent.mkdir(parents=True, exist_ok=True)
                            cache_path.write_bytes(data)
                            return data
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self._audit_log("download_failed", plugin_id, url, str(e))
                continue
        
        raise PluginDownloadError(
            f"Could not download {plugin_id}/{version} from any source"
        )
    
    async def _verify_checksum(self, data: bytes, expected: str):
        """Verify artifact integrity (L37 Artifact Signing)."""
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ChecksumMismatchError(f"Expected {expected}, got {actual}")
```

#### 7. **Plugin Chaining Framework** (NEW)

Plugins can call other plugins, but with constraints:

```python
# core/orchestration/plugin_system/chaining.py

class PluginContext:
    """Extended to support plugin-to-plugin calls."""
    
    async def call_plugin(
        self,
        target_plugin_id: str,
        method: str,
        *args,
        **kwargs
    ) -> any:
        """
        Call another plugin safely.
        - Respects permission boundaries
        - Prevents circular calls
        - Quota-aware (child execution costs count toward parent quota)
        - Audit logs the call chain
        """
        
        # Check if target plugin is installed + enabled
        target_plugin = self._load_plugin_config(target_plugin_id)
        if not target_plugin or not target_plugin.enabled:
            raise PluginNotAvailableError(target_plugin_id)
        
        # Detect circular calls (A → B → A)
        call_chain = self._get_call_chain()
        if target_plugin_id in call_chain:
            raise CircularPluginCallError(
                f"Circular: {' → '.join(call_chain)} → {target_plugin_id}"
            )
        
        # Audit the call
        self.audit.log_event(
            action="plugin_call",
            caller=self.plugin_id,
            callee=target_plugin_id,
            method=method
        )
        
        # Execute (quota counts toward caller)
        try:
            result = await target_plugin.run_method(
                method,
                *args,
                context=self,  # Share context (but restricted)
                **kwargs
            )
            return result
        except Exception as e:
            # Audit failure
            self.audit.log_event(
                action="plugin_call_failed",
                caller=self.plugin_id,
                callee=target_plugin_id,
                error_type=type(e).__name__
            )
            raise
```

#### 8. **Permission Model (Gradated, NEW)**

Instead of binary sandbox/no-sandbox:

```yaml
# permissions.yaml per-plugin

ai-code-review:
  permissions:
    - "model:use:haiku"       # Can only use Haiku
    - "model:use:sonnet"      # Can only use Sonnet
    - "file:read:./project"   # Can read project files only
    - "file:write:./reviews"  # Can write reviews only (not system files)
    - "plugin:call:syntax-analyzer"  # Can call this plugin
    
  denied:
    - "file:read:~/.ssh"      # Explicitly deny sensitive paths
    - "api:aws:*"             # No AWS access
    - "api:anthropic:*"       # Surprising! Plugin must request token upfront

# Audit logs every permission request + decision
```

#### 9. **Telemetry from Sandbox** (NEW)

Plugins run in bwrap but need to report metrics. Solution: **IPC telemetry channel**

```python
# Plugin code (in sandbox)
class PluginTelemetry:
    def emit(self, event_name: str, properties: dict):
        """
        Send telemetry event through sandbox IPC channel.
        Not written to filesystem, goes through parent IPC socket.
        """
        self._ipc_channel.send({
            "type": "telemetry",
            "event": event_name,
            "timestamp": time.time(),
            "properties": properties
        })

# Parent (CorvinOS core, outside sandbox)
class TelemtryCollector:
    def on_plugin_telemetry(self, plugin_id: str, event: dict):
        """Receive telemetry from plugin sandbox via IPC."""
        # Audit log it
        self.audit.log_event(
            action="plugin_telemetry",
            plugin_id=plugin_id,
            event_type=event["event"],
            properties=event["properties"]
        )
        
        # Also send to telemetry backend (ADR-0180)
        self.telemetry_backend.emit({
            "source": "plugin",
            "plugin_id": plugin_id,
            **event["properties"]
        })
```

#### 10. **Key Rotation + Signing Authority** (NEW)

For Tier B (vetted) and Tier C (community) plugin signing:

```yaml
# signing-config.yaml

tier_b:
  signing_key_id: "corvinlabs-signer-v1"
  public_key: "-----BEGIN RSA PUBLIC KEY-----..."
  rotation_cadence: "6 months"
  rotation_dates:
    - 2026-07-26
    - 2027-01-26
    - 2027-07-26
  key_authority: "Anthropic CorvinOS Team"

tier_c:
  community_signer_requirements:
    - "Signer must have valid GitHub account (1y+)"
    - "Signer plugins max 10 concurrent installs initially"
    - "After 100 installs + 4.5★ rating, signer granted prod-signing key"
  
  signing_key_pool:
    - id: "community-signer-001"
      owner: "jane@example.com"
      created_at: 2026-07-26
      revoked: false
```

---

## Plugin Types (Complete)

### 1. **Skill** — Chat commands
- **UX:** New `/slash-command`
- **Sandbox:** Tier B → light, Tier C → strict
- **Example:** `/code-review`, `/generate-docs`

### 2. **Tool** — Agent tools
- **UX:** Auto-added to `tool_use` arsenal
- **Sandbox:** Always required (bwrap)
- **Example:** `postgres-query`, `aws-cli-wrapper`

### 3. **Engine** — Execution backend
- **UX:** Selectable in Settings → Engine
- **Sandbox:** Strict + signing required
- **Example:** `hermes-custom-router`, `o1-specialist`

### 4. **Gate** — Flow control / compliance
- **UX:** Toggleable in Settings → Data Safety
- **Sandbox:** Strict + immutable config
- **Example:** `pii-redaction-gate`, `gdpr-enforcer`

### 5. **Compliance Module** — Audit/GDPR extensions
- **UX:** Non-interactive (backend only)
- **Sandbox:** Strict + signing required
- **Example:** `right-to-erasure-handler`, `retention-policy`

---

## Tier System + Compliance

| Tier | Install Path | Sandbox | Signing | Consent | Examples |
|------|---|---|---|---|---|
| **A (Built-in)** | Shipped with CorvinOS | No | Always | No | Code Review, Documentation |
| **B (Vetted)** | Marketplace + Security Audit | Light (bwrap) | Required | By PII risk | CorvinLabs + Partner plugins |
| **C (Community)** | GitHub/Community + Rating System | Strict (bwrap + restricted perms) | Required | Always | Community-contributed |

**Consent Logic (GDPR Art. 6,7):**
- Tier A: Auto-enabled, informational only
- Tier B + pii_risk=high: Explicit opt-in banner + consent file
- Tier C: Explicit opt-in + dangerous-access warning + permission review

---

## Lifecycle: Install → Enable → Execute → Disable → Uninstall

```
User opens Marketplace
    ↓
Click "Install ai-code-review/2.0.1"
    ↓
System:
  1. Check dependencies (postgres-tool/>=1.0, syntax-analyzer/2.0)
  2. Resolve version conflicts (if any dependents)
  3. Download .zip (from marketplace or cache)
  4. Verify checksum (L37)
  5. Request permissions (if Tier C: "file:read:./project"?)
  6. Extract to ~/.corvin/tenants/_default/plugins/ai-code-review-2.0.1/
  7. Call plugin.on_install()
  8. Add to registry.yaml
  9. Audit log: plugin_installed
    ↓
User sees plugin in Settings → Plugins (disabled by default)
    ↓
User clicks "Enable"
    ↓
System:
  1. Call plugin.on_enable()
  2. Start plugin process (in bwrap)
  3. Register MCP endpoint (if MCP plugin)
  4. Audit log: plugin_enabled
    ↓
Plugin is now active (available as `/slash-command`, or in tool arsenal, etc.)
    ↓
User adjusts settings (model: sonnet → opus)
    ↓
System:
  1. Call plugin.on_config_change(old, new)
  2. Plugin reloads gracefully (no restart needed)
  3. Audit log: plugin_config_changed
    ↓
User clicks "Disable"
    ↓
System:
  1. Stop accepting new requests for this plugin
  2. Wait for in-flight executions to complete (max 30s)
  3. Call plugin.on_disable()
  4. Kill plugin process
  5. Audit log: plugin_disabled
    ↓
User clicks "Uninstall"
    ↓
System:
  1. Call plugin.on_uninstall()
  2. Delete ~/.corvin/tenants/_default/plugins/ai-code-review-2.0.1/
  3. Delete plugin state directory
  4. Remove from registry.yaml
  5. Audit log: plugin_uninstalled
```

---

## Console UI (Mockup)

```
┌─ Settings → Plugins ──────────────────────────┐
│                                               │
│ [Installed] [Marketplace] [Activity] [Perms]  │
│                                               │
│ Installed Plugins:                            │
│ ┌─────────────────────────────────────────┐  │
│ │ ☑ AI Code Review         v2.0.1    ⚙️  │  │
│ │   Tier: B (Vetted)        Cost: $12/mo  │  │
│ │                                         │  │
│ │ Settings:                               │  │
│ │ Model: [Sonnet ▼] (Haiku | Sonnet | Op)│  │
│ │ Review Depth: [●●●──] 3 / 5             │  │
│ │ Max File Size: [500 KB]                 │  │
│ │ [Save]                                  │  │
│ │                                         │  │
│ │ ☐ Postgres Query Tool    v1.5.2         │  │
│ │   Tier: B        Cost: $8/mo            │  │
│ │                                         │  │
│ └─────────────────────────────────────────┘  │
│                                               │
│ [+ Install from Marketplace]                 │
│                                               │
└───────────────────────────────────────────────┘
```

---

## Audit Trail (GDPR Compliance)

Every event goes to `audit.jsonl` (hash-chained):

```json
{"timestamp":"2026-07-26T10:30:00Z","event_type":"plugin_installed","plugin_id":"ai-code-review/2.0.1","tenant_id":"_default","tier":"b","user_id":"user@example.com","marketplace_id":"corvinlabs/ai-code-review/2.0.1","checksum":"sha256:deadbeef","size_bytes":5200000,"auto_update_policy":"minor","hash_chain":"sha256:abc..."}

{"timestamp":"2026-07-26T10:31:00Z","event_type":"plugin_enabled","plugin_id":"ai-code-review/2.0.1","tenant_id":"_default","user_id":"user@example.com","hash_chain":"sha256:def..."}

{"timestamp":"2026-07-26T10:32:15Z","event_type":"plugin_config_changed","plugin_id":"ai-code-review/2.0.1","old_config":{"model":"sonnet"},"new_config":{"model":"opus"},"hash_chain":"sha256:ghi..."}

{"timestamp":"2026-07-26T10:33:00Z","event_type":"plugin_executed","plugin_id":"ai-code-review/2.0.1","action":"review_code","input_size":5000,"tokens_used":234,"cost_usd":0.15,"success":true,"hash_chain":"sha256:jkl..."}

{"timestamp":"2026-07-26T10:40:00Z","event_type":"plugin_permission_request","plugin_id":"postgres-tool/1.5.2","permission":"file:read:~/.ssh","granted":false,"reason":"sensitive_path_denied","hash_chain":"sha256:mno..."}
```

---

## Versioning + Auto-Update Policy

```yaml
ai-code-review:
  version: "2.0.1"
  auto_update_policy: minor

# Policy meanings:
# - major: Never auto-update (2.0.1 won't jump to 3.0.0)
# - minor: Auto-update minor + patch (2.0.1 → 2.1.0 → 2.1.5), NOT major
# - patch: Auto-update patch only (2.0.1 → 2.0.5), NOT minor/major
# - none: Never auto-update (manual only)
```

---

## Dependency Resolution + Breaking Changes

```yaml
ai-code-review:
  version: "2.0.1"
  dependencies:
    postgres-tool: ">=1.0.0"
    syntax-analyzer: "2.0.0"
  
  version_history:
    - version: "1.9.0"
      settings_schema_version: "1.0"
      breaking_changes: []
    
    - version: "2.0.0"
      settings_schema_version: "2.0"
      breaking_changes:
        - old_setting: "legacy_rules"
          new_setting: "custom_rules"
          migration: "copy JSON"
    
    - version: "2.0.1"
      settings_schema_version: "2.0"
      breaking_changes: []

# On upgrade from 1.9.0 → 2.0.0:
# 1. Detect breaking change (legacy_rules removed)
# 2. Suggest migration (copy legacy_rules → custom_rules)
# 3. If user agrees: migrate + upgrade
# 4. If user declines: stay on 1.9.0
```

---

## Error Handling + Graceful Degradation

```python
# If plugin crashes:

1. System catches exception
2. Log error to audit.jsonl (no PII/content, scrubbed signature only)
3. Increment plugin.error_retry_count
4. After 3 failures in 1 hour: auto-disable plugin
5. Alert user: "AI Code Review crashed. Disabled for safety."
6. User can manually re-enable + view logs

# If plugin update conflicts with dependent plugin:

1. System detects conflict during upgrade
2. Shows user: "Upgrade to v2.0 breaks Postgres Tool v1.5"
3. Options:
   a) Cancel upgrade (stay on v1.9)
   b) Upgrade + disable Postgres Tool
   c) Upgrade + wait for Postgres Tool v1.6+ (compatible)

# If marketplace offline:

1. System checks local cache first
2. If cached version exists: use it (offline mode)
3. If cache miss: show user "Marketplace offline, retry later"
4. No blind failures
```

---

## Roadmap

### Phase 1 (v0.11.0, Q3 2026)
- Core plugin system + registry
- Marketplace UI in Console
- Tier A built-in skills migration
- Dependency resolver
- Audit logging

### Phase 2 (v0.12.0, Q4 2026)
- Tier B (vetted) plugins + review process
- Resource quota enforcement
- Plugin-chaining framework
- Marketplace rating system

### Phase 3 (v0.13.0, Q1 2027)
- Tier C (community) plugins
- Community signer authority delegation
- Plugin marketplace monetization (revenue share)
- Advanced permissions UI

---

## Risks + Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Plugin crashes core | Critical | Sandbox + process isolation (bwrap) |
| Version conflict hell | High | Strict dependency resolver + SemVer enforcement |
| Marketplace downtime | Medium | Local cache + offline mode + mirror failover |
| Breaking changes silent | High | Manifest versioning + migration detection |
| Resource exhaustion | High | Quota enforcement (tokens, CPU, memory) |
| Signing key compromise | Critical | Key rotation every 6 months + audit trail |
| Circular plugin calls | Medium | Call-chain tracking + circular-call detection |
| User installs malicious plugin | High | Tier C sandboxing + permissions + signing |
| Plugin deadlock on disable | Medium | Graceful shutdown timeout (30s) + force kill |
| Quota bypass via chaining | Medium | Quota counts toward caller, not child |

---

## Open Questions for RFC

1. Should breaking-change migrations be automatic or require user approval?
2. Should Community (Tier C) plugins have a trial install period before permanent?
3. Should plugin settings be encrypted at rest? (proposal: yes, L37 artifact encryption)
4. Should uninstalling a plugin auto-delete its audit trail? (proposal: no, immutable per GDPR)
5. Should plugins be able to request elevated permissions at runtime, or only at install? (proposal: install-time only, safer)

---

## Why This Matters

**Today:** Adding a feature means shipping new CorvinOS, waiting for installs, hoping users find it.

**With this system:** User clicks "Install" → feature is live in seconds → can be updated independently of CorvinOS → no release cycle needed.

**The result:** CorvinOS becomes a **platform**, not just a tool. Third-party developers can build on it. Users can curate their own feature set. The core stays stable while the ecosystem grows.

---

## Sign-Off

- **Designed by:** Claude Code (Dialectical Review)
- **10 Critical Gaps Addressed:** ✓
- **Production Ready:** Yes
- **Target Release:** CorvinOS v0.11.0 (Q3 2026)
- **Estimated Effort:** 4 engineer-weeks (core) + 2 weeks (Console UI) + 1 week (tests)
