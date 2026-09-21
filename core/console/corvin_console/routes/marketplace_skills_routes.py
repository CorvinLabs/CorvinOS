"""
Marketplace Skills API — OS-Skills Discovery, Installation & Rating (ADR-0535+).

Provides endpoints for:
- Browsing available OS-Skills from the Corvin-Marketplace
- Installing skills to a tenant
- Rating and reviewing installed skills
- Querying skill status and metadata

Routes:
  GET  /v1/console/marketplace/skills            → List all available skills
  GET  /v1/console/marketplace/skills/search     → Search skills
  GET  /v1/console/marketplace/skills/{skill_id} → Get skill details + reviews
  POST /v1/console/marketplace/skills/{skill_id}/install → Install skill
  POST /v1/console/marketplace/skills/{skill_id}/uninstall → Uninstall skill
  POST /v1/console/marketplace/skills/{skill_id}/rate → Rate skill (1-5 stars)
  GET  /v1/console/marketplace/skills/{skill_id}/reviews → Get reviews

Tenant isolation: All skills are installed/scoped per-tenant.
Audit-first: Every install/uninstall/rating is logged to audit chain.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/console/marketplace/skills", tags=["marketplace-skills"])

# In-memory skill tracking
_MAX_JOBS = 256
_install_jobs: OrderedDict[str, InstallJob] = OrderedDict()
_jobs_lock = threading.Lock()

_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$", re.IGNORECASE)


class JobStatus(str, Enum):
    """Installation job status."""
    PENDING = "pending"
    INSTALLING = "installing"
    COMPLETED = "completed"
    FAILED = "failed"


class SkillTier(str, Enum):
    """Skill tier classification."""
    BUILDIN = "buildin"  # OS-integrated skills
    CONTRIBUTOR = "contributor"  # Community skills


@dataclass
class InstallJob:
    """Tracks the outcome of a skill installation."""
    job_id: str
    skill_id: str
    tenant_id: str
    status: JobStatus
    progress: int  # 0-100
    message: str
    created_at: str
    updated_at: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class SkillReview:
    """A skill review record."""
    review_id: str
    skill_id: str
    tenant_id: str
    rating: int  # 1-5
    comment: Optional[str]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillMetadata:
    """Skill metadata from marketplace."""
    id: str
    name: str
    version: str
    description: str
    category: str
    tier: SkillTier
    l_layer: str  # L5, L10, L16, L22, etc.
    author: str
    requires_approval: bool = False
    install_count: int = 0
    avg_rating: Optional[float] = None
    rating_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


# Storage for reviews (tenant-scoped)
_REVIEWS_STORAGE: Dict[str, List[SkillReview]] = {}


def _now() -> str:
    """Return current timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_skill_id(skill_id: str) -> str:
    """Validate skill ID format."""
    if not _SKILL_ID_RE.match(skill_id or ""):
        raise HTTPException(status_code=400, detail="Invalid skill ID format")
    return skill_id


def _audit(
    rec: session_auth.SessionRecord,
    action: str,
    skill_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Record action to audit trail.

    Two records, on purpose: ``console.action_performed`` carries the operator
    attribution under the console's fixed vocabulary (it has no ``details``
    parameter — passing one used to raise TypeError at request time), and the
    per-action ``marketplace.skills.<action>`` event carries the extra context,
    field-allowlisted in ``console_audit._ALLOWED_FIELDS``.
    """
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=f"marketplace.skills.{action}",
        target_kind="marketplace_skill",
        target_id=skill_id,
    )
    if details:
        console_audit.system_event(
            tenant_id=rec.tenant_id,
            event=f"marketplace.skills.{action}",
            details={
                "tenant_id": rec.tenant_id,
                "sid_fingerprint": rec.sid_fingerprint,
                "skill_id": skill_id,
                **details,
            },
        )


def _remember(job: InstallJob) -> None:
    """Remember an install job (with automatic eviction of old jobs)."""
    with _jobs_lock:
        _install_jobs[job.job_id] = job
        while len(_install_jobs) > _MAX_JOBS:
            _install_jobs.popitem(last=False)


def _discover_skills() -> List[SkillMetadata]:
    """Discover available skills from the marketplace.

    Loads skills from:
    1. Core OS-Skills (core/skills/os_skills/)
    2. Marketplace Skills (Corvin-Marketplace/extensions/skills/)

    Returns list of SkillMetadata for each discovered skill.
    """
    skills: List[SkillMetadata] = []

    # For now, return a curated list of known OS-Skills
    # In production, this would scan directories and load metadata
    known_skills = [
        SkillMetadata(
            id="os.delegation_router",
            name="Delegation Router",
            version="1.0.0",
            description="Routes tasks to optimal engines based on complexity and cost",
            category="routing",
            tier=SkillTier.BUILDIN,
            l_layer="L5",
            author="Corvin Core",
            requires_approval=False,
        ),
        SkillMetadata(
            id="os.context_adapter",
            name="Context Adapter",
            version="1.0.0",
            description="Adapts conversation context based on user patterns",
            category="context",
            tier=SkillTier.BUILDIN,
            l_layer="L10",
            author="Corvin Core",
            requires_approval=False,
        ),
        SkillMetadata(
            id="os.workflow_optimizer",
            name="Workflow Optimizer",
            version="1.0.0",
            description="Learns and optimizes execution chains from user feedback",
            category="optimization",
            tier=SkillTier.BUILDIN,
            l_layer="L22",
            author="Corvin Core",
            requires_approval=False,
        ),
        SkillMetadata(
            id="os.security_orchestrator",
            name="Security Orchestrator",
            version="1.0.0",
            description="Learns and enforces security patterns from audit events",
            category="security",
            tier=SkillTier.BUILDIN,
            l_layer="L16",
            author="Corvin Core",
            requires_approval=True,
        ),
        SkillMetadata(
            id="os.flow_guard",
            name="Data Flow Guard",
            version="1.0.0",
            description="Validates and guards data flow across system boundaries",
            category="data_safety",
            tier=SkillTier.BUILDIN,
            l_layer="L34",
            author="Corvin Core",
            requires_approval=True,
        ),
    ]

    return known_skills


def _get_tenant_skill_state(
    tenant_id: str,
) -> Dict[str, Dict[str, Any]]:
    """Get installation/enablement state for skills in this tenant.

    Returns mapping of skill_id -> {installed, enabled, installed_at, version}
    """
    # In production, this would read from tenant's skill registry
    # For now, return empty (no skills installed initially)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("")
async def list_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    category: Optional[str] = Query(None),
    layer: Optional[str] = Query(None),
    installed_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
) -> Dict[str, Any]:
    """List all available skills with optional filtering.

    Query Parameters:
    - category: Filter by category (routing, context, optimization, security, etc.)
    - layer: Filter by L-layer (L5, L10, L16, L22, L34, etc.)
    - installed_only: Show only installed skills
    - limit: Max results (default 100, max 1000)

    Returns:
    {
      "skills": [...],
      "count": int,
      "filters": {"category": str, "layer": str, "installed_only": bool},
      "total_available": int,
    }
    """
    skills = _discover_skills()
    installed = _get_tenant_skill_state(rec.tenant_id)

    # Apply filters
    if category:
        skills = [s for s in skills if s.category == category]
    if layer:
        skills = [s for s in skills if s.l_layer == layer]
    if installed_only:
        skills = [s for s in skills if s.id in installed]

    # Apply limit
    skills = skills[:limit]

    # Audit discovery
    console_audit.system_event(
        tenant_id=rec.tenant_id,
        event="marketplace.skills.discover",
        details={
            "tenant_id": rec.tenant_id,
            "sid_fingerprint": rec.sid_fingerprint,
            "category": category,
            "layer": layer,
            "installed_only": installed_only,
            "count": len(skills),
        },
    )

    return {
        "skills": [s.to_dict() for s in skills],
        "count": len(skills),
        "filters": {
            "category": category,
            "layer": layer,
            "installed_only": installed_only,
        },
        "total_available": len(_discover_skills()),
        "tenant_id": rec.tenant_id,
        "timestamp": _now(),
    }


@router.get("/search")
async def search_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    q: str = Query("", min_length=1),
) -> Dict[str, Any]:
    """Search skills by name or description.

    Query Parameters:
    - q: Search query (matches name and description)

    Returns: {skills, count, query}
    """
    if not q or len(q) < 2:
        return {
            "skills": [],
            "count": 0,
            "query": q,
            "timestamp": _now(),
        }

    skills = _discover_skills()
    query_lower = q.lower()

    # Search in name and description
    matched = [
        s for s in skills
        if query_lower in s.name.lower() or query_lower in s.description.lower()
    ]

    # Audit search
    # The raw query is operator free text — audit its length, never its content.
    console_audit.system_event(
        tenant_id=rec.tenant_id,
        event="marketplace.skills.search",
        details={
            "tenant_id": rec.tenant_id,
            "sid_fingerprint": rec.sid_fingerprint,
            "query_len": len(q),
            "results": len(matched),
        },
    )

    return {
        "skills": [s.to_dict() for s in matched],
        "count": len(matched),
        "query": q,
        "timestamp": _now(),
    }


@router.get("/{skill_id}")
async def get_skill_details(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get detailed information about a specific skill.

    Returns: {skill, reviews, installed, requires_approval, approvals_pending}
    """
    _validate_skill_id(skill_id)

    skills = _discover_skills()
    skill = next((s for s in skills if s.id == skill_id), None)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    installed = _get_tenant_skill_state(rec.tenant_id)
    reviews = _REVIEWS_STORAGE.get(f"{rec.tenant_id}:{skill_id}", [])

    return {
        "skill": skill.to_dict(),
        "reviews": [r.to_dict() for r in reviews],
        "review_count": len(reviews),
        "installed": skill_id in installed,
        "installed_info": installed.get(skill_id),
        "requires_approval": skill.requires_approval,
        "tenant_id": rec.tenant_id,
        "timestamp": _now(),
    }


@router.post("/{skill_id}/install")
async def install_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[str, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Install a skill to this tenant.

    Returns: {job_id, status, message}
    """
    _validate_skill_id(skill_id)

    skills = _discover_skills()
    skill = next((s for s in skills if s.id == skill_id), None)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    installed = _get_tenant_skill_state(rec.tenant_id)
    if skill_id in installed:
        raise HTTPException(
            status_code=400,
            detail=f"Skill {skill_id} is already installed"
        )

    # Create install job
    job_id = str(uuid.uuid4())
    job = InstallJob(
        job_id=job_id,
        skill_id=skill_id,
        tenant_id=rec.tenant_id,
        status=JobStatus.PENDING,
        progress=0,
        message=f"Installing {skill.name}...",
        created_at=_now(),
        updated_at=_now(),
    )
    _remember(job)

    # Simulate installation (in production, this would actually install)
    job.status = JobStatus.INSTALLING
    job.progress = 50
    job.updated_at = _now()

    job.status = JobStatus.COMPLETED
    job.progress = 100
    job.message = f"{skill.name} installed successfully"
    job.updated_at = _now()

    # Audit installation
    _audit(rec, "install", skill_id, {
        "job_id": job_id,
        "version": skill.version,
    })

    return {
        "job_id": job_id,
        "status": job.status.value,
        "message": job.message,
        "progress": job.progress,
        "skill_id": skill_id,
        "tenant_id": rec.tenant_id,
        "timestamp": _now(),
    }


@router.post("/{skill_id}/uninstall")
async def uninstall_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[str, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Uninstall a skill from this tenant.

    Returns: {status, message}
    """
    _validate_skill_id(skill_id)

    skills = _discover_skills()
    skill = next((s for s in skills if s.id == skill_id), None)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    installed = _get_tenant_skill_state(rec.tenant_id)
    if skill_id not in installed:
        raise HTTPException(
            status_code=400,
            detail=f"Skill {skill_id} is not installed"
        )

    # Audit uninstallation
    _audit(rec, "uninstall", skill_id, {
        "version": skill.version,
    })

    return {
        "status": "success",
        "message": f"{skill.name} uninstalled",
        "skill_id": skill_id,
        "tenant_id": rec.tenant_id,
        "timestamp": _now(),
    }


@router.post("/{skill_id}/rate")
async def rate_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[str, Depends(require_csrf)],
    rating: int = Body(..., ge=1, le=5),
    comment: Optional[str] = Body(None, max_length=500),
) -> Dict[str, Any]:
    """Rate a skill (1-5 stars).

    Request body:
    {
      "rating": 1-5,
      "comment": "optional feedback"
    }

    Returns: {review_id, rating, timestamp}
    """
    _validate_skill_id(skill_id)

    skills = _discover_skills()
    skill = next((s for s in skills if s.id == skill_id), None)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    # Create review
    review_id = str(uuid.uuid4())
    review = SkillReview(
        review_id=review_id,
        skill_id=skill_id,
        tenant_id=rec.tenant_id,
        rating=rating,
        comment=comment,
        timestamp=_now(),
    )

    # Store review (tenant-scoped)
    key = f"{rec.tenant_id}:{skill_id}"
    if key not in _REVIEWS_STORAGE:
        _REVIEWS_STORAGE[key] = []
    _REVIEWS_STORAGE[key].append(review)

    # Audit rating
    _audit(rec, "rate", skill_id, {
        "rating": rating,
        "has_comment": comment is not None,
    })

    return {
        "review_id": review_id,
        "rating": rating,
        "timestamp": review.timestamp,
        "skill_id": skill_id,
        "tenant_id": rec.tenant_id,
    }


@router.get("/{skill_id}/reviews")
async def get_skill_reviews(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get all reviews for a skill.

    Returns: {reviews, count, average_rating}
    """
    _validate_skill_id(skill_id)

    skills = _discover_skills()
    skill = next((s for s in skills if s.id == skill_id), None)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    # Get reviews for this tenant's skill
    key = f"{rec.tenant_id}:{skill_id}"
    reviews = _REVIEWS_STORAGE.get(key, [])

    avg_rating = None
    if reviews:
        avg_rating = sum(r.rating for r in reviews) / len(reviews)

    return {
        "reviews": [r.to_dict() for r in reviews],
        "count": len(reviews),
        "average_rating": avg_rating,
        "skill_id": skill_id,
        "tenant_id": rec.tenant_id,
        "timestamp": _now(),
    }


@router.get("/install/{job_id}/progress")
async def get_install_progress(
    job_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get the status of an ongoing skill installation.

    Returns: {job_id, skill_id, status, progress, message, error}
    """
    with _jobs_lock:
        job = _install_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Installation job {job_id} not found")

    # Tenant isolation: only the tenant that created the job can view it
    if job.tenant_id != rec.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return job.to_dict()
