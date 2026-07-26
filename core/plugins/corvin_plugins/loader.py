"""Plugin discovery: entry_points and explicit class-path loading (ADR-0030)."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
from pathlib import Path
from typing import Any, Callable, Collection

from .protocol import CorvinPlugin

log = logging.getLogger(__name__)


# ── Entry-point loader ────────────────────────────────────────────────────────
#
# Enumerating an entry point and LOADING one are two very different acts, and
# keeping them apart is the whole point of this section.  ``entry_points()``
# only reads distribution metadata: it yields ``name`` / ``value`` / ``group``
# strings and imports nothing.  ``ep.load()`` imports the module named in
# ``value`` and therefore executes third-party code at module scope.
#
# ``spec.plugins.auto_discover_entry_points`` defaults to false precisely so
# that the second act needs an operator decision (bootstrap.bootstrap_declared:
# "on a machine with third-party packages installed, flipping it means loading
# code nobody listed").  Any code path that calls ``ep.load()`` without that
# opt-in, or without a declaration that names this exact entry point, hands the
# default back for free.


def _iter_entry_points(group: str) -> list[importlib.metadata.EntryPoint]:
    """Metadata for the group.  Reads only — imports nothing."""
    try:
        return list(importlib.metadata.entry_points(group=group))
    except Exception:
        log.exception("failed to read entry_points group %r", group)
        return []


def _load_one(ep: importlib.metadata.EntryPoint, group: str) -> type | None:
    """``ep.load()``, i.e. IMPORT the third-party module.  None on failure."""
    try:
        cls = ep.load()
    except Exception:  # noqa: BLE001
        log.exception("failed to load entry_point %r in group %r", ep.name, group)
        return None
    log.debug("loaded entry_point %r -> %r", ep.name, cls)
    return cls


def load_from_entry_points(
    group: str = "corvin.plugins",
    *,
    names: Collection[str] | None = None,
) -> list[type]:
    """Load plugin classes declared under the given entry-point group.

    Returns a list of plugin *classes* (not instances).  Errors per entry
    point are logged and skipped so one broken package does not block others.

    ``names`` restricts which entry points are IMPORTED.  ``None`` (the
    default) keeps the historical behaviour — every entry point in the group is
    imported — and is only appropriate once the operator has opted in via
    ``auto_discover_entry_points``.  Passing a collection imports exactly the
    entry points whose ``name`` appears in it; the rest are enumerated and
    skipped without their module ever being executed.
    """
    classes: list[type] = []
    for ep in _iter_entry_points(group):
        if names is not None and ep.name not in names:
            log.debug(
                "entry_point %r in group %r not imported: no declaration needs "
                "it and auto_discover_entry_points is off",
                ep.name, group,
            )
            continue
        cls = _load_one(ep, group)
        if cls is not None:
            classes.append(cls)
    return classes


def resolve_declared_entry_points(
    needed: Collection[str],
    group: str = "corvin.plugins",
) -> dict[str, type]:
    """``{id: class}`` for the declared ids that name an installed entry point.

    The fallback map for ``spec.plugins.installed`` entries that carry no
    ``class_path``.  Only entry points whose ``name`` is in ``needed`` are
    imported — a declaration is the operator asking for that one package, which
    is a different thing from asking for every package on the machine.

    Keyed by the entry-point NAME (the id the declaration matched on, as
    documented by :func:`discover_and_load`) and additionally by the loaded
    class's own ``plugin_id`` when it differs, so a distribution whose entry
    point is named after the package rather than the plugin still resolves.

    The one case this deliberately no longer covers is a declared id that
    matches *no* entry-point name but happens to equal the ``plugin_id``
    attribute of some other package's class: finding that requires importing
    every candidate, which is the cost the default exists to avoid.  Set
    ``auto_discover_entry_points: true`` if that is what you want.
    """
    wanted = {n for n in needed if n}
    resolved: dict[str, type] = {}
    if not wanted:
        return resolved
    for ep in _iter_entry_points(group):
        if ep.name not in wanted:
            continue
        cls = _load_one(ep, group)
        if cls is None:
            continue
        resolved[ep.name] = cls
        pid = getattr(cls, "plugin_id", None)
        if isinstance(pid, str) and pid and pid not in resolved:
            resolved[pid] = cls
    return resolved


# ── Class-path loader ─────────────────────────────────────────────────────────

def load_from_class_path(class_path: str) -> type:
    """Import and return a class given a dotted class path.

    Accepts two forms:
        "module.submodule:ClassName"   (preferred — colon separator)
        "module.submodule.ClassName"   (dot separator — last component is class)

    Raises ImportError or AttributeError on failure.
    """
    if ":" in class_path:
        module_path, class_name = class_path.rsplit(":", 1)
    else:
        module_path, class_name = class_path.rsplit(".", 1)

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls  # type: ignore[return-value]


# ── Manifest loader ───────────────────────────────────────────────────────────

def load_from_manifest(manifest_path: Path) -> dict:
    """Read a plugin.corvin.yaml (or .json) manifest and return a dict.

    Tries PyYAML first; falls back to json.load() if PyYAML is not available.
    """
    text = manifest_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]
        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    # Fallback: attempt JSON (works for .json manifests)
    return json.loads(text)


# ── Tenant-config driven discovery ───────────────────────────────────────────

def discover_and_load(
    tenant_config: dict,
    *,
    corvin_home: Path,
    on_error: Callable[[str, str, str], None] | None = None,
) -> list[CorvinPlugin]:
    """Discover and instantiate plugins declared in tenant_config.

    Reads ``tenant_config["spec"]["plugins"]["installed"]``.  Each entry must
    have an ``id`` key and optionally a ``class_path`` key.  If ``class_path``
    is absent the loader searches installed entry points for an entry whose
    name matches the ``id`` — and imports THAT one, nothing else.

    Also loads from entry points if
    ``tenant_config["spec"]["plugins"].get("auto_discover_entry_points")`` is
    True.  That flag is the only thing that makes this function import a
    package nobody listed; with it off, the set of modules imported here is a
    function of the declarations alone (see
    :func:`resolve_declared_entry_points`).

    Returns a list of plugin *instances* (no-arg constructor).  Failed loads
    are skipped — one bad declaration must not cost the operator the good ones.

    ``on_error(plugin_id, reason, error_type)`` is called for each skipped entry.
    This is the only trace a failed declaration leaves: the caller turns it into a
    hash-chained audit event, because a log line means "never configured" and
    "silently died at boot" look identical to whoever reads the trail later. The
    callback must not raise; a caller that lets it raise loses the good plugins too.
    """
    def _fail(pid: str, reason: str, error_type: str = "") -> None:
        if on_error is None:
            return
        try:
            on_error(pid, reason, error_type)
        except Exception:  # noqa: BLE001 - visibility must not break the load
            log.debug("on_error callback failed for %r", pid)

    plugins_cfg: dict[str, Any] = (
        tenant_config.get("spec", {}).get("plugins", {})
    )
    installed: list[dict] = plugins_cfg.get("installed", [])
    auto_ep: bool = bool(plugins_cfg.get("auto_discover_entry_points", False))

    instances: list[CorvinPlugin] = []

    # Build the fallback id→class map for declarations without a class_path.
    #
    # This used to run on ``auto_ep or installed``, which meant a SINGLE line in
    # spec.plugins.installed — the most common configuration there is — imported
    # every corvin.plugins entry point on the machine.  That is exactly the act
    # ``auto_discover_entry_points: false`` exists to prevent, and it happened
    # even for a declaration that names its own class_path and needs no
    # fallback at all.  Now: opt-in imports everything, otherwise only the entry
    # points a declaration actually has to resolve.
    ep_map: dict[str, type] = {}
    if auto_ep:
        for cls in load_from_entry_points():
            # Use plugin_id class attribute as key if available, else rely on
            # ep.name matching — classes loaded here get checked below.
            pid = getattr(cls, "plugin_id", None)
            if pid:
                ep_map[pid] = cls
    else:
        ep_map = resolve_declared_entry_points(
            [
                entry.get("id", "")
                for entry in installed
                if isinstance(entry, dict) and not entry.get("class_path")
            ]
        )

    for entry in installed:
        pid = entry.get("id", "")
        class_path: str | None = entry.get("class_path")
        try:
            if class_path:
                cls = load_from_class_path(class_path)
            elif pid in ep_map:
                cls = ep_map[pid]
            else:
                log.error(
                    "plugin %r: no class_path and no matching entry_point — skipping",
                    pid,
                )
                _fail(pid, "no_class_path_or_entry_point")
                continue
            instance: CorvinPlugin = cls()  # type: ignore[call-arg]
            instances.append(instance)
            log.debug("instantiated plugin %r from %r", pid, cls)
        except Exception as exc:  # noqa: BLE001
            # Class name, not log.exception(): the traceback carries str(exc), and a
            # plugin's __init__ error can quote a DSN or a path.
            log.error(
                "failed to instantiate plugin %r (%s) — skipping",
                pid, type(exc).__name__,
            )
            _fail(pid, "instantiate_failed", type(exc).__name__)

    if auto_ep:
        loaded_ids = {p.plugin_id for p in instances}
        for cls in load_from_entry_points():
            pid = getattr(cls, "plugin_id", None)
            if pid and pid not in loaded_ids:
                try:
                    instance = cls()  # type: ignore[call-arg]
                    instances.append(instance)
                    loaded_ids.add(pid)
                    log.debug("auto-discovered plugin %r from entry_points", pid)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "failed to auto-instantiate plugin %r (%s) — skipping",
                        pid, type(exc).__name__,
                    )
                    _fail(pid, "auto_instantiate_failed", type(exc).__name__)

    return instances
