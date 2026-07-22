"""Feature telemetry aggregation endpoint (ADR-0212).

Serves /stats/features → instances × features heatmap data.
Aggregates anonymized, closed-enum feature usage for dashboard visualization.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_session

_log = logging.getLogger(__name__)
router = APIRouter()

_CORVIN_HOME = Path.home() / ".corvin"


class FeatureSnapshot(BaseModel):
    instance_id: str
    bridges_connected: list[str] = Field(default_factory=list)
    ldd_enabled: bool = False
    a2a_delegations_count: int = 0
    workflows_run_count: int = 0
    browser_automation_used: bool = False
    compute_jobs_count: int = 0


class FeatureHeatmapResponse(BaseModel):
    instances: list[FeatureSnapshot] = Field(default_factory=list)
    adoption_pct: dict[str, float] = Field(default_factory=dict)  # feature → adoption %
    total_instances: int = 0
    generated_at: str = ""


@router.get("/stats/features", response_model=FeatureHeatmapResponse)
async def get_feature_heatmap(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> FeatureHeatmapResponse:
    """Get instance × features heatmap (aggregated telemetry).

    Returns all known instances + their feature flags, anonymized + aggregated.
    Only available to authenticated console users.
    """
    instances = []
    feature_counts = {
        "bridges": 0, "ldd": 0, "a2a": 0, "workflows": 0,
        "browser": 0, "compute": 0,
    }

    # Scan for feature_snapshot.json files in visible instance homes
    # (this is a local-only endpoint; in production, backend aggregates across instances)
    telemetry_dir = _CORVIN_HOME / "telemetry"
    if telemetry_dir.exists():
        snapshot_path = telemetry_dir / "feature_snapshot.json"
        if snapshot_path.exists():
            try:
                data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                instance_id = rec.instance_id or f"local_{id(rec)}"

                snapshot = FeatureSnapshot(
                    instance_id=instance_id,
                    bridges_connected=data.get("bridges_connected", []),
                    ldd_enabled=data.get("ldd_enabled", False),
                    a2a_delegations_count=data.get("a2a_delegations_count", 0),
                    workflows_run_count=data.get("workflows_run_count", 0),
                    browser_automation_used=data.get("browser_automation_used", False),
                    compute_jobs_count=data.get("compute_jobs_count", 0),
                )
                instances.append(snapshot)

                # Tally adoption
                if len(snapshot.bridges_connected) > 0:
                    feature_counts["bridges"] += 1
                if snapshot.ldd_enabled:
                    feature_counts["ldd"] += 1
                if snapshot.a2a_delegations_count > 0:
                    feature_counts["a2a"] += 1
                if snapshot.workflows_run_count > 0:
                    feature_counts["workflows"] += 1
                if snapshot.browser_automation_used:
                    feature_counts["browser"] += 1
                if snapshot.compute_jobs_count > 0:
                    feature_counts["compute"] += 1

            except (OSError, json.JSONDecodeError) as e:
                _log.warning(f"Failed to load feature snapshot: {e}")

    total = len(instances) or 1  # Avoid division by zero
    adoption_pct = {
        k: (v / total * 100) for k, v in feature_counts.items()
    }

    from datetime import datetime
    return FeatureHeatmapResponse(
        instances=instances,
        adoption_pct=adoption_pct,
        total_instances=total,
        generated_at=datetime.utcnow().isoformat(),
    )
