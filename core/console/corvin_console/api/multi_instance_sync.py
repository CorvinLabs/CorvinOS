"""Multi-instance metrics sync API (Phase 7b/9a, ADR-0277)."""

from datetime import datetime
from typing import Optional
import hashlib
import json

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/multi-instance", tags=["multi-instance"])


# ── A2A Integration (Phase 9a) ─────────────────────────────────────────────
# ADR-0038: Agent-to-Agent TaskEnvelope protocol. Stubs for RPC calls to peers.

class A2ATaskEnvelope:
    """Minimal A2A TaskEnvelope for metric sync (Phase 9a)."""

    def __init__(self, method: str, params: dict, peer_id: str):
        self.method = method
        self.params = params
        self.peer_id = peer_id
        self.request_id = hashlib.sha256(
            f"{peer_id}:{method}:{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "method": self.method,
            "params": self.params,
            "target_peer": self.peer_id,
        }


async def _a2a_rpc_call(method: str, peer_id: str, params: dict) -> Optional[dict]:
    """Send RPC call to peer via A2A protocol (Phase 9a).

    TODO: Wire to actual A2A TaskEnvelope dispatch via corvin_orchestration.a2a_send().
    For now: returns mock data or None.
    """
    # TODO: from forge.orchestration import a2a_send
    # envelope = A2ATaskEnvelope(method, params, peer_id)
    # result = await a2a_send(envelope, timeout_s=10)
    # return result

    # Mock: simulate peer response
    if method == "get_metrics":
        return {
            "peer_id": peer_id,
            "invocation_count_24h": 500,
            "error_rate_24h": 0.008,
            "adoption_rate": 0.12,
        }
    return None


@router.get("/peers")
async def list_peers():
    """List known peer instances (A2A paired devices, Phase 9a)."""
    # TODO: Query A2A registry via forge.a2a.list_peers() from ADR-0038
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
    """Aggregate feature metrics from this instance + optionally from peers (Phase 9a).

    If peer_ids is omitted, returns local metrics only.
    If peer_ids="all", aggregates from all online peers via A2A RPC.
    If peer_ids="id1,id2", aggregates from specified peers.
    """
    aggregated_from = ["local"]
    peer_metrics = []

    if peer_ids:
        # Parse peer list
        if peer_ids == "all":
            # TODO: Get all online peers from A2A registry
            target_peers = ["ubuntu-host-abc123", "windows-dev-def456"]
        else:
            target_peers = peer_ids.split(",")

        # Fetch metrics from each peer via A2A RPC (Phase 9a)
        for peer_id in target_peers:
            result = await _a2a_rpc_call("get_metrics", peer_id.strip(), {})
            if result:
                peer_metrics.append(result)
                aggregated_from.append(peer_id.strip())

    # Aggregate: average error rates, sum invocations
    if peer_metrics:
        avg_error_rate = sum(m.get("error_rate_24h", 0) for m in peer_metrics) / len(peer_metrics)
        total_invocations = sum(m.get("invocation_count_24h", 0) for m in peer_metrics)
        avg_adoption = sum(m.get("adoption_rate", 0) for m in peer_metrics) / len(peer_metrics)
    else:
        avg_error_rate = 0.020
        total_invocations = 1500
        avg_adoption = 0.15

    return {
        "aggregated_from": aggregated_from,
        "aggregation_timestamp": datetime.utcnow().isoformat(),
        "flags": [
            {
                "flag_id": "auto_load_github_repo",
                "error_rate_avg": round(avg_error_rate, 4),
                "invocation_count_total": total_invocations,
                "adoption_rate": round(avg_adoption, 4),
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
