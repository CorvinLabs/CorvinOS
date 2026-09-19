"""Asset Library — Versioned, Reproducible Assets

Manages versioned asset library with SHA256 hashing for reproducibility.
Ensures "same input → same output" for animations across all videos.

ADR-0740, ADR-0741, ADR-0742: Phase 5 Video Producer Skill 2.0
- ADR-0740: Phase 5 design (validation framework)
- ADR-0741: Tier architecture (asset validation per tier)
- ADR-0742: Voice-sync timing (asset dependencies)
"""

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from datetime import datetime
from enum import Enum


class ValidationError(Exception):
    """Raised when asset validation fails"""
    pass


class AssetIntegrityError(ValidationError):
    """Raised when asset integrity check fails (SHA256 mismatch)"""
    pass


class AssetNotFoundError(ValidationError):
    """Raised when referenced asset is not in manifest"""
    pass


class ValidationResult:
    """Result of validation operation"""
    def __init__(self, valid: bool = True, errors: List[str] = None, warnings: List[str] = None):
        self.valid = valid
        self.errors = errors or []
        self.warnings = warnings or []

    def add_error(self, msg: str):
        self.valid = False
        self.errors.append(msg)

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def __repr__(self) -> str:
        status = "✅ VALID" if self.valid else "❌ INVALID"
        return f"ValidationResult({status}, errors={len(self.errors)}, warnings={len(self.warnings)})"


@dataclass
class AssetMetadata:
    """Metadata for a versioned asset"""
    id: str                      # "learning-loop"
    version: str                 # "1.0"
    name: str                    # "Learning Loop Diagram"
    description: str
    asset_type: str              # "manim-scene", "svg-diagram", "screenshot"
    checksum_sha256: str         # Asset hash (ensures reproducibility)
    didactic_level: List[str]    # ["beginner", "technical"]
    duration_seconds: int        # Estimated duration
    file_path: str               # Relative path to asset
    license: str                 # "MIT", "Apache-2.0", etc.
    created_at: str              # ISO8601
    created_by: str              # Author
    dependencies: List[str] = None  # Other asset IDs this depends on
    renderer_tier: int = 2       # Which tier renders this (1, 2, or 3)
    cache_valid: bool = True     # Cached validation state
    last_verified: Optional[str] = None  # ISO8601 timestamp of last verification

    def full_id(self) -> str:
        """Full qualified ID with version and hash"""
        return f"{self.id}#v{self.version}#{self.checksum_sha256[:8]}"

    def validate_schema(self) -> Tuple[bool, List[str]]:
        """Validate asset metadata schema

        Returns:
            Tuple of (valid, errors)
        """
        errors = []

        # Required fields check
        if not self.id:
            errors.append("Asset id is required")
        if not self.version:
            errors.append("Asset version is required")
        if not self.checksum_sha256:
            errors.append("Asset checksum_sha256 is required")
        if not self.asset_type:
            errors.append("Asset type is required")

        # SHA256 format validation (hex string, 64 chars)
        if self.checksum_sha256 and len(self.checksum_sha256) != 64:
            errors.append(f"Invalid checksum format: expected 64 hex chars, got {len(self.checksum_sha256)}")

        # Renderer tier validation
        if self.renderer_tier not in [1, 2, 3]:
            errors.append(f"Invalid renderer_tier: {self.renderer_tier} (must be 1, 2, or 3)")

        return len(errors) == 0, errors


class AssetLibraryManifest:
    """Versioned asset library manifest

    Manages a collection of versioned assets, each with SHA256 hash
    for reproducibility verification.
    """

    def __init__(self, manifest_path: Path):
        """Initialize asset library

        Args:
            manifest_path: Path to manifest.json file
        """
        self.manifest_path = manifest_path
        self.assets: Dict[str, AssetMetadata] = {}
        self.version = "1.0"
        self.generated_at = datetime.now().isoformat()

        if manifest_path.exists():
            self._load_manifest()

    def _load_manifest(self):
        """Load manifest from JSON file"""
        try:
            with open(self.manifest_path) as f:
                data = json.load(f)

            self.version = data.get("version", "1.0")
            self.generated_at = data.get("generated_at", datetime.now().isoformat())

            for asset_data in data.get("assets", []):
                asset = AssetMetadata(
                    id=asset_data["id"],
                    version=asset_data["version"],
                    name=asset_data["name"],
                    description=asset_data["description"],
                    asset_type=asset_data["type"],
                    checksum_sha256=asset_data["checksum_sha256"],
                    didactic_level=asset_data.get("didactic_level", []),
                    duration_seconds=asset_data.get("duration_seconds", 0),
                    file_path=asset_data["file_path"],
                    license=asset_data.get("license", "unknown"),
                    created_at=asset_data.get("created_at"),
                    created_by=asset_data.get("created_by", "unknown"),
                    dependencies=asset_data.get("dependencies"),
                    renderer_tier=asset_data.get("renderer_tier", 2)
                )
                key = f"{asset.id}:{asset.version}"
                self.assets[key] = asset

        except Exception as e:
            print(f"Error loading manifest: {e}")

    def add_asset(self, asset: AssetMetadata):
        """Add asset to library

        Args:
            asset: AssetMetadata to add
        """
        key = f"{asset.id}:{asset.version}"
        self.assets[key] = asset

    def get_asset(self, asset_id: str, version: str) -> Optional[AssetMetadata]:
        """Get asset by ID and version

        Args:
            asset_id: Asset ID
            version: Version string

        Returns:
            AssetMetadata or None if not found
        """
        key = f"{asset_id}:{version}"
        return self.assets.get(key)

    def get_latest_asset(self, asset_id: str) -> Optional[AssetMetadata]:
        """Get latest version of asset

        Args:
            asset_id: Asset ID

        Returns:
            Latest AssetMetadata or None if not found
        """
        matching = [a for a in self.assets.values() if a.id == asset_id]
        if not matching:
            return None
        # Sort by version (assumes semantic versioning)
        return sorted(matching, key=lambda a: a.version, reverse=True)[0]

    def list_assets(self, asset_type: Optional[str] = None) -> List[AssetMetadata]:
        """List all assets, optionally filtered by type

        Args:
            asset_type: Filter by asset type (optional)

        Returns:
            List of AssetMetadata
        """
        assets = list(self.assets.values())
        if asset_type:
            assets = [a for a in assets if a.asset_type == asset_type]
        return sorted(assets, key=lambda a: (a.id, a.version))

    def verify_asset(self, asset: AssetMetadata, file_path: Path) -> bool:
        """Verify asset file matches checksum

        Args:
            asset: AssetMetadata
            file_path: Path to asset file

        Returns:
            True if checksum matches
        """
        import hashlib
        if not file_path.exists():
            return False

        sha = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                sha.update(f.read())
            actual_hash = sha.hexdigest()
            return actual_hash == asset.checksum_sha256
        except Exception as e:
            print(f"Error verifying asset: {e}")
            return False

    def save_manifest(self):
        """Save manifest to JSON file"""
        data = {
            "version": self.version,
            "description": "Asset Library for Video Producer Phase 5.1",
            "generated_at": datetime.now().isoformat(),
            "assets": [
                {
                    "id": asset.id,
                    "version": asset.version,
                    "name": asset.name,
                    "description": asset.description,
                    "type": asset.asset_type,
                    "checksum_sha256": asset.checksum_sha256,
                    "didactic_level": asset.didactic_level,
                    "duration_seconds": asset.duration_seconds,
                    "file_path": asset.file_path,
                    "license": asset.license,
                    "created_at": asset.created_at,
                    "created_by": asset.created_by,
                    "dependencies": asset.dependencies or [],
                    "renderer_tier": asset.renderer_tier
                }
                for asset in sorted(self.assets.values(), key=lambda a: (a.id, a.version))
            ]
        }

        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w") as f:
            json.dump(data, f, indent=2)

    def export_json(self) -> str:
        """Export manifest as JSON string"""
        data = {
            "version": self.version,
            "description": "Asset Library for Video Producer Phase 5.1",
            "generated_at": datetime.now().isoformat(),
            "assets": [
                {
                    "id": asset.id,
                    "version": asset.version,
                    "name": asset.name,
                    "description": asset.description,
                    "type": asset.asset_type,
                    "checksum_sha256": asset.checksum_sha256,
                    "didactic_level": asset.didactic_level,
                    "duration_seconds": asset.duration_seconds,
                    "file_path": asset.file_path,
                    "license": asset.license,
                    "created_at": asset.created_at,
                    "created_by": asset.created_by
                }
                for asset in sorted(self.assets.values(), key=lambda a: (a.id, a.version))
            ]
        }
        return json.dumps(data, indent=2)

    def validate_manifest(self, asset_base_path: Optional[Path] = None) -> ValidationResult:
        """Validate all assets in manifest

        Checks:
        1. Schema validity (all required fields present, correct types)
        2. Checksum format (64-char hex strings)
        3. File existence (if asset_base_path provided)
        4. SHA256 integrity (if asset_base_path provided)

        Args:
            asset_base_path: Base path to asset files (optional, for file verification)

        Returns:
            ValidationResult with errors and warnings
        """
        result = ValidationResult()

        if not self.assets:
            result.add_warning("Manifest contains no assets")
            return result

        for asset_key, asset in self.assets.items():
            # Schema validation
            valid_schema, schema_errors = asset.validate_schema()
            for error in schema_errors:
                result.add_error(f"Asset {asset.id}:{asset.version} — {error}")

            # File existence and integrity check
            if asset_base_path:
                asset_file = asset_base_path / asset.file_path

                if not asset_file.exists():
                    result.add_error(f"Asset file not found: {asset.file_path} (asset {asset.id}:{asset.version})")
                else:
                    # Check integrity
                    if not self.verify_asset(asset, asset_file):
                        result.add_error(
                            f"Asset integrity check failed: {asset.id}:{asset.version} "
                            f"(SHA256 mismatch for {asset.file_path})"
                        )

        return result

    def validate_scene_assets(self, scene_spec: Dict, asset_base_path: Optional[Path] = None) -> ValidationResult:
        """Validate scene references to assets

        Checks:
        1. Scene specifies asset_ids (list or single)
        2. All asset_ids exist in manifest
        3. Assets are available at expected versions
        4. Optional: Verify asset files on disk

        Args:
            scene_spec: Scene storyboard specification
            asset_base_path: Base path to verify asset files (optional)

        Returns:
            ValidationResult with errors and warnings

        Raises:
            AssetNotFoundError: If required asset is missing
        """
        result = ValidationResult()

        # Extract asset IDs from scene spec
        asset_ids = []

        # Support both single asset and multiple assets
        if "asset_id" in scene_spec:
            asset_ids.append(scene_spec["asset_id"])
        elif "asset_ids" in scene_spec:
            asset_ids.extend(scene_spec["asset_ids"])

        if not asset_ids:
            result.add_warning("Scene spec contains no asset references")
            return result

        # Validate each referenced asset
        for asset_id in asset_ids:
            # Check if asset exists in manifest (get latest version)
            asset = self.get_latest_asset(asset_id)

            if asset is None:
                result.add_error(f"Scene references missing asset: {asset_id}")
                # Fail-closed: Missing asset is an error, not a warning
                raise AssetNotFoundError(f"Asset '{asset_id}' not found in manifest")

            # Optional: Verify file
            if asset_base_path:
                asset_file = asset_base_path / asset.file_path
                if not asset_file.exists():
                    result.add_error(f"Asset file missing: {asset.file_path} (referenced as {asset_id})")

        return result

    def check_asset_integrity(self, asset_id: str, asset_base_path: Path) -> ValidationResult:
        """Check asset file integrity against manifest

        Verifies:
        1. File exists
        2. SHA256 matches manifest
        3. Updates cache validation state
        4. Returns status for cache invalidation decision

        Args:
            asset_id: Asset ID (gets latest version)
            asset_base_path: Base path to asset files

        Returns:
            ValidationResult with integrity status

        Raises:
            AssetIntegrityError: If file not found or checksum mismatch
        """
        result = ValidationResult()

        # Get latest asset version
        asset = self.get_latest_asset(asset_id)
        if asset is None:
            raise AssetNotFoundError(f"Asset '{asset_id}' not found in manifest")

        asset_file = asset_base_path / asset.file_path

        # Check file exists
        if not asset_file.exists():
            result.add_error(f"Asset file not found: {asset_file} (asset {asset_id})")
            asset.cache_valid = False
            raise AssetIntegrityError(f"Asset file missing: {asset.file_path}")

        # Verify SHA256
        sha = hashlib.sha256()
        try:
            with open(asset_file, "rb") as f:
                sha.update(f.read())
            actual_hash = sha.hexdigest()

            if actual_hash != asset.checksum_sha256:
                result.add_error(
                    f"Checksum mismatch for {asset_id}: "
                    f"expected {asset.checksum_sha256[:16]}... "
                    f"got {actual_hash[:16]}..."
                )
                asset.cache_valid = False
                raise AssetIntegrityError(
                    f"Asset {asset_id} integrity failed: SHA256 mismatch"
                )

            # Cache is valid
            asset.cache_valid = True
            asset.last_verified = datetime.now().isoformat()

        except Exception as e:
            result.add_error(f"Error verifying asset {asset_id}: {str(e)}")
            asset.cache_valid = False
            raise

        return result

    def invalidate_cache(self, asset_id: Optional[str] = None):
        """Mark asset cache as invalid (requires re-download)

        Args:
            asset_id: Specific asset to invalidate, or None for all
        """
        if asset_id:
            asset = self.get_latest_asset(asset_id)
            if asset:
                asset.cache_valid = False
                asset.last_verified = None
        else:
            # Invalidate all assets
            for asset in self.assets.values():
                asset.cache_valid = False
                asset.last_verified = None

    def get_invalid_assets(self) -> List[AssetMetadata]:
        """Get list of assets with invalid cache

        Returns:
            List of assets that need re-verification or re-download
        """
        return [a for a in self.assets.values() if not a.cache_valid]

    def dependency_validation(self, asset_id: str) -> ValidationResult:
        """Validate asset dependencies (recursive)

        Ensures:
        1. Asset exists
        2. All dependencies exist
        3. No circular dependencies

        Args:
            asset_id: Asset ID to validate

        Returns:
            ValidationResult
        """
        result = ValidationResult()
        visited = set()

        def check_deps(aid: str, depth: int = 0):
            if depth > 10:
                result.add_error(f"Possible circular dependency: {aid}")
                return False

            if aid in visited:
                result.add_error(f"Circular dependency detected: {aid}")
                return False

            visited.add(aid)
            asset = self.get_latest_asset(aid)

            if asset is None:
                result.add_error(f"Dependency not found: {aid}")
                return False

            if asset.dependencies:
                for dep_id in asset.dependencies:
                    if not check_deps(dep_id, depth + 1):
                        return False

            return True

        if not check_deps(asset_id):
            result.valid = False

        return result
