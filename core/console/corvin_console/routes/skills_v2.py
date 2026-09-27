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

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — the
router is not mounted by ``corvin_console.app``; the working skill install /
uninstall / list surface is ``routes/skill_manager.py`` (``/skills-manager``).

NOT IMPLEMENTED (defused 2026-09-27): every handler returned MOCK data — a
hard-coded skill list, a fabricated install "task id" (audited as a real
``skill.install``), and a ``/status`` that derived "complete" from an md5 of
the task id. They now answer 501 ``not_implemented`` and audit nothing.
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
from ..deps import require_csrf, require_session

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

    ``SessionRecord`` has no ``user`` attribute — the previous
    ``rec.user is not None`` raised AttributeError (HTTP 500) on every
    mutation. The console's tier is the admin signal, as in skill_manager.
    """
    return getattr(rec, "tier", None) in {"owner", "admin"}


def require_admin(rec: session_auth.SessionRecord = Depends(require_csrf)) -> session_auth.SessionRecord:
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


# ``_emit_skill_audit`` removed 2026-09-27: it called ``console_audit.emit_audit``,
# which does not exist, and only ever recorded fabricated actions.


def _not_implemented() -> HTTPException:
    return HTTPException(
        status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
        detail={"status": "not_implemented",
                "reason": "use /v1/console/skills-manager for skill install/uninstall/list"},
    )


@router.get("/installed")
async def list_installed_skills(rec: session_auth.SessionRecord = Depends(require_session)):
    """NOT IMPLEMENTED (501) — see /skills-manager/skills/installed."""
    raise _not_implemented()


@router.get("/available")
async def list_available_skills(rec: session_auth.SessionRecord = Depends(require_session)):
    """NOT IMPLEMENTED (501) — see /marketplace/skills/search."""
    raise _not_implemented()


@router.post("/install")
async def install_skill(
    req: InstallSkillRequest,
    rec: session_auth.SessionRecord = Depends(require_admin),
):
    """NOT IMPLEMENTED (501) — nothing is installed, nothing is audited."""
    raise _not_implemented()


@router.post("/uninstall")
async def uninstall_skill(
    req: UninstallSkillRequest,
    rec: session_auth.SessionRecord = Depends(require_admin),
):
    """NOT IMPLEMENTED (501)."""
    raise _not_implemented()


@router.post("/upload")
async def upload_skill(
    file: UploadFile = File(...),
    rec: session_auth.SessionRecord = Depends(require_admin),
):
    """NOT IMPLEMENTED (501) — see /skills-manager/skills/install."""
    raise _not_implemented()


@router.get("/status")
async def get_skill_status(
    task_id: str,
    rec: session_auth.SessionRecord = Depends(require_session),
):
    """NOT IMPLEMENTED (501) — there are no install tasks to report on."""
    raise _not_implemented()
