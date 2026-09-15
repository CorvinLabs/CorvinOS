"""Tests for RAG Registry."""
import json
from pathlib import Path

import pytest
import yaml

from ..registry.rag_registry import RAGRegistry, ProviderEntry, get_default_registry_dir


@pytest.fixture
def registry_dir(tmp_path):
    """Create temporary registry directory."""
    return tmp_path / "rag"


@pytest.fixture
def registry(registry_dir):
    """Initialize registry."""
    return RAGRegistry(registry_dir)


@pytest.fixture
def valid_manifest(tmp_path):
    """Create a valid test manifest."""
    manifest = {
        "api_version": "rag.corvin.io/v1",
        "kind": "RAGProvider",
        "metadata": {
            "id": "test-provider",
            "name": "Test Provider",
            "version": "1.0.0",
        },
        "spec": {
            "retrieval": {
                "type": "http-api",
                "endpoint": "https://test.example.com/api",
                "timeout_ms": 5000,
                "auth": {
                    "type": "bearer-token",
                    "token_env_var": "RAG_TEST_TOKEN",
                },
                "response_schema": {
                    "type": "object",
                    "properties": {"results": {"type": "array"}},
                },
            },
            "classification": {"data_type": "INTERNAL"},
        },
    }

    manifest_file = tmp_path / "test-manifest.yaml"
    with open(manifest_file, "w") as f:
        yaml.dump(manifest, f)

    return manifest_file


class TestRegistryInit:
    """Test registry initialization."""

    def test_registry_creates_directories(self, registry_dir):
        """Registry creates required directories."""
        registry = RAGRegistry(registry_dir)
        assert registry.registry_dir.exists()
        assert registry.manifests_dir.exists()

    def test_registry_creates_index(self, registry_dir):
        """Registry creates index file."""
        registry = RAGRegistry(registry_dir)
        # Index should be created on first save
        assert len(registry.index.providers) == 0


class TestRegistration:
    """Test provider registration."""

    def test_register_valid_manifest(self, registry, valid_manifest):
        """Register a valid manifest."""
        success, message = registry.register(valid_manifest)
        assert success is True
        assert "test-provider" in message

    def test_register_creates_manifest_copy(self, registry, valid_manifest):
        """Registration copies manifest to registry."""
        registry.register(valid_manifest)
        manifest_copy = registry.manifests_dir / "test-provider.yaml"
        assert manifest_copy.exists()

    def test_register_adds_to_index(self, registry, valid_manifest):
        """Registration adds entry to registry index."""
        registry.register(valid_manifest)
        providers = registry.list_providers()
        assert len(providers) == 1
        assert providers[0].id == "test-provider"
        assert providers[0].status == "active"

    def test_register_nonexistent_file(self, registry):
        """Register nonexistent manifest fails."""
        success, message = registry.register("/nonexistent/manifest.yaml")
        assert success is False
        assert "not found" in message.lower()

    def test_register_invalid_manifest(self, registry, tmp_path):
        """Register invalid manifest fails."""
        bad_manifest = tmp_path / "bad.yaml"
        bad_manifest.write_text(
            """
api_version: rag.corvin.io/v1
kind: RAGProvider
metadata:
  id: bad-provider
# Missing spec!
"""
        )

        success, message = registry.register(bad_manifest)
        assert success is False
        assert "validation" in message.lower()

    def test_register_duplicate_updates(self, registry, valid_manifest):
        """Registering same provider twice updates entry."""
        registry.register(valid_manifest)
        providers_1 = registry.list_providers()
        assert len(providers_1) == 1

        # Register again
        registry.register(valid_manifest)
        providers_2 = registry.list_providers()
        assert len(providers_2) == 1  # Still just one


class TestListing:
    """Test provider listing."""

    def test_list_empty(self, registry):
        """List empty registry."""
        providers = registry.list_providers()
        assert len(providers) == 0

    def test_list_multiple_providers(self, registry, tmp_path):
        """List multiple providers."""
        # Register 3 providers
        for i in range(3):
            manifest = {
                "api_version": "rag.corvin.io/v1",
                "kind": "RAGProvider",
                "metadata": {
                    "id": f"provider-{i}",
                    "name": f"Provider {i}",
                    "version": "1.0.0",
                },
                "spec": {
                    "retrieval": {
                        "type": "http-api",
                        "endpoint": f"https://test{i}.example.com",
                        "timeout_ms": 5000,
                        "auth": {
                            "type": "bearer-token",
                            "token_env_var": f"RAG_TOKEN_{i}",
                        },
                        "response_schema": {"type": "object"},
                    },
                    "classification": {"data_type": "INTERNAL"},
                },
            }

            manifest_file = tmp_path / f"manifest-{i}.yaml"
            with open(manifest_file, "w") as f:
                yaml.dump(manifest, f)

            registry.register(manifest_file)

        providers = registry.list_providers()
        assert len(providers) == 3

    def test_list_filter_by_status(self, registry, valid_manifest):
        """List providers filtered by status."""
        registry.register(valid_manifest)

        active = registry.list_providers(status="active")
        assert len(active) == 1

        degraded = registry.list_providers(status="degraded")
        assert len(degraded) == 0


class TestRetrieval:
    """Test provider retrieval."""

    def test_get_provider(self, registry, valid_manifest):
        """Get provider by ID."""
        registry.register(valid_manifest)

        provider = registry.get_provider("test-provider")
        assert provider is not None
        assert provider.id == "test-provider"
        assert provider.name == "Test Provider"

    def test_get_provider_not_found(self, registry):
        """Get nonexistent provider returns None."""
        provider = registry.get_provider("nonexistent")
        assert provider is None

    def test_get_manifest(self, registry, valid_manifest):
        """Load provider manifest."""
        registry.register(valid_manifest)

        manifest = registry.get_manifest("test-provider")
        assert manifest is not None
        assert manifest["metadata"]["id"] == "test-provider"

    def test_get_manifest_not_found(self, registry):
        """Load nonexistent manifest returns None."""
        manifest = registry.get_manifest("nonexistent")
        assert manifest is None


class TestUnregistration:
    """Test provider unregistration."""

    def test_unregister_removes_entry(self, registry, valid_manifest):
        """Unregister removes from index."""
        registry.register(valid_manifest)
        assert len(registry.list_providers()) == 1

        success, message = registry.unregister("test-provider")
        assert success is True
        assert len(registry.list_providers()) == 0

    def test_unregister_removes_manifest(self, registry, valid_manifest):
        """Unregister removes manifest file."""
        registry.register(valid_manifest)
        manifest_path = registry.manifests_dir / "test-provider.yaml"
        assert manifest_path.exists()

        registry.unregister("test-provider")
        assert not manifest_path.exists()

    def test_unregister_nonexistent(self, registry):
        """Unregister nonexistent provider succeeds silently."""
        success, message = registry.unregister("nonexistent")
        # Unregister always returns success (idempotent)
        assert success is True


class TestHealthStatus:
    """Test health status updates."""

    def test_update_health_status(self, registry, valid_manifest):
        """Update provider health status."""
        registry.register(valid_manifest)

        success = registry.update_health_status(
            "test-provider",
            "degraded",
            latency_ms=1000,
        )
        assert success is True

        provider = registry.get_provider("test-provider")
        assert provider.health_status == "degraded"
        assert provider.last_health_check is not None

    def test_update_nonexistent_health(self, registry):
        """Update nonexistent provider fails."""
        success = registry.update_health_status("nonexistent", "healthy")
        assert success is False


class TestQueryStats:
    """Test query statistics tracking."""

    def test_update_query_stats(self, registry, valid_manifest):
        """Update query statistics."""
        registry.register(valid_manifest)

        # Record 5 queries
        for latency in [100, 200, 150, 300, 250]:
            registry.update_query_stats("test-provider", latency)

        provider = registry.get_provider("test-provider")
        assert provider.query_stats["total"] == 5
        assert provider.query_stats["today"] == 5
        assert provider.query_stats["avg_latency_ms"] == 200  # (100+200+150+300+250)/5

    def test_query_stats_nonexistent(self, registry):
        """Update stats for nonexistent provider fails."""
        success = registry.update_query_stats("nonexistent", 100)
        assert success is False


class TestPersistence:
    """Test registry persistence."""

    def test_registry_persists_to_disk(self, registry_dir, valid_manifest):
        """Registry persists index to disk."""
        registry1 = RAGRegistry(registry_dir)
        registry1.register(valid_manifest)

        # Create new registry instance, should load from disk
        registry2 = RAGRegistry(registry_dir)
        providers = registry2.list_providers()
        assert len(providers) == 1
        assert providers[0].id == "test-provider"

    def test_registry_manifest_persists(self, registry_dir, valid_manifest):
        """Manifests persist to disk."""
        registry1 = RAGRegistry(registry_dir)
        registry1.register(valid_manifest)

        # Create new registry instance
        registry2 = RAGRegistry(registry_dir)
        manifest = registry2.get_manifest("test-provider")
        assert manifest is not None
        assert manifest["metadata"]["id"] == "test-provider"
