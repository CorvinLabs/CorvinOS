"""
Console Marketplace API — FastAPI Edition.

Exposes core/plugins/marketplace.py (ADR-0385) backend to Console UI.
Implements CONCEPT-0023 Phase 1-2 endpoints via FastAPI (not Flask).

Routes:
  GET  /marketplace/index          → List all plugins
  GET  /marketplace/search         → Search + filter
  GET  /marketplace/extension/<id> → Get one plugin
  POST /marketplace/install        → Queue install (mock)
  POST /marketplace/uninstall      → Queue uninstall (mock)
  PATCH /marketplace/extension/<id>/enable  → Enable
  PATCH /marketplace/extension/<id>/disable → Disable

Design: FastAPI APIRouter (native async, better integration with console app).
Rewrite from Flask (ADR-0472 synthesis: adapter layer → full rewrite).
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
from dataclasses import asdict
import logging

# Import marketplace backend (ADR-0385)
try:
    from core.plugins.marketplace import (
        PluginMarketplace,
        PluginMetadata,
    )
except ImportError:
    PluginMarketplace = None
    PluginMetadata = None

# Import cache manager
try:
    from .marketplace_cache import MarketplaceCacheManager
except ImportError:
    MarketplaceCacheManager = None

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/marketplace", tags=["marketplace"])

# Global cache instance
_cache_manager = None


def get_cache_manager() -> Optional[MarketplaceCacheManager]:
    """Get or create cache manager singleton."""
    global _cache_manager
    if _cache_manager is None and MarketplaceCacheManager:
        _cache_manager = MarketplaceCacheManager()
    return _cache_manager


def serialize_plugin(plugin: PluginMetadata) -> Dict[str, Any]:
    """Convert PluginMetadata dataclass to JSON-serializable dict."""
    data = asdict(plugin)
    # Convert Enums to strings
    if hasattr(data.get("category"), "value"):
        data["category"] = data["category"].value
    if hasattr(data.get("origin"), "value"):
        data["origin"] = data["origin"].value
    if hasattr(data.get("boot_layer"), "value"):
        data["boot_layer"] = data["boot_layer"].value
    return data


@router.get("/index")
async def marketplace_index() -> Dict[str, Any]:
    """
    GET /marketplace/index

    Returns: {
      "version": "1.0",
      "last_updated": "2026-08-30T...",
      "extensions": [PluginMetadata{...}, ...]
    }

    Caching: 1h TTL with stale-while-revalidate fallback on network errors.
    Error: 503 if backend unavailable, 500 on other errors
    """
    if not PluginMarketplace:
        raise HTTPException(status_code=503, detail="Marketplace backend unavailable")

    try:
        cache = get_cache_manager()

        # Try cache first
        if cache:
            cached_data = cache.get()
            if cached_data:
                return {
                    "version": "1.0",
                    "last_updated": "2026-08-30T00:00:00Z",
                    "extensions": cached_data,
                    "cached": True,
                }

        # Cache miss or no cache manager: fetch from backend
        marketplace = PluginMarketplace()
        plugins = marketplace.list_all()
        serialized = [serialize_plugin(p) for p in plugins]

        # Update cache
        if cache:
            cache.set(serialized)

        return {
            "version": "1.0",
            "last_updated": "2026-08-30T00:00:00Z",
            "extensions": serialized,
            "cached": False,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch marketplace index: {e}")

        # Stale-while-revalidate: return stale cache on error
        cache = get_cache_manager()
        if cache:
            stale_data = cache.get_stale()
            if stale_data:
                logger.info("Returning stale cache on error")
                return {
                    "version": "1.0",
                    "extensions": stale_data,
                    "cached": True,
                    "stale": True,
                    "error": str(e),
                }

        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def marketplace_search(
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    origin: Optional[str] = Query(None),
    rating_min: Optional[float] = Query(None),
    sort: Optional[str] = Query("rating"),
) -> Dict[str, Any]:
    """
    GET /marketplace/search

    Query params:
      q: string (search by name/description)
      category: string (Authentication, Performance, Security, ...)
      origin: string (builtin, vetted, community)
      rating_min: float (1.0-5.0)
      sort: string (rating, downloads, name)

    Returns: {extensions: [filtered results]}
    """
    if not PluginMarketplace:
        raise HTTPException(status_code=503, detail="Marketplace backend unavailable")

    try:
        # Fetch from backend
        marketplace = PluginMarketplace()
        plugins = marketplace.list_all()

        # Client-side filtering
        filtered = plugins
        if q:
            q_lower = q.lower()
            filtered = [
                p
                for p in filtered
                if q_lower in p.name.lower() or q_lower in p.description.lower()
            ]
        if category:
            filtered = [p for p in filtered if p.category.value == category]
        if origin:
            filtered = [p for p in filtered if p.origin.value == origin]
        if rating_min:
            filtered = [p for p in filtered if p.rating_average >= rating_min]

        return {"extensions": [serialize_plugin(p) for p in filtered]}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid query params: {e}")
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/extension/{extension_id}")
async def marketplace_extension_details(extension_id: str) -> Dict[str, Any]:
    """
    GET /marketplace/extension/{id}

    Returns: {
      "id": "auth-saml-2.1",
      "metadata": {...},
      "readme_url": "https://raw.../README.md"
    }

    Error: 404 (not found), 500 (backend error)
    """
    if not PluginMarketplace:
        raise HTTPException(status_code=503, detail="Marketplace backend unavailable")

    try:
        marketplace = PluginMarketplace()
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Extension '{extension_id}' not found")

        return {
            "id": plugin.plugin_id,
            "metadata": serialize_plugin(plugin),
            "readme_url": (
                f"{plugin.repository_url}/blob/main/README.md"
                if plugin.repository_url
                else None
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get extension {extension_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/install")
async def marketplace_install(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /marketplace/install

    Body: {
      "extension_id": "plugin:example-plugin",
      "version": "0.1.0",
      "tenant_id": "default"
    }

    Returns: {
      "status": "queued",
      "job_id": "install-abc123"
    }

    Note: Phase 1 is mock. Phase 3 will implement actual installation.
    """
    if not PluginMarketplace:
        raise HTTPException(status_code=503, detail="Marketplace backend unavailable")

    try:
        if not body:
            raise HTTPException(status_code=400, detail="Request body required")

        extension_id = body.get("extension_id")
        version = body.get("version")
        tenant_id = body.get("tenant_id", "default")

        if not extension_id:
            raise HTTPException(status_code=400, detail="extension_id required")

        # Validate extension exists
        marketplace = PluginMarketplace()
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Extension '{extension_id}' not found")

        # Mock job_id
        job_id = f"install-{extension_id}-{version or plugin.version}"

        return {
            "status": "queued",
            "job_id": job_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Install failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/uninstall")
async def marketplace_uninstall(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /marketplace/uninstall

    Body: {
      "extension_id": "plugin:example-plugin",
      "tenant_id": "default"
    }

    Returns: {
      "status": "queued",
      "job_id": "uninstall-abc123"
    }
    """
    if not PluginMarketplace:
        raise HTTPException(status_code=503, detail="Marketplace backend unavailable")

    try:
        if not body:
            raise HTTPException(status_code=400, detail="Request body required")

        extension_id = body.get("extension_id")
        tenant_id = body.get("tenant_id", "default")

        if not extension_id:
            raise HTTPException(status_code=400, detail="extension_id required")

        # Validate extension exists
        marketplace = PluginMarketplace()
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Extension '{extension_id}' not found")

        job_id = f"uninstall-{extension_id}"

        return {"status": "queued", "job_id": job_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Uninstall failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/extension/{extension_id}/enable")
async def marketplace_enable(extension_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    PATCH /marketplace/extension/{id}/enable

    Body: {"tenant_id": "default"}

    Returns: {"status": "enabled"}

    Note: Phase 1 is mock. Phase 4 will implement actual enable logic.
    """
    if not PluginMarketplace:
        raise HTTPException(status_code=503, detail="Marketplace backend unavailable")

    try:
        # Validate extension exists
        marketplace = PluginMarketplace()
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Extension '{extension_id}' not found")

        return {"status": "enabled"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Enable failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/extension/{extension_id}/disable")
async def marketplace_disable(extension_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    PATCH /marketplace/extension/{id}/disable

    Body: {"tenant_id": "default"}

    Returns: {"status": "disabled"}

    Note: Phase 1 is mock. Phase 4 will implement actual disable logic.
    """
    if not PluginMarketplace:
        raise HTTPException(status_code=503, detail="Marketplace backend unavailable")

    try:
        # Validate extension exists
        marketplace = PluginMarketplace()
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Extension '{extension_id}' not found")

        return {"status": "disabled"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Disable failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
