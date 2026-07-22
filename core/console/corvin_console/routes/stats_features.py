"""ADR-0212 — Ecosystem Feature Telemetry API endpoint.

GET /v1/console/stats/features returns instance-level feature adoption percentages.
Aggregates locally stored feature_snapshot.json records; no backend call needed.

Response shape mirrors the ecosystem-wide GET /v1/stats/features on the
Railway backend (corvin_features.telemetry.feature_stats) — same
adoption_pct/adoption_counts/total_instances keys, same feature-key names —
so the dashboard card and the public stats page can share one mental model
even though one aggregates 1 local instance and the other aggregates the
whole fleet.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# Keep in sync with aco/feature_snapshot.py::_KNOWN_FEATURES and
# corvin_features.telemetry.feature_counter._KNOWN_FEATURES.
_KNOWN_FEATURES = (
    "bridges_connected",
    "ldd_enabled",
    "a2a_delegations_count",
    "workflows_run_count",
    "browser_automation_used",
    "compute_jobs_count",
    "forge_tools_created",
    "skills_created",
    "voice_sessions",
    "artifacts_created",
    "mcp_servers_connected",
)


class FeatureHeatmapResponse(BaseModel):
    """Adoption percentages for this instance's known features."""
    adoption_pct: dict[str, float]
    adoption_counts: dict[str, int]
    total_instances: int


@router.get("/stats/features", tags=["console-stats"])
def get_feature_stats() -> FeatureHeatmapResponse:
    """Get this instance's feature adoption snapshot.

    Scans ~/.corvin/telemetry/feature_snapshot.json (local instance data
    only — the ecosystem-wide aggregate lives on the Railway backend, see
    GET /v1/stats/features). Used by dashboard FeatureHeatmapCard component.
    """
    # Resolve home path from environment or default.
    import os
    home_str = os.environ.get("CORVIN_HOME")
    home = Path(home_str) if home_str else Path.home() / ".corvin"

    snapshot_file = home / "telemetry" / "feature_snapshot.json"
    snapshot_data: dict[str, Any] = {}

    if snapshot_file.exists():
        try:
            snapshot_data = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    has_snapshot = bool(snapshot_data)
    adoption_counts: dict[str, int] = {}
    adoption_pct: dict[str, float] = {}
    for key in _KNOWN_FEATURES:
        value = snapshot_data.get(key)
        adopted = bool(value) if isinstance(value, bool) else bool(value and value > 0)
        adoption_counts[key] = int(adopted)
        adoption_pct[key] = 100.0 if adopted else 0.0

    return FeatureHeatmapResponse(
        adoption_pct=adoption_pct,
        adoption_counts=adoption_counts,
        total_instances=1 if has_snapshot else 0,
    )
