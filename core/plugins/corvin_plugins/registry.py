"""PluginRegistry — discovery, lifecycle, health aggregation (ADR-0030)."""
from __future__ import annotations

import logging
import threading

from . import circuit_breaker as _breakers
from .protocol import (
    CorvinPlugin,
    HealthStatus,
    PluginAlreadyRegistered,
    PluginContext,
    PluginNotFound,
)

log = logging.getLogger(__name__)


def _structured(plugin_id: str | None = None):
    """A CorvinLogger when the observability package is present, else None.

    Optional exactly like cowork/forge: the plugin registry must keep working in a
    layout that ships without core/observability.
    """
    try:
        import sys as _sys
        from pathlib import Path as _Path

        obs = _Path(__file__).resolve().parents[2] / "observability"
        if obs.is_dir() and str(obs) not in _sys.path:
            # append, not insert(0) — same shadowing reasoning as elsewhere.
            _sys.path.append(str(obs))
        from corvin_logging import get_logger  # type: ignore[import-not-found]

        return get_logger("plugins", plugin_id)
    except Exception:  # noqa: BLE001
        return None


#: Cap on plugin-supplied health text. The audit chain is append-only and
#: hash-chained: a record cannot be edited afterwards (rewriting audit.jsonl breaks
#: the chain), so an oversized or leaky message is permanent.
MAX_STATUS_MESSAGE_CHARS = 240


def _scrub_plugin_text(value: str) -> str:
    """Redact PII shapes in plugin-supplied health text.  Fails CLOSED.

    ``health_check()`` returning ``ok=False`` with a helpful diagnostic is the
    NORMAL path, and its message flows into (a) the hash-chained audit log via
    plugin.health_alert, (b) the healing record's reason via plugin.healing_action,
    (c) the Console, (d) the log stream. The exception path is already reduced to a
    class name; the cooperative path was passed through verbatim, so a plugin author
    writing "auth failed for alice@corp.com" or "cannot reach
    postgres://u:pw@host/db" wrote personal data into a record nobody can redact
    later.

    When the scrubber is unavailable the free text is DROPPED, not forwarded: losing
    a diagnostic string is recoverable, an un-redactable audit record is not.
    """
    if not isinstance(value, str) or not value:
        return ""
    clipped = value[:MAX_STATUS_MESSAGE_CHARS]
    try:
        from corvin_logging.scrubber import scrub_text  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        _structured()  # side effect: puts core/observability on sys.path
        try:
            from corvin_logging.scrubber import scrub_text  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            return "[health message withheld — scrubber unavailable]"
    scrubbed, _ = scrub_text(clipped)
    if len(value) > MAX_STATUS_MESSAGE_CHARS:
        scrubbed += f"…[+{len(value) - MAX_STATUS_MESSAGE_CHARS} chars]"
    return scrubbed


def _scrub_plugin_details(details: object) -> dict:
    """Same gate for the plugin's free-form details dict (shown in the Console)."""
    if not isinstance(details, dict):
        return {}
    try:
        from corvin_logging.scrubber import scrub  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        _structured()
        try:
            from corvin_logging.scrubber import scrub  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            return {"withheld": "scrubber unavailable"}
    cleaned, _ = scrub(details)
    return cleaned if isinstance(cleaned, dict) else {}


class PluginRegistry:
    """Thread-safe registry for CorvinPlugin instances.

    Handles registration, lifecycle calls, health aggregation, and
    typed lookups.  One global instance is provided as ``_registry``
    at module level; convenience functions wrap it.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, CorvinPlugin] = {}
        self._contexts: dict[str, PluginContext] = {}
        self._lock = threading.Lock()

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, plugin: CorvinPlugin, ctx: PluginContext) -> None:
        """Call plugin.on_load(ctx) and store the plugin.

        Raises PluginAlreadyRegistered if plugin.plugin_id is already registered.
        """
        with self._lock:
            if plugin.plugin_id in self._plugins:
                raise PluginAlreadyRegistered(
                    f"plugin_id {plugin.plugin_id!r} is already registered"
                )
            # Optimistically reserve the slot before on_load so a re-entrant
            # call from on_load() (e.g. in tests) also gets the collision guard.
            self._plugins[plugin.plugin_id] = plugin
            self._contexts[plugin.plugin_id] = ctx

        try:
            plugin.on_load(ctx)
        except Exception:
            # on_load failed — roll back the slot reservation.
            with self._lock:
                self._plugins.pop(plugin.plugin_id, None)
                self._contexts.pop(plugin.plugin_id, None)
            raise

        log.info(
            "plugin loaded: id=%r type=%r version=%r tenant=%r",
            plugin.plugin_id, plugin.plugin_type, plugin.version, ctx.tenant_id,
        )
        if (slog := _structured(plugin.plugin_id)) is not None:
            slog.info(
                "plugin loaded",
                operation="on_load",
                context={"plugin_type": plugin.plugin_type, "version": plugin.version},
            )
        ctx.audit_emit("plugin.loaded", {
            "plugin_id": plugin.plugin_id,
            "plugin_type": plugin.plugin_type,
            "version": plugin.version,
            "tenant_id": ctx.tenant_id,
        })

    def unregister(self, plugin_id: str) -> None:
        """Call plugin.on_unload() and remove it from the registry.

        Raises PluginNotFound if plugin_id is not registered.
        """
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            ctx = self._contexts.get(plugin_id)
            if plugin is None:
                raise PluginNotFound(plugin_id)
            # pop, not del: the two maps are written together by register(), but a
            # KeyError here would abort the unload half-way — with the plugin
            # already gone from _plugins and on_unload() never called.
            self._plugins.pop(plugin_id, None)
            self._contexts.pop(plugin_id, None)

        try:
            plugin.on_unload()
        except Exception:
            log.exception("plugin %r raised during on_unload", plugin_id)

        # Drop the breaker with the plugin: a re-registered plugin must start
        # from a clean slate, not inherit the failure count that unloaded it.
        _breakers.forget(plugin_id)

        log.info("plugin unloaded: id=%r", plugin_id)
        if (slog := _structured(plugin_id)) is not None:
            slog.info("plugin unloaded", operation="on_unload")
        if ctx is not None:
            ctx.audit_emit("plugin.unloaded", {
                "plugin_id": plugin_id,
                "tenant_id": ctx.tenant_id,
            })

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, plugin_id: str) -> CorvinPlugin:
        """Return the plugin for plugin_id.

        Raises PluginNotFound if not registered.
        """
        with self._lock:
            plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise PluginNotFound(plugin_id)
        return plugin

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check_all(self) -> dict[str, HealthStatus]:
        """Call health_check() on every plugin, under its circuit breaker.

        Catches per-plugin exceptions and returns HealthStatus(ok=False, ...)
        so one broken plugin never blocks the rest.  Each result carries the
        plugin's breaker state under ``details["breaker"]`` (ADR-0233 Phase 2),
        so a plugin whose breaker is open is visibly contained rather than
        silently absent.

        A plugin whose breaker is OPEN is not called at all — that is the point
        of containment; its status reports ``ok=False`` with the breaker detail.
        """
        with self._lock:
            snapshot = list(self._plugins.items())

        results: dict[str, HealthStatus] = {}
        for pid, plugin in snapshot:
            breaker = _breakers.get_breaker(pid)
            try:
                breaker.guard()
            except _breakers.CircuitOpen as exc:
                results[pid] = HealthStatus(
                    ok=False,
                    message="circuit open — calls contained",
                    details={
                        "breaker": breaker.stats().to_dict(),
                        "retry_in_s": round(exc.retry_in_s, 1),
                    },
                )
                continue

            try:
                status = plugin.health_check()
            except Exception as exc:  # noqa: BLE001
                breaker.record_failure(exc)
                log.warning("health_check failed for plugin %r: %s", pid, type(exc).__name__)
                if (slog := _structured(pid)) is not None:
                    slog.error(
                        "health_check failed",
                        operation="health_check",
                        error=exc,
                        recovered=False,
                        context={"breaker": breaker.stats().to_dict()},
                    )
                ctx = self._contexts.get(pid)
                if ctx is not None:
                    ctx.audit_emit("plugin.health_check_failed", {
                        "plugin_id": pid,
                        "error_type": type(exc).__name__,  # class name only — no PII
                    })
                # Exception CLASS only. str(exc) reaches the Console and the logs;
                # a plugin's message routinely carries a path, a host or a record
                # fragment, and this surface must stay PII-free.
                results[pid] = HealthStatus(
                    ok=False,
                    message=f"health_check raised {type(exc).__name__}",
                    details={"breaker": breaker.stats().to_dict()},
                )
                continue

            if status.ok:
                breaker.record_success()
            else:
                breaker.record_failure()
            # Scrub the PLUGIN's contribution, then merge our own trusted breaker
            # stats on top — scrubbing after the merge would run the PII patterns
            # over our own numbers for nothing.
            merged = _scrub_plugin_details(status.details)
            merged["breaker"] = breaker.stats().to_dict()
            results[pid] = HealthStatus(
                ok=status.ok,
                message=_scrub_plugin_text(status.message),
                details=merged,
            )
        return results

    # ── Filtered lookup ───────────────────────────────────────────────────────

    def plugins_by_type(self, plugin_type: str) -> list[CorvinPlugin]:
        """Return all registered plugins of the given type."""
        with self._lock:
            return [p for p in self._plugins.values() if p.plugin_type == plugin_type]

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self) -> list[str]:
        """Return a sorted list of all registered plugin_ids."""
        with self._lock:
            return sorted(self._plugins.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._plugins)


# ── Module-level convenience functions ────────────────────────────────────────

_registry: PluginRegistry = PluginRegistry()


def register(plugin: CorvinPlugin, ctx: PluginContext) -> None:
    _registry.register(plugin, ctx)


def unregister(plugin_id: str) -> None:
    _registry.unregister(plugin_id)


def get(plugin_id: str) -> CorvinPlugin:
    return _registry.get(plugin_id)


def health_check_all() -> dict[str, HealthStatus]:
    return _registry.health_check_all()


def discover() -> list[str]:
    return _registry.discover()


def get_registry() -> PluginRegistry:
    """Return the module-level PluginRegistry instance."""
    return _registry
