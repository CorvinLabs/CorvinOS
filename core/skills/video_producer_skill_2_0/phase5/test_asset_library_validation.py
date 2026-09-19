"""Test Suite for Asset Library Validation Framework

Tests comprehensive validation of asset manifests, cross-references,
integrity checks, cache invalidation, and dependency resolution.

ADR-0740, ADR-0741, ADR-0742: Phase 5 Validation Framework
Coverage Target: >80%
Test Count: 25+ assertions
"""

import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime
from asset_library import (
    AssetMetadata,
    AssetLibraryManifest,
    ValidationResult,
    ValidationError,
    AssetIntegrityError,
    AssetNotFoundError,
)


@pytest.fixture
def temp_dir():
    """Create temporary directory for test assets"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def manifest_path(temp_dir):
    """Create manifest path"""
    return temp_dir / "manifest.json"


@pytest.fixture
def asset_base_path(temp_dir):
    """Create asset base directory"""
    assets_dir = temp_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    return assets_dir


@pytest.fixture
def sample_manifest(manifest_path, asset_base_path):
    """Create a sample manifest with test assets"""
    manifest = AssetLibraryManifest(manifest_path)

    # Add valid asset 1: learning-loop
    asset1 = AssetMetadata(
        id="learning-loop",
        version="1.0",
        name="Learning Loop Diagram",
        description="5-step feedback loop animation",
        asset_type="manim-scene",
        checksum_sha256="abc123def456789abc123def456789abc123def456789abc123def456789abcd",
        didactic_level=["beginner", "technical"],
        duration_seconds=30,
        file_path="scenes/learning_loop_scene.py",
        license="MIT",
        created_at="2026-09-14T13:00:00Z",
        created_by="video-producer-skill",
        renderer_tier=2
    )
    manifest.add_asset(asset1)

    # Add valid asset 2: maestro-workers
    asset2 = AssetMetadata(
        id="maestro-workers",
        version="1.0",
        name="Maestro with 5 Workers",
        description="Hierarchical diagram showing Maestro orchestrator",
        asset_type="manim-scene",
        checksum_sha256="def456789abc123def456789abc123def456789abc123def456789abcd12345678",
        didactic_level=["technical"],
        duration_seconds=30,
        file_path="scenes/maestro_workers_scene.py",
        license="MIT",
        created_at="2026-09-14T13:00:00Z",
        created_by="video-producer-skill",
        renderer_tier=2
    )
    manifest.add_asset(asset2)

    # Add asset with dependencies
    asset3 = AssetMetadata(
        id="audit-chain",
        version="1.0",
        name="Audit Chain",
        description="Hash-linked events",
        asset_type="manim-scene",
        checksum_sha256="789abc123def456789abc123def456789abc123def456789abc123def456789ab",
        didactic_level=["technical"],
        duration_seconds=30,
        file_path="scenes/audit_chain_scene.py",
        license="MIT",
        created_at="2026-09-14T13:00:00Z",
        created_by="video-producer-skill",
        renderer_tier=2,
        dependencies=["learning-loop"]
    )
    manifest.add_asset(asset3)

    manifest.save_manifest()
    return manifest


@pytest.fixture
def sample_asset_files(asset_base_path):
    """Create actual asset files with correct SHA256 hashes"""
    import hashlib

    files_and_hashes = {
        "scenes/learning_loop_scene.py": b"# Learning loop animation code\nprint('learning loop')",
        "scenes/maestro_workers_scene.py": b"# Maestro workers animation\nprint('maestro')",
        "scenes/audit_chain_scene.py": b"# Audit chain visualization\nprint('audit chain')",
    }

    created_files = {}
    for file_path, content in files_and_hashes.items():
        full_path = asset_base_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        # Calculate actual SHA256
        sha = hashlib.sha256()
        sha.update(content)
        created_files[file_path] = sha.hexdigest()

    return created_files, asset_base_path


# ============================================================================
# Asset Metadata Schema Validation Tests
# ============================================================================

class TestAssetMetadataValidation:
    """Test AssetMetadata schema validation"""

    def test_valid_asset_schema(self):
        """Test validation of asset with all required fields"""
        asset = AssetMetadata(
            id="test-asset",
            version="1.0",
            name="Test Asset",
            description="Test description",
            asset_type="manim-scene",
            checksum_sha256="a" * 64,  # Valid 64-char hex
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test-user",
            renderer_tier=2
        )

        valid, errors = asset.validate_schema()
        assert valid, f"Should be valid, but got errors: {errors}"
        assert len(errors) == 0

    def test_missing_id(self):
        """Test validation fails with missing id"""
        asset = AssetMetadata(
            id="",  # Empty id
            version="1.0",
            name="Test",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256="a" * 64,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2
        )

        valid, errors = asset.validate_schema()
        assert not valid
        assert any("id is required" in e for e in errors)

    def test_invalid_checksum_format(self):
        """Test validation fails with invalid SHA256 checksum"""
        asset = AssetMetadata(
            id="test",
            version="1.0",
            name="Test",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256="tooshort",  # Invalid — not 64 chars
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2
        )

        valid, errors = asset.validate_schema()
        assert not valid
        assert any("Invalid checksum format" in e for e in errors)

    def test_invalid_renderer_tier(self):
        """Test validation fails with invalid renderer tier"""
        asset = AssetMetadata(
            id="test",
            version="1.0",
            name="Test",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256="a" * 64,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=5  # Invalid — must be 1, 2, or 3
        )

        valid, errors = asset.validate_schema()
        assert not valid
        assert any("Invalid renderer_tier" in e for e in errors)


# ============================================================================
# Manifest Validation Tests
# ============================================================================

class TestManifestValidation:
    """Test AssetLibraryManifest validation"""

    def test_validate_empty_manifest(self):
        """Test validation of empty manifest"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = AssetLibraryManifest(Path(tmpdir) / "manifest.json")
            result = manifest.validate_manifest()

            assert result.valid  # Empty manifest is valid (warning issued)
            assert len(result.warnings) > 0
            assert any("no assets" in w for w in result.warnings)

    def test_validate_manifest_schema_only(self, sample_manifest):
        """Test manifest validation (schema only, no file checks)"""
        result = sample_manifest.validate_manifest()

        assert result.valid
        assert len(result.errors) == 0

    def test_validate_manifest_with_file_checks(self, sample_manifest, sample_asset_files):
        """Test manifest validation with file existence and integrity checks"""
        created_files, asset_base_path = sample_asset_files

        # Update manifest with actual hashes
        for asset in sample_manifest.assets.values():
            if asset.file_path in created_files:
                asset.checksum_sha256 = created_files[asset.file_path]

        result = sample_manifest.validate_manifest(asset_base_path)
        assert result.valid, f"Validation should pass, got errors: {result.errors}"

    def test_validate_missing_asset_file(self, sample_manifest, temp_dir):
        """Test validation detects missing asset files"""
        # Don't create asset files
        result = sample_manifest.validate_manifest(temp_dir / "nonexistent")

        assert not result.valid
        assert len(result.errors) > 0
        assert any("not found" in e for e in result.errors)

    def test_validate_checksum_mismatch(self, sample_manifest, asset_base_path):
        """Test validation detects SHA256 checksum mismatches"""
        # Create asset file with wrong content
        scenes_dir = asset_base_path / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        (scenes_dir / "learning_loop_scene.py").write_text("WRONG CONTENT")

        result = sample_manifest.validate_manifest(asset_base_path)
        assert not result.valid
        assert any("integrity check failed" in e or "checksum" in e for e in result.errors)


# ============================================================================
# Scene Asset Cross-Reference Tests
# ============================================================================

class TestSceneAssetValidation:
    """Test scene-to-asset cross-reference validation"""

    def test_validate_scene_with_valid_assets(self, sample_manifest):
        """Test scene validation with valid asset references"""
        scene_spec = {
            "asset_id": "learning-loop"
        }

        result = sample_manifest.validate_scene_assets(scene_spec)
        assert result.valid
        assert len(result.errors) == 0

    def test_validate_scene_with_multiple_assets(self, sample_manifest):
        """Test scene validation with multiple asset references"""
        scene_spec = {
            "asset_ids": ["learning-loop", "maestro-workers"]
        }

        result = sample_manifest.validate_scene_assets(scene_spec)
        assert result.valid

    def test_validate_scene_missing_asset(self, sample_manifest):
        """Test scene validation fails with missing asset reference"""
        scene_spec = {
            "asset_id": "nonexistent-asset"
        }

        with pytest.raises(AssetNotFoundError):
            sample_manifest.validate_scene_assets(scene_spec)

    def test_validate_scene_no_asset_references(self, sample_manifest):
        """Test scene validation with no asset references"""
        scene_spec = {
            "name": "Scene without assets"
        }

        result = sample_manifest.validate_scene_assets(scene_spec)
        assert result.valid  # Valid, but warning issued
        assert len(result.warnings) > 0

    def test_validate_scene_with_file_checks(self, sample_manifest, sample_asset_files):
        """Test scene validation with file existence checks"""
        created_files, asset_base_path = sample_asset_files

        # Update manifest with actual hashes
        for asset in sample_manifest.assets.values():
            if asset.file_path in created_files:
                asset.checksum_sha256 = created_files[asset.file_path]

        scene_spec = {
            "asset_id": "learning-loop"
        }

        result = sample_manifest.validate_scene_assets(scene_spec, asset_base_path)
        assert result.valid


# ============================================================================
# Asset Integrity and Cache Invalidation Tests
# ============================================================================

class TestAssetIntegrity:
    """Test asset integrity checks and cache invalidation"""

    def test_check_asset_integrity_success(self, sample_manifest, sample_asset_files):
        """Test successful asset integrity check"""
        created_files, asset_base_path = sample_asset_files

        # Update manifest with actual hashes
        for asset in sample_manifest.assets.values():
            if asset.file_path in created_files:
                asset.checksum_sha256 = created_files[asset.file_path]

        result = sample_manifest.check_asset_integrity("learning-loop", asset_base_path)

        assert result.valid
        # Check cache state updated
        asset = sample_manifest.get_latest_asset("learning-loop")
        assert asset.cache_valid is True
        assert asset.last_verified is not None

    def test_check_asset_integrity_missing_file(self, sample_manifest, temp_dir):
        """Test asset integrity check fails with missing file"""
        with pytest.raises(AssetIntegrityError):
            sample_manifest.check_asset_integrity(
                "learning-loop",
                temp_dir / "nonexistent"
            )

    def test_check_asset_integrity_mismatch(self, sample_manifest, asset_base_path):
        """Test asset integrity check fails with checksum mismatch"""
        # Create file with wrong content
        scenes_dir = asset_base_path / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        (scenes_dir / "learning_loop_scene.py").write_text("WRONG")

        with pytest.raises(AssetIntegrityError):
            sample_manifest.check_asset_integrity("learning-loop", asset_base_path)

    def test_invalidate_single_asset_cache(self, sample_manifest, sample_asset_files):
        """Test cache invalidation for single asset"""
        created_files, asset_base_path = sample_asset_files

        # First verify to set cache_valid = True
        for asset in sample_manifest.assets.values():
            if asset.file_path in created_files:
                asset.checksum_sha256 = created_files[asset.file_path]

        sample_manifest.check_asset_integrity("learning-loop", asset_base_path)
        asset = sample_manifest.get_latest_asset("learning-loop")
        assert asset.cache_valid is True

        # Now invalidate
        sample_manifest.invalidate_cache("learning-loop")
        assert asset.cache_valid is False
        assert asset.last_verified is None

    def test_invalidate_all_assets_cache(self, sample_manifest):
        """Test cache invalidation for all assets"""
        # Manually set cache_valid to True
        for asset in sample_manifest.assets.values():
            asset.cache_valid = True
            asset.last_verified = datetime.now().isoformat()

        # Invalidate all
        sample_manifest.invalidate_cache()

        # Check all are invalid
        for asset in sample_manifest.assets.values():
            assert asset.cache_valid is False
            assert asset.last_verified is None

    def test_get_invalid_assets(self, sample_manifest):
        """Test retrieval of invalid assets"""
        # Set some assets as invalid
        assets_list = list(sample_manifest.assets.values())
        assets_list[0].cache_valid = False
        assets_list[1].cache_valid = True

        invalid = sample_manifest.get_invalid_assets()
        assert len(invalid) == 1
        assert invalid[0].id == assets_list[0].id


# ============================================================================
# Dependency Validation Tests
# ============================================================================

class TestDependencyValidation:
    """Test asset dependency validation"""

    def test_validate_asset_with_no_dependencies(self, sample_manifest):
        """Test validation of asset with no dependencies"""
        result = sample_manifest.dependency_validation("learning-loop")
        assert result.valid

    def test_validate_asset_with_valid_dependencies(self, sample_manifest):
        """Test validation of asset with valid dependencies"""
        result = sample_manifest.dependency_validation("audit-chain")
        assert result.valid

    def test_validate_asset_missing_dependency(self, sample_manifest):
        """Test validation detects missing dependencies"""
        # Manually add asset with missing dependency
        asset = AssetMetadata(
            id="broken-asset",
            version="1.0",
            name="Broken",
            description="Has missing dependency",
            asset_type="manim-scene",
            checksum_sha256="a" * 64,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2,
            dependencies=["nonexistent-asset"]
        )
        sample_manifest.add_asset(asset)

        result = sample_manifest.dependency_validation("broken-asset")
        assert not result.valid
        assert any("not found" in e for e in result.errors)

    def test_validate_circular_dependencies(self, sample_manifest):
        """Test detection of circular dependencies"""
        # Create circular dependency: asset A → B → A
        asset_a = AssetMetadata(
            id="circular-a",
            version="1.0",
            name="Circular A",
            description="Has circular dependency",
            asset_type="manim-scene",
            checksum_sha256="a" * 64,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2,
            dependencies=["circular-b"]
        )

        asset_b = AssetMetadata(
            id="circular-b",
            version="1.0",
            name="Circular B",
            description="Part of circular",
            asset_type="manim-scene",
            checksum_sha256="b" * 64,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2,
            dependencies=["circular-a"]  # Back to A — circular!
        )

        sample_manifest.add_asset(asset_a)
        sample_manifest.add_asset(asset_b)

        result = sample_manifest.dependency_validation("circular-a")
        assert not result.valid
        assert any("circular" in e.lower() for e in result.errors)


# ============================================================================
# Integration Tests
# ============================================================================

class TestAssetLibraryIntegration:
    """Integration tests for asset library validation"""

    def test_end_to_end_manifest_loading_and_validation(self):
        """Test complete flow: load manifest, validate, check integrity"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            manifest_path = tmpdir / "manifest.json"
            asset_dir = tmpdir / "assets"
            asset_dir.mkdir()

            # Create manifest with asset
            manifest = AssetLibraryManifest(manifest_path)

            # Create actual file
            import hashlib
            content = b"test asset content"
            sha = hashlib.sha256()
            sha.update(content)
            actual_hash = sha.hexdigest()

            asset_file = asset_dir / "test.py"
            asset_file.write_bytes(content)

            # Add to manifest
            asset = AssetMetadata(
                id="test",
                version="1.0",
                name="Test",
                description="Test",
                asset_type="manim-scene",
                checksum_sha256=actual_hash,
                didactic_level=["beginner"],
                duration_seconds=30,
                file_path="test.py",
                license="MIT",
                created_at="2026-09-14T13:00:00Z",
                created_by="test",
                renderer_tier=2
            )
            manifest.add_asset(asset)

            # Validate everything
            result = manifest.validate_manifest(asset_dir)
            assert result.valid

            integrity_result = manifest.check_asset_integrity("test", asset_dir)
            assert integrity_result.valid

            # Check cache state
            asset = manifest.get_latest_asset("test")
            assert asset.cache_valid is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
