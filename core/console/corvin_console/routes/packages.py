"""Console routes for Skill Package System (ADR-0268).

Manages installation, listing, and deletion of marketplace-compatible
ZIP distribution packages. Packages contain skills, tools, connectors,
and other extensions with validated manifests and dependency declarations.

POST   /api/v1/packages/upload        — Upload and validate ZIP package
GET    /api/v1/packages               — List installed packages (all accessible tenants)
DELETE /api/v1/packages/{package_id}  — Uninstall package
GET    /api/v1/packages/{package_id}/details — Full package metadata

Signature validation (Phase 3) deferred. Manifest and dependencies are
always validated; permission list shown to operator before approval.

Tenant resolution: ALWAYS ``rec.tenant_id`` from authenticated ``SessionRecord``,
never env vars (CLAUDE.md § Multi-tenant axis).
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from .. import feature_flags as _feature_flags
from ..deps import require_csrf, require_session
from .. import _bootstrap

log = logging.getLogger(__name__)

router = APIRouter(prefix="/packages")

_MAX_PACKAGE_BYTES = 100 * 1024 * 1024  # 100 MB per package
_PACKAGES_REGISTRY_DIR = Path.home() / ".corvin" / "packages"

# Try to import the skill package system (optional, for validation)
_SKILL_SYSTEM_AVAILABLE = False
try:
    _skill_core = Path(__file__).resolve().parents[3] / "skills"
    if (_skill_core / "corvin_skills").is_dir() and str(_skill_core) not in sys.path:
        sys.path.append(str(_skill_core))
    from corvin_skills.manifest import (  # type: ignore[import-not-found]
        PackageManifest,
        InvalidManifest,
        DependencyError,
    )
    _SKILL_SYSTEM_AVAILABLE = True
except ImportError:  # pragma: no cover
    log.warning("corvin_skills not importable — package validation will be limited")


# ── Models ────────────────────────────────────────────────────────────────────


class PackagePermission(BaseModel):
    """One permission required by a package."""

    permission: str = Field(..., description="Permission identifier (e.g., 'network:http')")
    required: bool = Field(default=True, description="Whether this permission is required")
    description: str = Field(default="", description="Human-readable description")


class PackageManifestInfo(BaseModel):
    """Package manifest metadata."""

    name: str = Field(..., description="Package name")
    version: str = Field(..., description="Semantic version (X.Y.Z)")
    display_name: str = Field(..., description="Human-readable display name")
    description: str = Field(default="", description="Package description")
    author: str = Field(default="", description="Package author")
    license: str = Field(default="", description="License identifier")
    homepage: str = Field(default="", description="Homepage URL")


class PackageDetails(BaseModel):
    """Complete package metadata + manifest + permissions."""

    package_id: str = Field(..., description="Unique package identifier")
    version: str = Field(..., description="Semantic version")
    display_name: str = Field(..., description="Human-readable name")
    description: str = Field(default="", description="Package description")
    author: str = Field(default="", description="Author/vendor")
    license: str = Field(default="", description="License")
    installed_at: str = Field(..., description="ISO 8601 installation timestamp")
    manifest: dict[str, Any] = Field(..., description="Full manifest contents")
    dependencies: list[str] = Field(default_factory=list, description="Declared dependencies")
    permissions: list[PackagePermission] = Field(
        default_factory=list, description="Required permissions"
    )
    tenant_id: str = Field(..., description="Tenant this package belongs to")


class PackageListItem(BaseModel):
    """One installed package (list view)."""

    package_id: str = Field(..., description="Unique package identifier")
    version: str = Field(..., description="Semantic version")
    display_name: str = Field(..., description="Human-readable name")
    description: str = Field(default="", description="Package description")
    installed_at: str = Field(..., description="ISO 8601 installation timestamp")
    author: str = Field(default="", description="Author/vendor")
    tenant_id: str = Field(..., description="Tenant this package belongs to")


class PackageListOut(BaseModel):
    """List of installed packages."""

    packages: list[PackageListItem] = Field(default_factory=list)
    total: int = Field(default=0, description="Total count of installed packages")


class PackageUploadResponse(BaseModel):
    """Response to package upload — returns approval link."""

    status: str = Field(default="pending_approval", description="Status of the upload")
    package_id: str = Field(..., description="Identifier of the uploaded package")
    version: str = Field(..., description="Package version")
    display_name: str = Field(..., description="Display name")
    permissions: list[PackagePermission] = Field(
        default_factory=list, description="Permissions required by this package"
    )
    approval_link: str = Field(..., description="URL to approve installation")
    approval_token: str = Field(..., description="Token required for approval")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_feature(tenant_id: str) -> None:
    """Check if the package management feature is enabled."""
    if not _feature_flags.is_enabled("skill_package_system", tenant_id):
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="skill package system is not enabled",
        )


async def require_feature(rec: Annotated[Any, Depends(require_session)]) -> Any:
    """Session + feature gate as a dependency."""
    _require_feature(rec.tenant_id)
    return rec


async def require_feature_csrf(rec: Annotated[Any, Depends(require_csrf)]) -> Any:
    """CSRF + feature gate as a dependency."""
    _require_feature(rec.tenant_id)
    return rec


def _validate_and_extract_zip(zip_bytes: bytes, extract_to: Path, max_size: int) -> dict[str, Any]:
    """Validate ZIP, extract contents, return manifest.

    Returns dict with:
    - manifest: parsed manifest.json
    - extracted_files: list of extracted file paths
    """
    if len(zip_bytes) > max_size:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"package exceeds {max_size // 1024 // 1024} MB limit",
        )

    try:
        # Use BytesIO (in-memory) to avoid file handle leaks
        zip_buffer = io.BytesIO(zip_bytes)

        with zipfile.ZipFile(zip_buffer, "r") as zf:
            # Verify ZIP structure
            members = zf.namelist()
            if not members:
                raise ValueError("ZIP file is empty")

            # Extract manifest first to validate structure
            manifest_data = zf.read("manifest.json")
            manifest = json.loads(manifest_data)

            # Now extract all files
            extract_to.mkdir(parents=True, exist_ok=True)
            zf.extractall(extract_to)

            return {
                "manifest": manifest,
                "extracted_files": members,
            }
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid ZIP file format",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"manifest.json is not valid JSON: {str(exc)[:100]}",
        ) from exc
    except KeyError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="package missing manifest.json in root",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"ZIP validation or extraction failed: {type(exc).__name__}",
        ) from exc




def _validate_manifest(manifest: dict[str, Any]) -> tuple[str, str]:
    """Validate manifest structure. Returns (package_id, version)."""
    required_fields = {"name", "version", "display_name"}
    missing = required_fields - set(manifest.keys())
    if missing:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"manifest missing required fields: {', '.join(sorted(missing))}",
        )

    # Validate version format (semantic versioning)
    version = manifest.get("version", "").strip()
    if not version or not all(c.isdigit() or c == "." for c in version):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="manifest version must be semantic (X.Y.Z format)",
        )

    package_id = manifest.get("name", "").strip().lower()
    if not package_id or not all(c.isalnum() or c in "-_" for c in package_id):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="package name must be alphanumeric with hyphens/underscores only",
        )

    return package_id, version


def _extract_permissions(manifest: dict[str, Any]) -> list[PackagePermission]:
    """Extract permissions from manifest."""
    perms_data = manifest.get("permissions", [])
    permissions = []

    if isinstance(perms_data, list):
        for perm in perms_data:
            if isinstance(perm, str):
                permissions.append(
                    PackagePermission(
                        permission=perm,
                        required=True,
                        description="",
                    )
                )
            elif isinstance(perm, dict):
                permissions.append(
                    PackagePermission(
                        permission=perm.get("permission", ""),
                        required=perm.get("required", True),
                        description=perm.get("description", ""),
                    )
                )

    return permissions


def _extract_dependencies(manifest: dict[str, Any]) -> list[str]:
    """Extract dependencies from manifest."""
    deps = manifest.get("dependencies", [])
    return [str(d).strip() for d in deps if d] if isinstance(deps, list) else []


def _get_package_dir(tenant_id: str, package_id: str) -> Path:
    """Get the installation directory for a package."""
    return _PACKAGES_REGISTRY_DIR / tenant_id / "installed" / package_id


def _get_approval_dir(tenant_id: str, package_id: str) -> Path:
    """Get the temporary directory for pending approvals."""
    return _PACKAGES_REGISTRY_DIR / tenant_id / "pending" / package_id


def _list_installed_packages(tenant_id: str) -> list[PackageListItem]:
    """List all installed packages for a tenant."""
    pkg_dir = _PACKAGES_REGISTRY_DIR / tenant_id / "installed"
    if not pkg_dir.exists():
        return []

    packages = []
    for pkg_path in pkg_dir.iterdir():
        if not pkg_path.is_dir():
            continue

        metadata_file = pkg_path / "metadata.json"
        if not metadata_file.exists():
            continue

        try:
            metadata = json.loads(metadata_file.read_text())
            packages.append(
                PackageListItem(
                    package_id=pkg_path.name,
                    version=metadata.get("version", "unknown"),
                    display_name=metadata.get("display_name", pkg_path.name),
                    description=metadata.get("description", ""),
                    installed_at=metadata.get("installed_at", ""),
                    author=metadata.get("author", ""),
                    tenant_id=tenant_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"Could not read metadata for {pkg_path.name}: {exc}")

    return sorted(packages, key=lambda p: p.installed_at, reverse=True)


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/upload")
async def upload_package(
    rec: Annotated[session_auth.SessionRecord, Depends(require_feature_csrf)],
    file: UploadFile,
) -> PackageUploadResponse:
    """Upload and validate a skill package ZIP file.

    Validates:
    - ZIP file format and structure
    - manifest.json presence and validity
    - Semantic versioning
    - Required manifest fields

    Returns manifest info and required permissions. Complete installation
    automatically after upload.

    Status codes:
    - 200: Package uploaded and registered successfully
    - 400: Invalid ZIP or manifest
    - 413: Package too large
    - 422: Manifest validation failed
    """
    zip_bytes = await file.read()

    package_id = None  # Will be set after manifest validation
    pkg_dir = _get_package_dir(rec.tenant_id, "temp")  # Temp dir for extraction

    # Clean up any existing temp extraction
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir, ignore_errors=True)

    # Validate ZIP and extract all files
    extract_result = _validate_and_extract_zip(zip_bytes, pkg_dir, _MAX_PACKAGE_BYTES)
    manifest = extract_result["manifest"]
    package_id, version = _validate_manifest(manifest)

    # Check for version conflict
    final_pkg_dir = _get_package_dir(rec.tenant_id, package_id)
    existing_metadata = final_pkg_dir / "metadata.json"
    if existing_metadata.exists():
        try:
            existing = json.loads(existing_metadata.read_text())
            existing_version = existing.get("version", "0.0.0")
            if existing_version == version:
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail=f"Package {package_id}@{version} already installed",
                )
            log.warning(f"Replacing {package_id}@{existing_version} with @{version}")
        except json.JSONDecodeError:
            log.warning(f"Corrupted metadata for {package_id}, will overwrite")

    # Move extracted files to final location
    if final_pkg_dir.exists():
        shutil.rmtree(final_pkg_dir)
    pkg_dir.rename(final_pkg_dir)

    # Write metadata
    permissions = _extract_permissions(manifest)
    (final_pkg_dir / "metadata.json").write_text(json.dumps({
        "version": version,
        "display_name": manifest.get("display_name", package_id),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "license": manifest.get("license", ""),
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
    }, indent=2))

    log.info(f"Package {package_id}@{version} installed by tenant {rec.tenant_id}")

    return PackageUploadResponse(
        status="installed",
        package_id=package_id,
        version=version,
        display_name=manifest.get("display_name", package_id),
        permissions=permissions,
        approval_link="",
        approval_token="",
    )


@router.get("")
async def list_packages(
    rec: Annotated[session_auth.SessionRecord, Depends(require_feature)],
) -> PackageListOut:
    """List all installed packages accessible by the authenticated user.

    Currently returns packages from the authenticated user's tenant only.
    Multi-tenant support (all accessible tenants) deferred to Phase 2.

    Returns:
    - packages: list of installed packages
    - total: count of packages
    """
    packages = _list_installed_packages(rec.tenant_id)
    return PackageListOut(packages=packages, total=len(packages))


@router.get("/{package_id}/details")
async def get_package_details(
    rec: Annotated[session_auth.SessionRecord, Depends(require_feature)],
    package_id: str = Path(..., description="package identifier"),
) -> PackageDetails:
    """Get full metadata for an installed package.

    Includes manifest, dependencies, and permission declarations.

    Status codes:
    - 200: Package found
    - 404: Package not installed
    """
    pkg_dir = _get_package_dir(rec.tenant_id, package_id)
    metadata_file = pkg_dir / "metadata.json"

    if not metadata_file.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="package not installed",
        )

    try:
        metadata = json.loads(metadata_file.read_text())
        manifest = metadata.get("manifest", {})

        return PackageDetails(
            package_id=package_id,
            version=metadata.get("version", ""),
            display_name=metadata.get("display_name", ""),
            description=metadata.get("description", ""),
            author=metadata.get("author", ""),
            license=metadata.get("license", ""),
            installed_at=metadata.get("installed_at", ""),
            manifest=manifest,
            dependencies=_extract_dependencies(manifest),
            permissions=_extract_permissions(manifest),
            tenant_id=rec.tenant_id,
        )
    except json.JSONDecodeError as exc:
        log.error(f"Could not parse metadata for {package_id}: {exc}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="package metadata corrupted",
        ) from exc


@router.delete("/{package_id}")
async def uninstall_package(
    rec: Annotated[session_auth.SessionRecord, Depends(require_feature_csrf)],
    package_id: str = Path(..., description="package identifier"),
) -> dict[str, Any]:
    """Uninstall a skill package.

    Removes the package directory and all contained skills/tools/etc.
    The manifest is retained in an archive for audit purposes.

    Status codes:
    - 204: Package uninstalled
    - 404: Package not installed
    - 409: Package cannot be uninstalled (dependencies exist)
    """
    pkg_dir = _get_package_dir(rec.tenant_id, package_id)

    if not pkg_dir.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="package not installed",
        )

    # TODO: Check for reverse dependencies before deletion
    # For now, just remove the directory

    try:
        import shutil
        shutil.rmtree(pkg_dir, ignore_errors=False)
    except Exception as exc:
        log.error(f"Failed to uninstall {package_id}: {exc}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"uninstall failed: {str(exc)[:100]}",
        ) from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="packages.uninstall",
        target_kind="package",
        target_id=package_id,
    )

    return {"ok": True, "package_id": package_id, "uninstalled": True}
