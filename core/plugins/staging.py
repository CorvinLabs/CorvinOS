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
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forge import paths as _forge_paths

log = logging.getLogger(__name__)

# upload ids are the first 16 hex chars of the package's sha256 (content-addressed).
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_DECISIONS = ("approved", "rejected")


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
        from forge.tenants import validate_tenant_id

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
                # Validate all entry names for path traversal (fail-closed)
                for entry_name in zf.namelist():
                    if ".." in entry_name or entry_name.startswith("/"):
                        errors.append(f"Disallowed path in ZIP entry: {entry_name}")
                        return (False, {}, errors)

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

    # ── decision memory (ADV-09, 2026-10-08) ─────────────────────────────
    # Both decisions remove the staging .meta (approve moves the package to
    # plugins_installed, reject deletes it), so "was this exact package
    # decided before?" had no answer and forge_bundle's "already decided"
    # refusal was unreachable: a rejected package came back as
    # pending_approval on every re-import of the same bundle. The decision is
    # kept per content-addressed upload id under plugin_staging/decisions/.
    # It is a MEMORY, not a lock: the owner's own manual re-upload is a new
    # decision and stays possible; only a bundle import refuses (import_module).

    @property
    def decisions_root(self) -> Path:
        return self.staging_root / "decisions"

    def record_decision(self, upload_id: str, decision: str) -> None:
        if decision not in _DECISIONS or not _UPLOAD_ID_RE.match(upload_id):
            raise StagingError("invalid decision record")
        self.decisions_root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.decisions_root, prefix=".decision-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump({"upload_id": upload_id, "decision": decision,
                           "decided_at": datetime.now(timezone.utc).isoformat()}, fh)
            os.replace(tmp, self.decisions_root / f"{upload_id}.json")
        except OSError as e:
            Path(tmp).unlink(missing_ok=True)
            raise StagingError(f"Failed to record decision: {e}") from e

    def get_decision(self, upload_id: str) -> str | None:
        """'approved' / 'rejected' when this exact package was decided before.

        An unreadable record is "no decision" (logged), NOT "rejected": the memory
        only ever stops a bundle from RE-PROPOSING a package, and a re-proposal still
        waits for the operator's approval — so failing open here costs one more
        approval prompt, while failing closed would block a legitimate plugin for good
        after a truncated write, with no way to clear it (R4-A9-2)."""
        if not _UPLOAD_ID_RE.match(upload_id):
            return None
        try:
            rec = json.loads((self.decisions_root / f"{upload_id}.json").read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log.warning("plugin decision record for %s is unreadable; treating it as undecided", upload_id)
            return None
        d = rec.get("decision") if isinstance(rec, dict) else None
        if d not in _DECISIONS:
            log.warning("plugin decision record for %s has no valid decision; treating it as undecided", upload_id)
            return None
        return d

    def withdraw_rejection(self, upload_id: str) -> bool:
        """The owner uploading a package themselves is a NEW decision: it withdraws a
        REJECTION. An approval stays — the package is installed, and an upload of it
        must not make the memory forget that (R4-A9-3)."""
        return self.get_decision(upload_id) == "rejected" and self.clear_decision(upload_id)

    def clear_decision(self, upload_id: str) -> bool:
        """Forget a decision. The owner's own manual upload of a package is a NEW
        decision, so it withdraws the memory (the only way to clear one)."""
        if not _UPLOAD_ID_RE.match(upload_id):
            return False
        try:
            (self.decisions_root / f"{upload_id}.json").unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            raise StagingError(f"Failed to clear decision: {e}") from e

    def reject_staged_upload(self, upload_id: str) -> None:
        """Operator rejection: remember it FIRST, then delete. If the memory
        cannot be written nothing is deleted and the rejection fails loudly."""
        # An approval is sticky: rejecting a re-upload of a package that is ALREADY
        # installed must not rewrite the memory to "rejected" while the installed ZIP
        # stays (R4-A9-3). A later manual re-upload clears it (clear_decision).
        if self.get_decision(upload_id) != "approved":
            self.record_decision(upload_id, "rejected")
        self.delete_staged_upload(upload_id)

    def delete_staged_upload(self, upload_id: str) -> None:
        """Delete staged upload and metadata.

        Deletes ZIP first, then metadata. If metadata delete fails after ZIP
        deletion, raises StagingError with clear indication of partial delete.

        Raises:
            StagingError: Deletion failed (may be partial)
        """
        zip_path = self.staging_root / f"{upload_id}.zip"
        meta_path = self.staging_root / f"{upload_id}.meta"

        zip_deleted = False
        try:
            if zip_path.exists():
                zip_path.unlink()
                zip_deleted = True
        except OSError as e:
            raise StagingError(f"Failed to delete ZIP: {e}") from e

        try:
            if meta_path.exists():
                meta_path.unlink()
        except OSError as e:
            if zip_deleted:
                raise StagingError(
                    f"Partial delete: ZIP deleted but metadata unlink failed: {e}"
                ) from e
            raise StagingError(f"Failed to delete metadata: {e}") from e

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

            # Remember the approval (ADV-09). After the move: the package IS
            # installed, so a failure here must not fail the approval; it only
            # means a later bundle import could propose it again — still behind
            # an operator approval. Logged, never silent.
            try:
                self.record_decision(upload_id, "approved")
            except StagingError as e:
                log.warning("plugin approval of %s not remembered: %s", upload_id, e)

            return installed_zip

        except OSError as e:
            raise StagingError(f"Failed to move to installed: {e}") from e
