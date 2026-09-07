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
