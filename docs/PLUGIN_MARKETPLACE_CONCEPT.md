# Corvin Plugin Marketplace — Concept Document

**Version:** 0.1-draft  
**Status:** Design Phase (Pre-ADR)  
**Date:** 2026-08-27  
**Owner:** shumway  

---

## Executive Summary

Enable Corvin Console users to **discover, install, and manage plugins directly from GitHub** via a new "Plugins" section under Vibe Engineering. Plugins appear as **auto-registered Settings Panels** in Console with zero manual wiring. Installation is engine-based for resilience and consistency.

**User Flow (Happy Path):**
```
Console → Vibe Engineering → Plugins
  ↓
[Browse Marketplace] [Search GitHub]
  ↓
[Select plugin] [Install via Engine]
  ↓
[Automatic Panel Registration] [Visible in Console]
```

**Goals:**
- ✅ Easy install (one-click from Console)
- ✅ GitHub-native (plugins = public repos)
- ✅ Resilient (engine-based, not direct API calls)
- ✅ Auto-discover (plugins auto-register Settings Panels)
- ✅ Manageable (operator controls installed plugins)

---

## Architecture Overview

### 1. **Component Stack**

```
┌─────────────────────────────────────────────────────┐
│ Console UI (React)                                  │
│ • Plugins Section (Vibe Engineering)                │
│ • Browse/Search/Install UX                          │
│ • Installed Plugins List + Status                   │
└─────────────────────────────────────────────────────┘
           ↓ (HTTP API)
┌─────────────────────────────────────────────────────┐
│ Console Backend (Flask)                             │
│ • Plugin API Routes                                 │
│ • Manifest validation                               │
│ • Storage management                                │
└─────────────────────────────────────────────────────┘
           ↓ (Task Envelope)
┌─────────────────────────────────────────────────────┐
│ Plugin Installation Engine (Brain Task)             │
│ • GitHub clone → ~/.corvin/plugins/                 │
│ • Manifest validation + signing                     │
│ • Panel registration                                │
│ • Error handling & rollback                         │
└─────────────────────────────────────────────────────┘
           ↓ (File System)
┌─────────────────────────────────────────────────────┐
│ Local Plugin Storage                                │
│ ~/.corvin/plugins/                                  │
│ ├── plugin-id/                                      │
│ │   ├── plugin.yaml (manifest)                      │
│ │   ├── ui/                                         │
│ │   ├── skill/                                      │
│ │   └── docs/                                       │
│ └── registry.json (installed plugins index)         │
└─────────────────────────────────────────────────────┘
```

### 2. **Plugin Manifest Format** (v2.0)

```yaml
# ~/.corvin/plugins/my-plugin/plugin.yaml

plugin:
  id: my-plugin
  name: "My Awesome Plugin"
  version: "1.0.0"
  description: "Does X, Y, Z"
  author: "github/user"
  
  # GitHub source (immutable)
  source:
    repo: "https://github.com/user/my-plugin"
    branch: "main"
    commit_hash: "abc123..."  # pinned at install time
  
  # Console integration
  console:
    # Auto-register Settings Panel
    settings_panel:
      title: "My Plugin Settings"
      description: "Configure my plugin"
      icon: "settings"  # or URL
    
    # Skill2.0 for agent control
    skill:
      id: "my-plugin-v1"
      name: "My Plugin"
      description: "Use my plugin"
      body_md: "# My Plugin\n..."
  
  # Control schema (from ADR-0360 design)
  control:
    config_schema:
      api_key: {type: string, required: true, masked: true}
      enabled: {type: boolean, default: true}
    
    lifecycle:
      init: "validate_config()"
      shutdown: "cleanup()"
      health_check: "ping()"
    
    events:
      - name: "plugin_started"
        schema: {timestamp: int}
      - name: "plugin_error"
        schema: {error_msg: string}
  
  # Installation constraints
  requirements:
    corvin_version: ">=0.10.0"
    python_version: ">=3.9"
    disk_space_mb: 100
  
  # Permissions required
  permissions:
    audit_chain: write
    config: read_write
    filesystem: read_write
    network: "https://api.example.com"
  
  # Pricing/License
  license: "MIT"
  pricing: "free"  # or "paid" with payment_url
```

### 3. **GitHub Integration**

**Plugin Discovery:**
- Plugin repos are **tagged with `corvin-plugin` topic** on GitHub
- GitHub Search API: `topic:corvin-plugin`
- Marketplace index is **cached locally** (updated daily or on-demand)

**Installation Flow:**
```
User clicks "Install"
  ↓
Backend validates GitHub repo
  ↓
Backend creates PluginInstallTask → Brain Engine
  ↓
Engine: git clone + manifest validation + panel registration
  ↓
Engine: report status back to Console
  ↓
Console refreshes plugin list (auto-update UI)
```

### 4. **Panel Auto-Registration**

When plugin is installed, Console **automatically discovers and registers** the Settings Panel:

```python
# Console Backend: on_plugin_installed(plugin_id)

def register_plugin_panels(plugin_id: str):
    """Auto-discover and register Settings Panels from plugin manifest"""
    manifest = load_plugin_manifest(plugin_id)
    
    if manifest.get("console", {}).get("settings_panel"):
        panel_spec = manifest["console"]["settings_panel"]
        
        # Register in Console's PANELS registry
        PANELS.register(
            id=f"plugin-{plugin_id}",
            component=f"@/plugins/PluginSettingsPanel",
            nav_label=panel_spec.get("title"),
            icon=panel_spec.get("icon"),
            props={"plugin_id": plugin_id}
        )
        
        # Also register in NAV_GROUPS so it appears in sidebar
        NAV_GROUPS.add_entry(
            group="vibe-engineering",
            label=panel_spec.get("title"),
            panel_id=f"plugin-{plugin_id}"
        )
```

**Result:** User sees plugin Settings Panel in Console UI without any manual setup.

### 5. **Storage & Isolation**

```
~/.corvin/
├── plugins/
│   ├── plugin-1/
│   │   ├── plugin.yaml
│   │   ├── ui/
│   │   │   └── index.tsx (React component for Settings Panel)
│   │   ├── skill/
│   │   │   └── skill.md (skill2.0 body)
│   │   └── .git/ (minimal clone)
│   │
│   ├── plugin-2/
│   │   └── ...
│   │
│   └── registry.json
│       {
│         "version": "1.0",
│         "installed": [
│           {"id": "plugin-1", "version": "1.0.0", "installed_at": "2026-08-27"},
│           {"id": "plugin-2", "version": "2.1.0", "installed_at": "2026-08-27"}
│         ]
│       }

├── audit.jsonl
│   # All plugin installs/uninstalls logged
│   {"event": "plugin_installed", "plugin_id": "...", "version": "..."}
│   {"event": "plugin_uninstalled", "plugin_id": "..."}
```

**Isolation:**
- Each plugin in own directory
- Plugin can read its own config, cannot read others'
- Plugins executed in Engine (subprocess/container)
- No cross-plugin imports (unless explicit dependency)

---

## User Flows

### Flow 1: Browse & Install

```
1. User: Open Console → Vibe Engineering → Plugins
2. UI: Shows "Installed" tab + "Marketplace" tab
3. User: Click "Marketplace" tab
4. UI: Fetches from GitHub API (topic:corvin-plugin)
5. UI: Shows list [Install buttons]
6. User: Clicks "Install" on "awesome-plugin"
7. UI: Shows "Installing..." + progress
8. Backend: POST /api/v1/plugins/install {"repo": "user/awesome-plugin"}
9. Backend: Creates PluginInstallTask → Brain Engine
10. Engine: Clones repo, validates manifest, registers panel
11. Engine: Returns success/failure
12. Console: Refreshes plugin list + shows panel
13. User: Sees new "Awesome Plugin Settings" panel in sidebar
```

### Flow 2: Configure Plugin

```
1. User: Console → Vibe Engineering → Plugins → [Installed Plugin]
2. UI: Loads Settings Panel from plugin.yaml
3. UI: Renders config form (API key, enabled toggle, etc.)
4. User: Changes config
5. UI: POSTs /api/v1/plugins/{id}/config
6. Backend: Validates against plugin.yaml schema
7. Backend: Saves to ~/.corvin/plugins/{id}/config.json
8. Backend: Logs config change to audit.jsonl
9. UI: Shows "Saved" confirmation
10. Engine: Health check (optional, async)
```

### Flow 3: Uninstall & Cleanup

```
1. User: Console → Vibe Engineering → Plugins → [Installed Plugin] → Delete
2. UI: Shows confirmation "Uninstall awesome-plugin?"
3. User: Confirms
4. Backend: POST /api/v1/plugins/uninstall {"id": "awesome-plugin"}
5. Backend: Creates PluginUninstallTask → Brain Engine
6. Engine: Calls plugin.control.lifecycle.shutdown()
7. Engine: Removes ~/.corvin/plugins/awesome-plugin/
8. Engine: Unregisters Settings Panel
9. Backend: Removes entry from registry.json
10. Console: Refreshes UI, panel disappears
```

---

## Error Handling & Resilience

### Installation Failures

| Scenario | Handling |
|---|---|
| GitHub API rate-limit | Fallback to cached manifest (if available) |
| Repo not found | User-friendly error + suggestion to check GitHub URL |
| Manifest validation fails | Rollback: don't install, show validation errors |
| Panel registration fails | Install succeeds, plugin is disabled with warning |
| Disk full | Engine detects, aborts before write, shows error |
| Network timeout | Retry 3x with exponential backoff |

### Audit Trail

Every plugin operation is logged:

```jsonl
{"event": "plugin_install_initiated", "plugin_id": "awesome-plugin", "repo": "user/awesome-plugin", "timestamp": 1692874523}
{"event": "plugin_install_success", "plugin_id": "awesome-plugin", "version": "1.0.0", "manifest_hash": "abc123", "timestamp": 1692874535}
{"event": "plugin_config_changed", "plugin_id": "awesome-plugin", "key": "api_key", "old_hash": "xyz", "new_hash": "uvw", "timestamp": 1692874600}
{"event": "plugin_uninstall_success", "plugin_id": "awesome-plugin", "timestamp": 1692874700}
```

---

## Constraints & Assumptions

### Constraints
1. **Easy install:** User should install a plugin in <5 clicks
2. **Engine-based:** All installations go through Brain Engine (no direct API calls from UI)
3. **Resilience:** Failed installations must not break Console
4. **Audit:** Every plugin operation must be logged (GDPR Art. 30)
5. **Isolation:** Plugins cannot affect each other or Core
6. **GitHub-native:** Plugin repos are public GitHub repos (topic:corvin-plugin)

### Assumptions
1. Plugins are **public GitHub repos** (no auth needed for read)
2. Plugin authors **follow manifest.yaml convention**
3. Console has **network access** to GitHub (cached fallback OK)
4. Operator has **write access** to ~/.corvin/plugins/ directory
5. Brain Engine is **available** for async installation tasks

---

## Security Considerations

### Threat Model

| Threat | Mitigation |
|---|---|
| Malicious plugin code | Code review (future: signed manifests + code scanning) |
| Plugin reads another plugin's config | Filesystem isolation + OS permissions |
| Plugin exhausts resources | CPU/memory limits in Engine |
| GitHub account compromise | Operator should use GitHub tokens with plugin-read scope only |
| Man-in-the-middle on GitHub clone | Use HTTPS + verify commit hash in manifest |

### Compliance

- **GDPR Art. 5:** Plugin installs logged with timestamp + user + action
- **GDPR Art. 30:** Audit trail in ~/.corvin/audit.jsonl
- **EU AI Act:** Plugins must declare their control schema (what they do, how they're controlled)

---

## Success Criteria

- [ ] User can install plugin in <5 clicks from Console
- [ ] Installed plugin Settings Panel appears automatically
- [ ] Failed installations don't break Console
- [ ] All plugin operations logged to audit.jsonl
- [ ] Plugin isolation verified (one plugin cannot read another's config)
- [ ] 10+ plugins available on GitHub with `corvin-plugin` topic
- [ ] UX is intuitive (no CLI, no manual file editing)

---

## Related Documents

- **ADR-0360:** Plugin Control Schema (skill2.0 + lifecycle)
- **ADR-0236:** Plugin Registry & Boot Layers
- **ADR-0146:** Console Panel Architecture
- **Plugin Marketplace Roadmap:** (separate doc)

---

**Next:** ADR Suite (Installation, Storage, Updates, Console-Integration)
