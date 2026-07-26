# CorvinOS Plugin Architecture (Concept)

**Status:** Design Document (RFC)  
**Author:** Claude Code  
**Target:** L7+ Extension Layer (ADR-0XXX)

## Vision

CorvinOS wird von einem **starren Feature-Set** zu einer **Plattform mit erweiterbarem Plugin-System** transformiert. Neue Features (Tools, Skills, Engines, Compliance-Module) werden:

1. **Registriert** über eine einheitliche Plugin-Registry-Schnittstelle
2. **Konfiguriert** im Console Settings Panel mit dynamischen UI-Schemata
3. **Parametrisiert** mit Typing + Validierung (Settings ↔ Plugin)
4. **Versioniert** über Marketplace-Releases
5. **Isoliert** in ihrer eigenen Sandbox (Forge-bwrap-ähnlich, aber für Features)
6. **Audited** — jede Plugin-Aktivität wird in die GDPR-Audit-Chain geloggt

---

## Architecture

### 1. Plugin-Registry (Core Layer)

```
.corvin/tenants/_default/
├── plugins/
│   ├── registry.yaml              # Master registry (all installed plugins)
│   ├── schemas/                   # JSON Schema per plugin (settings validation)
│   │   ├── ai-code-review.json
│   │   ├── custom-tool-suite.json
│   │   └── ...
│   ├── instances/                 # Active plugin instances + state
│   │   ├── ai-code-review/
│   │   │   ├── config.json        # User-configured settings
│   │   │   ├── state.jsonl        # Plugin-internal state (audit-chained)
│   │   │   └── metadata.json      # Version, enabled, last-loaded, etc
│   │   └── ...
│   └── cache/                     # Transient: plugin outputs, compiled state
```

### 2. Registry Entry Schema

```yaml
# .corvin/tenants/_default/plugins/registry.yaml
plugins:
  - name: ai-code-review
    version: "2.0.1"
    type: skill                    # skill | tool | engine | gate | compliance-module
    provider: CorvinLabs
    marketplace_id: "corvinlabs/ai-code-review/2.0.1"
    
    # Schema reference (where user-editable params live)
    settings_schema_ref: "schemas/ai-code-review.json"
    
    # Lifecycle
    enabled: true
    auto_update: false             # Marketplace autoupdate policy
    installed_at: "2026-07-26T10:30:00Z"
    updated_at: "2026-07-26T10:30:00Z"
    
    # Compliance metadata (load-bearing for ADR-0XXX)
    compliance:
      tier: "tier-b"               # Tier A (built-in) | B (vetted) | C (community)
      requires_review: false       # Needs security review before load?
      sandbox_mode: true           # Run in bwrap (Forge-like)
      pii_risk: "medium"          # none | low | medium | high
      requires_consent: false      # GDPR: ask user before activation?
    
    # Entrypoint (where the plugin lives)
    entrypoint:
      type: "mcp"                  # mcp | forge | skill-forge | builtin
      module_path: "plugins/ai-code-review/__init__.py"  # relative to ~/.corvin/
      # or for MCP: mcp_server_url: "localhost:9999"
```

### 3. Settings Schema (JSON Schema + UI Hints)

```json
{
  "$schema": "http://json-schema.org/draft-7/schema",
  "title": "AI Code Review Settings",
  "type": "object",
  "properties": {
    "model_selection": {
      "type": "string",
      "enum": ["haiku", "sonnet", "opus"],
      "default": "sonnet",
      "title": "Model for Review",
      "description": "Which Claude model to use for code analysis",
      "ui": {
        "component": "select",
        "category": "Model"
      }
    },
    "review_depth": {
      "type": "number",
      "minimum": 1,
      "maximum": 5,
      "default": 3,
      "title": "Review Depth",
      "description": "1=shallow (style), 5=deep (architecture + security)",
      "ui": {
        "component": "slider",
        "category": "Behavior"
      }
    },
    "auto_run_on_pr": {
      "type": "boolean",
      "default": false,
      "title": "Auto-trigger on PRs",
      "description": "Run review automatically on pull request creation",
      "ui": {
        "component": "toggle",
        "category": "Automation",
        "warning": "Increases API costs"
      }
    },
    "custom_rules_json": {
      "type": "string",
      "default": "{}",
      "title": "Custom Review Rules",
      "description": "JSON string with custom linting/style rules",
      "ui": {
        "component": "textarea",
        "monospace": true,
        "category": "Advanced"
      }
    }
  },
  "required": ["model_selection"],
  "additionalProperties": false
}
```

### 4. Console Settings Panel (UI Wiring)

```typescript
// core/console/corvin_console/web-next/src/pages/plugins.tsx

interface PluginTab {
  name: string
  version: string
  type: "skill" | "tool" | "engine" | "gate" | "compliance-module"
  enabled: boolean
  tier: "a" | "b" | "c"
  settingsSchema: JSONSchema7
  currentConfig: Record<string, unknown>
  
  // Dynamic UI rendering per setting
  settingsUI: Array<{
    key: string
    component: "text" | "textarea" | "select" | "toggle" | "slider" | "password"
    label: string
    description: string
    placeholder?: string
    validation?: {
      pattern?: string
      minLength?: number
      maxLength?: number
    }
  }>
}

export function PluginsSettingsPanel() {
  const [plugins, setPlugins] = useState<PluginTab[]>([])
  const [selectedPlugin, setSelectedPlugin] = useState<PluginTab | null>(null)
  
  // Tab: Installed Plugins (list view)
  // Tab: Marketplace (browse & install)
  // Tab: Plugin Settings (per-plugin config)
  // Tab: Activity Log (audit trail for plugin operations)
}
```

### 5. Plugin Lifecycle & Hooks

```python
# plugins/ai-code-review/__init__.py (or MCP server)

class AICodeReviewPlugin:
    """Entrypoint for plugin system."""
    
    # Lifecycle hooks (all optional)
    async def on_install(self, config: dict, logger) -> None:
        """Run once on plugin installation."""
        logger.info(f"Installing ai-code-review with config: {config}")
        # Download model, validate credentials, etc
    
    async def on_enable(self, config: dict, logger) -> None:
        """Run when plugin is toggled ON."""
        # Load models into memory, warm caches
    
    async def on_disable(self, logger) -> None:
        """Run when plugin is toggled OFF."""
        # Unload models, cleanup resources
    
    async def on_config_change(self, old_config: dict, new_config: dict, logger) -> None:
        """Run when settings change (e.g., model_selection haiku→sonnet)."""
        # Reload with new config
    
    async def on_uninstall(self, logger) -> None:
        """Run before plugin removal."""
        # Cleanup, remove artifacts
    
    # Feature implementation
    async def review_code(self, code: str, filepath: str, config: dict) -> dict:
        """The actual feature the plugin provides."""
        # Implementation
        return {
            "feedback": [...],
            "severity": "high",
            "usage": {"tokens": 1234}
        }

# Register plugin with system
PLUGIN_METADATA = {
    "id": "ai-code-review",
    "version": "2.0.1",
    "type": "skill",
    "entrypoint_class": AICodeReviewPlugin,
}
```

### 6. Marketplace Integration

```yaml
# Marketplace Catalog (CorvinLabs/Corvin-Marketplace)
# → corvin-marketplace/ai-code-review/manifest.yml

name: AI Code Review
id: corvinlabs/ai-code-review
version: "2.0.1"
author: CorvinLabs
license: Apache-2.0

description: |
  Professional code review with multiple Claude models.
  Supports custom linting rules, auto-trigger on PRs.

categories:
  - code-quality
  - automation
  - development-tools

tier: b                    # Vetted by CorvinLabs

requirements:
  corvin_version: ">=0.10.60"
  python_version: ">=3.11"
  api_keys:
    - claude_api_key       # Plugin declares what it needs

dependencies:
  - tool: some-analyzer   # Can depend on other plugins/tools

pii_handling: "none"       # none | low | medium | high

pricing:
  model: "free"            # free | per-usage | subscription
  # per-usage: cost per review, billed to user's account
  # subscription: monthly tier

settings_schema: "settings-schema.json"  # Included in package

installation:
  url: "https://marketplace.corvinlabs.com/corvinlabs/ai-code-review/2.0.1"
  checksum: "sha256:deadbeef..."
  size_mb: 5.2
```

### 7. Console Install Flow

```
User Flow:
1. Settings → Plugins → Marketplace tab
2. Browse/search: "code review"
3. Click "ai-code-review" card
4. Review: version, tier, pricing, PII handling, author
5. Click "Install"
   → Download from marketplace
   → Verify checksum (L37 artifact signing)
   → Run on_install() hook
   → Add to registry.yaml
6. Plugin appears in "Installed" tab
7. User toggles "Enabled" + configures settings
8. Settings form dynamically generated from schema
9. Click "Save"
   → Validate against schema
   → Call on_config_change()
   → Persist to instances/{name}/config.json
   → Audit log: plugin_configured event
```

---

## Security & Compliance (Load-Bearing)

### Compliance Baseline

| Aspect | Rule | Ref |
|--------|------|-----|
| **Disclosure** | Each plugin shows its tier, PII handling, author in UI | GDPR Art. 50 + EU AI Act |
| **Consent** | High-risk plugins (tier C, pii_risk=high) require explicit opt-in | GDPR Art. 6, 7 |
| **Audit** | Every plugin operation (install, enable, disable, config change, error) logged to audit.jsonl | GDPR Art. 30, 32 |
| **Sandbox** | Tier B/C plugins run in bwrap (resource isolation, syscall filtering) | ADR-0XXX (new layer) |
| **Integrity** | Plugin binaries signed + checksummed (L37 artifact signing) | ADR-0141 LIP |
| **Licensing** | Plugin must declare license; CLA required for CorvinLabs-published plugins | Apache 2.0 + CLA v3.1 |
| **Right to Erasure** | GDPR Art. 17: plugin can request user delete its stored data | L36 Erasure Orchestrator |

### Audit Trail (GDPR Art. 30)

```json
// .corvin/audit.jsonl (hash-chained)
{
  "event_type": "plugin_installed",
  "timestamp": "2026-07-26T10:30:00Z",
  "plugin_id": "ai-code-review",
  "version": "2.0.1",
  "tier": "b",
  "user_id": "user123",
  "tenant_id": "_default",
  "pii_risk": "medium",
  "hash_chain": "sha256:prev_hash^this_record_hash",
  "details": {
    "source": "marketplace",
    "marketplace_id": "corvinlabs/ai-code-review/2.0.1",
    "file_size_mb": 5.2,
    "checksum": "sha256:deadbeef"
  }
}

{
  "event_type": "plugin_config_changed",
  "timestamp": "2026-07-26T10:35:00Z",
  "plugin_id": "ai-code-review",
  "user_id": "user123",
  "tenant_id": "_default",
  "old_config": { "model_selection": "sonnet", "review_depth": 3 },
  "new_config": { "model_selection": "opus", "review_depth": 5 },
  "hash_chain": "sha256:prev_hash^this_record_hash"
}

{
  "event_type": "plugin_error",
  "timestamp": "2026-07-26T10:40:15Z",
  "plugin_id": "ai-code-review",
  "user_id": "user123",
  "tenant_id": "_default",
  "error_type": "api_quota_exceeded",
  "error_message": "[SCRUBBED for PII]",  # Never log user data/code
  "recovery_action": "degraded_to_local_linting",
  "hash_chain": "sha256:prev_hash^this_record_hash"
}
```

---

## Plugin Types

### 1. **Skill** (L7 Integration)
- Entrypoint: MCP server or Python class
- Capabilities: adds `/slash-command`, chat instructions
- Examples: `ai-code-review`, `documentation-generator`
- Sandbox: Optional (bwrap for tier C)

### 2. **Tool** (L6 Integration)
- Entrypoint: Python function or MCP tool
- Capabilities: adds `tool_use` to agent arsenal
- Examples: `postgres-query-builder`, `aws-cli-wrapper`
- Sandbox: Required (Forge-bwrap)

### 3. **Engine** (L22 Integration)
- Entrypoint: `BaseEngine` subclass
- Capabilities: registers as a new execution engine
- Examples: `hermes-custom`, `specialist-model-selector`
- Sandbox: Required + strict signing

### 4. **Gate** (L34 Integration)
- Entrypoint: `BaseGate` subclass
- Capabilities: adds data-flow validation rule
- Examples: `pii-redaction-gate`, `custom-compliance-gate`
- Sandbox: Required + immutable config

### 5. **Compliance Module** (L16+ Integration)
- Entrypoint: `ComplianceHandler` subclass
- Capabilities: adds audit, consent, retention logic
- Examples: `gdpr-right-to-erasure`, `custom-retention-policy`
- Sandbox: Required + cryptographic signing

---

## Configuration Hierarchy

```
Global Defaults
  ↓
Tenant Config (registry.yaml)
  ↓
User Session Config (console settings)
  ↓
Per-Feature Overrides (e.g., /review --depth=5)
```

Example:

```yaml
# Global default (baked in)
review_depth: 3

# Tenant override (.corvin/tenants/_default/plugins/registry.yaml)
ai-code-review:
  config:
    review_depth: 4

# User session (console Settings → Plugins → ai-code-review)
# User changes review_depth slider to 5
# → instances/ai-code-review/config.json now has review_depth: 5
```

---

## API for Plugin Developers

```python
from corvin_sdk import Plugin, PluginConfig, Logger

class MyCustomTool(Plugin):
    def __init__(self, config: PluginConfig, logger: Logger):
        self.config = config
        self.logger = logger
    
    async def on_config_change(self, old: dict, new: dict, logger):
        # Validate new config
        if "api_key" in new and not new["api_key"]:
            raise ValueError("api_key is required")
        logger.info(f"Config updated: {new}")
    
    async def my_tool_function(self, user_input: str) -> dict:
        # Access config
        api_key = self.config.get("api_key")
        model = self.config.get("model_selection", "haiku")
        
        # Do work
        result = await expensive_api_call(user_input, model)
        
        # Return result + metadata
        return {
            "output": result,
            "usage": {"tokens": 1234},
            "audit": {"action": "custom_tool_executed"}
        }

# Decorator-based for simpler plugins
@corvin_plugin(
    id="hello-world",
    version="1.0.0",
    settings_schema={
        "greeting": {"type": "string", "default": "Hello"}
    }
)
async def my_skill(message: str, config: dict) -> str:
    return f"{config['greeting']}, {message}!"
```

---

## Rollout Phases

### Phase 1 (Month 1): Foundation
- [ ] Plugin Registry (YAML + Python loader)
- [ ] Settings Schema (JSON Schema validator)
- [ ] Console Plugins Tab (UI for enable/disable + basic config)
- [ ] Audit Trail (plugin operations logged)
- [ ] Marketplace Listing Schema

### Phase 2 (Month 2): Developer Experience
- [ ] Plugin SDK (corvin_sdk package)
- [ ] Plugin Sandbox (Forge-bwrap integration)
- [ ] Plugin Signing (L37 artifact signing)
- [ ] Plugin Testing Tools (CorvinOS test harness)

### Phase 3 (Month 3): Marketplace
- [ ] Marketplace Frontend (browse, install, rate)
- [ ] Auto-Updates (version checking + safe rollout)
- [ ] Plugin Dependencies (plugin → plugin deps)
- [ ] Community Marketplace (public GitHub-based catalog)

---

## Open Questions (RFC)

1. **Pricing Model:** Should plugins be sold via Paddle like CorvinOS itself, or hosted on Marketplace with separate billing?
2. **Isolation:** How strict should sandboxing be? Tier A (built-in) → no sandbox, Tier B → light sandbox, Tier C → strict bwrap?
3. **Auto-Updates:** Should users be prompted, or automatically updated within a version? (e.g., 2.0.x auto, 2.0→3.0 ask)
4. **Versioning:** Semver, or simpler scheme (v1, v2, ...)?
5. **Signature Verification:** Mandatory for all plugins, or only Tier B+?

---

## Alignment with Existing CorvinOS

| CorvinOS Layer | Integration Point |
|---|---|
| L6 Forge | Plugins can register Forge tools dynamically |
| L7 SkillForge | Plugins can provide SkillForge rulesets |
| L16 Security | Audit trail + consent gating |
| L34 Flow Guard | Plugins declare PII handling tier |
| L37 Artifact Signing | Plugin binaries signed (manifest.yml integrity check) |
| L44 House Rules | Plugins must pass compliance baseline before activation |
| Settings → Features | Plugin enable/disable mirrors feature flags UX |

