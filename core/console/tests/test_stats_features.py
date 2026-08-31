"""GET /v1/console/stats/features (ADR-0212) response shape.

Regression test: the FeatureHeatmapCard frontend reads
data.adoption_pct[key] / data.total_instances, but an earlier version of
this endpoint returned flat bridges_adoption/instance_count fields — a
guaranteed TypeError on every render. This locks the shape that actually
matches the dashboard component.
"""
from __future__ import annotations

import json

from corvin_console.routes import stats_features


def test_no_snapshot_returns_zero_instances_all_features_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    result = stats_features.get_feature_stats()
    assert result.total_instances == 0
    assert set(result.adoption_pct.keys()) == set(stats_features._KNOWN_FEATURES)
    assert all(v == 0.0 for v in result.adoption_pct.values())


def test_snapshot_present_reports_adopted_features(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    tele_dir = tmp_path / "telemetry"
    tele_dir.mkdir()
    (tele_dir / "feature_snapshot.json").write_text(
        json.dumps({"ldd_enabled": True, "workflows_run_count": 3, "bridges_connected": 0}),
        encoding="utf-8",
    )

    result = stats_features.get_feature_stats()
    assert result.total_instances == 1
    assert result.adoption_pct["ldd_enabled"] == 100.0
    assert result.adoption_pct["workflows_run_count"] == 100.0
    assert result.adoption_pct["bridges_connected"] == 0.0
    assert result.adoption_pct["mcp_servers_connected"] == 0.0


def test_malformed_snapshot_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    tele_dir = tmp_path / "telemetry"
    tele_dir.mkdir()
    (tele_dir / "feature_snapshot.json").write_text("not json", encoding="utf-8")

    result = stats_features.get_feature_stats()
    assert result.total_instances == 0
