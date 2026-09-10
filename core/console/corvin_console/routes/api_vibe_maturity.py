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
from pydantic import BaseModel

# Auth
from .. import auth as session_auth
from ..deps import require_session

log = logging.getLogger(__name__)

# ===== Request/Response Models =====

class MaturityMeasurementRecord(BaseModel):
    """Single measurement record."""
    timestamp: str
    unix_time: int
    tenant_id: str
    learning: dict
    system: dict
    user_actions: dict
    component_health: dict


class MaturityMeasurementsResponse(BaseModel):
    """Response from /vibe/maturity/measurements endpoint."""
    measurements: List[MaturityMeasurementRecord]
    count: int
    window: Literal["today", "7d", "30d", "90d"]
    updated_at: str


class MaturityMeasurementAPI:
    def __init__(self, corvin_home: str | None = None):
        """Initialize with corvin_home path."""
        if corvin_home is None:
            corvin_home = str(Path.home() / ".corvin")
        self.corvin_home = Path(corvin_home)
        self.measurements_dir = self.corvin_home / "tenants" / "_default" / "experiments" / "live_measurements"

    def get_measurements(self, window: str = "7d", tenant_id: str = "_default") -> List[Dict[str, Any]]:
        """
        Load measurements from JSONL files, filtered by time window.

        Args:
            window: "7d", "30d", "90d", or "today"
            tenant_id: Tenant to filter on

        Returns:
            List of measurement records
        """
        if not self.measurements_dir.exists():
            log.warning(f"Measurements directory does not exist: {self.measurements_dir}")
            return []

        # Determine cutoff time
        now = datetime.now()
        cutoff_days = {
            "today": 1,
            "7d": 7,
            "30d": 30,
            "90d": 90,
        }.get(window, 7)

        cutoff_date = now - timedelta(days=cutoff_days)

        measurements = []

        # Load all JSONL files in measurements_dir
        for jsonl_file in sorted(self.measurements_dir.glob("measurements_*.jsonl"), reverse=True):
            try:
                with open(jsonl_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)

                            # Filter by tenant_id
                            if record.get("tenant_id") != tenant_id:
                                continue

                            # Filter by time window
                            ts = datetime.fromisoformat(record.get("timestamp", "").replace("Z", "+00:00"))
                            if ts < cutoff_date:
                                continue

                            measurements.append(record)
                        except json.JSONDecodeError as e:
                            log.warning(f"Failed to parse JSONL line in {jsonl_file}: {e}")
                            continue

            except IOError as e:
                log.warning(f"Failed to read {jsonl_file}: {e}")
                continue

        return measurements

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
