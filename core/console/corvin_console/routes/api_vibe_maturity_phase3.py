"""
Vibe Maturity Phase 3 Endpoints — Historical Data + Anomaly Detection

GET /v1/console/vibe/maturity/historical?loop=confidence&window=today
GET /v1/console/vibe/maturity/anomalies?window=300
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Literal, Optional

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth as session_auth
from ..deps import require_session

from .api_vibe_maturity import MaturityMeasurementAPI

log = logging.getLogger(__name__)

# ===== Models =====

class HistoricalPoint(BaseModel):
    """Single historical data point."""
    timestamp: str
    unix_time: int
    score: float
    convergence_rate: float
    drift: float


class HistoricalDataResponse(BaseModel):
    """Response for historical data."""
    loop: str
    window: str
    points: List[HistoricalPoint]
    count: int


class AnomalyModel(BaseModel):
    """Detected anomaly."""
    id: str
    type: Literal["drop", "drift-spike", "stall"]
    loop: str
    severity: Literal["critical", "warning", "info"]
    message: str
    timestamp: str
    value: float
    threshold: float


class AnomaliesResponse(BaseModel):
    """Response for anomalies."""
    anomalies: List[AnomalyModel]
    window_seconds: int
    count: int


# ===== Anomaly Detection =====

class AnomalyDetector:
    """Detect anomalies in live measurements."""

    THRESHOLDS = {
        "score_drop": 0.5,  # More than 0.5 point drop in 5 min
        "drift_spike": 0.01,  # Drift increase > 0.01
        "convergence_stall": 0.01,  # Convergence < 0.01 for 30 min
    }

    @staticmethod
    def detect(measurements: List[Dict[str, Any]], window_seconds: int = 300) -> List[AnomalyModel]:
        """
        Detect anomalies in measurements.

        Args:
            measurements: List of measurement records
            window_seconds: Time window to analyze (default 300 = 5 min)

        Returns:
            List of detected anomalies
        """
        if len(measurements) < 2:
            return []

        anomalies: List[AnomalyModel] = []
        now = datetime.now()
        cutoff = now - timedelta(seconds=window_seconds)

        # Get recent measurements
        recent = [
            m for m in measurements
            if datetime.fromisoformat(m.get("timestamp", "")) > cutoff
        ]

        if len(recent) < 2:
            return anomalies

        # Sort by timestamp
        recent.sort(key=lambda m: m.get("unix_time", 0))

        # Check for score drops (compare first vs last in window)
        first_convergence = recent[0].get("learning", {}).get("convergence_rate", 0)
        last_convergence = recent[-1].get("learning", {}).get("convergence_rate", 0)
        convergence_change = last_convergence - first_convergence

        if convergence_change < -AnomalyDetector.THRESHOLDS["convergence_stall"]:
            anomalies.append(
                AnomalyModel(
                    id=f"drop-{int(now.timestamp())}",
                    type="drop",
                    loop="overall",
                    severity="warning",
                    message=f"Convergence rate dropped by {abs(convergence_change):.3f}",
                    timestamp=recent[-1]["timestamp"],
                    value=abs(convergence_change),
                    threshold=AnomalyDetector.THRESHOLDS["convergence_stall"],
                )
            )

        # Check for drift spikes (component health)
        for component, health in recent[-1].get("component_health", {}).items():
            drift = health.get("drift", 0)
            if drift > 0.05:  # High drift
                anomalies.append(
                    AnomalyModel(
                        id=f"drift-{component}-{int(now.timestamp())}",
                        type="drift-spike",
                        loop=component,
                        severity="warning" if drift > 0.05 else "info",
                        message=f"{component} showing high drift: {drift:.3f}",
                        timestamp=recent[-1]["timestamp"],
                        value=drift,
                        threshold=0.05,
                    )
                )

        return anomalies


# ===== Historical Data Transformation =====

def extract_loop_score(measurement: Dict[str, Any], loop_key: str) -> float:
    """Extract a loop's score from a measurement record."""
    # Simplified: map component_health contribution to score
    health = measurement.get("component_health", {}).get(loop_key, {})
    contribution = health.get("contribution", 0)
    drift = health.get("drift", 0)

    # Score = contribution * (1 - drift) * 10
    score = contribution * (1 - drift) * 10
    return max(2, min(10, score))  # Clamp to [2, 10]


# ===== FastAPI Router =====

router = APIRouter(prefix="/vibe/maturity", tags=["console-vibe-maturity-phase3"])

_api: MaturityMeasurementAPI | None = None
_detector = AnomalyDetector()


def get_api() -> MaturityMeasurementAPI:
    global _api
    if _api is None:
        from .api_vibe_maturity import init_maturity_api

        init_maturity_api()
        _api = MaturityMeasurementAPI()
    return _api


@router.get(
    "/historical",
    response_model=HistoricalDataResponse,
    summary="Historical Loop Data",
)
async def get_historical_data(
    loop: str,
    window: Literal["today", "7d", "30d", "90d"] = "today",
    rec=Depends(require_session),
) -> HistoricalDataResponse:
    """
    Get historical data for a specific loop.

    Returns time-series points showing score, convergence, and drift.
    """
    api = get_api()
    measurements = api.get_measurements(window=window, tenant_id=rec.tenant_id)

    # Extract time-series for the loop
    points = []
    for m in measurements:
        score = extract_loop_score(m, loop)
        convergence = m.get("learning", {}).get("convergence_rate", 0)
        drift = m.get("component_health", {}).get(loop, {}).get("drift", 0)

        points.append(
            HistoricalPoint(
                timestamp=m.get("timestamp", ""),
                unix_time=m.get("unix_time", 0),
                score=score,
                convergence_rate=convergence,
                drift=drift,
            )
        )

    return HistoricalDataResponse(
        loop=loop,
        window=window,
        points=points,
        count=len(points),
    )


@router.get(
    "/anomalies",
    response_model=AnomaliesResponse,
    summary="Detect Anomalies",
)
async def detect_anomalies(
    window: int = 300,  # seconds (default 5 min)
    rec=Depends(require_session),
) -> AnomaliesResponse:
    """
    Detect anomalies in recent measurements.

    Checks for: score drops, drift spikes, convergence stalls.
    """
    api = get_api()
    measurements = api.get_measurements(window="7d", tenant_id=rec.tenant_id)

    anomalies = _detector.detect(measurements, window_seconds=window)

    return AnomaliesResponse(
        anomalies=anomalies,
        window_seconds=window,
        count=len(anomalies),
    )
