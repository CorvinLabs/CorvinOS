# CorvinOS Plugin Examples

This directory contains example plugins and templates for building your own CorvinOS plugins.

## Examples

### `audit-backend-template/`

A minimal audit backend plugin showing best practices. Copy this directory as a starting point:

```bash
cp -r audit-backend-template/ ~/.corvin/plugins/installed/my-audit-backend/
cd ~/.corvin/plugins/installed/my-audit-backend/
```

Then customize:
1. Edit `manifest.yaml` — update plugin_id, author, homepage
2. Edit `plugin.py` — implement your backend logic
3. Edit `test_plugin.py` — add your own tests

**Key files:**
- `manifest.yaml` — Plugin metadata and configuration schema
- `plugin.py` — The plugin class implementing PluginInterface + AuditBackend
- `test_plugin.py` — Unit tests for the plugin

## From the templates/ Directory

The `templates/` directory (one level up) contains additional example plugins for each type:

- `notification_backend_plugin.py` — Send notifications to Slack, Discord, email, etc.
- `recall_backend_plugin.py` — Store/retrieve PII-redacted conversation turns
- `router_backend_plugin.py` — Route incoming messages to different personas
- `summary_provider_plugin.py` — Summarize long responses for TTS
- `user_backend_plugin.py` — Authenticate users from LDAP, OAuth, etc.
- `worker_engine_plugin.py` — Custom LLM worker (Claude API, Bedrock, local)
- `compute_engine_plugin.py` — Managed compute for large data jobs
- `bridge_channel_plugin.py` — Bridge transport (Discord, Slack, Telegram)

## Creating Your Plugin

1. **Start with a template:**
   - Copy `audit-backend-template/` or a matching type from `templates/`
   - Rename the directory to your `plugin_id`

2. **Update the manifest:**
   ```yaml
   plugin_id: my-plugin-id
   version: 1.0.0
   plugin_type: notification_backend  # or another type
   author: "Your Name"
   email: "your@email.com"
   ```

3. **Implement the plugin class:**
   - Inherit from `PluginInterface` (recommended) or implement `CorvinPlugin` protocol
   - Implement the capability protocol for your plugin_type
   - Write `on_load()`, `on_unload()`, `health_check()`

4. **Write tests:**
   - Copy `test_plugin.py` and adapt it
   - Test lifecycle methods, health checks, and capability methods
   - Run: `pytest test_plugin.py`

5. **Install and enable:**
   ```bash
   corvin plugin install ./my-plugin/
   corvin plugin enable my-plugin-id
   ```

## Plugin Types

| Type | Base Protocol | Purpose |
|---|---|---|
| `audit_backend` | AuditBackend | Receive audit events for external sinks |
| `notification_backend` | NotificationBackend | Send notifications (Slack, email, etc.) |
| `recall_backend` | RecallBackend | Store/retrieve conversation turns |
| `router_backend` | RouterBackend | Select personas |
| `summary_provider` | SummaryProvider | Summarize for TTS |
| `user_backend` | UserBackend | External user authentication |
| `worker_engine` | WorkerEngine | Custom LLM worker |
| `compute_engine` | ComputeEngine | Managed compute |
| `bridge_channel` | BridgeChannel | Bridge transport |
| `stt_provider` | STTProvider | Speech-to-text |
| `data_connector` | DataConnector | External data sources |
| `web_surface` | WebSurface | Browser UI surfaces |

## Testing Your Plugin

```bash
# Run unit tests
pytest test_plugin.py -v

# With coverage
pytest test_plugin.py --cov=plugin --cov-report=html

# Validate manifest
corvin plugin validate manifest.yaml

# Check reachability (once installed)
corvin plugin health my-plugin-id
```

## Documentation

- Full guide: `docs/PLUGIN_DEVELOPMENT.md`
- Interface reference: `core/plugins/corvin_plugins/plugin_interface.py`
- Protocol definitions: `core/plugins/corvin_plugins/protocol.py`
- Manifest reference: `core/plugins/corvin_plugins/manifest.py`

## Common Patterns

### Validating Configuration

```python
def on_load(self, ctx: PluginContext) -> None:
    config = ctx.config
    required = {"api_key", "host"}
    missing = required - set(config.keys())
    if missing:
        raise ValueError(f"Missing required config: {missing}")
```

### Never Block in health_check()

```python
def health_check(self) -> HealthStatus:
    # ✓ OK: check cached state
    if not self.is_connected:
        return HealthStatus(ok=False, message="Not connected")
    
    # ✗ BAD: don't do network I/O
    # response = requests.get("https://api.example.com/health")
```

### Log Only Exception Class, Never the Message

```python
def fanout(self, event_type: str, details: dict, **kwargs) -> None:
    try:
        self.send_to_backend(event_type, details)
    except Exception as e:
        # ✓ OK: log class name
        logger.warning(f"fanout raised {type(e).__name__}")
        # ✗ BAD: don't log the message (may contain PII or secrets)
        # logger.warning(f"fanout failed: {e}")
```

### Idempotent on_unload()

```python
def on_unload(self) -> None:
    try:
        if hasattr(self, "client") and self.client is not None:
            self.client.close()
    except Exception:
        # Don't raise; just log
        pass
```

## Troubleshooting

**Plugin won't load:**
```bash
corvin plugin health my-plugin-id
# Check: manifest syntax, required config, Python import errors
```

**Health check fails:**
- Ensure `health_check()` returns immediately (< 2s)
- Don't do I/O in health_check(); use cached state
- Don't raise exceptions; return unhealthy status instead

**Can't instantiate plugin:**
- Ensure `__init__` takes no arguments (except `self`)
- Don't initialize heavy resources in `__init__`; do it in `on_load()`

**Plugin shows "stale" in Console:**
- Hard-refresh browser: `Ctrl+Shift+R` (Chrome/Firefox) or `Cmd+Shift+R` (macOS)

## Contributing Examples

Found a great plugin pattern? Add it here:

1. Create a directory: `examples/my-plugin-name/`
2. Include: `manifest.yaml`, `plugin.py`, `test_plugin.py`, `README.md`
3. Submit a PR with a description of what the plugin does

---

**Questions?** See `docs/PLUGIN_DEVELOPMENT.md` or open an issue on GitHub.
