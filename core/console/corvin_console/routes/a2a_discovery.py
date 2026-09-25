"""Layer 38 — A2A Peer Discovery (Console routes).

Provides endpoints for discovering and listing paired A2A peers for the Console UI.

Routes:
- GET /v1/console/discovery/peers — list all discovered peers with metadata
- GET /v1/console/discovery/peers/{peer_id} — get peer details
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from .. import audit as console_audit
from ..deps import require_session

# ── path helpers ──────────────────────────────────────────────────────

_REPO = Path(__file__).resolve().parents[3]
_COWORK_DIR = _REPO / "corvin_operator" / "cowork"
_ORIGINS_DEFAULT = _COWORK_DIR / "remote_origins"


def _origins_dir() -> Path:
    env = os.environ.get("REMOTE_ORIGINS_DIR")
    return Path(env) if env else _ORIGINS_DEFAULT


# ── models ────────────────────────────────────────────────────────────

class PeerStatus(str):
    """Peer online status."""
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class PeerInfo(BaseModel):
    """Discovered A2A peer."""
    peer_id: str = Field(..., description="Unique peer identifier")
    name: str = Field(..., description="Human-readable peer name")
    status: str = Field(default="unknown", description="Peer status (online/offline/unknown)")
    endpoint: str | None = Field(default=None, description="Peer endpoint address")
    region: str | None = Field(default=None, description="Peer region")
    last_seen: str | None = Field(default=None, description="ISO timestamp of last contact")
    instance_id: str | None = Field(default=None, description="Peer instance ID")


class PeerListResponse(BaseModel):
    """Response with list of discovered peers."""
    peers: list[PeerInfo] = Field(default_factory=list)
    total: int = Field(default=0)


# ── helpers ───────────────────────────────────────────────────────────

def _load_peer_from_origin(origin_file: Path) -> PeerInfo | None:
    """Load peer metadata from an origin file (JSON).

    Origin file schema (minimal):
    {
      "peer_id": "...",
      "name": "...",
      "endpoint": "...",
      "region": "...",
      "last_seen": "..."
    }
    """
    try:
        if not origin_file.exists():
            return None

        with open(origin_file, "r") as f:
            data = json.load(f)

        return PeerInfo(
            peer_id=data.get("peer_id", origin_file.stem),
            name=data.get("name", origin_file.stem),
            status="online",  # TODO: implement liveness check
            endpoint=data.get("endpoint"),
            region=data.get("region"),
            last_seen=data.get("last_seen"),
            instance_id=data.get("instance_id"),
        )
    except Exception as e:
        console_audit.audit_warning(
            f"Failed to load peer metadata from {origin_file}: {e}"
        )
        return None


def _discover_peers() -> list[PeerInfo]:
    """Discover all known A2A peers from the origins directory."""
    origins_dir = _origins_dir()

    if not origins_dir.exists():
        return []

    peers: list[PeerInfo] = []

    try:
        for origin_file in origins_dir.glob("*.json"):
            peer = _load_peer_from_origin(origin_file)
            if peer:
                peers.append(peer)
    except Exception as e:
        console_audit.audit_warning(f"Failed to discover peers: {e}")
        return []

    # Sort by peer_id for deterministic ordering
    peers.sort(key=lambda p: p.peer_id)
    return peers


# ── router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/v1/console/discovery", tags=["discovery"])


@router.get("/peers", response_model=PeerListResponse)
async def list_peers(
    _session = Depends(require_session),
) -> PeerListResponse:
    """List all discovered A2A peers.

    Returns:
        PeerListResponse: List of peer metadata

    Audit:
        Logs a `discovery.peers_listed` event with peer count.
    """
    try:
        peers = _discover_peers()

        console_audit.audit_info(
            "discovery.peers_listed",
            extra={
                "peer_count": len(peers),
                "peer_ids": [p.peer_id for p in peers],
            },
        )

        return PeerListResponse(peers=peers, total=len(peers))
    except Exception as e:
        console_audit.audit_error(
            "discovery.list_peers_failed",
            extra={"error": str(e)},
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/peers/{peer_id}", response_model=PeerInfo)
async def get_peer(
    peer_id: str,
    _session = Depends(require_session),
) -> PeerInfo:
    """Get details for a specific peer.

    Args:
        peer_id: The peer identifier

    Returns:
        PeerInfo: Peer metadata

    Raises:
        HTTPException: 404 if peer not found

    Audit:
        Logs a `discovery.peer_detail_viewed` event.
    """
    try:
        origins_dir = _origins_dir()
        origin_file = origins_dir / f"{peer_id}.json"

        peer = _load_peer_from_origin(origin_file)
        if not peer:
            raise HTTPException(status_code=404, detail="Peer not found")

        console_audit.audit_info(
            "discovery.peer_detail_viewed",
            extra={"peer_id": peer_id},
        )

        return peer
    except HTTPException:
        raise
    except Exception as e:
        console_audit.audit_error(
            "discovery.get_peer_failed",
            extra={"peer_id": peer_id, "error": str(e)},
        )
        raise HTTPException(status_code=500, detail=str(e))
