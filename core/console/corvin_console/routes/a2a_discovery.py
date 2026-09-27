"""Layer 38 — A2A Peer Discovery (Console routes).

Provides endpoints for discovering and listing paired A2A peers for the Console UI.

Routes:
- GET /v1/console/discovery/peers — list all discovered peers with metadata
- GET /v1/console/discovery/peers/{peer_id} — get peer details
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from ..deps import require_session

# ``corvin_console.audit`` has no audit_info / audit_warning / audit_error: every
# call to them raised AttributeError, so GET /discovery/peers answered 500 on
# every request (the except branch re-raised from its own audit_error call).
# Listing peers is a read; it is logged, not chained.
log = logging.getLogger(__name__)

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
            # No liveness probe exists: "online" here was invented.
            status="unknown",
            endpoint=data.get("endpoint"),
            region=data.get("region"),
            last_seen=data.get("last_seen"),
            instance_id=data.get("instance_id"),
        )
    except Exception:
        log.warning("failed to load peer metadata from %s", origin_file.name, exc_info=True)
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
    except Exception:
        log.warning("failed to discover peers", exc_info=True)
        return []

    # Sort by peer_id for deterministic ordering
    peers.sort(key=lambda p: p.peer_id)
    return peers


# ── router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/peers", response_model=PeerListResponse)
async def list_peers(
    _session = Depends(require_session),
) -> PeerListResponse:
    """List all discovered A2A peers.

    Returns:
        PeerListResponse: List of peer metadata

    """
    try:
        peers = _discover_peers()
    except Exception:
        log.exception("discovery.list_peers_failed")
        raise HTTPException(status_code=500, detail="peer discovery failed")
    return PeerListResponse(peers=peers, total=len(peers))


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

    """
    try:
        origins_dir = _origins_dir()
        origin_file = origins_dir / f"{peer_id}.json"

        peer = _load_peer_from_origin(origin_file)
        if not peer:
            raise HTTPException(status_code=404, detail="Peer not found")

        return peer
    except HTTPException:
        raise
    except Exception:
        log.exception("discovery.get_peer_failed")
        raise HTTPException(status_code=500, detail="peer lookup failed")
