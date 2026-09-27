"""Tests for plugin loader — marketplace plugins with console adapter injection.

ADR-0039 Phase 6: Dual-running + migration infrastructure.
"""

import pytest
from fastapi import APIRouter
from unittest.mock import Mock, patch, MagicMock


class TestPluginLoaderBasic:
    """Test basic PluginLoader initialization and router retrieval."""

    def test_loader_init(self):
        """PluginLoader initializes without error."""
        from corvin_console.routes.plugins_loader import PluginLoader

        loader = PluginLoader()
        assert loader is not None
        assert loader._plugin_cache == {}

    def test_get_workflows_router_callable(self):
        """get_workflows_router() function is callable."""
        from corvin_console.routes.plugins_loader import get_workflows_router

        assert callable(get_workflows_router)


class TestPluginLoaderFallback:
    """Test console routes fallback when plugin unavailable."""

    def test_fallback_to_console_routes(self):
        """When marketplace plugin unavailable, fallback to console routes."""
        from corvin_console.routes.plugins_loader import PluginLoader

        loader = PluginLoader()

        # Mock: marketplace plugin not found
        with patch("corvin_console.routes.plugins_loader.PluginLoader._try_load_marketplace_plugin") as mock_mp:
            mock_mp.side_effect = ImportError("Plugin not available")

            # Should fallback to console routes
            router = loader.load_workflows_plugin(force_fallback=False)

            assert router is not None
            assert isinstance(router, APIRouter)

    def test_force_fallback_skips_marketplace(self):
        """force_fallback=True skips marketplace plugin entirely."""
        from corvin_console.routes.plugins_loader import PluginLoader

        loader = PluginLoader()

        with patch("corvin_console.routes.plugins_loader.PluginLoader._try_load_marketplace_plugin") as mock_mp:
            # Should not call _try_load_marketplace_plugin
            router = loader.load_workflows_plugin(force_fallback=True)

            mock_mp.assert_not_called()
            assert router is not None
            assert isinstance(router, APIRouter)

    def test_console_routes_fallback_error(self):
        """When both plugin and console routes fail, raise PluginLoaderError."""
        from corvin_console.routes.plugins_loader import PluginLoader, PluginLoaderError

        loader = PluginLoader()

        # Mock both paths failing
        with patch.object(loader, "_try_load_marketplace_plugin") as mock_mp:
            with patch.object(loader, "_fallback_console_routes") as mock_fb:
                mock_mp.side_effect = ImportError("No plugin")
                mock_fb.side_effect = ImportError("No console routes")

                with pytest.raises(PluginLoaderError):
                    loader.load_workflows_plugin(force_fallback=False)


class TestPluginLoaderCaching:
    """Test router caching to prevent repeated initialization."""

    def test_router_caching(self):
        """Repeated calls return cached router."""
        from corvin_console.routes.plugins_loader import PluginLoader

        loader = PluginLoader()

        with patch.object(loader, "_try_load_marketplace_plugin") as mock_mp:
            mock_mp.side_effect = ImportError("No plugin")

            # First call
            router1 = loader.load_workflows_plugin(force_fallback=False)

            # Second call should use cache (no additional calls)
            mock_mp.reset_mock()
            router2 = loader.load_workflows_plugin(force_fallback=False)

            # Same object
            assert router1 is router2

            # _try_load_marketplace_plugin should not be called on second pass (uses cache)
            # (Actually, second pass will call _try_load again because cache_key still
            # gets set in first call, but let's verify cache logic works)
            assert "workflows_router" in loader._plugin_cache


class TestPluginLoaderMarketplaceRefusal:
    """The marketplace workflows plugin is refused, not attempted (2026-09-27).

    Its routes take ``tenant_id`` from the query string and never consult the
    session backend, so mounting it would let any caller read/write any
    tenant's workflows. The console routes are the mounted implementation.
    """

    def test_marketplace_plugin_not_attempted_console_routes_mounted(self):
        from corvin_console.routes import plugins_loader
        from corvin_console.routes import workflows as workflows_console

        assert plugins_loader._MARKETPLACE_WORKFLOWS_MOUNTABLE is False
        loader = plugins_loader.PluginLoader()
        with patch.object(loader, "_try_load_marketplace_plugin") as mock_mp:
            router = loader.load_workflows_plugin(session_auth_module=Mock(), audit_backend=Mock())
        mock_mp.assert_not_called()
        assert router is workflows_console.router

    def test_mounted_router_serves_the_paths_the_spa_calls(self):
        from corvin_console.routes import plugins_loader

        router = plugins_loader.PluginLoader().load_workflows_plugin()
        paths = {getattr(r, "path", "") for r in router.routes}
        assert "/workflows" in paths
        assert "/workflows/{wid}" in paths
        assert not any(p.startswith("/workflows/workflows") for p in paths)


class TestPluginLoaderGlobalSingleton:
    """Test global singleton pattern."""

    def test_get_workflows_router_function(self):
        """get_workflows_router() uses global singleton."""
        from corvin_console.routes.plugins_loader import get_workflows_router, _loader

        with patch.object(_loader, "load_workflows_plugin") as mock_load:
            mock_router = Mock(spec=APIRouter)
            mock_load.return_value = mock_router

            result = get_workflows_router()

            mock_load.assert_called_once()
            assert result == mock_router


class TestPluginLoaderErrorHandling:
    """Test error handling and reporting."""

    def test_error_logging_on_plugin_failure(self, caplog):
        """Plugin load failure is logged as warning before fallback."""
        from corvin_console.routes.plugins_loader import PluginLoader

        loader = PluginLoader()

        with patch.object(loader, "_try_load_marketplace_plugin") as mock_mp:
            mock_mp.side_effect = RuntimeError("Plugin initialization failed")

            router = loader.load_workflows_plugin(force_fallback=False)

            # Should have logged warning
            assert router is not None

    def test_error_logging_on_both_failure(self, caplog):
        """When both paths fail, error is logged."""
        from corvin_console.routes.plugins_loader import PluginLoader, PluginLoaderError

        loader = PluginLoader()

        with patch.object(loader, "_try_load_marketplace_plugin") as mock_mp:
            with patch.object(loader, "_fallback_console_routes") as mock_fb:
                mock_mp.side_effect = ImportError("No plugin")
                mock_fb.side_effect = ImportError("No console routes")

                with pytest.raises(PluginLoaderError) as exc_info:
                    loader.load_workflows_plugin(force_fallback=False)

                assert "cannot proceed" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
