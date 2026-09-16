"""
Skill Forge v2.0 Distribution Endpoints

HTTP routes for:
- Phase 3: Package Skill → ZIP
- Phase 4: Install Skill from ZIP
- Phase 6: Download from marketplace

ADR-0677: Skill Package & ZIP Distribution
License: Apache-2.0
"""

from fastapi import APIRouter, HTTPException, File, UploadFile, Query
from pathlib import Path
from typing import Dict, Optional
import json
import logging
from datetime import datetime

from core.skills.skill_packager import SkillPackager
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
                "packaged_at": "2026-09-16T10:00:00Z",
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
                    "created_at": "2026-09-16T10:00:00Z",
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
async def download_package(filename: str):
    """
    Download a packaged Skill ZIP file.

    Args:
        filename: ZIP filename (e.g., "my_awesome_skill_1.0.0.zip")

    Returns:
        ZIP file (binary)

    Raises:
        HTTPException(404): Package not found
    """
    from fastapi.responses import FileResponse

    try:
        packages_dir = Path.home() / ".corvin" / "skills_packages"
        zip_path = packages_dir / filename

        if not zip_path.exists():
            raise HTTPException(status_code=404, detail=f"Package not found: {filename}")

        return FileResponse(
            path=zip_path,
            filename=filename,
            media_type="application/zip",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error downloading package: {filename}")
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
        packages_dir = Path.home() / ".corvin" / "skills_packages"
        zip_filename = f"{skill_id}_{version}.zip"
        zip_path = packages_dir / zip_filename

        if not zip_path.exists():
            raise HTTPException(status_code=404, detail=f"Package not found: {zip_filename}")

        # Read metadata from .forge/ inside ZIP
        import zipfile

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


def register_skill_forge_routes(app):
    """Register all Skill Forge v2.0 distribution routes."""
    app.include_router(router)
