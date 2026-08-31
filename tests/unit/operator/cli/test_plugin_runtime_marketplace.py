"""Tests for Gap 1: CLI marketplace plugin install."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile
import sys

# Add ops/launcher to path
sys.path.insert(0, str(Path(__file__).parents[5] / "ops" / "launcher"))

from corvin.plugin_runtime_cmd import (
    _is_plugin_id,
    _cmd_install_marketplace,
    _cmd_install_local,
    cmd_install,
)


class TestPluginIdDetection:
    """Test _is_plugin_id() heuristic."""

    def test_plugin_id_simple(self):
        """Simple plugin ID without path separators."""
        assert _is_plugin_id("auth-saml-2.1") is True
        assert _is_plugin_id("slack-bridge") is True
        assert _is_plugin_id("my-plugin") is True

    def test_plugin_id_with_dots(self):
        """Plugin ID may have dots (versions, namespaces)."""
        assert _is_plugin_id("auth.saml.2.1") is True
        assert _is_plugin_id("com.example.plugin") is True

    def test_path_unix(self):
        """Unix paths contain forward slashes."""
        assert _is_plugin_id("/usr/local/plugin") is False
        assert _is_plugin_id("./plugin") is False
        assert _is_plugin_id("../plugin") is False
        assert _is_plugin_id("~/plugin") is True  # ~ alone is a plugin ID

    def test_path_windows(self):
        """Windows paths contain backslashes."""
        assert _is_plugin_id("C:\\Users\\plugin") is False
        assert _is_plugin_id(".\\plugin") is False


class TestInstallMarketplace:
    """Test _cmd_install_marketplace()."""

    @patch("corvin.plugin_runtime_cmd.PluginMarketplace")
    @patch("corvin.plugin_runtime_cmd.DependencyResolver")
    @patch("corvin.plugin_runtime_cmd.get_tenant_registry")
    def test_marketplace_install_success(self, mock_registry, mock_resolver, mock_marketplace):
        """Successfully install plugin from marketplace."""
        # Setup mocks
        mock_mkt_instance = MagicMock()
        mock_marketplace.get_default.return_value = mock_mkt_instance
        mock_mkt_instance.get_index.return_value = {"auth-saml-2.1": {"version": "2.1"}}

        mock_resolver_instance = MagicMock()
        mock_resolver.return_value = mock_resolver_instance
        mock_resolver_instance.resolve_install_order.return_value = (
            ["auth-saml-2.1"],
            []
        )

        mock_mkt_instance.install_plugin.return_value = True
        mock_registry.return_value = MagicMock()

        # Call
        args = MagicMock()
        args.tenant = None
        result = _cmd_install_marketplace("auth-saml-2.1", args, "_default")

        # Verify
        assert result == 0
        mock_mkt_instance.install_plugin.assert_called_once()

    @patch("corvin.plugin_runtime_cmd.PluginMarketplace")
    @patch("corvin.plugin_runtime_cmd.DependencyResolver")
    def test_marketplace_install_not_found(self, mock_resolver, mock_marketplace):
        """Handle plugin not found in marketplace."""
        mock_mkt_instance = MagicMock()
        mock_marketplace.get_default.return_value = mock_mkt_instance
        mock_mkt_instance.get_index.return_value = {}

        mock_resolver_instance = MagicMock()
        mock_resolver.return_value = mock_resolver_instance
        mock_resolver_instance.resolve_install_order.return_value = ([], [])

        args = MagicMock()
        args.tenant = None
        result = _cmd_install_marketplace("nonexistent-plugin", args, "_default")

        # Should return error
        assert result == 1

    @patch("corvin.plugin_runtime_cmd.PluginMarketplace")
    @patch("corvin.plugin_runtime_cmd.DependencyResolver")
    def test_marketplace_install_conflicts(self, mock_resolver, mock_marketplace):
        """Handle dependency conflicts."""
        mock_mkt_instance = MagicMock()
        mock_marketplace.get_default.return_value = mock_mkt_instance
        mock_mkt_instance.get_index.return_value = {
            "plugin-a": {"version": "1.0"},
            "plugin-b": {"version": "1.0"}
        }

        mock_resolver_instance = MagicMock()
        mock_resolver.return_value = mock_resolver_instance
        mock_resolver_instance.resolve_install_order.return_value = (
            [],
            ["plugin-a conflicts with plugin-b"]
        )

        args = MagicMock()
        args.tenant = None
        result = _cmd_install_marketplace("plugin-a", args, "_default")

        # Should return conflict error
        assert result == 1


class TestInstallLocal:
    """Test _cmd_install_local()."""

    def test_local_install_missing_dir(self):
        """Handle missing plugin directory."""
        args = MagicMock()
        args.tenant = None
        result = _cmd_install_local("/nonexistent/path", args, "_default")

        # Should return error
        assert result == 2

    @patch("corvin.plugin_runtime_cmd.validate_manifest_file")
    def test_local_install_missing_manifest(self, mock_validate):
        """Handle missing plugin.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir)

            args = MagicMock()
            args.tenant = None
            result = _cmd_install_local(str(plugin_dir), args, "_default")

            # Should return error for missing manifest
            assert result == 2

    @patch("corvin.plugin_runtime_cmd.validate_manifest_file")
    @patch("corvin.plugin_runtime_cmd.get_tenant_registry")
    def test_local_install_success(self, mock_registry, mock_validate):
        """Successfully install plugin from local directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir)
            manifest_file = plugin_dir / "plugin.yaml"
            manifest_file.write_text("""
plugin_id: test-plugin
version: 1.0.0
display_name: Test Plugin
boot_layer: installed
""")

            # Mock validation success
            mock_report = MagicMock()
            mock_report.ok = True
            mock_validate.return_value = mock_report

            # Mock registry
            mock_registry_instance = MagicMock()
            mock_registry.return_value = mock_registry_instance

            args = MagicMock()
            args.tenant = None
            result = _cmd_install_local(str(plugin_dir), args, "_default")

            # Should succeed
            assert result == 0
            mock_registry_instance.register_plugin.assert_called_once()


class TestInstallDispatch:
    """Test cmd_install() dispatcher."""

    @patch("corvin.plugin_runtime_cmd._cmd_install_marketplace")
    def test_dispatch_to_marketplace(self, mock_marketplace):
        """Dispatch plugin ID to marketplace installer."""
        mock_marketplace.return_value = 0

        args = MagicMock()
        args.plugin_id_or_path = "auth-saml-2.1"
        args.tenant = None

        result = cmd_install(args)

        # Should call marketplace installer
        mock_marketplace.assert_called_once()
        assert result == 0

    @patch("corvin.plugin_runtime_cmd._cmd_install_local")
    def test_dispatch_to_local(self, mock_local):
        """Dispatch path to local installer."""
        mock_local.return_value = 0

        args = MagicMock()
        args.plugin_id_or_path = "/path/to/plugin"
        args.tenant = None

        result = cmd_install(args)

        # Should call local installer
        mock_local.assert_called_once()
        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
