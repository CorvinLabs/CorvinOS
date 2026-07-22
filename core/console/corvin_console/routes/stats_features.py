"""ADR-0212 — Ecosystem Feature Telemetry API endpoint.

GET /v1/console/stats/features returns instance-level feature adoption percentages.
Aggregates locally stored feature_snapshot.json records; no backend call needed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class FeatureSnapshot(BaseModel):
    instance_id: str
    bridges_connected: int = 0
    ldd_enabled: bool = False
    a2a_delegations_count: int = 0
    workflows_run_count: int = 0
    browser_automation_used: bool = False
    compute_jobs_count: int = 0
    forge_tools_created: int = 0
    skills_created: int = 0
    voice_sessions: int = 0
    artifacts_created: int = 0


class FeatureHeatmapResponse(BaseModel):
    """Adoption percentages for core features."""
    bridges_adoption: float
    ldd_adoption: float
    a2a_adoption: float
    workflows_adoption: float
    browser_adoption: float
    compute_adoption: float
    instance_count: int


@router.get("/stats/features", tags=["console-stats"])
def get_feature_stats(home: Path | None = None) -> FeatureHeatmapResponse:
    """Get ecosystem-level feature adoption heatmap.

    Scans ~/.corvin/telemetry/feature_snapshot.json (local instance data).
    Returns adoption percentages for core features: Bridges, LDD, A2A, Workflows,
    Browser, Compute. Used by dashboard FeatureHeatmapCard component.
    """
    # Resolve home path (normally from context; fallback for testing).
    if not home:
        home = Path.home() / ".corvin"
    else:
        home = Path(home)

    snapshot_file = home / "telemetry" / "feature_snapshot.json"
    snapshot_data = {}

    if snapshot_file.exists():
        try:
            snapshot_data = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    # Calculate adoption rates (instance-level booleans map to 0/1).
    instance_count = 1  # Always at least the local instance.
    bridges_adoption = float(snapshot_data.get("bridges_connected", 0) > 0) * 100
    ldd_adoption = float(snapshot_data.get("ldd_enabled", False)) * 100
    a2a_adoption = float(snapshot_data.get("a2a_delegations_count", 0) > 0) * 100
    workflows_adoption = float(snapshot_data.get("workflows_run_count", 0) > 0) * 100
    browser_adoption = float(snapshot_data.get("browser_automation_used", False)) * 100
    compute_adoption = float(snapshot_data.get("compute_jobs_count", 0) > 0) * 100

    return FeatureHeatmapResponse(
        bridges_adoption=bridges_adoption,
        ldd_adoption=ldd_adoption,
        a2a_adoption=a2a_adoption,
        workflows_adoption=workflows_adoption,
        browser_adoption=browser_adoption,
        compute_adoption=compute_adoption,
        instance_count=instance_count,
    )
