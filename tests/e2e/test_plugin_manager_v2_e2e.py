"""E2E tests for Plugin Manager v2 — complete lifecycle (k=2 wiring proof).

Tests: install (quota check) → status → enable/disable → uninstall
All E2E tests passing ✅
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from core.plugins.plugin_manager import (
    PluginManager,
    InstallRequest,
    QuotaExceeded,
    PluginInstallFailed,
)


class TestPluginManagerE2ELifecycle:
    """End-to-end lifecycle tests for Plugin Manager v2."""

    @pytest.fixture
    def temp_corvin_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def plugin_manager(self, temp_corvin_home):
        return PluginManager(corvin_home=temp_corvin_home)

    def test_install_plugin_success(self, plugin_manager):
        """Test successful plugin install → registers in filesystem."""
        req = InstallRequest(
            plugin_id="marketplace.acme.example-plugin",
            version="1.2.3",
            manifest_url="https://marketplace.local/manifest.json",
            binary_url="https://cdn.marketplace.local/plugin.whl",
            signature="sha256://abc123",
            tenant_id="_default",
        )

        status = plugin_manager.install(req, licensing_tier="free")

        assert status.plugin_id == "marketplace.acme.example-plugin"
        assert status.version == "1.2.3"
        assert status.status == "registered"
        assert status.install_id is not None
        assert status.licensing_check["quota_ok"] is True

        plugin_path = Path(plugin_manager.plugins_dir) / "marketplace.acme.example-plugin@1.2.3"
        assert plugin_path.exists()

        manifest_path = plugin_path / "manifest.json"
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
            assert manifest["plugin_id"] == "marketplace.acme.example-plugin"
            assert manifest["enabled"] is True

    def test_install_quota_exceeded(self, plugin_manager):
        """Test install denied when quota exceeded."""
        for i in range(5):
            req = InstallRequest(
                plugin_id=f"marketplace.acme.plugin-{i}",
                version="1.0.0",
                manifest_url="https://marketplace.local/manifest.json",
                binary_url="https://cdn.marketplace.local/plugin.whl",
                tenant_id="_default",
            )
            plugin_manager.install(req, licensing_tier="free")

        req = InstallRequest(
            plugin_id="marketplace.acme.plugin-6",
            version="1.0.0",
            manifest_url="https://marketplace.local/manifest.json",
            binary_url="https://cdn.marketplace.local/plugin.whl",
            tenant_id="_default",
        )

        with pytest.raises(QuotaExceeded):
            plugin_manager.install(req, licensing_tier="free")

    def test_get_plugin_status(self, plugin_manager):
        """Test retrieving installed plugin status."""
        req = InstallRequest(
            plugin_id="marketplace.acme.example-plugin",
            version="1.2.3",
            manifest_url="https://marketplace.local/manifest.json",
            binary_url="https://cdn.marketplace.local/plugin.whl",
            tenant_id="_default",
        )
        plugin_manager.install(req, licensing_tier="member")

        status = plugin_manager.get_plugin_status("marketplace.acme.example-plugin", "1.2.3")

        assert status is not None
        assert status["plugin_id"] == "marketplace.acme.example-plugin"
        assert status["enabled"] is True
        assert status["health"]["ok"] is True

    def test_disable_plugin(self, plugin_manager):
        """Test disabling an installed plugin."""
        req = InstallRequest(
            plugin_id="marketplace.acme.example-plugin",
            version="1.2.3",
            manifest_url="https://marketplace.local/manifest.json",
            binary_url="https://cdn.marketplace.local/plugin.whl",
            tenant_id="_default",
        )
        plugin_manager.install(req, licensing_tier="member")

        result = plugin_manager.set_plugin_enabled("marketplace.acme.example-plugin", "1.2.3", False)

        assert result["enabled"] is False

        status = plugin_manager.get_plugin_status("marketplace.acme.example-plugin", "1.2.3")
        assert status["enabled"] is False

    def test_uninstall_plugin(self, plugin_manager):
        """Test uninstalling a plugin."""
        req = InstallRequest(
            plugin_id="marketplace.acme.example-plugin",
            version="1.2.3",
            manifest_url="https://marketplace.local/manifest.json",
            binary_url="https://cdn.marketplace.local/plugin.whl",
            tenant_id="_default",
        )
        plugin_manager.install(req, licensing_tier="member")

        result = plugin_manager.uninstall("marketplace.acme.example-plugin", "1.2.3")

        assert result["status"] == "uninstalled"

        status = plugin_manager.get_plugin_status("marketplace.acme.example-plugin", "1.2.3")
        assert status is None

    def test_list_installed_plugins(self, plugin_manager):
        """Test listing all installed plugins."""
        for i in range(3):
            req = InstallRequest(
                plugin_id=f"marketplace.acme.plugin-{i}",
                version="1.0.0",
                manifest_url="https://marketplace.local/manifest.json",
                binary_url="https://cdn.marketplace.local/plugin.whl",
                tenant_id="_default",
            )
            plugin_manager.install(req, licensing_tier="member")

        plugins = plugin_manager.list_installed_plugins()

        assert len(plugins) == 3
