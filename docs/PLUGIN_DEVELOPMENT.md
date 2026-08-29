# CorvinOS Plugin Development Guide

**Version:** 0.1  
**Status:** APPROVED (ADR-0030, ADR-0033, ADR-0233, ADR-0243, ADR-0249)  
**Last Updated:** 2026-08-29

This guide teaches you how to build, test, deploy, and maintain CorvinOS plugins.

---

## Table of Contents

1. [Plugin Anatomy](#1-plugin-anatomy)
2. [Lifecycle Stages](#2-lifecycle-stages)
3. [Extension Points](#3-extension-points)
4. [Dependencies](#4-dependencies)
5. [Testing Plugins](#5-testing-plugins)
6. [Deployment & Registration](#6-deployment--registration)
7. [Security & Compliance](#7-security--compliance)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Plugin Anatomy

Every CorvinOS plugin consists of three parts:

### 1.1 Manifest (`manifest.yaml`)

The manifest is the plugin's metadata card. It declares the plugin's identity, capabilities,
dependencies, and compliance requirements.

**Example: `my-audit-backend/manifest.yaml`**

```yaml
plugin_id: my-audit-backend
version: 1.0.0
display_name: "My Audit Backend"
plugin_type: audit_backend
boot_layer: installed
origin: community

# Compliance declarations
pii_risk: low
requires_consent: false
audit_required: true
locality: local
network_egress: none

# Dependency declaration
dependencies:
  - core-audit-interface>=1.0.0
  - postgres-driver

# Settings schema (JSON Schema)
settings_schema:
  type: object
  properties:
    db_host:
      type: string
      description: "PostgreSQL host"
      default: "localhost"
    db_port:
      type: integer
      description: "PostgreSQL port"
      default: 5432
    db_name:
      type: string
      description: "Database name"
  required:
    - db_host
    - db_name

# Author metadata
author: "Your Name"
email: "your.email@example.com"
homepage: "https://github.com/your-org/my-audit-backend"
license: "Apache-2.0"
```

**Required Fields:**

| Field | Type | Description |
|---|---|---|
| `plugin_id` | string | Globally unique, lowercase, alphanumeric + `-._`. Max 64 chars. Used as the registry key. |
| `version` | string | Semantic version (e.g., `1.0.0`). Must match the class attribute. |
| `display_name` | string | Human-readable name for Console UI and logs. English only. |
| `plugin_type` | string | One of KNOWN_PLUGIN_TYPES (see [Extension Points](#3-extension-points)). |
| `author` | string | Author/organization name. |
| `email` | string | Contact email. |
| `license` | string | SPDX license identifier (e.g., `Apache-2.0`, `MIT`). |

**Optional Fields:**

| Field | Type | Default | Description |
|---|---|---|---|
| `boot_layer` | string | `installed` | When to load (see [Boot Layers](#11-boot-layers)). |
| `origin` | string | `community` | Provenance: `builtin`, `vetted`, or `community`. |
| `pii_risk` | string | `low` | PII exposure level: `none`, `low`, `medium`, `high`. Consent gate. |
| `requires_consent` | bool | false | Operator must explicitly consent before enable. |
| `audit_required` | bool | true | Log events to the audit trail. |
| `locality` | string | `unknown` | Where work happens: `local`, `eu_cloud`, `us_cloud`, `unknown`. |
| `network_egress` | string | `external` | Network permissions: `none`, `local`, `external`. |
| `egress_hosts` | list[string] | `[]` | Specific hosts to whitelist (only if egress_hosts != external). |
| `homepage` | string | — | Plugin URL. |
| `dependencies` | list[string] | `[]` | Plugin dependencies (other plugin_ids, with optional version constraints). |
| `settings_schema` | object | `{}` | JSON Schema for the operator's config. |
| `settings_schema_version` | string | `1.0` | Schema version for migration tracking. |

### 1.2 Entry Point (Python Class)

The entry point is the Python class that implements the plugin interface. It must
inherit from `PluginInterface` or implement the `CorvinPlugin` protocol.

**Example: `my-audit-backend/plugin.py`**

```python
"""My Audit Backend plugin — forwards audit events to PostgreSQL."""

from pathlib import Path
from typing import Any, Dict, Optional

from corvin.core.plugins.corvin_plugins.plugin_interface import PluginInterface
from corvin.core.plugins.corvin_plugins.protocol import HealthStatus, PluginContext


class MyAuditBackend(PluginInterface):
    """Receive audit events and fan them out to PostgreSQL."""

    # ── Required attributes ───────────────────────────────────────────

    plugin_id = "my-audit-backend"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "My Audit Backend"

    def __init__(self):
        """Initialize with no arguments (called by the loader)."""
        self.db_conn = None
        self.request_count = 0
        self.error_count = 0

    # ── Lifecycle methods ─────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Called once when the plugin is loaded.

        Register with the audit registry and initialize the database connection.
        """
        # Validate configuration
        config = ctx.config
        required = {"db_host", "db_name"}
        missing = required - set(config.keys())
        if missing:
            raise ValueError(f"Missing required config: {missing}")

        # Initialize database connection
        try:
            import psycopg2

            self.db_conn = psycopg2.connect(
                host=config.get("db_host", "localhost"),
                port=config.get("db_port", 5432),
                database=config["db_name"],
                user=config.get("db_user", "corvin"),
                password=config.get("db_password", ""),
            )
            self._create_tables()
        except Exception as e:
            raise RuntimeError(f"Failed to connect to database: {e}") from e

        # Self-register with the audit backend registry
        ctx.audit_registry.register(self.plugin_id, self)

        # Emit audit event for this plugin loading
        ctx.audit_emit("plugin.loaded", {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "type": self.plugin_type,
            "target": "postgresql",
        })

    def on_unload(self) -> None:
        """Release database connection."""
        try:
            if self.db_conn:
                self.db_conn.close()
        except Exception as e:
            # Do not raise; just log
            print(f"Warning: Failed to close DB connection: {e}")

    def health_check(self) -> HealthStatus:
        """Return health status (called ~1-2 times per second)."""
        try:
            if not self.db_conn:
                return HealthStatus(
                    ok=False,
                    message="Database connection not initialized",
                )

            # Quick non-blocking check: is the connection still alive?
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()

            return HealthStatus(
                ok=True,
                message="PostgreSQL connection healthy",
                details={
                    "requests_processed": self.request_count,
                    "errors": self.error_count,
                },
            )
        except Exception as e:
            return HealthStatus(
                ok=False,
                message=f"Connection check failed: {type(e).__name__}",
            )

    def on_enable(self) -> None:
        """Called when the operator enables the plugin."""
        print(f"Enabled {self.display_name}")

    def on_disable(self) -> None:
        """Called when the operator disables the plugin."""
        print(f"Disabled {self.display_name}")

    # ── Capability-specific methods ───────────────────────────────────

    def fanout(
        self,
        event_type: str,
        details: Dict[str, Any],
        *,
        severity: str = "INFO",
        tenant_id: str = "_default",
    ) -> None:
        """Implement AuditBackend.fanout() — receive audit events.

        This is called by the core audit writer AFTER it has written the event
        to its own hash-chained audit.jsonl. We receive a COPY, never suppress
        or rewrite it.
        """
        if not self.db_conn:
            self.error_count += 1
            return

        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_events (event_type, details, severity, tenant_id, ts)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (event_type, str(details), severity, tenant_id),
            )
            self.db_conn.commit()
            cursor.close()
            self.request_count += 1
        except Exception as e:
            self.error_count += 1
            # Never raise; a failing backend never takes down core

    def verify_chain(self) -> HealthStatus:
        """Verify that OUR copy of the audit chain is intact.

        This is NOT consulted to verify the core chain — that is core's job.
        We only report on our own backend's copy.
        """
        try:
            if not self.db_conn:
                return HealthStatus(ok=False, message="Not connected")

            cursor = self.db_conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM audit_events")
            count = cursor.fetchone()[0]
            cursor.close()

            return HealthStatus(
                ok=True,
                message=f"{count} events in backend database",
            )
        except Exception as e:
            return HealthStatus(
                ok=False,
                message=f"Verification failed: {type(e).__name__}",
            )

    def enforce_retention(
        self, max_age_days: int, *, tenant_id: str = "_default"
    ) -> Dict[str, Any]:
        """Delete audit events older than max_age_days."""
        if not self.db_conn:
            return {"deleted": 0, "error": "Not connected"}

        try:
            cursor = self.db_conn.cursor()
            cursor.execute(
                """
                DELETE FROM audit_events
                WHERE ts < NOW() - INTERVAL %s DAY
                AND tenant_id = %s
                """,
                (max_age_days, tenant_id),
            )
            deleted = cursor.rowcount
            self.db_conn.commit()
            cursor.close()

            return {"deleted": deleted, "max_age_days": max_age_days}
        except Exception as e:
            return {"error": str(e)}

    # ── Helper methods ────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """Create the audit_events table if it doesn't exist."""
        cursor = self.db_conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details TEXT,
                severity VARCHAR(20),
                tenant_id VARCHAR(255),
                ts TIMESTAMP DEFAULT NOW()
            )
            """
        )
        self.db_conn.commit()
        cursor.close()

    def get_metrics(self) -> Dict[str, Any]:
        """Export metrics for the admin dashboard."""
        return {
            "requests_processed": self.request_count,
            "errors_total": self.error_count,
            "request_rate": self.request_count / 60.0 if self.request_count > 0 else 0,
        }
```

**Key Rules:**

- **No arguments in `__init__`** — the loader instantiates with `PluginClass()`.
- **All attributes must be strings** — plugin_id, plugin_type, version, display_name.
- **All methods must match signatures** — see the protocol.py for exact signatures.
- **Never raise from on_unload()** — exceptions are logged but not re-raised.
- **Never block in health_check()** — max 2 seconds, target <100ms.
- **Never store plugin_id in ContextVar** — use instance attributes.

### 1.3 Directory Layout

```
my-audit-backend/
├── manifest.yaml              # Manifest (required)
├── plugin.py                  # Entry point (required)
├── __init__.py                # Python package marker (optional)
├── backend/
│   ├── __init__.py
│   └── postgres.py            # Helper modules (optional)
├── tests/
│   ├── __init__.py
│   ├── test_plugin.py         # Plugin tests (required)
│   └── conftest.py
├── pyproject.toml             # Dependencies (optional, if plugin is distributable)
├── README.md                  # Documentation (optional)
└── LICENSE                    # License file (optional)
```

---

## 2. Lifecycle Stages

### 2.1 Discovery

The loader searches for plugin modules in:
- `~/.corvin/plugins/installed/`
- `~/.corvin/plugins/bundled/` (built-in plugins)

Each directory contains a manifest.yaml and a Python module.

### 2.2 Instantiation

The loader imports the plugin module and instantiates the class with **no arguments**:

```python
# The loader does this:
import my_audit_backend.plugin as plugin_module
instance = plugin_module.MyAuditBackend()  # No args!
```

### 2.3 on_load() — Initialization

Called once, synchronously, receives `PluginContext` with access to:

- `ctx.config` — the operator's settings for this plugin
- `ctx.plugin_id` — the plugin's ID (from registry key)
- `ctx.tenant_id` — the tenant this plugin was loaded for
- `ctx.corvin_home` — the Corvin home directory
- `ctx.audit_emit()` — emit audit events
- `ctx.<capability>_registry` — the registry for this plugin's type (e.g., ctx.audit_registry)

**Contract:**
- Validate config. Raise if invalid (prevents bootstrap).
- Initialize external resources (DB, network, files).
- Self-register with the capability registry.
- Emit an audit event.
- Do NOT take longer than 10 seconds.

### 2.4 on_enable() → on_disable() (Optional)

Called when the operator toggles enable/disable via Console or CLI.

- `on_enable()` — called after on_load(); initialize enable-specific state.
- `on_disable()` — called before unload; tear down enable-specific state.

Both are optional (default: no-op).

### 2.5 health_check() — Periodic Health Monitoring

Called **1-2 times per second** during normal operation.

**Contract:**
- **Must return immediately** — max 2 seconds total, target <100ms.
- **Must NOT block I/O** — use cached state only.
- **Must NOT raise** — exceptions are logged and the plugin marked unhealthy.
- Return `HealthStatus(ok=True/False, ...)`.

### 2.6 on_unload() — Graceful Shutdown

Called when:
- The operator explicitly unloads the plugin
- The tenant is hot-reloaded
- The CorvinOS process shuts down

**Contract:**
- Release all resources (DB connections, threads, files).
- Complete quickly (target: <100ms).
- **NEVER raise** — exceptions are logged but not re-raised.
- Idempotent (safe to call multiple times).

---

## 3. Extension Points

CorvinOS supports 13 plugin types (KNOWN_PLUGIN_TYPES). Each type implements a specific
capability interface.

### 3.1 Built-in Plugin Types

#### Backends (ADR-0033, ADR-0233)

| Type | Protocol | Purpose | Constraint |
|---|---|---|---|
| `audit_backend` | AuditBackend | Receive audit events for external sinks (S3, Postgres, SIEM) | Additive-only; never suppresses core write |
| `user_backend` | UserBackend | Authenticate users from an external directory (LDAP, OAuth) | Async; deny is only safe failure |
| `notification_backend` | NotificationBackend | Deliver event notifications to Slack, Discord, etc. | Non-blocking; 100ms max |
| `recall_backend` | RecallBackend | Store/retrieve PII-redacted conversation turns | Always receives redacted text only |
| `router_backend` | RouterBackend | Select a persona for incoming messages | Returns dict or None; never raises |
| `summary_provider` | SummaryProvider | Summarize long responses for TTS | Non-blocking; must not raise |

#### Compute & Workers (ADR-0029, ADR-0022)

| Type | Protocol | Purpose |
|---|---|---|
| `compute_engine` | ComputeEngine | L25 — managed compute for large data jobs |
| `worker_engine` | WorkerEngine | L22 — custom model/API caller (Claude API, Bedrock, local) |

#### Data & Channels (ADR-0024)

| Type | Protocol | Purpose |
|---|---|---|
| `bridge_channel` | BridgeChannel | Bridge transport (Discord, Slack, Telegram, etc.) |
| `stt_provider` | STTProvider | Speech-to-text transcription |
| `data_connector` | DataConnector | Connect to external data sources (databases, APIs, files) |

#### UI (ADR-0356)

| Type | Protocol | Purpose |
|---|---|---|
| `web_surface` | WebSurface | Browser UI surface mounted on the CorvinOS HTTP server |

### 3.2 Capability Interfaces

Each plugin type implements a specific protocol. Example: `audit_backend` must implement
the `AuditBackend` protocol.

**To find the protocol:**

```python
# In core/plugins/corvin_plugins/protocol.py:
from corvin_plugins.protocol import AuditBackend, NotificationBackend, ...

# The protocol defines the methods your class must implement
```

**Example: NotificationBackend**

```python
class NotificationBackend(Protocol):
    def notify(
        self,
        event: str,
        payload: dict,
        *,
        tenant_id: str = "_default",
        severity: str = "info",
    ) -> None: ...
```

To implement it:

```python
class MyNotificationBackend(PluginInterface, NotificationBackend):
    def notify(self, event: str, payload: dict, *, tenant_id: str = "_default", severity: str = "info") -> None:
        # Send the notification somewhere
        print(f"[{severity}] {event}: {payload}")
```

---

## 4. Dependencies

### 4.1 Declaring Dependencies

In `manifest.yaml`, list other plugins your plugin depends on:

```yaml
dependencies:
  - postgres-driver>=1.0.0
  - my-helper-lib
  - another-plugin>=2.0.0,<3.0.0
```

**Version Constraints:**

| Constraint | Meaning |
|---|---|
| `plugin-id` | Any version |
| `>=1.0.0` | Version 1.0.0 or higher |
| `==1.0.0` | Exactly version 1.0.0 |
| `~=1.2.3` | Compatible release (1.2.3 to 1.9.x, but not 2.0.0) |
| `1.x` | Any version in the 1.x line |
| `<2.0.0` | Before version 2.0.0 |

### 4.2 Resolving Dependencies

The loader:
1. Topologically sorts plugins by dependency
2. Loads dependencies before dependents
3. Raises `DependencyConflictError` if cycles or missing dependencies are found
4. Calls `on_load()` only after all dependencies are loaded

### 4.3 Python Package Dependencies

If your plugin needs external Python packages (not other plugins), list them in
`pyproject.toml` or `requirements.txt`:

```toml
# pyproject.toml
[project]
dependencies = [
    "psycopg2-binary>=2.8",
    "requests>=2.25",
]
```

The operator must install these before enabling the plugin. The loader does NOT
install them automatically.

---

## 5. Testing Plugins

### 5.1 Unit Tests

Test the plugin class in isolation.

**Example: `tests/test_plugin.py`**

```python
"""Unit tests for MyAuditBackend plugin."""

import pytest
from unittest.mock import MagicMock, patch

from my_audit_backend.plugin import MyAuditBackend
from corvin.core.plugins.corvin_plugins.protocol import HealthStatus, PluginContext


class TestMyAuditBackend:
    """Test suite for MyAuditBackend."""

    @pytest.fixture
    def plugin(self):
        """Create a fresh plugin instance."""
        return MyAuditBackend()

    @pytest.fixture
    def mock_context(self):
        """Create a mock PluginContext."""
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {
            "db_host": "localhost",
            "db_port": 5432,
            "db_name": "corvin_test",
        }
        ctx.plugin_id = "my-audit-backend"
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()
        ctx.audit_registry = MagicMock()
        return ctx

    def test_on_load_success(self, plugin, mock_context):
        """Test successful initialization."""
        with patch("my_audit_backend.plugin.psycopg2.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            # on_load should not raise
            plugin.on_load(mock_context)

            # Should register with the registry
            mock_context.audit_registry.register.assert_called_once_with(
                "my-audit-backend", plugin
            )

            # Should emit an audit event
            mock_context.audit_emit.assert_called_once()
            args, kwargs = mock_context.audit_emit.call_args
            assert args[0] == "plugin.loaded"

    def test_on_load_missing_config(self, plugin, mock_context):
        """Test that on_load raises if config is incomplete."""
        mock_context.config = {"db_host": "localhost"}  # Missing db_name

        with pytest.raises(ValueError, match="Missing required config"):
            plugin.on_load(mock_context)

    def test_health_check_when_connected(self, plugin, mock_context):
        """Test health_check returns ok=True when connected."""
        with patch("my_audit_backend.plugin.psycopg2.connect"):
            plugin.on_load(mock_context)
            status = plugin.health_check()

            assert status.ok is True
            assert "healthy" in status.message.lower()

    def test_health_check_when_disconnected(self, plugin):
        """Test health_check returns ok=False when not connected."""
        plugin.db_conn = None
        status = plugin.health_check()

        assert status.ok is False
        assert "not initialized" in status.message.lower()

    def test_on_unload_closes_connection(self, plugin, mock_context):
        """Test that on_unload closes the DB connection."""
        with patch("my_audit_backend.plugin.psycopg2.connect"):
            plugin.on_load(mock_context)
            plugin.on_unload()

            plugin.db_conn.close.assert_called_once()

    def test_on_unload_idempotent(self, plugin):
        """Test that on_unload can be called multiple times safely."""
        # Should not raise even if db_conn is None
        plugin.on_unload()
        plugin.on_unload()  # Second call should also not raise
```

### 5.2 Integration Tests

Test the plugin with real or test doubles of external systems.

```python
"""Integration tests for MyAuditBackend with a test database."""

import pytest
import psycopg2
from corvin.core.plugins.corvin_plugins.protocol import PluginContext


@pytest.fixture
def test_db():
    """Create a test PostgreSQL database."""
    # Connect to the test database
    conn = psycopg2.connect(
        host="localhost",
        database="postgres",
        user="postgres",
        password="",
    )
    cursor = conn.cursor()
    cursor.execute("CREATE DATABASE corvin_test_audit")
    conn.commit()
    cursor.close()
    conn.close()

    yield "corvin_test_audit"

    # Cleanup
    conn = psycopg2.connect(
        host="localhost",
        database="postgres",
        user="postgres",
        password="",
    )
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute("DROP DATABASE corvin_test_audit")
    cursor.close()
    conn.close()


def test_fanout_writes_to_db(plugin, mock_context, test_db):
    """Test that fanout() actually writes to the database."""
    mock_context.config["db_name"] = test_db
    
    # Initialize the plugin
    plugin.on_load(mock_context)

    # Send an audit event
    plugin.fanout(
        event_type="user.login",
        details={"user_id": "123", "ip": "192.168.1.1"},
        severity="INFO",
    )

    # Verify it was written to the database
    cursor = plugin.db_conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM audit_events")
    count = cursor.fetchone()[0]
    cursor.close()

    assert count == 1
```

### 5.3 E2E Tests

Test the plugin within the full CorvinOS runtime.

```python
"""E2E test for MyAuditBackend loaded by CorvinOS."""

import pytest
from corvin.core.plugins.corvin_plugins.registry import PluginRegistry
from corvin.core.plugins.corvin_plugins.loader import PluginLoader


def test_plugin_loads_in_registry(corvin_home_fixture):
    """Test that the plugin is discovered and loaded by the registry."""
    registry = PluginRegistry(corvin_home_fixture / "plugins" / "registry.yaml")
    loader = PluginLoader(registry, corvin_home_fixture)

    # Load all plugins
    loaded = loader.load_all()

    # Check that our plugin was loaded
    assert "my-audit-backend" in loaded
    plugin = loaded["my-audit-backend"]
    assert plugin.plugin_type == "audit_backend"
    assert plugin.health_check().ok is True
```

**Running Tests:**

```bash
# Unit tests only
pytest tests/ -k "unit"

# Integration tests (requires test database)
pytest tests/ -k "integration"

# All tests
pytest tests/

# With coverage
pytest tests/ --cov=my_audit_backend --cov-report=html
```

---

## 6. Deployment & Registration

### 6.1 Directory Structure

Place your plugin in `~/.corvin/plugins/installed/<plugin_id>/`:

```bash
~/.corvin/plugins/
├── installed/
│   └── my-audit-backend/
│       ├── manifest.yaml
│       └── plugin.py
└── registry.yaml
```

### 6.2 Manual Registration

Edit `~/.corvin/plugins/registry.yaml` and add a record:

```yaml
installed:
  - plugin_id: my-audit-backend
    version: 1.0.0
    display_name: "My Audit Backend"
    plugin_type: audit_backend
    boot_layer: installed
    origin: community
    enabled: true
    settings:
      db_host: "localhost"
      db_port: 5432
      db_name: "corvin_audit"
```

### 6.3 CLI Registration

```bash
# Install a plugin from a directory
corvin plugin install ./my-audit-backend/

# Enable a plugin
corvin plugin enable my-audit-backend

# List installed plugins
corvin plugin list

# Health check a plugin
corvin plugin health my-audit-backend

# Uninstall a plugin
corvin plugin uninstall my-audit-backend
```

### 6.4 Console Registration (Web UI)

1. Open the CorvinOS Console
2. Navigate to **Settings → Plugins**
3. Click **Install Plugin**
4. Upload the plugin directory as a tarball (or use CLI)
5. Configure settings
6. Enable the plugin

### 6.5 Signature Verification (ADR-0249)

If the plugin is signed by the maintainer (origin=vetted):

```bash
# Verify the signature
corvin plugin verify my-plugin.tar.gz

# The public key must be in ~/.corvin/global/plugin_trust_anchors.txt
```

---

## 7. Security & Compliance

### 7.1 PII Protection

**Plugin manifest declares PII risk:**

```yaml
pii_risk: high  # Declares that the plugin may see user data
requires_consent: true  # Operator must consent before enable
```

**Plugin code must NOT:**

- Log user content, prompts, or transcripts
- Send PII to external systems without encryption
- Store PII in plain text on disk
- Include PII in audit events (use hashes instead)

**Example: Safe logging**

```python
# ✗ BAD: logs user content
logger.info(f"Processing message: {user_message}")

# ✓ GOOD: logs only a hash
import hashlib
msg_hash = hashlib.sha256(user_message.encode()).hexdigest()[:8]
logger.info(f"Processing message: {msg_hash}")
```

### 7.2 Consent Gate (ADR-0233)

If your plugin has `pii_risk: high` or `requires_consent: true`, the operator
must explicitly consent before the plugin can be enabled.

The loader checks `consent_required()` and prompts:

```
This plugin may access personal data. Do you consent?
[YES / NO]
```

### 7.3 Audit Trail (ADR-0232)

Every plugin action is hash-chained in `audit.jsonl`:

```json
{"event": "plugin.loaded", "plugin_id": "my-audit-backend", "ts": "2026-08-29T..."}
{"event": "plugin.fanout", "event_type": "user.login", "ts": "2026-08-29T..."}
{"event": "plugin.error", "plugin_id": "my-audit-backend", "error_type": "ConnectionError"}
```

**Never include in audit details:**

- User content or prompts
- Configuration secrets (API keys, passwords)
- Full error messages (exception class name only)

### 7.4 Boot Layers (ADR-0243)

Your plugin declares when it loads:

| Boot Layer | When | Disableable | Replaceable |
|---|---|---|---|
| `compliance` | First, before everything | NO | NO |
| `core` | Early, core systems | NO | YES (by boot_layer=core replacement) |
| `bundled` | Standard boot | YES | NO |
| `installed` | Last (default for community) | YES | NO |

**Community plugins can ONLY declare `installed`.**

### 7.5 Network Permissions (ADR-0035)

Declare your network needs:

```yaml
network_egress: external  # Needs internet access
egress_hosts:  # (optional) Whitelist specific hosts
  - api.example.com
  - db.company.local

# OR

network_egress: local  # Only local/LAN
# OR
network_egress: none  # No network access
```

### 7.6 Locality (ADR-0124)

Declare where your work happens:

```yaml
locality: eu_cloud     # EU-jurisdiction infrastructure
# OR
locality: local        # Local machine / LAN
# OR
locality: unknown      # Not classified (defaults to most-restrictive)
```

---

## 8. Troubleshooting

### 8.1 Plugin Won't Load

**Check the error message:**

```bash
corvin plugin health my-plugin
```

**Common errors:**

| Error | Cause | Fix |
|---|---|---|
| `InvalidPluginID` | plugin_id has forbidden chars | Use lowercase, alphanumeric + `._-` |
| `UnknownPluginType` | plugin_type not in KNOWN_PLUGIN_TYPES | Check manifest; use `corvin plugin list-types` |
| `DependencyConflictError` | Missing or incompatible dependency | Install the dependency first |
| `ValidationError` | Settings don't match schema | Check the schema in manifest.yaml |
| `Cannot instantiate with no arguments` | `__init__` requires arguments | Remove parameters from `__init__` |
| `Manifest load error` | YAML syntax error | Validate manifest.yaml with `yamllint` |

### 8.2 Plugin Crashes After Loading

**Check health:**

```bash
corvin plugin health my-plugin
```

**Check logs:**

```bash
journalctl -u corvin-service | grep my-plugin
# OR
tail ~/.corvin/logs/corvin.log | grep my-plugin
```

**Common causes:**

- `on_load()` raises an exception → fix the initialization logic
- External resource (database, API) is unreachable → check network
- Config is invalid → re-validate in Console UI
- Python import error → check dependencies in pyproject.toml

### 8.3 Plugin Health Check Fails

**Check implementation:**

- health_check() must NOT block > 2 seconds
- health_check() must NOT raise exceptions
- health_check() must return HealthStatus
- health_check() should check cached state, not do I/O

**Test locally:**

```python
# In a Python REPL
from my_plugin import MyPlugin
p = MyPlugin()
ctx = mock_context(...)
p.on_load(ctx)

# Simulate many health checks
import time
for _ in range(100):
    start = time.time()
    status = p.health_check()
    elapsed = time.time() - start
    print(f"health_check took {elapsed*1000:.1f}ms: {status.ok}")
    time.sleep(0.01)
```

### 8.4 "Permission Denied" When Installing

**On Linux/macOS:**

```bash
# Make sure the plugin directory is readable
chmod 755 ~/.corvin/plugins/installed/my-plugin/
chmod 644 ~/.corvin/plugins/installed/my-plugin/*
```

**On Windows:**

```powershell
# Ensure the plugin directory is not restricted
icacls $env:APPDATA\corvin\plugins\installed\my-plugin /grant Users:F /T
```

### 8.5 Plugin Shows "Stale" in Console

The Console caches plugin list. Hard-refresh:

```bash
# On the Console machine
Ctrl+Shift+R  # Chrome/Firefox
Cmd+Shift+R   # macOS
```

Or in the Console settings, toggle `console_auto_reload`.

---

## Summary Checklist

- [ ] **Manifest** — plugin_id, version, plugin_type, author, license all defined
- [ ] **Entry point** — Class inherits from PluginInterface or implements CorvinPlugin
- [ ] **Attributes** — plugin_id, plugin_type, version, display_name are class attributes
- [ ] **on_load()** — Validates config, initializes resources, self-registers
- [ ] **on_unload()** — Releases resources, idempotent, never raises
- [ ] **health_check()** — Returns quickly (< 2s), never blocks, never raises
- [ ] **Capability methods** — Implements all methods for the plugin_type (e.g., fanout() for audit_backend)
- [ ] **Tests** — Unit tests for the class, integration tests for external systems
- [ ] **Security** — No PII in logs, audit trail enabled, consent declared if needed
- [ ] **Documentation** — README with setup instructions and examples
- [ ] **Dependencies** — Listed in manifest.yaml and pyproject.toml

---

## Additional Resources

- **ADR-0030** — Plugin Lifecycle
- **ADR-0033** — Provider Backends
- **ADR-0233** — Plugin System Consolidation
- **ADR-0243** — Plugin Boot Layers
- **ADR-0249** — Plugin Trust Anchors (signing & verification)
- **Reference:** `core/plugins/corvin_plugins/protocol.py` — Protocol definitions
- **Templates:** `core/plugins/templates/` — Example plugins for each type
- **API Docs:** `core/plugins/corvin_plugins/plugin_interface.py` — PluginInterface class

---

**Questions?** Open an issue on the CorvinOS GitHub repository.
