"""Boot-time plugin wiring (ADR-0233).

The gap this closes: ``loader.discover_and_load()`` returns plugin *instances* and
``registry.register()`` needs a ``PluginContext`` — but nothing built that context,
so ``ctx.audit_registry`` and friends were always ``None`` and a plugin's
``on_load()`` had no registry to register with.  Structure without wiring is
exactly the defect ADR-0233 called out in the retired prototype, so it is fixed
here rather than left for a later phase.

Two entry points, both called from the gateway lifespan:

* :func:`assert_compliance` — fail-closed tripwires (ADR-0232/0233 D5).  Raises;
  the caller must NOT swallow it.
* :func:`bootstrap_tenant` — load the tenant's enabled plugins in dependency
  order and register them.  Best-effort per plugin: one bad plugin is skipped and
  logged, it never blocks the boot.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Iterable

from .manifest import PluginError, PluginRecord
from .protocol import PluginContext
from .providers import (
    audit_backend,
    data_connector,
    notification_backend,
    recall_backend,
    router_backend,
    stt_provider,
    summary_provider,
    user_backend,
)

log = logging.getLogger("corvin.plugins.bootstrap")


def _default_audit_emit(tenant_id: str) -> Callable[[str, dict], None]:
    """An audit_emit that reaches the real hash-chained writer.

    The PluginContext carried an ``audit_emit`` callable that every caller had to
    supply; without a default, plugin lifecycle events silently went nowhere.
    """

    def emit(event_type: str, details: dict) -> None:
        try:
            from audit import audit_event  # type: ignore[import-not-found]
        except ImportError:
            log.debug("audit module unavailable — %s not recorded", event_type)
            return
        try:
            audit_event(event_type, details=details, tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001
            log.error("audit emit failed for %s (%s)", event_type, type(exc).__name__)

    return emit


def build_context(
    *,
    plugin_id: str,
    tenant_id: str,
    corvin_home: Path,
    config: dict | None = None,
    compute_registry: Any | None = None,
    engine_factory: Any | None = None,
    channel_registry: Any | None = None,
) -> PluginContext:
    """Build a PluginContext with EVERY provider registry handle attached.

    A missing handle means a plugin of that type cannot self-register, so the
    handles are populated here in one place instead of per call site.
    """
    return PluginContext(
        plugin_id=plugin_id,
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        config=config or {},
        audit_emit=_default_audit_emit(tenant_id),
        compute_registry=compute_registry,
        engine_factory=engine_factory,
        channel_registry=channel_registry,
        notification_registry=notification_backend._registry,
        recall_registry=recall_backend._registry,
        summary_registry=summary_provider._registry,
        router_registry=router_backend._registry,
        audit_registry=audit_backend._registry,
        user_registry=user_backend._registry,
        stt_registry=stt_provider._registry,
        data_connector_registry=data_connector._registry,
    )


def _tripwire_module():
    """Import the tripwire module, extending sys.path like audit.py does.

    ``corvin_compliance_reports`` lives under ``core/compliance/`` and is not on
    the path of every process that boots the platform.
    """
    import sys

    try:
        from corvin_compliance_reports import tripwire

        return tripwire
    except ImportError:
        pass

    compliance_root = Path(__file__).resolve().parents[2] / "compliance"
    if compliance_root.is_dir() and str(compliance_root) not in sys.path:
        # append, NOT insert(0): this directory also contains generic top-level
        # names (tests/, templates/) with no __init__.py, so putting it FIRST on
        # sys.path lets them shadow another package's `tests` — the same class as
        # the operator/ stdlib-shadow trap. Appending means existing paths win.
        sys.path.append(str(compliance_root))
    from corvin_compliance_reports import tripwire

    return tripwire


def assert_compliance() -> list[Any]:
    """Run the fail-closed compliance tripwires.  Raises on failure.

    The caller MUST let a tripwire failure propagate: a boot that continues past
    one is the "compliance-off mode" the baseline forbids.

    The guarantee is attached to the MECHANISM, not to this module's import path.
    If the tripwire module itself cannot be imported (a stripped layout), this
    falls back to the same core assertion inline rather than skipping the check —
    "the checker is missing" must not read as "the check passed".
    """
    try:
        tripwire = _tripwire_module()
    except ImportError:
        log.warning(
            "tripwire module unavailable — running the inline core audit assertion"
        )
        return _assert_core_audit_inline()
    return tripwire.assert_all()


class CoreAuditUnavailable(RuntimeError):
    """The core audit writer is unusable and no tripwire module was available."""


def _assert_core_audit_inline() -> list[Any]:
    """Minimal fail-closed check: the core audit chain must be usable.

    Mirrors ``tripwire.audit_writer_reachable`` + ``audit_chain_intact`` without
    depending on the compliance package.  An absent audit module means audit is
    a documented no-op for that layout (standalone bridge mode), which is not a
    compliance failure; an audit module whose chain does NOT verify is.
    """
    try:
        import audit as _audit  # type: ignore[import-not-found]
    except ImportError:
        log.warning("audit module unavailable — audit writes are no-ops in this layout")
        return []

    try:
        path = Path(_audit.audit_path())
    except Exception as exc:  # noqa: BLE001
        raise CoreAuditUnavailable(
            f"audit_path() failed: {type(exc).__name__}"
        ) from exc

    if path.exists() and path.stat().st_size > 0:
        ok, problems = _audit.verify_audit(path)
        if not ok:
            raise CoreAuditUnavailable(
                f"core audit chain does not verify ({len(problems)} broken record(s))"
            )
    return []


def load_tenant_spec(tenant_id: str, corvin_home: Path) -> dict:
    """Read ``tenant.corvin.yaml`` for this tenant.  Returns ``{}`` when absent.

    Best-effort by design: a missing or unreadable tenant config means "no
    declared plugins", never a boot failure — the declarative path is opt-in.
    """
    path = corvin_home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    try:
        if not path.is_file():
            return {}
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        log.error(
            "tenant config unreadable for %r (%s) — no declared plugins",
            tenant_id,
            type(exc).__name__,
        )
        return {}


def bootstrap_declared(
    *,
    tenant_id: str,
    corvin_home: Path,
    tenant_config: dict | None = None,
    **registries: Any,
) -> list[str]:
    """Load the plugins DECLARED in ``spec.plugins.installed`` (ADR-0030 Phase 7).

    This is the declarative path ADR-0030 specified and that had no caller: the
    config was documented, `loader.discover_and_load()` implemented it, and nothing
    ever invoked either. Entries there are loaded unconditionally, because writing
    a plugin into a version-controlled tenant config IS the explicit opt-in the ADR
    asks for — it needs no feature flag on top.

    ``auto_discover_entry_points: true`` additionally loads every installed
    ``corvin.plugins`` entry point. It stays default-false: on a machine with
    third-party packages installed, flipping it means loading code nobody listed.
    """
    config = tenant_config if tenant_config is not None else load_tenant_spec(
        tenant_id, corvin_home
    )
    plugins_cfg = (config.get("spec") or {}).get("plugins") or {}
    declared: list[dict] = list(plugins_cfg.get("installed") or [])
    auto_ep = bool(plugins_cfg.get("auto_discover_entry_points", False))
    if not declared and not auto_ep:
        return []

    from .loader import discover_and_load

    try:
        instances = discover_and_load(config, corvin_home=corvin_home)
    except Exception as exc:  # noqa: BLE001 — a bad config must not stop the boot
        log.error(
            "declared-plugin discovery failed for %r (%s)",
            tenant_id,
            type(exc).__name__,
        )
        return []

    loaded: list[str] = []
    for instance in instances:
        plugin_id = getattr(instance, "plugin_id", "")
        if not plugin_id:
            log.error("declared plugin %r has no plugin_id — skipping", type(instance).__name__)
            continue
        # Per-plugin config from the declaration, so a declared plugin gets its
        # settings without a registry entry.
        entry_config = next(
            (e.get("config") or {} for e in declared if e.get("id") == plugin_id), {}
        )
        if _register_instance(
            instance,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            corvin_home=corvin_home,
            config=entry_config,
            **registries,
        ):
            loaded.append(plugin_id)

    if loaded:
        log.info(
            "loaded %d declared plugin(s) for tenant %r: %s", len(loaded), tenant_id, loaded
        )
    return loaded


def bootstrap_tenant(
    *,
    tenant_id: str,
    corvin_home: Path,
    lifecycle_enabled: bool = False,
    compute_registry: Any | None = None,
    engine_factory: Any | None = None,
    channel_registry: Any | None = None,
) -> list[str]:
    """Register the tenant's ENABLED plugins, in dependency order.

    Returns the plugin_ids that loaded successfully.  With
    ``lifecycle_enabled=False`` (the default, matching the shipped-dark flag) the
    registry is not consulted at all and this is a no-op returning ``[]`` — an
    install that predates the operator turning the flag on must not start
    loading plugins behind their back.

    NOTE: this is the RUNTIME registry path. The declarative
    ``spec.plugins.installed`` path is :func:`bootstrap_declared`, and it runs
    FIRST at boot — see :func:`bootstrap_all` for the precedence rule.
    """
    if not lifecycle_enabled:
        log.debug("plugin_runtime_lifecycle off — skipping registry bootstrap")
        return []

    from .state import TenantRegistry

    try:
        tenant_registry = TenantRegistry.load(
            tenant_id=tenant_id, corvin_home_path=corvin_home
        )
        order = tenant_registry.load_order()
    except PluginError as exc:
        # A corrupt or unsatisfiable registry must not take the process down: no
        # plugin loads (fail-closed for the FEATURE), the platform still boots.
        log.error(
            "plugin registry unusable for tenant %r (%s) — no plugins loaded",
            tenant_id,
            type(exc).__name__,
        )
        return []

    loaded: list[str] = []
    for plugin_id in order:
        record = tenant_registry.records[plugin_id]
        if _load_one(
            record,
            tenant_id=tenant_id,
            corvin_home=corvin_home,
            compute_registry=compute_registry,
            engine_factory=engine_factory,
            channel_registry=channel_registry,
        ):
            loaded.append(plugin_id)
    if loaded:
        log.info("loaded %d plugin(s) for tenant %r: %s", len(loaded), tenant_id, loaded)
    return loaded


def _register_instance(
    instance: Any,
    *,
    plugin_id: str,
    tenant_id: str,
    corvin_home: Path,
    config: dict | None = None,
    **registries: Any,
) -> bool:
    """Build a context and register one already-instantiated plugin.

    Shared by the declarative and the registry path so both get every provider
    handle and identical failure behaviour.
    """
    from .registry import get_registry, register

    if plugin_id in get_registry().discover():
        log.debug("plugin %r already registered — skipping", plugin_id)
        return True

    ctx = build_context(
        plugin_id=plugin_id,
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        config=config or {},
        **registries,
    )
    try:
        register(instance, ctx)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "plugin %r failed to register (%s) — skipping",
            plugin_id,
            type(exc).__name__,
        )
        return False
    return True


def bootstrap_all(
    *,
    tenant_id: str,
    corvin_home: Path,
    lifecycle_enabled: bool = False,
    tenant_config: dict | None = None,
    **registries: Any,
) -> list[str]:
    """Run BOTH load paths in the order their precedence demands.

    Precedence: the **declarative** config wins over the runtime registry. A plugin
    written into a version-controlled ``tenant.corvin.yaml`` is the operator's
    stronger, reviewable statement of intent; a registry entry is a Console click.
    Because ``_register_instance`` treats an already-registered id as loaded, a
    plugin present in both is loaded once — from the declaration — and the registry
    pass logs it as already-registered rather than colliding.
    """
    declared = bootstrap_declared(
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        tenant_config=tenant_config,
        **registries,
    )
    runtime = bootstrap_tenant(
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        lifecycle_enabled=lifecycle_enabled,
        **registries,
    )
    overlap = sorted(set(declared) & set(runtime))
    if overlap:
        log.info(
            "plugin(s) %s are both declared and in the registry — the declaration won",
            overlap,
        )
    # Preserve order: declarations first, then registry-only ids.
    return declared + [pid for pid in runtime if pid not in set(declared)]


def _load_one(
    record: PluginRecord,
    *,
    tenant_id: str,
    corvin_home: Path,
    **registries: Any,
) -> bool:
    """Instantiate + register one record.  Returns False on any failure."""
    from .loader import load_from_class_path

    if not record.class_path:
        log.error("plugin %r has no class_path — skipping", record.plugin_id)
        return False

    # Idempotence: enable() now hot-loads (ADR-0124 Inv. 6), so by the time a boot
    # or a re-bootstrap runs, the plugin may already be registered in this process.
    # Registering twice raises PluginAlreadyRegistered, which would turn a healthy
    # plugin into a "failed to register" line. Already-loaded counts as loaded.
    from .registry import get_registry

    if record.plugin_id in get_registry().discover():
        log.debug("plugin %r already registered — skipping load", record.plugin_id)
        return True

    try:
        cls = load_from_class_path(record.class_path)
        instance = cls()
    except Exception as exc:  # noqa: BLE001 - one bad plugin must not stop the boot
        log.error(
            "plugin %r failed to instantiate (%s) — skipping",
            record.plugin_id,
            type(exc).__name__,
        )
        return False

    return _register_instance(
        instance,
        plugin_id=record.plugin_id,
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        config=record.settings,
        **registries,
    )


def shutdown(plugin_ids: Iterable[str]) -> None:
    """Unregister plugins on graceful shutdown, detaching their provider slots."""
    from .registry import unregister

    for plugin_id in plugin_ids:
        try:
            unregister(plugin_id)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "plugin %r failed to unload (%s)", plugin_id, type(exc).__name__
            )


__all__ = [
    "assert_compliance",
    "bootstrap_all",
    "bootstrap_declared",
    "bootstrap_tenant",
    "build_context",
    "load_tenant_spec",
    "shutdown",
]
