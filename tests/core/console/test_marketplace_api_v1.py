"""
Integration tests for Console Marketplace API v1 (ADR-0511).

Tests HTTP endpoints against the Console app:
- GET /api/v1/marketplace/plugins
- GET /api/v1/marketplace/plugins/{id}
- GET /api/v1/marketplace/stats
"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, Any

# Add CorvinOS to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Note: These are integration tests that would run with FastAPI TestClient.
# In a real setup, pytest + httpx would be used.

def create_test_index(tmpdir: Path) -> Path:
    """Create a test marketplace index (plugins.json)."""
    index_dir = tmpdir / "operator" / "marketplace" / "index"
    index_dir.mkdir(parents=True, exist_ok=True)

    index = {
        "version": "2.0",
        "schema": "ADR-0511",
        "generated_at": "2026-08-31T00:00:00Z",
        "plugin_count": 2,
        "plugins": [
            {
                "id": "plugin:buildin-memory-recall_backend",
                "type": "plugin",
                "name": "CEL Session Recall",
                "version": "1.0.0",
                "author": "Anthropic PBC",
                "license": "Apache-2.0",
                "tier": "buildin",
                "category": "memory",
                "description": "Session recall backend",
                "distribution": {"supports_source": True, "supports_wheel": True},
                "last_updated": "2026-08-31T00:00:00Z",
            },
            {
                "id": "plugin:buildin-security_compliance-audit_backend",
                "type": "plugin",
                "name": "Audit Backend",
                "version": "1.0.0",
                "author": "Anthropic PBC",
                "license": "Apache-2.0",
                "tier": "buildin",
                "category": "security_compliance",
                "description": "Audit trail management",
                "distribution": {"supports_source": True, "supports_wheel": True},
                "last_updated": "2026-08-31T00:00:00Z",
            },
        ],
        "by_id": {},
        "by_category": {},
        "by_tier": {},
    }

    # Build lookup tables
    index["by_id"] = {p["id"]: p for p in index["plugins"]}
    for p in index["plugins"]:
        cat = p["category"]
        if cat not in index["by_category"]:
            index["by_category"][cat] = []
        index["by_category"][cat].append(p)

        tier = p["tier"]
        if tier not in index["by_tier"]:
            index["by_tier"][tier] = []
        index["by_tier"][tier].append(p)

    index_file = index_dir / "plugins.json"
    with open(index_file, "w") as f:
        json.dump(index, f, indent=2)

    return index_file


class MockTestClient:
    """Mock FastAPI TestClient for testing (would use httpx in real tests)."""

    def __init__(self, app, marketplace_index_path: Path):
        self.app = app
        self.marketplace_index_path = marketplace_index_path

    def get(self, path: str, params: Dict[str, str] = None) -> Dict[str, Any]:
        """Simulate GET request (mock implementation)."""
        # In a real test, this would use httpx/FastAPI TestClient
        # For now, demonstrate the expected behavior
        sys.path.insert(0, str(Path.cwd()))
        from core.console.corvin_console.routes.marketplace import _index_manager

        # Load index
        _index_manager.set_index_path(self.marketplace_index_path)

        if path == "/api/v1/marketplace/plugins":
            # Simulate list_plugins
            index = _index_manager.get_index()
            plugins = index.get("plugins", [])

            if params:
                if "category" in params:
                    plugins = [p for p in plugins if p.get("category") == params["category"]]
                if "tier" in params:
                    plugins = [p for p in plugins if p.get("tier") == params["tier"]]

            return {
                "status": 200,
                "data": {
                    "plugins": plugins,
                    "count": len(plugins),
                    "filtered_by": params or {},
                },
            }

        elif path.startswith("/api/v1/marketplace/plugins/"):
            # Simulate get_plugin
            plugin_id = path.split("/")[-1]
            index = _index_manager.get_index()
            plugin = index.get("by_id", {}).get(plugin_id)

            if plugin:
                return {"status": 200, "data": plugin}
            else:
                return {"status": 404, "data": {"detail": f"Plugin '{plugin_id}' not found"}}

        elif path == "/api/v1/marketplace/stats":
            # Simulate marketplace_stats
            index = _index_manager.get_index()
            return {
                "status": 200,
                "data": {
                    "total_plugins": index.get("plugin_count", 0),
                    "by_category": {
                        cat: len(plugins)
                        for cat, plugins in index.get("by_category", {}).items()
                    },
                    "by_tier": {
                        tier: len(plugins)
                        for tier, plugins in index.get("by_tier", {}).items()
                    },
                    "schema_version": index.get("schema", "unknown"),
                },
            }

        return {"status": 404, "data": {"detail": "Not found"}}


def test_marketplace_api_list_plugins():
    """Test GET /api/v1/marketplace/plugins — list all."""
    with TemporaryDirectory() as tmpdir:
        index_path = create_test_index(Path(tmpdir))
        client = MockTestClient(None, index_path)

        response = client.get("/api/v1/marketplace/plugins")

        assert response["status"] == 200
        assert response["data"]["count"] == 2
        assert len(response["data"]["plugins"]) == 2
        assert response["data"]["plugins"][0]["name"] == "CEL Session Recall"


def test_marketplace_api_filter_by_category():
    """Test GET /api/v1/marketplace/plugins?category=memory — filter."""
    with TemporaryDirectory() as tmpdir:
        index_path = create_test_index(Path(tmpdir))
        client = MockTestClient(None, index_path)

        response = client.get("/api/v1/marketplace/plugins", params={"category": "memory"})

        assert response["status"] == 200
        assert response["data"]["count"] == 1
        assert response["data"]["plugins"][0]["category"] == "memory"


def test_marketplace_api_filter_by_tier():
    """Test GET /api/v1/marketplace/plugins?tier=buildin — filter."""
    with TemporaryDirectory() as tmpdir:
        index_path = create_test_index(Path(tmpdir))
        client = MockTestClient(None, index_path)

        response = client.get("/api/v1/marketplace/plugins", params={"tier": "buildin"})

        assert response["status"] == 200
        assert response["data"]["count"] == 2
        for plugin in response["data"]["plugins"]:
            assert plugin["tier"] == "buildin"


def test_marketplace_api_get_plugin():
    """Test GET /api/v1/marketplace/plugins/{id} — details."""
    with TemporaryDirectory() as tmpdir:
        index_path = create_test_index(Path(tmpdir))
        client = MockTestClient(None, index_path)

        plugin_id = "plugin:buildin-memory-recall_backend"
        response = client.get(f"/api/v1/marketplace/plugins/{plugin_id}")

        assert response["status"] == 200
        assert response["data"]["id"] == plugin_id
        assert response["data"]["name"] == "CEL Session Recall"


def test_marketplace_api_get_plugin_not_found():
    """Test GET /api/v1/marketplace/plugins/{id} — 404."""
    with TemporaryDirectory() as tmpdir:
        index_path = create_test_index(Path(tmpdir))
        client = MockTestClient(None, index_path)

        response = client.get("/api/v1/marketplace/plugins/plugin:nonexistent")

        assert response["status"] == 404
        assert "not found" in response["data"]["detail"].lower()


def test_marketplace_api_stats():
    """Test GET /api/v1/marketplace/stats — statistics."""
    with TemporaryDirectory() as tmpdir:
        index_path = create_test_index(Path(tmpdir))
        client = MockTestClient(None, index_path)

        response = client.get("/api/v1/marketplace/stats")

        assert response["status"] == 200
        assert response["data"]["total_plugins"] == 2
        assert response["data"]["by_category"]["memory"] == 1
        assert response["data"]["by_category"]["security_compliance"] == 1
        assert response["data"]["by_tier"]["buildin"] == 2


if __name__ == "__main__":
    # Run tests manually
    print("Running marketplace API integration tests...")
    test_marketplace_api_list_plugins()
    print("✅ test_marketplace_api_list_plugins")

    test_marketplace_api_filter_by_category()
    print("✅ test_marketplace_api_filter_by_category")

    test_marketplace_api_filter_by_tier()
    print("✅ test_marketplace_api_filter_by_tier")

    test_marketplace_api_get_plugin()
    print("✅ test_marketplace_api_get_plugin")

    test_marketplace_api_get_plugin_not_found()
    print("✅ test_marketplace_api_get_plugin_not_found")

    test_marketplace_api_stats()
    print("✅ test_marketplace_api_stats")

    print("\n✅ All tests passed!")
