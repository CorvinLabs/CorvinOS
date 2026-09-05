"""Console API: Capabilities Discovery (ADR-0610)."""

from __future__ import annotations

import logging
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query
except ImportError:
    APIRouter = None

from core.plugins.corvin_plugins.capability_registry import get_registry
from core.plugins.corvin_plugins.manifest_capabilities import CapabilityType

log = logging.getLogger(__name__)


def create_capabilities_router() -> APIRouter:
    """Create FastAPI router for capabilities discovery."""
    if APIRouter is None:
        raise ImportError("FastAPI not installed")

    router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])

    @router.get("")
    async def list_all_capabilities() -> dict[str, Any]:
        """GET /api/capabilities — All plugins and their capabilities."""
        registry = get_registry()
        result = {}

        for plugin_id, manifest in registry.all_plugins().items():
            result[plugin_id] = {
                "id": plugin_id,
                "version": manifest.plugin_version,
                "capabilities_version": manifest.capabilities_version,
                "capabilities": [
                    {
                        "id": cap.id,
                        "type": cap.type.value,
                        "description": cap.description,
                        "slo_latency_ms": cap.slo_latency_ms,
                        "slo_error_rate": cap.slo_error_rate,
                        "audit_event": cap.audit_event,
                    }
                    for cap in manifest.capabilities
                ],
            }

        return {"plugins": result}

    @router.get("/by_type/{capability_type}")
    async def list_by_type(
        capability_type: str,
        min_version: str = Query("1.0", description="Minimum capabilities_version"),
    ) -> dict[str, Any]:
        """GET /api/capabilities/by_type/{type} — All plugins offering a capability type."""
        try:
            cap_type = CapabilityType(capability_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown capability type: {capability_type}. "
                f"Valid types: {[t.value for t in CapabilityType]}",
            )

        registry = get_registry()
        implementations = registry.find_implementations(cap_type, min_version=min_version)

        return {
            "type": capability_type,
            "min_capabilities_version": min_version,
            "implementations": [
                {
                    "plugin_id": plugin_id,
                    "plugin_version": registry.get_plugin_manifest(plugin_id).plugin_version,
                    "capability_id": cap.id,
                    "description": cap.description,
                    "slo_latency_ms": cap.slo_latency_ms,
                    "slo_error_rate": cap.slo_error_rate,
                    "audit_event": cap.audit_event,
                }
                for plugin_id, cap in implementations
            ],
        }

    @router.get("/by_plugin/{plugin_id}")
    async def list_by_plugin(plugin_id: str) -> dict[str, Any]:
        """GET /api/capabilities/by_plugin/{plugin_id} — All capabilities for a plugin."""
        registry = get_registry()
        manifest = registry.get_plugin_manifest(plugin_id)

        if not manifest:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")

        return {
            "plugin_id": plugin_id,
            "plugin_version": manifest.plugin_version,
            "capabilities_version": manifest.capabilities_version,
            "capabilities": [
                {
                    "id": cap.id,
                    "type": cap.type.value,
                    "description": cap.description,
                    "parameters": cap.parameters,
                    "returns": cap.returns,
                    "slo_latency_ms": cap.slo_latency_ms,
                    "slo_error_rate": cap.slo_error_rate,
                    "audit_event": cap.audit_event,
                    "on_failure": cap.on_failure.value,
                }
                for cap in manifest.capabilities
            ],
        }

    @router.get("/by_plugin/{plugin_id}/capability/{capability_id}")
    async def get_specific_capability(plugin_id: str, capability_id: str) -> dict[str, Any]:
        """GET /api/capabilities/by_plugin/{plugin_id}/capability/{id} — Single capability."""
        registry = get_registry()
        cap = registry.get_capability(plugin_id, capability_id)

        if not cap:
            raise HTTPException(
                status_code=404,
                detail=f"Capability {capability_id} not found for plugin {plugin_id}",
            )

        return {
            "plugin_id": plugin_id,
            "capability": {
                "id": cap.id,
                "type": cap.type.value,
                "description": cap.description,
                "parameters": cap.parameters,
                "returns": cap.returns,
                "slo_latency_ms": cap.slo_latency_ms,
                "slo_error_rate": cap.slo_error_rate,
                "audit_event": cap.audit_event,
                "on_failure": cap.on_failure.value,
                "fallback_capability_id": cap.fallback_capability_id,
                "added_in": cap.added_in,
                "deprecated_in": cap.deprecated_in,
                "removed_in": cap.removed_in,
            },
        }

    return router
