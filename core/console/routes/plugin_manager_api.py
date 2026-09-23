"""API routes for Plugin Manager v2 lifecycle.

Endpoints:
  POST   /v1/plugins/install              — Install plugin
  GET    /v1/plugins/{plugin_id}/status   — Get plugin status
  PUT    /v1/plugins/{plugin_id}/enable   — Enable plugin
  PUT    /v1/plugins/{plugin_id}/disable  — Disable plugin
  DELETE /v1/plugins/{plugin_id}          — Uninstall plugin
  GET    /v1/plugins                      — List installed plugins

ADR-0243 amendments: Plugin Manager v2 as marketplace integration hub.
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Body
except ImportError:
    APIRouter = None


log = logging.getLogger(__name__)


def create_plugin_manager_router(plugin_manager: Any) -> Any:
    """Create FastAPI router for plugin manager endpoints."""
    if APIRouter is None:
        log.warning("FastAPI not available, plugin manager routes unavailable")
        return None

    router = APIRouter(prefix="/v1/plugins", tags=["plugins"])

    @router.post("/install", status_code=202)
    async def install_plugin(
        plugin_id: str = Body(...),
        version: str = Body(...),
        manifest_url: str = Body(...),
        binary_url: str = Body(...),
        signature: str | None = Body(None),
        licensing_tier: str = Body("free"),
    ) -> dict:
        """Install a plugin from Marketplace (202 Accepted)."""
        from core.plugins.plugin_manager import InstallRequest, QuotaExceeded

        try:
            req = InstallRequest(
                plugin_id=plugin_id,
                version=version,
                manifest_url=manifest_url,
                binary_url=binary_url,
                signature=signature,
            )

            status = plugin_manager.install(req, licensing_tier=licensing_tier)
            return {
                "plugin_id": status.plugin_id,
                "version": status.version,
                "status": status.status,
                "install_id": status.install_id,
                "eta_seconds": status.eta_seconds,
                "licensing_check": status.licensing_check,
            }

        except QuotaExceeded as e:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "quota_exceeded",
                    "reason": str(e),
                    "licensing_tier": licensing_tier,
                },
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail={"error": "install_failed", "reason": str(e)})

    @router.get("/{plugin_id}/status", status_code=200)
    async def get_plugin_status(plugin_id: str, version: str) -> dict:
        """Get status of an installed plugin."""
        status = plugin_manager.get_plugin_status(plugin_id, version)
        if status is None:
            raise HTTPException(status_code=404, detail={"error": "plugin_not_found"})
        return status

    @router.put("/{plugin_id}/enable", status_code=200)
    async def enable_plugin(plugin_id: str, version: str = Body(...)) -> dict:
        """Enable a plugin."""
        try:
            return plugin_manager.set_plugin_enabled(plugin_id, version, True)
        except Exception as e:
            raise HTTPException(status_code=400, detail={"error": "enable_failed", "reason": str(e)})

    @router.put("/{plugin_id}/disable", status_code=200)
    async def disable_plugin(plugin_id: str, version: str = Body(...)) -> dict:
        """Disable a plugin."""
        try:
            return plugin_manager.set_plugin_enabled(plugin_id, version, False)
        except Exception as e:
            raise HTTPException(status_code=400, detail={"error": "disable_failed", "reason": str(e)})

    @router.delete("/{plugin_id}", status_code=200)
    async def uninstall_plugin(plugin_id: str, version: str = Body(...), force: bool = Body(False)) -> dict:
        """Uninstall a plugin."""
        try:
            return plugin_manager.uninstall(plugin_id, version, force=force)
        except Exception as e:
            raise HTTPException(status_code=400, detail={"error": "uninstall_failed", "reason": str(e)})

    @router.get("", status_code=200)
    async def list_plugins() -> dict:
        """List all installed plugins."""
        plugins = plugin_manager.list_installed_plugins()
        return {"plugins": plugins, "count": len(plugins)}

    return router
