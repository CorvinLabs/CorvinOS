"""Every plugin the Corvin-Marketplace checkout ships must be LOADABLE by CorvinOS.

Finding F-P2 (2026-09-07): 29 of 30 marketplace ``plugin.yaml`` manifests had no
``plugin_type``, so the ADR-0247 gate refused them (``manifest_invalid``) and the
live console booted exactly one plugin — while the marketplace's own index said
30. Discovery and loadability had silently diverged.

This test pins them together: for every directory ``bootstrap._builtin_plugin_dirs``
discovers under the marketplace root, the REAL gate (``validate_manifest_file``),
the REAL class loader (``_load_builtin_class``) and the REAL class check
(``validate_class``) must all pass, and the resulting count must equal the
discovered count. It skips — never fabricates a pass — when no checkout is
present (``CORVIN_MARKETPLACE_ROOT`` / sibling ``../Corvin-Marketplace``).
"""
from __future__ import annotations

import pytest

from corvin_plugins import bootstrap
from corvin_plugins.protocol import KNOWN_PLUGIN_TYPES
from corvin_plugins.validation import validate_class, validate_manifest_file


def _discovered():
    root = bootstrap._marketplace_root()
    dirs = bootstrap._builtin_plugin_dirs(root)
    if not dirs:
        pytest.skip(f"no Corvin-Marketplace checkout at {root}")
    return root, dirs


def test_every_discovered_marketplace_plugin_is_loadable():
    root, dirs = _discovered()
    loadable, problems = [], []
    for d in dirs:
        manifest_path = d / "plugin.yaml"
        report = validate_manifest_file(manifest_path)
        manifest = bootstrap.load_from_manifest_safe(manifest_path)
        plugin_id = str(manifest.get("plugin_id") or "")
        plugin_type = str(manifest.get("plugin_type") or "")
        rel = d.relative_to(root).as_posix()
        if not report.ok:
            problems.append(f"{rel}: manifest gate: {[f.message for f in report.errors]}")
            continue
        if plugin_type not in KNOWN_PLUGIN_TYPES:
            problems.append(f"{rel}: plugin_type {plugin_type!r} not in KNOWN_PLUGIN_TYPES")
            continue
        cls = bootstrap._load_builtin_class(d, plugin_id)
        if cls is None:
            problems.append(f"{rel}: no CorvinPlugin-shaped provider class")
            continue
        class_report = validate_class(cls, expected_type=plugin_type, expected_id=plugin_id)
        if not class_report.ok:
            problems.append(f"{rel}: class: {[f.message for f in class_report.errors]}")
            continue
        loadable.append(plugin_id)

    assert not problems, "\n".join(problems)
    assert len(loadable) == len(dirs), (len(loadable), len(dirs))
    assert len(set(loadable)) == len(loadable), "duplicate plugin_id across marketplace dirs"
    # The checkout this repo develops against ships 30 loadable builtins; fewer
    # means the checkout is stale or something regressed, and should be looked at.
    assert len(dirs) >= 30, f"only {len(dirs)} marketplace plugin dirs discovered"


def test_marketplace_root_plugins_are_vetted_never_builtin():
    """F-P5: provenance is root-derived. Nothing under the marketplace checkout
    may be reported as ``builtin`` — that word is reserved for the in-wheel root."""
    _root, dirs = _discovered()
    for d in dirs:
        origin, source = bootstrap.origin_for_plugin_dir(d)
        assert origin == "vetted", (d, origin)
        assert source.startswith("marketplace_root:"), source
        assert not source.startswith("/"), "source must be root-relative, never absolute"


# ─────────────────────────────────────────────────────────────────────────────
# F5 (round-4 review): the two tests above compare the ``plugin.yaml`` glob
# against ITSELF, so they were green while the marketplace INDEX — the surface
# the operator actually browses and installs from — disagreed with what loads,
# in BOTH directions: 34 indexed ids vs 30 loadable dirs, 12 indexed ids with no
# ``plugin.yaml`` at all (``resolve_builtin_dir`` refuses those, so the console
# advertised ``path_gate``/``flow_guard``/``consent_gate``/``audit_chain`` as
# installable builtins that can never load), and 8 plugins registering at every
# boot that appeared nowhere in the index. These compare INDEX ⟷ LOADABLE.
# ─────────────────────────────────────────────────────────────────────────────


def _index_buildin_ids():
    import json

    # ``_marketplace_root()`` points at ``<checkout>/plugins/buildin``; the
    # index lives at the checkout root.
    checkout = bootstrap._marketplace_root().parent.parent
    index_path = checkout / "index" / "plugins.json"
    if not index_path.exists():
        pytest.skip(f"no marketplace index at {index_path}")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    return {p["id"] for p in data.get("plugins", []) if p.get("tier") == "buildin"}


def _loadable_buildin_ids():
    """``plugin:buildin-<category>-<name>`` for every dir CorvinOS discovers."""
    root, dirs = _discovered()  # root == <checkout>/plugins/buildin
    ids = set()
    for d in dirs:
        rel = d.relative_to(root).parts  # <category>/<name>
        if len(rel) >= 2:
            ids.add(f"plugin:buildin-{rel[0]}-{rel[1]}")
    return ids


def test_every_indexed_buildin_is_actually_installable():
    """No entry may advertise a builtin CorvinOS cannot resolve or load."""
    from corvin_console.routes.marketplace_resolve import resolve_builtin_dir

    indexed = _index_buildin_ids()
    orphans = sorted(indexed - _loadable_buildin_ids())
    assert not orphans, (
        "marketplace index advertises buildin plugin(s) with no plugin.yaml — "
        "resolve_builtin_dir refuses them, so 'Install' can never succeed:\n  "
        + "\n  ".join(orphans)
    )
    # …and prove it through the REAL resolver, not just the glob.
    unresolvable = [i for i in sorted(indexed) if resolve_builtin_dir(i) is None]
    assert not unresolvable, (
        "resolve_builtin_dir() refuses these indexed ids:\n  " + "\n  ".join(unresolvable)
    )


def test_every_loadable_buildin_is_visible_in_the_index():
    """The reverse: nothing may register at boot while invisible to the operator."""
    missing = sorted(_loadable_buildin_ids() - _index_buildin_ids())
    assert not missing, (
        "plugin(s) load into the process at every boot but appear nowhere in the "
        "marketplace surface the operator inspects — run the marketplace's "
        "generate_index_v2.py:\n  " + "\n  ".join(missing)
    )
