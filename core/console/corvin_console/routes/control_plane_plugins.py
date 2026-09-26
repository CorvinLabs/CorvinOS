"""
Control Plane Routes — Plugin Management Stream 1.

GET    /v1/console/control-plane/plugins
GET    /v1/console/control-plane/plugins/<id>
PATCH  /v1/console/control-plane/plugins/<id>/enable
PATCH  /v1/console/control-plane/plugins/<id>/disable
DELETE /v1/console/control-plane/plugins/<id>
GET    /v1/console/control-plane/plugins/audit-log

ADR-2029: User-Centric CorvinOS Control Plane — Stream 1

There is deliberately NO install route here. ADR-0892 allows exactly one plugin
install path: ``POST /api/v1/marketplace/plugins/{id}/install``
(``marketplace_install.py``), which resolves real source from the marketplace
checkout, runs the manifest + licence gates and writes the tenant registry. The
``PUT /install`` that lived here recorded an operator-typed id/name/version in a
separate store and installed no code — a second install path, removed
2026-09-26. Guard: ``tests/e2e/test_marketplace_single_install_route.py``.
"""

from typing import Optional, List, Dict, Any, Annotated
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from corvin_console.control_plane.plugin_manager import PluginManager, PluginInfo
from corvin_console.deps import require_session, require_csrf, consent_required
from corvin_console import auth as session_auth

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


class PluginOperationResponse(BaseModel):
    """Response from plugin operation."""
    status: str  # success, error, warning
    message: str
    code: Optional[int] = None


@router.get("")
async def list_plugins(
    session: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _: Annotated[None, Depends(consent_required("plugin_management"))] = None
) -> List[Dict[str, Any]]:
    """
    List all plugins for the current tenant (tenant-scoped).

    Args:
        session: Session record (extracted from session cookie)

    Returns:
        List of plugin info for this tenant only
    """
    manager = get_plugin_manager()
    try:
        plugins = await manager.list_plugins(tenant_id=session.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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
            "tenant_id": p.tenant_id,
        }
        for p in plugins
    ]


@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _: Annotated[None, Depends(consent_required("plugin_management"))] = None
) -> Dict[str, Any]:
    """
    Get plugin info by ID (tenant-scoped).

    Args:
        plugin_id: Plugin identifier
        session: Session record (extracted from session cookie)

    Returns:
        Plugin info (only if it belongs to this tenant)

    Raises:
        HTTPException: If plugin not found or doesn't belong to tenant
    """
    manager = get_plugin_manager()
    try:
        plugin = await manager.get_plugin(plugin_id=plugin_id, tenant_id=session.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found for tenant {session.tenant_id}")

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
        "tenant_id": plugin.tenant_id,
    }


@router.patch("/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[None, Depends(consent_required("plugin_management"))] = None
) -> PluginOperationResponse:
    """
    Enable an installed plugin (tenant-scoped, CSRF-protected).

    Args:
        plugin_id: Plugin identifier
        session: Session record (extracted from CSRF-protected cookie)

    Returns:
        Operation result

    Raises:
        HTTPException: If plugin enable fails
    """
    manager = get_plugin_manager()
    try:
        result = await manager.enable_plugin(
            plugin_id=plugin_id,
            tenant_id=session.tenant_id,
            operator_id=session.sid
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result["status"] == "error":
        status_code = result.get("code", 400)
        raise HTTPException(status_code=status_code, detail=result["message"])

    return PluginOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.patch("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[None, Depends(consent_required("plugin_management"))] = None
) -> PluginOperationResponse:
    """
    Disable an enabled plugin (tenant-scoped, CSRF-protected).

    Graceful shutdown: flushes state before disabling.
    Blocked if dependents exist (403 Forbidden).

    Args:
        plugin_id: Plugin identifier
        session: Session record (extracted from CSRF-protected cookie)

    Returns:
        Operation result

    Raises:
        HTTPException: If plugin disable fails or dependents exist
    """
    manager = get_plugin_manager()
    try:
        result = await manager.disable_plugin(
            plugin_id=plugin_id,
            tenant_id=session.tenant_id,
            operator_id=session.sid
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result["status"] == "error":
        code = result.get("code", 400)
        raise HTTPException(status_code=code, detail=result["message"])

    return PluginOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.delete("/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[None, Depends(consent_required("plugin_management"))] = None
) -> PluginOperationResponse:
    """
    Uninstall a plugin (tenant-scoped, CSRF-protected).

    Must be disabled first (403 if enabled).

    Args:
        plugin_id: Plugin identifier
        session: Session record (extracted from CSRF-protected cookie)

    Returns:
        Operation result

    Raises:
        HTTPException: If plugin uninstall fails
    """
    manager = get_plugin_manager()
    try:
        result = await manager.uninstall_plugin(
            plugin_id=plugin_id,
            tenant_id=session.tenant_id,
            operator_id=session.sid
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result["status"] == "error":
        code = result.get("code", 400)
        raise HTTPException(status_code=code, detail=result["message"])

    return PluginOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.get("/audit-log")
async def get_audit_log(
    limit: int = Query(10, ge=1, le=100),
    session: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
    _: Annotated[None, Depends(consent_required("plugin_management"))] = None
) -> List[Dict[str, Any]]:
    """
    Get plugin operation audit log for a tenant (read-only, immutable, tenant-scoped).

    Args:
        limit: Max results (1-100)
        session: Session record (extracted from session cookie)

    Returns:
        List of audit events for this tenant only
    """
    manager = get_plugin_manager()
    try:
        events = manager.get_audit_log(tenant_id=session.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return events[-limit:]  # Return last N events
