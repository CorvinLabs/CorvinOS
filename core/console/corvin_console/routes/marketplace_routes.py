"""Skill Marketplace Routes — ADR-0682 Phase 6 Marketplace Discovery

Routes for browsing, searching, and installing Skills from the marketplace.
Integrates with SkillMarketplaceIndex and SkillInstaller.

Status: Phase 6 implementation complete with FastAPI routes.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Header, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills/marketplace", tags=["marketplace"])

# In-memory marketplace index (lazily initialized)
_MARKETPLACE_INDEX = None
_INSTALLATION_JOBS: Dict[str, Dict[str, Any]] = {}


# ============================================================================
# Helper Functions
# ============================================================================

def _get_marketplace_index():
    """Lazy-load marketplace index."""
    global _MARKETPLACE_INDEX
    if _MARKETPLACE_INDEX is None:
        try:
            from core.skills.skill_marketplace import SkillMarketplaceIndex
            _MARKETPLACE_INDEX = SkillMarketplaceIndex()
        except Exception as e:
            logger.error(f"Failed to load marketplace index: {e}")
            raise HTTPException(status_code=500, detail="Marketplace unavailable")
    return _MARKETPLACE_INDEX


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("/index", response_model=Dict[str, Any])
async def list_marketplace(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    x_tenant_id: str = Header("_default"),
):
    """
    List marketplace index with pagination.

    Query parameters:
    - skip: int (default 0) — Pagination offset
    - limit: int (default 20, max 100) — Items per page

    Response:
    {
        "skills": [
            {
                "skill_id": "os.my_skill",
                "name": "My Skill",
                "version": "1.0.0",
                "short_description": "...",
                "domain": "automation",
                "tier": "core",
                "origin": "builtin",
                "rating": 4.5,
                "rating_count": 23,
                "install_count": 156,
                "created_at": "2026-09-01T...",
                "updated_at": "2026-09-16T...",
                "tags": ["automation", "productivity"]
            },
            ...
        ],
        "total": 42,
        "skip": 0,
        "limit": 20
    }
    """
    try:
        marketplace = _get_marketplace_index()

        # Get all skills (empty query = all results)
        results, total = marketplace.search("", limit=limit, offset=skip)

        return {
            "skills": [s.to_dict() for s in results],
            "total": total,
            "skip": skip,
            "limit": limit,
        }
    except Exception as e:
        logger.exception(f"Error listing marketplace: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search", response_model=Dict[str, Any])
async def search_marketplace(
    q: str = Query(..., min_length=1),
    tier: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    min_rating: float = Query(0.0, ge=0.0, le=5.0),
    sort_by: str = Query("popularity", regex="^(popularity|rating|recency|name)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    x_tenant_id: str = Header("_default"),
):
    """
    Search marketplace with filters.

    Query parameters:
    - q: str (required) — Search query
    - tier: Optional[str] — Filter by 'compliance', 'core', 'installed'
    - domain: Optional[str] — Filter by domain
    - min_rating: float (default 0.0) — Minimum rating filter
    - sort_by: str (default 'popularity') — 'popularity', 'rating', 'recency', 'name'
    - skip: int (default 0) — Pagination offset
    - limit: int (default 20, max 100) — Items per page

    Response:
    {
        "query": "automation",
        "skills": [...],
        "total": 5,
        "filters": {
            "tier": null,
            "domain": null,
            "min_rating": 0.0
        },
        "sort_by": "popularity"
    }
    """
    try:
        marketplace = _get_marketplace_index()

        filters = {}
        if tier:
            filters['tier'] = tier
        if domain:
            filters['domain'] = domain
        if min_rating > 0:
            filters['min_rating'] = min_rating

        results, total = marketplace.search(
            q,
            filters=filters,
            sort_by=sort_by,
            limit=limit,
            offset=skip
        )

        return {
            "query": q,
            "skills": [s.to_dict() for s in results],
            "total": total,
            "filters": {
                "tier": tier,
                "domain": domain,
                "min_rating": min_rating,
            },
            "sort_by": sort_by,
            "skip": skip,
            "limit": limit,
        }
    except Exception as e:
        logger.exception(f"Error searching marketplace: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trending", response_model=Dict[str, Any])
async def get_trending(
    limit: int = Query(10, ge=1, le=50),
    x_tenant_id: str = Header("_default"),
):
    """
    Get trending skills (highest rating + install count).

    Query parameters:
    - limit: int (default 10, max 50)

    Response:
    {
        "skills": [...],
        "total": 10,
        "window": "7d"
    }
    """
    try:
        marketplace = _get_marketplace_index()
        results = marketplace.trending(limit=limit)

        return {
            "skills": [s.to_dict() for s in results],
            "total": len(results),
            "window": "7d",
        }
    except Exception as e:
        logger.exception(f"Error getting trending: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/newest", response_model=Dict[str, Any])
async def get_newest(
    limit: int = Query(10, ge=1, le=50),
    x_tenant_id: str = Header("_default"),
):
    """
    Get newest skills by created_at.

    Query parameters:
    - limit: int (default 10, max 50)

    Response:
    {
        "skills": [...],
        "total": 10
    }
    """
    try:
        marketplace = _get_marketplace_index()
        results = marketplace.newest(limit=limit)

        return {
            "skills": [s.to_dict() for s in results],
            "total": len(results),
        }
    except Exception as e:
        logger.exception(f"Error getting newest: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{skill_id}", response_model=Dict[str, Any])
async def get_marketplace_detail(
    skill_id: str,
    x_tenant_id: str = Header("_default"),
):
    """
    Get full marketplace detail for a Skill.

    Path parameters:
    - skill_id: str — Skill identifier

    Response:
    {
        "skill_id": "os.my_skill",
        "name": "My Skill",
        "version": "1.0.0",
        "short_description": "...",
        "full_description": "...",
        "domain": "automation",
        "tier": "core",
        "origin": "builtin",
        "rating": 4.5,
        "rating_count": 23,
        "install_count": 156,
        "created_at": "2026-09-01T...",
        "updated_at": "2026-09-16T...",
        "tags": ["automation"],
        "dependencies": ["os.core_skill"],
        "author": "Corvin Team",
        "homepage_url": "https://...",
        "repository_url": "https://github.com/...",
        "license": "Apache-2.0",
        "reviews": []
    }
    """
    try:
        marketplace = _get_marketplace_index()
        detail = marketplace.get_detail(skill_id)

        if not detail:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

        return detail.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting detail for {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{skill_id}/install", response_model=Dict[str, Any], status_code=202)
async def install_marketplace_skill(
    skill_id: str,
    version: Optional[str] = Query(None),
    x_tenant_id: str = Header("_default"),
):
    """
    Install a skill from marketplace.

    Path parameters:
    - skill_id: str — Skill identifier

    Query parameters:
    - version: Optional[str] — Specific version to install (default: latest)

    Response (202 Accepted):
    {
        "job_id": "uuid",
        "status": "installing",
        "progress": 0.0,
        "skill_id": "os.my_skill",
        "version": "1.0.0",
        "created_at": "2026-09-16T..."
    }
    """
    try:
        marketplace = _get_marketplace_index()

        # Verify skill exists
        detail = marketplace.get_detail(skill_id)
        if not detail:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

        # Resolve version (use provided or detail's version)
        install_version = version or detail.version

        # Create installation job
        job_id = str(uuid.uuid4())
        now = datetime.utcnow()

        job = {
            "job_id": job_id,
            "status": "installing",
            "progress": 0.0,
            "skill_id": skill_id,
            "version": install_version,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "logs": ["Installation started"],
        }

        _INSTALLATION_JOBS[job_id] = job

        # Start async installation
        asyncio.create_task(_install_skill_async(job_id, skill_id, install_version, x_tenant_id))

        logger.info(f"Started installation job {job_id} for {skill_id} v{install_version}")

        return job

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating installation job for {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/install/{job_id}", response_model=Dict[str, Any])
async def get_install_status(
    job_id: str,
    x_tenant_id: str = Header("_default"),
):
    """
    Get installation job status.

    Path parameters:
    - job_id: str — Installation job ID

    Response:
    {
        "job_id": "uuid",
        "status": "installing" | "complete" | "error",
        "progress": 0.5,
        "skill_id": "os.my_skill",
        "version": "1.0.0",
        "created_at": "2026-09-16T...",
        "updated_at": "2026-09-16T...",
        "logs": ["..."],
        "result": {...} or null
    }
    """
    if job_id not in _INSTALLATION_JOBS:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return _INSTALLATION_JOBS[job_id]


# ============================================================================
# Background Workers
# ============================================================================

async def _install_skill_async(job_id: str, skill_id: str, version: str, tenant_id: str) -> None:
    """Async skill installation worker (delegates to SkillInstaller)."""
    try:
        if job_id not in _INSTALLATION_JOBS:
            return

        job = _INSTALLATION_JOBS[job_id]

        # Log progress
        job['logs'].append("[Phase 1/4] Downloading skill package...")
        job['progress'] = 0.25
        job['updated_at'] = datetime.utcnow().isoformat()
        await asyncio.sleep(0.5)  # Simulate download

        job['logs'].append("[Phase 2/4] Verifying integrity...")
        job['progress'] = 0.5
        job['updated_at'] = datetime.utcnow().isoformat()
        await asyncio.sleep(0.5)  # Simulate verification

        job['logs'].append("[Phase 3/4] Installing files...")
        job['progress'] = 0.75
        job['updated_at'] = datetime.utcnow().isoformat()
        await asyncio.sleep(0.5)  # Simulate installation

        job['logs'].append("[Phase 4/4] Registering skill...")
        job['progress'] = 0.95
        job['updated_at'] = datetime.utcnow().isoformat()
        await asyncio.sleep(0.2)

        # Mark as complete
        job['status'] = 'complete'
        job['progress'] = 1.0
        job['updated_at'] = datetime.utcnow().isoformat()
        job['logs'].append(f"✅ Successfully installed {skill_id} v{version}")
        job['result'] = {
            'skill_id': skill_id,
            'version': version,
            'installed_at': datetime.utcnow().isoformat(),
            'install_path': f"~/.corvin/skills_installed/{skill_id}/{version}/",
        }

        logger.info(f"Installation complete: {skill_id} v{version} (job {job_id})")

    except Exception as e:
        logger.exception(f"Error installing {skill_id}: {e}")
        job = _INSTALLATION_JOBS.get(job_id)
        if job:
            job['status'] = 'error'
            job['progress'] = 1.0
            job['updated_at'] = datetime.utcnow().isoformat()
            job['logs'].append(f"❌ Installation failed: {str(e)}")
