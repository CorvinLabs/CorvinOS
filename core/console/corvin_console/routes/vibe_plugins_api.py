"""Phase 4: Vibe Plugin Installation API Routes (Console).

FastAPI routes for POST /v1/vibe/plugins/install, GET /v1/vibe/plugins, etc.
Includes marketplace discovery endpoint (ADR-0249 v0.1).
"""

from fastapi import APIRouter, Query, HTTPException, Depends, status as http_status
import logging
import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Annotated
from pydantic import BaseModel, Field, validator

from ..deps import require_session
from .. import auth as session_auth

logger = logging.getLogger(__name__)

# Create FastAPI router
router = APIRouter(prefix="/vibe/plugins", tags=["vibe-plugins"])

# Global plugin API handler (injected at app init)
_plugin_api = None

# Marketplace cache (loaded from config file)
_MARKETPLACE_PLUGINS: List[Dict[str, Any]] = []
_MARKETPLACE_CONFIG: Dict[str, Any] = {}
_MARKETPLACE_LOADED = False


def _load_marketplace_config() -> None:
    """Load marketplace plugins from JSON config file."""
    global _MARKETPLACE_PLUGINS, _MARKETPLACE_CONFIG, _MARKETPLACE_LOADED

    if _MARKETPLACE_LOADED:
        return

    # Try to find the marketplace config file
    config_paths = [
        Path(__file__).parent.parent.parent.parent / "gateway" / "corvin_gateway" / "config" / "marketplace.json",
        Path("/etc/corvin/marketplace.json"),  # System-wide override
        Path.home() / ".corvin" / "marketplace.json",  # User override
    ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)
                _MARKETPLACE_PLUGINS = data.get("plugins", [])
                _MARKETPLACE_CONFIG = data.get("config", {})
                logger.info(f"Loaded marketplace from {config_path}")
                _MARKETPLACE_LOADED = True
                return
            except Exception as e:
                logger.warning(f"Failed to load marketplace from {config_path}: {e}")
                continue

    # Fallback: Empty marketplace if no config found
    logger.warning("No marketplace config found, starting with empty catalog")
    _MARKETPLACE_LOADED = True


class PluginInstallRequest(BaseModel):
    """Pydantic model for plugin installation request."""
    manifest_url: Optional[str] = Field(None, description="URL to plugin manifest")
    manifest_json: Optional[Dict[str, Any]] = Field(None, description="Inline plugin manifest")

    @validator('manifest_url', 'manifest_json')
    def at_least_one_required(cls, v, values):
        """Ensure at least manifest_url or manifest_json is provided."""
        if not values and not v:
            raise ValueError("Either manifest_url or manifest_json must be provided")
        return v

    class Config:
        extra = "forbid"  # Reject unknown fields

def set_plugin_api(api):
    """Inject plugin API handler."""
    global _plugin_api
    _plugin_api = api

def get_plugin_api():
    """Get plugin API handler."""
    return _plugin_api

@router.post("/install")
async def install_plugin(request: PluginInstallRequest):
    """
    Install plugin from manifest.

    Request:
    {
        "manifest_url": "file:///path/to/plugin.json",
        "or": "manifest_json": "{...}"
    }

    Response:
    {
        "plugin_id": "my_plugin",
        "status": "success",
        "message": "Plugin installed",
        "manifest": {...}
    }
    """
    try:
        api = get_plugin_api()
        if not api:
            raise HTTPException(status_code=503, detail="Plugin API not initialized")

        # Validate request
        if not request.manifest_url and not request.manifest_json:
            raise HTTPException(status_code=400, detail="Either manifest_url or manifest_json is required")

        # Convert Pydantic model to dict for compatibility
        install_req_dict = request.dict(exclude_none=True)

        # Import the actual PluginInstallRequest if it exists in vibe_engineering
        try:
            from ...vibe_engineering.plugin_api import PluginInstallRequest as VibePR
            install_req = VibePR.from_request_body(install_req_dict)
        except (ImportError, AttributeError):
            # Fallback if vibe_engineering is not available
            install_req = install_req_dict

        response = await api.install_plugin(install_req)
        return response.to_dict() if hasattr(response, 'to_dict') else response

    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid install request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid request: {str(e)}")
    except Exception as e:
        logger.error(f"Install failed: {e}")
        raise HTTPException(status_code=500, detail=f"Installation failed: {str(e)}")

@router.get("/list")
async def list_plugins():
    """
    List installed plugins.

    Response:
    {
        "loaded": [...],
        "failed": {...},
        "count_total": 5
    }
    """
    try:
        api = get_plugin_api()
        if not api:
            return {"error": "Plugin API not initialized"}

        result = await api.list_plugins()
        return result

    except Exception as e:
        logger.error(f"List failed: {e}")
        return {"error": str(e)}

@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str):
    """Get plugin details."""
    try:
        api = get_plugin_api()
        if not api:
            return {"error": "Plugin API not initialized"}

        plugin = await api.get_plugin(plugin_id)
        if not plugin:
            return {"error": f"Plugin {plugin_id} not found"}

        return plugin

    except Exception as e:
        logger.error(f"Get plugin failed: {e}")
        return {"error": str(e)}

@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    """Disable plugin."""
    try:
        api = get_plugin_api()
        response = await api.disable_plugin(plugin_id)
        return response.to_dict()
    except Exception as e:
        return {"error": str(e)}

@router.post("/{plugin_id}/uninstall")
async def uninstall_plugin(plugin_id: str):
    """Uninstall plugin."""
    try:
        api = get_plugin_api()
        response = await api.uninstall_plugin(plugin_id)
        return response.to_dict()
    except Exception as e:
        return {"error": str(e)}

@router.post("/{plugin_id}/report")
async def report_plugin(plugin_id: str, body: Dict[str, Any]):
    """
    Report a plugin as inappropriate or malicious.

    Request:
    {
        "reason": "malicious|inappropriate|permission_abuse|misrepresentation|other",
        "details": "Description of the issue"
    }

    Response:
    {
        "status": "success",
        "message": "Report submitted",
        "report_id": "uuid-here"
    }

    ADR-0249: Plugin Trust Anchor — allows community to flag malicious plugins.

    CRITICAL: Audit failure returns 503, preventing unaudited reports (GDPR Art. 30, 32).
    """
    try:
        import uuid
        from fastapi import HTTPException, status as http_status

        # Validate request
        reason = body.get("reason", "").strip()
        details = body.get("details", "").strip()

        if not reason:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Missing reason field",
            )

        if not details:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Missing details field",
            )

        if len(details) < 10 or len(details) > 500:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Details must be 10-500 characters",
            )

        valid_reasons = {
            "malicious",
            "inappropriate",
            "permission_abuse",
            "misrepresentation",
            "other",
        }
        if reason not in valid_reasons:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid reason. Must be one of {valid_reasons}",
            )

        # Generate report ID
        report_id = str(uuid.uuid4())

        # Write audit event with fail-closed semantics (ADR-0249, ADR-0233)
        try:
            from corvinOS.core.console.corvin_console import audit as console_audit

            console_audit.plugin_reported(
                tenant_id="_default",  # Reports from unauthenticated context use default tenant
                sid_fingerprint="anonymous",
                plugin_id=plugin_id,
                reason=reason,
                report_id=report_id,
            )
        except Exception as audit_err:
            logger.error(f"Audit failure on plugin report: {audit_err}", exc_info=True)
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="audit system unavailable",
            )

        logger.info(f"Plugin {plugin_id} reported: {reason} (report_id={report_id})")

        return {
            "status": "success",
            "message": "Report submitted. Thank you for reporting this plugin.",
            "report_id": report_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Report plugin failed: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Report submission failed: {type(e).__name__}",
        )

@router.get("/marketplace")
async def list_marketplace(
    category: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    origin: Optional[str] = Query(None),
    region: Optional[str] = Query(None, description="Filter by region (eu, us, apac)"),
    sort: str = Query("rating"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
):
    """
    Discover available plugins in the marketplace.

    CRITICAL: Requires authenticated session (tenant isolation per GDPR Art. 5, 6, 32).
    Without session context, returns 403 Forbidden (CRIT-02).

    Query parameters:
    - category: Filter by category (Authentication, Performance, Security, Database, Integration, UI, Analytics, Tooling)
    - query: Search in name/description
    - origin: Filter by origin (vetted, community, builtin)
    - region: Filter by region (eu, us, apac) - operator-customizable
    - sort: Sort by rating|downloads|recent (default: rating)
    - limit: Max results (default: 20, max: 100)
    - offset: Pagination offset (default: 0)

    Response:
    {
        "plugins": [...],
        "total": 42,
        "limit": 20,
        "offset": 0,
        "config": {
            "allow_region_filtering": true,
            "default_region": "eu"
        }
    }

    ADR-0249 v0.1: Discovery-only marketplace. Install-from-catalog deferred to v0.2.
    Config is operator-customizable via /core/gateway/corvin_gateway/config/marketplace.json
    """
    try:
        # CRIT-02: Tenant isolation via session requirement (GDPR Art. 5, 6, 32)
        # rec.tenant_id is now available from authenticated session.
        # Future: Filter marketplace by tenant_id when per-tenant catalogs are supported.
        tenant_id = rec.tenant_id if rec else "_default"

        # Load marketplace if not already loaded
        _load_marketplace_config()

        # Normalize query parameters
        category_filter = (category or "").strip().lower()
        query_filter = (query or "").strip().lower()
        origin_filter = (origin or "").strip().lower()
        region_filter = (region or "").strip().lower()

        # Validate region parameter
        valid_regions = {"eu", "us", "apac"}
        if region_filter and region_filter not in valid_regions:
            logger.warning(f"Invalid region filter: {region_filter}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid region. Must be one of: {', '.join(valid_regions)}"
            )

        # Start with all marketplace plugins
        results = []
        for plugin in _MARKETPLACE_PLUGINS:
            # Filter by category
            if category_filter and plugin.get("category", "").lower() != category_filter:
                continue

            # Filter by origin
            if origin_filter and plugin.get("origin", "").lower() != origin_filter:
                continue

            # Filter by region
            if region_filter:
                plugin_regions = plugin.get("regions", [])
                if region_filter not in plugin_regions:
                    continue

            # Filter by search query
            if query_filter:
                name = plugin.get("name", "").lower()
                description = plugin.get("description", "").lower()
                long_desc = plugin.get("long_description", "").lower()
                if query_filter not in name and query_filter not in description and query_filter not in long_desc:
                    continue

            # Must be listed
            if not plugin.get("listed", True):
                continue

            # Compute trust badge
            plugin_origin = plugin.get("origin", "community")
            if plugin_origin == "vetted":
                trust_badge = "verified"
            elif plugin_origin == "builtin":
                trust_badge = "verified"
            else:
                trust_badge = "community"

            # Add badge to result
            result_plugin = plugin.copy()
            result_plugin["trust_badge"] = trust_badge

            results.append(result_plugin)

        # Sort results
        if sort == "rating":
            results.sort(key=lambda p: (-p.get("rating", 0), -p.get("download_count", 0)))
        elif sort == "downloads":
            results.sort(key=lambda p: (-p.get("download_count", 0), -p.get("rating", 0)))
        elif sort == "recent":
            results.sort(key=lambda p: p.get("plugin_id", ""))  # Stub for now

        # Apply pagination
        total = len(results)
        paginated = results[offset:offset + limit]

        return {
            "plugins": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
            "config": {
                "allow_region_filtering": _MARKETPLACE_CONFIG.get("allow_region_filtering", True),
                "default_region": _MARKETPLACE_CONFIG.get("default_region", "eu"),
            }
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid query parameter: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid query parameter: {str(e)}")
    except Exception as e:
        logger.error(f"Marketplace list failed: {e}")
        raise HTTPException(status_code=500, detail=f"Marketplace query failed: {str(e)}")
