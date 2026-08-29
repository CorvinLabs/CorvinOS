# CorvinOS Plugin Quick Reference Card

**Print this for your desk!**

---

## The Three Required Methods

Every plugin must implement these (or inherit from PluginInterface):

```python
def on_load(self, ctx: PluginContext) -> None:
    """Initialize. Raise if config is invalid."""
    
def on_unload(self) -> None:
    """Cleanup. Never raise. Idempotent."""
    
def health_check(self) -> HealthStatus:
    """Return immediately. Never block. Never raise."""
```

---

## The Four Required Attributes

Every plugin class must define:

```python
plugin_id = "my-plugin-id"           # lowercase, alphanumeric + ._-
plugin_type = "audit_backend"        # one of 13 KNOWN_PLUGIN_TYPES
version = "1.0.0"                    # semantic version
display_name = "My Plugin"           # human-readable
```

---

## Quick Template

```python
from corvin.core.plugins.corvin_plugins.plugin_interface import PluginInterface
from corvin.core.plugins.corvin_plugins.protocol import AuditBackend, PluginContext, HealthStatus

class MyAuditBackend(PluginInterface, AuditBackend):
    plugin_id = "my-audit-backend"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "My Audit Backend"

    def on_load(self, ctx: PluginContext) -> None:
        # 1. Validate config
        if "api_key" not in ctx.config:
            raise ValueError("Missing api_key")
        
        # 2. Initialize
        self.api_key = ctx.config["api_key"]
        
        # 3. Self-register
        ctx.audit_registry.register(self.plugin_id, self)
        
        # 4. Emit audit event
        ctx.audit_emit("plugin.loaded", {"plugin_id": self.plugin_id})

    def on_unload(self) -> None:
        # Cleanup (never raise)
        pass

    def health_check(self) -> HealthStatus:
        # Return immediately
        return HealthStatus(ok=True, message="OK")

    # Implement capability methods for your plugin_type
    def fanout(self, event_type: str, details: dict, **kwargs) -> None:
        # Don't raise; don't block
        pass
```

---

## Manifest Minimum

```yaml
plugin_id: my-plugin-id
version: 1.0.0
display_name: "My Plugin"
plugin_type: audit_backend
author: "Your Name"
email: "your@email.com"
license: "Apache-2.0"

settings_schema:
  type: object
  properties:
    api_key:
      type: string
  required:
    - api_key
```

---

## The Golden Rules

### ✅ DO

- Validate config in `on_load()`
- Raise from `on_load()` if config is bad
- Release resources in `on_unload()`
- Return from `health_check()` immediately (< 2s)
- Log exception CLASS only (e.g., `ConnectionError`)
- Self-register with your capability registry
- Emit audit events for important actions

### ❌ DON'T

- Block in `health_check()` — use cached state
- Raise from `on_unload()` — catch and ignore
- Raise from capability methods (fanout, notify, etc.)
- Block in capability methods (> 100ms max)
- Log full error messages (may contain PII)
- Log secrets (api_key, password, token)
- Log user content (prompts, transcripts)
- Store plugin_id in ContextVar

---

## Testing Checklist

```python
# Unit test template
import pytest
from unittest.mock import MagicMock
from plugin import MyAuditBackend
from corvin.core.plugins.corvin_plugins.protocol import PluginContext

@pytest.fixture
def plugin():
    return MyAuditBackend()

@pytest.fixture
def mock_ctx():
    ctx = MagicMock(spec=PluginContext)
    ctx.config = {"api_key": "test"}
    ctx.audit_registry = MagicMock()
    return ctx

def test_on_load_success(plugin, mock_ctx):
    plugin.on_load(mock_ctx)
    ctx.audit_registry.register.assert_called_once()

def test_on_load_fails_without_config(plugin):
    plugin_ctx = MagicMock(spec=PluginContext)
    plugin_ctx.config = {}
    with pytest.raises(ValueError):
        plugin.on_load(plugin_ctx)

def test_health_check_returns_status(plugin, mock_ctx):
    plugin.on_load(mock_ctx)
    status = plugin.health_check()
    assert status.ok is True or status.ok is False

def test_on_unload_idempotent(plugin):
    plugin.on_unload()
    plugin.on_unload()  # Should not raise
```

---

## The 13 Plugin Types

| Type | Protocol | Purpose |
|---|---|---|
| `audit_backend` | AuditBackend | Fanout to external sinks |
| `notification_backend` | NotificationBackend | Send alerts |
| `recall_backend` | RecallBackend | Store conversation history |
| `router_backend` | RouterBackend | Route to personas |
| `summary_provider` | SummaryProvider | Summarize for TTS |
| `user_backend` | UserBackend | External auth |
| `worker_engine` | WorkerEngine | Custom LLM |
| `compute_engine` | ComputeEngine | Managed compute |
| `bridge_channel` | BridgeChannel | Bridge transport |
| `stt_provider` | STTProvider | Speech-to-text |
| `data_connector` | DataConnector | External data |
| `web_surface` | WebSurface | Browser UI |

---

## Lifecycle Diagram

```
1. DISCOVERY
   └─ Manifest found: manifest.yaml

2. INSTANTIATION
   └─ Class() called with no arguments

3. ON_LOAD()
   ├─ Validate config
   ├─ Initialize resources
   ├─ Self-register with registry
   └─ Emit audit event

4. ENABLE/DISABLE (optional)
   ├─ on_enable() called
   └─ on_disable() called

5. HEALTH_CHECK() (periodic, 1-2x/sec)
   └─ Return HealthStatus(ok=True/False)

6. ON_UNLOAD()
   ├─ Release resources
   └─ Never raise
```

---

## Error Handling

```python
# ✓ GOOD: Health check never raises
def health_check(self) -> HealthStatus:
    try:
        if not self.is_connected:
            return HealthStatus(ok=False, message="Not connected")
        return HealthStatus(ok=True, message="OK")
    except Exception as e:
        return HealthStatus(ok=False, message=f"Exception: {type(e).__name__}")

# ✓ GOOD: Fanout never raises
def fanout(self, event_type: str, details: dict, **kwargs) -> None:
    try:
        self.send_to_backend(event_type, details)
    except Exception as e:
        logger.warning(f"fanout raised {type(e).__name__}")  # CLASS NAME ONLY

# ✓ GOOD: on_unload is idempotent, never raises
def on_unload(self) -> None:
    try:
        if hasattr(self, "client"):
            self.client.close()
    except Exception:
        pass  # Don't raise
```

---

## Configuration Validation

```python
# In manifest.yaml:
settings_schema:
  type: object
  properties:
    api_key:
      type: string
      description: "API key"
    port:
      type: integer
      minimum: 1
      maximum: 65535
    timeout:
      type: number
      default: 30
  required:
    - api_key

# In plugin.py:
def on_load(self, ctx: PluginContext) -> None:
    config = ctx.config
    if "api_key" not in config or not config["api_key"]:
        raise ValueError("api_key is required and must not be empty")
    if config.get("port", 0) < 1 or config.get("port", 0) > 65535:
        raise ValueError("port must be 1-65535")
```

---

## HealthStatus

```python
# Healthy
HealthStatus(
    ok=True,
    message="Connected and operational",
    details={"requests": 100, "latency_ms": 42}
)

# Unhealthy
HealthStatus(
    ok=False,
    message="Connection failed",
    details={"retry_after": 60}
)

# When exception occurs
HealthStatus(
    ok=False,
    message=f"Exception: {type(e).__name__}",  # CLASS NAME ONLY
)
```

---

## Common Commands

```bash
# Validate a plugin
corvin plugin validate ~/.corvin/plugins/installed/my-plugin/manifest.yaml

# Install from a directory
corvin plugin install ./my-plugin/

# Enable/disable
corvin plugin enable my-plugin-id
corvin plugin disable my-plugin-id

# Check health
corvin plugin health my-plugin-id

# List installed
corvin plugin list

# Uninstall
corvin plugin uninstall my-plugin-id

# View logs
journalctl -u corvin-service | grep my-plugin-id
```

---

## Links

- **Full Guide:** `/docs/PLUGIN_DEVELOPMENT.md`
- **Specification:** `/docs/PLUGIN_INTERFACE_SPECIFICATION.md`
- **Code Reference:** `/core/plugins/corvin_plugins/plugin_interface.py`
- **Protocols:** `/core/plugins/corvin_plugins/protocol.py`
- **Examples:** `/core/plugins/examples/`
- **Template:** `/core/plugins/examples/audit-backend-template/`

---

## One Minute Setup

```bash
# 1. Copy template
cp -r ~/.corvin/plugins/templates/audit-backend-template/ ~/.corvin/plugins/installed/my-plugin/

# 2. Edit manifest.yaml
nano ~/.corvin/plugins/installed/my-plugin/manifest.yaml

# 3. Edit plugin.py
nano ~/.corvin/plugins/installed/my-plugin/plugin.py

# 4. Run tests
cd ~/.corvin/plugins/installed/my-plugin/
pytest test_plugin.py -v

# 5. Install and enable
corvin plugin install ./
corvin plugin enable my-plugin-id

# 6. Check it works
corvin plugin health my-plugin-id
```

---

**Print this card. Keep it on your desk. Reference it while coding.**
