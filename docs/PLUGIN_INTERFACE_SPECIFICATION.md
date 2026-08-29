# CorvinOS Plugin Interface Specification

**Version:** 1.0  
**Status:** APPROVED (ADR-0030, ADR-0033, ADR-0233)  
**Last Updated:** 2026-08-29  
**Audience:** Plugin developers, plugin reviewers, maintainers

This document specifies the formal contract that all CorvinOS plugins must satisfy.

---

## Overview

The CorvinOS plugin system allows third-party code to extend core functionality without
forking, restarting, or compromising security. Plugins are Python classes that implement
one of 13 capability interfaces and must adhere to a strict lifecycle contract.

**Key Design Principles:**

1. **Perimeter is Attribution, Not Containment** — An in-process plugin is part of the
   process. We don't sandbox it; we make its actions attributable and auditable.
2. **Fail-Closed, Deny-by-Default** — Plugins default to off. Enabling requires explicit
   consent. Compliance mechanisms are always-on and non-disableable.
3. **One Lifecycle, One Registry, Three Orthogonal Axes** — boot_layer (load order) /
   origin (provenance) / tier (capability) are separate; never conflate them.
4. **Never Owned by Plugins** — The core audit trail is always written first and is
   never suppressible. Plugins receive copies, never ownership.

---

## The PluginInterface Contract

All plugins must implement one of two patterns:

### Pattern 1: Inherit from PluginInterface (Recommended)

```python
from corvin.core.plugins.corvin_plugins.plugin_interface import PluginInterface
from corvin.core.plugins.corvin_plugins.protocol import AuditBackend, PluginContext, HealthStatus

class MyAuditBackend(PluginInterface, AuditBackend):
    plugin_id = "my-audit-backend"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "My Audit Backend"

    def on_load(self, ctx: PluginContext) -> None:
        # Initialize
        ...

    def on_unload(self) -> None:
        # Cleanup
        ...

    def health_check(self) -> HealthStatus:
        # Return health status
        ...

    # Implement AuditBackend methods...
    def fanout(self, event_type: str, details: dict, **kwargs) -> None:
        ...
```

### Pattern 2: Implement the CorvinPlugin Protocol (Minimal)

```python
from corvin.core.plugins.corvin_plugins.protocol import CorvinPlugin, AuditBackend

class MyAuditBackend(AuditBackend):
    # Required attributes
    plugin_id: str = "my-audit-backend"
    plugin_type: str = "audit_backend"
    version: str = "1.0.0"
    display_name: str = "My Audit Backend"

    # Required methods
    def on_load(self, ctx: PluginContext) -> None: ...
    def on_unload(self) -> None: ...
    def health_check(self) -> HealthStatus: ...

    # Capability-specific methods
    def fanout(self, event_type: str, details: dict, **kwargs) -> None: ...
```

**Recommendation:** Use Pattern 1 (inherit from PluginInterface) for better IDE support,
type hints, and documentation. The base class is designed as a reference implementation.

---

## Required Attributes

Every plugin class must define these **class attributes** (not instance attributes):

| Attribute | Type | Length | Purpose |
|---|---|---|---|
| `plugin_id` | str | 1–64 | Unique identifier; lowercase, alphanumeric + `._-`. Matches registry key. |
| `plugin_type` | str | — | One of 13 KNOWN_PLUGIN_TYPES. Determines capability interface. |
| `version` | str | — | Semantic version (e.g., `1.0.0`). Must match manifest. |
| `display_name` | str | — | Human-readable name for logs/Console. English only. |

**Examples:**

```python
plugin_id = "postgres-audit-backend"      # Reverse-domain style OK
plugin_type = "audit_backend"             # Must be in KNOWN_PLUGIN_TYPES
version = "1.0.0"                         # Semantic version
display_name = "PostgreSQL Audit Backend" # Show in Console
```

---

## Required Methods

### 1. `on_load(ctx: PluginContext) -> None`

Called **exactly once** when the plugin is loaded. This is where you initialize.

**Contract:**

- Validate configuration from `ctx.config`
- Initialize external resources (DB, API, threads)
- Self-register with the capability registry via `ctx.<type>_registry`
- Emit an audit event via `ctx.audit_emit()`
- **Raise an exception if initialization fails** (prevents loading)
- Complete within 10 seconds

**Arguments:**

```python
class PluginContext:
    plugin_id: str                    # Your plugin's ID
    tenant_id: str                    # Tenant this was loaded for
    corvin_home: Path                 # Corvin home directory (~/.corvin)
    config: dict                      # Operator's settings for this plugin
    audit_emit: Callable              # Emit audit events
    
    # Capability-specific registries (use the one matching your plugin_type)
    audit_registry: Any               # For audit_backend plugins
    user_registry: Any                # For user_backend plugins
    notification_registry: Any        # For notification_backend plugins
    router_registry: Any              # For router_backend plugins
    # ... others for other types
```

**Example:**

```python
def on_load(self, ctx: PluginContext) -> None:
    # 1. Validate config
    required = {"api_key", "host"}
    missing = required - set(ctx.config.keys())
    if missing:
        raise ValueError(f"Missing config: {missing}")

    # 2. Initialize
    self.api_key = ctx.config["api_key"]
    self.host = ctx.config["host"]
    self.client = MyAPIClient(self.host, self.api_key)

    # 3. Self-register
    ctx.audit_registry.register(ctx.plugin_id, self)

    # 4. Audit the load
    ctx.audit_emit("plugin.loaded", {
        "plugin_id": ctx.plugin_id,
        "version": self.version,
        "type": self.plugin_type,
    })
```

### 2. `on_unload() -> None`

Called when the plugin is unloaded (shutdown, hot-reload, or explicit uninstall).

**Contract:**

- Release all external resources (close DB connections, stop threads, etc.)
- Complete quickly (target: < 100ms)
- **NEVER raise exceptions** (they are logged but not re-raised)
- **Idempotent** (safe to call multiple times)

**Example:**

```python
def on_unload(self) -> None:
    try:
        if hasattr(self, "client"):
            self.client.close()
    except Exception:
        # Don't raise; just silently close
        pass

    # Safe to call multiple times
    self.client = None
```

### 3. `health_check() -> HealthStatus`

Called **1–2 times per second** during normal operation. Used to detect failures.

**Contract:**

- Return **immediately** (max 2 seconds, target < 100ms)
- **Do NOT block I/O** (use cached state only)
- **Do NOT raise exceptions** (they are logged and the plugin marked unhealthy)
- Return `HealthStatus(ok=bool, message=str, details=dict)`

**The HealthStatus class:**

```python
@dataclass
class HealthStatus:
    ok: bool                              # True if healthy
    message: str = ""                     # One-line summary
    details: dict = field(default_factory=dict)  # Diagnostics
```

**Example:**

```python
def health_check(self) -> HealthStatus:
    try:
        if not hasattr(self, "client") or self.client is None:
            return HealthStatus(
                ok=False,
                message="Client not initialized",
            )

        # Check cached state, NOT network I/O
        if self.connection_failed:
            return HealthStatus(
                ok=False,
                message=f"Connection error: {self.last_error}",
                details={"retry_after": 60},
            )

        return HealthStatus(
            ok=True,
            message="Connected and operational",
            details={
                "requests_processed": self.request_count,
                "last_request": self.last_request_time,
            },
        )
    except Exception as e:
        # Never raise; return unhealthy
        return HealthStatus(
            ok=False,
            message=f"Exception: {type(e).__name__}",
        )
```

---

## Optional Methods

### `on_enable() -> None`

Called when the operator enables the plugin (after `on_load()`). Default: no-op.

Override to run additional setup that should only happen when explicitly enabled.

```python
def on_enable(self) -> None:
    logger.info(f"Enabled {self.display_name}")
    self.start_background_worker()
```

### `on_disable() -> None`

Called when the operator disables the plugin (before unload). Default: no-op.

Override to run teardown specific to disabling (vs. full unload). Idempotent.

```python
def on_disable(self) -> None:
    logger.info(f"Disabled {self.display_name}")
    self.stop_background_worker()
```

### `get_metrics() -> Dict[str, Any]`

Export plugin-specific metrics for observability. Default: empty dict.

Keys must be lowercase; values must be numbers (int/float) only. Keep < 50 entries.

```python
def get_metrics(self) -> Dict[str, Any]:
    return {
        "requests_processed": self.request_count,
        "errors_total": self.error_count,
        "latency_ms": self.avg_latency,
    }
```

---

## Capability Interfaces

Each plugin_type implements a specific protocol. The plugin class must implement
both `CorvinPlugin` (lifecycle) AND the capability-specific protocol.

### Example: AuditBackend

```python
class AuditBackend(Protocol):
    """Receive audit events for fan-out to external sinks."""

    def fanout(
        self,
        event_type: str,
        details: dict,
        *,
        severity: str = "INFO",
        tenant_id: str = "_default",
    ) -> None: ...

    def verify_chain(self) -> HealthStatus: ...

    def enforce_retention(self, max_age_days: int, *, tenant_id: str = "_default") -> dict: ...
```

**Implementation:**

```python
class MyAuditBackend(PluginInterface, AuditBackend):
    # ... lifecycle methods ...

    def fanout(self, event_type: str, details: dict, **kwargs) -> None:
        # Called by core AFTER it has written the event to audit.jsonl
        # We receive a COPY; never suppress or rewrite
        try:
            self.send_to_backend(event_type, details)
        except Exception as e:
            # Never raise; never block core
            logger.warning(f"fanout raised {type(e).__name__}")

    def verify_chain(self) -> HealthStatus:
        # Verify OUR copy of the audit chain (not the core chain)
        if not self.is_connected:
            return HealthStatus(ok=False, message="Not connected")
        return HealthStatus(ok=True, message="Backend chain intact")

    def enforce_retention(self, max_age_days: int, **kwargs) -> dict:
        # Delete events older than max_age_days
        return {"deleted": self.delete_old_events(max_age_days)}
```

### All Capability Protocols

See `core/plugins/corvin_plugins/protocol.py` for the full list:

- `NotificationBackend` — Send notifications
- `RecallBackend` — Store/retrieve conversation turns
- `RouterBackend` — Select personas
- `SummaryProvider` — Summarize for TTS
- `UserBackend` — Authenticate users
- `ComputeEngine` — Managed compute
- `WorkerEngine` — Custom LLM worker
- `BridgeChannel` — Bridge transport
- `STTProvider` — Speech-to-text
- `DataConnector` — External data
- `WebSurface` — Browser UI

---

## Validation

### At Development Time

Use the validation utilities to catch errors before deploying:

```python
from corvin.core.plugins.corvin_plugins.plugin_interface import (
    validate_plugin_class,
    PluginInterfaceValidationResult,
)

result = validate_plugin_class(MyAuditBackend, strict=True)
if not result.valid:
    for error in result.errors:
        print(f"ERROR: {error}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
```

### At Load Time

The loader validates every plugin before loading:

```bash
# Validate a manifest
corvin plugin validate ~/.corvin/plugins/installed/my-plugin/manifest.yaml

# Check health after loading
corvin plugin health my-plugin-id
```

---

## Manifest

Every plugin must have a `manifest.yaml` that declares:

```yaml
plugin_id: my-plugin-id          # Required: unique ID
version: 1.0.0                   # Required: semver
display_name: "My Plugin"        # Required: display name
plugin_type: audit_backend       # Required: one of KNOWN_PLUGIN_TYPES
author: "Your Name"              # Required
email: "your@email.com"          # Required
license: "Apache-2.0"            # Required

# Optional
boot_layer: installed            # When to load (default: installed)
origin: community                # Provenance (default: community)
pii_risk: low                    # PII exposure (default: low)
requires_consent: false          # Operator consent (default: false)
audit_required: true             # Audit trail (default: true)
locality: local                  # Where work happens
network_egress: external         # Network permissions
egress_hosts: []                 # Specific hosts to whitelist

# Settings schema (JSON Schema)
settings_schema:
  type: object
  properties:
    api_key:
      type: string
      description: "API key for authentication"
  required:
    - api_key

dependencies: []                 # Other plugins to load first
```

---

## Summary Checklist

- [ ] Class defines `plugin_id`, `plugin_type`, `version`, `display_name` as class attributes
- [ ] Class implements `on_load(ctx: PluginContext) -> None`
- [ ] Class implements `on_unload() -> None`
- [ ] Class implements `health_check() -> HealthStatus`
- [ ] Class implements all capability-specific methods for the `plugin_type`
- [ ] `__init__` takes no arguments (called as `PluginClass()`)
- [ ] `on_load()` validates config and raises on error
- [ ] `on_unload()` is idempotent and never raises
- [ ] `health_check()` returns in < 2 seconds and never raises
- [ ] Manifest.yaml defines all required fields
- [ ] Plugin type matches a value in `KNOWN_PLUGIN_TYPES`
- [ ] Unit tests cover lifecycle and capability methods
- [ ] No PII in logs, audit details, or config

---

## Security Guidelines

### Never

- Log user content, prompts, or transcripts
- Log configuration secrets (API keys, passwords)
- Log full error messages (exception class name only)
- Block in `health_check()` (max 2 seconds)
- Raise from `on_unload()`
- Store PII in plain text
- Suppress the core audit trail
- Escalate privilege via ContextVar

### Always

- Emit audit events for important actions
- Validate configuration in `on_load()`
- Release resources in `on_unload()`
- Return cached state in `health_check()`
- Handle exceptions gracefully
- Declare PII risk accurately
- Require consent if PII is involved

---

## Testing

Every plugin must have unit tests covering:

1. **Initialization** — `on_load()` with valid and invalid config
2. **Health check** — Various states (connected, disconnected, degraded)
3. **Capability methods** — Normal operation and error handling
4. **Cleanup** — `on_unload()` is idempotent

**Example test:**

```python
import pytest
from unittest.mock import MagicMock

def test_on_load_with_valid_config():
    plugin = MyAuditBackend()
    ctx = MagicMock()
    ctx.config = {"api_key": "test"}

    plugin.on_load(ctx)

    assert plugin.is_connected
    ctx.audit_registry.register.assert_called_once()
```

Run: `pytest test_plugin.py -v`

---

## Deployment

1. **Install:** Place plugin in `~/.corvin/plugins/installed/<plugin_id>/`
2. **Enable:** Run `corvin plugin enable <plugin_id>` or enable in Console
3. **Monitor:** Check health with `corvin plugin health <plugin_id>`
4. **Uninstall:** Run `corvin plugin uninstall <plugin_id>`

---

## References

- **ADR-0030** — Plugin Lifecycle (protocol)
- **ADR-0033** — Provider Backends (registry patterns)
- **ADR-0233** — Plugin System Consolidation (full design)
- **ADR-0243** — Plugin Boot Layers (load order + disableability)
- **ADR-0249** — Plugin Trust Anchors (signing + verification)
- **Code:** `core/plugins/corvin_plugins/plugin_interface.py` (base class)
- **Code:** `core/plugins/corvin_plugins/protocol.py` (protocols)
- **Code:** `core/plugins/corvin_plugins/manifest.py` (manifest)
- **Guide:** `docs/PLUGIN_DEVELOPMENT.md` (tutorial)
- **Examples:** `core/plugins/examples/` (complete examples)

---

**Questions?** See the [Plugin Development Guide](PLUGIN_DEVELOPMENT.md) or open an issue.
