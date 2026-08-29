"""
Console Marketplace API — discovery, search, and installation.

Exposes core/plugins/marketplace.py (ADR-0385) backend to Console UI.
Implements CONCEPT-0023 Phase 1 endpoints.

Routes:
  GET  /api/v2/marketplace/index          → List all plugins
  GET  /api/v2/marketplace/search         → Search + filter
  GET  /api/v2/marketplace/extension/<id> → Get one plugin
  POST /api/v2/marketplace/install        → Queue install (mock)
  POST /api/v2/marketplace/uninstall      → Queue uninstall (mock)
  PATCH /api/v2/marketplace/extension/<id>/enable  → Enable
  PATCH /api/v2/marketplace/extension/<id>/disable → Disable
"""

from flask import Blueprint, request, jsonify, current_app
from typing import List, Optional, Dict, Any
from dataclasses import asdict
import logging

# Import marketplace backend (ADR-0385)
try:
    from core.plugins.marketplace import (
        PluginMarketplace,
        PluginMetadata,
        PluginCategory,
        PluginOrigin,
    )
except ImportError:
    PluginMarketplace = None
    PluginMetadata = None

# Import cache manager (Task #3)
try:
    from .marketplace_cache import MarketplaceCacheManager
except ImportError:
    MarketplaceCacheManager = None

logger = logging.getLogger(__name__)

marketplace_bp = Blueprint(
    "marketplace",
    __name__,
    url_prefix="/api/v2/marketplace",
)

# Global cache instance
_cache_manager = None


def get_cache_manager():
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


@marketplace_bp.route("/index", methods=["GET"])
def get_marketplace_index():
    """
    GET /api/v2/marketplace/index

    Returns: {
      "version": "1.0",
      "last_updated": "2026-08-30T...",
      "extensions": [PluginMetadata{...}, ...]
    }

    Caching: 1h TTL with stale-while-revalidate fallback on network errors.
    Error: 503 if backend unavailable, 500 on other errors
    """
    if not PluginMarketplace:
        return jsonify({"error": "Marketplace backend unavailable"}), 503

    try:
        cache = get_cache_manager()

        # Try cache first
        if cache:
            cached_data = cache.get()
            if cached_data:
                return jsonify(
                    {
                        "version": "1.0",
                        "last_updated": "2026-08-30T00:00:00Z",
                        "extensions": cached_data,
                        "cached": True,
                    }
                ), 200

        # Cache miss or no cache manager: fetch from backend
        marketplace = PluginMarketplace()  # TODO: wire to global instance
        plugins = marketplace.list_all()
        serialized = [serialize_plugin(p) for p in plugins]

        # Update cache (async in production, sync for now)
        if cache:
            cache.set(serialized)

        return jsonify(
            {
                "version": "1.0",
                "last_updated": "2026-08-30T00:00:00Z",  # TODO: actual timestamp
                "extensions": serialized,
                "cached": False,
            }
        ), 200

    except Exception as e:
        logger.error(f"Failed to fetch marketplace index: {e}")

        # Stale-while-revalidate: return stale cache on error
        cache = get_cache_manager()
        if cache:
            stale_data = cache.get_stale()
            if stale_data:
                logger.info("Returning stale cache on error")
                return jsonify(
                    {
                        "version": "1.0",
                        "extensions": stale_data,
                        "cached": True,
                        "stale": True,
                        "error": str(e),
                    }
                ), 200

        return jsonify({"error": str(e)}), 500


@marketplace_bp.route("/search", methods=["GET"])
def search_marketplace():
    """
    GET /api/v2/marketplace/search

    Query params:
      q: string (search by name/description)
      category: string (Authentication, Performance, Security, ...)
      origin: string (builtin, vetted, community)
      rating_min: float (1.0-5.0)
      sort: string (rating, downloads, name)

    Returns: {extensions: [filtered results]}
    Error: 400 (bad query), 500 (backend error)
    """
    if not PluginMarketplace:
        return jsonify({"error": "Marketplace backend unavailable"}), 503

    try:
        # Parse query params
        q = request.args.get("q", "").strip()
        category = request.args.get("category")
        origin = request.args.get("origin")
        rating_min = request.args.get("rating_min", type=float)
        sort = request.args.get("sort", "rating")

        # TODO: wire PluginMarketplace.search(q, category, origin, rating_min, sort)
        # For now, return all and filter client-side
        marketplace = PluginMarketplace()  # TODO: wire to global instance
        plugins = marketplace.list_all()

        # Simple client-side filtering (temporary)
        filtered = plugins
        if q:
            q_lower = q.lower()
            filtered = [
                p
                for p in filtered
                if q_lower in p.name.lower()
                or q_lower in p.description.lower()
            ]
        if category:
            filtered = [p for p in filtered if p.category.value == category]
        if origin:
            filtered = [p for p in filtered if p.origin.value == origin]
        if rating_min:
            filtered = [p for p in filtered if p.rating_average >= rating_min]

        return jsonify(
            {"extensions": [serialize_plugin(p) for p in filtered]}
        ), 200
    except ValueError as e:
        return jsonify({"error": f"Invalid query params: {e}"}), 400
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return jsonify({"error": str(e)}), 500


@marketplace_bp.route("/extension/<extension_id>", methods=["GET"])
def get_extension_details(extension_id: str):
    """
    GET /api/v2/marketplace/extension/{id}

    Returns: {
      "id": "auth-saml-2.1",
      "name": "SAML 2.0 Authentication",
      "version": "0.1.0",
      "metadata": {...},
      "readme_url": "https://raw.../README.md"
    }

    Error: 404 (not found), 500 (backend error)
    """
    if not PluginMarketplace:
        return jsonify({"error": "Marketplace backend unavailable"}), 503

    try:
        marketplace = PluginMarketplace()  # TODO: wire to global instance
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            return jsonify({"error": f"Extension '{extension_id}' not found"}), 404

        return jsonify(
            {
                "id": plugin.plugin_id,
                "metadata": serialize_plugin(plugin),
                "readme_url": plugin.repository_url + "/blob/main/README.md"
                if plugin.repository_url
                else None,
            }
        ), 200
    except Exception as e:
        logger.error(f"Failed to get extension {extension_id}: {e}")
        return jsonify({"error": str(e)}), 500


@marketplace_bp.route("/install", methods=["POST"])
def install_extension():
    """
    POST /api/v2/marketplace/install

    Body: {
      "extension_id": "plugin:example-plugin",
      "version": "0.1.0",
      "tenant_id": "default"
    }

    Returns: {
      "status": "queued",
      "job_id": "install-abc123",
      "progress_url": "/api/v2/marketplace/install/abc123/progress"
    }

    Error: 400 (validation), 404 (not found), 500 (backend error)

    Note: Phase 1 is mock. Phase 3 will implement actual installation.
    """
    if not PluginMarketplace:
        return jsonify({"error": "Marketplace backend unavailable"}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body required"}), 400

        extension_id = data.get("extension_id")
        version = data.get("version")
        tenant_id = data.get("tenant_id", "default")

        if not extension_id:
            return jsonify({"error": "extension_id required"}), 400

        # Validate extension exists
        marketplace = PluginMarketplace()  # TODO: wire to global instance
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            return jsonify({"error": f"Extension '{extension_id}' not found"}), 404

        # TODO: Phase 3 will implement real installation logic
        # For now, return mock job_id
        job_id = f"install-{extension_id}-{version or plugin.version}"

        return jsonify(
            {
                "status": "queued",
                "job_id": job_id,
                "progress_url": f"/api/v2/marketplace/install/{job_id}/progress",
            }
        ), 202

    except Exception as e:
        logger.error(f"Install failed: {e}")
        return jsonify({"error": str(e)}), 500


@marketplace_bp.route("/uninstall", methods=["POST"])
def uninstall_extension():
    """
    POST /api/v2/marketplace/uninstall

    Body: {
      "extension_id": "plugin:example-plugin",
      "tenant_id": "default"
    }

    Returns: {
      "status": "queued",
      "job_id": "uninstall-abc123"
    }

    Error: 400 (validation), 404 (not found), 500 (backend error)
    """
    if not PluginMarketplace:
        return jsonify({"error": "Marketplace backend unavailable"}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body required"}), 400

        extension_id = data.get("extension_id")
        tenant_id = data.get("tenant_id", "default")

        if not extension_id:
            return jsonify({"error": "extension_id required"}), 400

        # Validate extension exists
        marketplace = PluginMarketplace()  # TODO: wire to global instance
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            return jsonify({"error": f"Extension '{extension_id}' not found"}), 404

        # TODO: Phase 3 will implement real uninstall logic
        job_id = f"uninstall-{extension_id}"

        return jsonify(
            {"status": "queued", "job_id": job_id}
        ), 202

    except Exception as e:
        logger.error(f"Uninstall failed: {e}")
        return jsonify({"error": str(e)}), 500


@marketplace_bp.route("/extension/<extension_id>/enable", methods=["PATCH"])
def enable_extension(extension_id: str):
    """
    PATCH /api/v2/marketplace/extension/{id}/enable

    Body: {"tenant_id": "default"}

    Returns: {"status": "enabled"}
    Error: 404 (not found), 500 (backend error)

    Note: Phase 1 is mock. Phase 4 will implement actual enable logic.
    """
    if not PluginMarketplace:
        return jsonify({"error": "Marketplace backend unavailable"}), 503

    try:
        data = request.get_json() or {}
        tenant_id = data.get("tenant_id", "default")

        # Validate extension exists
        marketplace = PluginMarketplace()  # TODO: wire to global instance
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            return jsonify({"error": f"Extension '{extension_id}' not found"}), 404

        # TODO: Phase 4 will call PluginGovernance.mark_enabled(extension_id, tenant_id)
        # For now, return success

        return jsonify({"status": "enabled"}), 200

    except Exception as e:
        logger.error(f"Enable failed: {e}")
        return jsonify({"error": str(e)}), 500


@marketplace_bp.route("/extension/<extension_id>/disable", methods=["PATCH"])
def disable_extension(extension_id: str):
    """
    PATCH /api/v2/marketplace/extension/{id}/disable

    Body: {"tenant_id": "default"}

    Returns: {"status": "disabled"}
    Error: 404 (not found), 500 (backend error)

    Note: Phase 1 is mock. Phase 4 will implement actual disable logic.
    """
    if not PluginMarketplace:
        return jsonify({"error": "Marketplace backend unavailable"}), 503

    try:
        data = request.get_json() or {}
        tenant_id = data.get("tenant_id", "default")

        # Validate extension exists
        marketplace = PluginMarketplace()  # TODO: wire to global instance
        plugin = marketplace.get_plugin(extension_id)
        if not plugin:
            return jsonify({"error": f"Extension '{extension_id}' not found"}), 404

        # TODO: Phase 4 will call PluginGovernance.mark_disabled(extension_id, tenant_id)
        # For now, return success

        return jsonify({"status": "disabled"}), 200

    except Exception as e:
        logger.error(f"Disable failed: {e}")
        return jsonify({"error": str(e)}), 500
