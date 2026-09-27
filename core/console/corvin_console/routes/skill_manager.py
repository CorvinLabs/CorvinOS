"""Phase 5: Console Skill Manager API Routes (ADR-0681) — FastAPI

Mounted at ``/v1/console/skills-manager``. Every route needs a live console
session; install/uninstall additionally need the CSRF token and the owner tier.

These routes used to carry no dependency at all: any local process — and any
web page the operator visited, since a multipart POST is a CORS "simple
request" that needs no preflight — could install or remove skills. They also
returned raw exception text and the absolute registry path to the client.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from forge import paths as _forge_paths

from core.skills.skill_installer import SkillInstaller

from .. import audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session_csrf_on_mutation

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

_MAX_ZIP_BYTES = 100 * 1024 * 1024


def installed_skills_root() -> Path:
    """The host's installed-skill store — the same directory SkillInstaller
    defaults to, but resolved through ``corvin_home()`` so ``CORVIN_HOME`` is
    honoured (a hard-wired ``~/.corvin`` let tests write into the live install)."""
    return _forge_paths.corvin_home() / "skills_installed"


def get_installer() -> SkillInstaller:
    return SkillInstaller(installed_skills_root())


def _owner_mutation(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> session_auth.SessionRecord:
    if getattr(rec, "tier", None) not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="owner or admin required")
    return rec


Mutation = Annotated[session_auth.SessionRecord, Depends(_owner_mutation)]


@router.get("/skills/installed", tags=["skill-manager"])
async def list_installed_skills():
    """List all installed skills."""
    try:
        registry = get_installer()._load_registry()
    except Exception:
        log.exception("skill registry unreadable")
        raise HTTPException(status_code=500, detail="skill registry unreadable")
    skills = []
    for skill_id, versions in registry.items():
        for v in versions:
            skills.append({
                "skill_id": skill_id,
                "version": v.get("version"),
                "boot_layer": v.get("boot_layer", "installed"),
                "verified": v.get("verified", False),
            })
    return {"skills": skills, "total": len(skills)}


@router.post("/skills/install", tags=["skill-manager"])
async def install_skill(
    rec: Mutation,
    file: UploadFile = File(...),
    skill_id: str = Form(...),
    version: str = Form(...),
):
    """Install skill from uploaded ZIP."""
    if not (file.filename or "").endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be ZIP")
    content = await file.read(_MAX_ZIP_BYTES + 1)
    if len(content) > _MAX_ZIP_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 100 MB limit")

    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        success, msg = get_installer().install_skill(
            Path(tmp_name),
            hashlib.sha256(content).hexdigest(),
            {"skill_id": skill_id, "version": version},
        )
    except Exception:
        log.exception("skill install failed")
        success, msg = False, "install failed"
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    target = f"{skill_id}@{version}"[:200]
    if not success:
        audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="skill.install", target_kind="skill", target_id=target,
            reason="install_refused",
        )
        # SkillInstaller wraps unexpected errors as "Error: <exception text>";
        # that text (paths, OS errors) stays in the log, not in the response.
        detail = "install failed" if msg.startswith("Error:") else msg
        raise HTTPException(status_code=400, detail=detail)
    audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="skill.install", target_kind="skill", target_id=target,
    )
    return {"success": True, "message": msg}


@router.delete("/skills/uninstall/{skill_id}/{version}", tags=["skill-manager"])
async def uninstall_skill(skill_id: str, version: str, rec: Mutation):
    """Uninstall skill version."""
    success, msg = get_installer().uninstall_skill(skill_id, version)
    target = f"{skill_id}@{version}"[:200]
    if not success:
        audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="skill.uninstall", target_kind="skill", target_id=target,
            reason="uninstall_refused",
        )
        detail = "uninstall failed" if msg.startswith("Error:") else msg
        raise HTTPException(status_code=400, detail=detail)
    audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="skill.uninstall", target_kind="skill", target_id=target,
    )
    return {"success": True, "message": msg}


@router.get("/skills/health", tags=["skill-manager"])
async def health_check():
    """Health check for skill manager."""
    try:
        registry = get_installer()._load_registry()
    except Exception:
        log.exception("skill registry unreadable")
        raise HTTPException(status_code=500, detail={"status": "error"})
    return {"status": "ok", "installed_count": len(registry)}
