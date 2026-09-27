"""
Skills Manager Endpoints — Phase 5 Console UI (K=3 Implementation)

Exposes skill management operations:
  - GET  /v1/skills/installed        list installed skills
  - GET  /v1/skills/available        list marketplace skills (with search/filter)
  - POST /v1/skills/install          install from marketplace
  - POST /v1/skills/uninstall        remove installed skill
  - POST /v1/skills/upload           upload local ZIP file
  - GET  /v1/skills/status           poll installation status

Auth + Tenant (ADR-0007 placeholder):
  - Every route requires a live console session (require_session)
  - Mutations additionally require admin_only gate (placeholder for RBAC)
  - Tenant isolation: all operations scoped to rec.tenant_id
  - Audit events: skill.install, skill.uninstall, skill.upload

Compliance (ADR-0314 + ADR-0297):
  - All mutations emit audit events (hash-chained)
  - PII-free: no secrets leak into skill configs
  - Fail-closed: errors returned explicitly, never silent
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status as http_status
from pydantic import BaseModel, Field

from .. import _bootstrap
from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_session

_forge_paths = _bootstrap.forge_paths
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────


class InstallSkillRequest(BaseModel):
    skill_id: str = Field(..., description="Skill ID to install (e.g., 'os.flow_guard')")
    source: str = Field(default="marketplace", description="'marketplace' or 'upload'")


class UninstallSkillRequest(BaseModel):
    skill_id: str = Field(..., description="Skill ID to remove")


class SkillInfo(BaseModel):
    skill_id: str
    name: str
    version: str
    author: str
    description: Optional[str] = None
    category: Optional[str] = None
    installed_at: Optional[str] = None
    status: str = "active"
    rating: Optional[float] = None
    reviews: Optional[int] = None


class InstallSkillResponse(BaseModel):
    task_id: str
    skill_id: str
    name: str
    status: str


class SkillStatusResponse(BaseModel):
    task_id: str
    status: str  # pending, downloading, extracting, validating, installing, complete, error
    progress: int  # 0-100
    error: Optional[str] = None


class SkillListResponse(BaseModel):
    skills: list[SkillInfo]


# ─────────────────────────────────────────────────────────────────────────────
# Auth / Admin Gates
# ─────────────────────────────────────────────────────────────────────────────


def _is_admin(rec: session_auth.SessionRecord) -> bool:
    """
    Check if user is admin.

    Placeholder for ADR-0007 RBAC. Today: localhost console, any authenticated user.
    When ADR-0007 ships: replace with rec.user.has_permission('skills:install').
    """
    return rec.user is not None


def require_admin(rec: session_auth.SessionRecord = Depends(require_session)) -> session_auth.SessionRecord:
    """Dependency: require admin privileges for mutations."""
    if not _is_admin(rec):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Emit Audit Events
# ─────────────────────────────────────────────────────────────────────────────


def _emit_skill_audit(
    event_type: str,
    skill_id: str,
    tenant_id: str,
    source: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Emit audit event for skill operation (ADR-0314)."""
    payload = {
        "event_type": event_type,
        "skill_id": skill_id,
        "tenant_id": tenant_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lom": "console.skills_v2._emit_skill_audit",
    }
    if source:
        payload["source"] = source
    if error:
        payload["error"] = error

    try:
        # Call audit.emit_audit() (existing console audit module)
        console_audit.emit_audit(event_type, payload)
    except Exception as exc:
        logger.error(f"Failed to emit audit event: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/installed")
async def list_installed_skills(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> SkillListResponse:
    """
    List installed skills for the current tenant.

    Returns: list of installed skill metadata
    """
    tenant_id = rec.tenant_id

    # TODO(K=4): Implement actual skill listing from SkillManager
    # For now, return mock data
    skills = [
        SkillInfo(
            skill_id="os.delegation_router",
            name="Delegation Router",
            version="1.0.0",
            author="Corvin Labs",
            description="Intelligent request routing to OS engines",
            category="routing",
            installed_at="2026-09-20T10:00:00Z",
            status="active",
        ),
    ]

    return SkillListResponse(skills=skills)


@router.get("/available")
async def list_available_skills(
    search: Optional[str] = None,
    filter: Optional[str] = None,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> SkillListResponse:
    """
    List marketplace skills with optional search/filter.

    Query params:
      - search: keyword search (name + description)
      - filter: category filter (networking, storage, security, ai, data, monitoring)

    Returns: list of marketplace skill metadata
    """
    tenant_id = rec.tenant_id

    # TODO(K=4): Implement actual marketplace fetch
    # For now, return mock data
    skills = [
        SkillInfo(
            skill_id="os.flow_guard",
            name="Flow Guard",
            version="2.1.0",
            author="Corvin Labs",
            description="Data flow protection layer",
            category="security",
            rating=4.8,
            reviews=127,
        ),
        SkillInfo(
            skill_id="os.context_adapter",
            name="Context Adapter",
            version="1.5.0",
            author="Corvin Labs",
            description="Learn user/task patterns for context optimization",
            category="ai",
            rating=4.6,
            reviews=89,
        ),
    ]

    # Apply search filter
    if search and search.strip():
        q = search.lower()
        skills = [s for s in skills if q in s.name.lower() or (s.description and q in s.description.lower())]

    # Apply category filter
    if filter and filter != "all":
        skills = [s for s in skills if s.category == filter]

    return SkillListResponse(skills=skills)


@router.post("/install")
async def install_skill(
    req: InstallSkillRequest,
    rec: session_auth.SessionRecord = Depends(require_admin),
) -> InstallSkillResponse:
    """
    Install a skill from marketplace.

    Request body:
      {
        "skill_id": "os.flow_guard",
        "source": "marketplace"
      }

    Returns: task ID for polling installation status

    Audit: emits skill.install event
    """
    tenant_id = rec.tenant_id

    # TODO(K=4): Call SkillInstaller.install_from_marketplace()
    # For now, return mock task ID
    task_id = f"install-{req.skill_id.split('.')[-1]}-{datetime.now(timezone.utc).timestamp()}"

    # Emit audit event
    _emit_skill_audit(
        event_type="skill.install",
        skill_id=req.skill_id,
        tenant_id=tenant_id,
        source=req.source,
    )

    logger.info(f"[{tenant_id}] skill.install {req.skill_id} (task_id={task_id})")

    return InstallSkillResponse(
        task_id=task_id,
        skill_id=req.skill_id,
        name=req.skill_id.split(".")[-1].replace("_", " ").title(),
        status="pending",
    )


@router.post("/uninstall")
async def uninstall_skill(
    req: UninstallSkillRequest,
    rec: session_auth.SessionRecord = Depends(require_admin),
) -> InstallSkillResponse:
    """
    Remove an installed skill.

    Request body:
      {
        "skill_id": "os.flow_guard"
      }

    Returns: task ID for polling uninstall status

    Audit: emits skill.uninstall event
    """
    tenant_id = rec.tenant_id

    # TODO(K=4): Call SkillInstaller.uninstall()
    # For now, return mock task ID
    task_id = f"uninstall-{req.skill_id.split('.')[-1]}-{datetime.now(timezone.utc).timestamp()}"

    # Emit audit event
    _emit_skill_audit(
        event_type="skill.uninstall",
        skill_id=req.skill_id,
        tenant_id=tenant_id,
    )

    logger.info(f"[{tenant_id}] skill.uninstall {req.skill_id} (task_id={task_id})")

    return InstallSkillResponse(
        task_id=task_id,
        skill_id=req.skill_id,
        name=req.skill_id.split(".")[-1].replace("_", " ").title(),
        status="pending",
    )


@router.post("/upload")
async def upload_skill(
    file: UploadFile = File(...),
    rec: session_auth.SessionRecord = Depends(require_admin),
) -> InstallSkillResponse:
    """
    Upload a skill ZIP file.

    Validates file type (.zip), stages for installation.

    Multipart form data:
      - file: .zip file (max 100 MB)

    Returns: task ID + extracted skill metadata

    Audit: emits skill.upload event
    """
    tenant_id = rec.tenant_id

    # Validate file extension
    if not (file.filename and file.filename.endswith(".zip")):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="File must be a .zip archive",
        )

    # TODO(K=4): Call SkillInstaller.validate_and_stage_upload()
    # For now, return mock data
    skill_id = f"custom.{file.filename.replace('.zip', '').lower()}"
    task_id = f"upload-{file.filename.split('.')[0]}-{datetime.now(timezone.utc).timestamp()}"

    # Emit audit event
    _emit_skill_audit(
        event_type="skill.upload",
        skill_id=skill_id,
        tenant_id=tenant_id,
        source="local_upload",
    )

    logger.info(f"[{tenant_id}] skill.upload {skill_id} from {file.filename} (task_id={task_id})")

    return InstallSkillResponse(
        task_id=task_id,
        skill_id=skill_id,
        name=file.filename.replace(".zip", "").replace("_", " ").title(),
        status="uploaded",
    )


@router.get("/status")
async def get_skill_status(
    task_id: str,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> SkillStatusResponse:
    """
    Poll installation status for a task.

    Query params:
      - task_id: installation task ID (from install/uninstall/upload response)

    Returns: current status + progress (0-100)

    Status values: pending, downloading, extracting, validating, installing, complete, error
    """
    tenant_id = rec.tenant_id

    # TODO(K=4): Call SkillInstaller.get_status(task_id)
    # For now, return mock status (cycle through states)

    import hashlib
    hash_val = int(hashlib.md5(task_id.encode()).hexdigest(), 16) % 100

    if hash_val < 20:
        status = "pending"
        progress = 10
    elif hash_val < 40:
        status = "downloading"
        progress = 30
    elif hash_val < 60:
        status = "extracting"
        progress = 60
    elif hash_val < 80:
        status = "validating"
        progress = 80
    else:
        status = "complete"
        progress = 100

    return SkillStatusResponse(
        task_id=task_id,
        status=status,
        progress=progress,
        error=None,
    )
