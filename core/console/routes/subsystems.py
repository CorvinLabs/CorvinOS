from core.security.csrf import require_csrf
"""API routes for Subsystem Control — Operator authority & transparency (ADR-2029).

Endpoints:
  GET    /v1/subsystems                      — List all subsystems for tenant
  GET    /v1/subsystems/{subsystem_id}       — Get subsystem details
  POST   /v1/subsystems/{subsystem_id}/control — Enable/disable/restart subsystem
  POST   /v1/subsystems/{subsystem_id}/health  — Health check

All operations are immutably audited with tenant_id isolation.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from enum import Enum

try:
    from fastapi import APIRouter, HTTPException, Body, Depends
except ImportError:
    APIRouter = None

logger = logging.getLogger(__name__)


class SubsystemControlAction(str, Enum):
    """Subsystem control actions."""
    ENABLE = "enable"
    DISABLE = "disable"
    RESTART = "restart"
    PAUSE = "pause"
    RESUME = "resume"


def create_subsystems_router(
    subsystem_registry: Any,
    subsystem_controller: Any,
    get_current_tenant: Any = None,
) -> Any:
    """Create FastAPI router for subsystem control endpoints.

    Args:
        subsystem_registry: SubsystemRegistry instance
        subsystem_controller: SubsystemController instance
        get_current_tenant: Dependency to extract tenant_id from request

    Returns:
        FastAPI APIRouter or None if FastAPI unavailable
    """
    if APIRouter is None:
        logger.warning("FastAPI not available, subsystem routes unavailable")
        return None

    router = APIRouter(prefix="/v1/subsystems", tags=["subsystems"])

    @router.get("", status_code=200)
    async def list_subsystems(tenant_id: str = Body(default="_default")) -> dict:
        """List all subsystems for a tenant.

        Returns:
            Dict with list of subsystem instances
        """
        try:
            # Fail-closed: validate tenant_id
            if not tenant_id:
                raise HTTPException(status_code=400, detail="tenant_id required")

            subsystems = subsystem_registry.list_subsystems_by_tenant(tenant_id)

            # Emit immutable audit event
            await subsystem_registry.audit.log_event(
                "subsystems_list_queried",
                {
                    "tenant_id": tenant_id,
                    "count": len(subsystems),
                    "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                }
            )

            return {
                "subsystems": [s.to_dict() for s in subsystems],
                "count": len(subsystems),
                "tenant_id": tenant_id,
            }
        except Exception as e:
            logger.error(f"Failed to list subsystems: {e}")
            raise HTTPException(status_code=500, detail={"error": "list_failed", "reason": str(e)})

    @router.get("/{subsystem_id}", status_code=200)
    async def get_subsystem_status(
        subsystem_id: str,
        tenant_id: str = Body(default="_default"),
    ) -> dict:
        """Get detailed status of a subsystem.

        Args:
            subsystem_id: Subsystem to query
            tenant_id: Tenant scope

        Returns:
            Subsystem instance dict with status details
        """
        try:
            # Fail-closed validation
            if not subsystem_id or not tenant_id:
                raise HTTPException(status_code=400, detail="subsystem_id and tenant_id required")

            instance = await subsystem_registry.get_subsystem(subsystem_id, tenant_id)
            if not instance:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "not_found", "subsystem_id": subsystem_id}
                )

            # Emit immutable audit event
            await subsystem_registry.audit.log_event(
                "subsystem_status_queried",
                {
                    "subsystem_id": subsystem_id,
                    "tenant_id": tenant_id,
                    "health_status": instance.health_status,
                    "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                }
            )

            return instance.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get subsystem status for {subsystem_id}: {e}")
            raise HTTPException(status_code=500, detail={"error": "status_failed", "reason": str(e)})

    @require_csrf
    @router.post("/{subsystem_id}/control", status_code=200)
    async def control_subsystem(
        subsystem_id: str,
        action: SubsystemControlAction = Body(...),
        tenant_id: str = Body(default="_default"),
    ) -> dict:
        """Control a subsystem (enable, disable, restart, pause, resume).

        Args:
            subsystem_id: Subsystem to control
            action: Control action (enable/disable/restart/pause/resume)
            tenant_id: Tenant scope

        Returns:
            Status dict with result
        """
        try:
            # Fail-closed validation
            if not subsystem_id or not tenant_id:
                raise HTTPException(status_code=400, detail="subsystem_id and tenant_id required")

            # Verify subsystem exists
            instance = await subsystem_registry.get_subsystem(subsystem_id, tenant_id)
            if not instance:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "not_found", "subsystem_id": subsystem_id}
                )

            result = {}

            # Execute control action via SubsystemController
            if action == SubsystemControlAction.ENABLE:
                result = await subsystem_controller.enable_subsystem(subsystem_id, tenant_id)
            elif action == SubsystemControlAction.DISABLE:
                result = await subsystem_controller.disable_subsystem(subsystem_id, tenant_id)
            elif action == SubsystemControlAction.RESTART:
                # Restart = disable + enable
                await subsystem_controller.disable_subsystem(subsystem_id, tenant_id)
                result = await subsystem_controller.enable_subsystem(subsystem_id, tenant_id)
                result["action"] = "restart"
            elif action == SubsystemControlAction.PAUSE:
                result = await subsystem_controller.pause_subsystem(subsystem_id, tenant_id)
            elif action == SubsystemControlAction.RESUME:
                result = await subsystem_controller.resume_subsystem(subsystem_id, tenant_id)
            else:
                raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

            # Emit immutable audit event for control action
            await subsystem_registry.audit.log_event(
                "subsystem_controlled",
                {
                    "subsystem_id": subsystem_id,
                    "action": action.value,
                    "tenant_id": tenant_id,
                    "result": result.get("status", "unknown"),
                    "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                }
            )

            return {
                "subsystem_id": subsystem_id,
                "action": action.value,
                "result": result,
                "tenant_id": tenant_id,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to control subsystem {subsystem_id}: {e}")
            raise HTTPException(status_code=500, detail={"error": "control_failed", "reason": str(e)})

    @require_csrf
    @router.post("/{subsystem_id}/health", status_code=200)
    async def health_check_subsystem(
        subsystem_id: str,
        tenant_id: str = Body(default="_default"),
    ) -> dict:
        """Perform health check on a subsystem.

        Args:
            subsystem_id: Subsystem to check
            tenant_id: Tenant scope

        Returns:
            Health check result dict
        """
        try:
            # Fail-closed validation
            if not subsystem_id or not tenant_id:
                raise HTTPException(status_code=400, detail="subsystem_id and tenant_id required")

            # Verify subsystem exists
            instance = await subsystem_registry.get_subsystem(subsystem_id, tenant_id)
            if not instance:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "not_found", "subsystem_id": subsystem_id}
                )

            # Run health check
            result = await subsystem_registry.check_health(subsystem_id, tenant_id)

            # Emit immutable audit event (already emitted by check_health)

            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Health check failed for {subsystem_id}: {e}")
            raise HTTPException(status_code=500, detail={"error": "health_check_failed", "reason": str(e)})

    return router
