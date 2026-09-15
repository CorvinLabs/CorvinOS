"""
Vibe Maturity Endpoint — Live 9D Learning Loops Data

GET /v1/console/vibe/maturity/measurements?window=7d

Returns:
  {
    "measurements": [{ timestamp, unix_time, tenant_id, learning, system, user_actions, component_health }, ...],
    "count": int,
    "window": "7d|30d|90d|today",
    "updated_at": ISO timestamp
  }
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Literal

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

# Auth
from .. import auth as session_auth
from ..deps import require_session

from . import maturity_live

log = logging.getLogger(__name__)

# ===== Request/Response Models =====

class MaturityMeasurementRecord(BaseModel):
    """Single measurement record.

    Computed on demand by ``maturity_live.build_measurement`` — carries the
    server-authoritative ``loop_scores`` and ``meta``/``signals`` alongside the
    raw ``learning``/``system``/``component_health`` inputs. ``extra="allow"``
    keeps the record forward-compatible with new derived fields.
    """
    model_config = ConfigDict(extra="allow")

    timestamp: str
    unix_time: int
    tenant_id: str
    learning: dict
    system: dict
    user_actions: dict
    component_health: dict
    loop_scores: dict = {}
    meta: dict = {}
    signals: dict = {}


class MaturityMeasurementsResponse(BaseModel):
    """Response from /vibe/maturity/measurements endpoint."""
    measurements: List[MaturityMeasurementRecord]
    count: int
    window: Literal["today", "7d", "30d", "90d"]
    updated_at: str


class MaturityMeasurementAPI:
    """Serves the maturity dashboard from ON-DEMAND live measurements.

    Historically this read per-minute JSONL files written by
    ``core.learning.live_experiment_collector`` — but that collector is POSIX-
    only (``import resource``/``os.getloadavg``), never ran on Windows and never
    got started by the console, so the directory was empty and the panel fell
    back to hardcoded sample data. We now compute each measurement on demand
    from the real EventStore + audit chain (see ``maturity_live``), which is
    cross-platform, always fresh, and needs no background writer.
    """

    def __init__(self, corvin_home: str | None = None):
        # Kept for signature compatibility; the live builder resolves paths via
        # ``core.paths.tenant`` (honours CORVIN_HOME) rather than a hardcoded root.
        self.corvin_home = Path(corvin_home) if corvin_home else None

    def get_measurements(self, window: str = "7d", tenant_id: str = "_default") -> List[Dict[str, Any]]:
        """Return the current live measurement (one snapshot) for the tenant.

        A single-element list keeps the historical list-shaped response contract
        while the value itself is computed fresh from real sources on every call.
        """
        try:
            return [maturity_live.build_measurement(tenant_id=tenant_id, window=window)]
        except Exception as exc:  # noqa: BLE001 — never 500 the dashboard; log + empty
            log.warning("maturity: on-demand measurement failed for %s/%s: %r", tenant_id, window, exc)
            return []

    def to_dict(self, measurements: List[Dict[str, Any]], window: str) -> Dict[str, Any]:
        """Convert measurements list to API response."""
        return {
            "measurements": measurements,
            "count": len(measurements),
            "window": window,
            "updated_at": datetime.now().isoformat(),
        }


# Singleton instance (created by app.py)
_api: MaturityMeasurementAPI | None = None


def init_maturity_api(corvin_home: str | None = None):
    """Initialize the maturity API (call this from app.py)."""
    global _api
    _api = MaturityMeasurementAPI(corvin_home)


def get_measurements(window: str = "7d", tenant_id: str = "_default") -> Dict[str, Any]:
    """Get measurements via the singleton API."""
    if _api is None:
        init_maturity_api()
    return _api.to_dict(
        _api.get_measurements(window=window, tenant_id=tenant_id),
        window=window,
    )


# ===== FastAPI Router =====

router = APIRouter(prefix="/vibe/maturity", tags=["console-vibe-maturity"])


@router.get(
    "/measurements",
    response_model=MaturityMeasurementsResponse,
    summary="Live 9D Maturity Measurements",
    description="Fetch live learning loop measurements for the 9D maturity dashboard",
)
async def get_live_measurements(
    window: Literal["today", "7d", "30d", "90d"] = "7d",
    rec=Depends(require_session),  # Auth gate
) -> MaturityMeasurementsResponse:
    """
    Get live measurements for the maturity dashboard.

    Filters by authenticated tenant_id from session.
    Supports time windows: today, 7d, 30d, 90d.
    """
    if _api is None:
        init_maturity_api()

    measurements = _api.get_measurements(window=window, tenant_id=rec.tenant_id)
    response = _api.to_dict(measurements, window=window)

    return MaturityMeasurementsResponse(
        measurements=[MaturityMeasurementRecord(**m) for m in measurements],
        count=response["count"],
        window=window,
        updated_at=response["updated_at"],
    )
