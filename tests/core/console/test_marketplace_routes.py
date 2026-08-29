"""
Unit tests for marketplace API routes (core/console/routes/marketplace.py).

Tests the 7 endpoints exposed to Console UI, using mocked backend.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json

# Import the blueprint
try:
    from core.console.corvin_console.routes.marketplace import (
        marketplace_bp,
        serialize_plugin,
    )
except ImportError:
    pytest.skip("Marketplace routes not available", allow_module_level=True)


@pytest.fixture
def client():
    """Flask test client with marketplace blueprint."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(marketplace_bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def mock_plugin():
    """Mock PluginMetadata for testing."""
    from enum import Enum

    class MockCategory(Enum):
        SECURITY = "Security"

    class MockOrigin(Enum):
        COMMUNITY = "community"

    class MockBootLayer(Enum):
        INSTALLED = "installed"

    class MockPlugin:
        def __init__(self):
            self.plugin_id = "test-plugin"
            self.name = "Test Plugin"
            self.version = "0.1.0"
            self.category = MockCategory.SECURITY
            self.origin = MockOrigin.COMMUNITY
            self.boot_layer = MockBootLayer.INSTALLED
            self.author_id = "test-author"
            self.author_email = "test@example.com"
            self.license = "MIT"
            self.description = "A test plugin"
            self.long_description = "# Test Plugin\n\nThis is a test."
            self.homepage_url = "https://example.com"
            self.repository_url = "https://github.com/example/test-plugin"
            self.rating_count = 10
            self.rating_average = 4.5
            self.download_count = 100
            self.listed = True

    return MockPlugin()


class TestMarketplaceIndex:
    """Test GET /api/v2/marketplace/index"""

    def test_index_success(self, client, mock_plugin):
        """Successfully fetch marketplace index."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.list_all.return_value = [mock_plugin]

            response = client.get("/api/v2/marketplace/index")
            assert response.status_code == 200

            data = json.loads(response.data)
            assert data["version"] == "1.0"
            assert "extensions" in data
            assert len(data["extensions"]) == 1
            assert data["extensions"][0]["plugin_id"] == "test-plugin"

    def test_index_backend_unavailable(self, client):
        """Return 503 if marketplace backend unavailable."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery",
            None,
        ):
            response = client.get("/api/v2/marketplace/index")
            assert response.status_code == 503


class TestMarketplaceSearch:
    """Test GET /api/v2/marketplace/search"""

    def test_search_success(self, client, mock_plugin):
        """Successfully search marketplace."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.list_all.return_value = [mock_plugin]

            response = client.get("/api/v2/marketplace/search?q=test")
            assert response.status_code == 200

            data = json.loads(response.data)
            assert "extensions" in data

    def test_search_with_category_filter(self, client, mock_plugin):
        """Search with category filter."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.list_all.return_value = [mock_plugin]

            response = client.get(
                "/api/v2/marketplace/search?category=Security"
            )
            assert response.status_code == 200

            data = json.loads(response.data)
            assert len(data["extensions"]) > 0

    def test_search_with_invalid_rating_min(self, client):
        """Handle invalid rating_min parameter."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ):
            response = client.get(
                "/api/v2/marketplace/search?rating_min=invalid"
            )
            assert response.status_code == 400


class TestMarketplaceExtensionDetails:
    """Test GET /api/v2/marketplace/extension/<id>"""

    def test_extension_details_success(self, client, mock_plugin):
        """Successfully fetch extension details."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = mock_plugin

            response = client.get("/api/v2/marketplace/extension/test-plugin")
            assert response.status_code == 200

            data = json.loads(response.data)
            assert data["id"] == "test-plugin"
            assert "metadata" in data
            assert "readme_url" in data

    def test_extension_details_not_found(self, client):
        """Return 404 if extension not found."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = None

            response = client.get("/api/v2/marketplace/extension/nonexistent")
            assert response.status_code == 404


class TestMarketplaceInstall:
    """Test POST /api/v2/marketplace/install"""

    def test_install_success(self, client, mock_plugin):
        """Successfully queue installation."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = mock_plugin

            response = client.post(
                "/api/v2/marketplace/install",
                json={
                    "extension_id": "test-plugin",
                    "version": "0.1.0",
                    "tenant_id": "default",
                },
            )
            assert response.status_code == 202

            data = json.loads(response.data)
            assert data["status"] == "queued"
            assert "job_id" in data
            assert "progress_url" in data

    def test_install_missing_extension_id(self, client):
        """Return 400 if extension_id missing."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ):
            response = client.post(
                "/api/v2/marketplace/install",
                json={"version": "0.1.0"},
            )
            assert response.status_code == 400

    def test_install_extension_not_found(self, client):
        """Return 404 if extension not found."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = None

            response = client.post(
                "/api/v2/marketplace/install",
                json={"extension_id": "nonexistent"},
            )
            assert response.status_code == 404

    def test_install_no_body(self, client):
        """Return 400 if request body missing."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ):
            response = client.post("/api/v2/marketplace/install")
            assert response.status_code == 400


class TestMarketplaceUninstall:
    """Test POST /api/v2/marketplace/uninstall"""

    def test_uninstall_success(self, client, mock_plugin):
        """Successfully queue uninstallation."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = mock_plugin

            response = client.post(
                "/api/v2/marketplace/uninstall",
                json={
                    "extension_id": "test-plugin",
                    "tenant_id": "default",
                },
            )
            assert response.status_code == 202

            data = json.loads(response.data)
            assert data["status"] == "queued"
            assert "job_id" in data

    def test_uninstall_extension_not_found(self, client):
        """Return 404 if extension not found."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = None

            response = client.post(
                "/api/v2/marketplace/uninstall",
                json={"extension_id": "nonexistent"},
            )
            assert response.status_code == 404


class TestMarketplaceEnable:
    """Test PATCH /api/v2/marketplace/extension/<id>/enable"""

    def test_enable_success(self, client, mock_plugin):
        """Successfully enable extension."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = mock_plugin

            response = client.patch(
                "/api/v2/marketplace/extension/test-plugin/enable",
                json={"tenant_id": "default"},
            )
            assert response.status_code == 200

            data = json.loads(response.data)
            assert data["status"] == "enabled"

    def test_enable_extension_not_found(self, client):
        """Return 404 if extension not found."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = None

            response = client.patch(
                "/api/v2/marketplace/extension/nonexistent/enable"
            )
            assert response.status_code == 404


class TestMarketplaceDisable:
    """Test PATCH /api/v2/marketplace/extension/<id>/disable"""

    def test_disable_success(self, client, mock_plugin):
        """Successfully disable extension."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = mock_plugin

            response = client.patch(
                "/api/v2/marketplace/extension/test-plugin/disable",
                json={"tenant_id": "default"},
            )
            assert response.status_code == 200

            data = json.loads(response.data)
            assert data["status"] == "disabled"

    def test_disable_extension_not_found(self, client):
        """Return 404 if extension not found."""
        with patch(
            "core.console.corvin_console.routes.marketplace.PluginDiscovery"
        ) as mock_discovery:
            mock_discovery.get_by_id.return_value = None

            response = client.patch(
                "/api/v2/marketplace/extension/nonexistent/disable"
            )
            assert response.status_code == 404


class TestSerializePlugin:
    """Test serialize_plugin helper."""

    def test_serialize_plugin(self, mock_plugin):
        """Successfully serialize plugin metadata."""
        result = serialize_plugin(mock_plugin)

        assert result["plugin_id"] == "test-plugin"
        assert result["name"] == "Test Plugin"
        assert result["category"] == "Security"  # Enum converted to string
        assert result["origin"] == "community"
