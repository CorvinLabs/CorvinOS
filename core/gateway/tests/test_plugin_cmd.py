"""Unit tests for plugin CLI (ADR-0249 Stage 6)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from corvin_gateway import plugin_cmd


class TestExtractMetadata:
    """Test metadata extraction from plugin directories."""

    def test_extract_from_plugin_yaml(self, tmp_path):
        """Extract metadata from plugin.yaml (new format)."""
        plugin_dir = tmp_path / "my_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text(
            """
id: com.example.my_plugin
name: My Plugin
version: 1.0.0
origin: community
boot_layer: installed
class_path: my_plugin.backend:MyBackend
config:
  setting1: value1
"""
        )

        meta = plugin_cmd.extract_plugin_metadata(str(plugin_dir))
        assert meta.plugin_id == "com.example.my_plugin"
        assert meta.name == "My Plugin"
        assert meta.version == "1.0.0"
        assert meta.origin == "community"
        assert meta.boot_layer == "installed"
        assert meta.class_path == "my_plugin.backend:MyBackend"
        assert meta.config == {"setting1": "value1"}

    def test_extract_from_setup_py(self, tmp_path):
        """Extract metadata from setup.py (legacy format)."""
        plugin_dir = tmp_path / "legacy_plugin"
        plugin_dir.mkdir()

        setup_py = plugin_dir / "setup.py"
        setup_py.write_text(
            """
from setuptools import setup
setup(
    name="legacy-plugin",
    version="2.0.0",
    packages=["legacy_plugin"],
)
"""
        )

        meta = plugin_cmd.extract_plugin_metadata(str(plugin_dir))
        assert meta.plugin_id == "legacy_plugin"  # name normalized
        assert meta.version == "2.0.0"
        assert meta.origin == "community"  # Defaulted
        assert meta.boot_layer == "installed"  # Defaulted

    def test_no_metadata_fails(self, tmp_path):
        """Fail if no metadata file found."""
        plugin_dir = tmp_path / "empty_plugin"
        plugin_dir.mkdir()

        with pytest.raises(ValueError, match="Could not extract plugin metadata"):
            plugin_cmd.extract_plugin_metadata(str(plugin_dir))

    def test_url_path_fails(self):
        """Reject URL paths in CLI."""
        with pytest.raises(ValueError):
            plugin_cmd.extract_plugin_metadata("https://github.com/user/plugin")

    def test_nonexistent_path_fails(self):
        """Fail if path does not exist."""
        with pytest.raises(ValueError, match="Plugin path must be a directory"):
            plugin_cmd.extract_plugin_metadata("/nonexistent/path")


class TestPluginAlreadyInstalled:
    """Test idempotency detection."""

    def test_installed_is_detected(self):
        """Detect when plugin is already in config."""
        config = {
            "spec": {
                "plugins": {
                    "installed": [
                        {
                            "id": "com.example.plugin1",
                            "name": "Plugin 1",
                            "version": "1.0.0",
                        }
                    ]
                }
            }
        }

        assert plugin_cmd.plugin_already_installed(config, "com.example.plugin1")
        assert not plugin_cmd.plugin_already_installed(config, "com.example.plugin2")

    def test_empty_config(self):
        """Handle empty plugin config."""
        config = {}
        assert not plugin_cmd.plugin_already_installed(config, "com.example.plugin")


class TestAddPluginToConfig:
    """Test adding plugins to tenant config."""

    def test_add_to_empty_config(self):
        """Add plugin to empty tenant config."""
        config = {}
        metadata = plugin_cmd.PluginMetadata(
            plugin_id="test.plugin",
            name="Test Plugin",
            version="1.0.0",
            origin="community",
            boot_layer="installed",
        )

        plugin_cmd.add_plugin_to_config(config, metadata)

        assert "spec" in config
        assert config["spec"]["plugins"]["installed"][0]["id"] == "test.plugin"

    def test_add_with_class_path_and_config(self):
        """Add plugin with class_path and config."""
        config = {}
        metadata = plugin_cmd.PluginMetadata(
            plugin_id="test.backend",
            name="Test Backend",
            version="2.0.0",
            origin="vetted",
            boot_layer="bundled",
            class_path="test_backend:Handler",
            config={"key": "value"},
        )

        plugin_cmd.add_plugin_to_config(config, metadata)

        entry = config["spec"]["plugins"]["installed"][0]
        assert entry["class_path"] == "test_backend:Handler"
        assert entry["config"] == {"key": "value"}
        assert entry["boot_layer"] == "bundled"


class TestVerifySignature:
    """Test signature verification."""

    def test_unsigned_plugin_passes(self):
        """Unsigned plugins pass as community."""
        manifest = {"id": "test", "version": "1.0.0"}
        verified, reason = plugin_cmd.verify_plugin_signature(
            Path("/tmp"), manifest, trust_anchors=("anchor1",)
        )
        # Unsigned is OK (treated as community)
        assert verified
        assert reason == "unsigned"

    def test_no_anchors_fails_vetted(self):
        """Vetted plugins fail without trust anchors."""
        manifest = {"id": "test", "signature": {"algorithm": "ed25519"}}
        verified, reason = plugin_cmd.verify_plugin_signature(
            Path("/tmp"), manifest, trust_anchors=()
        )
        assert not verified
        assert reason == "no trust anchors configured"


class TestInstallCommand:
    """Integration tests for install_plugin function."""

    def test_rejects_url_paths(self):
        """Reject URL-based plugin paths."""
        result = plugin_cmd.install_plugin("https://github.com/user/plugin")
        assert result == 1

    def test_rejects_nonexistent_paths(self):
        """Reject paths that don't exist."""
        result = plugin_cmd.install_plugin("/nonexistent/path/to/plugin")
        assert result == 1

    def test_idempotent_install_returns_success(self, tmp_path):
        """Idempotent install (already present) returns 0."""
        plugin_dir = tmp_path / "test_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text("""
id: test.idempotent
name: Test Plugin
version: 1.0.0
""")

        # Mock tenant config that already has the plugin
        config = {
            "spec": {
                "plugins": {
                    "installed": [
                        {
                            "id": "test.idempotent",
                            "name": "Test Plugin",
                            "version": "1.0.0",
                        }
                    ]
                }
            }
        }

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            result = plugin_cmd.install_plugin(str(plugin_dir), no_prompt=True)

        # Should return 0 (success) for idempotent case
        assert result == 0

    def test_community_plugin_requires_confirmation(self, tmp_path):
        """Community plugins require operator confirmation."""
        plugin_dir = tmp_path / "community_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text("""
id: test.community
name: Community Plugin
version: 1.0.0
origin: community
""")

        config = {"spec": {"plugins": {"installed": []}}}

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(plugin_cmd, "save_tenant_config"):
                with mock.patch.object(
                    plugin_cmd,
                    "prompt_community_confirmation",
                    return_value=False,
                ):
                    result = plugin_cmd.install_plugin(str(plugin_dir))

        # Should reject when confirmation is denied
        assert result == 1

    def test_force_flag_skips_confirmation(self, tmp_path):
        """Force flag skips community plugin confirmation."""
        plugin_dir = tmp_path / "force_test_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text("""
id: test.force
name: Force Plugin
version: 1.0.0
origin: community
""")

        config = {"spec": {"plugins": {"installed": []}}}

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(plugin_cmd, "save_tenant_config"):
                # With force=True, should not call prompt_community_confirmation
                with mock.patch.object(
                    plugin_cmd, "prompt_community_confirmation"
                ) as mock_prompt:
                    result = plugin_cmd.install_plugin(
                        str(plugin_dir), force=True, no_prompt=True
                    )

                # prompt should not be called when force=True
                mock_prompt.assert_not_called()

        assert result == 0


class TestPromptCommunityConfirmation:
    """Test community plugin confirmation prompt."""

    def test_prompt_accepts_yes(self, monkeypatch):
        """Test that 'yes' input is accepted."""
        monkeypatch.setattr("builtins.input", lambda _: "yes")

        metadata = plugin_cmd.PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            origin="community",
            boot_layer="installed",
        )

        result = plugin_cmd.prompt_community_confirmation(metadata)
        assert result is True

    def test_prompt_accepts_no(self, monkeypatch):
        """Test that 'no' input is accepted."""
        monkeypatch.setattr("builtins.input", lambda _: "no")

        metadata = plugin_cmd.PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            origin="community",
            boot_layer="installed",
        )

        result = plugin_cmd.prompt_community_confirmation(metadata)
        assert result is False

    def test_prompt_rejects_invalid_input(self, monkeypatch):
        """Test that invalid input is rejected and reprompted."""
        inputs = iter(["maybe", "definitely", "yes"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        metadata = plugin_cmd.PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            origin="community",
            boot_layer="installed",
        )

        result = plugin_cmd.prompt_community_confirmation(metadata)
        assert result is True
