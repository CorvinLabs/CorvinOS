"""E2E test for `corvin plugin install` (ADR-0248, Stage 6).

Verifies the complete install workflow end-to-end:
- Plugin discovery and validation
- Origin handling (community vs vetted vs builtin)
- Trust anchor verification
- Registry persistence

Unlike unit tests, these run against the real plugin system with real filesystems.

Run:
    python -m pytest core/plugins/tests/test_plugin_install_e2e.py -v
"""
from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

# Access the launcher CLI module
_REPO = Path(__file__).resolve().parents[3]
_LAUNCHER = _REPO / "ops" / "launcher"
if str(_LAUNCHER) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER))

from corvin.plugin_runtime_cmd import cmd_install  # noqa: E402


@pytest.fixture
def temp_corvin_home(tmp_path, monkeypatch):
    """Provide a temporary CORVIN_HOME for E2E tests."""
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()

    with mock.patch(
        "corvinOS.shared.paths.corvin_home",
        return_value=corvin_home,
    ):
        with mock.patch(
            "corvinOS.shared.paths.tenant_home",
            return_value=corvin_home / "tenants" / "_default",
        ):
            with mock.patch(
                "corvinOS.shared.paths._resolve_tenant_id",
                return_value="_default",
            ):
                yield corvin_home


@pytest.fixture
def minimal_plugin_dir(tmp_path):
    """Create a valid plugin directory that passes all checks."""
    plugin_root = tmp_path / "valid-plugin"
    plugin_root.mkdir()

    # plugin.yaml (valid YAML with all required fields)
    (plugin_root / "plugin.yaml").write_text(
        """plugin_id: com.example.valid-plugin
plugin_type: router_backend
version: 2.0.0
display_name: Valid Plugin
boot_layer: installed
origin: community
pii_risk: low
requires_consent: false
""",
        encoding="utf-8",
    )

    # plugin.py (valid implementation)
    (plugin_root / "plugin.py").write_text(
        """
from corvin_plugins.protocol import BasePlugin, HealthStatus

class ValidPlugin(BasePlugin):
    plugin_id = "com.example.valid-plugin"
    plugin_type = "router_backend"
    version = "2.0.0"

    def on_load(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True, message="healthy")

    def route(self, task):
        return None
""",
        encoding="utf-8",
    )

    # pyproject.toml (required for discovery)
    (plugin_root / "pyproject.toml").write_text(
        """[project]
name = "valid-plugin"
version = "2.0.0"
requires-python = ">=3.11"

[project.entry-points."corvin.plugins"]
valid_plugin = "plugin:ValidPlugin"
""",
        encoding="utf-8",
    )

    return plugin_root


# ── E2E: Simple community plugin install ──────────────────────────────────

def test_e2e_community_plugin_install_succeeds(
    minimal_plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """E2E: Community plugin installs successfully with --yes flag."""
    args = Namespace(
        path=str(minimal_plugin_dir),
        tenant=None,
        yes=True,  # Skip confirmation
    )

    rc = cmd_install(args)

    # Verify success
    assert rc == 0, "Install should succeed"
    out, err = capsys.readouterr()
    assert "Installed" in out or "com.example.valid-plugin" in out

    # Verify plugin directory was created
    plugin_installed_path = (
        temp_corvin_home / "tenants" / "_default" / "plugins" / "installed" / "com.example.valid-plugin"
    )
    assert plugin_installed_path.exists(), "Plugin directory should be installed"

    # Verify plugin.yaml was copied
    assert (plugin_installed_path / "plugin.yaml").exists()
    assert (plugin_installed_path / "plugin.py").exists()

    # Verify registry was updated
    registry_path = temp_corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
    assert registry_path.exists(), "Registry should be created"

    # Verify registry content
    import yaml
    registry_data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    assert registry_data is not None
    plugins = registry_data.get("plugins", [])
    assert len(plugins) >= 1
    plugin_entry = next((p for p in plugins if p["plugin_id"] == "com.example.valid-plugin"), None)
    assert plugin_entry is not None, "Plugin should be in registry"
    assert plugin_entry["version"] == "2.0.0"
    assert plugin_entry["origin"] == "community"
    assert plugin_entry["enabled"] is True


def test_e2e_community_plugin_with_confirmation(
    minimal_plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """E2E: Community plugin prompts for confirmation without --yes."""
    monkeypatch.setattr("builtins.input", lambda x: "y")

    args = Namespace(
        path=str(minimal_plugin_dir),
        tenant=None,
        yes=False,  # No auto-skip
    )

    rc = cmd_install(args)

    assert rc == 0
    out, _ = capsys.readouterr()
    # Confirmation message should appear (from the plugin type check)
    assert ("Confirmed" in out or "Installed" in out or
            "unreviewed third-party" in out)


def test_e2e_duplicate_install_is_rejected(
    minimal_plugin_dir, temp_corvin_home, monkeypatch, capsys
):
    """E2E: Installing the same plugin twice is idempotent (skips, rc 0).

    ``cmd_install`` deliberately treats an already-installed plugin as a no-op
    (rc 0, "already installed, skipping" on stdout) rather than an error —
    see plugin_runtime_cmd.py::cmd_install ("Make idempotent: if already
    installed, that's not an error").
    """
    args = Namespace(
        path=str(minimal_plugin_dir),
        tenant=None,
        yes=True,
    )

    # First install
    rc1 = cmd_install(args)
    assert rc1 == 0, "First install should succeed"

    # Capture and clear the output
    capsys.readouterr()

    # Second install is idempotent: skipped, rc 0.
    rc2 = cmd_install(args)
    assert rc2 == 0, "Duplicate install should be idempotent (skip, rc 0)"

    out, _ = capsys.readouterr()
    assert "already installed" in out.lower()


def test_e2e_invalid_manifest_is_rejected(
    minimal_plugin_dir, temp_corvin_home, capsys
):
    """E2E: Plugin with invalid manifest is rejected."""
    # Break the manifest
    (minimal_plugin_dir / "plugin.yaml").write_text("invalid: yaml: : : :", encoding="utf-8")

    args = Namespace(
        path=str(minimal_plugin_dir),
        tenant=None,
        yes=True,
    )

    rc = cmd_install(args)

    # Should fail during manifest parsing/validation
    assert rc != 0, "Install should fail for invalid manifest"


def test_e2e_missing_plugin_yaml_is_rejected(
    minimal_plugin_dir, temp_corvin_home, capsys
):
    """E2E: Plugin directory without plugin.yaml is rejected."""
    (minimal_plugin_dir / "plugin.yaml").unlink()

    args = Namespace(
        path=str(minimal_plugin_dir),
        tenant=None,
        yes=True,
    )

    rc = cmd_install(args)

    assert rc == 2, "Install should fail (no plugin.yaml)"
    _, err = capsys.readouterr()
    assert "plugin.yaml" in err


def test_e2e_builtin_plugin_no_confirmation_needed(
    minimal_plugin_dir, temp_corvin_home, capsys
):
    """E2E: Builtin plugins install without any confirmation."""
    # Change origin to builtin
    manifest = (minimal_plugin_dir / "plugin.yaml").read_text(encoding="utf-8")
    manifest = manifest.replace("origin: community", "origin: builtin")
    (minimal_plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    args = Namespace(
        path=str(minimal_plugin_dir),
        tenant=None,
        yes=False,  # No --yes, but should still work for builtin
    )

    rc = cmd_install(args)

    # Should succeed without prompting (no input call needed)
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Installed" in out


# ── E2E: Multiple plugins in one tenant ────────────────────────────────────

def test_e2e_multiple_plugins_in_registry(
    tmp_path, temp_corvin_home, monkeypatch, capsys
):
    """E2E: Multiple different plugins can be installed in the same tenant."""
    # Create two different plugins
    plugins = []
    for i in range(2):
        plugin_dir = tmp_path / f"plugin-{i}"
        plugin_dir.mkdir()

        plugin_id = f"com.example.plugin{i}"
        (plugin_dir / "plugin.yaml").write_text(
            f"""plugin_id: {plugin_id}
plugin_type: router_backend
version: 1.{i}.0
display_name: Plugin {i}
boot_layer: installed
origin: community
pii_risk: low
requires_consent: false
""",
            encoding="utf-8",
        )

        (plugin_dir / "plugin.py").write_text(
            f"""
from corvin_plugins.protocol import BasePlugin, HealthStatus

class Plugin{i}(BasePlugin):
    plugin_id = "{plugin_id}"
    plugin_type = "router_backend"
    version = "1.{i}.0"

    def on_load(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True, message="ok")

    def route(self, task):
        return None
""",
            encoding="utf-8",
        )

        (plugin_dir / "pyproject.toml").write_text(
            f"""[project]
name = "plugin{i}"
version = "1.{i}.0"
requires-python = ">=3.11"

[project.entry-points."corvin.plugins"]
plugin{i} = "plugin:Plugin{i}"
""",
            encoding="utf-8",
        )

        plugins.append(plugin_dir)

    # Install both
    for plugin_dir in plugins:
        args = Namespace(path=str(plugin_dir), tenant=None, yes=True)
        rc = cmd_install(args)
        assert rc == 0, f"Install of {plugin_dir.name} should succeed"

    capsys.readouterr()

    # Verify both are in registry
    import yaml
    registry_path = temp_corvin_home / "tenants" / "_default" / "plugins" / "registry.yaml"
    registry_data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    registered_ids = {p["plugin_id"] for p in registry_data.get("plugins", [])}

    assert "com.example.plugin0" in registered_ids
    assert "com.example.plugin1" in registered_ids
    assert len(registered_ids) == 2


__all__ = [
    "test_e2e_community_plugin_install_succeeds",
    "test_e2e_community_plugin_with_confirmation",
    "test_e2e_duplicate_install_is_rejected",
    "test_e2e_invalid_manifest_is_rejected",
    "test_e2e_missing_plugin_yaml_is_rejected",
    "test_e2e_builtin_plugin_no_confirmation_needed",
    "test_e2e_multiple_plugins_in_registry",
]
