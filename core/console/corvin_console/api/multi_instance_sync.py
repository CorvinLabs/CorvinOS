"""Multi-instance metrics sync API (Phase 7b, ADR-0277)."""

from datetime import datetime
from typing import Optional
import hashlib

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/multi-instance", tags=["multi-instance"])


@router.get("/peers")
async def list_peers():
    """List known peer instances (A2A paired devices)."""
    # TODO: Query A2A registry from ADR-0038
    # For now, return mock data
    return {
        "peers": [
            {
                "instance_id": "ubuntu-host-abc123",
                "hostname": "ubuntu-host",
                "last_seen": datetime.utcnow().isoformat(),
                "status": "online",
            },
            {
                "instance_id": "windows-dev-def456",
                "hostname": "windows-dev",
                "last_seen": datetime.utcnow().isoformat(),
                "status": "online",
            },
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/metrics/aggregate")
async def aggregate_metrics(peer_ids: Optional[str] = None):
    """Aggregate feature metrics from this instance + optionally from peers.

    If peer_ids is omitted, returns local metrics only.
    If peer_ids="all", aggregates from all online peers.
    If peer_ids="id1,id2", aggregates from specified peers.
    """
    # TODO: Query local metrics via telemetry.get_flag_metrics()
    # TODO: Fetch peer metrics via A2A RPC
    # For now, return local-only stub
    return {
        "aggregated_from": ["local"],
        "aggregation_timestamp": datetime.utcnow().isoformat(),
        "flags": [
            {
                "flag_id": "auto_load_github_repo",
                "error_rate_avg": 0.020,
                "invocation_count_total": 1500,
                "adoption_rate": 0.15,
            },
        ],
    }


@router.post("/sync-config")
async def sync_config(body: dict):
    """Sync tenant config (including preset) to peer instances."""
    peer_id = body.get("peer_id")
    config_fields = body.get("fields", ["preset"])  # which fields to sync

    if not peer_id:
        raise HTTPException(status_code=400, detail="peer_id required")

    # TODO: Send config via A2A to peer_id
    # For now, return success stub
    return {
        "status": "scheduled",
        "peer_id": peer_id,
        "fields_to_sync": config_fields,
        "message": "Config sync queued (not yet implemented)",
    }


@router.get("/sync-status/{peer_id}")
async def sync_status(peer_id: str):
    """Check sync status for a specific peer."""
    # TODO: Query A2A status log
    # Stub: always in-sync
    return {
        "peer_id": peer_id,
        "status": "in_sync",
        "last_sync": datetime.utcnow().isoformat(),
        "fields_synced": ["preset", "telemetry"],
    }
