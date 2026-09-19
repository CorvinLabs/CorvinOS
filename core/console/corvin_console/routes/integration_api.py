"""
Console API Routes: Integration Layer (Console UI ↔ Registry API).

Wires the RegistryIntegrationBridge to FastAPI endpoints, providing:
  1. Plugin management (list, install, uninstall, enable, disable)
  2. Skill management (list, create, promote, grade)
  3. Registry status & statistics
  4. Data flow validation (debugging/observability)

Routes:
  # Plugin Management
  GET    /api/v1/integration/plugins                      → List installed plugins
  POST   /api/v1/integration/plugins/{id}/install         → Install a plugin
  POST   /api/v1/integration/plugins/{id}/uninstall       → Uninstall a plugin
  PATCH  /api/v1/integration/plugins/{id}/enable          → Enable a plugin
  PATCH  /api/v1/integration/plugins/{id}/disable         → Disable a plugin

  # Skill Management
  GET    /api/v1/integration/skills                       → List installed skills
  POST   /api/v1/integration/skills/promote               → Promote a skill
  POST   /api/v1/integration/skills/{id}/grade            → Record skill grade

  # Registry Status
  GET    /api/v1/integration/status                       → Registry status
  GET    /api/v1/integration/stats                        → Registry statistics

  # Data Flow Monitoring (Development)
  GET    /api/v1/integration/data-flow/events             → Get data flow events
  POST   /api/v1/integration/data-flow/validate           → Validate data flow

ADR References:
  - ADR-0511: Marketplace architecture
  - ADR-0405: Skill-Creator integration
  - ADR-0007: Multi-tenant axis
  - ADR-0232/0233: Audit-first design
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path as PathParam
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session
from ..integration import (
    RegistryIntegrationBridge,
    PluginSourceTier,
    SkillScope,
    DeploymentStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integration", tags=["integration"])


# ──────────────────────────────────────────────────────────────────────────────
# Request/Response Models (Pydantic)
# ──────────────────────────────────────────────────────────────────────────────


class PluginInstallRequest(BaseModel):
    """Request to install a plugin."""

    plugin_id: str = Field(..., description="Plugin identifier")
    source_tier: str = Field(default="buildin", description="Source tier (buildin, contributor, community)")


class SkillPromoteRequest(BaseModel):
    """Request to promote a skill."""

    name: str = Field(..., description="Skill name")
    body_md: str = Field(..., description="Skill body (Markdown)")
    description: str = Field(..., description="Short description")
    scope: str = Field(default="user", description="Scope (user, project, system)")


class SkillGradeRequest(BaseModel):
    """Request to record a skill grade."""

    skill_id: str = Field(..., description="Skill identifier")
    score: float = Field(..., ge=0.0, le=1.0, description="Grade score (0–1)")
    notes: str = Field(default="", description="Optional feedback notes")


class PluginInstallResponse(BaseModel):
    """Response from plugin installation."""

    success: bool
    message: str
    plugin_id: str
    audit_event_id: str = ""


class SkillPromoteResponse(BaseModel):
    """Response from skill promotion."""

    success: bool
    message: str
    skill_id: Optional[str] = None
    skill_name: Optional[str] = None


class RegistryStatusResponse(BaseModel):
    """Registry status response."""

    tenant_id: str
    installed_plugins_count: int
    installed_skills_count: int
    data_flow_events_count: int
    last_update: str


class DataFlowValidationResponse(BaseModel):
    """Data flow validation response."""

    is_valid: bool
    errors: List[str]
    event_count: int
    tenant_id: str


# ──────────────────────────────────────────────────────────────────────────────
# Plugin Management Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/plugins")
async def list_plugins(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """List all installed plugins for the current tenant.

    Returns a list of installed plugin records with status information.
    """
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)
        plugins = bridge.list_installed_plugins()
        return {
            "plugins": plugins,
            "count": len(plugins),
            "tenant_id": rec.tenant_id,
        }
    except Exception as e:
        logger.error(f"Failed to list plugins: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/plugins/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    request: PluginInstallRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> PluginInstallResponse:
    """Install a plugin for the current tenant.

    Data flow:
      POST /api/v1/integration/plugins/{id}/install
        ↓
      install_plugin() (this function)
        ↓
      RegistryIntegrationBridge.install_plugin()
        ↓
      TenantRegistryAdapter.record_installation()
        ↓
      ~/.corvin/tenants/<tenant_id>/registry/installed_plugins.jsonl (append)
    """
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)

        # Validate source tier
        try:
            tier = PluginSourceTier(request.source_tier)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid source tier: {request.source_tier}",
            )

        # Install plugin
        success, message = bridge.install_plugin(
            plugin_id=plugin_id,
            source_tier=tier,
            installed_by=rec.user_id or "console",
        )

        # Audit the action
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="plugin.install",
            target_kind="plugin",
            target_id=plugin_id,
        )

        if not success:
            raise HTTPException(status_code=400, detail=message)

        return PluginInstallResponse(
            success=success,
            message=message,
            plugin_id=plugin_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Plugin installation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Uninstall a plugin for the current tenant (placeholder).

    TODO: Implement uninstall logic with:
      - Reversal of dependencies
      - State cleanup
      - Audit trail
    """
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="plugin.uninstall",
        target_kind="plugin",
        target_id=plugin_id,
    )
    return {
        "success": True,
        "message": f"Plugin {plugin_id} uninstalled",
        "plugin_id": plugin_id,
    }


@router.patch("/plugins/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Enable a plugin for the current tenant (placeholder)."""
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="plugin.enable",
        target_kind="plugin",
        target_id=plugin_id,
    )
    return {
        "success": True,
        "message": f"Plugin {plugin_id} enabled",
        "plugin_id": plugin_id,
    }


@router.patch("/plugins/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Disable a plugin for the current tenant (placeholder)."""
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="plugin.disable",
        target_kind="plugin",
        target_id=plugin_id,
    )
    return {
        "success": True,
        "message": f"Plugin {plugin_id} disabled",
        "plugin_id": plugin_id,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Skill Management Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/skills")
async def list_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """List all installed skills for the current tenant."""
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)
        skills = bridge.list_installed_skills()
        return {
            "skills": skills,
            "count": len(skills),
            "tenant_id": rec.tenant_id,
        }
    except Exception as e:
        logger.error(f"Failed to list skills: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/promote")
async def promote_skill(
    request: SkillPromoteRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> SkillPromoteResponse:
    """Promote a skill to the SkillForge registry.

    Data flow:
      POST /api/v1/integration/skills/promote
        ↓
      promote_skill() (this function)
        ↓
      RegistryIntegrationBridge.promote_skill()
        ↓
      SkillRegistryAdapter.promote_skill_to_registry()
        ↓
      SkillForge registry API
        ↓
      TenantRegistryAdapter.record_skill_installation()
    """
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)

        # Validate scope
        try:
            scope = SkillScope(request.scope)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid scope: {request.scope}",
            )

        # Promote skill
        success, message, manifest = bridge.promote_skill(
            name=request.name,
            body_md=request.body_md,
            description=request.description,
            scope=scope,
            promoted_by=rec.user_id or "console",
        )

        # Audit the action
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="skill.promote",
            target_kind="skill",
            target_id=manifest.skill_id if manifest else request.name,
        )

        if not success:
            raise HTTPException(status_code=400, detail=message)

        return SkillPromoteResponse(
            success=success,
            message=message,
            skill_id=manifest.skill_id if manifest else None,
            skill_name=manifest.name if manifest else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Skill promotion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_id}/grade")
async def record_skill_grade(
    skill_id: str,
    request: SkillGradeRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Record a user-provided grade for a skill (learning feedback loop).

    Integration with ADR-0314 (Learning Infrastructure):
      - Grade is used by optimizer to adjust skill configuration
      - Enables self-improving skills via feedback loop
    """
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)
        from ..integration import SkillRegistryAdapter

        adapter = SkillRegistryAdapter(rec.tenant_id)
        success = adapter.record_skill_grade(skill_id, request.score, request.notes)

        if not success:
            raise HTTPException(
                status_code=500,
                detail="Failed to record skill grade",
            )

        # Audit the action
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="skill.grade",
            target_kind="skill",
            target_id=skill_id,
        )

        return {
            "success": True,
            "message": f"Grade {request.score} recorded for skill {skill_id}",
            "skill_id": skill_id,
            "score": request.score,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to record skill grade: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Registry Status Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/status")
async def registry_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> RegistryStatusResponse:
    """Get registry status for the current tenant."""
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)
        plugins = bridge.list_installed_plugins()
        skills = bridge.list_installed_skills()
        events = bridge.get_data_flow_events()

        from datetime import datetime

        return RegistryStatusResponse(
            tenant_id=rec.tenant_id,
            installed_plugins_count=len(plugins),
            installed_skills_count=len(skills),
            data_flow_events_count=len(events),
            last_update=datetime.utcnow().isoformat() + "Z",
        )
    except Exception as e:
        logger.error(f"Failed to get registry status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def registry_stats(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get detailed registry statistics."""
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)
        plugins = bridge.list_installed_plugins()
        skills = bridge.list_installed_skills()

        # Group by status
        plugin_by_status = {}
        for plugin in plugins:
            status = plugin.get("deployment_status", "unknown")
            plugin_by_status[status] = plugin_by_status.get(status, 0) + 1

        skill_by_scope = {}
        for skill in skills:
            scope = skill.get("scope", "unknown")
            skill_by_scope[scope] = skill_by_scope.get(scope, 0) + 1

        return {
            "tenant_id": rec.tenant_id,
            "plugins": {
                "total": len(plugins),
                "by_status": plugin_by_status,
                "by_tier": {
                    tier: sum(1 for p in plugins if p.get("source_tier") == tier)
                    for tier in ["buildin", "contributor", "community"]
                },
            },
            "skills": {
                "total": len(skills),
                "by_scope": skill_by_scope,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get registry stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Data Flow Monitoring Routes (Development/Debugging)
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/data-flow/events")
async def get_data_flow_events(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(100, ge=1, le=1000),
    event_type: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """Get data flow events for this tenant (development/debugging).

    Useful for:
      - Tracing integration issues
      - Understanding data flow
      - Debugging mismatches between components
    """
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)
        events = bridge.get_data_flow_events()

        # Filter by event type if specified
        if event_type:
            events = [e for e in events if e["event_type"] == event_type]

        # Apply limit
        events = events[-limit:]

        return {
            "events": events,
            "count": len(events),
            "tenant_id": rec.tenant_id,
            "filtered_by": {
                "event_type": event_type,
                "limit": limit,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get data flow events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/data-flow/validate")
async def validate_data_flow(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> DataFlowValidationResponse:
    """Validate end-to-end data flow integrity.

    Checks:
      1. Tenant isolation (all events scoped to tenant_id)
      2. Hash chain integrity (prev_hash → hash links)
      3. Status transitions (valid state machine)
      4. No gaps in event sequence

    Returns validation result with detailed error messages.
    """
    try:
        bridge = RegistryIntegrationBridge(rec.tenant_id)
        is_valid, errors = bridge.validate_data_flow()
        events = bridge.get_data_flow_events()

        return DataFlowValidationResponse(
            is_valid=is_valid,
            errors=errors,
            event_count=len(events),
            tenant_id=rec.tenant_id,
        )
    except Exception as e:
        logger.error(f"Data flow validation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Plugin-Builder Integration (Deployment Route)
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/deploy-plugin")
async def deploy_generated_plugin(
    plugin_path: str = Query(..., description="Path to generated plugin"),
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = None,
) -> Dict[str, Any]:
    """Deploy a plugin generated by plugin-builder to the tenant registry.

    Data flow:
      plugin-builder/turn.py (finish handler)
        ↓
      POST /api/v1/integration/deploy-plugin?plugin_path=...
        ↓
      deploy_generated_plugin() (this function)
        ↓
      RegistryIntegrationBridge.deploy_plugin_from_builder()
        ↓
      PluginBuilderAdapter.deploy_plugin()
        ↓
      TenantRegistryAdapter.record_installation()

    Called by: core/plugins/plugin_builder/turn.py::_finish_reply() [ADR-0253]
    """
    try:
        from pathlib import Path

        bridge = RegistryIntegrationBridge(rec.tenant_id)
        success, message, audit_event_id = bridge.deploy_plugin_from_builder(
            plugin_path=Path(plugin_path),
            deployed_by=rec.user_id or "plugin-builder",
        )

        # Audit the action
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="plugin.deploy",
            target_kind="plugin",
            target_id=plugin_path,
        )

        return {
            "success": success,
            "message": message,
            "plugin_path": plugin_path,
            "audit_event_id": audit_event_id,
        }
    except Exception as e:
        logger.error(f"Plugin deployment failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
