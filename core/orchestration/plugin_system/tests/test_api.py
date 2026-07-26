"""Test suite for Plugin System REST API (ADR-0XXX Phase 1b k=1)."""

import pytest
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import FastAPI

from core.orchestration.plugin_system.models import (
    Plugin, PluginRegistry, PluginType, PluginTier
)
from core.orchestration.plugin_system.managers.lifecycle_manager import PluginLifecycleManager
from core.orchestration.plugin_system.managers.api import create_plugin_routes


@pytest.fixture
def app():
    """Create test FastAPI app with plugin routes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        registry_path = tmppath / "registry.yaml"
        state_path = tmppath / "state"
        
        # Create registry and populate with test plugins
        registry = PluginRegistry(path=registry_path)
        
        # Add test plugins
        plugin1 = Plugin(
            id="test-plugin-1",
            version="1.0.0",
            name="Test Plugin 1",
            plugin_type=PluginType.SKILL,
            enabled=False,
            settings_schema={"type": "object"},
            settings={"model": "haiku"}
        )
        
        plugin2 = Plugin(
            id="test-plugin-2",
            version="2.0.0",
            name="Test Plugin 2",
            plugin_type=PluginType.TOOL,
            enabled=True,
            settings_schema={"type": "object"},
            settings={}
        )
        
        registry.add(plugin1)
        registry.add(plugin2)
        registry.save()
        
        # Create lifecycle manager
        audit_events = []
        def audit_emit(event):
            audit_events.append(event)
        
        lifecycle = PluginLifecycleManager(
            registry=registry,
            audit_emit=audit_emit,
            base_state_path=state_path
        )
        
        # Create FastAPI app
        app = FastAPI()
        router = create_plugin_routes(registry, lifecycle)
        app.include_router(router)
        
        yield app, registry


class TestPluginAPI:
    """Tests for plugin REST API."""
    
    def test_list_plugins(self, app):
        """Test GET /api/plugins."""
        app_instance, _ = app
        client = TestClient(app_instance)
        
        response = client.get("/api/plugins")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["plugins"]) == 2
        assert data["plugins"][0]["id"] == "test-plugin-1"
        assert data["plugins"][1]["id"] == "test-plugin-2"
    
    def test_get_single_plugin(self, app):
        """Test GET /api/plugins/{id}."""
        app_instance, _ = app
        client = TestClient(app_instance)
        
        response = client.get("/api/plugins/test-plugin-1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test-plugin-1"
        assert data["version"] == "1.0.0"
        assert data["enabled"] is False
    
    def test_get_nonexistent_plugin(self, app):
        """Test GET /api/plugins/{id} with nonexistent plugin."""
        app_instance, _ = app
        client = TestClient(app_instance)
        
        response = client.get("/api/plugins/nonexistent")
        
        assert response.status_code == 404
    
    def test_enable_plugin(self, app):
        """Test POST /api/plugins/{id}/enable."""
        app_instance, registry = app
        client = TestClient(app_instance)
        
        # Before: disabled
        assert not registry.get("test-plugin-1").enabled
        
        response = client.post("/api/plugins/test-plugin-1/enable")
        
        # After: enabled
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert registry.get("test-plugin-1").enabled is True
    
    def test_disable_plugin(self, app):
        """Test POST /api/plugins/{id}/disable."""
        app_instance, registry = app
        client = TestClient(app_instance)
        
        # Before: enabled
        assert registry.get("test-plugin-2").enabled
        
        response = client.post("/api/plugins/test-plugin-2/disable")
        
        # After: disabled
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert registry.get("test-plugin-2").enabled is False
    
    def test_update_config(self, app):
        """Test POST /api/plugins/{id}/config."""
        app_instance, registry = app
        client = TestClient(app_instance)
        
        response = client.post(
            "/api/plugins/test-plugin-1/config",
            json={"settings": {"model": "sonnet"}}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["settings"]["model"] == "sonnet"
        assert registry.get("test-plugin-1").settings["model"] == "sonnet"
    
    def test_update_config_validation_error(self, app):
        """Test POST /api/plugins/{id}/config with invalid settings."""
        app_instance, registry = app
        client = TestClient(app_instance)
        
        # Update registry with strict schema
        plugin = registry.get("test-plugin-1")
        plugin.settings_schema = {
            "type": "object",
            "properties": {"model": {"type": "string", "enum": ["haiku", "sonnet"]}},
            "required": ["model"]
        }
        registry.plugins["test-plugin-1"] = plugin
        registry.save()
        
        # Try to set invalid value
        response = client.post(
            "/api/plugins/test-plugin-1/config",
            json={"settings": {"model": "invalid"}}
        )
        
        assert response.status_code == 400  # Validation error


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
