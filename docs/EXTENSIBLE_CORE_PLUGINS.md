# Extensible Core Plugins Architecture
## Reference Implementations with Full Replacement Capability

**Date:** 2026-07-26  
**Critical Clarification:** Core Plugins are NOT immutable. They are *reference implementations* with extension points for complete replacement.

---

## The Principle: Open Core, Not Locked Core

### What We're NOT Building
```
❌ "You can't replace Tier-1 plugins"
❌ "ACS is proprietary, you must use ours"
❌ "Voice Summary is hardcoded, no alternatives"
```

### What We ARE Building
```
✅ "Here's our battle-tested Voice Summary (default)"
✅ "Want to replace it? Override this hook + plugin system will use yours"
✅ "Building custom ACS? Plug it in, same interface, zero changes to core"
✅ "All bridges bundled, choose which to enable"
```

---

## Core Plugin Categories

### Category A: Immutable Compliance (Tier-0)
```
❌ CANNOT be replaced (regulatory requirement)

- Audit Writer
- Consent Gate
- Flow Guard
- House Rules
- Erasure
```

**Why:** GDPR/EU AI Act 2026. No compliance workaround.

---

### Category B: Strategic Defaults (Tier-1) ← EXTENSIBLE
```
✅ CAN be replaced via plugins

- A2A Orchestration (default: our implementation)
  └─ User can build: geo-aware A2A, custom attestation, etc.

- TDE Routing (default: our token optimizer)
  └─ User can build: cost-optimized router, ML-based router, etc.

- Conversation Recall (default: file-based + encryption)
  └─ User can build: graph DB storage, S3 archival, etc.

- ACS Manager (default: in-process thread pool)
  └─ User can build: remote worker network, GPU-accelerated compute, etc.

- Compute Worker (default: sandboxed Python/JS)
  └─ User can build: WASM sandbox, Docker container per request, etc.

- Delegation Router (default: native → TDE → ACS)
  └─ User can build: geo-aware, SLA-aware, cost-aware routing, etc.

- Workflow Engine (default: DAG executor)
  └─ User can build: state machine engine, visual workflow builder, etc.

- Engine Control (default: budget-aware model selection)
  └─ User can build: quality-first, speed-first, cost-first policies, etc.

- Voice Summary (default: LLM summarization)
  └─ User can build: extractive summary, bullet-point summary, custom language, etc.

- Admin Control Plane (default: REST API + React dashboard)
  └─ User can build: custom dashboard, CLI-only interface, etc.
```

**How they work:**
1. Core provides default implementation
2. User can register hook to *override* default
3. Or: User can install alternative plugin to *replace* entire component
4. System doesn't know the difference (both are plugins)

---

## Extension Points vs. Replacement

### Option 1: Hook-Based Customization (Light)
```python
# User registers a hook (plugin runs alongside default)
policy.register_hook("voice_summary_algorithm", my_summarizer_fn)

def my_summarizer_fn(turns: list[Turn]) -> str:
    """Override default LLM summary with custom algorithm."""
    return extract_key_points(turns)  # Simpler, faster

# System calls:
# 1. Default voice summary → LLM
# 2. Your hook → extract_key_points
# 3. User gets your summary instead
```

### Option 2: Full Replacement (Deep)
```python
# User builds complete alternative plugin
class VoiceSummaryAlternativePlugin(CorvinPlugin):
    plugin_id = "custom-voice-summary/1.0.0"
    plugin_type = "tier-1-alternative"  # Replaces bundled default
    
    def on_load(self, ctx):
        # Disable default voice summary
        voice_summary = ctx.registry.registry["voice-summary/1.0.0"]
        voice_summary.on_unload()
        
        # Install custom version
        ctx.registry.register(self)
    
    def summarize(self, turns: list[Turn]) -> str:
        """Complete replacement logic."""
        # Could be: extractive, abstractive, bullet-point, etc.
        return my_custom_algorithm(turns)

# User installs:
corvinctl plugin install custom-voice-summary --tier-1-replace voice-summary
```

---

## Bundled Bridges (All Included)

### In Repo Structure
```
/home/shumway/projects/CorvinOS/

core/core_plugins/
├─ tier_0/                        ← Immutable compliance
│  ├─ audit_compliance/
│  ├─ consent_gate/
│  └─ ...
│
├─ tier_1_core/                   ← Strategic defaults (extensible)
│  ├─ a2a_orchestration/
│  ├─ tde_routing/
│  ├─ conversation_recall/
│  ├─ acs_manager/
│  ├─ compute_worker/
│  ├─ delegation_router/
│  ├─ workflow_engine/
│  ├─ engine_control/
│  ├─ voice_summary/              ← NEW: Default implementation
│  └─ admin_control_plane/
│
├─ tier_2_bundled/                ← Bundled, pre-installed, optional
│  ├─ discord_bridge/             ← All bridges included
│  ├─ slack_bridge/
│  ├─ telegram_bridge/
│  ├─ whatsapp_bridge/
│  ├─ web_ui/
│  ├─ structured_logging/
│  ├─ forge/
│  ├─ skillforge/
│  └─ monitoring/
```

### Boot: Enable/Disable by Config
```yaml
# ~/.corvin/tenants/_default/config.yaml

plugins:
  tier_0:
    auto_load: true              # Always load (immutable)
  
  tier_1_core:
    auto_load: true              # Load defaults
  
  tier_2_bundled:
    auto_load:                   # User chooses what to enable
      - discord_bridge           # ✅ Enable
      - slack_bridge             # ✅ Enable
      - telegram_bridge          # ❌ Disabled
      - web_ui                   # ✅ Enable
      - forge                    # ✅ Enable
      - skillforge               # ✅ Enable
  
  tier_1_alternatives:
    installed:
      - custom-voice-summary/1.0.0     # Override default voice summary
      - my-agentic-compute/2.0.0       # Replace ACS with custom version
```

---

## Voice Summary: Example of Full Replacement

### Default Implementation (Bundled)
```
core/core_plugins/tier_1_core/voice_summary/
├─ __init__.py
├─ plugin.py                 ← class VoiceSummaryPlugin(CorvinPlugin)
├─ summarizer.py            ← Default: LLM-based
├─ hooks.py                 ← Extension points
└─ test/
   ├─ test_default.py       ← Test default behavior
   └─ test_hooks.py         ← Test hook overrides
```

### User's Custom Implementation
```
~/.corvin/tenants/_default/plugins/custom-voice-summary/
├─ __init__.py
├─ plugin.py               ← class CustomVoiceSummaryPlugin(CorvinPlugin)
├─ extractive.py           ← User's extractive summary
├─ bullet_point.py         ← Alternative: bullet points
└─ config.yaml             ← Which algorithm to use
```

### How It Works
```python
# Default (bundled with CorvinOS)
class VoiceSummaryPlugin(CorvinPlugin):
    plugin_id = "voice-summary/1.0.0"
    plugin_type = "tier-1-core"
    
    def on_load(self, ctx):
        self.hook_manager = HookManager()
        self.hook_manager.register(
            "summary_algorithm",
            self._summarize_via_llm  # Default
        )
    
    def summarize(self, turns: list[Turn]) -> str:
        # Call hook (might be overridden)
        algorithm = self.hook_manager.call(
            "summary_algorithm",
            turns=turns
        )
        if algorithm:
            return algorithm
        
        # Fallback to default
        return self._summarize_via_llm(turns)
    
    def _summarize_via_llm(self, turns: list[Turn]) -> str:
        prompt = f"Summarize conversation:\n{format_turns(turns)}"
        response = claude.messages.create(model="sonnet", messages=[{"role": "user", "content": prompt}])
        return response.content[0].text


# User's custom plugin
class CustomVoiceSummaryPlugin(CorvinPlugin):
    plugin_id = "custom-voice-summary/1.0.0"
    plugin_type = "tier-1-alternative"
    
    def on_load(self, ctx):
        # Method 1: Override hook in existing plugin
        voice_summary = ctx.registry.registry["voice-summary/1.0.0"]
        voice_summary.hook_manager.register(
            "summary_algorithm",
            self.my_extractive_summary,
            priority=100  # Override default
        )
    
    def my_extractive_summary(self, turns: list[Turn]) -> str:
        """Extract key sentences instead of LLM summarization."""
        key_sentences = []
        for turn in turns:
            if turn.is_important:  # Heuristic
                key_sentences.append(turn.content)
        return "\n".join(key_sentences)


# OR Method 2: Complete replacement
class AlternativeVoiceSummaryPlugin(CorvinPlugin):
    plugin_id = "alternative-voice-summary/1.0.0"
    plugin_type = "tier-1-alternative"
    
    def on_load(self, ctx):
        # Disable default
        default = ctx.registry.registry.get("voice-summary/1.0.0")
        if default:
            default.on_unload()
            del ctx.registry.registry["voice-summary/1.0.0"]
        
        # Install as replacement
        ctx.registry.register(self)
    
    def summarize(self, turns: list[Turn]) -> str:
        """My custom algorithm (e.g., bullet-point summary)."""
        return bullet_point_summary(turns)
```

---

## Agentic Compute: Replace ACS with Custom Version

### Default ACS (Bundled)
```
core/core_plugins/tier_1_core/acs_manager/
├─ manager.py              ← In-process thread pool
├─ task_queue.py
└─ worker_lifecycle.py
```

### User's Custom Implementation
```
~/.corvin/tenants/_default/plugins/my-agentic-compute/
├─ plugin.py               ← class MyAgenticComputePlugin(CorvinPlugin)
├─ orchestrator.py         ← Kubernetes-based workers
├─ gpu_manager.py          ← GPU scheduling
└─ remote_worker.py        ← gRPC remote workers
```

### Installation
```bash
corvinctl plugin install my-agentic-compute \
    --tier-1-replace acs-manager \
    --config remote_worker_pool=https://workers.example.com

# Result: Every request goes to custom ACS, not default
```

---

## Bundled Bridges: All in Repo

### Structure
```
core/core_plugins/tier_2_bundled/

discord_bridge/
├─ plugin.py
├─ bridge.py               ← Discord client
├─ adapter.py              ← Corvin API ↔ Discord
└─ config.yaml             ← Token, rate limits

slack_bridge/
├─ plugin.py
├─ bridge.py
├─ adapter.py
└─ config.yaml

telegram_bridge/
├─ plugin.py
├─ bridge.py
├─ adapter.py
└─ config.yaml

whatsapp_bridge/          ← Via Twilio API
├─ plugin.py
├─ bridge.py
├─ adapter.py
└─ config.yaml

web_ui/                   ← React frontend
├─ plugin.py              ← Web server plugin
├─ public/                ← Built React app
├─ server.py              ← FastAPI server
└─ routes.py
```

### Enable/Disable
```yaml
# ~/.corvin/tenants/_default/config.yaml

plugins:
  tier_2_bundled:
    enabled:
      - discord_bridge
      - slack_bridge
      - web_ui
    
    disabled:
      - telegram_bridge
      - whatsapp_bridge
    
    config:
      discord_bridge:
        token: "${DISCORD_TOKEN}"
      slack_bridge:
        token: "${SLACK_TOKEN}"
      web_ui:
        port: 3000
```

### Boot
```python
# core/bootstrap.py

def load_bundled_plugins():
    """Load tier_2_bundled plugins based on config."""
    enabled = config.plugins.tier_2_bundled.enabled
    
    bundled_path = Path(__file__).parent / "core_plugins" / "tier_2_bundled"
    
    for plugin_name in enabled:
        module = __import__(f"corvin.core.core_plugins.tier_2_bundled.{plugin_name}")
        plugin = module.plugin()
        plugin.on_load(ctx)
        registry.register(plugin)
```

---

## Extension Points per Tier-1 Component

### A2A Orchestration
```python
a2a.register_hook("routing.select_target", custom_routing)      # Override routing
a2a.register_hook("attestation.verify", custom_attestation)    # ADD attestation
a2a.register_hook("envelope.pre_send", custom_pre_send)        # Inspect before send
```

### TDE Routing
```python
tde.register_hook("cost_model", custom_cost_model)             # Override cost calculation
tde.register_hook("router_strategy", custom_strategy)          # Override routing
```

### Conversation Recall
```python
recall.register_hook("storage_backend", custom_backend)        # Override storage
recall.register_hook("encryption", custom_crypto)              # Override encryption (if allowed)
```

### ACS Manager
```python
acs.register_hook("worker_selector", custom_selector)          # Choose worker
acs.register_hook("task_prioritizer", custom_priority)         # Sort queue
```

### Workflow Engine
```python
workflow.register_hook("workflow_gate", custom_gate)           # Custom gate before run
workflow.register_hook("node_executor", custom_node)           # Custom node type
```

### Engine Control
```python
engine.register_hook("model_selection", custom_model_fn)       # Choose model
engine.register_hook("engine_selection", custom_engine_fn)     # Choose engine
```

### Voice Summary ← NEW
```python
voice_summary.register_hook("summary_algorithm", custom_summarizer)  # Custom algorithm
```

---

## Tier System Redefined

| Tier | Immutable | Extensible | Replaceable | Example |
|------|-----------|-----------|------------|---------|
| **0** | ✅ | ❌ | ❌ | Audit Writer |
| **1-Core** | ❌ | ✅ | ✅ | ACS, TDE, A2A, Voice Summary |
| **1-Alt** | ✅ (for compliance) | N/A | ✅ | Custom ACS, Custom Voice Summary |
| **2-Bundled** | ❌ | ✅ | ✅ | Discord Bridge, Web UI |
| **3-Premium** | ❌ | ✅ | ✅ | Postgres Backend, OKTA Auth |

---

## Directory Structure (Final)

```
/home/shumway/projects/CorvinOS/

core/
├─ compliance/                      ← Tier-0, hardcoded, immutable
│  ├─ audit_writer.py
│  ├─ consent_gate.py
│  └─ ...
│
├─ core_plugins/
│  ├─ tier_0/                      ← Compliance (immutable)
│  │  ├─ audit_compliance/
│  │  ├─ consent_gate/
│  │  ├─ flow_guard/
│  │  ├─ house_rules/
│  │  └─ erasure/
│  │
│  ├─ tier_1_core/                 ← Strategic defaults (extensible + replaceable)
│  │  ├─ a2a_orchestration/        ← Default A2A implementation
│  │  ├─ tde_routing/              ← Default TDE router
│  │  ├─ conversation_recall/      ← Default storage (file-based)
│  │  ├─ acs_manager/              ← Default in-process ACS
│  │  ├─ compute_worker/           ← Default Python/JS sandbox
│  │  ├─ delegation_router/        ← Default native→TDE→ACS
│  │  ├─ workflow_engine/          ← Default DAG executor
│  │  ├─ engine_control/           ← Default budget-aware selector
│  │  ├─ voice_summary/            ← Default LLM summarizer (NEW)
│  │  └─ admin_control_plane/      ← Default REST + React
│  │
│  ├─ tier_2_bundled/              ← Bundled, pre-installed, enable/disable
│  │  ├─ discord_bridge/
│  │  ├─ slack_bridge/
│  │  ├─ telegram_bridge/
│  │  ├─ whatsapp_bridge/
│  │  ├─ web_ui/
│  │  ├─ structured_logging/
│  │  ├─ forge/
│  │  ├─ skillforge/
│  │  └─ monitoring/
│  │
│  └─ base/
│     ├─ plugin.py                ← CorvinPlugin abstract
│     ├─ registry.py              ← PluginRegistry (tier-aware)
│     └─ hooks.py                 ← HookManager

api/
├─ server.py                       ← FastAPI + gRPC
└─ routes/
   ├─ admin.py
   └─ ...

~/.corvin/tenants/_default/

├─ plugins/
│  ├─ custom-voice-summary/        ← User can replace default
│  │  ├─ plugin.py
│  │  └─ summarizer.py
│  │
│  ├─ my-agentic-compute/          ← User can replace ACS
│  │  ├─ plugin.py
│  │  └─ orchestrator.py
│  │
│  ├─ geo-aware-routing/           ← User can extend A2A
│  │  ├─ plugin.py
│  │  └─ routing_logic.py
│  │
│  └─ (user-installed plugins)
│
├─ config.yaml                     ← Which plugins enabled/disabled
└─ hooks/                          ← User extension code
   ├─ voice_summary.py
   ├─ engine_routing.py
   └─ (user code)
```

---

## Installation & Deployment

### Scenario A: Developer (Wants Custom Voice Summary)
```bash
# Step 1: Clone repo
git clone https://github.com/anthropics/corvinOS.git
cd corvinOS

# Step 2: Create custom plugin in repo
mkdir core/core_plugins/tier_2_custom
cat > core/core_plugins/tier_2_custom/custom_voice_summary/plugin.py << 'EOF'
class CustomVoiceSummaryPlugin(CorvinPlugin):
    def summarize(self, turns): 
        return extract_key_points(turns)
EOF

# Step 3: Install & run
pip install -e .
corvinctl start

# Step 4: Register hook
corvinctl plugin install custom-voice-summary --tier-1-replace voice-summary
```

### Scenario B: End User (Install Pre-built)
```bash
# Install from wheel (all bundled)
pip install corvinOS

# Configure which bridges to enable
cat > ~/.corvin/tenants/_default/config.yaml << 'EOF'
plugins:
  tier_2_bundled:
    enabled:
      - discord_bridge
      - slack_bridge
      - web_ui
EOF

# Start
corvinctl start

# Discord + Slack now running, Web UI on :3000
```

### Scenario C: Enterprise (Custom ACS)
```bash
# Base install
pip install corvinOS

# Build custom ACS (in separate repo or as plugin)
cd my-custom-acs
pip install -e .

# Register
corvinctl plugin install my-custom-acs \
    --tier-1-replace acs-manager

# Now uses custom ACS for all requests
```

---

## Summary: Open Core Philosophy

| Aspect | Before | After |
|--------|--------|-------|
| ACS | "Use ours or fork" | "Use ours, or register your own" |
| Voice Summary | "Fixed" | "Default ours, override via hook or replace plugin" |
| Bridges | "Some bundled, others optional" | "All bundled, enable/disable per tenant" |
| Customization | "Limited to hooks" | "Hooks + full plugin replacement" |
| Development | "Hard to test alternatives" | "Build custom plugin alongside default" |

**Result:** Open Core (reference implementations) + Full Extensibility = Community-friendly, enterprise-flexible, zero lock-in.

