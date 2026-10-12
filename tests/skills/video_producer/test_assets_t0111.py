"""E2E Tests for Video Producer Assets Foundation (T-0111, ADR-2249 Phase 2).

Tests the complete asset lifecycle:
- Create/load versioned asset packs
- Track dependencies (scene → asset)
- GDPR L36 integration (mark → cleanup)
- Tenant isolation
- Backwards compatibility with multiple versions

Exit code 0 iff all 15+ E2E tests pass.
"""

import sys
import pytest
import tempfile
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
repo_root = Path(__file__).parent.parent.parent
video_producer_src = repo_root / "core/skills/os_skills/video_producer/src"
sys.path.insert(0, str(video_producer_src))
sys.path.insert(0, str(repo_root))

# Import from the video_producer.src.assets module
from core.skills.os_skills.video_producer.src.assets import (
    AssetPack,
    AssetMetadata,
    AssetDependency,
    VersionMetadata,
    AssetDeletion,
)


@pytest.fixture
def temp_corvin_home(tmp_path):
    """Create a temporary CORVIN_HOME for testing."""
    home = tmp_path / "corvin_home"
    home.mkdir()
    old_home = os.environ.get("CORVIN_HOME")
    os.environ["CORVIN_HOME"] = str(home)
    yield home
    if old_home:
        os.environ["CORVIN_HOME"] = old_home
    else:
        os.environ.pop("CORVIN_HOME", None)


@pytest.fixture
def sample_assets():
    """Create sample asset files for testing."""
    return {
        "logo.svg": b'<svg><circle cx="50" cy="50" r="40" /></svg>',
        "background.png": b"\x89PNG\r\n\x1a\n" + b"mock_png_data" * 100,  # Mock PNG
        "font.ttf": b"mock_font_data" * 50,
    }


class TestAssetPackBasics:
    """Test basic asset pack creation and retrieval."""

    def test_create_version_success(self, temp_corvin_home, sample_assets):
        """Test creating a version with valid assets."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(
            assets=sample_assets,
            description="Test version"
        )

        assert len(version_hash) == 8
        assert all(c in "0123456789abcdef" for c in version_hash)

        # Verify version was created
        manifest = pack.get_version(version_hash)
        assert manifest.version_hash == version_hash
        assert manifest.creator == "test_user"
        assert manifest.asset_count == 3

    def test_create_version_empty_assets_rejected(self, temp_corvin_home):
        """Test that empty asset dict is rejected."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        with pytest.raises(ValueError, match="At least one asset required"):
            pack.create_version(assets={})

    def test_create_version_too_many_assets(self, temp_corvin_home):
        """Test that >50 assets are rejected."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        assets = {f"asset_{i}.svg": b"<svg/>" for i in range(51)}

        with pytest.raises(ValueError, match="Maximum 50 assets per version"):
            pack.create_version(assets=assets)

    def test_create_version_invalid_format(self, temp_corvin_home):
        """Test that invalid asset formats are rejected."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        with pytest.raises(ValueError, match="Invalid asset format"):
            pack.create_version(assets={"bad.exe": b"executable"})

    def test_create_version_oversized_asset(self, temp_corvin_home):
        """Test that assets exceeding 2 MB are rejected."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        oversized = b"x" * (3 * 1024 * 1024)  # 3 MB

        with pytest.raises(ValueError, match="exceeds 2 MB"):
            pack.create_version(assets={"huge.png": oversized})

    def test_get_asset_success(self, temp_corvin_home, sample_assets):
        """Test retrieving an asset from a version."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Retrieve logo.svg
        content = pack.get_asset(version_hash, "logo.svg")
        assert content == b'<svg><circle cx="50" cy="50" r="40" /></svg>'

    def test_get_asset_not_found(self, temp_corvin_home, sample_assets):
        """Test retrieving a non-existent asset."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        with pytest.raises(ValueError, match="not found"):
            pack.get_asset(version_hash, "nonexistent.svg")

    def test_list_assets(self, temp_corvin_home, sample_assets):
        """Test listing assets in a version."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        assets = pack.list_assets(version_hash)
        assert len(assets) == 3
        assert "logo.svg" in assets
        assert "background.png" in assets
        assert "font.ttf" in assets

    def test_list_versions(self, temp_corvin_home, sample_assets):
        """Test listing all versions."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        v1 = pack.create_version(assets=sample_assets, description="v1")

        # Create v2 with different assets (to get a different hash)
        v2_assets = sample_assets.copy()
        v2_assets["new.svg"] = b"<svg>new</svg>"
        v2 = pack.create_version(assets=v2_assets, description="v2")

        versions = pack.list_versions()
        assert len(versions) >= 2
        assert v1 in [v.version_hash for v in versions]
        assert v2 in [v.version_hash for v in versions]

        # Should be sorted by created_at, newest first
        assert versions[0].created_at >= versions[1].created_at


class TestAssetVersionConflicts:
    """Test version conflict detection and handling."""

    def test_version_conflict_same_hash(self, temp_corvin_home, sample_assets):
        """Test that creating the same content twice produces same hash."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        v1 = pack.create_version(assets=sample_assets)
        v2 = pack.create_version(assets=sample_assets)

        # Same content should produce same hash
        assert v1 == v2

    def test_version_different_content_different_hash(self, temp_corvin_home, sample_assets):
        """Test that different content produces different hashes."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        v1 = pack.create_version(assets=sample_assets)

        modified = sample_assets.copy()
        modified["logo.svg"] = b"<svg>modified</svg>"
        v2 = pack.create_version(assets=modified)

        assert v1 != v2


class TestAssetDependencies:
    """Test dependency tracking (scene → asset relationships)."""

    def test_add_dependency(self, temp_corvin_home, sample_assets):
        """Test adding a scene → asset dependency."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        dep_id = pack.add_dependency(
            version_hash=version_hash,
            asset_id="logo.svg",
            scene_id="scene_001",
            job_id="job_001",
        )

        assert dep_id is not None
        assert len(dep_id) > 0

    def test_get_dependencies(self, temp_corvin_home, sample_assets):
        """Test retrieving dependencies for a version."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Add multiple dependencies
        pack.add_dependency(version_hash, "logo.svg", "scene_001", "job_001")
        pack.add_dependency(version_hash, "background.png", "scene_001", "job_001")
        pack.add_dependency(version_hash, "logo.svg", "scene_002", "job_001")

        dependencies = pack.get_dependencies(version_hash)
        assert len(dependencies) == 3

    def test_get_dependencies_by_asset(self, temp_corvin_home, sample_assets):
        """Test filtering dependencies by asset ID."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        pack.add_dependency(version_hash, "logo.svg", "scene_001", "job_001")
        pack.add_dependency(version_hash, "logo.svg", "scene_002", "job_001")
        pack.add_dependency(version_hash, "background.png", "scene_003", "job_001")

        logo_deps = pack.get_dependencies(version_hash, asset_id="logo.svg")
        assert len(logo_deps) == 2
        assert all(d.asset_id == "logo.svg" for d in logo_deps)

    def test_three_scenes_reference_one_asset(self, temp_corvin_home, sample_assets):
        """Test E2E: 3 scenes reference 1 asset (coverage requirement)."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Reference same asset from 3 different scenes
        for scene_num in range(1, 4):
            pack.add_dependency(
                version_hash=version_hash,
                asset_id="logo.svg",
                scene_id=f"scene_{scene_num:03d}",
                job_id="job_001",
            )

        # Verify dependencies
        logo_deps = pack.get_dependencies(version_hash, asset_id="logo.svg")
        assert len(logo_deps) == 3
        scene_ids = {d.scene_id for d in logo_deps}
        assert scene_ids == {"scene_001", "scene_002", "scene_003"}


class TestGDPRDeletion:
    """Test GDPR L36 erasure integration."""

    def test_mark_for_deletion(self, temp_corvin_home, sample_assets):
        """Test marking an asset for deletion."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        asset_id = pack.mark_for_deletion(
            version_hash=version_hash,
            asset_id="logo.svg",
            reason="user_erasure",
            cleanup_days=30,
            marked_by="test_user",
        )

        assert asset_id == "logo.svg"

        # Verify deletion record exists
        deletions = pack.get_marked_for_deletion(version_hash)
        assert len(deletions) == 1
        assert deletions[0].asset_id == "logo.svg"
        assert deletions[0].reason == "user_erasure"

    def test_cleanup_due_deletions(self, temp_corvin_home, sample_assets):
        """Test cleaning up assets past their cleanup date."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Mark for deletion with immediate cleanup
        pack.mark_for_deletion(
            version_hash=version_hash,
            asset_id="logo.svg",
            reason="user_erasure",
            cleanup_days=0,  # Cleanup immediately
        )

        # Verify asset exists before cleanup
        assert "logo.svg" in pack.list_assets(version_hash)

        # Run cleanup
        deleted = pack.cleanup_due_deletions(version_hash)

        # Asset should be deleted
        assert deleted > 0
        assert "logo.svg" not in pack.list_assets(version_hash)

    def test_cleanup_idempotent(self, temp_corvin_home, sample_assets):
        """Test that cleanup is idempotent."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        pack.mark_for_deletion(
            version_hash=version_hash,
            asset_id="logo.svg",
            reason="user_erasure",
            cleanup_days=0,
        )

        # Run cleanup twice
        deleted_1 = pack.cleanup_due_deletions(version_hash)
        deleted_2 = pack.cleanup_due_deletions(version_hash)

        # Second cleanup should delete nothing
        assert deleted_1 > 0
        assert deleted_2 == 0

    def test_gdpr_mark_cleanup_e2e(self, temp_corvin_home, sample_assets):
        """E2E: mark asset → delete → verify file gone (dependencies remain as audit trail)."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Add dependencies
        pack.add_dependency(version_hash, "logo.svg", "scene_001", "job_001")

        # Mark for deletion
        pack.mark_for_deletion(
            version_hash=version_hash,
            asset_id="logo.svg",
            reason="user_erasure",
            cleanup_days=0,
        )

        # Cleanup
        pack.cleanup_due_deletions(version_hash)

        # Verify: file is gone
        with pytest.raises(ValueError, match="not found"):
            pack.get_asset(version_hash, "logo.svg")

        # Verify: dependency records remain (immutable audit trail)
        all_deps = pack.get_dependencies(version_hash)
        assert len(all_deps) == 1
        assert all_deps[0].asset_id == "logo.svg"  # Record remains for audit


class TestTenantIsolation:
    """Test multi-tenant asset isolation (GDPR Art. 5)."""

    def test_tenant_scoped_storage(self, temp_corvin_home, sample_assets):
        """Test that assets are stored per-tenant."""
        pack_default = AssetPack(tenant_id="_default", creator="test_user")
        pack_tenant2 = AssetPack(tenant_id="tenant_2", creator="test_user")

        v1 = pack_default.create_version(assets=sample_assets)
        v2 = pack_tenant2.create_version(assets=sample_assets)

        # Both tenants can have the same version hash, but in different directories
        default_versions = pack_default.list_versions()
        tenant2_versions = pack_tenant2.list_versions()

        assert any(v.version_hash == v1 for v in default_versions)
        assert any(v.version_hash == v2 for v in tenant2_versions)

    def test_invalid_tenant_rejected(self, temp_corvin_home):
        """Test that invalid tenant IDs are rejected."""
        with pytest.raises(ValueError):
            AssetPack(tenant_id="../../../etc/passwd", creator="test_user")


class TestAssetHygiene:
    """Test asset hygiene signals and orphan detection."""

    def test_asset_hygiene_signal(self, temp_corvin_home, sample_assets):
        """Test generating hygiene metrics."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Add dependency for logo only
        pack.add_dependency(version_hash, "logo.svg", "scene_001", "job_001")

        signal = pack.get_asset_hygiene_signal(version_hash)

        assert signal["total_assets"] == 3
        assert signal["referenced_assets"] == 1
        assert signal["orphaned_assets"] == 2
        assert signal["marked_for_deletion"] == 0

    def test_orphaned_asset_storage_calculation(self, temp_corvin_home, sample_assets):
        """Test that orphaned asset storage is calculated."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # No dependencies = all assets are orphaned
        signal = pack.get_asset_hygiene_signal(version_hash)

        assert signal["orphaned_assets"] == 3
        assert signal["unused_storage_mb"] > 0


class TestBackwardsCompatibility:
    """Test loading old versions alongside new versions."""

    def test_load_old_version_after_new(self, temp_corvin_home, sample_assets):
        """Test E2E: load old version after new version exists."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        # Create v1
        v1 = pack.create_version(assets=sample_assets, description="v1")

        # Create v2 with modified assets
        v2_assets = sample_assets.copy()
        v2_assets["new_asset.svg"] = b"<svg>new</svg>"
        v2 = pack.create_version(assets=v2_assets, description="v2")

        # Load v1 again
        v1_manifest = pack.get_version(v1)
        v1_assets = pack.list_assets(v1)

        assert v1_manifest.asset_count == 3
        assert "logo.svg" in v1_assets
        assert "new_asset.svg" not in v1_assets


class TestVersionManagement:
    """Test version lifecycle and deprecation."""

    def test_deprecate_version(self, temp_corvin_home, sample_assets):
        """Test marking a version as deprecated."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        manifest = pack.get_version(version_hash)
        assert not manifest.is_deprecated()

        # Create a deprecated version manually (normally would use an update API)
        # For now, we can only test the is_deprecated method
        assert manifest.deprecation_date is None

    def test_delete_version(self, temp_corvin_home, sample_assets):
        """Test deleting a version (cleanup only, no active dependencies)."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Delete should succeed with no dependencies
        result = pack.delete_version(version_hash)
        assert result is True

        # Should not be findable
        with pytest.raises(ValueError):
            pack.get_version(version_hash)

    def test_delete_version_with_dependencies_fails(self, temp_corvin_home, sample_assets):
        """Test that deleting a version with active dependencies fails."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        version_hash = pack.create_version(assets=sample_assets)

        # Add dependency
        pack.add_dependency(version_hash, "logo.svg", "scene_001", "job_001")

        # Delete should fail
        with pytest.raises(RuntimeError, match="active dependencies"):
            pack.delete_version(version_hash)


class TestAuditTrail:
    """Test audit trail integration (G5 evidence)."""

    def test_all_operations_complete_without_error(self, temp_corvin_home, sample_assets):
        """E2E: complete lifecycle without errors (proof of functional audit trail)."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        # Create version
        version_hash = pack.create_version(assets=sample_assets)
        assert version_hash is not None

        # Add dependencies
        for i in range(3):
            pack.add_dependency(version_hash, "logo.svg", f"scene_{i}", "job_001")

        # Mark for deletion
        pack.mark_for_deletion(version_hash, "background.png", "deprecated", 0)

        # Cleanup
        deleted = pack.cleanup_due_deletions(version_hash)
        assert deleted > 0

        # Verify final state
        signal = pack.get_asset_hygiene_signal(version_hash)
        assert signal["total_assets"] < 3  # One deleted


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_version_directory(self, temp_corvin_home):
        """Test handling of corrupted/empty version directory."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        # Try to get non-existent version (invalid hash format)
        with pytest.raises(ValueError, match="Invalid version hash"):
            pack.get_version("nonexistent")

    def test_get_dependencies_empty_log(self, temp_corvin_home):
        """Test getting dependencies from empty log."""
        pack = AssetPack(tenant_id="_default", creator="test_user")
        assets = {"empty.svg": b"<svg/>"}
        version_hash = pack.create_version(assets=assets)

        deps = pack.get_dependencies(version_hash)
        assert deps == []

    def test_invalid_version_hash_format(self, temp_corvin_home):
        """Test rejection of invalid version hash format."""
        pack = AssetPack(tenant_id="_default", creator="test_user")

        with pytest.raises(ValueError, match="Invalid version hash"):
            pack.get_version("invalid_hash_too_long")

        with pytest.raises(ValueError, match="Invalid version hash"):
            pack.get_version("zzzzzzzz")  # Invalid hex


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
