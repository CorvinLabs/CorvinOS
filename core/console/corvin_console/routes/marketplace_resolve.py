"""Resolve a marketplace index-id to a local builtin plugin directory + manifest.

The console marketplace index (``operator/marketplace/index/plugins.json``,
ADR-0511) identifies a plugin as ``plugin:<tier>-<category>-<name>`` — e.g.
``plugin:buildin-memory-semantic_context_retriever``. The plugin's SOURCE lives
in the Corvin-Marketplace repo, not in CorvinOS (operator rule), one nested tree
per category: ``plugins/buildin/<category>/<name>/{plugin.yaml,provider.py}``.

The manifest's own ``plugin_id`` is a DIFFERENT string from the index-id — the
index directory-name convention uses underscores (``semantic_context_retriever``)
while the manifest id is hyphenated (``semantic-context-retriever``). This module
is the single place that bridges the two, so the install route can turn an index
id into a real ``PluginRecord`` and the mutation routes can turn it back into the
registry key.

**Security — origin is LOCATION-derived, never manifest-believed.** A record built
here is stamped ``origin=builtin`` because the directory resolved UNDER a trusted
``buildin/`` root (the marketplace checkout or the in-wheel ``core/plugins/buildin``),
NOT because a manifest said so. A community plugin cannot mint itself ``builtin`` by
writing ``origin: builtin`` in its manifest: it never resolves under a buildin root,
and the index tier is checked independently. A resolved candidate that escapes its
root (path traversal) is refused.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

# corvin_plugins lives outside the console package; import it the same guarded,
# path-appended way routes/plugins.py does (append, never insert(0)).
try:
    _core_plugins = Path(__file__).resolve().parents[3] / "plugins"
    if (_core_plugins / "corvin_plugins").is_dir() and str(_core_plugins) not in sys.path:
        sys.path.append(str(_core_plugins))
    from corvin_plugins import bootstrap as _bootstrap  # type: ignore[import-not-found]
    from corvin_plugins.manifest import (  # type: ignore[import-not-found]
        BootLayer,
        Locality,
        NetworkEgress,
        PIIRisk,
        PluginOrigin,
        PluginRecord,
    )
    from corvin_plugins.validation import (  # type: ignore[import-not-found]
        validate_manifest_file,
    )
    _AVAILABLE = True
except ImportError:  # pragma: no cover - stripped install without core/plugins
    _AVAILABLE = False


class MarketplaceResolveError(Exception):
    """The index-id could not be resolved to a local builtin plugin directory."""


def available() -> bool:
    return _AVAILABLE


def parse_index_id(index_id: str) -> Tuple[str, str, str] | None:
    """``plugin:<tier>-<category>-<name>`` → ``(tier, category, name)``.

    The only ``-`` separators are the two after the ``plugin:`` prefix — tier,
    category and name each use underscores internally (``security_compliance``,
    ``semantic_context_retriever``), so two left-splits recover the parts. Returns
    ``None`` for anything not shaped like a plugin index-id.
    """
    if not isinstance(index_id, str) or not index_id.startswith("plugin:"):
        return None
    body = index_id[len("plugin:"):]
    parts = body.split("-", 2)
    if len(parts) != 3 or not all(parts):
        return None
    tier, category, name = parts
    return tier, category, name


def _plugins_roots() -> list[Path]:
    """Trusted ``.../plugins`` roots under which a ``<tier>/<category>/<name>``
    builtin directory may live: the marketplace checkout first (operator rule),
    then the in-wheel ``core/plugins/buildin`` parent as a fallback."""
    roots: list[Path] = []
    # bootstrap._marketplace_root() -> ``.../plugins/buildin``; its parent is the
    # ``.../plugins`` root the index-id's tier segment expands under.
    try:
        roots.append(_bootstrap._marketplace_root().parent)
    except Exception:  # noqa: BLE001
        pass
    try:
        roots.append(_bootstrap._BUILTIN_ROOT.parent)  # core/plugins
    except Exception:  # noqa: BLE001
        pass
    return roots


def resolve_builtin_dir(index_id: str) -> Path:
    """Return the local directory for a ``buildin``-tier index-id.

    Raises :class:`MarketplaceResolveError` when the id is not a builtin, cannot
    be found under any trusted root, or would resolve outside its root.
    """
    if not _AVAILABLE:
        raise MarketplaceResolveError("plugin subsystem unavailable in this installation")
    parsed = parse_index_id(index_id)
    if parsed is None:
        raise MarketplaceResolveError(f"{index_id!r} is not a marketplace plugin id")
    tier, category, name = parsed
    if tier != "buildin":
        raise MarketplaceResolveError(
            f"{index_id!r} is tier {tier!r}; only builtin plugins install locally "
            f"(remote download of community plugins is out of scope)"
        )
    for root in _plugins_roots():
        resolved_root = root.resolve(strict=False)
        candidate = (root / tier / category / name)
        resolved = candidate.resolve(strict=False)
        # Containment guard: even a crafted category/name may not escape the root.
        if resolved_root not in resolved.parents:
            continue
        if (resolved / "plugin.yaml").is_file():
            return resolved
    raise MarketplaceResolveError(
        f"no local builtin source for {index_id!r} "
        f"(expected buildin/{category}/{name}/plugin.yaml under the marketplace checkout)"
    )


def load_manifest(index_id: str) -> Tuple[Path, dict]:
    """Resolve the directory and read its ``plugin.yaml`` into a dict."""
    plugin_dir = resolve_builtin_dir(index_id)
    manifest = _bootstrap.load_from_manifest_safe(plugin_dir / "plugin.yaml")
    if not isinstance(manifest, dict) or not manifest.get("plugin_id"):
        raise MarketplaceResolveError(
            f"{index_id!r}: plugin.yaml has no plugin_id"
        )
    return plugin_dir, manifest


def manifest_plugin_id(index_id: str) -> str:
    """The registry key for a builtin index-id (the manifest's own plugin_id).

    Bridges the index/manifest id divergence for the mutation routes: an index-id
    is resolved to the hyphenated manifest id the registry stores under; a value
    that is already a plain plugin_id (no ``plugin:`` prefix) is returned as-is.
    """
    if not (isinstance(index_id, str) and index_id.startswith("plugin:")):
        return index_id
    _dir, manifest = load_manifest(index_id)
    return str(manifest["plugin_id"])


def validate_builtin_manifest(plugin_dir: Path):
    """Run the ADR-0247 manifest gate on a resolved builtin dir (not bypassed)."""
    return validate_manifest_file(plugin_dir / "plugin.yaml")


def record_from_manifest(manifest: dict) -> "PluginRecord":
    """Project a builtin plugin.yaml manifest onto a ``PluginRecord``.

    ``origin`` is forced to ``builtin`` and ``boot_layer`` to ``installed``: this
    function is only ever reached with a manifest resolved from under a trusted
    ``buildin/`` root, so builtin is a LOCATION fact, and an operator-installed
    builtin lands on the ``installed`` boot layer regardless of what the manifest
    declares (a tenant registry may only express bundled/installed anyway).
    """
    plugin_id = str(manifest["plugin_id"])
    return PluginRecord(
        plugin_id=plugin_id,
        version=str(manifest.get("version", "0.0.0")),
        display_name=str(manifest.get("display_name") or plugin_id),
        plugin_type=str(manifest.get("plugin_type") or "generic"),
        origin=PluginOrigin.BUILTIN,
        boot_layer=BootLayer.INSTALLED,
        pii_risk=PIIRisk(str(manifest.get("pii_risk", "low"))),
        requires_consent=bool(manifest.get("requires_consent", False)),
        audit_required=bool(manifest.get("audit_required", True)),
        locality=Locality(str(manifest.get("locality", "unknown"))),
        network_egress=NetworkEgress(str(manifest.get("network_egress", "external"))),
        egress_hosts=list(manifest.get("egress_hosts") or []),
        dependencies=list(manifest.get("dependencies") or []),
        settings_schema=dict(manifest.get("settings_schema") or {}),
        class_path=manifest.get("class_path"),
    )
