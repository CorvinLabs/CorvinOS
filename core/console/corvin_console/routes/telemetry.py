"""Public telemetry endpoints for live instance dashboard.

GET /api/v1/telemetry/instances/live — Live world map data (aggregated, anonymized)
Returns per-country/continent instance counts and activity metrics (60-second cache).
No authentication required (public dashboard).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from starlette import status as http_status
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v1/telemetry/instances/live", tags=["telemetry"])
async def get_live_instances() -> dict:
    """Get live instance counts per country/continent.
    
    Returns aggregated, anonymized stats suitable for public dashboard.
    Updated every 60 seconds (cached).
    """
    try:
        from ..aco.telemetry_instances_api import (
            InstanceStatsAggregator,
            load_telemetry_instances_from_file,
        )
        from pathlib import Path
        import os
        
        # Load telemetry from ~/.corvin/audit.jsonl or pings database
        corvin_home = Path(os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin")))
        telemetry_dir = corvin_home / "aco" / "telemetry"
        pings_file = telemetry_dir / "pings.jsonl"
        
        # Fall back to audit.jsonl if pings don't exist
        if not pings_file.exists():
            pings_file = corvin_home / "audit.jsonl"
        
        records = load_telemetry_instances_from_file(pings_file)
        
        aggregator = InstanceStatsAggregator()
        aggregator.load_instances(records)
        stats = aggregator.get_cached_stats()
        
        return stats
    except Exception as e:
        logger.error("Failed to get live instances: %s", e, exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load telemetry data",
        )
