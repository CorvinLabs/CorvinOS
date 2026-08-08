"""Console API routes for package management (ADR-0268 Phase 1)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Header, UploadFile, File
from fastapi.responses import JSONResponse

from core.package_manager import PackageManager
from core.package_manager.validators import ValidationError

router = APIRouter(prefix="/api/v1/packages", tags=["packages"])


@router.post("/upload", status_code=202)
async def upload_package(
    file: UploadFile = File(...),
    x_tenant_id: str = Header(default="_default", alias="X-Tenant-ID"),
):
    """
    Upload and install a skill package.

    Requires multipart form with 'file' field containing ZIP.

    Returns:
        - 202: Accepted, returns package metadata + permissions requiring approval
        - 400: Invalid ZIP / manifest / dependencies
        - 409: Package already installed
    """
    if not file.filename or not file.filename.endswith(".zip"):
        return JSONResponse(
            {"error": "File must be a ZIP archive"},
            status_code=400,
        )

    try:
        manager = PackageManager(x_tenant_id)

        # Save uploaded file to temp location
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Load package (validates, extracts, checks deps)
        try:
            pkg = manager.load_from_zip(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # Extract permissions for approval modal
        permissions = pkg.manifest.get("permissions", [])

        return {
            "status": "pending_approval",
            "package": {
                "id": pkg.id,
                "version": pkg.version,
                "name": pkg.manifest.get("name"),
                "path": pkg.path,
            },
            "permissions": permissions,
            "dependencies": pkg.manifest.get("dependencies", []),
        }

    except ValidationError as e:
        return JSONResponse(
            {
                "error": e.message,
                "field": e.field,
                "details": e.details or {},
            },
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"Package installation failed: {str(e)}"},
            status_code=400,
        )


@router.get("", status_code=200)
async def list_packages(
    x_tenant_id: str = Header(default="_default", alias="X-Tenant-ID"),
):
    """
    List all installed packages.

    Returns:
        200: List of installed packages
    """
    manager = PackageManager(x_tenant_id)

    packages = manager.list_packages()
    return {
        "packages": [
            {
                "id": pkg.id,
                "version": pkg.version,
                "name": pkg.manifest.get("name"),
                "installed_at": pkg.installed_at,
                "enabled": pkg.enabled,
            }
            for pkg in packages.values()
        ]
    }


@router.delete("/{package_id}", status_code=204)
async def delete_package(
    package_id: str,
    x_tenant_id: str = Header(default="_default", alias="X-Tenant-ID"),
):
    """
    Uninstall (delete) a skill package.

    Returns:
        204: Success
        404: Package not found
    """
    try:
        manager = PackageManager(x_tenant_id)
        manager.unload_package(package_id)
        return None

    except ValueError:
        return JSONResponse(
            {"error": f"Package not found: {package_id}"},
            status_code=404,
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"Uninstall failed: {str(e)}"},
            status_code=400,
        )


@router.get("/{package_id}/details", status_code=200)
async def get_package_details(
    package_id: str,
    x_tenant_id: str = Header(default="_default", alias="X-Tenant-ID"),
):
    """
    Get full metadata for an installed package.

    Returns:
        200: Package metadata
        404: Package not found
    """
    try:
        manager = PackageManager(x_tenant_id)

        pkg = manager.get_package(package_id)
        if not pkg:
            return JSONResponse(
                {"error": f"Package not found: {package_id}"},
                status_code=404,
            )

        return {
            "id": pkg.id,
            "version": pkg.version,
            "path": pkg.path,
            "installed_at": pkg.installed_at,
            "enabled": pkg.enabled,
            "manifest": pkg.manifest,
        }

    except Exception as e:
        return JSONResponse(
            {"error": str(e)},
            status_code=400,
        )
