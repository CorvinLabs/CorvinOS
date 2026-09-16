"""Marketplace Hub Routes (Phase 1, ADR-0686)

FastAPI endpoints for unified discovery across 5 subsystems:
- GET /v1/marketplace/hub/index - Full index with pagination
- GET /v1/marketplace/hub/search - Search with filters
- GET /v1/marketplace/hub/trending - Trending items
- GET /v1/marketplace/hub/newest - Newest items
- GET /v1/marketplace/hub/{category}/{id} - Item detail
- POST /v1/marketplace/hub/drill-down - Navigate to subsystem UI

License: Apache-2.0
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any
import logging

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel

from core.skills.marketplace_hub import MarketplaceHub, DiscoveryItem, SearchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/marketplace", tags=["marketplace"])

# Global service instance
_hub: Optional[MarketplaceHub] = None


# ============================================================================
# Pydantic Models (for API schemas)
# ============================================================================

class DiscoveryItemResponse(BaseModel):
    """API response for a discovery item."""
    id: str
    category: str
    name: str
    description: str
    version: str
    author: str
    rating: float
    rating_count: int
    tags: List[str]
    domain: str
    tier: str
    origin: str
    install_count: int
    created_at: str
    updated_at: str
    is_trending: bool
    is_new: bool
    badge: str


class SearchResultResponse(BaseModel):
    """API response for search results."""
    items: List[DiscoveryItemResponse]
    total: int
    page: int
    per_page: int
    query: str
    filters: Dict[str, Any]
    facets: Dict[str, Dict[str, int]]


class HubIndexResponse(BaseModel):
    """API response for hub index."""
    skills: List[DiscoveryItemResponse]
    plugins: List[DiscoveryItemResponse]
    tools: List[DiscoveryItemResponse]
    connectors: List[DiscoveryItemResponse]
    layers: List[DiscoveryItemResponse]
    timestamp: str
    total_count: int


class DrillDownRequest(BaseModel):
    """Request to navigate to subsystem UI."""
    subsystem_type: str  # "skills", "plugins", "tools", "connectors", "layers"
    item_id: str
    target_page: str = "detail"  # "detail", "install", "reviews", "docs"


class DrillDownResponse(BaseModel):
    """Response with navigation URL."""
    url: str
    subsystem_type: str
    item_id: str
    target_page: str


# ============================================================================
# Service Initialization
# ============================================================================

def init_service(corvin_home: str) -> None:
    """Initialize marketplace hub service.

    Args:
        corvin_home: Path to ~/.corvin
    """
    global _hub
    _hub = MarketplaceHub(corvin_home)
    logger.info("Marketplace Hub service initialized")


def get_service() -> MarketplaceHub:
    """Get the hub service instance."""
    if not _hub:
        raise RuntimeError("Marketplace Hub service not initialized; call init_service()")
    return _hub


# ============================================================================
# API Routes
# ============================================================================

@router.get("/hub/index")
async def get_index(
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max items per category"),
    force_refresh: bool = Query(False, description="Bypass 5-min cache"),
) -> HubIndexResponse:
    """Get full hub index with all 5 categories.

    Returns paginated items across Skills, Plugins, Tools, Connectors, Layers.
    Respects 5-minute cache unless force_refresh=true.

    Args:
        skip: Number of items to skip per category
        limit: Max items per category (1-100, default 20)
        force_refresh: Force reload from source (bypass cache)

    Returns:
        HubIndexResponse with items from all 5 categories
    """
    service = get_service()

    try:
        index = service.get_index(force_refresh=force_refresh)

        # Apply pagination per category
        response = HubIndexResponse(
            skills=[item.to_dict() for item in index.skills[skip:skip+limit]],
            plugins=[item.to_dict() for item in index.plugins[skip:skip+limit]],
            tools=[item.to_dict() for item in index.tools[skip:skip+limit]],
            connectors=[item.to_dict() for item in index.connectors[skip:skip+limit]],
            layers=[item.to_dict() for item in index.layers[skip:skip+limit]],
            timestamp=index.timestamp,
            total_count=index.total_count,
        )

        return response

    except Exception as e:
        logger.error(f"Error loading hub index: {e}")
        raise HTTPException(status_code=500, detail="Failed to load hub index")


@router.get("/hub/search")
async def search(
    q: str = Query("", description="Search query (fuzzy matched)"),
    categories: Optional[str] = Query(None, description="Comma-separated categories (skills,plugins,tools,connectors,layers)"),
    tier: Optional[str] = Query(None, description="Filter by tier (compliance, core, installed, builtin, vetted, community)"),
    domain: Optional[str] = Query(None, description="Filter by domain"),
    origin: Optional[str] = Query(None, description="Filter by origin (builtin, vetted, community)"),
    min_rating: Optional[float] = Query(None, ge=0, le=5, description="Min rating (0-5)"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(20, ge=1, le=100, description="Results per page"),
) -> SearchResultResponse:
    """Search across all categories with fuzzy matching and filters.

    Searches item names, descriptions, and tags. Returns faceted results.

    Args:
        q: Search query (fuzzy matched)
        categories: Comma-separated categories to search
        tier: Filter by tier
        domain: Filter by domain
        origin: Filter by origin
        min_rating: Minimum rating filter
        page: Page number (1-indexed)
        per_page: Results per page (1-100)

    Returns:
        SearchResultResponse with items, facets, and pagination
    """
    service = get_service()

    try:
        # Parse categories
        category_list = None
        if categories:
            category_list = [c.strip().lower() for c in categories.split(",")]

        # Build filters
        filters = {}
        if tier:
            filters["tier"] = tier
        if domain:
            filters["domain"] = domain
        if origin:
            filters["origin"] = origin
        if min_rating is not None:
            filters["rating_min"] = min_rating

        result = service.search(
            query=q,
            categories=category_list,
            filters=filters if filters else None,
            page=page,
            per_page=per_page,
        )

        return SearchResultResponse(
            items=[item.to_dict() for item in result.items],
            total=result.total,
            page=result.page,
            per_page=result.per_page,
            query=result.query,
            filters=result.filters,
            facets=result.facets,
        )

    except Exception as e:
        logger.error(f"Error during search: {e}")
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/hub/trending")
async def get_trending(
    limit: int = Query(10, ge=1, le=50, description="Max items to return"),
) -> Dict[str, Any]:
    """Get trending items across all categories.

    Items scored by recency, rating, and install count.

    Args:
        limit: Max items to return (1-50)

    Returns:
        List of trending DiscoveryItems
    """
    service = get_service()

    try:
        items = service.trending(limit=limit)

        return {
            "items": [item.to_dict() for item in items],
            "total": len(items),
            "label": "Trending Now",
            "description": "Items with high activity and ratings",
        }

    except Exception as e:
        logger.error(f"Error loading trending: {e}")
        raise HTTPException(status_code=500, detail="Failed to load trending items")


@router.get("/hub/newest")
async def get_newest(
    limit: int = Query(10, ge=1, le=50, description="Max items to return"),
) -> Dict[str, Any]:
    """Get newest items across all categories.

    Sorted by created_at (descending).

    Args:
        limit: Max items to return (1-50)

    Returns:
        List of newest DiscoveryItems
    """
    service = get_service()

    try:
        items = service.newest(limit=limit)

        return {
            "items": [item.to_dict() for item in items],
            "total": len(items),
            "label": "Newly Added",
            "description": "Recently added skills, plugins, and integrations",
        }

    except Exception as e:
        logger.error(f"Error loading newest: {e}")
        raise HTTPException(status_code=500, detail="Failed to load newest items")


@router.get("/hub/{category}/{item_id}")
async def get_detail(
    category: str = Query(..., description="Category (skills, plugins, tools, connectors, layers)"),
    item_id: str = Query(..., description="Item ID"),
) -> DiscoveryItemResponse:
    """Get detailed view of a single item.

    Args:
        category: Item category
        item_id: Item ID

    Returns:
        Full DiscoveryItem details
    """
    service = get_service()

    try:
        item = service.get_detail(item_id, category)

        if not item:
            raise HTTPException(
                status_code=404,
                detail=f"Item {item_id} not found in category {category}"
            )

        return DiscoveryItemResponse(**item.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading detail: {e}")
        raise HTTPException(status_code=500, detail="Failed to load item details")


@router.post("/hub/drill-down")
async def drill_down(
    request: DrillDownRequest = Body(..., description="Drill-down navigation request"),
) -> DrillDownResponse:
    """Navigate to subsystem UI from marketplace.

    Constructs URL to navigate from marketplace to specific subsystem panel
    (e.g., marketplace -> Skills Manager -> detail view).

    Args:
        request: DrillDownRequest with subsystem, item_id, target_page

    Returns:
        DrillDownResponse with URL to navigate to
    """
    service = get_service()

    try:
        # Verify item exists
        item = service.get_detail(request.item_id, request.subsystem_type)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # Construct URL based on subsystem type
        subsystem_urls = {
            "skills": f"/console/skill-manager?id={request.item_id}&action={request.target_page}",
            "plugins": f"/console/plugin-center?id={request.item_id}&action={request.target_page}",
            "tools": f"/console/tools?id={request.item_id}&action={request.target_page}",
            "connectors": f"/console/connectors?id={request.item_id}&action={request.target_page}",
            "layers": f"/console/layers?id={request.item_id}&action={request.target_page}",
        }

        url = subsystem_urls.get(request.subsystem_type)
        if not url:
            raise HTTPException(status_code=400, detail=f"Unknown subsystem: {request.subsystem_type}")

        return DrillDownResponse(
            url=url,
            subsystem_type=request.subsystem_type,
            item_id=request.item_id,
            target_page=request.target_page,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during drill-down: {e}")
        raise HTTPException(status_code=500, detail="Drill-down failed")
