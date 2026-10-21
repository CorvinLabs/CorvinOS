"""Three things every boot of every install logged until 2026-10-21 (measured on
a fresh Ubuntu 24.04 system and on the maintainer's host alike):

* ``'str' object has no attribute 'items'`` — the Phase 1d list-schema reader
  tried to parse state.TenantRegistry's mapping-schema ``registry.yaml``;
* ``plugin 'corvin_knowledge' has no class_path — skipping`` as an ERROR plus a
  ``plugin.load_failed`` audit record for every record-only default plugin;
* ``Manifest not found … returning empty manifest`` + ``No plugins in canonical
  manifest`` as WARNINGs every 30 s from the drift detector (1 500+ a day).
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "corvin"
    (root / "tenants" / "_default" / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(root))
    return root


def test_the_list_schema_reader_recognises_the_state_schema(home, caplog):
    from corvin_plugins.tenant_plugins import TenantPluginRegistry

    (home / "tenants" / "_default" / "plugins" / "registry.yaml").write_text(
        "spec:\n  schema_version: '1.0'\n"
        "plugins:\n  corvin_knowledge:\n    plugin_id: corvin_knowledge\n    version: 1.2.0\n",
        encoding="utf-8",
    )
    reg = TenantPluginRegistry(tenant_id="_default")
    with caplog.at_level(logging.DEBUG, logger="corvin.plugins.tenant_plugins"):
        reg.load_registry()
    assert reg.plugins == []
    assert "has no attribute" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_record_only_plugin_is_not_a_load_failure(home, caplog):
    from corvin_plugins import bootstrap

    fake = SimpleNamespace(
        load_order=lambda: ["corvin_knowledge"],
        records={"corvin_knowledge": SimpleNamespace(plugin_id="corvin_knowledge", class_path=None)},
    )
    with patch("corvin_plugins.state.TenantRegistry.load", return_value=fake), \
            patch.object(bootstrap, "_load_one") as load_one, \
            patch.object(bootstrap, "_audit_degradation") as audit, \
            caplog.at_level(logging.DEBUG, logger="corvin.plugins.bootstrap"):
        loaded = bootstrap._bootstrap_state_registry(tenant_id="_default", corvin_home=home)
    assert loaded == []
    load_one.assert_not_called()
    audit.assert_not_called()
    assert "record-only" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_an_absent_manifest_is_reported_once_per_path(tmp_path, caplog):
    from core.plugins import canonical_manifest as cm
    from core.plugins.registry_sync import PluginRegistrySynchronizer

    missing = str(tmp_path / "canonical_manifest.json")
    cm._ABSENCE_REPORTED.discard(missing)
    with caplog.at_level(logging.DEBUG):
        cm.CanonicalManifestManager(manifest_url=missing).load_manifest()
        cm.CanonicalManifestManager(manifest_url=missing).load_manifest()  # the detector's next poll
        sync = PluginRegistrySynchronizer(
            instance_id="x", manifest_manager=cm.CanonicalManifestManager(manifest_url=missing)
        )
        assert sync.detect_plugin_drift() == []
    found = [r for r in caplog.records if "Manifest not found" in r.getMessage()]
    assert [r.levelno for r in found][:1] == [logging.INFO]
    assert all(r.levelno == logging.DEBUG for r in found[1:])
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert Path(missing).exists() is False


# ── Round 2 (2026-10-21): the quiet reader must not become a silent writer ──

def test_the_list_schema_reader_never_overwrites_the_state_registry(home, tmp_path):
    """``corvin plugin install`` (ops/launcher plugin_runtime_cmd) loads the
    Phase 1d registry, appends an entry and saves. With the state schema now
    recognised at DEBUG, that save would replace state.TenantRegistry's
    records (the default plugins) with a one-entry list. It must refuse, and
    the file must stay byte-identical."""
    from corvin_plugins.tenant_plugins import TenantPluginRegistry

    reg_file = home / "tenants" / "_default" / "plugins" / "registry.yaml"
    original = (
        "spec:\n  schema_version: '1.0'\n"
        "plugins:\n  corvin_knowledge:\n    plugin_id: corvin_knowledge\n    version: 1.2.0\n"
    )
    reg_file.write_text(original, encoding="utf-8")
    src = tmp_path / "plug"
    src.mkdir()
    (src / "plugin.json").write_text("{}", encoding="utf-8")

    reg = TenantPluginRegistry(tenant_id="_default")
    reg.load_registry()
    assert reg.foreign_schema is True
    with pytest.raises(RuntimeError, match="state registry"):
        reg.register_plugin("evil", src, {"version": "0.1.0"})
    with pytest.raises(RuntimeError):
        reg.save_registry()
    assert reg_file.read_text(encoding="utf-8") == original
    assert not (home / "tenants" / "_default" / "plugins" / "installed" / "evil").exists()

    # A Phase 1d file is still writable.
    reg_file.write_text("schema_version: '1.0'\nplugins: []\n", encoding="utf-8")
    reg.load_registry()
    assert reg.foreign_schema is False
    reg.save_registry()


def test_a_manifest_that_appears_and_vanishes_is_reported_again(tmp_path, caplog):
    from core.plugins import canonical_manifest as cm

    path = tmp_path / "canonical_manifest.json"
    cm._ABSENCE_REPORTED.discard(str(path))
    with caplog.at_level(logging.DEBUG):
        cm.CanonicalManifestManager(manifest_url=str(path)).load_manifest()      # INFO
        path.write_text('{"plugins": [], "timestamp": "t"}', encoding="utf-8")
        cm.CanonicalManifestManager(manifest_url=str(path)).load_manifest()      # present
        path.unlink()
        cm.CanonicalManifestManager(manifest_url=str(path)).load_manifest()      # INFO again
    found = [r.levelno for r in caplog.records if "Manifest not found" in r.getMessage()]
    assert found == [logging.INFO, logging.INFO], found
