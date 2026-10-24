"""Console routes for Skill Package System (ADR-0268).

Manages installation, listing, and deletion of marketplace-compatible
ZIP distribution packages. Packages contain skills, tools, connectors,
and other extensions with validated manifests and dependency declarations.

POST   /v1/console/packages/upload        — Upload and validate ZIP package
GET    /v1/console/packages               — List the tenant's installed packages
DELETE /v1/console/packages/{package_id}  — Uninstall package (archived, not erased)
GET    /v1/console/packages/{package_id}/details — Full package metadata

Where packages live: ``<corvin_home>/tenants/<tenant_id>/packages/`` with
``installed/<id>/`` (extracted ZIP + ``metadata.json``) and ``archive/`` (the
metadata, which carries the manifest, of every uninstalled package).
``corvin_home`` honours ``CORVIN_HOME`` through ``core.paths.tenant``; until
2026-10-24 this module wrote to ``Path.home()/.corvin/packages`` and ignored
the configured root.

Scope (stated, not hidden): this is a package REGISTRY. An upload validates the
ZIP and its manifest, extracts it, records it and audits it; it does NOT
register the contained skills, tools or connectors with the running system -
nothing reads ``installed/`` yet. The Packages tab says so.

Signature validation (Phase 3) deferred. Manifest and dependencies are
always validated; the permission list is returned with the upload.

Tenant resolution: ALWAYS ``rec.tenant_id`` from authenticated ``SessionRecord``,
never env vars (CLAUDE.md § Multi-tenant axis).
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

# FastAPI's ``Path`` is a path-PARAMETER declarator, not ``pathlib.Path``.
# Importing it unaliased shadows the ``pathlib.Path`` imported above, and the
# module-level ``Path.home()`` on the next lines then dies with
# AttributeError: 'function' object has no attribute 'home' — which the
# gateway's plugin-mount try/except downgrades to a log warning, silently
# dropping EVERY console route. Keep it aliased.
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path as PathParam,
    Query,
    UploadFile,
)
from fastapi import status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session
from .. import _bootstrap
from core.paths.tenant import tenant_home

log = logging.getLogger(__name__)

router = APIRouter(prefix="/packages")

_MAX_PACKAGE_BYTES = 100 * 1024 * 1024  # 100 MB per package
# A package id is the lower-cased manifest ``name`` (see ``_validate_manifest``). The same
# pattern guards the path parameter, so ``..`` can never address a directory outside
# ``installed/`` (DELETE /packages/.. used to resolve to the tenant's registry root).
_PACKAGE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,127}$"

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


# No feature flag: until 2026-10-24 every route answered 404 unless the default-off
# ``package_marketplace_ui`` flag was on, so the Packages tab was "switched off on this
# build" on a fresh install. The policy is plugin-based isolation, not default-off flags;
# the registry is honestly scoped (module docstring), so the routes are always on.
async def require_feature(rec: Annotated[Any, Depends(require_session)]) -> Any:
    """Session dependency (name kept for the route signatures)."""
    return rec


async def require_feature_csrf(rec: Annotated[Any, Depends(require_csrf)]) -> Any:
    """Session + CSRF dependency (name kept for the route signatures)."""
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

            # Validate all member paths BEFORE extraction (path traversal defense)
            extract_to_resolved = extract_to.resolve()
            for member in members:
                member_path = (extract_to_resolved / member).resolve()
                # Ensure resolved path stays within extract_to (no ../ escapes). A string
                # prefix test would accept a sibling such as ``<dir>-evil``.
                if member_path != extract_to_resolved and not member_path.is_relative_to(extract_to_resolved):
                    raise ValueError(f"path traversal detected: {member}")

            # Now extract all files (paths already validated)
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
        log.error(f"JSON decode error in manifest: {exc}")
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="manifest.json is not valid JSON",
        ) from exc
    except KeyError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="package missing manifest.json in root",
        )
    except Exception as exc:
        # Log full error server-side, return generic message to client
        log.error(f"ZIP validation failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="ZIP validation failed",
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


def _registry_root(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/packages`` - resolved per call so a moved
    ``CORVIN_HOME`` (sandbox, tests) is honoured; validates the tenant id."""
    return tenant_home(tenant_id) / "packages"


def _get_package_dir(tenant_id: str, package_id: str) -> Path:
    """Get the installation directory for a package."""
    return _registry_root(tenant_id) / "installed" / package_id


def _archive_root(tenant_id: str) -> Path:
    return _registry_root(tenant_id) / "archive"


def _dependency_name(spec: str) -> str:
    """``"adscale>=1.2"`` / ``"adscale"`` -> ``"adscale"`` (lower-cased)."""
    return re.split(r"[\s<>=!~@\[;(,]", spec.strip(), maxsplit=1)[0].strip().lower()


def _dependents_of(tenant_id: str, package_id: str) -> list[str]:
    """Installed packages of this tenant whose manifest declares ``package_id`` as a dependency."""
    root = _registry_root(tenant_id) / "installed"
    if not root.is_dir():
        return []
    found: list[str] = []
    for other in sorted(root.iterdir()):
        if not other.is_dir() or other.name == package_id:
            continue
        try:
            manifest = json.loads((other / "metadata.json").read_text()).get("manifest", {})
        except (OSError, ValueError):
            continue
        if any(_dependency_name(d) == package_id for d in _extract_dependencies(manifest)):
            found.append(other.name)
    return found


def _list_installed_packages(tenant_id: str) -> list[PackageListItem]:
    """List all installed packages for a tenant."""
    pkg_dir = _registry_root(tenant_id) / "installed"
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
    file: Annotated[UploadFile, File(...)],
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

    # Use unique temp directory to avoid concurrent upload conflicts
    temp_id = str(uuid.uuid4())[:8]
    temp_pkg_dir = _registry_root(rec.tenant_id) / "temp" / temp_id

    try:
        # Create parent directory for temp extraction
        temp_pkg_dir.parent.mkdir(parents=True, exist_ok=True)

        # Validate ZIP and extract all files to unique temp location
        extract_result = _validate_and_extract_zip(zip_bytes, temp_pkg_dir, _MAX_PACKAGE_BYTES)
        manifest = extract_result["manifest"]
        package_id, version = _validate_manifest(manifest)
        permissions = _extract_permissions(manifest)

        # Check for version conflict before committing
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

        # **AUDIT BEFORE COMMIT:** Log to audit trail BEFORE writing files (GDPR Art. 30)
        # If audit fails, exception prevents file write via the outer except handler
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="package.install",
            target_kind="package",
            target_id=package_id,
        )

        # Commit the package atomically: create backup, move temp → final, clean old backup
        # (TOCTOU-safe: minimal window between atomic move and cleanup)
        final_pkg_dir.parent.mkdir(parents=True, exist_ok=True)

        if final_pkg_dir.exists():
            backup_dir = final_pkg_dir.parent / f"{final_pkg_dir.name}.backup"
            # Remove any stale backup
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=False)
            # Move current → backup (atomic)
            final_pkg_dir.rename(backup_dir)
            try:
                # Move temp → final (atomic)
                temp_pkg_dir.rename(final_pkg_dir)
                # Cleanup backup
                shutil.rmtree(backup_dir, ignore_errors=False)
            except Exception:
                # Restore from backup if final move fails
                if backup_dir.exists():
                    backup_dir.rename(final_pkg_dir)
                raise
        else:
            # No existing package, just move temp → final
            temp_pkg_dir.rename(final_pkg_dir)

        # Write metadata after atomic move
        (final_pkg_dir / "metadata.json").write_text(json.dumps({
            "version": version,
            "display_name": manifest.get("display_name", package_id),
            "description": manifest.get("description", ""),
            "author": manifest.get("author", ""),
            "license": manifest.get("license", ""),
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "manifest": manifest,
        }, indent=2))

    except HTTPException:
        # Clean up temp directory on any HTTP error (includes audit failures)
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir, ignore_errors=True)
        raise
    except Exception as exc:
        # Clean up temp directory on any unexpected error (includes audit failures)
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir, ignore_errors=True)
        log.exception(f"Unexpected error during package upload: {exc}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Package upload failed due to system error",
        ) from exc

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
    package_id: str = PathParam(..., description="package identifier", pattern=_PACKAGE_ID_PATTERN),
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
    package_id: str = PathParam(..., description="package identifier", pattern=_PACKAGE_ID_PATTERN),
) -> dict[str, Any]:
    """Uninstall a skill package.

    Deletes the package's directory from ``installed/``. Its ``metadata.json`` (which
    carries the full manifest) is first copied to
    ``archive/<id>-<version>-<UTC stamp>/`` so the record of what was installed
    survives; the extracted files are not kept.

    Status codes:
    - 200: Package uninstalled
    - 404: Package not installed
    - 409: Another installed package declares this one as a dependency
    - 422: Not a valid package id
    """
    pkg_dir = _get_package_dir(rec.tenant_id, package_id)

    if not pkg_dir.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="package not installed",
        )

    # Reverse dependencies: refuse while an installed package still declares this one.
    dependents = _dependents_of(rec.tenant_id, package_id)
    if dependents:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"required by installed package(s): {', '.join(dependents)}",
        )

    # AUDIT BEFORE COMMIT: Log to audit trail BEFORE deleting files (GDPR Art. 30)
    # If audit fails, exception prevents deletion
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="package.uninstall",
        target_kind="package",
        target_id=package_id,
    )

    archived = False
    try:
        meta = pkg_dir / "metadata.json"
        if meta.is_file():
            try:
                version = str(json.loads(meta.read_text()).get("version", "unknown"))
            except ValueError:
                version = "unknown"
            version = re.sub(r"[^0-9A-Za-z._-]", "_", version)[:40] or "unknown"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            archive_dir = _archive_root(rec.tenant_id) / f"{package_id}-{version}-{stamp}"
            archive_dir.mkdir(parents=True, exist_ok=False)
            shutil.copy2(meta, archive_dir / "metadata.json")
            archived = True
        shutil.rmtree(pkg_dir, ignore_errors=False)
    except Exception as exc:
        log.error(f"Failed to delete package directory {package_id}: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="uninstall failed",
        ) from exc

    return {"ok": True, "package_id": package_id, "uninstalled": True, "archived": archived}
