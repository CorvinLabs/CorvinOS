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

from .manifest import BootLayer, PluginError, PluginRecord
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


def _audit_degradation(
    tenant_id: str, event_type: str, details: dict
) -> None:
    """Record a plugin subsystem degradation in the hash-chained core trail.

    Every failure path here logs and continues — correct for core stability, but
    for one commit a log line was the ONLY trace. There is a ``plugin.loaded``
    event on success and, before this, no counterpart on failure: an operator who
    declared an audit_backend that ships copies to a SIEM got a booting platform, a
    working core chain, a dead SIEM stream, and an audit trail in which "never
    configured" and "silently died at boot" look exactly the same.

    Exception CLASS only, never str(exc) — a loader error routinely carries a path.
    """
    try:
        _default_audit_emit(tenant_id)(event_type, details)
    except Exception:  # noqa: BLE001 - visibility must not become a boot failure
        log.debug("degradation audit for %s could not be written", event_type)


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
        _audit_degradation(tenant_id, "plugin.config_unreadable", {
            "tenant_id": tenant_id,
            "error_type": type(exc).__name__,
        })
        return {}


# ── Global plugins (ADR-0240 / ADR-0243) ──────────────────────────────────────
#
# Two scopes, and the boundary between them is a trust boundary, not a
# convenience:
#
# * GLOBAL — ships in the wheel, applies to every tenant, carries the privileged
#   boot layers ``compliance`` and ``core``.  Registered from CODE (below), not
#   from a tenant's YAML.
# * TENANT — declared in ``tenant.corvin.yaml`` or installed through the Console.
#   Carries ``bundled`` and ``installed`` only.
#
# The asymmetry is the point.  If a tenant config could declare ``boot_layer:
# compliance``, any operator-writable file would be able to mint an undisableable
# plugin that loads before everything else.  ``_TENANT_DECLARABLE_BOOT_LAYERS``
# is what stops that, and :func:`bootstrap_declared` enforces it.

#: ``(class_path, boot_layer)`` pairs contributed by bundled code.  Populated by
#: :func:`register_global_plugin` at import time of the bundling module.
_GLOBAL_SPECS: list[tuple[str, BootLayer]] = []

#: Retired. A ``corvin.global_plugins`` entry-point group was implemented here
#: and then removed before it had a single user, because an adversarial pass
#: showed what it actually was: any third-party wheel on the machine could
#: publish ``compliance:whatever`` and be loaded (a) before everything else,
#: (b) as undisableable, (c) with no PluginRecord, therefore past the
#: ``_PRIVILEGED_BOOT_LAYERS`` gate, the consent prompt and the L34/L35 fields,
#: and (d) able to abort the platform's boot permanently by raising in
#: ``__init__``, since a compliance load failure is fatal by design.
#:
#: The tenant-scope guard (``_declared_boot_layer``) was carefully locking the YAML
#: door while this one stood open to `pip install`. Global plugins are therefore
#: contributed from CODE only — :func:`register_global_plugin`, called by a
#: module that ships in this wheel. Re-adding discovery needs signature
#: verification and an allowlist, not an entry-point name.
GLOBAL_ENTRY_POINT_GROUP = None

#: The only boot layers a tenant-scoped declaration may claim.
_TENANT_DECLARABLE_BOOT_LAYERS: frozenset[BootLayer] = frozenset(
    {BootLayer.BUNDLED, BootLayer.INSTALLED}
)


def register_global_plugin(class_path: str, *, boot_layer: BootLayer | str) -> None:
    """Declare a bundled global plugin and the boot layer it boots on.

    Intended to be called from bundled code (import side effect), never from
    tenant config. That is a CONVENTION, not an enforced one: there is no
    caller check, and in-process code can call this. It holds because the
    wheel is the only thing that does — not because anything stops others.
    See docs/claude-ref/layer-plugins.md § "The perimeter is attribution".
    Registering the same ``class_path`` twice is a no-op so a module that is
    imported from two places does not double-load its plugin.
    """
    resolved = BootLayer(boot_layer)
    if resolved not in (BootLayer.COMPLIANCE, BootLayer.CORE):
        raise ValueError(
            f"global plugins live on the compliance or core boot layer, "
            f"not {resolved.value} — tenant-scoped boot layers are declared in "
            f"tenant.corvin.yaml"
        )
    if any(cp == class_path for cp, _ in _GLOBAL_SPECS):
        return
    _GLOBAL_SPECS.append((class_path, resolved))


def _global_specs() -> list[tuple[str, BootLayer]]:
    """The code-registered global plugins, in a deterministic boot order.

    Compliance first, then core, alphabetically inside each boot layer so two
    installs with the same wheel boot in the same order.

    There is deliberately NO discovery step here — see
    :data:`GLOBAL_ENTRY_POINT_GROUP` for why the entry-point variant was
    removed, which closed the one path that let a third-party WHEEL land on
    this list. Everything here came through :func:`register_global_plugin`;
    that it came from bundled code is convention rather than enforcement.
    """
    order = {BootLayer.COMPLIANCE: 0, BootLayer.CORE: 1}
    return sorted(_GLOBAL_SPECS, key=lambda s: (order[s[1]], s[0]))


def _record_granted_compliance(plugin_id: str) -> None:
    """Tell the tripwire that the wheel granted ``plugin_id`` the compliance layer.

    Guarded: the tripwire module is not importable in every packaging layout,
    and a bookkeeping call must never fail a load that already succeeded. A
    missing record is safe in the right direction — the post-boot check then
    sees an ungranted plugin and refuses the boot, which is louder than the
    alternative and never quieter.
    """
    try:
        _tripwire_module().record_granted_compliance_plugin(plugin_id)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "could not record compliance grant for %r (%s) — the post-boot "
            "tripwire will treat it as ungranted",
            plugin_id, type(exc).__name__,
        )


class GlobalComplianceLoadFailed(RuntimeError):
    """A ``boot_layer=compliance`` global plugin failed to load.

    Deliberately fatal.  Every other load failure in this module degrades and
    logs, because one broken bridge must not cost the platform its boot.  The
    compliance boot layer is the exception: booting without a mechanism
    that the install declares it has is the "compliance-off mode" the baseline
    forbids, and a degraded boot would hide it behind a log line.
    """


def bootstrap_global(
    *,
    tenant_id: str,
    corvin_home: Path,
    **registries: Any,
) -> list[str]:
    """Load the bundled global plugins, compliance boot layer first.

    NOT behind a feature flag, and that is deliberate rather than an oversight:
    the boot layer it exists to load is the compliance one, and CLAUDE.md forbids
    putting a compliance mechanism behind a switch. With no bundled global
    plugins registered this is a no-op returning ``[]``, so the flagless path
    changes nothing on an install that has none.

    Failure semantics differ by boot layer (ADR-0240 § Boot sequence):

    * ``compliance`` — raises :class:`GlobalComplianceLoadFailed`; the boot aborts.
    * ``core`` — logged, audited, skipped; the platform boots degraded.
    """
    specs = _global_specs()
    if not specs:
        return []

    from .loader import load_from_class_path

    loaded: list[str] = []
    for class_path, boot_layer in specs:
        try:
            cls = load_from_class_path(class_path)
            instance = cls()
            plugin_id = getattr(instance, "plugin_id", "")
            if not plugin_id:
                raise PluginError(f"{class_path} has no plugin_id")
        except Exception as exc:  # noqa: BLE001
            details = {
                "class_path": class_path,
                "boot_layer": boot_layer.value,
                "tenant_id": tenant_id,
                "reason": "instantiate_failed",
                "error_type": type(exc).__name__,
            }
            _audit_degradation(tenant_id, "plugin.global_load_failed", details)
            if boot_layer is BootLayer.COMPLIANCE:
                raise GlobalComplianceLoadFailed(
                    f"compliance plugin {class_path} failed to load "
                    f"({type(exc).__name__})"
                ) from exc
            log.error(
                "global core plugin %s failed to load (%s) — booting degraded",
                class_path, type(exc).__name__,
            )
            continue

        ok = _register_instance(
            instance,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            corvin_home=corvin_home,
            boot_layer=boot_layer,
            # These ARE the wheel: a global spec can only be contributed from
            # code, by `register_global_plugin`, which no config can reach. So
            # this is the one place `builtin` is a fact rather than a claim, and
            # the one exemption the ADR-0250 slot gate grants.
            origin="builtin",
            **registries,
        )
        if ok:
            loaded.append(plugin_id)
            if boot_layer is BootLayer.COMPLIANCE:
                # Tell the tripwire that THIS id was granted the compliance
                # layer by the wheel's own boot code. The post-boot check
                # compares the registry against this list, so anything that put
                # itself on the layer some other way stops the boot. Recorded
                # only on the success path: a plugin that failed to register was
                # never granted anything.
                _record_granted_compliance(plugin_id)
        elif boot_layer is BootLayer.COMPLIANCE:
            raise GlobalComplianceLoadFailed(
                f"compliance plugin {plugin_id!r} failed to register"
            )
    if loaded:
        log.info("loaded %d global plugin(s): %s", len(loaded), loaded)
    return loaded


def bootstrap_declared(
    *,
    tenant_id: str,
    corvin_home: Path,
    tenant_config: dict | None = None,
    only_types: frozenset[str] | set[str] | None = None,
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

    ``only_types`` restricts loading to the named ``plugin_type`` values. It
    exists because a plugin's consumer is not always in the gateway process: the
    compute worker is a separate process that owns engine dispatch, and it calls
    this with ``{"compute_engine"}``. Without the filter the worker would also
    load the tenant's bridge supervisors, i.e. start messenger daemons from the
    compute process — a second set of daemons racing the real ones, which is the
    duplicate-start failure ADR-0238 calls out by name.

    The filter is applied AFTER instantiation and BEFORE ``on_load``, so a
    filtered-out plugin never runs any of its own code beyond ``__init__``.
    Filtering earlier would mean reading ``plugin_type`` off the declaration
    instead of the class, and a declaration can claim any type it likes.
    """
    config = tenant_config if tenant_config is not None else load_tenant_spec(
        tenant_id, corvin_home
    )
    plugins_cfg = (config.get("spec") or {}).get("plugins") or {}
    declared: list[dict] = list(plugins_cfg.get("installed") or [])
    auto_ep = bool(plugins_cfg.get("auto_discover_entry_points", False))

    # Skipped entirely when bridge_channel is filtered out, rather than
    # injected-then-discarded: the compute worker calls this with
    # only_types={"compute_engine"}, and instantiating seven bridge supervisors
    # in a process that will never register them is pointless work on a path
    # that runs at every worker start.
    bundled = (
        _bundled_bridge_declarations(tenant_id, declared)
        if only_types is None or "bridge_channel" in only_types
        else []
    )
    if bundled:
        declared = declared + bundled
        # `discover_and_load` re-reads the config rather than taking `declared`,
        # so the injection has to land there too. Copied, never mutated in
        # place: `tenant_config` may be the caller's own dict, and a bootstrap
        # that silently grew the caller's config would be a side effect nobody
        # asked for.
        config = dict(config)
        spec = dict(config.get("spec") or {})
        plugins_cfg = dict(spec.get("plugins") or {})
        plugins_cfg["installed"] = declared
        spec["plugins"] = plugins_cfg
        config["spec"] = spec

    if not declared and not auto_ep:
        return []

    from .loader import discover_and_load

    try:
        instances = discover_and_load(
            config,
            corvin_home=corvin_home,
            # The loader skips a bad entry per plugin, which is where a typo'd
            # class_path actually dies. Without this hook that failure left no
            # trace in the audit chain at all.
            on_error=lambda pid, reason, error_type: _audit_degradation(
                tenant_id,
                "plugin.load_failed",
                {
                    "plugin_id": pid,
                    "tenant_id": tenant_id,
                    "reason": reason,
                    "error_type": error_type,
                },
            ),
        )
    except Exception as exc:  # noqa: BLE001 — a bad config must not stop the boot
        log.error(
            "declared-plugin discovery failed for %r (%s)",
            tenant_id,
            type(exc).__name__,
        )
        _audit_degradation(tenant_id, "plugin.discovery_failed", {
            "tenant_id": tenant_id,
            "error_type": type(exc).__name__,
        })
        return []

    loaded: list[str] = []
    for instance in instances:
        if only_types is not None:
            # Read off the CLASS, never off the declaration: the declaration is
            # operator-written YAML and a wrong `plugin_type` there would let a
            # bridge supervisor into the compute worker.
            itype = getattr(instance, "plugin_type", "") or ""
            if itype not in only_types:
                log.debug(
                    "skipping %r (type %r) — this process loads only %s",
                    getattr(instance, "plugin_id", "?"), itype, sorted(only_types),
                )
                continue
        plugin_id = getattr(instance, "plugin_id", "")
        if not plugin_id:
            log.error("declared plugin %r has no plugin_id — skipping", type(instance).__name__)
            _audit_degradation(tenant_id, "plugin.load_failed", {
                "plugin_id": "", "tenant_id": tenant_id,
                "reason": "no_plugin_id", "class_name": type(instance).__name__,
            })
            continue
        # Per-plugin config from the declaration, so a declared plugin gets its
        # settings without a registry entry.
        entry = next((e for e in declared if e.get("id") == plugin_id), {})
        entry_config = entry.get("config") or {}
        boot_layer = _declared_boot_layer(
            entry, plugin_id=plugin_id, tenant_id=tenant_id
        )
        if _register_instance(
            instance,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            corvin_home=corvin_home,
            config=entry_config,
            boot_layer=boot_layer,
            **registries,
        ):
            loaded.append(plugin_id)

    if loaded:
        log.info(
            "loaded %d declared plugin(s) for tenant %r: %s", len(loaded), tenant_id, loaded
        )
    return loaded


def _bundled_bridge_declarations(
    tenant_id: str, declared: list[dict]
) -> list[dict]:
    """The bundled bridge supervisors, as declarations (ADR-0238, Stage 5).

    ``registry_entries.declaration_entries()` has existed since the supervisors
    were written and was imported by nothing outside its own package: the
    classes, the start gate and the entry generator were all complete, and no
    boot path ever reached them. This is that path.

    Why the boot injects them rather than the operator hand-writing seven class
    paths: ``boot_layer=bundled`` means "ships with CorvinOS, opt-out per
    tenant" (ADR-0243). Requiring a dotted class path in ``tenant.corvin.yaml``
    for something that ships in the wheel is the ``installed`` contract, not the
    ``bundled`` one — and it is a path that goes stale on the first rename.

    Three properties this deliberately preserves:

    * **The operator's own entry always wins.** A channel already named in
      ``spec.plugins.installed`` is skipped here, so
      ``{id: discord-bridge, config: {enabled: false}}`` parks that bridge and
      an explicit class path overrides the bundled one.
    * **Off is quiet and total.** With ``bridge_supervisor_plugins`` off — the
      default — nothing is injected and nothing is instantiated. The supervisor
      re-checks the same flag in its own start gate, which is the load-bearing
      check; this one exists so a default install does not construct seven
      objects to have each of them decide to do nothing.
    * **Declaring is not starting.** These entries make the supervisors
      *loadable*. Whether a daemon actually starts is the six-condition gate in
      ``supervisor.py`` — credentials present, no duplicate already running,
      runtime provisioned, Node available. ADR-0238's fail-closed defaults are
      untouched by this function.
    """
    try:
        from .bridges.supervisor import _flag_enabled  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        # No supervisor module in this install (stripped wheel). Bundled
        # bridges are simply absent, which is the pre-feature behaviour.
        return []
    if not _flag_enabled(tenant_id):
        return []
    try:
        from .bridges.registry_entries import declaration_entries  # noqa: PLC0415

        already = {e.get("id") for e in declared if isinstance(e, dict)}
        return [e for e in declaration_entries() if e.get("id") not in already]
    except Exception as exc:  # noqa: BLE001 — a bad bundled list must not stop boot
        log.error(
            "bundled bridge declarations unavailable for %r (%s) — bridges keep "
            "being managed as before",
            tenant_id, type(exc).__name__,
        )
        return []


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
        # The single most consequential degradation: EVERY runtime plugin is now
        # absent, including any audit or notification backend the operator relies on.
        _audit_degradation(tenant_id, "plugin.registry_unusable", {
            "tenant_id": tenant_id,
            "error_type": type(exc).__name__,
        })
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


def _declared_boot_layer(
    entry: dict, *, plugin_id: str, tenant_id: str
) -> BootLayer:
    """Resolve the boot layer of a tenant-declared plugin, refusing privileged claims.

    A tenant config is operator-writable, so it may say "this is a bundled
    bridge" but not "this is a compliance mechanism".  A privileged claim is
    downgraded to ``installed`` and audited rather than honoured — and rather
    than aborting the boot, because a single mis-declared entry should cost that
    entry its privilege, not the platform its start.
    """
    raw = entry.get("boot_layer")
    if raw is None:
        return BootLayer.INSTALLED
    try:
        boot_layer = BootLayer(raw)
    except ValueError:
        log.error(
            "tenant declaration for %r names unknown boot_layer %r — using installed",
            plugin_id, raw,
        )
        _audit_degradation(tenant_id, "plugin.boot_layer_rejected", {
            "plugin_id": plugin_id, "tenant_id": tenant_id,
            "declared_boot_layer": str(raw)[:32], "reason": "unknown_boot_layer",
        })
        return BootLayer.INSTALLED
    if boot_layer not in _TENANT_DECLARABLE_BOOT_LAYERS:
        log.error(
            "tenant declaration for %r claims privileged boot_layer %s — "
            "downgrading to installed (global boot layers come from the wheel)",
            plugin_id, boot_layer.value,
        )
        _audit_degradation(tenant_id, "plugin.boot_layer_rejected", {
            "plugin_id": plugin_id, "tenant_id": tenant_id,
            "declared_boot_layer": boot_layer.value,
            "reason": "privileged_boot_layer_from_tenant",
        })
        return BootLayer.INSTALLED
    return boot_layer


def _tenant_scope_permits(
    instance: Any,
    *,
    plugin_id: str,
    tenant_id: str,
    corvin_home: Path,
    origin: str | None,
) -> bool:
    """Provider-slot gate (ADR-0250 D1).  True = this plugin may take a slot.

    Sits in ``_register_instance`` rather than in ``PluginLifecycle.enable`` or in
    the providers themselves, because that is the one point BOTH load paths pass
    through.  The lifecycle path has a ``PluginRecord``; the declarative
    ``spec.plugins.installed`` path has only a config entry with no ``origin`` —
    putting the check on either side alone would leave the other open, which is
    how ADR-0249's trust gate ended up covering the runtime path only.

    Never raises.  A refusal costs one plugin its slot; an exception here would
    cost the boot, and the check exists to protect data, not to stop the platform.
    """
    plugin_type = getattr(instance, "plugin_type", "") or ""
    try:
        from . import tenant_scope

        decision = tenant_scope.evaluate(
            plugin_type=plugin_type, origin=origin, corvin_home=corvin_home
        )
    except Exception:  # noqa: BLE001 - a broken check must not brick the boot
        log.exception(
            "plugin %r: tenant-scope evaluation failed — allowing", plugin_id
        )
        return True

    if decision.allowed:
        return True

    log.error(
        "plugin %r (type=%s, origin=%s) refused a provider slot: %s. Provider "
        "slots are process-wide (ADR-0250), so this plugin would see every "
        "tenant's data.",
        plugin_id, plugin_type, origin or "unknown", decision.reason,
    )
    _audit_degradation(tenant_id, "plugin.provider_slot_refused", {
        "plugin_id": plugin_id,
        "tenant_id": tenant_id,
        "plugin_type": plugin_type,
        "origin": origin or "unknown",
        "reason": decision.reason,
        # The COUNT, never the ids. Recording which other tenants exist to close
        # a tenant-isolation gap would be a net loss.
        "tenant_count": decision.tenant_count if decision.tenant_count else -1,
    })
    return False


def _register_instance(
    instance: Any,
    *,
    plugin_id: str,
    tenant_id: str,
    corvin_home: Path,
    config: dict | None = None,
    boot_layer: BootLayer | str | None = None,
    origin: str | None = None,
    **registries: Any,
) -> bool:
    """Build a context and register one already-instantiated plugin.

    Shared by the declarative and the registry path so both get every provider
    handle and identical failure behaviour.

    ``origin`` is the manifest provenance, used only by the ADR-0250 provider-slot
    gate.  It defaults to ``None`` — meaning "not builtin" — so a caller that
    forgets it gets the restrictive answer rather than the permissive one.
    """
    from .registry import get_registry, register

    if plugin_id in get_registry().discover():
        log.debug("plugin %r already registered — skipping", plugin_id)
        return True

    if not _tenant_scope_permits(
        instance,
        plugin_id=plugin_id,
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        origin=origin,
    ):
        return False

    ctx = build_context(
        plugin_id=plugin_id,
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        config=config or {},
        **registries,
    )
    try:
        register(instance, ctx, boot_layer=boot_layer)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "plugin %r failed to register (%s) — skipping",
            plugin_id,
            type(exc).__name__,
        )
        _audit_degradation(tenant_id, "plugin.load_failed", {
            "plugin_id": plugin_id, "tenant_id": tenant_id,
            "reason": "register_failed", "error_type": type(exc).__name__,
        })
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
    """Run ALL THREE load paths in the order their precedence demands.

    Order (ADR-0240 § Boot sequence): global before tenant, and inside the tenant
    scope declarative before runtime.

    1. **Global** — bundled ``compliance`` then ``core`` plugins.  A compliance
       failure here aborts the boot; a core failure degrades.
    2. **Declarative** — ``spec.plugins.installed`` from ``tenant.corvin.yaml``.
    3. **Runtime registry** — Console-installed plugins, gated on the
       ``plugin_runtime_lifecycle`` flag.

    Precedence inside the tenant scope: the **declarative** config wins over the
    runtime registry. A plugin written into a version-controlled
    ``tenant.corvin.yaml`` is the operator's stronger, reviewable statement of
    intent; a registry entry is a Console click. Because ``_register_instance``
    treats an already-registered id as loaded, a plugin present in both is loaded
    once — from the declaration — and the registry pass logs it as
    already-registered rather than colliding.  The same mechanism makes the
    global pass win over both: a tenant cannot shadow a global plugin's id.
    """
    global_ids = bootstrap_global(
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        **registries,
    )
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
    # Preserve order: globals, then declarations, then registry-only ids.
    seen = set(global_ids)
    ordered = list(global_ids)
    for pid in declared + runtime:
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered


def _trust_permits(
    record: PluginRecord, *, tenant_id: str, corvin_home: Path
) -> bool:
    """Provenance gate (ADR-0249). True = this plugin may be imported.

    Never raises: a broken trust config must degrade to the pre-feature path, not
    abort a boot. The refusal itself is audited, because "an operator installed a
    plugin and it silently never loaded" is the failure mode this whole area keeps
    producing.
    """
    try:
        from . import trust
    except ImportError:  # stripped install
        return True

    try:
        enforcement = trust.enforcement_enabled(tenant_id)
        decision = trust.evaluate(
            record.to_dict(),
            corvin_home=corvin_home,
            tenant_id=tenant_id,
            enforcement=enforcement,
        )
    except Exception:  # noqa: BLE001 - trust must not break the boot
        log.exception("plugin %r: trust evaluation failed — allowing", record.plugin_id)
        return True

    if decision.allowed:
        if decision.verdict is trust.Verdict.FORGED:
            # Flag off: it loads, but this must not pass in silence.
            log.warning(
                "plugin %r claims origin=vetted without a valid pinned signature "
                "— loading anyway because %s is off",
                record.plugin_id,
                trust.TRUST_ENFORCEMENT_FLAG,
            )
        return True

    log.error(
        "plugin %r refused: %s (verdict=%s)",
        record.plugin_id,
        decision.reason,
        decision.verdict.value,
    )
    _audit_degradation(tenant_id, "plugin.load_refused", {
        "plugin_id": record.plugin_id,
        "tenant_id": tenant_id,
        "verdict": decision.verdict.value,
        "reason": decision.reason,
    })
    return False


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
        _audit_degradation(tenant_id, "plugin.load_failed", {
            "plugin_id": record.plugin_id, "tenant_id": tenant_id,
            "reason": "no_class_path",
        })
        return False

    # Idempotence: enable() now hot-loads (ADR-0124 Inv. 6), so by the time a boot
    # or a re-bootstrap runs, the plugin may already be registered in this process.
    # Registering twice raises PluginAlreadyRegistered, which would turn a healthy
    # plugin into a "failed to register" line. Already-loaded counts as loaded.
    from .registry import get_registry

    if record.plugin_id in get_registry().discover():
        log.debug("plugin %r already registered — skipping load", record.plugin_id)
        return True

    # ADR-0249 provenance gate. Deliberately BEFORE load_from_class_path: that
    # call imports the plugin module and runs its top-level code, so a check
    # placed after it would be asking "may we run this?" about something already
    # running. Ships dark — with plugin_trust_enforcement off, evaluate() still
    # returns a verdict but allows everything, so an existing install is unchanged.
    if not _trust_permits(record, tenant_id=tenant_id, corvin_home=corvin_home):
        return False

    try:
        cls = load_from_class_path(record.class_path)
        instance = cls()
    except Exception as exc:  # noqa: BLE001 - one bad plugin must not stop the boot
        log.error(
            "plugin %r failed to instantiate (%s) — skipping",
            record.plugin_id,
            type(exc).__name__,
        )
        _audit_degradation(tenant_id, "plugin.load_failed", {
            "plugin_id": record.plugin_id, "tenant_id": tenant_id,
            "reason": "instantiate_failed", "error_type": type(exc).__name__,
        })
        return False

    # Same trust boundary as the declarative path: registry.yaml is per-tenant
    # state, so a record there cannot mint a compliance- or core-boot-layer
    # plugin either. The manifest gate already refuses a community-origin
    # privileged claim; this refuses a privileged claim from tenant scope
    # regardless of origin.
    boot_layer = _declared_boot_layer(
        {"boot_layer": record.boot_layer.value},
        plugin_id=record.plugin_id,
        tenant_id=tenant_id,
    )
    return _register_instance(
        instance,
        plugin_id=record.plugin_id,
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        config=record.settings,
        boot_layer=boot_layer,
        # The runtime path is the one with a manifest, so it is the one that can
        # answer the ADR-0250 provider-slot question honestly. The declarative
        # path cannot and deliberately does not (see _register_instance).
        origin=record.origin.value,
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
    "GLOBAL_ENTRY_POINT_GROUP",
    "GlobalComplianceLoadFailed",
    "assert_compliance",
    "bootstrap_all",
    "bootstrap_declared",
    "bootstrap_global",
    "bootstrap_tenant",
    "build_context",
    "load_tenant_spec",
    "register_global_plugin",
    "shutdown",
]
