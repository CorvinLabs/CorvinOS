"""Asset Library — Versioned, Reproducible Assets

Manages versioned asset library with SHA256 hashing for reproducibility.
Ensures "same input → same output" for animations across all videos.

ADR-0742: Didactic Storyboard System (Asset Versioning)
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime


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

    def full_id(self) -> str:
        """Full qualified ID with version and hash"""
        return f"{self.id}#v{self.version}#{self.checksum_sha256[:8]}"


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
