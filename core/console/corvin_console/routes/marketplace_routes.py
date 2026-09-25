"""Phase 6: Skill Marketplace Discovery API Routes (ADR-0682)"""
from fastapi import APIRouter, Query, HTTPException
from pathlib import Path
from core.skills.skill_marketplace import (
    SkillMarketplaceIndex, SkillSearchQuery, SkillDomain, SkillTier
)
from typing import Optional

router = APIRouter()

# Initialize marketplace index
_MARKETPLACE_INDEX = None

def get_marketplace_index():
    global _MARKETPLACE_INDEX
    if _MARKETPLACE_INDEX is None:
        registry_path = Path.home() / ".corvin" / "skills_installed" / "skills_registry.json"
        _MARKETPLACE_INDEX = SkillMarketplaceIndex(registry_path)
    return _MARKETPLACE_INDEX

@router.get("/marketplace/index", tags=["marketplace"])
async def marketplace_index(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List skills in marketplace (paginated)."""
    try:
        index = get_marketplace_index()
        query = SkillSearchQuery(limit=limit, offset=offset)
        results = index.search(query)
        return {
            "total": len(index._registry),
            "limit": limit,
            "offset": offset,
            "skills": [
                {
                    "skill_id": r.metadata.skill_id,
                    "name": r.metadata.name,
                    "description": r.metadata.description,
                    "domain": r.metadata.domain.value,
                    "tier": r.metadata.tier.value,
                    "rating": r.metadata.rating,
                    "install_count": r.metadata.install_count,
                    "relevance_score": r.relevance_score,
                }
                for r in results
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/marketplace/search", tags=["marketplace"])
async def marketplace_search(
    q: str = Query("", min_length=0, max_length=100),
    domain: Optional[str] = None,
    tier: Optional[str] = None,
    min_rating: float = Query(0.0, ge=0.0, le=5.0),
    sort_by: str = Query("relevance", regex="^(relevance|popularity|rating|recency|alphabetical)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Search marketplace with filters."""
    try:
        index = get_marketplace_index()
        
        domain_enum = SkillDomain(domain) if domain else None
        tier_enum = SkillTier(tier) if tier else None
        
        query = SkillSearchQuery(
            text=q,
            domain=domain_enum,
            tier=tier_enum,
            min_rating=min_rating,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
        )
        
        results = index.search(query)
        return {
            "query": q,
            "filters": {"domain": domain, "tier": tier, "min_rating": min_rating},
            "sort_by": sort_by,
            "count": len(results),
            "skills": [
                {
                    "skill_id": r.metadata.skill_id,
                    "name": r.metadata.name,
                    "description": r.metadata.description,
                    "domain": r.metadata.domain.value,
                    "rating": r.metadata.rating,
                    "install_count": r.metadata.install_count,
                    "relevance_score": r.relevance_score,
                    "matched_fields": r.matched_fields,
                }
                for r in results
            ]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid filter: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/marketplace/trending", tags=["marketplace"])
async def marketplace_trending(days: int = Query(7)):
    """Get trending skills (by install_count × rating)."""
    try:
        index = get_marketplace_index()
        trending = index.get_trending(days)
        return {
            "trending_window_days": days,
            "skills": [
                {
                    "skill_id": s.skill_id,
                    "name": s.name,
                    "rating": s.rating,
                    "install_count": s.install_count,
                    "score": s.install_count * (s.rating / 5.0 + 0.1),
                }
                for s in trending
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/marketplace/newest", tags=["marketplace"])
async def marketplace_newest(limit: int = Query(5, ge=1, le=20)):
    """Get newest skills."""
    try:
        index = get_marketplace_index()
        newest = index.get_newest(limit)
        return {
            "limit": limit,
            "skills": [
                {
                    "skill_id": s.skill_id,
                    "name": s.name,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                }
                for s in newest
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/marketplace/{skill_id}", tags=["marketplace"])
async def marketplace_detail(skill_id: str):
    """Get full skill details."""
    try:
        index = get_marketplace_index()
        metadata = index.get_detail(skill_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
        
        return {
            "skill_id": metadata.skill_id,
            "name": metadata.name,
            "version": metadata.version,
            "description": metadata.description,
            "domain": metadata.domain.value,
            "tier": metadata.tier.value,
            "origin": metadata.origin.value,
            "tags": metadata.tags,
            "rating": metadata.rating,
            "install_count": metadata.install_count,
            "created_at": metadata.created_at,
            "updated_at": metadata.updated_at,
            "dependencies": metadata.dependencies,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/marketplace/{skill_id}/install", tags=["marketplace"])
async def marketplace_install(skill_id: str):
    """One-click install from marketplace (async)."""
    try:
        # This is handled by Phase 5 SkillInstaller
        # Marketplace just triggers the download + install workflow
        from core.skills.skill_installer import SkillInstaller
        installer = SkillInstaller()
        
        index = get_marketplace_index()
        metadata = index.get_detail(skill_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
        
        # Return 202 Accepted (async operation)
        return {
            "status": "accepted",
            "skill_id": skill_id,
            "message": f"Installation of {skill_id}@{metadata.version} queued",
            "job_id": f"install-{skill_id}-{metadata.version}",
        }, 202
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

