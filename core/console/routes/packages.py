"""Console API routes for package management (ADR-0268 Phase 1)."""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

from core.package_manager import PackageManager
from core.package_manager.validators import ValidationError

packages_bp = Blueprint("packages", __name__, url_prefix="/api/v1/packages")


@packages_bp.route("/upload", methods=["POST"])
def upload_package():
    """
    Upload and install a skill package.

    Requires multipart form with 'file' field containing ZIP.

    Returns:
        - 202: Accepted, returns package metadata + permissions requiring approval
        - 400: Invalid ZIP / manifest / dependencies
        - 409: Package already installed
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not file.filename.endswith(".zip"):
        return jsonify({"error": "File must be a ZIP archive"}), 400

    try:
        # Get tenant from session or default
        tenant_id = request.headers.get("X-Tenant-ID", "_default")
        manager = PackageManager(tenant_id)

        # Save uploaded file to temp location
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        # Load package (validates, extracts, checks deps)
        try:
            pkg = manager.load_from_zip(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # Extract permissions for approval modal
        permissions = pkg.manifest.get("permissions", [])

        return (
            jsonify(
                {
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
            ),
            202,
        )

    except ValidationError as e:
        return (
            jsonify(
                {
                    "error": e.message,
                    "field": e.field,
                    "details": e.details or {},
                }
            ),
            400,
        )
    except Exception as e:
        return (
            jsonify({"error": f"Package installation failed: {str(e)}"}),
            400,
        )


@packages_bp.route("", methods=["GET"])
def list_packages():
    """
    List all installed packages.

    Returns:
        200: List of installed packages
    """
    tenant_id = request.headers.get("X-Tenant-ID", "_default")
    manager = PackageManager(tenant_id)

    packages = manager.list_packages()
    return jsonify(
        {
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
    ), 200


@packages_bp.route("/<package_id>", methods=["DELETE"])
def delete_package(package_id: str):
    """
    Uninstall (delete) a skill package.

    Returns:
        204: Success
        404: Package not found
    """
    try:
        tenant_id = request.headers.get("X-Tenant-ID", "_default")
        manager = PackageManager(tenant_id)

        manager.unload_package(package_id)
        return "", 204

    except ValueError:
        return jsonify({"error": f"Package not found: {package_id}"}), 404
    except Exception as e:
        return jsonify({"error": f"Uninstall failed: {str(e)}"}), 400


@packages_bp.route("/<package_id>/details", methods=["GET"])
def get_package_details(package_id: str):
    """
    Get full metadata for an installed package.

    Returns:
        200: Package metadata
        404: Package not found
    """
    try:
        tenant_id = request.headers.get("X-Tenant-ID", "_default")
        manager = PackageManager(tenant_id)

        pkg = manager.get_package(package_id)
        if not pkg:
            return jsonify({"error": f"Package not found: {package_id}"}), 404

        return (
            jsonify(
                {
                    "id": pkg.id,
                    "version": pkg.version,
                    "path": pkg.path,
                    "installed_at": pkg.installed_at,
                    "enabled": pkg.enabled,
                    "manifest": pkg.manifest,
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 400
