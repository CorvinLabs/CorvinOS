"""REST API Endpoints for Plugin System (ADR-0XXX Phase 1b).

Routes:
  GET /api/plugins — list all installed plugins
  POST /api/plugins/{id}/enable — enable a plugin
  POST /api/plugins/{id}/disable — disable a plugin
  POST /api/plugins/{id}/config — update plugin settings
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Dict, Any, Optional, List

from core.orchestration.plugin_system.models import (
    Plugin, PluginRegistry, PluginNotFound, ValidationError
)
from core.orchestration.plugin_system.managers.lifecycle_manager import PluginLifecycleManager


# ── Request/Response Models ───────────────────────────────────────────────────

class PluginResponse(BaseModel):
    """Plugin data for API responses."""
    id: str
    version: str
    name: str
    enabled: bool
    tier: str
    pii_risk: str
    settings: Dict[str, Any]
    settings_schema: Dict[str, Any]
    
    @classmethod
    def from_plugin(cls, plugin: Plugin) -> "PluginResponse":
        """Convert Plugin model to API response."""
        return cls(
            id=plugin.id,
            version=plugin.version,
            name=plugin.name,
            enabled=plugin.enabled,
            tier=plugin.tier.value,
            pii_risk=plugin.pii_risk.value,
            settings=plugin.settings,
            settings_schema=plugin.settings_schema
        )


class ConfigUpdateRequest(BaseModel):
    """Request to update plugin settings."""
    settings: Dict[str, Any]


class PluginListResponse(BaseModel):
    """Response for plugin list."""
    plugins: List[PluginResponse]
    total: int


# ── API Router ────────────────────────────────────────────────────────────────

def create_plugin_routes(
    registry: PluginRegistry,
    lifecycle_manager: PluginLifecycleManager,
    get_current_user: callable = None
) -> APIRouter:
    """Create plugin API routes.
    
    Args:
        registry: Plugin registry
        lifecycle_manager: Lifecycle manager instance
        get_current_user: Optional dependency to get current user from auth
    
    Returns:
        APIRouter with plugin endpoints
    """
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])
    
    # GET /api/plugins — list all plugins
    @router.get("", response_model=PluginListResponse)
    async def list_plugins():
        """List all installed plugins."""
        plugins = list(registry.plugins.values())
        return PluginListResponse(
            plugins=[PluginResponse.from_plugin(p) for p in plugins],
            total=len(plugins)
        )
    
    # GET /api/plugins/{plugin_id} — get single plugin
    @router.get("/{plugin_id}", response_model=PluginResponse)
    async def get_plugin(plugin_id: str):
        """Get a single plugin by ID."""
        try:
            plugin = registry.get(plugin_id)
            return PluginResponse.from_plugin(plugin)
        except PluginNotFound:
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
    
    # POST /api/plugins/{plugin_id}/enable — enable plugin
    @router.post("/{plugin_id}/enable", response_model=PluginResponse)
    async def enable_plugin(plugin_id: str):
        """Enable a plugin."""
        try:
            user_id = "anonymous"  # TODO: get from get_current_user()
            plugin = lifecycle_manager.enable(plugin_id, user_id=user_id)
            return PluginResponse.from_plugin(plugin)
        except PluginNotFound:
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    # POST /api/plugins/{plugin_id}/disable — disable plugin
    @router.post("/{plugin_id}/disable", response_model=PluginResponse)
    async def disable_plugin(plugin_id: str):
        """Disable a plugin."""
        try:
            user_id = "anonymous"
            plugin = lifecycle_manager.disable(plugin_id, user_id=user_id)
            return PluginResponse.from_plugin(plugin)
        except PluginNotFound:
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    # POST /api/plugins/{plugin_id}/config — update settings
    @router.post("/{plugin_id}/config", response_model=PluginResponse)
    async def update_config(plugin_id: str, request: ConfigUpdateRequest):
        """Update plugin configuration."""
        try:
            user_id = "anonymous"
            plugin = lifecycle_manager.config_change(
                plugin_id,
                request.settings,
                user_id=user_id
            )
            return PluginResponse.from_plugin(plugin)
        except PluginNotFound:
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"Validation failed: {e}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    return router


# ── Install Endpoint (NEW for Phase 2a) ───────────────────────────────────

class InstallRequest(BaseModel):
    """Request to install plugin from marketplace."""
    marketplace_id: str                      # corvinlabs/ai-review/2.0.1
    download_url: str
    checksum: str


@router.post("/{plugin_id}/install", response_model=PluginResponse)
async def install_plugin(
    plugin_id: str,
    request: InstallRequest
):
    """Install plugin from marketplace.
    
    Downloads, verifies checksum, extracts, and adds to registry.
    """
    try:
        from core.orchestration.plugin_system.managers.marketplace import (
            MarketplaceDownloadManager
        )
        
        user_id = "anonymous"
        manager = MarketplaceDownloadManager()
        
        # Download + verify + extract
        plugin_dir = await manager.install_plugin(
            url=request.download_url,
            expected_checksum=request.checksum,
            install_dir=Path(".corvin/tenants/_default/plugins"),
            plugin_id=plugin_id
        )
        
        # Create Plugin object from extracted manifest
        # (in real impl, would parse manifest.json from plugin_dir)
        plugin = Plugin(
            id=plugin_id,
            version="1.0.0",  # TODO: read from manifest
            name=plugin_id.replace("-", " ").title(),
            installed_at=datetime.utcnow(),
            installed_by=user_id,
            marketplace=MarketplaceMetadata(
                source="marketplace",
                artifact_url=request.download_url,
                checksum=request.checksum,
                size_bytes=0,
                cached_locally=True
            )
        )
        
        # Add to registry
        lifecycle_manager.install(plugin, user_id=user_id)
        
        return PluginResponse.from_plugin(plugin)
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
