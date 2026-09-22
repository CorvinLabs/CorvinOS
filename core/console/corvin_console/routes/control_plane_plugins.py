"""
Control Plane Routes — Plugin Management Stream 1.

PUT    /v1/console/control-plane/plugins/install
GET    /v1/console/control-plane/plugins
GET    /v1/console/control-plane/plugins/<id>
PATCH  /v1/console/control-plane/plugins/<id>/enable
PATCH  /v1/console/control-plane/plugins/<id>/disable
DELETE /v1/console/control-plane/plugins/<id>
GET    /v1/console/control-plane/plugins/audit-log

ADR-2029: User-Centric CorvinOS Control Plane — Stream 1
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from corvin_console.control_plane.plugin_manager import PluginManager, PluginInfo

router = APIRouter(
    prefix="/v1/console/control-plane/plugins",
    tags=["control-plane-plugins"]
)

# Singleton plugin manager
_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get or create singleton plugin manager."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager


class PluginInstallRequest(BaseModel):
    """Request to install a plugin."""
    plugin_id: str
    name: str
    version: str
    boot_layer: str  # bundled, installed, community


class PluginOperationResponse(BaseModel):
    """Response from plugin operation."""
    status: str  # success, error, warning
    message: str
    code: Optional[int] = None


@router.put("/install")
async def install_plugin(req: PluginInstallRequest) -> PluginOperationResponse:
    """
    Install a plugin from marketplace.

    Args:
        req: PluginInstallRequest

    Returns:
        Installation result
    """
    manager = get_plugin_manager()
    result = await manager.install_plugin(
        plugin_id=req.plugin_id,
        name=req.name,
        version=req.version,
        boot_layer=req.boot_layer,
        tenant_id="default",
        operator_id="console-user"
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return PluginOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.get("")
async def list_plugins() -> List[Dict[str, Any]]:
    """
    List all plugins.

    Returns:
        List of plugin info
    """
    manager = get_plugin_manager()
    plugins = await manager.list_plugins()

    return [
        {
            "plugin_id": p.plugin_id,
            "name": p.name,
            "version": p.version,
            "boot_layer": p.boot_layer,
            "enabled": p.enabled,
            "dependencies": p.dependencies,
            "dependents": p.dependents,
            "installed_at": p.installed_at,
            "updated_at": p.updated_at,
        }
        for p in plugins
    ]


@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str) -> Dict[str, Any]:
    """
    Get plugin info by ID.

    Args:
        plugin_id: Plugin identifier

    Returns:
        Plugin info
    """
    manager = get_plugin_manager()
    plugin = await manager.get_plugin(plugin_id)

    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")

    return {
        "plugin_id": plugin.plugin_id,
        "name": plugin.name,
        "version": plugin.version,
        "boot_layer": plugin.boot_layer,
        "enabled": plugin.enabled,
        "dependencies": plugin.dependencies,
        "dependents": plugin.dependents,
        "installed_at": plugin.installed_at,
        "updated_at": plugin.updated_at,
    }


@router.patch("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str) -> PluginOperationResponse:
    """
    Enable an installed plugin.

    Args:
        plugin_id: Plugin identifier

    Returns:
        Operation result
    """
    manager = get_plugin_manager()
    result = await manager.enable_plugin(
        plugin_id=plugin_id,
        tenant_id="default",
        operator_id="console-user"
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return PluginOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.patch("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str) -> PluginOperationResponse:
    """
    Disable an enabled plugin.

    Graceful shutdown: 30s timeout to flush state.
    Blocked if dependents exist (403 Forbidden).

    Args:
        plugin_id: Plugin identifier

    Returns:
        Operation result
    """
    manager = get_plugin_manager()
    result = await manager.disable_plugin(
        plugin_id=plugin_id,
        tenant_id="default",
        operator_id="console-user"
    )

    if result["status"] == "error":
        code = result.get("code", 400)
        raise HTTPException(status_code=code, detail=result["message"])

    return PluginOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: str) -> PluginOperationResponse:
    """
    Uninstall a plugin.

    Must be disabled first (403 if enabled).

    Args:
        plugin_id: Plugin identifier

    Returns:
        Operation result
    """
    manager = get_plugin_manager()
    result = await manager.uninstall_plugin(
        plugin_id=plugin_id,
        tenant_id="default",
        operator_id="console-user"
    )

    if result["status"] == "error":
        code = result.get("code", 400)
        raise HTTPException(status_code=code, detail=result["message"])

    return PluginOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.get("/audit-log")
async def get_audit_log(limit: int = Query(10, ge=1, le=100)) -> List[Dict[str, Any]]:
    """
    Get plugin operation audit log (read-only, immutable).

    Args:
        limit: Max results (1-100)

    Returns:
        List of audit events
    """
    manager = get_plugin_manager()
    events = manager.get_audit_log()

    return events[-limit:]  # Return last N events
