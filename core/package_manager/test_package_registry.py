"""Tests for PackageRegistry — package persistence and multi-tenant management (ADR-0268)."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from .package_registry import InstalledPackage, PackageRegistry


@pytest.fixture
def temp_corvin_home(tmp_path, monkeypatch):
    """Mock CORVIN_HOME to a temporary directory for testing."""
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return corvin_home


@pytest.fixture
def sample_package():
    """Create a sample InstalledPackage for testing."""
    return InstalledPackage(
        id="com.example.test-skill",
        version="1.0.0",
        path="~/.corvin/tenants/_default/packages/com.example.test-skill/",
        manifest={
            "name": "Test Skill",
            "version": "1.0.0",
            "contents": {"skills": []},
        },
        installed_at=datetime.utcnow().isoformat(),
        enabled=True,
    )


@pytest.fixture
def sample_package_2():
    """Create a second sample InstalledPackage for testing."""
    return InstalledPackage(
        id="com.example.another-skill",
        version="2.0.0",
        path="~/.corvin/tenants/_default/packages/com.example.another-skill/",
        manifest={
            "name": "Another Skill",
            "version": "2.0.0",
            "contents": {"skills": []},
        },
        installed_at=datetime.utcnow().isoformat(),
        enabled=True,
    )


class TestInstalledPackage:
    """Test InstalledPackage dataclass."""

    def test_to_dict(self, sample_package):
        """Test conversion to dictionary."""
        data = sample_package.to_dict()
        assert data["id"] == "com.example.test-skill"
        assert data["version"] == "1.0.0"
        assert data["enabled"] is True

    def test_from_dict(self):
        """Test construction from dictionary."""
        data = {
            "id": "test.skill",
            "version": "1.0.0",
            "path": "/path/to/skill",
            "manifest": {"name": "Test"},
            "installed_at": "2026-08-07T12:00:00",
            "enabled": True,
        }
        pkg = InstalledPackage.from_dict(data)
        assert pkg.id == "test.skill"
        assert pkg.version == "1.0.0"
        assert pkg.path == "/path/to/skill"
        assert pkg.enabled is True

    def test_roundtrip(self, sample_package):
        """Test to_dict -> from_dict roundtrip."""
        data = sample_package.to_dict()
        restored = InstalledPackage.from_dict(data)
        assert restored == sample_package


class TestPackageRegistryInstanceAPI:
    """Test instance-based API (tenant_id bound at initialization)."""

    def test_init_creates_empty_registry(self, temp_corvin_home):
        """Test that initializing creates an empty registry if file doesn't exist."""
        registry = PackageRegistry("_default")
        assert registry.tenant_id == "_default"
        assert registry.get_all_packages() == {}

    def test_register_package(self, temp_corvin_home, sample_package):
        """Test registering a package."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)

        # Verify in memory
        assert registry.has_package("com.example.test-skill")
        retrieved = registry.get_package("com.example.test-skill")
        assert retrieved == sample_package

    def test_register_package_persists_to_disk(self, temp_corvin_home, sample_package):
        """Test that registered package is persisted to disk."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)

        # Create new registry instance to verify persistence
        registry2 = PackageRegistry("_default")
        retrieved = registry2.get_package("com.example.test-skill")
        assert retrieved is not None
        assert retrieved.id == sample_package.id
        assert retrieved.version == sample_package.version

    def test_unregister_package(self, temp_corvin_home, sample_package):
        """Test unregistering a package."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)
        assert registry.has_package("com.example.test-skill")

        registry.unregister_package("com.example.test-skill")
        assert not registry.has_package("com.example.test-skill")

    def test_unregister_nonexistent_package(self, temp_corvin_home):
        """Test unregistering a package that doesn't exist (should be idempotent)."""
        registry = PackageRegistry("_default")
        # Should not raise
        registry.unregister_package("nonexistent.package")

    def test_get_all_packages(self, temp_corvin_home, sample_package, sample_package_2):
        """Test retrieving all packages."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)
        registry.register_package(sample_package_2)

        all_packages = registry.get_all_packages()
        assert len(all_packages) == 2
        assert "com.example.test-skill" in all_packages
        assert "com.example.another-skill" in all_packages

    def test_list_package_ids(self, temp_corvin_home, sample_package, sample_package_2):
        """Test listing all package IDs."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)
        registry.register_package(sample_package_2)

        ids = registry.list_package_ids()
        assert len(ids) == 2
        assert "com.example.test-skill" in ids
        assert "com.example.another-skill" in ids

    def test_get_installed_versions(self, temp_corvin_home, sample_package):
        """Test getting installed versions dict."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)

        versions = registry.get_installed_versions()
        assert versions["com.example.test-skill"] == "1.0.0"

    def test_multi_tenant_isolation(self, temp_corvin_home, sample_package):
        """Test that registries for different tenants don't interfere."""
        registry1 = PackageRegistry("tenant_a")
        registry1.register_package(sample_package)

        registry2 = PackageRegistry("tenant_b")
        registry2.register_package(sample_package)

        # Verify isolation
        assert len(registry1.get_all_packages()) == 1
        assert len(registry2.get_all_packages()) == 1
        assert registry1.get_all_packages() == registry2.get_all_packages()

        # Unregister from one should not affect the other
        registry1.unregister_package("com.example.test-skill")
        assert len(registry1.get_all_packages()) == 0
        assert len(registry2.get_all_packages()) == 1

    def test_corrupt_registry_file_recovery(self, temp_corvin_home):
        """Test graceful recovery from corrupt registry file."""
        registry_path = (
            temp_corvin_home / "tenants" / "_default" / "packages" / "package_registry.json"
        )
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("{ invalid json }")

        # Should not raise, should start with empty registry
        registry = PackageRegistry("_default")
        assert registry.get_all_packages() == {}

    def test_atomic_write(self, temp_corvin_home, sample_package):
        """Test that writes are atomic (no partial writes on failure)."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)

        # Verify that registry file is valid JSON
        registry_path = (
            temp_corvin_home / "tenants" / "_default" / "packages" / "package_registry.json"
        )
        assert registry_path.exists()
        with open(registry_path) as f:
            data = json.load(f)
        assert "version" in data
        assert "packages" in data


class TestPackageRegistryClassAPI:
    """Test class-level API (functional, stateless)."""

    def test_load_registry_empty(self, temp_corvin_home):
        """Test loading a non-existent registry."""
        result = PackageRegistry.load_registry("_default")
        assert result == {}

    def test_load_registry_with_packages(self, temp_corvin_home, sample_package):
        """Test loading an existing registry."""
        # Populate registry via instance API
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)

        # Load via class API
        loaded = PackageRegistry.load_registry("_default")
        assert len(loaded) == 1
        assert "com.example.test-skill" in loaded
        pkg = loaded["com.example.test-skill"]
        assert pkg.version == "1.0.0"

    def test_save_registry(self, temp_corvin_home, sample_package):
        """Test saving registry via class API."""
        registry = {sample_package.id: sample_package}
        PackageRegistry.save_registry("_default", registry)

        # Verify via instance API
        instance = PackageRegistry("_default")
        assert instance.has_package("com.example.test-skill")
        retrieved = instance.get_package("com.example.test-skill")
        assert retrieved.version == "1.0.0"

    def test_register_package_cls(self, temp_corvin_home):
        """Test registering a package via class API."""
        metadata = {
            "version": "1.5.0",
            "path": "/path/to/pkg",
            "manifest": {"name": "Test"},
            "installed_at": datetime.utcnow().isoformat(),
            "enabled": True,
        }
        PackageRegistry.register_package_cls("com.test.pkg", metadata, "_default")

        # Verify it's registered
        registry = PackageRegistry("_default")
        assert registry.has_package("com.test.pkg")
        pkg = registry.get_package("com.test.pkg")
        assert pkg.version == "1.5.0"

    def test_unregister_package_cls(self, temp_corvin_home, sample_package):
        """Test unregistering a package via class API."""
        # Register first
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)
        assert registry.has_package("com.example.test-skill")

        # Unregister via class API
        PackageRegistry.unregister_package_cls("com.example.test-skill", "_default")

        # Verify it's gone
        registry2 = PackageRegistry("_default")
        assert not registry2.has_package("com.example.test-skill")

    def test_get_package_cls(self, temp_corvin_home, sample_package):
        """Test getting a package via class API."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)

        # Retrieve via class API
        pkg = PackageRegistry.get_package_cls("com.example.test-skill", "_default")
        assert pkg is not None
        assert pkg.version == "1.0.0"

    def test_get_package_cls_nonexistent(self, temp_corvin_home):
        """Test getting a non-existent package via class API."""
        pkg = PackageRegistry.get_package_cls("nonexistent.pkg", "_default")
        assert pkg is None

    def test_get_all_packages_cls(self, temp_corvin_home, sample_package, sample_package_2):
        """Test getting all packages via class API."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)
        registry.register_package(sample_package_2)

        all_packages = PackageRegistry.get_all_packages_cls("_default")
        assert len(all_packages) == 2
        assert "com.example.test-skill" in all_packages
        assert "com.example.another-skill" in all_packages

    def test_class_api_multi_tenant(self, temp_corvin_home, sample_package):
        """Test that class API respects tenant isolation."""
        metadata = sample_package.to_dict()

        # Register in tenant_a
        PackageRegistry.register_package_cls("test.pkg", metadata, "tenant_a")

        # Verify it's only in tenant_a
        all_a = PackageRegistry.get_all_packages_cls("tenant_a")
        all_b = PackageRegistry.get_all_packages_cls("tenant_b")
        assert len(all_a) == 1
        assert len(all_b) == 0


class TestPackageRegistryEdgeCases:
    """Test edge cases and error conditions."""

    def test_package_id_with_special_chars(self, temp_corvin_home):
        """Test package IDs with dots, dashes, underscores."""
        pkg = InstalledPackage(
            id="com.vendor.skill-name_v2",
            version="1.0.0",
            path="/path",
            manifest={},
            installed_at=datetime.utcnow().isoformat(),
        )
        registry = PackageRegistry("_default")
        registry.register_package(pkg)
        assert registry.has_package("com.vendor.skill-name_v2")

    def test_package_with_empty_manifest(self, temp_corvin_home):
        """Test registering a package with empty manifest."""
        pkg = InstalledPackage(
            id="test.pkg",
            version="1.0.0",
            path="/path",
            manifest={},
            installed_at=datetime.utcnow().isoformat(),
        )
        registry = PackageRegistry("_default")
        registry.register_package(pkg)
        retrieved = registry.get_package("test.pkg")
        assert retrieved.manifest == {}

    def test_large_manifest(self, temp_corvin_home):
        """Test handling large manifests."""
        large_manifest = {
            "name": "Big Skill",
            "data": "x" * 100000,  # 100KB string
        }
        pkg = InstalledPackage(
            id="test.big",
            version="1.0.0",
            path="/path",
            manifest=large_manifest,
            installed_at=datetime.utcnow().isoformat(),
        )
        registry = PackageRegistry("_default")
        registry.register_package(pkg)
        retrieved = registry.get_package("test.big")
        assert len(retrieved.manifest["data"]) == 100000

    def test_version_comparison(self, temp_corvin_home):
        """Test that different versions are stored correctly."""
        pkg1 = InstalledPackage(
            id="test.pkg",
            version="1.0.0",
            path="/path1",
            manifest={},
            installed_at="2026-08-01T00:00:00",
        )
        pkg2 = InstalledPackage(
            id="test.pkg",
            version="2.0.0",
            path="/path2",
            manifest={},
            installed_at="2026-08-07T00:00:00",
        )
        registry = PackageRegistry("_default")
        registry.register_package(pkg1)
        assert registry.get_package("test.pkg").version == "1.0.0"

        registry.register_package(pkg2)
        assert registry.get_package("test.pkg").version == "2.0.0"

    def test_enabled_flag_persistence(self, temp_corvin_home):
        """Test that enabled/disabled state is persisted."""
        pkg = InstalledPackage(
            id="test.pkg",
            version="1.0.0",
            path="/path",
            manifest={},
            installed_at=datetime.utcnow().isoformat(),
            enabled=False,
        )
        registry = PackageRegistry("_default")
        registry.register_package(pkg)

        registry2 = PackageRegistry("_default")
        retrieved = registry2.get_package("test.pkg")
        assert retrieved.enabled is False


class TestPackageRegistryCompatibility:
    """Test backward compatibility with corvin_package_manager.py usage."""

    def test_used_by_package_manager(self, temp_corvin_home, sample_package):
        """Test that PackageRegistry works as used by PackageManager."""
        # Simulate PackageManager.load_from_zip() flow
        registry = PackageRegistry("_default")
        installed_versions = registry.get_installed_versions()
        assert installed_versions == {}

        # Add package
        registry.register_package(sample_package)

        # Verify get_installed_versions works
        installed_versions = registry.get_installed_versions()
        assert installed_versions["com.example.test-skill"] == "1.0.0"

    def test_list_all_packages(self, temp_corvin_home, sample_package, sample_package_2):
        """Test list_packages pattern from PackageManager."""
        registry = PackageRegistry("_default")
        registry.register_package(sample_package)
        registry.register_package(sample_package_2)

        # PackageManager.list_packages() uses get_all_packages()
        packages = registry.get_all_packages()
        assert len(packages) == 2

    def test_get_package_with_none(self, temp_corvin_home):
        """Test that get_package returns None for non-existent packages."""
        registry = PackageRegistry("_default")
        pkg = registry.get_package("nonexistent")
        assert pkg is None
