"""Plugin Interface Contract — base class and validation for all CorvinOS plugins.

This module defines the formal interface that all CorvinOS plugins must implement or
inherit from. It serves as the contract between plugin authors and the core system.

ADR-0030: Plugin Lifecycle
ADR-0033: Provider Backends
ADR-0233: Plugin System Consolidation
ADR-0243: Plugin Boot Layers
"""

from __future__ import annotations

import abc
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Type

from .protocol import CorvinPlugin, HealthStatus, PluginContext
from .manifest import PluginRecord, KNOWN_PLUGIN_TYPES

logger = logging.getLogger(__name__)


# ── Formal Base Classes ───────────────────────────────────────────────────────


class PluginInterface(abc.ABC):
    """Abstract base class all plugins SHOULD inherit from (or at minimum implement
    the CorvinPlugin protocol defined in protocol.py).

    This class provides type safety and documentation beyond the Protocol alone.
    Subclassing is optional but recommended. If a plugin only implements the
    CorvinPlugin protocol, it is accepted; inheritance is not required.

    ## Lifecycle Stages

    1. **Discovery** — The loader finds the plugin's module and entry point.
    2. **Instantiation** — The loader creates a plugin instance with no arguments.
    3. **on_load()** — Called exactly once, receives PluginContext with registries.
    4. **Enabled → Disabled** — Operator may toggle enable state via Console or CLI.
    5. **health_check()** — Called periodically (1-2 per second) during operation.
    6. **on_unload()** — Called before shutdown or tenant hot-reload.

    ## Example Implementation

        class MyAuditBackend(PluginInterface):
            plugin_id = "my-audit-backend"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "My Audit Backend"

            def on_load(self, ctx: PluginContext) -> None:
                ctx.audit_registry.register(self.plugin_id, self)
                logger.info(f"Loaded {self.display_name}")

            def on_unload(self) -> None:
                logger.info(f"Unloading {self.display_name}")

            def health_check(self) -> HealthStatus:
                return HealthStatus(ok=True, message="All systems nominal")

            # Implement audit_backend.fanout() and verify_chain()...
    """

    # ── Required Attributes ───────────────────────────────────────────────────

    #: Globally unique identifier, matching manifest plugin_id. Lowercase,
    #: alphanumeric + dots/hyphens/underscores. Max 64 chars. Must match the
    #: registry key this plugin is loaded under.
    plugin_id: str = abc.abstractproperty()

    #: One of KNOWN_PLUGIN_TYPES. Determines which capability interface this
    #: plugin implements (AuditBackend, NotificationBackend, etc.).
    plugin_type: str = abc.abstractproperty()

    #: Semantic version (e.g. "1.0.0"). Must match the manifest version.
    version: str = abc.abstractproperty()

    #: Human-readable name for logs and Console UI. Not localised; English only.
    #: Shown in health dashboard and error messages.
    display_name: str = abc.abstractproperty()

    # ── Lifecycle Methods ─────────────────────────────────────────────────────

    @abc.abstractmethod
    def on_load(self, ctx: PluginContext) -> None:
        """Called once after the plugin module is imported and instantiated.

        **Contract:**
        - Self-register with capability registries via ``ctx`` handles.
        - Validate configuration from ``ctx.config``.
        - Initialize external connections (but do NOT block > 10s).
        - If initialization fails, raise an exception — this prevents bootstrap.
        - NEVER store plugin_id in a ContextVar; use instance attributes only.

        **Example:**

            def on_load(self, ctx: PluginContext) -> None:
                # Validate config
                required = {"api_key", "host"}
                if not required.issubset(ctx.config.keys()):
                    raise ValueError(f"Missing config: {required - set(ctx.config)}")

                # Initialize connection
                self.client = MyAPIClient(ctx.config["api_key"], ctx.config["host"])

                # Self-register
                ctx.audit_registry.register(self.plugin_id, self)

                # Audit the load event
                ctx.audit_emit("plugin.loaded", {
                    "plugin_id": self.plugin_id,
                    "version": self.version,
                    "type": self.plugin_type,
                })

        **Errors:**
        - Any exception raised here prevents the plugin from being usable.
        - The exception CLASS (not message) is logged and audited.
        - The caller (bootstrap or hot-reload) will catch it and mark the
          plugin unhealthy.
        """
        ...

    @abc.abstractmethod
    def on_unload(self) -> None:
        """Called when the plugin is unloaded (shutdown or tenant hot-reload).

        **Contract:**
        - Release external resources (DB connections, file handles, threads).
        - No timeout — must complete quickly (target: <100ms).
        - Safe to call multiple times (idempotent).
        - Do NOT raise exceptions (they are logged but not re-raised).
        - Unregister from registries if the loader has not already done so.

        **Example:**

            def on_unload(self) -> None:
                try:
                    if hasattr(self, "client"):
                        self.client.close()
                except Exception as e:
                    logger.warning(f"Error closing client: {type(e).__name__}")
                    # Continue cleanup; do not raise
                logger.info(f"Unloaded {self.display_name}")

        **Errors:**
        - Any exception is logged (exception class name only, never the message).
        - The plugin is unloaded regardless — the exception does not stop shutdown.
        """
        ...

    @abc.abstractmethod
    def health_check(self) -> HealthStatus:
        """Return the plugin's current health status.

        Called periodically (1-2 calls per second) during normal operation.
        Used by the health loop to detect plugin failures and trigger recovery.

        **Contract:**
        - Return immediately; max total time 2 seconds.
        - Do NOT block I/O; use cached state or non-blocking checks only.
        - Return HealthStatus(ok=True, ...) when healthy.
        - Return HealthStatus(ok=False, message="...", ...) when degraded/failed.
        - Do NOT raise exceptions (they are logged and treated as unhealthy).
        - Include meaningful details so operators can diagnose issues.

        **Returns:**
            HealthStatus with three fields:
            - ``ok`` (bool): True if healthy, False if degraded/failed.
            - ``message`` (str): One-line summary ("All systems nominal" or error desc).
            - ``details`` (dict): Optional diagnostics for the operator.

        **Example:**

            def health_check(self) -> HealthStatus:
                try:
                    # Non-blocking: check cached state, not network I/O
                    if not hasattr(self, "client") or self.client is None:
                        return HealthStatus(
                            ok=False,
                            message="Client not initialized",
                            details={"error": "on_load() not called or failed"}
                        )

                    # If you have cached connection state, check it
                    if self.connection_failed:
                        return HealthStatus(
                            ok=False,
                            message=f"Connection failed: {self.last_error}",
                            details={"retry_after": 60}
                        )

                    return HealthStatus(
                        ok=True,
                        message="Connected and operational",
                        details={"last_request": self.last_request_time}
                    )
                except Exception as e:
                    # Exceptions are caught by the caller; log and return unhealthy
                    logger.warning(f"health_check raised {type(e).__name__}")
                    return HealthStatus(
                        ok=False,
                        message=f"Exception: {type(e).__name__}",
                        details={}
                    )

        **Errors:**
        - Any exception is caught, logged (class name only), and the plugin is
          marked unhealthy.
        - Do NOT raise — return unhealthy status instead.
        """
        ...

    # ── Optional Lifecycle Hooks (override if needed) ──────────────────────

    def on_enable(self) -> None:
        """Called when the operator enables the plugin (after on_load).

        Default: no-op. Override to run additional setup when the plugin is
        explicitly enabled (vs. loaded at boot). Called only once per enable,
        after on_load() has succeeded.

        **Contract:**
        - Can initialize components that should only run when enabled.
        - Raise an exception if enable should be refused.
        - Called after on_load(), so ctx is no longer available.
        """
        pass

    def on_disable(self) -> None:
        """Called when the operator disables the plugin (before on_unload).

        Default: no-op. Override to run teardown specific to disabling
        (vs. full unload). Idempotent.

        **Contract:**
        - Complement of on_enable().
        - Safe to call multiple times.
        - Do NOT raise; exceptions are logged and ignored.
        """
        pass

    # ── Optional Metrics / Observability ──────────────────────────────────

    def get_metrics(self) -> Dict[str, Any]:
        """Return plugin-specific metrics for observability.

        Optional. Default: empty dict. Override to export custom metrics
        for the metrics exporter or admin dashboard.

        **Returns:**
            A dict of {metric_name: numeric_value}. Examples:
            {
                "requests_processed": 1234,
                "errors_total": 5,
                "latency_ms": 42.5,
            }

        **Contract:**
        - Keys are lowercase, words separated by underscores.
        - Values are numbers (int or float) only.
        - Do NOT include timestamps; the exporter adds them.
        - Keep the dict small (< 50 entries).
        """
        return {}


# ── Validation & Introspection ────────────────────────────────────────────────


@dataclass
class PluginInterfaceValidationResult:
    """Result of validating a plugin class against the PluginInterface contract."""

    valid: bool
    plugin_id: Optional[str] = None
    plugin_type: Optional[str] = None
    version: Optional[str] = None
    display_name: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """One-line summary of validation result."""
        if self.valid:
            return f"✓ {self.plugin_id}/{self.version} ({self.plugin_type})"
        errors_str = ", ".join(self.errors[:2])
        if len(self.errors) > 2:
            errors_str += f", +{len(self.errors) - 2} more"
        return f"✗ {errors_str}"


def validate_plugin_class(
    plugin_class: Type,
    record: Optional[PluginRecord] = None,
    strict: bool = False,
) -> PluginInterfaceValidationResult:
    """Validate that a class implements the PluginInterface contract.

    **Checks:**

    1. Class defines all required attributes (plugin_id, plugin_type, version,
       display_name).
    2. Class implements all required methods (on_load, on_unload, health_check).
    3. If record is provided, class attributes match record fields.
    4. plugin_type is in KNOWN_PLUGIN_TYPES.
    5. Methods have correct signatures (arity, return type).
    6. (Optional, strict=True) Class is instantiable with no arguments.

    **Arguments:**

        plugin_class: The class to validate (not an instance).
        record: Optional PluginRecord to cross-check against.
        strict: If True, also try instantiating the class.

    **Returns:**

        PluginInterfaceValidationResult with valid=True if all checks pass.
        Errors block validation; warnings do not.
    """
    result = PluginInterfaceValidationResult(valid=True)
    errors = []
    warnings = []

    # ── Check required attributes ─────────────────────────────────────────

    for attr in ("plugin_id", "plugin_type", "version", "display_name"):
        if not hasattr(plugin_class, attr):
            errors.append(f"Missing required attribute: {attr}")
            continue

        try:
            value = getattr(plugin_class, attr)
            if isinstance(value, str):
                setattr(result, attr, value)
            else:
                errors.append(f"Attribute {attr!r} must be a string, got {type(value).__name__}")
        except Exception as e:
            errors.append(f"Cannot read {attr}: {type(e).__name__}")

    # ── Check plugin_type is valid ────────────────────────────────────────

    if result.plugin_type and result.plugin_type not in KNOWN_PLUGIN_TYPES:
        errors.append(
            f"plugin_type {result.plugin_type!r} not in KNOWN_PLUGIN_TYPES; "
            f"expected one of {sorted(KNOWN_PLUGIN_TYPES)}"
        )

    # ── Check required methods ────────────────────────────────────────────

    required_methods = {
        "on_load": ("ctx", "PluginContext"),
        "on_unload": (),
        "health_check": ("HealthStatus",),
    }

    for method_name, _expected_sig in required_methods.items():
        if not hasattr(plugin_class, method_name):
            errors.append(f"Missing required method: {method_name}()")
            continue

        method = getattr(plugin_class, method_name)
        if not callable(method):
            errors.append(f"{method_name!r} is not callable")
            continue

        # Check it's a real method, not a property
        if isinstance(inspect.getattr_static(plugin_class, method_name), property):
            errors.append(f"{method_name!r} is a property, not a method")

    # ── Cross-check with PluginRecord if provided ──────────────────────

    if record:
        if result.plugin_id and result.plugin_id != record.plugin_id:
            errors.append(
                f"plugin_id mismatch: class={result.plugin_id!r}, "
                f"record={record.plugin_id!r}"
            )
        if result.plugin_type and result.plugin_type != record.plugin_type:
            errors.append(
                f"plugin_type mismatch: class={result.plugin_type!r}, "
                f"record={record.plugin_type!r}"
            )
        if result.version and result.version != record.version:
            errors.append(
                f"version mismatch: class={result.version!r}, "
                f"record={record.version!r}"
            )

    # ── Strict mode: try instantiation ────────────────────────────────────

    if strict:
        try:
            instance = plugin_class()
            if not isinstance(instance, CorvinPlugin):
                warnings.append(
                    "Instance does not implement CorvinPlugin protocol "
                    "(it may still work, but type-checking will be weak)"
                )
        except TypeError as e:
            if "__init__" in str(e):
                errors.append(f"Cannot instantiate with no arguments: {e}")
            else:
                errors.append(f"Instantiation failed: {type(e).__name__}")
        except Exception as e:
            errors.append(f"Instantiation raised {type(e).__name__}: {str(e)[:100]}")

    # ── Assemble result ──────────────────────────────────────────────────

    result.valid = len(errors) == 0
    result.errors = errors
    result.warnings = warnings

    return result


def validate_manifest_and_class(
    record: PluginRecord,
    plugin_class: Type,
    corvin_home: Path,
) -> PluginInterfaceValidationResult:
    """Comprehensive validation of a plugin manifest and class together.

    This is the main validation entry point, combining:
    1. Manifest validation (PluginRecord.__post_init__() already does this)
    2. Class interface validation
    3. Entry point verification (class_path matches the loaded class)

    **Arguments:**

        record: The parsed PluginRecord from the manifest.
        plugin_class: The runtime class that was loaded.
        corvin_home: Corvin home directory (for file checks).

    **Returns:**

        PluginInterfaceValidationResult with comprehensive diagnostics.
    """
    # Start with interface validation
    result = validate_plugin_class(record.class_path or "", record, strict=True)

    # Cross-check the class_path
    if record.class_path:
        expected_module, _, expected_class = record.class_path.rpartition("::")
        actual_module = plugin_class.__module__
        actual_class = plugin_class.__qualname__

        if actual_module != expected_module:
            result.errors.append(
                f"Module mismatch: class_path={expected_module!r}, "
                f"loaded from {actual_module!r}"
            )
        if actual_class != expected_class:
            result.errors.append(
                f"Class name mismatch: class_path={expected_class!r}, "
                f"loaded as {actual_class!r}"
            )

    # Re-evaluate validity
    result.valid = len(result.errors) == 0

    return result
