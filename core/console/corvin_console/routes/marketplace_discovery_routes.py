"""
Marketplace Discovery API Routes (ADR-0892, Phase 1 Session 1).

Exposes discovery, search, filtering, and collection endpoints.

Routes:
  GET  /api/v1/marketplace/search       → Full-text search with filters
  GET  /api/v1/marketplace/collections  → Pre-curated skill bundles
  GET  /api/v1/marketplace/plugins/{id} → Skill details + history
  GET  /api/v1/marketplace/categories   → Available categories + counts
  GET  /api/v1/marketplace/tags         → Available tags + counts
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_session
from .marketplace_discovery import (
    DiscoveryEngine,
    SearchQuery,
    SortBy,
    SkillCollection,
    SkillDetails,
)
from .marketplace import _index_manager, _tenant_install_state, _local_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace-discovery"])


def get_discovery_engine() -> DiscoveryEngine:
    """Get the discovery engine with current plugin index."""
    index = _index_manager.get_index()
    plugins = index.get("plugins", [])
    return DiscoveryEngine(plugins)


@router.get("/search")
async def search(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    q: str = Query("", min_length=0, max_length=200),
    category: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    license: Optional[str] = Query(None),
    min_rating: float = Query(0.0, ge=0.0, le=5.0),
    sort_by: SortBy = Query(SortBy.RELEVANCE),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    Full-text search with advanced filtering and sorting.

    Query Parameters:
    - q: Free-text search (name, description, tags, author)
    - category: Filter by category
    - tier: Filter by tier (buildin, contributor)
    - tag: Filter by tag
    - license: Filter by license
    - min_rating: Minimum rating (0-5)
    - sort_by: Sort order (relevance, downloads, rating, recency, name)
    - limit: Results per page (default 100)
    - offset: Pagination offset

    Returns:
    {
      "results": [...],
      "total": int,
      "query": {...},
      "facets": {
        "categories": {...},
        "tiers": {...},
        "tags": {...}
      }
    }
    """
    engine = get_discovery_engine()

    # Build search query
    query = SearchQuery(
        q=q,
        category=category,
        tier=tier,
        tag=tag,
        license=license,
        min_rating=min_rating,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )

    # Execute search
    results, total = engine.search(query)

    # Enrich with local state
    installed = _tenant_install_state(rec.tenant_id)
    enriched = [dict(p, **_local_state(p, installed)) for p in results]

    # Audit the search
    console_audit.system_event(
        tenant_id=rec.tenant_id,
        event="marketplace.search",
        details={
            "tenant_id": rec.tenant_id,
            "query": q,
            "filters": {
                "category": category,
                "tier": tier,
                "tag": tag,
                "license": license,
                "min_rating": min_rating,
            },
            "sort_by": sort_by.value,
            "results_count": len(enriched),
            "total_count": total,
        },
    )

    return {
        "results": enriched,
        "total": total,
        "query": {
            "q": q,
            "category": category,
            "tier": tier,
            "tag": tag,
            "license": license,
            "min_rating": min_rating,
            "sort_by": sort_by.value,
            "limit": limit,
            "offset": offset,
        },
        "facets": {
            "categories": engine.get_categories(),
            "tiers": engine.get_tiers(),
            "tags": engine.get_tags(),
        },
    }


@router.get("/plugins/{plugin_id}")
async def get_plugin_details(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    plugin_id: str,
) -> Dict[str, Any]:
    """
    Get complete skill details with version history and reviews.

    Returns:
    {
      "id": "plugin:buildin-...",
      "name": "...",
      "version": "...",
      "author": "...",
      "description": "...",
      "long_description": "...",
      "category": "...",
      "tier": "...",
      "license": "...",
      "tags": [...],
      "dependencies": [...],
      "metrics": {
        "downloads": int,
        "installs": int,
        "rating": float,
        "review_count": int,
        "success_rate": float,
        "avg_latency_ms": float,
        "last_updated": "..."
      },
      "versions": [...],
      "reviews": [...]
    }
    """
    engine = get_discovery_engine()
    details = engine.get_plugin_details(plugin_id)

    if not details:
        raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")

    # Audit the view
    console_audit.system_event(
        tenant_id=rec.tenant_id,
        event="marketplace.view_details",
        details={
            "tenant_id": rec.tenant_id,
            "plugin_id": plugin_id,
        },
    )

    return {
        "id": details.id,
        "name": details.name,
        "version": details.version,
        "author": details.author,
        "description": details.description,
        "long_description": details.long_description,
        "category": details.category,
        "tier": details.tier,
        "license": details.license,
        "tags": details.tags,
        "dependencies": details.dependencies,
        "requires_version": details.requires_version,
        "boot_layer": details.boot_layer,
        "sla_level": details.sla_level,
        "readme_url": details.readme_url,
        "source_url": details.source_url,
        "documentation_url": details.documentation_url,
        "support_url": details.support_url,
        "metrics": {
            "downloads": details.metrics.downloads,
            "installs": details.metrics.installs,
            "rating": details.metrics.rating,
            "review_count": details.metrics.review_count,
            "success_rate": details.metrics.success_rate,
            "avg_latency_ms": details.metrics.avg_latency_ms,
            "last_updated": details.metrics.last_updated,
        },
        "versions": [
            {
                "version": v.version,
                "release_date": v.release_date,
                "release_notes": v.release_notes,
                "downloads": v.downloads,
                "required_version": v.required_version,
            }
            for v in details.versions
        ],
        "reviews": details.reviews,
    }


@router.get("/collections")
async def list_collections(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """
    Get pre-curated skill collections for common use cases.

    Returns:
    {
      "collections": [
        {
          "id": "collection:...",
          "name": "...",
          "description": "...",
          "icon": "...",
          "skills": [...],
          "target_use_case": "...",
          "difficulty": "beginner|intermediate|advanced",
          "estimated_setup_time_minutes": int
        }
      ]
    }
    """
    engine = get_discovery_engine()
    collections = engine.get_collections()

    # Audit collection view
    console_audit.system_event(
        tenant_id=rec.tenant_id,
        event="marketplace.view_collections",
        details={
            "tenant_id": rec.tenant_id,
            "collections_count": len(collections),
        },
    )

    return {
        "collections": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "icon": c.icon,
                "skills": c.skills,
                "target_use_case": c.target_use_case,
                "difficulty": c.difficulty,
                "estimated_setup_time_minutes": c.estimated_setup_time_minutes,
            }
            for c in collections
        ]
    }


@router.get("/categories")
async def list_categories(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """
    Get available categories and their skill counts.

    Returns:
    {
      "categories": {
        "memory": 5,
        "security_compliance": 3,
        "integration": 8,
        ...
      }
    }
    """
    engine = get_discovery_engine()
    categories = engine.get_categories()

    console_audit.system_event(
        tenant_id=rec.tenant_id,
        event="marketplace.view_categories",
        details={
            "tenant_id": rec.tenant_id,
            "categories_count": len(categories),
        },
    )

    return {"categories": categories}


@router.get("/tags")
async def list_tags(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """
    Get available tags and their skill counts.

    Returns:
    {
      "tags": {
        "ml": 12,
        "learning": 8,
        "optimization": 6,
        ...
      }
    }
    """
    engine = get_discovery_engine()
    tags = engine.get_tags()

    console_audit.system_event(
        tenant_id=rec.tenant_id,
        event="marketplace.view_tags",
        details={
            "tenant_id": rec.tenant_id,
            "tags_count": len(tags),
        },
    )

    return {"tags": tags}
