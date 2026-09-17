"""
Skill Forge v2.0 Distribution Endpoints

HTTP routes for:
- Phase 3: Package Skill → ZIP
- Phase 4: Install Skill from ZIP
- Phase 6: Download from marketplace

ADR-0674: Skill Package & ZIP Distribution
License: Apache-2.0
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Dict, Optional
import json
import logging
from datetime import datetime
import re

from core.skills.skill_packager import SkillPackager, ChecksumVerificationError
from core.skills.skill_installer import SkillInstaller, InstallationError
from core.skills.phase1_manifest_v2 import SkillManifestV2

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/skill-forge", tags=["skill-forge-v2"])


@router.post("/package")
async def package_skill(
    skill_id: str = Query(..., description="ID of Skill to package"),
    skill_path: Optional[str] = Query(None, description="Custom path to Skill folder"),
) -> Dict:
    """
    Package a complete Skill (Phase 1-2 output) into a ZIP archive.

    **Phase 3 Operation**

    Args:
        skill_id: Skill identifier (e.g., "my_awesome_skill")
        skill_path: Optional custom path; default: ~/.corvin/skills_gen/{skill_id}

    Returns:
        {
            "zip_url": "/v1/skill-forge/download/my_awesome_skill_1.0.0.zip",
            "zip_hash": "sha256:xyz789...",
            "metadata": {
                "skill_id": "my_awesome_skill",
                "version": "1.0.0",
                "packaged_at": "2026-09-17T10:00:00Z",
                "zip_path": "~/.corvin/skills_packages/...",
                "checksum_count": 42,
                "audit_trail_events": 8
            }
        }

    Raises:
        HTTPException(404): Skill not found
        HTTPException(400): Invalid Skill folder structure
        HTTPException(409): Package already exists
    """
    try:
        # Resolve Skill folder
        if skill_path:
            skill_folder = Path(skill_path)
        else:
            skill_folder = Path.home() / ".corvin" / "skills_gen" / skill_id

        if not skill_folder.exists():
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

        # Load manifest
        manifest_file = skill_folder / "skill.json"
        if not manifest_file.exists():
            raise HTTPException(
                status_code=400, detail=f"Missing manifest: {manifest_file}"
            )

        manifest_data = json.loads(manifest_file.read_text())
        manifest = SkillManifestV2.from_dict(manifest_data)

        # Package Skill
        packager = SkillPackager()
        zip_path, zip_hash, metadata = packager.package(skill_folder, manifest)

        logger.info(f"Packaged Skill: {skill_id} v{manifest.version}")

        return {
            "zip_url": f"/v1/skill-forge/download/{zip_path.name}",
            "zip_hash": zip_hash,
            "metadata": metadata,
        }

    except HTTPException:
        raise
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error packaging Skill: {skill_id}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get("/packages")
async def list_packages() -> Dict:
    """
    List all packaged Skills.

    Returns:
        {
            "packages": [
                {
                    "filename": "my_awesome_skill_1.0.0.zip",
                    "skill_id": "my_awesome_skill",
                    "version": "1.0.0",
                    "created_at": "2026-09-17T10:00:00Z",
                    "size_bytes": 45678,
                    "download_url": "/v1/skill-forge/download/..."
                }
            ]
        }
    """
    try:
        packages_dir = Path.home() / ".corvin" / "skills_packages"
        packages = []

        if packages_dir.exists():
            for zip_file in packages_dir.glob("*.zip"):
                stat = zip_file.stat()
                packages.append({
                    "filename": zip_file.name,
                    "skill_id": zip_file.stem.rsplit("_", 1)[0],  # Remove version
                    "version": zip_file.stem.split("_")[-1],
                    "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat() + "Z",
                    "size_bytes": stat.st_size,
                    "download_url": f"/v1/skill-forge/download/{zip_file.name}",
                })

        return {"packages": packages}

    except Exception as e:
        logger.exception("Error listing packages")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get("/download/{filename}")
async def download_package(filename: str, request: Request = None):
    """
    Download a packaged Skill ZIP file.

    Args:
        filename: ZIP filename (e.g., "my_awesome_skill_1.0.0.zip")

    Returns:
        ZIP file (binary)

    Raises:
        HTTPException(404): Package not found
    """
    try:
        # Validate filename (prevent directory traversal)
        if ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        packages_dir = Path.home() / ".corvin" / "skills_packages"
        zip_path = packages_dir / filename

        if not zip_path.exists():
            raise HTTPException(status_code=404, detail=f"Package not found: {filename}")

        # Extract skill_id and version for audit
        match = re.match(r"^([a-z0-9_]+)_([a-z0-9.]+)\.zip$", filename)
        if match:
            skill_id, version = match.groups()
            _emit_audit_event({
                "event_type": "skill_downloaded",
                "skill_id": skill_id,
                "version": version,
                "package_hash": SkillPackager._compute_file_hash(zip_path),
                "lom": "core.console.corvin_console.routes.skill_forge_distribution_routes:download_package:L158"
            })

        return FileResponse(
            path=zip_path,
            filename=filename,
            media_type="application/zip",
            headers={"X-Skill-Hash": SkillPackager._compute_file_hash(zip_path)}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error downloading package: {filename}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.post("/install")
async def install_skill(
    zip_path: Optional[str] = Query(None, description="Path to ZIP file"),
    url: Optional[str] = Query(None, description="URL to download ZIP from"),
    verify_checksum: bool = Query(True, description="Verify checksums before installation")
) -> Dict:
    """
    Install Skill from ZIP file or URL.

    **Phase 4 Operation**

    Args:
        zip_path: Local path to .zip file (OR)
        url: URL to download .zip from (OR upload via FormData)
        verify_checksum: Whether to verify checksums

    Returns:
        {
            "installed_path": "/home/user/.corvin/skills/custom/my_awesome_skill",
            "skill_id": "my_awesome_skill",
            "version": "1.0.0",
            "status": "success|already_installed|partial",
            "errors": []
        }

    Raises:
        HTTPException(400): Invalid request
        HTTPException(404): ZIP not found
        HTTPException(409): Installation conflict
        HTTPException(500): Installation error
    """
    try:
        if not zip_path and not url:
            raise HTTPException(status_code=400, detail="Either zip_path or url must be provided")

        # Resolve ZIP path
        if zip_path:
            pkg_path = Path(zip_path).expanduser()
            if not pkg_path.exists():
                raise HTTPException(status_code=404, detail=f"ZIP not found: {zip_path}")
        elif url:
            # Download from URL
            import httpx
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url, follow_redirects=True, timeout=300.0)
                    if response.status_code != 200:
                        raise HTTPException(status_code=400, detail=f"Failed to download from {url}")
                    pkg_path = Path("/tmp") / f"skill_download_{datetime.utcnow().timestamp()}.zip"
                    pkg_path.write_bytes(response.content)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Download failed: {str(e)}")
        else:
            raise HTTPException(status_code=400, detail="No ZIP source provided")

        # Install
        installer = SkillInstaller()
        result = await installer.install_skill(
            str(pkg_path),
            installed_by="http_api",
            verify_checksum=verify_checksum
        )

        logger.info(f"Skill installed: {result['skill_id']} v{result['version']} ({result['status']})")
        _emit_audit_event({
            "event_type": "skill_install_completed",
            "skill_id": result["skill_id"],
            "version": result["version"],
            "status": result["status"],
            "errors": result.get("errors", []),
            "lom": "core.console.corvin_console.routes.skill_forge_distribution_routes:install_skill:L261"
        })

        return result

    except HTTPException:
        raise
    except InstallationError as e:
        logger.exception(f"Installation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Installation error: {str(e)}")
    except Exception as e:
        logger.exception("Error installing skill")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get("/installed")
async def list_installed_skills() -> Dict:
    """
    List all installed Skills.

    Returns:
        {
            "skills": [
                {
                    "skill_id": "my_awesome_skill",
                    "version": "1.0.0",
                    "installed_at": "2026-09-17T12:34:56Z",
                    "path": "/home/user/.corvin/skills/custom/my_awesome_skill",
                    "status": "healthy|unhealthy"
                }
            ]
        }
    """
    try:
        installer = SkillInstaller()
        registry = await installer._load_registry()

        skills = []
        for record in registry.get("installed_skills", []):
            skill_dir = Path(record["path"]) if "path" in record else None
            if not skill_dir or not skill_dir.exists():
                skill_dir = installer.skills_dir / record["skill_id"]

            status = "healthy" if skill_dir.exists() else "unhealthy"

            skills.append({
                "skill_id": record["skill_id"],
                "version": record["version"],
                "installed_at": record["installed_at"],
                "path": str(skill_dir),
                "status": status,
                "boot_layer": record.get("boot_layer", "installed")
            })

        return {"skills": skills}

    except Exception as e:
        logger.exception("Error listing installed skills")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get("/packages/{skill_id}/{version}/metadata")
async def get_package_metadata(skill_id: str, version: str) -> Dict:
    """
    Retrieve metadata for a specific packaged Skill.

    Args:
        skill_id: Skill identifier
        version: Semver version (e.g., "1.0.0")

    Returns:
        {
            "generation_context": {...},
            "audit_trail": [...],
            "checksums": {...}
        }

    Raises:
        HTTPException(404): Package not found
    """
    try:
        import zipfile
        packages_dir = Path.home() / ".corvin" / "skills_packages"
        zip_filename = f"{skill_id}_{version}.zip"
        zip_path = packages_dir / zip_filename

        if not zip_path.exists():
            raise HTTPException(status_code=404, detail=f"Package not found: {zip_filename}")

        # Read metadata from .forge/ inside ZIP
        with zipfile.ZipFile(zip_path, "r") as zf:
            try:
                generation_context = json.loads(
                    zf.read(f"{skill_id}/.forge/generation_context.json").decode()
                )
                audit_trail = [
                    json.loads(line)
                    for line in zf.read(f"{skill_id}/.forge/audit_trail.jsonl").decode().split("\n")
                    if line
                ]
                checksums = {}
                for line in zf.read(f"{skill_id}/.forge/checksum.sha256").decode().split("\n"):
                    if line:
                        hash_val, file_path = line.split(" ", 1)
                        checksums[file_path] = hash_val

                return {
                    "generation_context": generation_context,
                    "audit_trail": audit_trail,
                    "checksums": checksums,
                }
            except KeyError as e:
                raise HTTPException(
                    status_code=500, detail=f"Corrupted package: missing metadata {e}"
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error retrieving metadata: {skill_id} v{version}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


def _emit_audit_event(event: Dict) -> None:
    """Emit audit event for skill distribution operations."""
    try:
        # Try to get audit backend from context
        from core.compliance.corvin_compliance_reports.audit_backend import get_audit_backend
        backend = get_audit_backend()
        if backend:
            event.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")
            event.setdefault("tenant_id", "_default")
            backend.emit_event(event)
    except Exception as e:
        logger.warning(f"Failed to emit audit event: {e}")


def register_skill_forge_routes(app):
    """Register all Skill Forge v2.0 distribution routes."""
    app.include_router(router)
