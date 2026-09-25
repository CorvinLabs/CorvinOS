"""Skill catalogue for the Marketplace "Skills" tab (ADR-0682, inside ADR-0892).

Mounted at `/v1/console/marketplace/skills/*` — its own namespace under the ONE
marketplace, so the `{skill_id}` detail route can never swallow a sibling path.

The catalogue is the skill registry `SkillInstaller` (ADR-0680) writes: it lists
skills that ARE installed on this host. There is deliberately no install route
here — the one that shipped with Phase 6 answered "queued" and installed
nothing. Skills arrive as packages (Packages tab) or via the Skill Manager.
"""
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.skills.skill_marketplace import (
    SkillDomain, SkillMarketplaceIndex, SkillSearchQuery, SkillTier,
)

from ..deps import require_session

router = APIRouter(dependencies=[Depends(require_session)])

# Same file SkillInstaller.__init__ defaults to (core/skills/skill_installer.py).
_REGISTRY_PATH = Path.home() / ".corvin" / "skills_installed" / "skills_registry.json"


def get_marketplace_index() -> SkillMarketplaceIndex:
    """A fresh read per request: the registry changes whenever a skill is
    installed or removed, and a process-lifetime copy went stale on the first."""
    return SkillMarketplaceIndex(_REGISTRY_PATH)


def _card(m: Any) -> dict[str, Any]:
    return {
        "skill_id": m.skill_id,
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "domain": m.domain.value,
        "tier": m.tier.value,
        "origin": m.origin.value,
        "rating": m.rating,
        "install_count": m.install_count,
    }


@router.get("/index")
async def marketplace_index(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List installed skills (paginated)."""
    index = get_marketplace_index()
    results = index.search(SkillSearchQuery(limit=limit, offset=offset))
    return {
        "total": len(index._registry),
        "limit": limit,
        "offset": offset,
        "skills": [{**_card(r.metadata), "relevance_score": r.relevance_score} for r in results],
    }


@router.get("/search")
async def marketplace_search(
    q: str = Query("", min_length=0, max_length=100),
    domain: Optional[str] = None,
    tier: Optional[str] = None,
    min_rating: float = Query(0.0, ge=0.0, le=5.0),
    sort_by: str = Query("relevance", pattern="^(relevance|popularity|rating|recency|alphabetical)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Search the catalogue with filters."""
    try:
        domain_enum = SkillDomain(domain) if domain else None
        tier_enum = SkillTier(tier) if tier else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid filter: {e}")

    index = get_marketplace_index()
    results = index.search(SkillSearchQuery(
        text=q, domain=domain_enum, tier=tier_enum, min_rating=min_rating,
        sort_by=sort_by, limit=limit, offset=offset,
    ))
    return {
        "query": q,
        "filters": {"domain": domain, "tier": tier, "min_rating": min_rating},
        "sort_by": sort_by,
        "total": len(index._registry),
        "count": len(results),
        "skills": [
            {**_card(r.metadata), "relevance_score": r.relevance_score, "matched_fields": r.matched_fields}
            for r in results
        ],
    }


@router.get("/trending")
async def marketplace_trending(days: int = Query(7, ge=1, le=365)):
    """Skills ranked by install_count × rating."""
    trending = get_marketplace_index().get_trending(days)
    return {
        "trending_window_days": days,
        "skills": [
            {**_card(s), "score": s.install_count * (s.rating / 5.0 + 0.1)} for s in trending
        ],
    }


@router.get("/newest")
async def marketplace_newest(limit: int = Query(5, ge=1, le=20)):
    """Most recently created skills."""
    newest = get_marketplace_index().get_newest(limit)
    return {
        "limit": limit,
        "skills": [
            {**_card(s), "created_at": s.created_at, "updated_at": s.updated_at} for s in newest
        ],
    }


@router.get("/{skill_id}")
async def marketplace_detail(skill_id: str):
    """Full metadata of one skill."""
    m = get_marketplace_index().get_detail(skill_id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    return {
        **_card(m),
        "tags": m.tags,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "dependencies": m.dependencies,
    }
