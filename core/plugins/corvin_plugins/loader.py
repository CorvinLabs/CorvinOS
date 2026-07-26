"""Plugin discovery: entry_points and explicit class-path loading (ADR-0030)."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
from pathlib import Path
from typing import Any, Callable

from .protocol import CorvinPlugin

log = logging.getLogger(__name__)


# ── Entry-point loader ────────────────────────────────────────────────────────

def load_from_entry_points(group: str = "corvin.plugins") -> list[type]:
    """Load plugin classes declared under the given entry-point group.

    Returns a list of plugin *classes* (not instances).  Errors per entry
    point are logged and skipped so one broken package does not block others.
    """
    classes: list[type] = []
    try:
        eps = importlib.metadata.entry_points(group=group)
    except Exception:
        log.exception("failed to read entry_points group %r", group)
        return classes

    for ep in eps:
        try:
            cls = ep.load()
            classes.append(cls)
            log.debug("loaded entry_point %r -> %r", ep.name, cls)
        except Exception:  # noqa: BLE001
            log.exception("failed to load entry_point %r in group %r", ep.name, group)

    return classes


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
    name matches the ``id``.

    Also loads from entry points if
    ``tenant_config["spec"]["plugins"].get("auto_discover_entry_points")`` is
    True.

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

    # Build an entry-point name→class map for fallback resolution.
    ep_map: dict[str, type] = {}
    if auto_ep or installed:
        for cls in load_from_entry_points():
            # Use plugin_id class attribute as key if available, else rely on
            # ep.name matching — classes loaded here get checked below.
            pid = getattr(cls, "plugin_id", None)
            if pid:
                ep_map[pid] = cls

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
