"""RAG Hub Analytics — Provider marketplace metrics.

Provides insights into provider popularity, trends, and community engagement.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)

_HUB = None  # Reference to RAG Hub (initialized elsewhere)


def set_hub(hub: Any):
    """Set the Hub instance for analytics to use."""
    global _HUB
    _HUB = hub


router = APIRouter(prefix="/hub/analytics", tags=["console-rag-hub-analytics"])


# ── Dashboard ──────────────────────────────────────────────

@router.get("/summary")
async def get_analytics_summary(
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get overall Hub analytics summary."""
    if _HUB is None:
        return {"error": "Hub not initialized"}

    try:
        all_providers = _HUB.list(limit=1000)
        all_reviews = []
        for provider in all_providers:
            all_reviews.extend(_HUB.get_reviews(provider.id))

        total_downloads = sum(p.download_count for p in all_providers)
        avg_rating = sum(p.rating for p in all_providers) / len(all_providers) if all_providers else 0.0

        return {
            "total_providers": len(all_providers),
            "total_downloads": total_downloads,
            "total_reviews": len(all_reviews),
            "average_rating": round(avg_rating, 1),
            "compliance_zones": {
                zone: len([p for p in all_providers if p.compliance_zone == zone])
                for zone in ["EU", "US", "APAC", "HYBRID"]
            },
            "data_classifications": {
                cls: len([p for p in all_providers if p.data_classification == cls])
                for cls in ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"]
            },
        }
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        logger.error("analytics error", exc_info=True)
        return {"error": "internal error"}


# ── Trending ───────────────────────────────────────────────

@router.get("/trending")
async def get_trending_analytics(
    period_days: int = Query(7, ge=1, le=90),
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get trending providers over a time period."""
    if _HUB is None:
        return {"trending": []}

    try:
        providers = _HUB.get_trending(limit=20)
        return {
            "period_days": period_days,
            "trending": [
                {
                    "id": p.id,
                    "name": p.name,
                    "trending_score": round(p.trending_score, 2),
                    "download_count": p.download_count,
                    "rating": p.rating,
                }
                for p in providers
            ],
        }
    except Exception as e:
        logger.error(f"Trending error: {e}")
        logger.error("analytics error", exc_info=True)
        return {"error": "internal error"}


# ── Most Downloaded ────────────────────────────────────────

@router.get("/most-downloaded")
async def get_most_downloaded_analytics(
    limit: int = Query(10, ge=1, le=50),
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get most-downloaded providers."""
    if _HUB is None:
        return {"providers": []}

    try:
        providers = _HUB.get_most_downloaded(limit=limit)
        total_downloads = sum(p.download_count for p in providers)

        return {
            "providers": [
                {
                    "id": p.id,
                    "name": p.name,
                    "download_count": p.download_count,
                    "percentage": round((p.download_count / total_downloads) * 100, 1) if total_downloads else 0,
                }
                for p in providers
            ],
            "total_downloads": total_downloads,
        }
    except Exception as e:
        logger.error(f"Download analytics error: {e}")
        logger.error("analytics error", exc_info=True)
        return {"error": "internal error"}


# ── Top Rated ──────────────────────────────────────────────

@router.get("/top-rated")
async def get_top_rated_analytics(
    min_reviews: int = Query(1, ge=0),
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get highest-rated providers (min review threshold)."""
    if _HUB is None:
        return {"providers": []}

    try:
        providers = _HUB.get_top_rated(limit=20)
        filtered = [p for p in providers if p.review_count >= min_reviews]

        return {
            "min_reviews": min_reviews,
            "providers": [
                {
                    "id": p.id,
                    "name": p.name,
                    "rating": p.rating,
                    "review_count": p.review_count,
                }
                for p in filtered
            ],
        }
    except Exception as e:
        logger.error(f"Top rated error: {e}")
        logger.error("analytics error", exc_info=True)
        return {"error": "internal error"}


# ── Capability Distribution ────────────────────────────────

@router.get("/capabilities")
async def get_capability_distribution(
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get distribution of capabilities across providers."""
    if _HUB is None:
        return {"capabilities": {}}

    try:
        all_providers = _HUB.list(limit=1000)
        capability_counts: dict[str, int] = {}

        for provider in all_providers:
            for capability in provider.capabilities:
                capability_counts[capability] = capability_counts.get(capability, 0) + 1

        # Sort by frequency
        sorted_capabilities = sorted(
            capability_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return {
            "total_unique_capabilities": len(capability_counts),
            "capabilities": [
                {"name": cap, "providers": count}
                for cap, count in sorted_capabilities
            ],
        }
    except Exception as e:
        logger.error(f"Capability error: {e}")
        logger.error("analytics error", exc_info=True)
        return {"error": "internal error"}


# ── Compliance Insights ────────────────────────────────────

@router.get("/compliance")
async def get_compliance_insights(
    _session: Annotated[Any, Depends(require_session)] = None,
) -> dict[str, Any]:
    """Get compliance zone and classification distribution."""
    if _HUB is None:
        return {"zones": {}, "classifications": {}}

    try:
        all_providers = _HUB.list(limit=1000)

        zones = {}
        classifications = {}

        for provider in all_providers:
            zone = provider.compliance_zone
            zones[zone] = zones.get(zone, 0) + 1

            cls = provider.data_classification
            classifications[cls] = classifications.get(cls, 0) + 1

        return {
            "compliance_zones": zones,
            "data_classifications": classifications,
            "total_providers": len(all_providers),
        }
    except Exception as e:
        logger.error(f"Compliance insights error: {e}")
        logger.error("analytics error", exc_info=True)
        return {"error": "internal error"}
