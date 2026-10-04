"""
Skill Forge v2.0 Distribution Endpoints

HTTP routes for:
- Phase 3: Package Skill → ZIP
- Phase 4: Install Skill from ZIP
- Phase 6: Download from marketplace

ADR-0674: Skill Package & ZIP Distribution
License: Apache-2.0
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Dict, Optional
import json
import logging
import re
import zipfile
from datetime import datetime

from forge import paths as _forge_paths

from core.skills.skill_packager import SkillPackager, ChecksumVerificationError
from core.skills.skill_installer import SkillInstaller, InstallationError, _is_safe_segment
from core.skills.manifest_v2 import SkillManifestV2
from typing import Annotated, Any

# ADR-0892 — every route on this router carried NO session dependency
# (2026-09-19 finding): ``POST /install`` accepted a ``url`` or an absolute
# ``zip_path`` and unpacked it into the operator's skill root for anyone who
# could reach the port. Reads require a session, writes a CSRF-signed one —
# the same doors as every other console route (deps.py).
from .. import audit as console_audit
from ..deps import require_csrf, require_session
from .skill_manager import get_installer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/skill-forge", tags=["skill-forge-v2"])


# Roots — resolved through ``corvin_home()`` so ``CORVIN_HOME`` is honoured.
# They were hard-wired to ``~/.corvin``, which on a test run or a relocated
# install read and wrote the operator's LIVE home. The installed-skill store is
# the SAME one ``/skills-manager`` manages (``skill_manager.get_installer``).
def _skills_gen_root() -> Path:
    return _forge_paths.corvin_home() / "skills_gen"


def _packages_root() -> Path:
    return _forge_paths.corvin_home() / "skills_packages"


def _audit(rec: Any, action: str, target: str, *, ok: bool = True, reason: str = "") -> None:
    """Content-free record on the SESSION tenant's chain (console audit helper).

    Replaces ``_emit_audit_event``, which imported a module that does not
    exist (every distribution action went unaudited) and stamped every record
    ``tenant_id=_default`` whatever the caller's tenant.
    """
    kwargs = dict(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action=f"skill_forge.{action}", target_kind="skill", target_id=target[:200],
    )
    if ok:
        console_audit.action_performed(**kwargs)
    else:
        console_audit.action_failed(**kwargs, reason=reason or "refused")


def _owner(rec: Any) -> None:
    if getattr(rec, "tier", None) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="owner or admin required")


def _manifest_from_zip(zip_file: Path) -> Dict:
    """``{skill_id, version, dependencies}`` from the package's own ``skill.json``.

    A package produced by ``/package`` holds exactly one top-level directory
    ``<skill_id>/`` with ``skill.json`` in it. The installer is told what the
    ARCHIVE says it is — never a caller-supplied name.
    """
    try:
        with zipfile.ZipFile(zip_file) as zf:
            tops = {n.split("/", 1)[0] for n in zf.namelist() if n.strip("/")}
            if len(tops) != 1:
                raise HTTPException(status_code=400, detail="package must contain exactly one skill folder")
            top = next(iter(tops))
            try:
                data = json.loads(zf.read(f"{top}/skill.json").decode("utf-8"))
            except KeyError:
                raise HTTPException(status_code=400, detail="package has no skill.json") from None
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="not a ZIP archive") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="skill.json is not valid JSON") from None
    skill_id, version = data.get("skill_id"), data.get("version")
    if top != skill_id or not _is_safe_segment(skill_id) or not _is_safe_segment(version):
        raise HTTPException(status_code=400, detail="invalid skill_id/version in skill.json")
    deps = data.get("dependencies") or []
    if isinstance(deps, dict):  # {"other_skill": "1.x"} form
        deps = [{"skill_id": k, "version": v} for k, v in deps.items()]
    return {"skill_id": skill_id, "version": version, "dependencies": list(deps)}


@router.post("/package")
async def package_skill(
    rec: Annotated[Any, Depends(require_csrf)],
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
        # Resolve Skill folder — only inside the skills_gen root: packaging
        # WRITES ``.forge/`` metadata into the folder, so an arbitrary
        # ``skill_path`` let a request write into any directory the process can.
        if not _is_safe_segment(skill_id):
            raise HTTPException(status_code=400, detail="invalid skill_id")
        root = _skills_gen_root().resolve()
        skill_folder = (Path(skill_path) if skill_path else root / skill_id).resolve()
        if skill_folder.parent != root:
            raise HTTPException(status_code=400, detail="skill_path must be a folder directly under the skills_gen root")

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
        packager = SkillPackager(_packages_root())
        zip_path, zip_hash, metadata = packager.package(skill_folder, manifest)
        metadata.pop("zip_path", None)  # absolute host path — not for the client

        logger.info(f"Packaged Skill: {skill_id} v{manifest.version}")
        _audit(rec, "package", f"{manifest.skill_id}@{manifest.version}")

        return {
            "zip_url": f"/v1/skill-forge/download/{zip_path.name}",
            "zip_hash": zip_hash,
            "metadata": metadata,
        }

    except HTTPException:
        raise
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail="package already exists")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception(f"Error packaging Skill: {skill_id}")
        raise HTTPException(status_code=500, detail="packaging failed")


@router.get("/packages")
async def list_packages(rec: Annotated[Any, Depends(require_session)]) -> Dict:
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
        packages_dir = _packages_root()
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

    except Exception:
        logger.exception("Error listing packages")
        raise HTTPException(status_code=500, detail="listing packages failed")


@router.get("/download/{filename}")
async def download_package(filename: str, rec: Annotated[Any, Depends(require_session)], request: Request = None):
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

        zip_path = _packages_root() / filename

        if not zip_path.exists():
            raise HTTPException(status_code=404, detail=f"Package not found: {filename}")

        _audit(rec, "download", filename)

        return FileResponse(
            path=zip_path,
            filename=filename,
            media_type="application/zip",
            headers={"X-Skill-Hash": SkillPackager._compute_file_hash(zip_path)}
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error downloading package: {filename}")
        raise HTTPException(status_code=500, detail="download failed")


@router.post("/install")
async def install_skill(
    rec: Annotated[Any, Depends(require_csrf)],
    zip_path: Optional[str] = Query(None, description="Package in the packages root (filename or path)"),
    zip_hash: Optional[str] = Query(None, description="Expected sha256 of the ZIP (from /package or X-Skill-Hash)"),
    url: Optional[str] = Query(None, description="Not supported — see below"),
) -> Dict:
    """
    Install a packaged Skill into the host's installed-skill store.

    **Phase 4 Operation.** Owner/admin only. The package must sit in the
    packages root (where ``/package`` writes it); its identity comes from its
    own ``skill.json``; ``zip_hash`` is REQUIRED and checked before anything
    is unpacked — a hash computed from the same bytes it guards would verify
    nothing.

    ``url`` is refused (400): fetching an arbitrary URL from the console
    process bypassed the L35 egress policy. Upload a downloaded package through
    ``POST /v1/console/skills-manager/skills/install`` instead.

    Until 2026-09-27 this handler called a SkillInstaller API that no longer
    exists (``await installer.install_skill(path, installed_by=...)``) and
    answered 500 on every call.

    Returns:
        {"skill_id", "version", "status": "installed", "message"}

    Raises:
        HTTPException(400): bad request / invalid package / install refused
        HTTPException(403): not owner/admin
        HTTPException(404): package not found
    """
    _owner(rec)
    if url:
        raise HTTPException(status_code=400, detail="install from url is not supported; upload the package via /v1/console/skills-manager/skills/install")
    if not zip_path:
        raise HTTPException(status_code=400, detail="zip_path is required")
    if not zip_hash:
        raise HTTPException(status_code=400, detail="zip_hash is required")

    root = _packages_root().resolve()
    candidate = Path(zip_path)
    pkg_path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if pkg_path.parent != root or pkg_path.suffix != ".zip":
        raise HTTPException(status_code=400, detail="zip_path must name a .zip in the packages root")
    if not pkg_path.is_file():
        raise HTTPException(status_code=404, detail=f"Package not found: {pkg_path.name}")

    metadata = _manifest_from_zip(pkg_path)
    target = f"{metadata['skill_id']}@{metadata['version']}"
    try:
        ok, msg = get_installer().install_skill(pkg_path, zip_hash, metadata)
    except Exception:  # noqa: BLE001
        logger.exception("skill install failed")
        ok, msg = False, "Error: install failed"
    if not ok:
        _audit(rec, "install", target, ok=False, reason="install_refused")
        # SkillInstaller wraps unexpected errors as "Error: <exception text>";
        # that text stays in the log.
        raise HTTPException(status_code=400, detail="install failed" if msg.startswith("Error:") else msg)

    logger.info(f"Skill installed: {target}")
    _audit(rec, "install", target)
    return {
        "skill_id": metadata["skill_id"],
        "version": metadata["version"],
        "status": "installed",
        "message": msg,
    }


@router.get("/installed")
async def list_installed_skills(rec: Annotated[Any, Depends(require_session)]) -> Dict:
    """
    List all installed Skills (the same store ``/skills-manager`` manages).

    Returns:
        {"skills": [{"skill_id", "version", "boot_layer", "verified", "status"}]}

    ``status`` is ``healthy`` only when the version's directory exists on disk.
    """
    try:
        installer = get_installer()
        registry = installer._load_registry()
    except Exception:
        logger.exception("Error listing installed skills")
        raise HTTPException(status_code=500, detail="skill registry unreadable")

    skills = []
    for skill_id, versions in registry.items():
        for record in versions or []:
            version = record.get("version")
            present = bool(version) and (installer.install_root / skill_id / str(version)).is_dir()
            skills.append({
                "skill_id": skill_id,
                "version": version,
                "boot_layer": record.get("boot_layer", "installed"),
                "verified": bool(record.get("verified", False)),
                "status": "healthy" if present else "unhealthy",
            })
    return {"skills": skills}


@router.get("/packages/{skill_id}/{version}/metadata")
async def get_package_metadata(skill_id: str, version: str, rec: Annotated[Any, Depends(require_session)]) -> Dict:
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
        if not _is_safe_segment(skill_id) or not _is_safe_segment(version):
            raise HTTPException(status_code=400, detail="invalid skill_id or version")
        packages_dir = _packages_root()
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
    except Exception:
        logger.exception(f"Error retrieving metadata: {skill_id} v{version}")
        raise HTTPException(status_code=500, detail="reading package metadata failed")


def register_skill_forge_routes(app):
    """Register all Skill Forge v2.0 distribution routes."""
    app.include_router(router)
