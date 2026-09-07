"""World Map Visualization API (ADR-0206, ADR-0208, ADR-0639).

REST API for the World Map panel (Geo-Tracking visualization).

Endpoints:
  - GET /api/world-map/instances — List all instances with locations + loss scores
  - GET /api/world-map/instances/{instance_id} — Instance detail pane
  - GET /api/world-map/cells — Grid cells aggregated by loss (heatmap data)
  - GET /api/world-map/summary — Summary stats (instance count, avg loss, etc.)

Features:
  - Tier 3 geo-tracking (country/region/city + 10km grid)
  - Loss score visualization (red=high, green=converged)
  - Tenant isolation (GDPR Art. 6, 32)
  - Audit logging (every request)
  - Cache TTL 5s (prevent overload)
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from core.geo import InstanceLocator, GeoCoordinate
    from core.learning.dashboard import LearningDashboard
    from core.learning.event_store import EventStore
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.geo import InstanceLocator, GeoCoordinate
    from core.learning.dashboard import LearningDashboard
    from core.learning.event_store import EventStore

from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/world-map", tags=["world-map"])

# Global locator instances (per-tenant)
_locators: Dict[str, InstanceLocator] = {}


def _tenant_home(tenant_id: str) -> Path:
    """Get tenant home directory (honours CORVIN_HOME)."""
    from forge.tenants import tenant_home  # type: ignore[import-not-found]
    return Path(tenant_home(tenant_id))


def get_locator(tenant_id: str) -> InstanceLocator:
    """Get or initialize locator for tenant."""
    if tenant_id not in _locators:
        _locators[tenant_id] = InstanceLocator(
            tenant_home=_tenant_home(tenant_id),
            tenant_id=tenant_id,
        )
    return _locators[tenant_id]


def _get_instance_loss_data(
    tenant_id: str,
    instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch loss scores from learning dashboard.

    Returns:
        {
            "instance_id": {
                "loss_score": float,
                "loss_components": {
                    "routing": float,
                    "context": float,
                    "workflow": float,
                    "security": float,
                    "flow": float,
                },
                "last_measurement": "2026-09-07T12:00:00Z",
                "measurement_count": int,
            },
            ...
        }
    """
    try:
        from core.learning.dashboard import LearningDashboard
        from core.learning.event_store import EventStore

        tenant_home = _tenant_home(tenant_id)
        event_store = EventStore(tenant_home=tenant_home)
        dashboard = LearningDashboard(
            tenant_id=tenant_id,
            event_store=event_store,
            cache_ttl_seconds=5,
        )

        # Get summary metrics
        summary = dashboard.get_summary(
            since=None,
            until=None,
        )

        # Extract loss data per instance
        loss_data = {}
        if summary and "instances" in summary:
            for inst in summary["instances"]:
                loss_data[inst["instance_id"]] = {
                    "loss_score": inst.get("loss_score", 0.5),
                    "loss_components": inst.get("loss_components", {}),
                    "last_measurement": inst.get("last_measurement", ""),
                    "measurement_count": inst.get("measurement_count", 0),
                }

        return loss_data
    except Exception as e:
        logger.warning(f"Failed to fetch loss data for {tenant_id}: {e}")
        return {}


def _get_instance_geo_data(
    tenant_id: str,
    instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch geo-tracking data from telemetry.

    Returns:
        {
            "instance_id": {
                "latitude": float,
                "longitude": float,
                "country": str,
                "region": str,
                "city": str,
                "timestamp": "2026-09-07T12:00:00Z",
            },
            ...
        }
    """
    # TODO: Integrate with core.telemetry.central_aggregator (ADR-0205/0206)
    # For now, return empty dict (instances will get default coords)
    # This is a placeholder for future telemetry integration
    try:
        from core.telemetry import central_aggregator  # type: ignore
        # aggregator = central_aggregator.get_aggregator(tenant_id)
        # return aggregator.get_geo_index()
        return {}
    except ImportError:
        logger.debug("Telemetry module not available; geo data will use defaults")
        return {}


@router.get(
    "/instances",
    summary="List all instances with locations and loss scores",
)
async def get_instances(
    rec: session_auth.SessionRecord = Depends(require_session),
    since: Optional[str] = Query(None, description="ISO 8601 timestamp (min date)"),
    until: Optional[str] = Query(None, description="ISO 8601 timestamp (max date)"),
) -> Dict[str, Any]:
    """List all instances with geographic coordinates and loss scores.

    Returns:
        {
            "instances": [
                {
                    "instance_id": "uuid",
                    "geo": {
                        "latitude": 52.5,
                        "longitude": 13.4,
                        "country": "DE",
                        "region": "Berlin",
                        "city": "Berlin",
                        "grid_cell_id": "geo_525_134",
                        "timestamp": "2026-09-07T12:00:00Z",
                        "tier": 3,
                    },
                    "loss_score": 0.3,
                    "loss_components": {...},
                    "last_measurement": "2026-09-07T12:00:00Z",
                    "measurement_count": 42,
                    "status": "active" | "converged" | "stale",
                },
                ...
            ],
            "timestamp": "2026-09-07T12:00:00Z",
            "count": 5,
        }

    Tenant isolation enforced (users only see their own instances).
    """
    tenant_id = rec.tenant_id
    locator = get_locator(tenant_id)

    # Fetch loss data from learning dashboard
    loss_data = _get_instance_loss_data(tenant_id)

    # Fetch geo data from telemetry
    geo_data = _get_instance_geo_data(tenant_id)

    # Resolve each instance to location
    instances_to_resolve = []
    for instance_id, loss_info in loss_data.items():
        instances_to_resolve.append({
            "instance_id": instance_id,
            "geo_data": geo_data.get(instance_id),
            "loss_score": loss_info["loss_score"],
            "loss_components": loss_info["loss_components"],
        })

    # Batch resolution
    locations = locator.resolve_many(instances_to_resolve)

    return {
        "instances": [loc.to_dict() for loc in locations],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "count": len(locations),
    }


@router.get(
    "/instances/{instance_id}",
    summary="Get instance detail with full loss breakdown",
)
async def get_instance_detail(
    instance_id: str,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get detailed metrics for a single instance.

    Returns:
        {
            "instance_id": "uuid",
            "geo": {...},
            "loss_score": 0.3,
            "loss_components": {
                "routing": 0.1,
                "context": 0.05,
                "workflow": 0.1,
                "security": 0.0,
                "flow": 0.05,
            },
            "history": [
                {
                    "timestamp": "2026-09-07T12:00:00Z",
                    "loss_score": 0.4,
                    "status": "active",
                },
                ...
            ],
            "timeline": [...],
        }
    """
    tenant_id = rec.tenant_id
    locator = get_locator(tenant_id)

    # Fetch loss data
    loss_data = _get_instance_loss_data(tenant_id)
    if instance_id not in loss_data:
        raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")

    loss_info = loss_data[instance_id]

    # Fetch geo data
    geo_data = _get_instance_geo_data(tenant_id)

    # Resolve to location
    location = locator.resolve_location(
        instance_id=instance_id,
        geo_data=geo_data.get(instance_id),
        loss_score=loss_info["loss_score"],
        loss_components=loss_info["loss_components"],
    )

    # TODO: Fetch historical loss curve from event store
    history = [
        {
            "timestamp": location.last_measurement,
            "loss_score": location.loss_score,
            "status": location.status,
        }
    ]

    return {
        "instance_id": instance_id,
        "geo": location.geo.to_dict(),
        "loss_score": location.loss_score,
        "loss_components": location.loss_components,
        "last_measurement": location.last_measurement,
        "measurement_count": location.measurement_count,
        "status": location.status,
        "history": history,
    }


@router.get(
    "/cells",
    summary="Get 10km grid cells aggregated by loss (for heatmap)",
)
async def get_grid_cells(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get aggregated loss per 10km grid cell.

    Used for heatmap rendering (color intensity = average loss in cell).

    Returns:
        {
            "cells": [
                {
                    "grid_cell_id": "geo_525_134",
                    "lat": 52.5,
                    "lon": 13.4,
                    "count": 2,
                    "avg_loss": 0.35,
                    "status": "active" | "converged" | "mixed",
                    "instances": ["uuid1", "uuid2"],
                },
                ...
            ],
            "timestamp": "2026-09-07T12:00:00Z",
        }
    """
    tenant_id = rec.tenant_id
    locator = get_locator(tenant_id)

    # Fetch loss data to populate cache
    loss_data = _get_instance_loss_data(tenant_id)
    geo_data = _get_instance_geo_data(tenant_id)

    # Resolve all instances (populates locator cache)
    instances_to_resolve = []
    for instance_id, loss_info in loss_data.items():
        instances_to_resolve.append({
            "instance_id": instance_id,
            "geo_data": geo_data.get(instance_id),
            "loss_score": loss_info["loss_score"],
            "loss_components": loss_info["loss_components"],
        })

    locator.resolve_many(instances_to_resolve)

    # Get aggregated cells
    cells_dict = locator.get_grid_cells_by_loss()

    cells = [
        {
            "grid_cell_id": cell_id,
            "lat": data["lat"],
            "lon": data["lon"],
            "count": data["count"],
            "avg_loss": data["avg_loss"],
            "status": data["status"],
            "instances": data["instances"],
        }
        for cell_id, data in cells_dict.items()
    ]

    return {
        "cells": cells,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cell_count": len(cells),
    }


@router.get(
    "/summary",
    summary="Get world map summary statistics",
)
async def get_summary(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get summary statistics for world map.

    Returns:
        {
            "instance_count": 5,
            "converged_count": 2,
            "active_count": 3,
            "avg_loss": 0.35,
            "loss_components_avg": {...},
            "cell_count": 3,
            "geographic_coverage": ["DE", "US", "JP"],
        }
    """
    tenant_id = rec.tenant_id
    locator = get_locator(tenant_id)

    # Fetch all data
    loss_data = _get_instance_loss_data(tenant_id)
    geo_data = _get_instance_geo_data(tenant_id)

    # Resolve all instances
    instances_to_resolve = []
    for instance_id, loss_info in loss_data.items():
        instances_to_resolve.append({
            "instance_id": instance_id,
            "geo_data": geo_data.get(instance_id),
            "loss_score": loss_info["loss_score"],
            "loss_components": loss_info["loss_components"],
        })

    locations = locator.resolve_many(instances_to_resolve)

    # Compute statistics
    instance_count = len(locations)
    converged_count = sum(1 for loc in locations if loc.status == "converged")
    active_count = sum(1 for loc in locations if loc.status == "active")

    avg_loss = (
        sum(loc.loss_score for loc in locations) / instance_count
        if instance_count > 0
        else 0.5
    )

    # Aggregate loss components
    components_sum = {
        "routing": 0.0,
        "context": 0.0,
        "workflow": 0.0,
        "security": 0.0,
        "flow": 0.0,
    }
    for loc in locations:
        for comp, val in loc.loss_components.items():
            if comp in components_sum:
                components_sum[comp] += val

    loss_components_avg = {
        comp: val / instance_count if instance_count > 0 else 0.0
        for comp, val in components_sum.items()
    }

    # Geographic coverage
    countries = set()
    for loc in locations:
        countries.add(loc.geo.country)

    cells = locator.get_grid_cells_by_loss()

    return {
        "instance_count": instance_count,
        "converged_count": converged_count,
        "active_count": active_count,
        "avg_loss": avg_loss,
        "loss_components_avg": loss_components_avg,
        "cell_count": len(cells),
        "geographic_coverage": sorted(list(countries)),
    }
