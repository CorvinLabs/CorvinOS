"""Asset Library Helper Functions

Utility functions for asset manifest management, including:
- Missing asset detection and reporting
- Cache invalidation workflows
- Manifest synchronization with disk files
- Batch validation operations

ADR-0740, ADR-0741, ADR-0742: Phase 5 Validation Framework
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple
from datetime import datetime
from dataclasses import asdict

from asset_library import (
    AssetMetadata,
    AssetLibraryManifest,
    ValidationResult,
    AssetIntegrityError,
)


class AssetLibraryHelper:
    """Helper functions for asset library management"""

    @staticmethod
    def detect_missing_assets(
        manifest: AssetLibraryManifest,
        asset_base_path: Path
    ) -> Dict[str, List[str]]:
        """Detect missing asset files

        Returns:
            Dict mapping asset_id to list of missing file issues
        """
        missing = {}

        for asset in manifest.assets.values():
            asset_file = asset_base_path / asset.file_path

            if not asset_file.exists():
                if asset.id not in missing:
                    missing[asset.id] = []
                missing[asset.id].append(f"File not found: {asset.file_path}")

        return missing

    @staticmethod
    def detect_checksum_mismatches(
        manifest: AssetLibraryManifest,
        asset_base_path: Path
    ) -> Dict[str, Dict]:
        """Detect assets with checksum mismatches

        Returns:
            Dict mapping asset_id to mismatch details
        """
        mismatches = {}

        for asset in manifest.assets.values():
            asset_file = asset_base_path / asset.file_path

            if asset_file.exists():
                sha = hashlib.sha256()
                try:
                    with open(asset_file, "rb") as f:
                        sha.update(f.read())
                    actual_hash = sha.hexdigest()

                    if actual_hash != asset.checksum_sha256:
                        mismatches[asset.id] = {
                            "expected": asset.checksum_sha256[:16] + "...",
                            "actual": actual_hash[:16] + "...",
                            "file": str(asset.file_path),
                            "version": asset.version
                        }
                except Exception as e:
                    mismatches[asset.id] = {
                        "error": str(e),
                        "file": str(asset.file_path)
                    }

        return mismatches

    @staticmethod
    def invalidate_assets_by_type(
        manifest: AssetLibraryManifest,
        asset_type: str
    ) -> int:
        """Invalidate all assets of a specific type

        Useful when a tier's rendering changes require re-validation

        Returns:
            Count of invalidated assets
        """
        count = 0
        for asset in manifest.assets.values():
            if asset.asset_type == asset_type:
                asset.cache_valid = False
                asset.last_verified = None
                count += 1

        return count

    @staticmethod
    def invalidate_assets_by_renderer_tier(
        manifest: AssetLibraryManifest,
        tier: int
    ) -> int:
        """Invalidate all assets for a specific renderer tier

        Useful when a tier's implementation changes

        Returns:
            Count of invalidated assets
        """
        count = 0
        for asset in manifest.assets.values():
            if asset.renderer_tier == tier:
                asset.cache_valid = False
                asset.last_verified = None
                count += 1

        return count

    @staticmethod
    def batch_verify_integrity(
        manifest: AssetLibraryManifest,
        asset_base_path: Path
    ) -> Tuple[int, int, List[str]]:
        """Verify integrity of all assets in batch

        Returns:
            Tuple of (valid_count, failed_count, error_messages)
        """
        valid_count = 0
        failed_count = 0
        errors = []

        for asset in manifest.assets.values():
            try:
                manifest.check_asset_integrity(asset.id, asset_base_path)
                valid_count += 1
            except AssetIntegrityError as e:
                failed_count += 1
                errors.append(f"{asset.id}:{asset.version} — {str(e)}")
            except Exception as e:
                failed_count += 1
                errors.append(f"{asset.id}:{asset.version} — {type(e).__name__}: {str(e)}")

        return valid_count, failed_count, errors

    @staticmethod
    def generate_integrity_report(
        manifest: AssetLibraryManifest,
        asset_base_path: Path
    ) -> Dict:
        """Generate comprehensive integrity report

        Returns:
            Dict with integrity statistics and details
        """
        valid_count, failed_count, errors = AssetLibraryHelper.batch_verify_integrity(
            manifest, asset_base_path
        )

        missing = AssetLibraryHelper.detect_missing_assets(manifest, asset_base_path)
        mismatches = AssetLibraryHelper.detect_checksum_mismatches(manifest, asset_base_path)
        invalid_assets = manifest.get_invalid_assets()

        return {
            "timestamp": datetime.now().isoformat(),
            "total_assets": len(manifest.assets),
            "valid_count": valid_count,
            "failed_count": failed_count,
            "missing_assets": missing,
            "checksum_mismatches": mismatches,
            "invalid_cache": len(invalid_assets),
            "errors": errors,
            "status": "✅ OK" if failed_count == 0 and len(missing) == 0 else "❌ ERRORS DETECTED"
        }

    @staticmethod
    def export_integrity_report(
        manifest: AssetLibraryManifest,
        asset_base_path: Path,
        report_path: Path
    ) -> None:
        """Export integrity report to JSON file

        Args:
            manifest: Asset library manifest
            asset_base_path: Base path to assets
            report_path: Path to write report JSON
        """
        report = AssetLibraryHelper.generate_integrity_report(manifest, asset_base_path)

        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

    @staticmethod
    def sync_manifest_with_disk(
        manifest: AssetLibraryManifest,
        asset_base_path: Path,
        auto_hash: bool = False
    ) -> Dict[str, any]:
        """Synchronize manifest with actual disk files

        Can detect missing files, compute actual hashes, and update manifest.

        Args:
            manifest: Asset library manifest
            asset_base_path: Base path to assets
            auto_hash: If True, compute actual SHA256 for files

        Returns:
            Sync report with findings
        """
        report = {
            "timestamp": datetime.now().isoformat(),
            "files_found": 0,
            "files_added": 0,
            "files_missing": [],
            "hashes_updated": 0,
            "errors": []
        }

        # Check all manifest entries against disk
        for asset in list(manifest.assets.values()):
            asset_file = asset_base_path / asset.file_path

            if asset_file.exists():
                report["files_found"] += 1

                if auto_hash:
                    try:
                        sha = hashlib.sha256()
                        with open(asset_file, "rb") as f:
                            sha.update(f.read())

                        new_hash = sha.hexdigest()
                        if new_hash != asset.checksum_sha256:
                            asset.checksum_sha256 = new_hash
                            report["hashes_updated"] += 1
                    except Exception as e:
                        report["errors"].append(
                            f"Error hashing {asset.file_path}: {str(e)}"
                        )
            else:
                report["files_missing"].append(f"{asset.id}:{asset.version} ({asset.file_path})")

        return report


class CacheInvalidationScheduler:
    """Manage cache invalidation policies"""

    @staticmethod
    def invalidate_by_age(
        manifest: AssetLibraryManifest,
        max_age_days: int
    ) -> int:
        """Invalidate assets not verified within max_age_days

        Returns:
            Count of invalidated assets
        """
        from datetime import datetime, timedelta

        count = 0
        cutoff = datetime.now() - timedelta(days=max_age_days)

        for asset in manifest.assets.values():
            if asset.last_verified:
                try:
                    verified_time = datetime.fromisoformat(asset.last_verified)
                    if verified_time < cutoff:
                        asset.cache_valid = False
                        count += 1
                except ValueError:
                    pass

        return count

    @staticmethod
    def invalidate_by_tier_degradation(
        manifest: AssetLibraryManifest,
        tier_degradation_map: Dict[int, bool]
    ) -> int:
        """Invalidate assets if their tier rendering was downgraded

        Args:
            manifest: Asset library manifest
            tier_degradation_map: Dict mapping tier to downgrade flag

        Returns:
            Count of invalidated assets
        """
        count = 0
        for asset in manifest.assets.values():
            if tier_degradation_map.get(asset.renderer_tier, False):
                asset.cache_valid = False
                asset.last_verified = None
                count += 1

        return count


if __name__ == "__main__":
    # Example usage
    print("Asset Library Helper Functions")
    print("ADR-0740, ADR-0741, ADR-0742: Phase 5 Validation Framework")
    print("\nProvides:")
    print("  - Missing asset detection")
    print("  - Checksum mismatch detection")
    print("  - Batch integrity verification")
    print("  - Integrity report generation")
    print("  - Manifest-disk synchronization")
    print("  - Cache invalidation policies")
