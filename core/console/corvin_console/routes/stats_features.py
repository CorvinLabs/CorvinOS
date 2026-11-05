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
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import auth as session_auth
from ..deps import require_session

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


def _validate_feature_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and sanitize feature snapshot data (v0.10.60: input validation gate).

    Fail-closed: only known feature keys are returned; unknown keys are dropped.
    Type checking: bool/int values only. Strings/lists/dicts are coerced to bool.
    This prevents corrupted or tampered snapshot files from breaking the dashboard.
    """
    validated = {}
    for key in _KNOWN_FEATURES:
        if key not in data:
            continue
        value = data[key]
        # Allow bool or numeric types
        if isinstance(value, bool):
            validated[key] = value
        elif isinstance(value, (int, float)):
            validated[key] = value > 0
        elif isinstance(value, str):
            # Coerce non-empty strings to True
            validated[key] = len(value) > 0
        elif isinstance(value, (list, dict)):
            # Coerce collections by length
            validated[key] = len(value) > 0  # type: ignore[arg-type]
        # else: skip unknown types entirely (fail-closed)
    return validated


def _home() -> Path:
    """The install home - the same resolver the telemetry overview reads it with
    (``forge.paths.corvin_home``), never a hand-composed CORVIN_HOME/~/.corvin."""
    from forge import paths as _forge_paths  # noqa: PLC0415
    return Path(_forge_paths.corvin_home())


@router.get("/stats/features", tags=["console-stats"])
def get_feature_stats(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> FeatureHeatmapResponse:
    """Get this instance's feature adoption snapshot.

    Scans ~/.corvin/telemetry/feature_snapshot.json (local instance data
    only — the ecosystem-wide aggregate lives on the Railway backend, see
    GET /v1/stats/features). Used by dashboard FeatureHeatmapCard component.

    v0.10.60: Validates snapshot data before using (fails gracefully if corrupted).

    Behind the console session (review SRF-12): which features an install has
    adopted is reconnaissance for an unauthenticated caller on a LAN bind.
    """
    home = _home()

    snapshot_file = home / "telemetry" / "feature_snapshot.json"
    snapshot_data: dict[str, Any] = {}

    if snapshot_file.exists():
        try:
            raw_data = json.loads(snapshot_file.read_text(encoding="utf-8"))
            snapshot_data = _validate_feature_snapshot(raw_data)
        except (OSError, json.JSONDecodeError):
            pass

    has_snapshot = bool(snapshot_data)
    adoption_counts: dict[str, int] = {}
    adoption_pct: dict[str, float] = {}
    for key in _KNOWN_FEATURES:
        value = snapshot_data.get(key, False)
        adopted = bool(value)
        adoption_counts[key] = int(adopted)
        adoption_pct[key] = 100.0 if adopted else 0.0

    return FeatureHeatmapResponse(
        adoption_pct=adoption_pct,
        adoption_counts=adoption_counts,
        total_instances=1 if has_snapshot else 0,
    )
