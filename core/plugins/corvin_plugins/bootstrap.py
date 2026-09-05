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
    context_retriever,
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
        context_retriever_registry=context_retriever._registry,
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
    depending on the compliance package.  Fail-closed on every branch: an absent
    audit module, an audit module without a loaded writer, and a chain that does
    not verify are all :class:`CoreAuditUnavailable`.  "Audit is a documented
    no-op for this layout" was the old reading of the first two, and it is the
    compliance-off mode the baseline forbids, one ImportError deep (findings A1,
    A8): a platform whose audit trail goes nowhere must not boot.
    """
    try:
        import audit as _audit  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CoreAuditUnavailable(
            "core audit module not importable — refusing to boot without an audit trail"
        ) from exc

    writer_available = getattr(_audit, "writer_available", None)
    if not callable(writer_available):
        raise CoreAuditUnavailable(
            "audit module exposes no writer_available() — not the core audit module"
        )
    if not writer_available():
        raise CoreAuditUnavailable(
            "core audit writer unavailable (forge.security_events not loaded) — "
            "audit_event() would be a silent no-op"
        )

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

        # Serialize grant BEFORE on_load() is called; locks it in the current
        # epoch before threads spawned in on_load() try to re-register (ADR-0233 D5)
        if boot_layer is BootLayer.COMPLIANCE:
            _record_granted_compliance(plugin_id)

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
    # ADR-0356 P2.5 — the Console as a bundled web_surface, symmetric to the
    # bridges: injected only when its own ship-dark flag is on, skipped for a
    # type-filtered worker call that does not want web_surface.
    if only_types is None or "web_surface" in only_types:
        bundled = bundled + _bundled_console_declaration(tenant_id, declared + bundled)
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


def _bundled_console_declaration(
    tenant_id: str, declared: list[dict]
) -> list[dict]:
    """The bundled Console web surface, as a declaration (ADR-0356, P2.5).

    Symmetric to :func:`_bundled_bridge_declarations`. Three properties preserved:

    * **Off is quiet and total.** With ``console_web_surface_plugin`` off — the
      default — nothing is injected. standalone.py still mounts the SPA the old
      hard-wired way, so a default install is byte-for-byte unchanged.
    * **The operator's own entry always wins.** A ``{id: console}`` already in
      ``spec.plugins.installed`` is skipped here, so an operator can park or
      override the Console surface.
    * **Declaring is not mounting.** This entry makes the Console *loadable* as a
      plugin. Actually mounting the SPA THROUGH the plugin (instead of the
      standalone.py hard-wire) is the loader's job — P7, deliberately not here.
    """
    try:
        from .console.plugin import console_flag_enabled  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — no console plugin in this install (stripped wheel)
        return []
    if not console_flag_enabled(tenant_id):
        return []
    try:
        from .console.registry_entries import declaration_entry  # noqa: PLC0415

        already = {e.get("id") for e in declared if isinstance(e, dict)}
        entry = declaration_entry()
        return [] if entry.get("id") in already else [entry]
    except Exception as exc:  # noqa: BLE001 — a bad declaration must not stop boot
        log.error(
            "bundled console declaration unavailable for %r (%s) — Console keeps "
            "being served as before",
            tenant_id, exc,
        )
        return []


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


#: Root of the bundled builtin plugins shipped IN THE WHEEL, one nested tree per
#: category: ``core/plugins/buildin/<category>/<name>/plugin.yaml`` (+ provider.py).
#:
#: This is the CODE root, distinct from the repo-root ``/buildin`` the legacy
#: marketplace directory-scanner reads (which holds plugin.json INDEX metadata,
#: no provider code). ``bootstrap.py`` lives at ``core/plugins/corvin_plugins/``,
#: so ``parents[1]`` is ``core/plugins/``.
_BUILTIN_ROOT: Path = Path(__file__).resolve().parents[1] / "buildin"


def _marketplace_root() -> Path:
    """Root of plugins whose SOURCE lives in the Corvin-Marketplace repo, not in
    CorvinOS (operator rule — keep the CorvinOS codebase small; plugin source is owned
    by the marketplace, CorvinOS only loads it). Resolved from ``CORVIN_MARKETPLACE_ROOT``
    if set, else a sibling ``../Corvin-Marketplace/plugins/buildin`` checkout next to the
    CorvinOS repo. A missing path scans to ``[]`` (behaviour-neutral)."""
    import os
    env = os.environ.get("CORVIN_MARKETPLACE_ROOT")
    if env:
        return Path(env)
    # bootstrap.py is core/plugins/corvin_plugins/ → parents[3] is the CorvinOS repo root;
    # its sibling is the Corvin-Marketplace checkout.
    return Path(__file__).resolve().parents[3].parent / "Corvin-Marketplace" / "plugins" / "buildin"


def _builtin_plugin_dirs(root: Path) -> list[Path]:
    """Every directory under ``root`` that holds a ``plugin.yaml`` (recursive).

    The marketplace's own directory scan (``core/plugins/marketplace.py``) looks
    only at the DIRECT children of a ``buildin/`` for ``plugin.json``; a builtin
    organised as ``buildin/<category>/<name>/plugin.yaml`` is therefore invisible
    to it. This walk finds the load manifest wherever it is nested, which is the
    layout the ADR-0598 plugin actually ships in.
    """
    if not root.is_dir():
        return []
    return sorted({p.parent for p in root.rglob("plugin.yaml")})


def _load_builtin_class(plugin_dir: Path, plugin_id: str) -> type | None:
    """Import a builtin plugin's provider module BY FILE PATH; return its class.

    A builtin dir name is hyphenated (``semantic-context-retriever``) and its
    code lives in ``provider.py``, so neither ``importlib.import_module`` (needs a
    dotted, identifier-legal path) nor the tenant-registry loader (looks for
    ``plugin.py``) can reach it. Loading by file location is the one mechanism
    that can. Returns the class whose ``plugin_id`` matches the manifest, else the
    first class exposing the ``plugin_id``/``on_load`` lifecycle shape, else None.
    """
    import importlib.util
    import inspect
    import sys

    for fname in ("provider.py", "plugin.py"):
        cand = plugin_dir / fname
        if not cand.is_file():
            continue
        mod_name = f"corvin_builtin_{plugin_id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(mod_name, cand)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # Register under a stable name so dataclasses/pickling inside the module
        # resolve, mirroring _load_tenant_plugin. Overwriting a prior load is
        # fine: builtin discovery is idempotent per boot.
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)

        exact: type | None = None
        fallback: type | None = None
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != mod_name:
                continue  # only classes DEFINED here, not imported ones
            if not (hasattr(obj, "plugin_id") and hasattr(obj, "on_load")):
                continue
            if getattr(obj, "plugin_id", None) == plugin_id:
                exact = obj
                break
            if fallback is None:
                fallback = obj
        return exact or fallback
    return None


def bootstrap_builtin(
    *,
    tenant_id: str,
    corvin_home: Path,
    tenant_config: dict | None = None,
    root: Path | None = None,
    **registries: Any,
) -> list[str]:
    """Discover + load the bundled builtin plugins shipped under ``buildin/``.

    The gap this closes: a builtin plugin under
    ``core/plugins/buildin/<category>/<name>/`` with a ``plugin.yaml`` + hyphenated
    directory + ``provider.py`` matched NONE of the three prior load paths —
    ``bootstrap_global`` (code-registered only), ``bootstrap_declared`` (needs a
    dotted ``class_path`` an importlib call can resolve — a hyphenated dir cannot
    be one), and the runtime registry (loads ``plugin.py`` from a per-tenant
    install dir). So the ADR-0598 retriever shipped in the wheel, registered as an
    ADR-0599 ``context_retriever`` provider, and yet nothing ever called its
    ``on_load`` → ``set_active``: the CEL/TDE seams stayed on the passthrough.

    This is that path. It is ADDITIVE and behaviour-neutral where there are no
    builtin manifests (``_builtin_plugin_dirs`` returns ``[]`` → ``[]``). Each
    discovered plugin is:

    * VALIDATED through the ADR-0247 manifest gate — a manifest that fails the
      gate is skipped and audited, never loaded (the gate is not bypassed);
    * loaded with ``origin="builtin"`` — a FACT here, not a claim: these dirs ship
      in the wheel, so the ADR-0250 provider-slot gate's ``origin_builtin``
      exemption applies (a ``context_retriever`` is a process-wide provider slot;
      an ``origin=None`` declarative load of it is refused on a multi-tenant
      install, which is why the builtin path must assert its real provenance);
    * registered on ``boot_layer=installed`` through the SAME ``_register_instance``
      the other paths use, so ``register()`` runs ``on_load(ctx)`` under the
      loading context — which is what lets ``set_active`` record slot ownership.

    Precedence: an id already declared in ``tenant.corvin.yaml`` or already
    registered wins (``_register_instance`` treats an already-registered id as
    loaded), so an operator's explicit config is never shadowed by discovery.

    Opt-out: ``spec.plugins.builtin_disabled: [<id>, ...]`` skips named builtins,
    and ``spec.plugins.load_builtin: false`` skips discovery entirely. Both honour
    the Phase 2b "builtin always active locally" default — absent config loads
    everything discovered.
    """
    scan_root = root if root is not None else _BUILTIN_ROOT
    plugin_dirs = _builtin_plugin_dirs(scan_root)
    if root is None:
        # Also discover plugins whose SOURCE lives in the Corvin-Marketplace repo, not in
        # CorvinOS (operator rule). Same load contract (plugin.yaml + provider.py); deduped
        # by directory. An explicit test ``root`` scans only that root (isolation).
        for _d in _builtin_plugin_dirs(_marketplace_root()):
            if _d not in plugin_dirs:
                plugin_dirs.append(_d)
    if not plugin_dirs:
        return []

    config = tenant_config if tenant_config is not None else load_tenant_spec(
        tenant_id, corvin_home
    )
    plugins_cfg = (config.get("spec") or {}).get("plugins") or {}
    if plugins_cfg.get("load_builtin") is False:
        log.debug("spec.plugins.load_builtin is false — skipping builtin discovery")
        return []
    disabled = {
        str(x) for x in (plugins_cfg.get("builtin_disabled") or []) if x
    }
    declared_ids = {
        e.get("id")
        for e in (plugins_cfg.get("installed") or [])
        if isinstance(e, dict)
    }

    from .validation import validate_manifest_file

    loaded: list[str] = []
    for plugin_dir in plugin_dirs:
        manifest_path = plugin_dir / "plugin.yaml"
        try:
            manifest = load_from_manifest_safe(manifest_path)
        except Exception as exc:  # noqa: BLE001 — a bad manifest skips one plugin
            log.error(
                "builtin manifest unreadable at %s (%s) — skipping",
                plugin_dir, type(exc).__name__,
            )
            continue
        plugin_id = str(manifest.get("plugin_id") or "").strip()
        if not plugin_id:
            log.error("builtin at %s has no plugin_id — skipping", plugin_dir)
            continue
        if plugin_id in disabled:
            log.info("builtin %r opted out via builtin_disabled — skipping", plugin_id)
            continue
        if plugin_id in declared_ids:
            log.debug(
                "builtin %r also declared in spec.plugins.installed — the "
                "declaration wins; discovery skips it", plugin_id,
            )
            continue

        # ADR-0247 gate — a manifest that does not validate is NOT loaded.
        report = validate_manifest_file(manifest_path)
        if not report.ok:
            log.error(
                "builtin %r manifest failed the ADR-0247 gate (%d error(s)) — skipping",
                plugin_id, len(report.errors),
            )
            _audit_degradation(tenant_id, "plugin.load_failed", {
                "plugin_id": plugin_id, "tenant_id": tenant_id,
                "reason": "manifest_invalid",
            })
            continue

        cls = _load_builtin_class(plugin_dir, plugin_id)
        if cls is None:
            log.error(
                "builtin %r: no loadable provider class in %s — skipping",
                plugin_id, plugin_dir,
            )
            _audit_degradation(tenant_id, "plugin.load_failed", {
                "plugin_id": plugin_id, "tenant_id": tenant_id,
                "reason": "no_provider_class",
            })
            continue
        try:
            instance = cls()
        except Exception as exc:  # noqa: BLE001 — one bad builtin must not stop boot
            log.error(
                "builtin %r failed to instantiate (%s) — skipping",
                plugin_id, type(exc).__name__,
            )
            _audit_degradation(tenant_id, "plugin.load_failed", {
                "plugin_id": plugin_id, "tenant_id": tenant_id,
                "reason": "instantiate_failed", "error_type": type(exc).__name__,
            })
            continue

        if _register_instance(
            instance,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            corvin_home=corvin_home,
            boot_layer=BootLayer.INSTALLED,
            # These dirs ship in the wheel, so builtin is a fact, not a claim —
            # the one place the ADR-0250 slot gate's origin exemption is honest.
            origin="builtin",
            **registries,
        ):
            loaded.append(plugin_id)

    if loaded:
        log.info(
            "loaded %d builtin plugin(s) for tenant %r: %s",
            len(loaded), tenant_id, loaded,
        )
    return loaded


def load_from_manifest_safe(manifest_path: Path) -> dict:
    """Read a plugin.yaml manifest into a dict, tolerating an absent PyYAML."""
    from .loader import load_from_manifest

    data = load_from_manifest(manifest_path)
    return data if isinstance(data, dict) else {}


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

    NOTE: this is the RUNTIME registry path. Loads from BOTH:
    * state.TenantRegistry (existing PluginLifecycle path)
    * tenant_plugins.TenantPluginRegistry (Phase 1d path)

    The declarative ``spec.plugins.installed`` path is :func:`bootstrap_declared`,
    and it runs FIRST at boot — see :func:`bootstrap_all` for the precedence rule.
    """
    if not lifecycle_enabled:
        log.debug("plugin_runtime_lifecycle off — skipping registry bootstrap")
        return []

    loaded: list[str] = []

    # Phase 1d (NEW): Load from TenantPluginRegistry (tenant/plugins/installed/)
    loaded.extend(_bootstrap_tenant_plugin_registry(
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        compute_registry=compute_registry,
        engine_factory=engine_factory,
        channel_registry=channel_registry,
    ))

    # Existing path: Load from state.TenantRegistry (for backwards compat)
    loaded.extend(_bootstrap_state_registry(
        tenant_id=tenant_id,
        corvin_home=corvin_home,
        compute_registry=compute_registry,
        engine_factory=engine_factory,
        channel_registry=channel_registry,
    ))

    if loaded:
        log.info("loaded %d plugin(s) for tenant %r: %s", len(loaded), tenant_id, loaded)
    return loaded


def _bootstrap_tenant_plugin_registry(
    *,
    tenant_id: str,
    corvin_home: Path,
    compute_registry: Any | None = None,
    engine_factory: Any | None = None,
    channel_registry: Any | None = None,
) -> list[str]:
    """Phase 1d: Load from TenantPluginRegistry (tenant/plugins/installed/)."""
    from .tenant_plugins import TenantPluginRegistry

    try:
        registry = TenantPluginRegistry(tenant_id=tenant_id)
        registry.load_registry()
        plugins = registry.list_plugins()
    except Exception as exc:  # noqa: BLE001
        # A corrupt or unreadable registry must not take the process down
        log.debug(
            "plugin registry (TenantPluginRegistry) unreadable for tenant %r (%s) — "
            "no plugins loaded from tenant/plugins/installed/",
            tenant_id,
            type(exc).__name__,
        )
        # Don't audit here — this registry is optional; state.py's is the primary one
        return []

    loaded: list[str] = []
    for plugin_entry in plugins:
        if not plugin_entry.enabled:
            log.debug("plugin %r is disabled — skipping", plugin_entry.plugin_id)
            continue

        plugin_id = plugin_entry.plugin_id
        plugin_path = registry.get_plugin_path(plugin_id)

        if not plugin_path:
            log.warning("plugin %r path not found on disk", plugin_id)
            _audit_degradation(tenant_id, "plugin.load_failed", {
                "plugin_id": plugin_id,
                "tenant_id": tenant_id,
                "reason": "plugin_path_not_found",
            })
            continue

        if _load_tenant_plugin(
            plugin_id,
            plugin_path,
            tenant_id=tenant_id,
            corvin_home=corvin_home,
            compute_registry=compute_registry,
            engine_factory=engine_factory,
            channel_registry=channel_registry,
        ):
            loaded.append(plugin_id)

    if loaded:
        log.info(
            "loaded %d plugin(s) from TenantPluginRegistry for tenant %r: %s",
            len(loaded), tenant_id, loaded
        )
    return loaded


def _bootstrap_state_registry(
    *,
    tenant_id: str,
    corvin_home: Path,
    compute_registry: Any | None = None,
    engine_factory: Any | None = None,
    channel_registry: Any | None = None,
) -> list[str]:
    """Load from state.TenantRegistry (existing PluginLifecycle path, for backwards compat)."""
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
            "plugin registry (state.TenantRegistry) unusable for tenant %r (%s) — "
            "no plugins loaded",
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
    except AttributeError as exc:
        # AttributeError likely means the registry.yaml was saved by a different
        # tool (e.g., TenantPluginRegistry uses a different schema). This is not
        # a corruption of state.TenantRegistry's own format, just a schema mismatch.
        # Log at DEBUG since this is expected when using TenantPluginRegistry.
        log.debug(
            "plugin registry (state.TenantRegistry) schema mismatch for tenant %r "
            "(%s) — this is normal when using TenantPluginRegistry; "
            "continuing without state registry plugins",
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
        log.info(
            "loaded %d plugin(s) from state.TenantRegistry for tenant %r: %s",
            len(loaded), tenant_id, loaded
        )
    return loaded


def _load_tenant_plugin(
    plugin_id: str,
    plugin_path: Path,
    *,
    tenant_id: str,
    corvin_home: Path,
    compute_registry: Any | None = None,
    engine_factory: Any | None = None,
    channel_registry: Any | None = None,
) -> bool:
    """Load a tenant plugin from a directory and register it.

    Looks for plugin.py in the plugin directory, loads it as a module, and
    registers the plugin in the global registry.

    Returns True if successfully loaded, False otherwise (logged and audited).
    """
    import importlib.util
    import sys

    plugin_file = plugin_path / "plugin.py"
    if not plugin_file.exists():
        log.error(
            "plugin %r: plugin.py not found in %s — skipping",
            plugin_id, plugin_path
        )
        _audit_degradation(tenant_id, "plugin.load_failed", {
            "plugin_id": plugin_id,
            "tenant_id": tenant_id,
            "reason": "plugin_py_not_found",
        })
        return False

    try:
        # Load the plugin module from disk
        spec = importlib.util.spec_from_file_location(
            f"tenant_plugin_{plugin_id}", plugin_file
        )
        if not spec or not spec.loader:
            raise RuntimeError(f"Cannot load spec for plugin {plugin_id}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[f"tenant_plugin_{plugin_id}"] = module
        spec.loader.exec_module(module)

        # Plugin loaded: now instantiate and register it.
        # The plugin module should export a Plugin class or have setup() function.
        plugin_class = getattr(module, "Plugin", None)
        if not plugin_class:
            # No explicit Plugin class — try a setup() hook pattern
            setup = getattr(module, "setup", None)
            if setup and callable(setup):
                # setup(context) pattern: build context and call setup
                ctx = build_context(
                    plugin_id=plugin_id,
                    tenant_id=tenant_id,
                    corvin_home=corvin_home,
                    compute_registry=compute_registry,
                    engine_factory=engine_factory,
                    channel_registry=channel_registry,
                )
                setup(ctx)
                log.info("loaded tenant plugin %r (setup hook)", plugin_id)
                return True
            else:
                log.error(
                    "plugin %r: no Plugin class or setup() function — skipping",
                    plugin_id
                )
                _audit_degradation(tenant_id, "plugin.load_failed", {
                    "plugin_id": plugin_id,
                    "tenant_id": tenant_id,
                    "reason": "no_plugin_class_or_setup",
                })
                return False

        # Plugin class found: instantiate and register
        try:
            instance = plugin_class()
        except Exception as exc:  # noqa: BLE001
            log.error(
                "plugin %r failed to instantiate (%s) — skipping",
                plugin_id, type(exc).__name__,
            )
            _audit_degradation(tenant_id, "plugin.load_failed", {
                "plugin_id": plugin_id,
                "tenant_id": tenant_id,
                "reason": "instantiate_failed",
                "error_type": type(exc).__name__,
            })
            return False

        # Register the plugin instance
        success = _register_instance(
            instance,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            corvin_home=corvin_home,
            boot_layer=BootLayer.INSTALLED,
            origin="tenant",
            compute_registry=compute_registry,
            engine_factory=engine_factory,
            channel_registry=channel_registry,
        )
        if success:
            log.info("loaded tenant plugin %r from %s", plugin_id, plugin_path)
        return success

    except Exception as exc:  # noqa: BLE001
        log.error(
            "plugin %r failed to load (%s) — skipping",
            plugin_id, type(exc).__name__,
        )
        _audit_degradation(tenant_id, "plugin.load_failed", {
            "plugin_id": plugin_id,
            "tenant_id": tenant_id,
            "reason": "load_failed",
            "error_type": type(exc).__name__,
        })
        return False


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
    An evaluator that RAISES refuses (fail-closed, audited): "allowing" on an
    exception let any plugin take a process-wide provider slot on a multi-tenant
    install the moment the check broke (2026-09-03 finding A4).
    """
    plugin_type = getattr(instance, "plugin_type", "") or ""
    try:
        from . import tenant_scope

        decision = tenant_scope.evaluate(
            plugin_type=plugin_type, origin=origin, corvin_home=corvin_home
        )
    except Exception as exc:  # noqa: BLE001 - a broken check costs the slot, not the boot
        log.error(
            "plugin %r (type=%s): tenant-scope evaluation failed (%s) — refusing "
            "the provider slot (fail-closed)",
            plugin_id, plugin_type, type(exc).__name__,
        )
        _audit_degradation(tenant_id, "plugin.provider_slot_refused", {
            "plugin_id": plugin_id,
            "plugin_type": plugin_type,
            "origin": origin or "unknown",
            "tenant_id": tenant_id,
            "reason": "evaluation_failed",
            "error_type": type(exc).__name__,
        })
        return False

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
        from .registry import PluginLoadTimeout

        # An on_load that overran LOAD_DEADLINE_S is a load failure with its own
        # reason code, so an operator can tell "raised" from "hung" in the chain.
        reason = "on_load_timeout" if isinstance(exc, PluginLoadTimeout) else "register_failed"
        log.error(
            "plugin %r failed to register (%s, %s) — skipping",
            plugin_id,
            reason,
            type(exc).__name__,
        )
        _audit_degradation(tenant_id, "plugin.load_failed", {
            "plugin_id": plugin_id, "tenant_id": tenant_id,
            "reason": reason, "error_type": type(exc).__name__,
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
    3. **Builtin** — the plugins shipped under ``core/plugins/buildin/`` and
       discovered by :func:`bootstrap_builtin`. Loaded with ``origin=builtin`` and
       default-on locally (opt-out via ``spec.plugins.builtin_disabled`` /
       ``load_builtin: false``). An id already declared in step 2 wins.
    4. **Runtime registry** — Console-installed plugins, gated on the
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
    builtin = bootstrap_builtin(
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
    # Preserve order: globals, then declarations, then builtins, then
    # registry-only ids.
    seen = set(global_ids)
    ordered = list(global_ids)
    for pid in declared + builtin + runtime:
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
    except Exception as exc:  # noqa: BLE001 - trust must not break the boot: it costs the plugin
        # Fail-closed (finding A4): an evaluator that cannot answer has not
        # answered "yes". Audited, because a plugin that never loads with only a
        # log line is the failure mode this module keeps producing.
        log.error(
            "plugin %r: trust evaluation failed (%s) — refusing to load (fail-closed)",
            record.plugin_id, type(exc).__name__,
        )
        _audit_degradation(tenant_id, "plugin.load_refused", {
            "plugin_id": record.plugin_id,
            "tenant_id": tenant_id,
            "reason": "trust_evaluation_failed",
            "error_type": type(exc).__name__,
        })
        return False

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


def boot_platform() -> list[str]:
    """The compliance-critical boot sequence, for every host that serves CorvinOS.

    Three steps, in this order and for these reasons:

    1. :func:`assert_compliance` — the fail-closed tripwires.  Runs FIRST so a
       broken core audit writer stops the boot before anything else happens.
    2. :func:`bootstrap_all` — load the tenant's plugins.  Best-effort per the
       ADR-0030 contract, with ONE exception: ``GlobalComplianceLoadFailed`` is
       re-raised, because swallowing a failed ``boot_layer=compliance`` plugin
       would be the "compliance-off mode" the baseline forbids, one log line
       deep.
    3. ``assert_post_boot`` — asks whether anything put itself on the compliance
       boot layer that :func:`bootstrap_global` did not grant.  Can only be asked
       AFTER the plugins are loaded, which is why it is not folded into step 1.

    **This function exists because the sequence had two homes and only one
    caller.** It lived inline in ``corvin_gateway.app``; the standalone console
    (``corvin_console.standalone:create_app``, which is what ``corvinos-serve``
    runs and what ``install.sh`` launches) had copied the license-load block
    above it and stopped before the compliance block.  A console with a
    deliberately corrupted audit hash chain therefore booted and served
    requests, while the very same tripwire — invoked by hand in that same
    process — correctly refused.  The mechanism was sound; nothing called it.
    That is the defect class ADR-0233 is named for, and the fix is one sequence
    with two callers rather than two sequences that agree until one is edited.

    There is deliberately NO flag and NO override, matching every other tripwire
    (CLAUDE.md § Compliance Baseline).  Returns the ids of the plugins that
    loaded, so the caller can hand them to :func:`shutdown`.

    Raises whatever step 1 or step 3 raises — the caller MUST let it propagate.
    """
    assert_compliance()  # raises TripwireError -> boot aborts

    # ADR-0233 D5: Increment registration epoch before loading plugins.
    # This prevents threads spawned during the PREVIOUS boot from re-registering
    # and escalating privilege in the current boot.
    from .registry import advance_registration_epoch
    advance_registration_epoch()

    loaded: list[str] = []
    _tid = "_default"
    try:
        from corvin_core import feature_flags as _flags  # noqa: PLC0415
        from forge.paths import corvin_home as _corvin_home  # noqa: PLC0415
        from forge.tenants import current_tenant as _current_tenant  # noqa: PLC0415

        _tid = _current_tenant()
        # BOTH load paths: the declarative spec.plugins.installed (ADR-0030
        # Phase 7, always honoured — writing it into a version-controlled tenant
        # config IS the opt-in) and the runtime registry (flag-gated).
        loaded = bootstrap_all(
            tenant_id=_tid,
            corvin_home=_corvin_home(),
            lifecycle_enabled=_flags.is_enabled("plugin_runtime_lifecycle", _tid),
        )
    except Exception as exc:  # noqa: BLE001
        # Matched by NAME rather than imported at the top so a stripped install
        # without the compliance plugin machinery still boots.
        if type(exc).__name__ == "GlobalComplianceLoadFailed":
            raise
        # Into the chain, not only the log (finding A9): a platform that booted
        # with ZERO plugins because bootstrap_all() raised must be
        # distinguishable, in the audit trail, from one that had none declared.
        _audit_degradation(_tid, "plugin.bootstrap_skipped", {
            "tenant_id": _tid, "error_type": type(exc).__name__,
        })
        log.warning("plugin bootstrap skipped", exc_info=True)

    # 2b. ACP Skills registry (ADR-0532/0544). The global Skills registry that
    # the gateway health collector, the console headless check, /build, the vibe
    # pipeline route and /capabilities all read from was never POPULATED by any
    # host until 2026-09-03 — every consumer got "Skill not found" and fell back
    # to off. It is wired here, in the one shared sequence, with the same audit
    # writer the plugins receive, so Skill decisions join the hash chain.
    _boot_skills_registry()

    try:
        from corvin_compliance_reports.tripwire import (  # noqa: PLC0415
            assert_post_boot as _assert_post_boot,
        )
    except ImportError:
        # A stripped layout without the compliance package: assert_compliance()
        # above already handled that case the same way.
        _assert_post_boot = None  # type: ignore[assignment]
    if _assert_post_boot is not None:
        _assert_post_boot()  # raises TripwireError -> boot aborts

    # 4. ADR-0231 Stage 2/3 — health polling + self-healing. Started HERE, in
    # the shared sequence, after the post-boot tripwire has accepted the boot:
    # until 2026-09-03 the collector and healer were wired only in the gateway
    # lifespan, so the standalone console (corvinos-serve, install.sh) booted
    # every plugin and never polled one (finding A6).
    start_health_monitoring(loaded)

    return loaded


# ── ADR-0231 Stage 2/3 — health monitoring, shared by every host ─────────────

#: The one running collector for this process, or None. Held here so
#: :func:`shutdown` (and the hosts' lifespans) can stop it without each host
#: keeping its own reference — a second copy is how the console lost it.
_HEALTH_COLLECTOR: Any | None = None


def health_collector() -> Any | None:
    """The running :class:`~corvin_plugins.health.HealthCollector`, or None."""
    return _HEALTH_COLLECTOR


def _self_healing_enabled(tenant_id: str) -> bool:
    """Lazy per-tenant read of the ``plugin_self_healing`` flag (default OFF).

    Read on every healing decision, never cached, so toggling it in the Console
    takes effect without a restart. Absent or broken flag registry reads as
    off — an autonomous actor that restarts plugins must be switched ON by an
    operator, never assumed.
    """
    try:
        from corvin_core import feature_flags as _flags  # noqa: PLC0415

        return bool(_flags.is_enabled("plugin_self_healing", tenant_id))
    except Exception:  # noqa: BLE001
        return False


def start_health_monitoring(plugin_ids: Iterable[str]) -> Any | None:
    """Start the plugin health collector (+ healer) for this process, once.

    Gated on the ``os.plugin_health_monitoring`` Skill (ADR-0532 ACP), which
    :func:`_boot_skills_registry` populates earlier in :func:`boot_platform`.
    Skill off (the default) means NO timer is created at all: the ``/plugins``
    health route still answers from the breaker state, which costs nothing.

    Requires a running asyncio loop — both shipped hosts call
    :func:`boot_platform` from inside an async lifespan. A synchronous caller
    (a probe, a CLI) gets ``None`` and a log line, not a crash: monitoring is a
    runtime service, not a compliance mechanism.

    Returns the collector, or ``None`` when monitoring was not started. Never
    raises. The Console's ``/plugins`` route receives the collector through a
    lazy, ImportError-guarded import so this helper works on a headless core.
    """
    global _HEALTH_COLLECTOR

    if _HEALTH_COLLECTOR is not None:
        return _HEALTH_COLLECTOR

    import asyncio  # noqa: PLC0415

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        log.info(
            "plugin health monitoring not started: no running event loop "
            "(synchronous boot_platform caller)"
        )
        return None

    try:
        from core.skills.skill_registry_phase1 import get_registry as _get_registry  # noqa: PLC0415
        from forge.paths import corvin_home as _hc_home  # noqa: PLC0415
        from forge.tenants import current_tenant as _hc_tenant  # noqa: PLC0415

        from .healing import HealingOrchestrator  # noqa: PLC0415
        from .health import HealthCollector  # noqa: PLC0415

        tenant_id = _hc_tenant()
        result = _get_registry().execute(
            "os.plugin_health_monitoring", {"tenant_id": tenant_id}
        )
        enabled = result.status == "success" and bool(
            (result.output or {}).get("enabled")
        )
        if not enabled:
            log.info(
                "plugin health monitoring off (os.plugin_health_monitoring: %s)",
                getattr(result, "status", "?"),
            )
            return None

        audit_emit = build_context(
            plugin_id="health-collector",
            tenant_id=tenant_id,
            corvin_home=_hc_home(),
        ).audit_emit
        # ADR-0231 Stage 3 — self-healing hangs off the collector (the only
        # poller) rather than a second timer; gated on its OWN flag, lazily.
        healer = HealingOrchestrator(
            enabled=lambda: _self_healing_enabled(tenant_id),
            audit_emit=audit_emit,
        )
        collector = HealthCollector(audit_emit=audit_emit, healer=healer)
        collector.start()
        _HEALTH_COLLECTOR = collector
    except Exception:  # noqa: BLE001 - monitoring must not cost the boot
        log.warning(
            "plugin health collector not started (Skills registry unavailable?)",
            exc_info=True,
        )
        return None

    _publish_collector(collector)
    log.info(
        "plugin health monitoring started for %d plugin(s)", len(list(plugin_ids))
    )
    return collector


def _publish_collector(collector: Any | None) -> None:
    """Hand the collector to the Console's ``/plugins`` route, if the Console
    package is present. Headless core (ADR-0241): absent Console is not an error."""
    try:
        from corvin_console.routes import plugins as _plugins_route  # noqa: PLC0415
    except ImportError:
        return
    try:
        _plugins_route.set_collector(collector)
    except Exception:  # noqa: BLE001
        log.warning("could not publish the health collector to the Console route", exc_info=True)


async def stop_health_monitoring() -> None:
    """Stop the collector started by :func:`start_health_monitoring` (awaits it).

    Call BEFORE :func:`shutdown`: a poll must not land on a plugin that is
    halfway through ``on_unload()``. Idempotent; never raises.
    """
    global _HEALTH_COLLECTOR

    collector, _HEALTH_COLLECTOR = _HEALTH_COLLECTOR, None
    if collector is None:
        return
    try:
        await collector.stop()
    except Exception:  # noqa: BLE001
        log.warning("health collector did not stop cleanly", exc_info=True)
    _publish_collector(None)


def _stop_health_monitoring_sync() -> None:
    """Synchronous best-effort stop, for a :func:`shutdown` caller that did not
    await :func:`stop_health_monitoring` first: signals the loop, cancels the
    task, clears the handles. Cannot await the task from here."""
    global _HEALTH_COLLECTOR

    collector, _HEALTH_COLLECTOR = _HEALTH_COLLECTOR, None
    if collector is None:
        return
    try:
        stop_event = getattr(collector, "_stop", None)
        if stop_event is not None:
            stop_event.set()
        task = getattr(collector, "_task", None)
        if task is not None:
            task.cancel()
    except Exception:  # noqa: BLE001
        pass
    _publish_collector(None)


def _boot_skills_registry() -> list[str]:
    """Populate the ACP Skills registry for the boot tenant (best-effort, logged).

    Absent ``core.skills`` (stripped install) is tolerated like an absent plugin
    package; a present-but-failing registration is logged at ERROR so the
    "Skill not found" fallbacks downstream are never silent again.
    """
    try:
        from core.skills.boot import boot_skills  # noqa: PLC0415
    except ImportError:
        log.debug("core.skills absent — ACP Skills registry not populated")
        return []
    try:
        from forge.tenants import current_tenant as _current_tenant  # noqa: PLC0415

        tenant_id = _current_tenant()
    except Exception:  # noqa: BLE001
        tenant_id = "_default"
    try:
        return boot_skills(tenant_id=tenant_id, audit_emit=_default_audit_emit(tenant_id))
    except Exception:  # noqa: BLE001
        log.error("ACP Skills registry boot FAILED — Skill consumers will fall back to off", exc_info=True)
        return []


def shutdown(plugin_ids: Iterable[str]) -> None:
    """Unregister plugins on graceful shutdown, detaching their provider slots.

    Stops health monitoring first (best-effort, synchronous) if the host did not
    already ``await stop_health_monitoring()``.
    """
    from .registry import unregister

    _stop_health_monitoring_sync()
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
    "boot_platform",
    "bootstrap_all",
    "bootstrap_builtin",
    "bootstrap_declared",
    "bootstrap_global",
    "bootstrap_tenant",
    "build_context",
    "health_collector",
    "load_tenant_spec",
    "register_global_plugin",
    "shutdown",
    "start_health_monitoring",
    "stop_health_monitoring",
]
