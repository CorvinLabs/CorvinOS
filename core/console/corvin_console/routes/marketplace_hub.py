"""
Marketplace Hub Backend Routes
Provides unified discovery + aggregation for all artifact types
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query

from core.console.auth.session_record import SessionRecord
from core.console.auth.dependencies import get_session_record
from core.compliance.audit_backend import audit_backend
from core.console.marketplace.index_cache import get_marketplace_index_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace"])


# ============================================================================
# Schemas
# ============================================================================


class PluginsStatus(BaseModel):
    installed: int
    available: int
    status: str


class SkillsStatus(BaseModel):
    active: int
    available: int
    status: str


class ToolsStatus(BaseModel):
    registered: int
    available: int
    status: str


class ConnectorsStatus(BaseModel):
    configured: int
    available: int
    status: str


class LayersStatus(BaseModel):
    builtin: int
    immutable: bool
    status: str


class MarketplaceStatus(BaseModel):
    plugins: PluginsStatus
    skills: SkillsStatus
    tools: ToolsStatus
    connectors: ConnectorsStatus
    layers: LayersStatus


class ArtifactSearchResult(BaseModel):
    id: str
    type: str  # 'plugins' | 'skills' | 'tools' | 'connectors' | 'layers'
    name: str
    category: Optional[str] = None
    tier: Optional[str] = None
    description: Optional[str] = None
    installed: bool
    link: Optional[str] = None


# ============================================================================
# Routes
# ============================================================================


@router.get("/status", response_model=MarketplaceStatus)
async def get_marketplace_status(
    rec: SessionRecord = Depends(get_session_record),
) -> MarketplaceStatus:
    """
    Aggregate installation status across all 5 artifact types.

    Tenant isolation: uses rec.tenant_id (from SessionRecord, never env)
    Audit trail: logged as marketplace_hub_status event
    Cache: 30-second client-side cache
    """
    tenant_id = rec.tenant_id

    try:
        # TODO: Implement calls to actual registries
        # For now, return mock data with zero counts (registries not yet exposed)

        status = MarketplaceStatus(
            plugins=PluginsStatus(installed=0, available=48, status="active"),
            skills=SkillsStatus(active=0, available=18, status="active"),
            tools=ToolsStatus(registered=0, available=12, status="active"),
            connectors=ConnectorsStatus(configured=0, available=8, status="pending"),
            layers=LayersStatus(builtin=6, immutable=True, status="active"),
        )

        # Audit trail
        await audit_backend.write_event({
            "tenant_id": tenant_id,
            "event_type": "marketplace_hub_status",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

        return status

    except Exception as e:
        logger.error(f"Error fetching marketplace status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch marketplace status")


@router.get("/search", response_model=List[ArtifactSearchResult])
async def search_marketplace(
    q: str = Query("", description="Search query"),
    types: List[str] = Query(
        ["plugins", "skills", "tools", "connectors", "layers"],
        description="Artifact types to search"
    ),
    category: Optional[str] = Query(None, description="Filter by category"),
    tier: Optional[str] = Query(None, description="Filter by tier"),
    rec: SessionRecord = Depends(get_session_record),
) -> List[ArtifactSearchResult]:
    """
    Cross-type search via marketplace index + local registries.

    Query string: search in name + description
    Types filter: which artifact types to include
    Category/tier filters: narrow down results
    Tenant isolation: rec.tenant_id ensures user sees only their data
    """
    tenant_id = rec.tenant_id
    results: List[ArtifactSearchResult] = []

    try:
        from core.console.paths import corvin_home
        cache_dir = corvin_home() / "tenants" / tenant_id / "marketplace"
        cache = get_marketplace_index_cache(tenant_id, cache_dir)
        index = await cache.load(refresh_if_stale=False)

        for artifact_type in types:
            if artifact_type not in index.get("artifacts", {}):
                continue

            items = index["artifacts"][artifact_type].get("items", [])

            for item in items:
                if category and item.get("category") != category:
                    continue

                if tier and item.get("tier") != tier:
                    continue

                if q:
                    name_match = q.lower() in item.get("name", "").lower()
                    desc_match = q.lower() in item.get("description", "").lower()
                    if not (name_match or desc_match):
                        continue

                results.append(ArtifactSearchResult(
                    id=item.get("id"),
                    type=artifact_type,
                    name=item.get("name", ""),
                    category=item.get("category"),
                    tier=item.get("tier"),
                    description=item.get("description"),
                    installed=await is_installed(tenant_id, artifact_type, item.get("id")),
                    link=await get_panel_link(artifact_type),
                ))

        results.sort(
            key=lambda x: (
                not x.installed,
                not (x.name.lower().startswith(q.lower()) if q else False),
            )
        )

        await audit_backend.write_event({
            "tenant_id": tenant_id,
            "event_type": "marketplace_hub_search",
            "query": q,
            "types": types,
            "results_count": len(results),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

        return results

    except Exception as e:
        logger.error(f"Error searching marketplace: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


# ============================================================================
# Helper Functions
# ============================================================================


async def is_installed(tenant_id: str, artifact_type: str, artifact_id: str) -> bool:
    """Check if an artifact is installed in the tenant."""
    # TODO: Implement per type
    # - Plugins: check if in plugin_registry.list_installed(tenant_id)
    # - Skills: check if in skill_registry.list_active(tenant_id)
    # - etc.
    return False  # Placeholder


async def get_panel_link(artifact_type: str) -> Optional[str]:
    """Get the link to the type-specific management panel."""
    links = {
        "plugins": "/console/plugin-center",
        "skills": "/console/skills",
        "tools": "/console/mcp-tools",
        "connectors": "/console/connectors",
        "layers": None,  # Read-only
    }
    return links.get(artifact_type)
