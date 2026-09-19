#!/usr/bin/env python3
"""Integration Tests for Asset Library Validation

Complete end-to-end tests covering:
- Missing asset detection
- Cache invalidation workflows
- Manifest-disk synchronization
- Batch integrity operations
- Real-world scenarios

ADR-0740, ADR-0741, ADR-0742: Phase 5 Validation Framework
"""

import sys
import tempfile
import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from asset_library import (
    AssetMetadata,
    AssetLibraryManifest,
)
from asset_library_helpers import (
    AssetLibraryHelper,
    CacheInvalidationScheduler,
)


def test_missing_asset_detection():
    """Test detection of missing asset files

    Scenario:
    1. Create manifest with 5 assets
    2. Create only 3 asset files on disk
    3. Verify missing asset detection
    """
    print("\n[TEST] Missing Asset Detection")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        manifest = AssetLibraryManifest(tmpdir / "manifest.json")
        asset_dir = tmpdir / "assets"
        asset_dir.mkdir()

        # Create 5 assets in manifest, but only 3 on disk
        for i in range(5):
            asset = AssetMetadata(
                id=f"asset-{i}",
                version="1.0",
                name=f"Asset {i}",
                description="Test",
                asset_type="manim-scene",
                checksum_sha256="a" * 64,
                didactic_level=["beginner"],
                duration_seconds=30,
                file_path=f"scenes/asset_{i}.py",
                license="MIT",
                created_at="2026-09-14T13:00:00Z",
                created_by="test",
                renderer_tier=2
            )
            manifest.add_asset(asset)

        # Create only first 3 files
        scenes_dir = asset_dir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        for i in range(3):
            (scenes_dir / f"asset_{i}.py").write_text(f"# Asset {i}")

        # Detect missing assets
        missing = AssetLibraryHelper.detect_missing_assets(manifest, asset_dir)

        # Verify results
        assert len(missing) == 2, f"Expected 2 missing assets, got {len(missing)}"
        assert "asset-3" in missing, "asset-3 should be detected as missing"
        assert "asset-4" in missing, "asset-4 should be detected as missing"

        print("  ✅ PASS: Successfully detected 2/5 missing assets")
        return True


def test_checksum_mismatch_detection():
    """Test detection of checksum mismatches

    Scenario:
    1. Create assets with specific checksums
    2. Create files with different content
    3. Verify mismatch detection
    """
    print("\n[TEST] Checksum Mismatch Detection")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        manifest = AssetLibraryManifest(tmpdir / "manifest.json")
        asset_dir = tmpdir / "assets"
        asset_dir.mkdir()

        # Create content and calculate real hash
        content1 = b"asset 1 content"
        sha = hashlib.sha256()
        sha.update(content1)
        real_hash1 = sha.hexdigest()

        # Create asset with wrong hash
        asset = AssetMetadata(
            id="asset-1",
            version="1.0",
            name="Asset 1",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256="b" * 64,  # Wrong hash!
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="scenes/asset_1.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2
        )
        manifest.add_asset(asset)

        # Write file with correct content
        scenes_dir = asset_dir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        (scenes_dir / "asset_1.py").write_bytes(content1)

        # Detect mismatch
        mismatches = AssetLibraryHelper.detect_checksum_mismatches(manifest, asset_dir)

        assert len(mismatches) == 1, "Should detect 1 mismatch"
        assert "asset-1" in mismatches, "asset-1 should be flagged"
        assert mismatches["asset-1"]["expected"] != mismatches["asset-1"]["actual"]

        print("  ✅ PASS: Successfully detected checksum mismatch")
        return True


def test_cache_invalidation_by_age():
    """Test cache invalidation by age

    Scenario:
    1. Create assets with old verification timestamps
    2. Create assets with recent verification timestamps
    3. Apply age-based invalidation
    4. Verify correct assets are invalidated
    """
    print("\n[TEST] Cache Invalidation by Age")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        manifest = AssetLibraryManifest(tmpdir / "manifest.json")

        now = datetime.now()
        old_time = (now - timedelta(days=10)).isoformat()
        recent_time = (now - timedelta(days=1)).isoformat()

        # Create old asset (should be invalidated)
        asset_old = AssetMetadata(
            id="asset-old",
            version="1.0",
            name="Old Asset",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256="a" * 64,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2,
            cache_valid=True,
            last_verified=old_time
        )
        manifest.add_asset(asset_old)

        # Create recent asset (should NOT be invalidated)
        asset_recent = AssetMetadata(
            id="asset-recent",
            version="1.0",
            name="Recent Asset",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256="b" * 64,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2,
            cache_valid=True,
            last_verified=recent_time
        )
        manifest.add_asset(asset_recent)

        # Apply age-based invalidation (max 5 days)
        invalidated_count = CacheInvalidationScheduler.invalidate_by_age(manifest, 5)

        assert invalidated_count == 1, f"Expected 1 invalidated asset, got {invalidated_count}"
        assert not asset_old.cache_valid, "Old asset should be invalidated"
        assert asset_recent.cache_valid, "Recent asset should remain valid"

        print("  ✅ PASS: Successfully invalidated 1/2 assets by age")
        return True


def test_cache_invalidation_by_tier():
    """Test cache invalidation by renderer tier

    Scenario:
    1. Create assets for tiers 1, 2, 3
    2. Mark tier 2 as degraded
    3. Invalidate tier 2 assets
    4. Verify only tier 2 assets are affected
    """
    print("\n[TEST] Cache Invalidation by Renderer Tier")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        manifest = AssetLibraryManifest(tmpdir / "manifest.json")

        # Create assets for each tier
        for tier in [1, 2, 3]:
            for j in range(2):
                asset = AssetMetadata(
                    id=f"asset-tier{tier}-{j}",
                    version="1.0",
                    name=f"Asset Tier {tier}",
                    description="Test",
                    asset_type="manim-scene",
                    checksum_sha256=("a" * 63 + str(tier)) + str(j),
                    didactic_level=["beginner"],
                    duration_seconds=30,
                    file_path="test.py",
                    license="MIT",
                    created_at="2026-09-14T13:00:00Z",
                    created_by="test",
                    renderer_tier=tier,
                    cache_valid=True
                )
                manifest.add_asset(asset)

        # Invalidate tier 2
        tier_degradation = {1: False, 2: True, 3: False}
        invalidated_count = CacheInvalidationScheduler.invalidate_by_tier_degradation(
            manifest, tier_degradation
        )

        assert invalidated_count == 2, f"Expected 2 tier-2 assets invalidated, got {invalidated_count}"

        # Verify only tier 2 assets are invalidated
        for asset in manifest.assets.values():
            if asset.renderer_tier == 2:
                assert not asset.cache_valid, f"{asset.id} should be invalidated"
            else:
                assert asset.cache_valid, f"{asset.id} should remain valid"

        print("  ✅ PASS: Successfully invalidated 2 tier-2 assets")
        return True


def test_batch_integrity_verification():
    """Test batch integrity verification

    Scenario:
    1. Create manifest with 5 assets
    2. Create 3 with correct hashes, 1 with mismatch, 1 missing
    3. Run batch verification
    4. Verify results
    """
    print("\n[TEST] Batch Integrity Verification")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        manifest = AssetLibraryManifest(tmpdir / "manifest.json")
        asset_dir = tmpdir / "assets"
        asset_dir.mkdir()

        scenes_dir = asset_dir / "scenes"
        scenes_dir.mkdir()

        # Create assets
        for i in range(5):
            content = f"asset {i} content".encode()
            sha = hashlib.sha256()
            sha.update(content)
            real_hash = sha.hexdigest()

            if i < 3:
                # Valid assets
                hash_to_use = real_hash
                (scenes_dir / f"asset_{i}.py").write_bytes(content)
            elif i == 3:
                # Checksum mismatch
                hash_to_use = "b" * 64
                (scenes_dir / f"asset_{i}.py").write_bytes(content)
            else:
                # Missing file
                hash_to_use = real_hash

            asset = AssetMetadata(
                id=f"asset-{i}",
                version="1.0",
                name=f"Asset {i}",
                description="Test",
                asset_type="manim-scene",
                checksum_sha256=hash_to_use,
                didactic_level=["beginner"],
                duration_seconds=30,
                file_path=f"scenes/asset_{i}.py",
                license="MIT",
                created_at="2026-09-14T13:00:00Z",
                created_by="test",
                renderer_tier=2
            )
            manifest.add_asset(asset)

        # Run batch verification
        valid_count, failed_count, errors = AssetLibraryHelper.batch_verify_integrity(
            manifest, asset_dir
        )

        assert valid_count == 3, f"Expected 3 valid, got {valid_count}"
        assert failed_count == 2, f"Expected 2 failed, got {failed_count}"
        assert len(errors) == 2, f"Expected 2 error messages, got {len(errors)}"

        print(f"  ✅ PASS: Verified 3 valid + 2 failed assets")
        return True


def test_integrity_report_generation():
    """Test comprehensive integrity report generation

    Scenario:
    1. Create manifest with various asset states
    2. Generate integrity report
    3. Verify report structure and content
    """
    print("\n[TEST] Integrity Report Generation")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        manifest = AssetLibraryManifest(tmpdir / "manifest.json")
        asset_dir = tmpdir / "assets"
        asset_dir.mkdir()

        # Add valid asset
        scenes_dir = asset_dir / "scenes"
        scenes_dir.mkdir()

        content = b"valid asset"
        sha = hashlib.sha256()
        sha.update(content)
        valid_hash = sha.hexdigest()

        (scenes_dir / "valid.py").write_bytes(content)

        asset = AssetMetadata(
            id="valid-asset",
            version="1.0",
            name="Valid Asset",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256=valid_hash,
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="scenes/valid.py",
            license="MIT",
            created_at="2026-09-14T13:00:00Z",
            created_by="test",
            renderer_tier=2
        )
        manifest.add_asset(asset)

        # Generate report
        report = AssetLibraryHelper.generate_integrity_report(manifest, asset_dir)

        # Verify report structure
        assert "timestamp" in report
        assert "total_assets" in report
        assert "valid_count" in report
        assert "failed_count" in report
        assert "status" in report

        assert report["total_assets"] == 1
        assert report["valid_count"] == 1
        assert report["failed_count"] == 0
        assert "✅ OK" in report["status"]

        print("  ✅ PASS: Generated integrity report with correct structure")
        return True


def run_all_integration_tests():
    """Run all integration tests"""
    print("=" * 70)
    print("ASSET LIBRARY INTEGRATION TEST SUITE")
    print("ADR-0740, ADR-0741, ADR-0742: Phase 5 Validation Framework")
    print("=" * 70)

    tests = [
        test_missing_asset_detection,
        test_checksum_mismatch_detection,
        test_cache_invalidation_by_age,
        test_cache_invalidation_by_tier,
        test_batch_integrity_verification,
        test_integrity_report_generation,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {type(e).__name__}: {e}")
            failed += 1

    # Print summary
    print("\n" + "=" * 70)
    print(f"INTEGRATION TEST SUMMARY: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"Success Rate: {passed / len(tests) * 100:.1f}%")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = run_all_integration_tests()
    sys.exit(0 if success else 1)
