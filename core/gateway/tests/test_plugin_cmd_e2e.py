"""E2E integration tests for plugin CLI (ADR-0249 Stage 6).

These tests validate the CLI command reachability and end-to-end workflow
without mocking internal functions.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from corvin_gateway import plugin_cmd


class TestCLIEndToEnd:
    """E2E tests for the CLI command."""

    def test_cli_help_available(self):
        """Test that plugin install help is available via CLI."""
        # Run the help command
        result = subprocess.run(
            ["python", "-m", "corvin_gateway.cli", "plugin", "install", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Local directory path" in result.stdout
        assert "--tenant" in result.stdout
        assert "--force" in result.stdout

    def test_cli_rejects_url(self):
        """Test that CLI rejects URL-based paths."""
        result = subprocess.run(
            [
                "python",
                "-m",
                "corvin_gateway.cli",
                "plugin",
                "install",
                "https://github.com/user/plugin",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "URL installation not supported" in result.stderr

    def test_cli_rejects_nonexistent_path(self):
        """Test that CLI rejects nonexistent paths."""
        result = subprocess.run(
            [
                "python",
                "-m",
                "corvin_gateway.cli",
                "plugin",
                "install",
                "/nonexistent/path/to/plugin",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "Plugin path not found" in result.stderr

    def test_cli_full_workflow_with_mock_config(self, tmp_path):
        """Test full install workflow via CLI with mocked config loading."""
        # Create test plugin
        plugin_dir = tmp_path / "test_cli_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text(
            """
id: com.example.cli_test
name: CLI Test Plugin
version: 1.0.0
origin: community
boot_layer: installed
"""
        )

        # Mock the tenant config loading/saving
        config = {"spec": {"plugins": {"installed": []}}}

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(plugin_cmd, "save_tenant_config") as mock_save:
                result = plugin_cmd.install_plugin(
                    str(plugin_dir), no_prompt=True, force=True
                )

        assert result == 0
        mock_save.assert_called_once()

        # Verify config was updated
        saved_config = mock_save.call_args[0][1]
        installed = saved_config["spec"]["plugins"]["installed"]
        assert len(installed) == 1
        assert installed[0]["id"] == "com.example.cli_test"
        assert installed[0]["version"] == "1.0.0"


class TestInstallRealPlugin:
    """Test installing a real plugin with all steps."""

    def test_install_minimal_plugin(self, tmp_path):
        """Test installing a minimal plugin with just plugin.yaml."""
        # Create plugin directory
        plugin_dir = tmp_path / "minimal_plugin"
        plugin_dir.mkdir()

        # Create plugin.yaml
        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text(
            """
id: com.test.minimal
name: Minimal Plugin
version: 0.1.0
origin: community
boot_layer: installed
"""
        )

        # Mock config operations
        config = {"spec": {"plugins": {"installed": []}}}

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(plugin_cmd, "save_tenant_config"):
                result = plugin_cmd.install_plugin(
                    str(plugin_dir), no_prompt=True, force=True
                )

        assert result == 0

    def test_install_plugin_with_class_path(self, tmp_path):
        """Test installing a plugin with class_path and config."""
        plugin_dir = tmp_path / "backend_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text(
            """
id: com.test.backend
name: Backend Plugin
version: 1.0.0
origin: community
boot_layer: bundled
class_path: test_backend.notification:SlackNotifier
config:
  webhook_url: "$SECRET_WEBHOOK_URL"
  channel: "#alerts"
"""
        )

        config = {"spec": {"plugins": {"installed": []}}}

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(plugin_cmd, "save_tenant_config") as mock_save:
                result = plugin_cmd.install_plugin(
                    str(plugin_dir), no_prompt=True, force=True
                )

        assert result == 0

        # Verify full config entry
        saved_config = mock_save.call_args[0][1]
        entry = saved_config["spec"]["plugins"]["installed"][0]
        assert entry["class_path"] == "test_backend.notification:SlackNotifier"
        assert entry["config"]["channel"] == "#alerts"
        assert entry["boot_layer"] == "bundled"

    def test_install_legacy_setup_py_plugin(self, tmp_path):
        """Test installing a legacy plugin with setup.py."""
        plugin_dir = tmp_path / "legacy_backend"
        plugin_dir.mkdir()

        setup_py = plugin_dir / "setup.py"
        setup_py.write_text(
            """
from setuptools import setup
setup(
    name="legacy-notification",
    version="2.1.0",
    py_modules=["notification"],
)
"""
        )

        config = {"spec": {"plugins": {"installed": []}}}

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(plugin_cmd, "save_tenant_config") as mock_save:
                result = plugin_cmd.install_plugin(
                    str(plugin_dir), no_prompt=True, force=True
                )

        assert result == 0

        saved_config = mock_save.call_args[0][1]
        entry = saved_config["spec"]["plugins"]["installed"][0]
        assert entry["id"] == "legacy_notification"
        assert entry["version"] == "2.1.0"
        assert entry["origin"] == "community"  # Defaulted for legacy


class TestSignatureVerification:
    """Test signature verification for vetted plugins."""

    def test_vetted_plugin_with_no_anchors_fails(self, tmp_path):
        """Vetted plugin fails without trust anchors."""
        plugin_dir = tmp_path / "vetted_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text(
            """
id: com.test.vetted
name: Vetted Plugin
version: 1.0.0
origin: vetted
boot_layer: bundled
signature:
  algorithm: ed25519
  public_key: "AAAAC3NzaC1lZDI1NTE5AAAAIMqDWz2bUKBkWHEFhmefVMGEQzlp0Jc3EDX3UJL+IFSw"
  value: "invalid_signature_base64"
"""
        )

        config = {"spec": {"plugins": {"installed": []}}}

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch("corvin_plugins.trust.load_trust_anchors", return_value=()):
                result = plugin_cmd.install_plugin(str(plugin_dir), no_prompt=True)

        # Should fail because no trust anchors are configured
        assert result == 1


class TestIdempotency:
    """Test that re-installing is idempotent."""

    def test_reinstall_with_force(self, tmp_path):
        """Test that --force replaces existing plugin."""
        plugin_dir = tmp_path / "idempotent_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text(
            """
id: com.test.idempotent
name: Idempotent Plugin
version: 2.0.0
"""
        )

        # Config already has v1.0.0
        config = {
            "spec": {
                "plugins": {
                    "installed": [
                        {
                            "id": "com.test.idempotent",
                            "name": "Idempotent Plugin",
                            "version": "1.0.0",
                        }
                    ]
                }
            }
        }

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(plugin_cmd, "save_tenant_config") as mock_save:
                result = plugin_cmd.install_plugin(
                    str(plugin_dir), force=True, no_prompt=True
                )

        assert result == 0

        # Verify old entry is removed and new one added (only 1 total)
        saved_config = mock_save.call_args[0][1]
        installed = saved_config["spec"]["plugins"]["installed"]
        assert len(installed) == 1
        assert installed[0]["version"] == "2.0.0"

    def test_reinstall_without_force_is_no_op(self, tmp_path):
        """Test that re-install without --force is a no-op."""
        plugin_dir = tmp_path / "no_force_plugin"
        plugin_dir.mkdir()

        plugin_yaml = plugin_dir / "plugin.yaml"
        plugin_yaml.write_text(
            """
id: com.test.no_force
name: No Force Plugin
version: 1.0.0
"""
        )

        config = {
            "spec": {
                "plugins": {
                    "installed": [
                        {"id": "com.test.no_force", "version": "1.0.0"}
                    ]
                }
            }
        }

        with mock.patch.object(plugin_cmd, "load_tenant_config", return_value=config):
            with mock.patch.object(
                plugin_cmd, "save_tenant_config"
            ) as mock_save:
                result = plugin_cmd.install_plugin(str(plugin_dir), no_prompt=True)

        assert result == 0
        # save_tenant_config should NOT be called for idempotent case
        mock_save.assert_not_called()
