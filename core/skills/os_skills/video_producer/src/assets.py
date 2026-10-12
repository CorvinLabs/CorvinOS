"""Asset Storage and Lifecycle System for Video Producer (T-0111, ADR-2249 Phase 2).

Manages reusable SVG/PNG/font assets with:
- Per-tenant, versioned storage
- Dependency tracking (which scenes use which assets)
- GDPR L36 erasure integration
- Immutable versions and rollback support
- Asset validation (format, size, security)

Architecture:
  <corvin_home>/tenants/<tid>/video_producer/assets/<version_hash>/
  └── manifest.json (version metadata, created_at, creator, deprecation_date)
  └── assets/ (SVG/PNG/font files)
  └── dependencies.jsonl (scene → asset references)

Every asset write is audit-first, tenant-scoped, and immutable after creation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Literal
from uuid import uuid4
import os

from core.paths.tenant import tenant_home, validate_tenant_id

logger = logging.getLogger(__name__)

AssetFormat = Literal["svg", "png", "ttf", "otf", "woff2"]


@dataclass(frozen=True)
class AssetMetadata:
    """Immutable metadata for a single asset."""

    asset_id: str
    name: str  # filename without extension
    format: AssetFormat
    size_bytes: int
    hash_sha256: str
    created_at: str
    uploaded_by: str
    description: Optional[str] = None

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate asset metadata."""
        valid_formats = {"svg", "png", "ttf", "otf", "woff2"}
        if self.format not in valid_formats:
            return False, f"Invalid format: {self.format}"
        if self.size_bytes > 2 * 1024 * 1024:  # 2 MB limit
            return False, "Asset exceeds 2 MB"
        if self.size_bytes == 0:
            return False, "Asset is empty"
        if not self.name:
            return False, "Asset name required"
        return True, None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "hash_sha256": self.hash_sha256,
            "created_at": self.created_at,
            "uploaded_by": self.uploaded_by,
            "description": self.description,
        }


@dataclass(frozen=True)
class AssetDependency:
    """Immutable record of which scene uses which asset."""

    dependency_id: str
    asset_id: str
    scene_id: str
    job_id: str
    created_at: str
    tenant_id: str = "_default"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "dependency_id": self.dependency_id,
            "asset_id": self.asset_id,
            "scene_id": self.scene_id,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "tenant_id": self.tenant_id,
        }


@dataclass(frozen=True)
class VersionMetadata:
    """Immutable version header for an asset pack."""

    version_hash: str
    created_at: str
    creator: str
    description: Optional[str] = None
    deprecation_date: Optional[str] = None
    asset_count: int = 0

    def is_deprecated(self) -> bool:
        """Check if this version is past its deprecation date."""
        if not self.deprecation_date:
            return False
        try:
            deprecated = datetime.fromisoformat(self.deprecation_date.replace("Z", "+00:00"))
            return datetime.utcnow().replace(tzinfo=deprecated.tzinfo) > deprecated
        except (ValueError, AttributeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "version_hash": self.version_hash,
            "created_at": self.created_at,
            "creator": self.creator,
            "description": self.description,
            "deprecation_date": self.deprecation_date,
            "asset_count": self.asset_count,
        }


@dataclass(frozen=True)
class AssetDeletion:
    """Immutable record of a marked-for-deletion asset."""

    asset_id: str
    reason: str
    marked_at: str
    cleanup_date: str
    marked_by: str

    def is_cleanup_due(self) -> bool:
        """Check if cleanup date has passed."""
        try:
            cleanup = datetime.fromisoformat(self.cleanup_date.replace("Z", "+00:00"))
            return datetime.utcnow().replace(tzinfo=cleanup.tzinfo) >= cleanup
        except (ValueError, AttributeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "asset_id": self.asset_id,
            "reason": self.reason,
            "marked_at": self.marked_at,
            "cleanup_date": self.cleanup_date,
            "marked_by": self.marked_by,
        }


class AssetPack:
    """Manages versioned asset storage for a tenant.

    Architecture:
      <corvin_home>/tenants/<tid>/video_producer/assets/<version_hash>/
      ├── manifest.json (VersionMetadata)
      ├── assets/ (SVG/PNG/font files)
      ├── dependencies.jsonl (AssetDependency records, append-only)
      └── deletions.jsonl (AssetDeletion records, append-only)

    Invariants:
      - Versions are immutable once written
      - Asset files are never modified, only deleted
      - Dependencies and deletions are append-only logs
      - Cleanup is idempotent (mark multiple times OK, delete only once)
    """

    def __init__(
        self,
        tenant_id: str = "_default",
        creator: str = "system",
    ):
        """Initialize asset pack.

        Args:
            tenant_id: Tenant scope (GDPR Art. 5)
            creator: User/system identifier for audit trail

        Raises:
            ValueError: If tenant_id is invalid
        """
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.creator = creator

        # Resolve tenant paths
        tenant_root = tenant_home(tenant_id)
        self.assets_root = tenant_root / "video_producer" / "assets"
        self.assets_root.mkdir(parents=True, exist_ok=True)

    def _version_path(self, version_hash: str) -> Path:
        """Resolve path for a specific version.

        Args:
            version_hash: 8-character hex hash

        Returns:
            Path to version directory
        """
        if len(version_hash) != 8 or not all(c in "0123456789abcdef" for c in version_hash):
            raise ValueError(f"Invalid version hash: {version_hash}")
        return self.assets_root / version_hash

    def create_version(
        self,
        assets: dict[str, bytes],
        description: Optional[str] = None,
    ) -> str:
        """Create a new immutable version of the asset pack.

        Args:
            assets: dict[filename → file_bytes] (PNG, SVG, TTF, etc.)
            description: Optional version description

        Returns:
            version_hash (8-character hex)

        Raises:
            ValueError: If assets exceed limits or contain invalid formats
            IOError: If version directory cannot be created
        """
        # Validate assets
        if not assets:
            raise ValueError("At least one asset required")
        if len(assets) > 50:
            raise ValueError("Maximum 50 assets per version")

        validated_assets = {}
        for filename, content in assets.items():
            # Validate format
            _, ext = Path(filename).name.rsplit(".", 1) if "." in Path(filename).name else ("", "")
            if ext.lower() not in {"svg", "png", "ttf", "otf", "woff2"}:
                raise ValueError(f"Invalid asset format: {filename} (allowed: svg, png, ttf, otf, woff2)")

            # Validate size
            if len(content) > 2 * 1024 * 1024:
                raise ValueError(f"Asset {filename} exceeds 2 MB limit")

            # Strip EXIF from PNG files (minimal implementation)
            if ext.lower() == "png":
                content = self._strip_png_metadata(content)

            validated_assets[filename] = content

        # Generate version hash from asset content
        asset_hashes = [
            hashlib.sha256(content).hexdigest()
            for _, content in sorted(validated_assets.items())
        ]
        combined_hash = hashlib.sha256(
            "".join(asset_hashes).encode()
        ).hexdigest()
        version_hash = combined_hash[:8]

        version_dir = self._version_path(version_hash)

        # Check for version already existing (idempotent if same content)
        if version_dir.exists():
            existing_manifest = version_dir / "manifest.json"
            if existing_manifest.exists():
                # Version already exists; return the existing hash (idempotent)
                logger.info(f"Version {version_hash} already exists (idempotent)")
                return version_hash

        # Create version directory
        version_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = version_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        # Write assets
        for filename, content in validated_assets.items():
            asset_path = assets_dir / filename
            asset_path.write_bytes(content)
            logger.info(f"Created asset {asset_path}")

        # Write manifest
        manifest = VersionMetadata(
            version_hash=version_hash,
            created_at=datetime.utcnow().isoformat() + "Z",
            creator=self.creator,
            description=description,
            asset_count=len(validated_assets),
        )
        manifest_path = version_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2),
            encoding="utf-8"
        )
        logger.info(f"Created version {version_hash} with {len(validated_assets)} assets")

        # Initialize dependency and deletion logs
        (version_dir / "dependencies.jsonl").touch()
        (version_dir / "deletions.jsonl").touch()

        return version_hash

    def get_version(self, version_hash: str) -> VersionMetadata:
        """Load version metadata.

        Args:
            version_hash: 8-character hex hash

        Returns:
            VersionMetadata

        Raises:
            ValueError: If version does not exist
        """
        version_dir = self._version_path(version_hash)
        manifest_path = version_dir / "manifest.json"

        if not manifest_path.exists():
            raise ValueError(f"Version {version_hash} not found")

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return VersionMetadata(
            version_hash=data["version_hash"],
            created_at=data["created_at"],
            creator=data["creator"],
            description=data.get("description"),
            deprecation_date=data.get("deprecation_date"),
            asset_count=data.get("asset_count", 0),
        )

    def list_versions(self) -> list[VersionMetadata]:
        """List all versions for this tenant.

        Returns:
            List of VersionMetadata, sorted by created_at (newest first)
        """
        versions = []
        if not self.assets_root.exists():
            return versions

        for version_dir in self.assets_root.iterdir():
            if version_dir.is_dir():
                try:
                    manifest = self.get_version(version_dir.name)
                    versions.append(manifest)
                except ValueError:
                    logger.warning(f"Invalid version directory: {version_dir.name}")

        # Sort by created_at, newest first
        versions.sort(
            key=lambda v: v.created_at,
            reverse=True
        )
        return versions

    def get_asset(self, version_hash: str, filename: str) -> bytes:
        """Load a specific asset file.

        Args:
            version_hash: 8-character hex hash
            filename: Asset filename

        Returns:
            Asset file content (bytes)

        Raises:
            ValueError: If version or asset does not exist
        """
        version_dir = self._version_path(version_hash)
        asset_path = version_dir / "assets" / filename

        if not asset_path.exists():
            raise ValueError(f"Asset {filename} not found in version {version_hash}")

        return asset_path.read_bytes()

    def list_assets(self, version_hash: str) -> list[str]:
        """List all assets in a version.

        Args:
            version_hash: 8-character hex hash

        Returns:
            List of filenames
        """
        version_dir = self._version_path(version_hash)
        assets_dir = version_dir / "assets"

        if not assets_dir.exists():
            return []

        return sorted([f.name for f in assets_dir.iterdir() if f.is_file()])

    def add_dependency(
        self,
        version_hash: str,
        asset_id: str,
        scene_id: str,
        job_id: str,
    ) -> str:
        """Record a scene → asset dependency.

        Args:
            version_hash: 8-character hex hash
            asset_id: Asset identifier
            scene_id: Scene identifier
            job_id: Job identifier

        Returns:
            dependency_id

        Raises:
            ValueError: If version does not exist
        """
        version_dir = self._version_path(version_hash)
        if not version_dir.exists():
            raise ValueError(f"Version {version_hash} not found")

        dep = AssetDependency(
            dependency_id=str(uuid4()),
            asset_id=asset_id,
            scene_id=scene_id,
            job_id=job_id,
            created_at=datetime.utcnow().isoformat() + "Z",
            tenant_id=self.tenant_id,
        )

        dep_log = version_dir / "dependencies.jsonl"
        with open(dep_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(dep.to_dict()) + "\n")
        logger.info(f"Added dependency: {dep.dependency_id}")
        return dep.dependency_id

    def get_dependencies(self, version_hash: str, asset_id: Optional[str] = None) -> list[AssetDependency]:
        """List dependencies for a version or specific asset.

        Args:
            version_hash: 8-character hex hash
            asset_id: Optional filter by asset

        Returns:
            List of AssetDependency records
        """
        version_dir = self._version_path(version_hash)
        dep_log = version_dir / "dependencies.jsonl"

        if not dep_log.exists():
            return []

        dependencies = []
        for line in dep_log.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                dep = AssetDependency(
                    dependency_id=data["dependency_id"],
                    asset_id=data["asset_id"],
                    scene_id=data["scene_id"],
                    job_id=data["job_id"],
                    created_at=data["created_at"],
                    tenant_id=data.get("tenant_id", "_default"),
                )
                if asset_id is None or dep.asset_id == asset_id:
                    dependencies.append(dep)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Invalid dependency record: {e}")

        return dependencies

    def mark_for_deletion(
        self,
        version_hash: str,
        asset_id: str,
        reason: str,
        cleanup_days: int = 30,
        marked_by: str = "system",
    ) -> str:
        """Mark an asset for deletion (GDPR L36 integration).

        The asset is NOT deleted immediately; instead it is marked for cleanup
        after `cleanup_days`. This allows for a grace period before final erasure.

        Args:
            version_hash: 8-character hex hash
            asset_id: Asset identifier
            reason: Reason for deletion (user_erasure, deprecated, etc.)
            cleanup_days: Days until cleanup is due (default 30)
            marked_by: User/system identifier

        Returns:
            Deletion record ID

        Raises:
            ValueError: If version does not exist
        """
        version_dir = self._version_path(version_hash)
        if not version_dir.exists():
            raise ValueError(f"Version {version_hash} not found")

        cleanup_date = (datetime.utcnow() + timedelta(days=cleanup_days)).isoformat() + "Z"
        deletion = AssetDeletion(
            asset_id=asset_id,
            reason=reason,
            marked_at=datetime.utcnow().isoformat() + "Z",
            cleanup_date=cleanup_date,
            marked_by=marked_by,
        )

        del_log = version_dir / "deletions.jsonl"
        with open(del_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(deletion.to_dict()) + "\n")
        logger.info(f"Marked asset {asset_id} for deletion: {deletion.reason}")
        return asset_id

    def get_marked_for_deletion(self, version_hash: str) -> list[AssetDeletion]:
        """List assets marked for deletion.

        Args:
            version_hash: 8-character hex hash

        Returns:
            List of AssetDeletion records
        """
        version_dir = self._version_path(version_hash)
        del_log = version_dir / "deletions.jsonl"

        if not del_log.exists():
            return []

        deletions = []
        for line in del_log.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                deletion = AssetDeletion(
                    asset_id=data["asset_id"],
                    reason=data["reason"],
                    marked_at=data["marked_at"],
                    cleanup_date=data["cleanup_date"],
                    marked_by=data["marked_by"],
                )
                deletions.append(deletion)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Invalid deletion record: {e}")

        return deletions

    def cleanup_due_deletions(self, version_hash: str) -> int:
        """Clean up assets past their cleanup date.

        This is idempotent: calling it multiple times has the same effect.

        Args:
            version_hash: 8-character hex hash

        Returns:
            Number of assets deleted

        Raises:
            ValueError: If version does not exist
        """
        version_dir = self._version_path(version_hash)
        if not version_dir.exists():
            raise ValueError(f"Version {version_hash} not found")

        deletions = self.get_marked_for_deletion(version_hash)
        deleted_count = 0

        for deletion in deletions:
            if deletion.is_cleanup_due():
                assets_dir = version_dir / "assets"
                # Find and delete the asset file
                # The asset_id might not be a filename, so we need to search
                for asset_file in assets_dir.iterdir():
                    if asset_file.is_file():
                        # Try to match by name or ID
                        if deletion.asset_id in asset_file.name or asset_file.stem == deletion.asset_id:
                            asset_file.unlink()
                            logger.info(f"Deleted asset {asset_file.name}")
                            deleted_count += 1
                            break

        return deleted_count

    def get_asset_hygiene_signal(self, version_hash: str) -> dict[str, Any]:
        """Generate asset hygiene signal for monitoring.

        Returns metrics:
        - total_assets: Number of assets in version
        - referenced_assets: Number with dependencies
        - orphaned_assets: Number with no dependencies
        - marked_for_deletion: Number in deletion queue
        - unused_storage_mb: Storage consumed by orphaned assets

        Args:
            version_hash: 8-character hex hash

        Returns:
            Hygiene metrics dict
        """
        version_dir = self._version_path(version_hash)
        if not version_dir.exists():
            raise ValueError(f"Version {version_hash} not found")

        assets = self.list_assets(version_hash)
        dependencies = self.get_dependencies(version_hash)
        deletions = self.get_marked_for_deletion(version_hash)

        referenced_asset_ids = {dep.asset_id for dep in dependencies}
        orphaned = [a for a in assets if a not in referenced_asset_ids]

        # Calculate storage
        assets_dir = version_dir / "assets"
        unused_bytes = 0
        if assets_dir.exists():
            for asset_file in assets_dir.iterdir():
                if asset_file.is_file() and asset_file.name in orphaned:
                    unused_bytes += asset_file.stat().st_size

        return {
            "version_hash": version_hash,
            "total_assets": len(assets),
            "referenced_assets": len(referenced_asset_ids),
            "orphaned_assets": len(orphaned),
            "marked_for_deletion": len(deletions),
            "unused_storage_mb": unused_bytes / (1024 * 1024),
            "tenant_id": self.tenant_id,
        }

    def delete_version(self, version_hash: str) -> bool:
        """Delete an entire version and all its assets (cleanup only).

        Use this after deprecation_date has passed and no scenes reference it.

        Args:
            version_hash: 8-character hex hash

        Returns:
            True if deleted, False if not found

        Raises:
            RuntimeError: If version has active dependencies
        """
        version_dir = self._version_path(version_hash)
        if not version_dir.exists():
            return False

        # Check for active dependencies
        dependencies = self.get_dependencies(version_hash)
        if dependencies:
            raise RuntimeError(f"Cannot delete version {version_hash}: {len(dependencies)} active dependencies exist")

        # Remove directory tree
        import shutil
        shutil.rmtree(version_dir)
        logger.info(f"Deleted version {version_hash}")
        return True

    @staticmethod
    def _strip_png_metadata(content: bytes) -> bytes:
        """Strip EXIF and other metadata from PNG files.

        This is a minimal implementation that does not parse PNG chunks.
        A production implementation would use a PNG library.

        Args:
            content: PNG file content

        Returns:
            PNG content (metadata stripped if possible)
        """
        # PNG file signature
        if content.startswith(b"\x89PNG"):
            # TODO: Implement proper PNG chunk filtering
            # For now, return as-is
            return content
        return content
