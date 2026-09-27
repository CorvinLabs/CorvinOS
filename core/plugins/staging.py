"""Plugin Staging Manager — ZIP validation and atomic storage (ADR-0511)

Responsibilities:
  - Validate ZIP file structure and manifest
  - Compute file hashes (SHA256)
  - Atomic staging + metadata storage
  - Move to installed location after approval
  - Cleanup on rejection

All paths use pathlib.Path (Windows-safe, no string concatenation).
Fail-closed: raise StagingError on any validation failure.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forge import paths as _forge_paths


class StagingError(Exception):
    """Staging operation failed."""

    pass


class StagingManager:
    """Manages plugin ZIP staging, validation, and installation.

    Args:
        tenant_id: Tenant identifier (mandatory, validated)

    Raises:
        StagingError: Invalid tenant_id or I/O failure
    """

    def __init__(self, tenant_id: str) -> None:
        """Initialize staging manager for a tenant."""
        from forge.tenant import validate_tenant_id

        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.staging_root = _forge_paths.tenant_global_dir(tenant_id) / "plugin_staging"
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def validate_zip_file(self, zip_path: Path) -> tuple[bool, dict[str, Any], list[str]]:
        """Validate ZIP file structure and extract manifest.

        Returns:
            (is_valid: bool, manifest: dict, errors: list[str])

        Raises:
            StagingError: ZIP is corrupt or unreadable
        """
        errors: list[str] = []
        manifest: dict[str, Any] = {}

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Check for manifest.json
                manifest_path = None
                for name in zf.namelist():
                    if name.endswith("manifest.json"):
                        manifest_path = name
                        break

                if not manifest_path:
                    errors.append("manifest.json not found in ZIP")
                    return (False, {}, errors)

                # Read manifest
                try:
                    manifest_data = zf.read(manifest_path).decode("utf-8")
                    manifest = json.loads(manifest_data)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    errors.append(f"manifest.json invalid: {e}")
                    return (False, {}, errors)

                # Validate manifest schema
                required_fields = ["name", "version", "author"]
                for field in required_fields:
                    if field not in manifest:
                        errors.append(f"Missing required field: {field}")

                # Validate field types
                if not isinstance(manifest.get("name"), str):
                    errors.append("name must be string")
                if not isinstance(manifest.get("version"), str):
                    errors.append("version must be string")

                # Check for disallowed files (fail-closed)
                disallowed_extensions = [".exe", ".sh", ".bat", ".cmd"]
                for name in zf.namelist():
                    for ext in disallowed_extensions:
                        if name.lower().endswith(ext):
                            errors.append(f"Disallowed file type: {ext}")
                            break

        except zipfile.BadZipFile:
            errors.append("ZIP file is corrupt or invalid")
            return (False, {}, errors)
        except Exception as e:
            errors.append(f"ZIP validation error: {type(e).__name__}")
            return (False, {}, errors)

        is_valid = len(errors) == 0
        return (is_valid, manifest, errors)

    def compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file.

        Args:
            file_path: Path to file

        Returns:
            Hex-encoded SHA256 hash

        Raises:
            StagingError: File not readable
        """
        try:
            sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except OSError as e:
            raise StagingError(f"Cannot compute hash: {e}") from e

    def store_staged_upload(
        self,
        upload_id: str,
        file_path: Path,
        manifest: dict[str, Any],
    ) -> Path:
        """Store validated ZIP in staging area with metadata.

        Args:
            upload_id: Unique upload identifier
            file_path: Path to validated ZIP file
            manifest: Extracted manifest dict

        Returns:
            Path to stored ZIP file

        Raises:
            StagingError: Storage failed
        """
        try:
            # Atomic move to staging
            staged_zip = self.staging_root / f"{upload_id}.zip"
            file_path.replace(staged_zip)

            # Write metadata (no raw file contents, PII-safe)
            metadata = {
                "upload_id": upload_id,
                "file_name": file_path.name,
                "file_size": staged_zip.stat().st_size,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "pending_approval",
                "manifest": {
                    "name": manifest.get("name", ""),
                    "version": manifest.get("version", ""),
                    "author": manifest.get("author", ""),
                },
                "errors": [],
                "tenant_id": self.tenant_id,
            }

            metadata_path = self.staging_root / f"{upload_id}.meta"
            metadata_path.write_text(json.dumps(metadata, indent=2))

            return staged_zip

        except OSError as e:
            raise StagingError(f"Failed to store upload: {e}") from e

    def get_staged_upload(self, upload_id: str) -> dict[str, Any] | None:
        """Retrieve metadata for a staged upload.

        Returns:
            Metadata dict or None if not found
        """
        try:
            metadata_path = self.staging_root / f"{upload_id}.meta"
            if not metadata_path.exists():
                return None
            return json.loads(metadata_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def delete_staged_upload(self, upload_id: str) -> None:
        """Delete staged upload and metadata.

        Raises:
            StagingError: Deletion failed
        """
        try:
            zip_path = self.staging_root / f"{upload_id}.zip"
            meta_path = self.staging_root / f"{upload_id}.meta"

            if zip_path.exists():
                zip_path.unlink()
            if meta_path.exists():
                meta_path.unlink()

        except OSError as e:
            raise StagingError(f"Failed to delete upload: {e}") from e

    def move_to_installed(
        self,
        upload_id: str,
        installer_result: tuple[bool, str],
    ) -> Path:
        """Move approved upload to installed location.

        Args:
            upload_id: Upload identifier
            installer_result: (success: bool, message: str)

        Returns:
            Path to installed plugin

        Raises:
            StagingError: Move failed or installer returned error
        """
        try:
            success, message = installer_result
            if not success:
                raise StagingError(f"Installation failed: {message}")

            zip_path = self.staging_root / f"{upload_id}.zip"
            meta_path = self.staging_root / f"{upload_id}.meta"

            if not zip_path.exists():
                raise StagingError("Staged file not found")

            # Get manifest info
            metadata = self.get_staged_upload(upload_id)
            if not metadata:
                raise StagingError("Metadata not found")

            # Move to installed directory
            installed_dir = (
                _forge_paths.tenant_global_dir(self.tenant_id)
                / "plugins_installed"
            )
            installed_dir.mkdir(parents=True, exist_ok=True)

            installed_zip = installed_dir / f"{upload_id}.zip"
            zip_path.replace(installed_zip)

            # Cleanup metadata
            if meta_path.exists():
                meta_path.unlink()

            return installed_zip

        except OSError as e:
            raise StagingError(f"Failed to move to installed: {e}") from e
