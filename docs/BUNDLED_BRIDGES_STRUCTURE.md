# Bundled Bridges: All Distribution Channels Included

**Date:** 2026-07-26  
**Principle:** Bridges are pre-built plugins, all in repo, enabled/disabled per tenant config.

---

## Bridges Included in CorvinOS (Tier-2 Bundled)

```
core/core_plugins/tier_2_bundled/

discord_bridge/          ← Discord server/user DMs
slack_bridge/            ← Slack workspace
telegram_bridge/         ← Telegram chat
whatsapp_bridge/         ← WhatsApp via Twilio
web_ui/                  ← React web app
cli_bridge/              ← Terminal/SSH
```

---

## Each Bridge: Same Interface, Different Transport

### Structure Per Bridge
```
discord_bridge/
├─ __init__.py
├─ plugin.py                  ← class DiscordBridgePlugin(CorvinPlugin)
│
├─ bridge.py                  ← Discord client (discord.py library)
│  ├─ connect()              ← Connect to Discord
│  ├─ send_message()         ← Send to user/channel
│  ├─ receive_message()      ← Listen for messages
│  └─ on_message_received()  ← Handler
│
├─ adapter.py                 ← Bridge ↔ Corvin Core
│  ├─ message_to_request()   ← Discord msg → Corvin request
│  ├─ response_to_message()  ← Corvin response → Discord msg
│  └─ handle_error()         ← Fallback messages
│
├─ config.yaml               ← Bridge config schema
│  ├─ token (env var)
│  ├─ rate_limit
│  ├─ cache_ttl
│  └─ prefix (e.g., "!")
│
├─ test/
│  ├─ test_bridge.py
│  ├─ test_adapter.py
│  └─ fixtures/
│
└─ README.md                 ← Setup guide
```

---

## Implementation Pattern

### Discord Bridge Example
```python
# core/core_plugins/tier_2_bundled/discord_bridge/plugin.py

class DiscordBridgePlugin(CorvinPlugin):
    plugin_id = "discord-bridge/1.0.0"
    plugin_type = "tier-2-bundled"
    
    def on_load(self, ctx):
        """Boot the Discord bridge."""
        self.ctx = ctx
        self.config = self.get_config()
        
        # Initialize Discord client
        self.bridge = DiscordBridge(
            token=self.config["token"],
            intents=discord.Intents.default()
        )
        
        # Start connection
        asyncio.create_task(self.bridge.start())
        
        # Register message handler
        self.bridge.on("message", self.on_message)
        
        ctx.logger.info("Discord bridge loaded")
    
    def on_unload(self):
        """Shutdown gracefully."""
        asyncio.create_task(self.bridge.close())
    
    async def on_message(self, message):
        """Handle incoming Discord message."""
        if message.author.bot:
            return  # Ignore bots
        
        # Convert Discord → Corvin request
        request = MessageToRequest(
            user_id=str(message.author.id),
            user_name=message.author.name,
            channel_id=str(message.channel.id),
            content=message.content,
            platform="discord",
            metadata={
                "guild_id": str(message.guild.id) if message.guild else None,
                "is_dm": isinstance(message.channel, discord.DMChannel),
            }
        )
        
        # Send to Corvin Core API
        try:
            response = await self.ctx.api_client.execute(request)
        except Exception as e:
            response = f"Error: {type(e).__name__}"
        
        # Convert Corvin → Discord message
        discord_msg = ResponseToMessage(
            content=response,
            channel=message.channel,
            reply_to=message
        )
        
        await self.bridge.send(discord_msg)
    
    def health_check(self) -> HealthStatus:
        """Is Discord connection alive?"""
        return HealthStatus(
            ok=self.bridge.is_connected,
            message="Discord connected" if self.bridge.is_connected else "Disconnected"
        )
```

### Slack Bridge (Same Pattern)
```python
class SlackBridgePlugin(CorvinPlugin):
    plugin_id = "slack-bridge/1.0.0"
    plugin_type = "tier-2-bundled"
    
    def on_load(self, ctx):
        """Boot Slack bridge."""
        self.ctx = ctx
        self.config = self.get_config()
        
        self.bridge = SlackBridge(token=self.config["token"])
        asyncio.create_task(self.bridge.start())
        self.bridge.on("message", self.on_message)
    
    async def on_message(self, event):
        """Handle Slack message."""
        request = MessageToRequest(
            user_id=event["user"],
            channel_id=event["channel"],
            content=event["text"],
            platform="slack",
            metadata={"thread_ts": event.get("thread_ts")}
        )
        
        response = await self.ctx.api_client.execute(request)
        
        await self.bridge.send(
            channel=event["channel"],
            text=response,
            thread_ts=event.get("thread_ts")
        )
```

---

## Web UI: Special Case (Requires Server)

```python
# core/core_plugins/tier_2_bundled/web_ui/plugin.py

class WebUIPlugin(CorvinPlugin):
    plugin_id = "web-ui/1.0.0"
    plugin_type = "tier-2-bundled"
    
    def on_load(self, ctx):
        """Boot web UI server."""
        self.ctx = ctx
        self.config = self.get_config()
        
        # Spin up FastAPI server for static files
        self.app = FastAPI(title="CorvinOS Web UI")
        
        # Serve React build
        self.app.mount(
            "/",
            StaticFiles(directory="core/core_plugins/tier_2_bundled/web_ui/public"),
            name="web_ui"
        )
        
        # WebSocket for real-time chat
        @self.app.websocket("/ws/chat")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            while True:
                data = await websocket.receive_json()
                request = MessageToRequest(
                    user_id=data["user_id"],
                    content=data["message"],
                    platform="web"
                )
                response = await self.ctx.api_client.execute(request)
                await websocket.send_json({"response": response})
        
        # Start server
        config = uvicorn.Config(
            app=self.app,
            host=self.config["host"],
            port=self.config["port"]
        )
        self.server = uvicorn.Server(config)
        asyncio.create_task(self.server.serve())
```

---

## Configuration: Enable/Disable Bridges

### Per-Tenant Config
```yaml
# ~/.corvin/tenants/_default/config.yaml

plugins:
  tier_2_bundled:
    enabled:
      - discord_bridge
      - slack_bridge
      - web_ui
      - structured_logging
      - forge
      - skillforge
    
    disabled:
      - telegram_bridge
      - whatsapp_bridge
      - cli_bridge
    
    config:
      discord_bridge:
        token: "${DISCORD_TOKEN}"
        rate_limit: 10
        prefix: "!"
        cache_ttl: 60
      
      slack_bridge:
        token: "${SLACK_TOKEN}"
        rate_limit: 5
      
      web_ui:
        host: "0.0.0.0"
        port: 3000
        cors_origins: ["https://app.example.com"]
      
      structured_logging:
        level: "INFO"
        output: "loki"  # stdout, loki, splunk
        loki_url: "http://localhost:3100"
      
      forge:
        sandbox_memory_mb: 512
        sandbox_timeout_s: 30
```

---

## Boot: Enable Selected Bridges

```python
# core/bootstrap.py

def load_bundled_plugins():
    """Load tier_2_bundled based on tenant config."""
    enabled_bridges = config.plugins.tier_2_bundled.enabled
    
    bundled_path = Path(__file__).parent / "core_plugins" / "tier_2_bundled"
    
    for bridge_name in enabled_bridges:
        bridge_path = bundled_path / bridge_name
        
        if not bridge_path.exists():
            logger.warning(f"Bridge {bridge_name} not found (disabled in config)")
            continue
        
        try:
            # Dynamic import
            module = importlib.import_module(
                f"corvin.core.core_plugins.tier_2_bundled.{bridge_name}.plugin"
            )
            
            plugin_class = getattr(module, f"{camel_case(bridge_name)}Plugin")
            plugin = plugin_class()
            
            # Load into registry
            plugin.on_load(PluginContext(...))
            registry.register(plugin)
            
            logger.info(f"✅ Bridge loaded: {bridge_name}")
        
        except Exception as e:
            logger.error(f"❌ Bridge failed: {bridge_name}: {e}")
            # Continue (bridge-specific failure doesn't break system)
```

---

## Deployment Scenarios

### Scenario 1: All Bridges (Small Instance)
```yaml
plugins:
  tier_2_bundled:
    enabled:
      - discord_bridge
      - slack_bridge
      - telegram_bridge
      - whatsapp_bridge
      - web_ui
      - forge
      - skillforge

# Result: Single container with all bridges
# Pros: Simple, everything together
# Cons: Resources high, one bridge failure affects others (if in-process)
```

### Scenario 2: Web + Chat Only (Most Common)
```yaml
plugins:
  tier_2_bundled:
    enabled:
      - web_ui
      - discord_bridge
      - slack_bridge
      - forge
      - skillforge
    
    disabled:
      - telegram_bridge
      - whatsapp_bridge
      - cli_bridge

# Result: Web app + Discord + Slack only
# Pros: Lean, focused
# Cons: User can't access via Telegram
```

### Scenario 3: API Only (Enterprise)
```yaml
plugins:
  tier_2_bundled:
    enabled:
      - structured_logging
      - monitoring
    
    disabled:
      - discord_bridge
      - slack_bridge
      - telegram_bridge
      - whatsapp_bridge
      - web_ui
      - forge          # Company provides own agent builders
      - skillforge     # Company provides own skill registry

# Result: Pure API server, no bridges
# Pros: Minimal, secure, API-driven
# Cons: No web UI, no chat integrations
```

---

## Load Modes for Bridges

### Option A: In-Process (Simple, Default)
```
CorvinOS Core (process A)
├─ Tier-0/1 (hardcoded)
├─ Discord Bridge (in-process thread)
├─ Slack Bridge (in-process thread)
└─ Web UI (in-process FastAPI server)
```

**Pros:** Simple, low latency, shared memory  
**Cons:** One bridge crash can affect all, resource-heavy

### Option B: Subprocess (Isolated, Recommended)
```
CorvinOS Core (process A, gRPC server on :8000)
├─ Tier-0/1

Discord Bridge (subprocess B)
  └─ gRPC client to Core

Slack Bridge (subprocess C)
  └─ gRPC client to Core

Web UI (subprocess D)
  └─ gRPC client to Core
```

**Pros:** Isolated, can restart independently  
**Cons:** gRPC overhead, more complex deployment

### Option C: Hybrid
```
CorvinOS Core (in-process)
├─ Tier-0/1 (hardcoded)
└─ Web UI (in-process, stable)

Discord Bridge (subprocess)
Slack Bridge (subprocess)
```

**Pros:** Best of both (stable web UI, isolated chat bridges)

---

## Config Path Resolution

### 1. Installation-Wide
```
~/.corvin/global/config.yaml
└─ server.mode, plugins.load_mode, engine defaults
```

### 2. Tenant-Specific
```
~/.corvin/tenants/_default/config.yaml
└─ Which bridges enabled, API settings
```

### 3. Bridge-Specific
```
~/.corvin/tenants/_default/plugins/discord_bridge/config.yaml
└─ Token, rate limit, prefix
```

### Env Var Override (Secrets)
```bash
export DISCORD_TOKEN=xoxb-...
export SLACK_TOKEN=xoxb-...

# Plugin reads from env (takes precedence over config.yaml)
```

---

## Summary: Bundled Bridges Model

| Aspect | Model |
|--------|-------|
| **Location** | All in `/repo/core/core_plugins/tier_2_bundled/` |
| **Shipped** | In every wheel (no download needed) |
| **Enabled** | Per-tenant config (not per-installation) |
| **Config** | hierarchy: global → tenant → bridge-specific |
| **Load** | In-process (simple) or subprocess (isolated) |
| **Failure** | Bridge fails → other bridges + API unaffected (if subprocess) |
| **Customization** | User can extend via hooks or replace entire bridge |
| **Examples** | Discord, Slack, Telegram, WhatsApp, Web UI, CLI |

**Result:** Deploy once, enable what you need per tenant, zero external dependencies for basic bridges.

